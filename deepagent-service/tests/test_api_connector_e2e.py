"""tests/test_api_connector_e2e.py -- connector 機制端到端驗收(Task 1-7 全部串起來):
lookup 窄化反問(跨 turn)、fetch 資料+敘事綁定出貨、功能關閉不變式。照 test_chat.py 既有
e2e 構造(ScriptedChatModel + monkeypatch execute_fetch),不重用其模組內私有 helper
(需要另外掛 AGENT_CONNECTORS_FILE,構造不同故本檔自帶一份精簡版)。
"""

import json

from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import app.agent.tools.data as data_module
from app import main as main_module
from app.agent import chat_turn
from app.agent.graph import build_agent
from app.agent.prompts import SYSTEM_PROMPT, build_connector_prompt_section
from app.agent.tools.recording import ToolResultRecorder
from app.engine.api_fetch import land_snapshot, load_fetch_records, snapshot_fingerprint
from app.engine.connectors import load_connector_registry
from app.engine.duck import Source, open_locked_connection
from app.engine.workspace import prepare_local_layout, stage_skills
from app.engine.workspace_store import build_workspace_store
from tests.fake_model import ScriptedChatModel

# -- Task 6: selectedGroups 過濾——兩個 group,各一個 data connector,無 params/validate_against
# (那條路徑已由上面 turn1/turn2 測試涵蓋,這裡只聚焦 group 過濾本身)。
_GROUPED_CONNECTORS_YAML = """\
connector_groups:
  - name: mes
    display: MES 製造執行系統
    description: 產線良率
    members:
      - name: mes_yield
        kind: data
        description: 產線良率
        endpoint: http://connector.internal/mes/yield
        method: GET
        params: {}
  - name: erp
    display: ERP 企業資源規劃
    description: 訂單
    members:
      - name: erp_orders
        kind: data
        description: 訂單清單
        endpoint: http://connector.internal/erp/orders
        method: GET
        params: {}
"""

_MES_YIELD_RECORDS = [{"line_id": "L1", "yield_pct": 95.5}]
_ERP_ORDER_RECORDS = [{"order_id": "O1", "amount": 1000}]


class _RecordingScriptedChatModel(BaseChatModel):
    """像 tests.fake_model.ScriptedChatModel,但額外記錄每次呼叫收到的訊息——FETCH_ERROR
    退貨文字只活在 ToolMessage content 裡,不上 SSE wire(events.py 的 STEP 只帶 title/status),
    要驗證退貨內容只能從模型視角(收到的訊息 history)撈。"""

    scripted_messages: list[AIMessage]
    received_message_batches: list[list[BaseMessage]] = Field(default_factory=list)

    def __init__(self, scripted_messages: list[AIMessage], **kwargs: object) -> None:
        super().__init__(scripted_messages=list(scripted_messages), **kwargs)

    def bind_tools(self, tools: object, **kwargs: object) -> "_RecordingScriptedChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        self.received_message_batches.append(list(messages))
        if self.scripted_messages:
            message = self.scripted_messages.pop(0)
        else:
            message = AIMessage(content="")
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "recording-scripted"


_CONNECTORS_YAML = """\
connectors:
  - name: line_list
    kind: lookup
    description: 產線清單
    endpoint: http://connector.internal/lines
    method: GET
    params: {}
  - name: mes_yield
    kind: data
    description: 產線良率
    endpoint: http://connector.internal/yield
    method: POST
    params:
      line_id:
        type: str
        required: true
        validate_against: {connector: line_list, column: line_id}
      start_date: {type: date, required: true}
    limits: {timeout_s: 10, max_bytes: 1000000, max_rows: 50000}
"""

_LINE_LIST_RECORDS = [
    {"line_id": "L1", "line_name": "Line 1"},
    {"line_id": "L2", "line_name": "Line 2"},
]
_YIELD_RECORDS = [
    {"date": "2026-08-01", "yield_pct": 95.5},
    {"date": "2026-08-02", "yield_pct": 96.2},
]

