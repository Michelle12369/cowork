"""`ChatTurn` connector 模式整合測試——connector 掛載/skill staging/prompt 段/remount/
互斥防禦。單一 turn 的內部狀態(`_agent`/`_workspace`/`_run_input`)直接測
`async with ChatTurn(...) as turn: await turn.prepare()`(不經 `/chat` SSE 層,斷言更直接);
跨 turn remount 需要真的 persist,改走 `/chat` e2e 兩輪。

純 MCP 化後 wire 收 `ConnectorSpec`(id/name/url)清單,`ChatTurn` 直接呼叫
`load_mcp_connector`,無目錄可查——大多數測試 monkeypatch `load_mcp_connector` 回傳
`demo_connector()`(只驗證掛載/skill/prompt 等下游行為,不需要真的 MCP server);至少一條
(`test_connectors_real_mcp_path_wires_tools_and_skill_through_chat_turn`)起一個真的
FastMCP fixture server 證明實際 MCP 線路能透過 `ChatTurn` 走通。
"""

import json
import socket
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app import main as main_module
from app.agent import chat_turn
from app.agent.chat_turn import ChatTurn
from app.agent.connectors.mcp_adapter import load_mcp_connector as real_load_mcp_connector
from app.agent.connectors.registry import demo_connector
from app.agent.prompts import CONNECTOR_MODE_SYSTEM_SECTION
from app.api.schemas import ChatRequest, SourceItem
from app.engine.replay_manifest import load_landings
from app.engine.request_context import (
    require_session_id,
    require_sso_token,
    require_sso_url,
    require_user_id,
)
from app.engine.workspace_store import build_workspace_store
from tests.conftest import TEST_BEARER_TOKEN
from tests.fake_model import ScriptedChatModel

_DEMO_CONNECTOR_SPEC = {
    "id": "demo_quality",
    "name": "示範品質資料（合成）",
    "url": "http://demo-connector.invalid/mcp",
}


async def _stub_load_mcp_connector(
    connector_id: str, display_name: str, url: str, bearer_token_key: str | None = None
):
    """`load_mcp_connector` 現為 async——monkeypatch 替身也需是 coroutine function,
    才能配合 `chat_turn.py` 的 `await load_mcp_connector(...)`。第四參數
    `bearer_token_key` 隨 wire 的 `ConnectorSpec.bearerTokenKey` 傳入,這裡的測試不驗證
    認證行為(見 `tests/test_mcp_adapter.py`),故不使用,只接住避免 TypeError。"""
    return demo_connector()


def _connector_request(**overrides) -> ChatRequest:
    payload = {
        "sessionId": "sess-connector",
        "userId": "user-1",
        "message": "幫我看 Fab A 的品質資料",
        "history": [],
        "sources": [],
        "connectors": [_DEMO_CONNECTOR_SPEC],
    }
    payload.update(overrides)
    return ChatRequest(**payload)