_DASHBOARD_WITH_Q2_REFERENCE_CONTENT = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    'const yieldRows = window.__ERD_RESULTS__["q2"].rows;'
    "document.getElementById('c').textContent = String(yieldRows.length);"
    "</script></body></html>"
)


def _sse_events(raw_body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in raw_body.splitlines()
        if line.startswith("data: ")
    ]


def _skill_read_step() -> AIMessage:
    """照 test_chat.py 的 `_skill_read_step()`——dashboard.html write_file 前 gate 要求整個
    skill 資料夾都讀過,回傳新實例避免多個腳本共用同一個 AIMessage.id。"""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "id": "read-skill",
                "args": {"file_path": ".skills/builtin/dashboard/SKILL.md", "limit": 1000},
            },
            {
                "name": "read_file",
                "id": "read-examples",
                "args": {
                    "file_path": ".skills/builtin/dashboard/references/examples.md",
                    "limit": 1000,
                },
            },
            {
                "name": "read_file",
                "id": "read-html-contract",
                "args": {
                    "file_path": ".skills/builtin/dashboard/references/html-contract.md",
                    "limit": 1000,
                },
            },
            {
                "name": "read_file",
                "id": "read-chart-rules",
                "args": {
                    "file_path": ".skills/builtin/dashboard/references/chart-rules.md",
                    "limit": 1000,
                },
            },
        ],
    )


async def _post_chat(
    session_id: str,
    user_id: str,
    message: str,
    selected_groups: list[str] | None = None,
) -> list[dict]:
    payload = {
        "sessionId": session_id,
        "userId": user_id,
        "message": message,
        "history": [],
        "sources": [],
    }
    # None(預設)= key 整個不帶,對齊「selectedGroups 未帶」的真實前端情境;傳空 list 才是
    # 顯式帶著空陣列——兩者在 schema 上等價(預設值同為 []),但測試 2 要驗的正是前者。
    if selected_groups is not None:
        payload["selectedGroups"] = selected_groups
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat", json=payload)
    return _sse_events(response.text)