@pytest.fixture()
def connector_turn_env(tmp_path, monkeypatch):
    """單一 turn 的 attribute 檢查用——workspace 隔離＋不會真的呼叫模型(不驅動
    `turn.stream()`,`build_model()` 只在 `build_agent` 建圖時被引用一次);
    `load_mcp_connector` 預設 stub 回 `demo_connector()`,不需要真的 MCP server——需要真實
    MCP 線路的測試在測試本體內用同一個 `monkeypatch` 覆寫回真正的實作。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setattr(chat_turn, "build_model", lambda: ScriptedChatModel([]))
    monkeypatch.setattr(chat_turn, "load_mcp_connector", _stub_load_mcp_connector)
    return tmp_path


async def test_connectors_wires_connector_tools_into_agent(connector_turn_env) -> None:
    request = _connector_request()
    async with ChatTurn(request) as turn:
        await turn.prepare()
        tool_names = set(turn._agent.nodes["tools"].bound.tools_by_name)

    assert {"demo_quality_get_quality", "demo_quality_list_fabs"} <= tool_names
    # extra_tools 是併入既有 data tools,不是取代。
    assert {"get_schema", "run_sql", "preview_data"} <= tool_names


async def test_connectors_with_sources_logs_defense_in_depth_warning(
    connector_turn_env, caplog
) -> None:
    """後端是 connectors/sources 互斥的唯一權威(不再於此 raise,見 bcf1ce7)——但
    ChatTurn 仍應留一筆警告紀錄,萬一該不變式被打破時至少可觀測到 sources 被忽略。"""
    request = _connector_request(
        sources=[SourceItem(alias="orders", path="orders.csv", fileType="csv")]
    )
    with caplog.at_level("WARNING", logger="app.agent.chat_turn"):
        async with ChatTurn(request) as turn:
            await turn.prepare()

    assert any(
        "connector mode active" in record.message and "ignoring 1 sources" in record.message
        for record in caplog.records
    )


async def test_chat_turn_prepare_failure_still_resets_identity_via_aexit(
    connector_turn_env, monkeypatch
) -> None:
    """`__aenter__` 瘦身後不可失敗,可失敗的初始化重活在 `prepare()`——呼叫端仍在
    `async with` 區塊內,`prepare()` 拋出時 `__aexit__` MUST 照樣執行並 reset identity
    contextvar,不能因為初始化重活炸掉就洩漏(對應新不變式:清理保證收斂到
    `__aexit__` 單一入口,不再靠 `__aenter__` 自己的 except 分支複製一份)。"""

    def failing_build_agent(*args, **kwargs):
        raise RuntimeError("boom during agent assembly")

    monkeypatch.setattr(chat_turn, "build_agent", failing_build_agent)
    request = _connector_request()

    with pytest.raises(RuntimeError, match="boom during agent assembly"):
        async with ChatTurn(request) as turn:
            await turn.prepare()

    with pytest.raises(LookupError, match="current_user_id"):
        require_user_id()
    with pytest.raises(LookupError, match="current_session_id"):
        require_session_id()


async def test_chat_turn_sso_kwargs_populate_request_context(connector_turn_env) -> None:
    """sso_token/sso_url 一律以 ChatTurn 的 keyword-only 建構子參數傳入(main.py 的 /chat
    handler 從 X-SSO-Token/X-SSO-Url header 解析後轉呼叫),NEVER 是 ChatRequest 的 body 欄位
    ——驗證 __aenter__ 期間 require_sso_token()/require_sso_url() 讀得到這兩個值,__aexit__
    之後還原成未設定(fail loud)。"""
    request = _connector_request()
    async with ChatTurn(request, sso_token="tok-1", sso_url="https://sso.example/auth"):
        assert require_sso_token() == "tok-1"
        assert require_sso_url() == "https://sso.example/auth"

    with pytest.raises(LookupError, match="sso_token"):
        require_sso_token()
    with pytest.raises(LookupError, match="sso_url"):
        require_sso_url()


async def test_chat_turn_without_sso_kwargs_defaults_to_none_and_fails_loud(
    connector_turn_env,
) -> None:
    request = _connector_request()
    async with ChatTurn(request):
        with pytest.raises(LookupError, match="sso_token"):
            require_sso_token()
        with pytest.raises(LookupError, match="sso_url"):
            require_sso_url()


async def test_connectors_stages_connector_skill_markdown(connector_turn_env) -> None:
    """demo_connector 只供一份 skill(`usage`)——staged 到單層目錄
    `connectors/{frontmatter_name}/SKILL.md`(不是 `{connector_id}/{skill_name}`,見
    `workspace.stage_connector_skills` docstring 的佈局說明——兩層深度會讓 deepagents
    `SkillsMiddleware` 掃不到),frontmatter 是 demo_connector 自帶的契約值
    (`demo-quality-usage`),staging 端原樣寫入不合成。"""
    request = _connector_request()
    async with ChatTurn(request) as turn:
        await turn.prepare()
        skill_path = turn._workspace.skills_dir / "connectors" / "demo-quality-usage" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")

    assert "name: demo-quality-usage" in content
    # frontmatter 是 registry.py fixture 自帶的契約值,skill 正文原樣保留。
    assert "demo_quality skill" in content
    assert "get_quality(fab, week)" in content


async def test_connectors_mode_passes_connector_system_section_to_build_agent(
    connector_turn_env, monkeypatch
) -> None:
    """connector 引導已搬進 system prompt 條件段——每輪由
    `build_connector_mode_system_section(connectors)` 組裝(已連接 connector 清單＋靜態
    規則),不再織進每輪 user 訊息。這裡驗證 chat_turn 傳給 `build_agent` 的
    `extra_system_section` 含 connector 清單與規則段。"""
    captured_kwargs: list[dict] = []
    original_build_agent = chat_turn.build_agent

    def spy_build_agent(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return original_build_agent(*args, **kwargs)

    monkeypatch.setattr(chat_turn, "build_agent", spy_build_agent)
    request = _connector_request()
    async with ChatTurn(request) as turn:
        await turn.prepare()
        seeded_message = turn._run_input["messages"][-1].content

    assert len(captured_kwargs) == 1
    extra_system_section = captured_kwargs[0]["extra_system_section"]
    assert "本 session 已連接的 API connector" in extra_system_section
    assert "`demo_quality`" in extra_system_section
    assert CONNECTOR_MODE_SYSTEM_SECTION in extra_system_section
    # 舊版每輪織進 user 訊息的做法已移除——seeded user 訊息不再帶連結器索引/護欄文字。
    assert "前綴掛載" not in seeded_message
    assert "join key" not in seeded_message


async def test_connectors_share_same_connection_lock_across_tool_families(
    connector_turn_env, monkeypatch
) -> None:
    """一個 DuckDB connection 只能有一把鎖守門(spec/`api_snapshot.py` docstring 的
    「MUST 用同一把 connection_lock」)——`ChatTurn` 建的鎖 MUST 是同一個物件同時傳給
    `build_connector_tools` 與(經 `build_agent` 轉交的)`build_data_tools`,不是各自
    建一把各管各的。攔截兩個 build 函式實際收到的 `connection_lock` 參數比對 identity
    (`is`),而不是只比較兩者都非 `None`。"""
    import app.agent.graph as graph_module

    captured_connector_lock: list[object] = []
    captured_data_tools_lock: list[object] = []

    original_build_connector_tools = chat_turn.build_connector_tools
    original_build_data_tools = graph_module.build_data_tools

    def spy_build_connector_tools(connectors, connection, connection_lock, workspace, **kwargs):
        captured_connector_lock.append(connection_lock)
        return original_build_connector_tools(
            connectors, connection, connection_lock, workspace, **kwargs
        )

    def spy_build_data_tools(connection, workspace, recorder, connection_lock=None):
        captured_data_tools_lock.append(connection_lock)
        return original_build_data_tools(
            connection, workspace, recorder, connection_lock=connection_lock
        )

    monkeypatch.setattr(chat_turn, "build_connector_tools", spy_build_connector_tools)
    monkeypatch.setattr(graph_module, "build_data_tools", spy_build_data_tools)

    request = _connector_request()
    async with ChatTurn(request) as turn:
        await turn.prepare()

    assert len(captured_connector_lock) == 1
    assert len(captured_data_tools_lock) == 1
    assert captured_connector_lock[0] is not None
    assert captured_connector_lock[0] is captured_data_tools_lock[0]


async def test_chat_unreachable_connector_url_emits_clean_actionable_error_event(
    connector_turn_env, monkeypatch
) -> None:
    """`/chat` e2e:純 MCP 化後沒有目錄可查——未知/不可達的 connector 改由 `load_mcp_connector`
    在 `tools/list` 階段對不可達 url 拋 `ConnectorToolError`,經 main.py 的 handler 轉成乾淨的
    ErrorEvent,而不是裸例外中斷 SSE 傳輸層;訊息帶方法名與例外類型,是可行動訊息且不洩漏
    SSO token。這條測試改回真正的 `load_mcp_connector`(不用 connector_turn_env 的 stub)。"""
    monkeypatch.setattr(chat_turn, "load_mcp_connector", real_load_mcp_connector)
    payload = {
        "sessionId": "sess-unreachable-connector",
        "userId": "user-1",
        "message": "幫我看資料",
        "history": [],
        "sources": [],
        "connectors": [
            {"id": "demo_quality", "name": "示範品質資料", "url": "http://127.0.0.1:1/mcp"}
        ],
    }

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {TEST_BEARER_TOKEN}",
            "X-SSO-Token": "must-not-leak-token",
            "X-SSO-Url": "https://sso.test.example/auth",
        },
    ) as client:
        response = await client.post("/chat", json=payload)

    assert response.status_code == 200
    error_events = [event for event in _sse_events(response.text) if event["type"] == "ERROR"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "CHAT_INIT_FAILED"
    assert "tools/list" in error_events[0]["message"]
    assert "must-not-leak-token" not in error_events[0]["message"]


async def test_empty_connectors_uses_file_mode_unaffected(connector_turn_env, monkeypatch) -> None:
    captured_kwargs: list[dict] = []
    original_build_agent = chat_turn.build_agent

    def spy_build_agent(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return original_build_agent(*args, **kwargs)

    monkeypatch.setattr(chat_turn, "build_agent", spy_build_agent)

    tmp_path = connector_turn_env
    csv_path = tmp_path / "uploads" / "sess-1" / "orders.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("system\nCRM\n", encoding="utf-8")
    request = ChatRequest(
        sessionId="sess-1",
        userId="user-1",
        message="哪個系統最多?",
        history=[],
        sources=[SourceItem(alias="orders", path=str(csv_path), fileType="csv")],
    )

    async with ChatTurn(request) as turn:
        await turn.prepare()
        tool_names = set(turn._agent.nodes["tools"].bound.tools_by_name)
        external_access = turn._connection.execute(
            "SELECT current_setting('enable_external_access')"
        ).fetchone()[0]
        connectors_skill_dir = turn._workspace.skills_dir / "connectors"
        seeded_message = turn._run_input["messages"][-1].content

    assert not any(name.startswith("demo_quality_") for name in tool_names)
    assert external_access is False
    assert not connectors_skill_dir.exists()
    assert "System note" not in seeded_message
    # 檔案模式(無 connectors)不注入 connector 條件段——反向斷言,對齊 connector 模式那條的
    # 正向斷言(見 test_connectors_mode_passes_connector_system_section_to_build_agent)。
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["extra_system_section"] is None


# -- 真的 MCP 線路(不 monkeypatch load_mcp_connector)---------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        return probe_socket.getsockname()[1]


def _run_server_in_thread(app: Any, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return server
        time.sleep(0.02)
    raise RuntimeError("fixture uvicorn server 未在時限內就緒")


@pytest.fixture(scope="module")
def real_mcp_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """真的 `fastmcp` v3 fixture server(pattern 同 tests/test_mcp_adapter.py)——證明
    connectors wiring 走的是真正的 MCP 線路,不只是 monkeypatch 的替身。skill 走目錄式慣例
    (`SkillsDirectoryProvider`),同 mcp_adapter.py 現行契約。"""
    skills_root = tmp_path_factory.mktemp("connector-skills")
    usage_dir = skills_root / "usage"
    usage_dir.mkdir()
    (usage_dir / "SKILL.md").write_text(
        "---\n"
        "name: fixture-connector-usage\n"
        "description: fixture connector 的使用 skill。\n"
        "---\n\n"
        "# fixture connector skill\n\n呼叫 ping(message) 取得回聲。",
        encoding="utf-8",
    )

    mcp_server = FastMCP("fixture-connector-server")

    @mcp_server.tool()
    def ping(message: str) -> dict[str, Any]:
        """回傳原樣訊息,驗證 tools/call round-trip。"""
        return {"pong": message}

    mcp_server.add_provider(SkillsDirectoryProvider(roots=skills_root))

    port = _free_port()
    server = _run_server_in_thread(mcp_server.http_app(stateless_http=True), port)

    yield {"base_url": f"http://127.0.0.1:{port}/mcp"}

    server.should_exit = True


async def test_connectors_real_mcp_path_wires_tools_and_skill_through_chat_turn(
    connector_turn_env, monkeypatch, real_mcp_server
) -> None:
    """至少一條測試證明真正的 MCP 線路能透過 `ChatTurn` 走通(其餘測試皆 monkeypatch
    `load_mcp_connector` 換速度)——真實起一個 FastMCP fixture server,`ConnectorSpec.url`
    指過去,`__aenter__` 內對它打 `tools/list`/`resources/read`,驗證掛載的 tool 與 staging
    的 skill 內容是伺服端回傳的真實資料,不是替身。"""
    monkeypatch.setattr(chat_turn, "load_mcp_connector", real_load_mcp_connector)
    request = _connector_request(
        connectors=[
            {
                "id": "fixture_connector",
                "name": "Fixture Connector",
                "url": real_mcp_server["base_url"],
            }
        ]
    )

    async with ChatTurn(
        request, sso_token="test-token", sso_url="https://sso.test.example/auth"
    ) as turn:
        await turn.prepare()
        tool_names = set(turn._agent.nodes["tools"].bound.tools_by_name)
        skill_path = (
            turn._workspace.skills_dir / "connectors" / "fixture-connector-usage" / "SKILL.md"
        )
        skill_content = skill_path.read_text(encoding="utf-8")

    assert "fixture_connector_ping" in tool_names
    assert "name: fixture-connector-usage" in skill_content
    assert "fixture connector skill" in skill_content


# -- 跨 turn remount(需要真的 persist,走 /chat e2e)---------------------------------------


def _connector_chat_payload(**overrides) -> dict:
    payload = {
        "sessionId": "sess-connector-remount",
        "userId": "user-1",
        "message": "幫我看 Fab A 上週的品質數據",
        "history": [],
        "sources": [],
        "connectors": [_DEMO_CONNECTOR_SPEC],
    }
    payload.update(overrides)
    return payload


def _sse_events(raw_body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in raw_body.splitlines()
        if line.startswith("data: ")
    ]


def _land_then_answer_script() -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "demo_quality_get_quality",
                    "id": "call-land",
                    "args": {
                        "fab": "FAB_A",
                        "week": "2026-W32",
                        "land_as": "quality_fab_a",
                    },
                }
            ],
        ),
        AIMessage(content="已取得並落表。"),
    ]


async def test_second_turn_remounts_previously_landed_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setattr(chat_turn, "load_mcp_connector", _stub_load_mcp_connector)
    scripted = ScriptedChatModel(
        [
            *_land_then_answer_script(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call-count",
                        "args": {
                            "sql": "SELECT COUNT(*) AS row_count FROM quality_fab_a",
                            "intent": "驗證 remount 後資料可查",
                        },
                    }
                ],
            ),
            AIMessage(content="共 9 列。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"},
    ) as client:
        first_response = await client.post("/chat", json=_connector_chat_payload())
        assert first_response.status_code == 200
        second_response = await client.post(
            "/chat", json=_connector_chat_payload(message="幫我算列數")
        )
        assert second_response.status_code == 200

    second_turn_events = _sse_events(second_response.text)
    table_events = [event for event in second_turn_events if event["type"] == "TABLE"]
    assert table_events
    # envelope payload {"data": [...9 列...], "errorCode": ""} 寬鬆落表成單列表——data 欄
    # 整包變成 LIST 欄不拆封,故 remount 後這條 turn 2 的 run_sql COUNT(*) 查到的是 1 列,
    # 不是 9。這裡驗證的重點是「remount 真的把 turn 1 落的表接回來、turn 2 能直接查」。
    assert table_events[0]["rows"] == [[1]]

    workspace = build_workspace_store().prepare("user-1", "sess-connector-remount")
    landings = load_landings(workspace)
    assert any(landing["land_as"] == "quality_fab_a" for landing in landings)


def _tamper_zip_entry(zip_path: Path, entry_name: str, new_content: bytes) -> None:
    """重寫一顆已持久化的 generation zip 裡的單一 entry——模擬 `run_sql` 透過鎖門後仍開放
    寫入的 `allowed_directories` 白名單目錄覆寫/竄改已落表 snapshot 檔案(見
    `app.engine.duck.open_locked_connection` docstring 的完整性守則),藉此驗證
    `remount_snapshots` 的雜湊門禁真的擋下遭竄改的資料。"""
    with zipfile.ZipFile(zip_path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries[entry_name] = new_content
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


async def test_second_turn_tampered_snapshot_heals_via_note_instead_of_aborting(
    tmp_path, monkeypatch
) -> None:
    """remount 校驗失敗已改 fail-soft(見上方 `_tamper_zip_entry` docstring 與
    `api_snapshot.remount_snapshots`)——被竄改的 alias 跳過不掛,但整輪不再中止:第二輪
    `prepare()`/`stream()` 正常跑完、不冒出 ERROR 事件,且模型在本輪實際收到的訊息裡
    含有自癒 note(凍結的原始呼叫參數＋「不需徵詢使用者」指令),供模型視需要以原參數
    重新呼叫該 tool 落表。"""
    workspace_root = tmp_path / "ws"
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setattr(chat_turn, "load_mcp_connector", _stub_load_mcp_connector)
    second_turn_model = ScriptedChatModel([AIMessage(content="已重新拉取資料並完成分析。")])
    models = iter([ScriptedChatModel(_land_then_answer_script()), second_turn_model])
    monkeypatch.setattr(chat_turn, "build_model", lambda: next(models))

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"},
    ) as client:
        first_response = await client.post("/chat", json=_connector_chat_payload())
        assert first_response.status_code == 200

        session_dir = (
            workspace_root / "workspace" / "user-1" / "sessions" / "sess-connector-remount"
        )
        zip_candidates = list(session_dir.glob("gen-*.zip"))
        assert len(zip_candidates) == 1
        _tamper_zip_entry(
            zip_candidates[0], "api_snapshots/quality_fab_a.json", b'{"tampered": true}'
        )

        second_response = await client.post(
            "/chat", json=_connector_chat_payload(message="幫我算列數")
        )

    assert second_response.status_code == 200
    error_events = [
        event for event in _sse_events(second_response.text) if event["type"] == "ERROR"
    ]
    assert error_events == []

    assert second_turn_model.received_message_batches
    seed_message_text = second_turn_model.received_message_batches[0][-1].content
    assert "quality_fab_a" in seed_message_text
    assert "demo_quality_get_quality" in seed_message_text
    assert '"fab": "FAB_A"' in seed_message_text
    assert "不需徵詢使用者" in seed_message_text