async def test_connector_flow_lookupNarrowAskThenFetchAndDashboard(tmp_path, monkeypatch) -> None:
    """turn1:fetch(line_list) → run_sql 窄化 → 問題區塊(QUESTION)。turn2(同 session,同一顆
    duckdb 連線需重新掛回 turn1 落的 snapshot):fetch(mes_yield,validate_against line_list)
    → run_sql → write_file dashboard.html(含 __ERD_RESULTS__ 字面引用)→ DASHBOARD_HTML + ANSWER。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    connectors_path = tmp_path / "connectors.yaml"
    connectors_path.write_text(_CONNECTORS_YAML, encoding="utf-8")
    monkeypatch.setenv("AGENT_CONNECTORS_FILE", str(connectors_path))

    fetch_calls: list[tuple[str, dict]] = []

    def fake_execute_fetch(definition, params, transport=None):
        fetch_calls.append((definition.name, dict(params)))
        if definition.name == "line_list":
            return json.dumps(_LINE_LIST_RECORDS).encode()
        if definition.name == "mes_yield":
            return json.dumps(_YIELD_RECORDS).encode()
        raise AssertionError(f"unexpected connector fetch: {definition.name}")

    monkeypatch.setattr(data_module, "execute_fetch", fake_execute_fetch)

    scripted = ScriptedChatModel(
        [
            # -- turn1 --
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fetch_api_data",
                        "id": "call-fetch-lookup",
                        "args": {"connector": "line_list", "params": {}, "alias": "line_list"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call-narrow",
                        "args": {
                            "sql": "SELECT line_id FROM line_list WHERE line_id LIKE 'L%'",
                            "intent": "窄化候選產線",
                        },
                    }
                ],
            ),
            AIMessage(
                content=(
                    "```questions\n"
                    '[{"text": "要查詢哪一條產線?", "options": ["L1", "L2"], '
                    '"multiSelect": false}]\n'
                    "```"
                )
            ),
            # -- turn2 --
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fetch_api_data",
                        "id": "call-fetch-data",
                        "args": {
                            "connector": "mes_yield",
                            "params": {"line_id": "L1", "start_date": "2026-08-01"},
                            "alias": "yield_data",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call-yield",
                        "args": {
                            "sql": "SELECT date, yield_pct FROM yield_data ORDER BY date",
                            "intent": "良率趨勢",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-dashboard",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": _DASHBOARD_WITH_Q2_REFERENCE_CONTENT,
                        },
                    }
                ],
            ),
            AIMessage(content="產線 L1 良率已更新,請查看儀表板。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)

    turn1_events = await _post_chat("sess-connector", "user-1", "哪條產線良率最高?")
    turn1_types = [event["type"] for event in turn1_events]
    turn1_step_titles = {event["title"] for event in turn1_events if event["type"] == "STEP"}
    assert "取得 API 資料" in turn1_step_titles
    assert "QUESTION" in turn1_types
    assert "DASHBOARD_HTML" not in turn1_types

    turn2_events = await _post_chat("sess-connector", "user-1", "L1")
    turn2_types = [event["type"] for event in turn2_events]
    turn2_step_titles = {event["title"] for event in turn2_events if event["type"] == "STEP"}
    assert "取得 API 資料" in turn2_step_titles
    assert "DASHBOARD_HTML" in turn2_types
    assert turn2_events[-1] == {"type": "ANSWER", "text": "產線 L1 良率已更新,請查看儀表板。"}

    dashboard_events = [event for event in turn2_events if event["type"] == "DASHBOARD_HTML"]
    assert len(dashboard_events) == 1
    final_html = dashboard_events[0]["html"]
    assert '__ERD_RESULTS__["q2"]' in final_html
    assert '"rows"' in final_html  # q2 結果確實被 embed 進 __ERD_RESULTS__ 注入區塊

    # cross-turn:turn2 開的是全新 duckdb 連線,mes_yield 的 validate_against 只有在該連線
    # 成功重新掛回 turn1 落的 line_list snapshot 時才會通過驗證進而呼叫 execute_fetch——
    # 通不過就在驗證階段被攔下,execute_fetch 只會被呼叫一次(line_list),不會有第二次。
    assert fetch_calls == [
        ("line_list", {}),
        ("mes_yield", {"line_id": "L1", "start_date": "2026-08-01"}),
    ]

    # snapshot 身分是指紋(§12.4)——檔名不再是 alias,`fingerprint` 欄位才是查檔用的鍵;
    # alias 降為表名,turn2 靠 fetches.json 的 fingerprint→alias 映射掛回 line_list 表
    # (前面的 fetch_calls/final_html 斷言已間接驗證掛回成功,這裡直接驗證紀錄與落檔形狀)。
    line_list_fingerprint = snapshot_fingerprint("line_list", {})
    yield_data_fingerprint = snapshot_fingerprint(
        "mes_yield", {"line_id": "L1", "start_date": "2026-08-01"}
    )
    workspace = build_workspace_store().prepare("user-1", "sess-connector")
    assert load_fetch_records(workspace) == [
        {
            "fingerprint": line_list_fingerprint,
            "alias": "line_list",
            "connector": "line_list",
            "params": {},
        },
        {
            "fingerprint": yield_data_fingerprint,
            "alias": "yield_data",
            "connector": "mes_yield",
            "params": {"line_id": "L1", "start_date": "2026-08-01"},
        },
    ]
    assert (workspace.api_snapshots_dir / f"{line_list_fingerprint}.json").is_file()


async def test_connector_feature_off_noConfigMeansNoChanges(tmp_path, monkeypatch) -> None:
    """AGENT_CONNECTORS_FILE 未設 → registry 為空 → build_agent 的工具集無 fetch_api_data、
    system prompt 與現況(SYSTEM_PROMPT 本身,不附加任何 connector 段)相同——功能關閉不變式,
    是合併安全的核心保證(Task 6 brief)。"""
    monkeypatch.delenv("AGENT_CONNECTORS_FILE", raising=False)

    registry = load_connector_registry(None)
    assert build_connector_prompt_section(registry) == ""
    assert SYSTEM_PROMPT + build_connector_prompt_section(registry) == SYSTEM_PROMPT

    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\n", encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(csv_path), "csv")])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")

    builtin_dir = tmp_path / "skills" / "dashboard"
    builtin_dir.mkdir(parents=True)
    (builtin_dir / "SKILL.md").write_text(
        "---\nname: dashboard\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    staged = stage_skills(workspace, builtin_dir.parent, tmp_path / "no-user-skills")

    model = GenericFakeChatModel(messages=iter([]))
    agent = build_agent(
        model, connection, workspace, staged, ToolResultRecorder(), selected_groups=[]
    )

    main_tools = agent.nodes["tools"].bound.tools_by_name
    assert "fetch_api_data" not in main_tools


async def test_connector_feature_off_fullTurnNeverTouchesSnapshotMachinery(
    tmp_path, monkeypatch
) -> None:
    """§12 補強:功能關閉不只是「工具不存在」——真的跑一輪完整 /chat 也要驗到指紋機制
    全程不可達。`land_snapshot` 是 fingerprint 落檔的唯一入口(只有 fetch_api_data 呼叫它),
    spy 到零呼叫即代表指紋機制真的沒被摸到,而非恰好沒被用到。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.delenv("AGENT_CONNECTORS_FILE", raising=False)

    land_snapshot_calls: list[str] = []

    def spy_land_snapshot(workspace, fingerprint, payload):
        land_snapshot_calls.append(fingerprint)
        return land_snapshot(workspace, fingerprint, payload)

    monkeypatch.setattr(data_module, "land_snapshot", spy_land_snapshot)

    scripted = ScriptedChatModel([AIMessage(content="沒有連結任何外部資料源,已直接回答。")])
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)

    events = await _post_chat("sess-off", "user-1", "你好")
    assert events[-1] == {"type": "ANSWER", "text": "沒有連結任何外部資料源,已直接回答。"}

    assert land_snapshot_calls == []
    workspace = build_workspace_store().prepare("user-1", "sess-off")
    assert not workspace.fetches_path.exists()
    assert list(workspace.api_snapshots_dir.glob("*.json")) == []


# -- Task 6:selectedGroups 過濾——真實 agent loop 全鏈驗證(不只 build_agent 單元層級) -------


async def test_connector_selectedGroups_mesOnly_fetchesMesRejectsErpWithMesOnlyAvailableList(
    tmp_path, monkeypatch
) -> None:
    """selectedGroups=["mes"]:mes_yield fetch 成功、erp_orders 因不在過濾後 registry 內被
    退貨("connector 不存在"),退貨文字的可用清單只列 mes 組成員——證明過濾在真實 agent
    loop(而非只在 build_agent 單元層級)生效,且不變式一致(erp fetch 從未真正打出去)。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    connectors_path = tmp_path / "connectors.yaml"
    connectors_path.write_text(_GROUPED_CONNECTORS_YAML, encoding="utf-8")
    monkeypatch.setenv("AGENT_CONNECTORS_FILE", str(connectors_path))

    fetch_calls: list[tuple[str, dict]] = []

    def fake_execute_fetch(definition, params, transport=None):
        fetch_calls.append((definition.name, dict(params)))
        if definition.name == "mes_yield":
            return json.dumps(_MES_YIELD_RECORDS).encode()
        raise AssertionError(f"unexpected connector fetch: {definition.name}")

    monkeypatch.setattr(data_module, "execute_fetch", fake_execute_fetch)

    scripted = _RecordingScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fetch_api_data",
                        "id": "call-fetch-mes",
                        "args": {"connector": "mes_yield", "params": {}, "alias": "mes_data"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fetch_api_data",
                        "id": "call-fetch-erp",
                        "args": {"connector": "erp_orders", "params": {}, "alias": "erp_data"},
                    }
                ],
            ),
            AIMessage(content="目前選定範圍只有 MES 資料,已依此回答。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)

    events = await _post_chat(
        "sess-groups-mes", "user-1", "查 MES 跟 ERP 資料", selected_groups=["mes"]
    )

    assert events[-1] == {"type": "ANSWER", "text": "目前選定範圍只有 MES 資料,已依此回答。"}

    # mes fetch 真的落地;erp 連 execute_fetch 都沒被呼叫到(在 registry 過濾階段就被攔下,
    # 根本進不到 execute_fetch)。
    assert fetch_calls == [("mes_yield", {})]
    workspace = build_workspace_store().prepare("user-1", "sess-groups-mes")
    assert load_fetch_records(workspace) == [
        {
            "fingerprint": snapshot_fingerprint("mes_yield", {}),
            "alias": "mes_data",
            "connector": "mes_yield",
            "params": {},
        }
    ]

    # erp 的退貨文字不上 SSE wire(FETCH_ERROR 只是 ToolMessage content,STEP 事件只帶
    # title/status)——從模型視角(收到的訊息 history)撈出那則 ToolMessage 驗證退貨內容:
    # 「不存在」+ 可用清單只有 mes 組的 mes_yield,erp_orders 完全不在清單裡。
    erp_tool_messages = [
        message
        for batch in scripted.received_message_batches
        for message in batch
        if isinstance(message, ToolMessage) and message.tool_call_id == "call-fetch-erp"
    ]
    assert len(erp_tool_messages) == 1
    rejection_text = erp_tool_messages[0].content
    assert "erp_orders" in rejection_text
    assert "不存在" in rejection_text
    available_list = rejection_text.split("可用:", 1)[1]
    assert "mes_yield" in available_list
    assert "erp_orders" not in available_list


async def test_connector_selectedGroups_omitted_allowsAllGroupsIncludingErp(
    tmp_path, monkeypatch
) -> None:
    """selectedGroups 完全未帶(前端未圈選,§11 不變式)→ filter_by_groups 空清單短路回傳
    全量 registry——同一份雙 group config,erp_orders(非 mes 組)一樣能成功 fetch,端到端
    證明「空=全部可見」不只是 build_agent 單元層級的行為。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    connectors_path = tmp_path / "connectors.yaml"
    connectors_path.write_text(_GROUPED_CONNECTORS_YAML, encoding="utf-8")
    monkeypatch.setenv("AGENT_CONNECTORS_FILE", str(connectors_path))

    fetch_calls: list[tuple[str, dict]] = []

    def fake_execute_fetch(definition, params, transport=None):
        fetch_calls.append((definition.name, dict(params)))
        if definition.name == "erp_orders":
            return json.dumps(_ERP_ORDER_RECORDS).encode()
        raise AssertionError(f"unexpected connector fetch: {definition.name}")

    monkeypatch.setattr(data_module, "execute_fetch", fake_execute_fetch)

    scripted = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fetch_api_data",
                        "id": "call-fetch-erp",
                        "args": {"connector": "erp_orders", "params": {}, "alias": "erp_data"},
                    }
                ],
            ),
            AIMessage(content="ERP 訂單資料已取得。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)

    events = await _post_chat("sess-groups-all", "user-1", "查 ERP 訂單資料")

    assert events[-1] == {"type": "ANSWER", "text": "ERP 訂單資料已取得。"}
    assert fetch_calls == [("erp_orders", {})]
    workspace = build_workspace_store().prepare("user-1", "sess-groups-all")
    assert load_fetch_records(workspace) == [
        {
            "fingerprint": snapshot_fingerprint("erp_orders", {}),
            "alias": "erp_data",
            "connector": "erp_orders",
            "params": {},
        }
    ]
