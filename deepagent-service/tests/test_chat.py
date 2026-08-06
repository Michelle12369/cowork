import json

import duckdb
import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage

from app import main as main_module
from app.agent import chat_turn
from app.agent.events import EventBridge
from app.agent.tools.recording import ToolResultRecorder
from app.api.events import ErrorEvent
from app.engine.workspace import LocalWorkspaceStore, WorkspacePersistError
from tests.fake_model import FailingChatModel, ScriptedChatModel


def _sse_events(raw_body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in raw_body.splitlines()
        if line.startswith("data: ")
    ]


DASHBOARD_HTML_CONTENT = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q1"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ tooltip: { trigger: 'axis' } });"
    "</script></body></html>"
)

BROKEN_DASHBOARD_HTML_CONTENT = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q9"];'
    "echarts.init(document.getElementById('c'), 'erd');"
    "</script></body></html>"
)

# 模擬 Java 端 LangGraphAnalysisProvider 帶來的「選定歷史版本」rawHtml -- 已是本服務先前
# 注入過的 artifact(含帶 id 的 __ERD_RESULTS__/主題 script),外加一個可識別的標記字串
# version-marker-v2,用來驗證進場基底重建確實剝掉了注入區塊、又確實沿用了該版內容。
PREVIOUS_VERSION_DASHBOARD_HTML_CONTENT = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script>'
    '<script id="erd-results-data">window.__ERD_RESULTS__ = {"q1": '
    '{"columns": ["system"], "rows": [["CRM"]], "truncated": false}};</script>'
    '<script id="erd-theme">(function(){registerErdTheme();})();</script>'
    "</head><body>"
    '<div id="version-marker-v2">v2 content</div>'
    '<div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q1"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ tooltip: { trigger: 'axis' } });"
    "</script></body></html>"
)

# 模擬模型整份重寫 dashboard.html 這輪的產出——沿用進場基底重建剝掉注入區塊後的內容,只是
# 這次透過 write_file 整份送出,標記字串本身被更新過,
# 用來驗證基底沿用(而非從零重寫)這件事本身,與模型改用哪個工具無關。
PREVIOUS_VERSION_DASHBOARD_HTML_REWRITTEN_CONTENT = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script>'
    "</head><body>"
    '<div id="version-marker-v2">v2 content updated</div>'
    '<div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q1"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ tooltip: { trigger: 'axis' } });"
    "</script></body></html>"
)


def _skill_read_step() -> AIMessage:
    """DashboardSkillGateMiddleware 擋掉未讀過 skill 的 dashboard.html write_file/edit_file——
    每個腳本需要在寫檔前補這一步(讀 SKILL.md + references/examples.md + references/
    html-contract.md 三份)才能通過 gate。回傳新實例(不共用同一個物件),避免多個腳本重用
    同一個 AIMessage.id。"""
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
        ],
    )


@pytest.fixture()
def scripted_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": "SELECT system, COUNT(*) AS tickets FROM orders GROUP BY system",
                            "intent": "各系統工單數",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {"file_path": "dashboard.html", "content": DASHBOARD_HTML_CONTENT},
                    }
                ],
            ),
            AIMessage(content="CRM 系統工單最多,最需要改善。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_dashboard_updated_empty_answer(tmp_path, monkeypatch):
    """儀表板成功寫入且過 guard,但模型最終一輪沒有文字說明(content="")——回歸 Task
    「dashboard 已更新但文字空」修正:此時 ANSWER 應為 DASHBOARD_UPDATED_FALLBACK_MESSAGE,
    不是誤導性的 EMPTY_ANSWER_FALLBACK_MESSAGE(工作明明成功了)。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": "SELECT system, COUNT(*) AS tickets FROM orders GROUP BY system",
                            "intent": "各系統工單數",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {"file_path": "dashboard.html", "content": DASHBOARD_HTML_CONTENT},
                    }
                ],
            ),
            AIMessage(content=""),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_guard_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": "SELECT system, COUNT(*) AS tickets FROM orders GROUP BY system",
                            "intent": "各系統工單數",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": BROKEN_DASHBOARD_HTML_CONTENT,
                        },
                    }
                ],
            ),
            AIMessage(content="CRM 系統工單最多,最需要改善。"),
            # 修復輪：不呼叫任何工具、不修正 dashboard.html -- guard 仍不過。
            AIMessage(content=""),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_guard_failure_empty_answer(tmp_path, monkeypatch):
    """guard 終敗且原始分析輪也沒有文字說明——驗證 ANSWER 這時只有警示句本身(不接
    `\\n\\n` 或掉回 EMPTY_ANSWER_FALLBACK_MESSAGE),見 main.py 的 DASHBOARD_REJECTED_PREFIX
    說明。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": "SELECT system, COUNT(*) AS tickets FROM orders GROUP BY system",
                            "intent": "各系統工單數",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": BROKEN_DASHBOARD_HTML_CONTENT,
                        },
                    }
                ],
            ),
            AIMessage(content=""),
            # 修復輪：不呼叫任何工具、不修正 dashboard.html -- guard 仍不過。
            AIMessage(content=""),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_previous_version(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": "SELECT system, COUNT(*) AS tickets FROM orders GROUP BY system",
                            "intent": "各系統工單數",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": PREVIOUS_VERSION_DASHBOARD_HTML_REWRITTEN_CONTENT,
                        },
                    }
                ],
            ),
            AIMessage(content="已依歷史版本基底更新完成。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


async def _post_chat(tmp_path, previous_dashboard_html: str | None = None) -> list[dict]:
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system\nCRM\nCRM\nERP\n", encoding="utf-8")
    payload = {
        "sessionId": "sess-1",
        "userId": "user-1",
        "message": "哪個系統最需要改善?",
        "history": [],
        "sources": [{"alias": "orders", "path": str(csv_path), "fileType": "csv"}],
    }
    if previous_dashboard_html is not None:
        payload["previousDashboardHtml"] = previous_dashboard_html
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat", json=payload)
    return _sse_events(response.text)


async def test_chat_full_flow_emits_contracted_events(tmp_path, scripted_flow) -> None:
    events = await _post_chat(tmp_path)
    types = [event["type"] for event in events]

    assert "STEP" in types and "TABLE" in types
    dashboard_events = [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert len(dashboard_events) == 1
    assert "window.__ERD_RESULTS__" in dashboard_events[0]["html"]  # 結果已注入
    assert "registerTheme('erd'" in dashboard_events[0]["html"]  # 主題已注入
    assert events[-1] == {"type": "ANSWER", "text": "CRM 系統工單最多,最需要改善。"}


async def test_chat_event_payloads_pin_exact_wire_contract_keys(
    tmp_path, scripted_flow, monkeypatch
) -> None:
    """Java's LangGraphAnalysisProvider deserializes these dicts with Jackson
    @JsonSubTypes -- an added or renamed key breaks that, and a subset check would miss
    an added key entirely. Asserts an exact key set (and value types) per event type,
    for every event type this scripted flow can actually produce, plus ERROR driven
    separately via FailingChatModel. TOKEN is not covered: no fixture drives text
    streaming before the first tool call, so EventBridge never emits one (see
    app/agent/events.py's `_handle_chat_model_stream` -- forwarding stops once
    `tool_started` flips true, and every scripted AIMessage here starts with a tool
    call and empty content).
    """
    events = await _post_chat(tmp_path)

    step_events = [event for event in events if event["type"] == "STEP"]
    assert step_events
    for event in step_events:
        assert set(event.keys()) == {"type", "stepKey", "title", "status"}
        assert isinstance(event["stepKey"], str)
        assert isinstance(event["title"], str)
        assert event["status"] in ("RUNNING", "SUCCESS", "ERROR")

    table_events = [event for event in events if event["type"] == "TABLE"]
    assert table_events
    for event in table_events:
        assert set(event.keys()) == {
            "type",
            "tableId",
            "intent",
            "columns",
            "rows",
            "truncated",
        }
        assert isinstance(event["tableId"], str)
        assert isinstance(event["intent"], str)
        assert isinstance(event["columns"], list)
        assert isinstance(event["rows"], list)
        assert isinstance(event["truncated"], bool)

    dashboard_events = [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert dashboard_events
    for event in dashboard_events:
        assert set(event.keys()) == {"type", "html"}
        assert isinstance(event["html"], str)

    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert answer_events
    for event in answer_events:
        assert set(event.keys()) == {"type", "text"}
        assert isinstance(event["text"], str)

    # Same session, same workspace -- switch the model to drive the ERROR path too, so
    # this one test pins every event type the wire contract defines except TOKEN.
    monkeypatch.setattr(chat_turn, "build_model", lambda: FailingChatModel())
    error_flow_events = await _post_chat(tmp_path)
    error_events = [event for event in error_flow_events if event["type"] == "ERROR"]
    assert error_events
    for event in error_events:
        assert set(event.keys()) == {"type", "code", "message"}
        assert isinstance(event["code"], str)
        assert isinstance(event["message"], str)


async def test_chat_dashboard_file_persisted_in_workspace(tmp_path, scripted_flow) -> None:
    await _post_chat(tmp_path)
    workspace_root = tmp_path / "ws" / "user-1" / "sessions" / "sess-1"
    assert (workspace_root / "dashboard.html").is_file()
    assert (workspace_root / "queries" / "q1.sql").is_file()
    assert (workspace_root / "results" / "q1.json").is_file()


async def test_chat_guard_failure_skips_dashboard_html(
    tmp_path, scripted_flow_guard_failure
) -> None:
    events = await _post_chat(tmp_path)

    assert {
        "type": "STEP",
        "stepKey": "dashboard_guard",
        "title": "dashboard 製作失敗",
        "status": "ERROR",
    } in events
    assert not [event for event in events if event["type"] == "DASHBOARD_HTML"]

    # guard 終敗時模型這輪的文字("CRM 系統工單最多,最需要改善。")仍在講分析結論,對使用者
    # 讀起來像儀表板已經做好了——ANSWER MUST 以警示句開頭戳破這個假成功,不能原樣照登。
    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert len(answer_events) == 1
    assert answer_events[0]["text"].startswith(chat_turn.DASHBOARD_REJECTED_PREFIX)


async def test_chat_guard_failure_with_empty_original_answer_is_only_the_warning(
    tmp_path, scripted_flow_guard_failure_empty_answer
) -> None:
    events = await _post_chat(tmp_path)

    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert len(answer_events) == 1
    # 原文字為空:ANSWER 恰好是警示句本身,不接 "\n\n"、也不落回 EMPTY_ANSWER_FALLBACK_MESSAGE
    # (guard 失敗跟「整輪什麼都沒做出來」是不同原因,不該混用同一句兜底文案)。
    assert answer_events[0]["text"] == chat_turn.DASHBOARD_REJECTED_PREFIX


async def test_chat_guard_failure_non_blocking_still_emits_dashboard_html(
    tmp_path, scripted_flow_guard_failure, monkeypatch
) -> None:
    monkeypatch.setattr(chat_turn, "ERD_GUARD_BLOCKING", False)
    events = await _post_chat(tmp_path)

    assert [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert not [
        event
        for event in events
        if event["type"] == "STEP" and event.get("stepKey") == "dashboard_guard"
    ]

    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert len(answer_events) == 1
    assert not answer_events[0]["text"].startswith(chat_turn.DASHBOARD_REJECTED_PREFIX)


async def test_chat_previous_dashboard_html_becomes_editing_base(
    tmp_path, scripted_flow_previous_version, monkeypatch
) -> None:
    # 用 spy 包住 app/main.py 進場基底重建呼叫的 strip_injected_blocks,直接耦合到那段程式碼
    # 本身。這輪的模型腳本改用 write_file 整份重寫 dashboard.html 之後,workspace 檔案最終
    # 內容已經不能反推「entry-rebuild 真的執行過」——就算整段 entry-rebuild 邏輯被刪掉,
    # 模型腳本裡 hardcode 的 write_file content 一樣能讓「檔案內容含標記、缺注入區塊」這種
    # 事後斷言通過。spy 直接斷言 entry-rebuild 呼叫過 strip_injected_blocks、輸入是這輪的
    # previousDashboardHtml,這段邏輯被刪掉時 spy 從未被呼叫,測試就會失敗。
    entry_rebuild_calls: list[tuple[str, str]] = []
    original_strip_injected_blocks = chat_turn.strip_injected_blocks

    def spy_strip_injected_blocks(html: str) -> str:
        stripped = original_strip_injected_blocks(html)
        entry_rebuild_calls.append((html, stripped))
        return stripped

    monkeypatch.setattr(chat_turn, "strip_injected_blocks", spy_strip_injected_blocks)

    events = await _post_chat(
        tmp_path, previous_dashboard_html=PREVIOUS_VERSION_DASHBOARD_HTML_CONTENT
    )

    # (a) 進場基底重建 MUST 真的呼叫 strip_injected_blocks(previousDashboardHtml),輸出剝掉
    # 帶 id 的注入區塊、留著標記字串——這就是寫進 workspace 當這輪編輯基底的內容。
    assert len(entry_rebuild_calls) == 1
    entry_rebuild_input, entry_rebuild_output = entry_rebuild_calls[0]
    assert entry_rebuild_input == PREVIOUS_VERSION_DASHBOARD_HTML_CONTENT
    assert 'id="version-marker-v2"' in entry_rebuild_output
    assert 'id="erd-results-data"' not in entry_rebuild_output
    assert 'id="erd-theme"' not in entry_rebuild_output

    workspace_root = tmp_path / "ws" / "user-1" / "sessions" / "sess-1"
    workspace_dashboard_html = (workspace_root / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="version-marker-v2"' in workspace_dashboard_html
    assert 'id="erd-results-data"' not in workspace_dashboard_html
    assert 'id="erd-theme"' not in workspace_dashboard_html

    # (b) 最終送出的 DASHBOARD_HTML 事件仍帶著同一個標記字串 -- 模型整份重寫 dashboard.html,
    # 但保留了基底標記,證明基底確實被沿用,不是憑空生出全新內容。
    dashboard_events = [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert len(dashboard_events) == 1
    assert 'id="version-marker-v2"' in dashboard_events[0]["html"]


async def test_chat_without_previous_dashboard_html_unaffected(tmp_path, scripted_flow) -> None:
    # (c) 不帶 previousDashboardHtml 的既有流程不受影響 -- 沿用既有 fixture 重跑一次全流程斷言。
    events = await _post_chat(tmp_path)
    dashboard_events = [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert len(dashboard_events) == 1
    assert events[-1] == {"type": "ANSWER", "text": "CRM 系統工單最多,最需要改善。"}


async def test_chat_dashboard_updated_with_empty_final_text_uses_dashboard_fallback(
    tmp_path, scripted_flow_dashboard_updated_empty_answer
) -> None:
    events = await _post_chat(tmp_path)

    dashboard_events = [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert len(dashboard_events) == 1
    assert events[-1] == {
        "type": "ANSWER",
        "text": chat_turn.DASHBOARD_UPDATED_FALLBACK_MESSAGE,
    }


# -- STREAM_RETRY_MAX_RUNS / _is_transient_stream_error（Task「串流斷線 turn 級自動重試」）------


def _history_seed_request(role: str) -> main_module.ChatRequest:
    return main_module.ChatRequest(
        sessionId="sess-history",
        userId="user-1",
        message="second question",
        history=[main_module.HistoryItem(role=role, text="previous turn text")],
    )


def test_seed_messages_role_assistant_produces_ai_message() -> None:
    """Java 端 LangGraphAnalysisProvider 一律送 `\"assistant\"`(OpenAI 角色詞彙),從未送
    `\"AI\"`——這是實際 wire 上會發生的情況,MUST 重建成 AIMessage,否則每次 checkpoint 缺失
    時整段 AI 歷史都被誤植成 HumanMessage。"""
    messages = chat_turn._seed_messages(_history_seed_request("assistant"))
    assert isinstance(messages[0], AIMessage)
    assert messages[0].content == "previous turn text"


def test_seed_messages_role_user_produces_human_message() -> None:
    messages = chat_turn._seed_messages(_history_seed_request("user"))
    assert isinstance(messages[0], HumanMessage)


def test_seed_messages_role_AI_still_produces_ai_message() -> None:
    """`\"AI\"` 不是目前 Java 端真的會送的值,但保留容錯(便宜、且 wire 契約已經漂移過一次)。"""
    messages = chat_turn._seed_messages(_history_seed_request("AI"))
    assert isinstance(messages[0], AIMessage)


def test_guard_repair_should_stop_identical_sets_stops() -> None:
    previous_errors = {"error A", "error B"}
    current_errors = {"error A", "error B"}
    assert chat_turn._guard_repair_should_stop(previous_errors, current_errors)


def test_guard_repair_should_stop_nothing_fixed_stops_even_if_set_changed() -> None:
    """集合有變化(新錯誤 C 進來),但沒有任何一筆前一輪的錯誤真的消失——仍算卡住。"""
    previous_errors = {"error A", "error B"}
    current_errors = {"error A", "error B", "error C"}
    assert chat_turn._guard_repair_should_stop(previous_errors, current_errors)


def test_guard_repair_should_stop_two_fixed_one_introduced_continues() -> None:
    """前一輪 3 個錯誤修掉 2 個(A、B),同時新冒出 1 個(D)——整份重寫下這是正常進度,
    不該被舊的「數量增加就停」規則誤判。"""
    previous_errors = {"error A", "error B", "error C"}
    current_errors = {"error C", "error D"}
    assert not chat_turn._guard_repair_should_stop(previous_errors, current_errors)


def test_guard_repair_should_stop_all_fixed_but_more_new_errors_still_continues() -> None:
    """兩個前一輪錯誤全部修掉,但整份重寫額外冒出 3 個新錯誤,回報筆數從 2 上升到 3——舊的
    「數量增加就停」規則會在這裡誤判成退步而放棄;實際上前一輪回報的錯誤一個不剩地被修掉了,
    這是真進度,值得再修一輪。這是本次修正要解掉的核心案例(數量比較與集合比較在此案例上
    給出相反答案)。"""
    previous_errors = {"error A", "error B"}
    current_errors = {"error C", "error D", "error E"}
    assert not chat_turn._guard_repair_should_stop(previous_errors, current_errors)


def test_is_transient_stream_error_matches_connection_keywords() -> None:
    assert chat_turn._is_transient_stream_error(ConnectionError("Network connection lost."))
    assert chat_turn._is_transient_stream_error(Exception("Read timed out"))
    assert chat_turn._is_transient_stream_error(httpx.ConnectError("connect failed"))
    # str(exc) 為空但類名本身透露連線性質的情況也算。
    assert chat_turn._is_transient_stream_error(ConnectionResetError())


def test_is_transient_stream_error_rejects_unrelated_errors() -> None:
    assert not chat_turn._is_transient_stream_error(ValueError("bad input"))
    assert not chat_turn._is_transient_stream_error(RuntimeError("something else broke"))


class _CountingFakePump:
    """假 `pump_agent_events`——依呼叫次數回放不同的 queue 內容序列，模擬 producer 第一次
    連線中斷、重試後第二次正常的情境。"""

    def __init__(self, item_sequences: list[list]) -> None:
        self._item_sequences = item_sequences
        self.calls = 0

    async def __call__(self, agent, run_input, run_config, event_queue) -> None:
        items = self._item_sequences[self.calls]
        self.calls += 1
        for item in items:
            await event_queue.put(item)
        await event_queue.put(None)


async def test_stream_agent_turn_retries_once_on_transient_connection_error(monkeypatch) -> None:
    fake_pump = _CountingFakePump(
        [
            [("error", ConnectionError("Network connection lost."))],
            [
                {
                    "event": "on_chat_model_end",
                    "data": {"output": AIMessage(content="重試後正常回答")},
                }
            ],
        ]
    )
    monkeypatch.setattr(chat_turn, "pump_agent_events", fake_pump)
    bridge = EventBridge(ToolResultRecorder())

    events = [event async for event in chat_turn.stream_agent_turn(None, {}, {}, bridge)]

    assert not [event for event in events if event["type"] == "ERROR"]
    assert bridge.final_answer() == "重試後正常回答"
    assert fake_pump.calls == 2


async def test_stream_agent_turn_does_not_retry_non_transient_error(monkeypatch) -> None:
    fake_pump = _CountingFakePump([[("error", ValueError("bad input"))]])
    monkeypatch.setattr(chat_turn, "pump_agent_events", fake_pump)
    bridge = EventBridge(ToolResultRecorder())

    events = [event async for event in chat_turn.stream_agent_turn(None, {}, {}, bridge)]

    assert events == [ErrorEvent(code="AGENT_FAILURE", message="bad input")]
    assert fake_pump.calls == 1


# -- 併發 edit_file lost-update 回歸 -------------------------------------------------------
#
# 這條守的是 `SerializedToolCallsMiddleware` 下併發 edit_file 的 lost-update 行為,與
# dashboard.html 無涉——目標檔改用 notes.md,不受 dashboard.html 專屬的 skill gate 影響,
# 腳本因此可以省去 skill 讀取與 run_sql 這兩步。

_CONCURRENT_EDIT_BASE_NOTES_CONTENT = "# Notes\n<!-- SLOT_A -->\n<!-- SLOT_B -->\n"


@pytest.fixture()
def scripted_flow_concurrent_edit(tmp_path, monkeypatch):
    """同一則 AI message 併發兩個 edit_file 改同一檔案的不相交區段——`perform_string_replacement`
    monkeypatch 把 deepagents `FilesystemBackend.edit` 的讀改寫窗口撐開,沒有序列化中介層時
    後寫者一定覆蓋前寫者(本測試因此在缺陷還在時必紅)。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))

    import time

    from deepagents.backends import filesystem as filesystem_backend

    original_replacement = filesystem_backend.perform_string_replacement

    def slow_replacement(*arguments, **keyword_arguments):
        time.sleep(0.05)
        return original_replacement(*arguments, **keyword_arguments)

    monkeypatch.setattr(filesystem_backend, "perform_string_replacement", slow_replacement)

    scripted = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call1",
                        "args": {
                            "file_path": "notes.md",
                            "content": _CONCURRENT_EDIT_BASE_NOTES_CONTENT,
                        },
                    }
                ],
            ),
            # 一則 AI message 兩個 edit_file -> ToolNode 併發送出。
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "edit_file",
                        "id": "call-edit-a",
                        "args": {
                            "file_path": "notes.md",
                            "old_string": "<!-- SLOT_A -->",
                            "new_string": "<div id='panel-a'>A</div>",
                        },
                    },
                    {
                        "name": "edit_file",
                        "id": "call-edit-b",
                        "args": {
                            "file_path": "notes.md",
                            "old_string": "<!-- SLOT_B -->",
                            "new_string": "<div id='panel-b'>B</div>",
                        },
                    },
                ],
            ),
            AIMessage(content="兩個區塊都已補上。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


async def test_concurrent_edit_file_calls_both_land_on_notes_md(
    tmp_path, scripted_flow_concurrent_edit
) -> None:
    """同一則 AI message 併發兩個 edit_file 改同一檔案的不相交區段時,兩個改動 MUST 都在。"""
    await _post_chat(tmp_path)

    notes_md = (tmp_path / "ws" / "user-1" / "sessions" / "sess-1" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert "panel-a" in notes_md
    assert "panel-b" in notes_md


# -- DashboardSkillGateMiddleware：/chat 端到端 -----------------------------------------------


@pytest.fixture()
def scripted_flow_skill_not_read(tmp_path, monkeypatch):
    """腳本第一則就直接 write_file dashboard.html,完全不先讀 skill——gate MUST 擋下這次寫檔;
    模型收到退貨 ToolMessage 後（腳本裡）不重試,直接給出一句文字結束這輪。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call1",
                        "args": {"file_path": "dashboard.html", "content": DASHBOARD_HTML_CONTENT},
                    }
                ],
            ),
            AIMessage(content="已完成。"),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


async def test_chat_dashboard_write_blocked_before_skill_is_read(
    tmp_path, scripted_flow_skill_not_read
) -> None:
    events = await _post_chat(tmp_path)

    assert not [event for event in events if event["type"] == "DASHBOARD_HTML"]
    workspace_root = tmp_path / "ws" / "user-1" / "sessions" / "sess-1"
    assert not (workspace_root / "dashboard.html").is_file()


async def test_chat_dashboard_write_allowed_after_both_skill_files_read(
    tmp_path, scripted_flow
) -> None:
    """`scripted_flow` 的第一步就是 `_skill_read_step()`(讀 SKILL.md + references/examples.md)
    才 write_file dashboard.html——這條就是 gate 真的放行時的端到端斷言,與
    `test_chat_full_flow_emits_contracted_events` 是同一份事實的兩個角度。"""
    events = await _post_chat(tmp_path)

    assert [event for event in events if event["type"] == "DASHBOARD_HTML"]


# -- gate ＋ 修復迴圈：修復輪的 write_file 不需要重讀 skill -----------------------------------

_REPAIR_ROUND_INITIAL_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q1"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ series: [], tooltip: {} });"
    "</script></body>"  # 缺 </html> 收尾標籤 -- guard 第一輪必退。
)

_REPAIR_ROUND_FIXED_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q1"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ series: [], tooltip: {} });"
    "</script></body></html>"  # 補上 </html> -- guard 通過。
)


@pytest.fixture()
def scripted_flow_repair_round_write_file(tmp_path, monkeypatch):
    """初版 dashboard.html 缺 </html> 收尾標籤、guard 第一輪退貨,觸發 app/main.py 的修復迴圈；
    修復輪只呼叫一次 write_file 整份重寫(不重讀 skill)。這條腳本用來驗證修復輪的 write_file
    不會被 DashboardSkillGateMiddleware 誤擋——初版寫檔前讀過的兩份 skill 檔留在同一 thread 的
    checkpointed 訊息歷史裡,修復輪 MUST 沿用那份歷史,不需要重讀。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": "SELECT system, COUNT(*) AS tickets FROM orders GROUP BY system",
                            "intent": "各系統工單數",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": _REPAIR_ROUND_INITIAL_HTML,
                        },
                    }
                ],
            ),
            AIMessage(content="已完成初版。"),
            # 修復輪：沒有 read_file,直接 write_file 整份重寫 -- 驗證 gate 沿用同一 thread 的
            # 歷史放行。
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-repair",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": _REPAIR_ROUND_FIXED_HTML,
                        },
                    }
                ],
            ),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


async def test_chat_repair_round_write_file_allowed_without_rereading_skill(
    tmp_path, scripted_flow_repair_round_write_file
) -> None:
    """修復輪呼叫 write_file 時,DashboardSkillGateMiddleware MUST 放行——若這條紅了,代表 gate
    把合法的修復迴圈也擋掉了(那是需要停下來回報的真實問題,不是加特例繞過)。"""
    events = await _post_chat(tmp_path)

    assert not [
        event
        for event in events
        if event["type"] == "STEP" and event.get("stepKey") == "dashboard_guard"
    ], events
    dashboard_events = [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert dashboard_events, events
    assert dashboard_events[-1]["html"].rstrip().endswith("</html>")


# -- guard repair loop convergence ---------------------------------------------------------
#
# 修復迴圈舊行為(GUARD_REPAIR_MAX_RUNS 固定 2、只比數量的停滯偵測)在真實案例造成兩個
# 問題:連續多個獨立錯誤的檔案兩輪修不完就被迫放棄,而且模型可能把對的改壞卻沒有東西讓它
# 停下來。新規則:硬上限拉到 5,把前後兩輪的錯誤當集合比對而不是只比數量——集合不變才是
# 真正卡住,數量增加代表改壞了,兩者都立刻停;第一輪永遠會跑(沒有「上一輪」可比較),
# 第二輪起才受這條規則約束。

_GUARD_REPAIR_ROUND0_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    "echarts.registerTheme('erd', {});"
    'const table = window.__ERD_RESULTS__["q99"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ series: [] });"
    "</script></body>"  # 缺 </html> 收尾標籤。
)

# 第 1 輪重寫版:拿掉 registerTheme 呼叫(3 個錯誤 -> 2 個)。
_GUARD_REPAIR_ROUND1_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q99"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ series: [] });"
    "</script></body>"
)

# 第 2 輪重寫版:把不存在的 q99 改回真實的 q1(2 個錯誤 -> 1 個)。
_GUARD_REPAIR_ROUND2_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q1"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ series: [] });"
    "</script></body>"
)

# 第 3 輪重寫版:補上 </html>(1 個錯誤 -> 0,guard 通過)。
_GUARD_REPAIR_ROUND3_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    '<body><div id="c"></div><script>'
    'const table = window.__ERD_RESULTS__["q1"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ series: [] });"
    "</script></body></html>"
)


@pytest.fixture()
def scripted_flow_guard_repair_converges_over_three_rounds(tmp_path, monkeypatch):
    """初版 dashboard.html 帶 3 個互相獨立的錯誤(registerTheme 呼叫、引用不存在的 q99、缺
    </html> 收尾標籤),修復輪逐一修掉、每輪錯誤數嚴格下降(3 -> 2 -> 1 -> 0),第 3 輪才全綠
    ——舊的 GUARD_REPAIR_MAX_RUNS=2 會在能收斂前放棄。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": "SELECT system, COUNT(*) AS tickets FROM orders GROUP BY system",
                            "intent": "各系統工單數",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": _GUARD_REPAIR_ROUND0_HTML,
                        },
                    }
                ],
            ),
            AIMessage(content="初版完成。"),
            # 第 1 輪:整份重寫,拿掉 registerTheme 呼叫(3 個錯誤 -> 2 個)。
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-repair-1",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": _GUARD_REPAIR_ROUND1_HTML,
                        },
                    }
                ],
            ),
            AIMessage(content=""),
            # 第 2 輪:整份重寫,把不存在的 q99 改回真實的 q1(2 個錯誤 -> 1 個)。
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-repair-2",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": _GUARD_REPAIR_ROUND2_HTML,
                        },
                    }
                ],
            ),
            AIMessage(content=""),
            # 第 3 輪:整份重寫,補上 </html>(1 個錯誤 -> 0,guard 通過)。
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-repair-3",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": _GUARD_REPAIR_ROUND3_HTML,
                        },
                    }
                ],
            ),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


async def test_guard_repair_continues_while_error_count_drops(
    tmp_path, scripted_flow_guard_repair_converges_over_three_rounds
) -> None:
    """錯誤數逐輪嚴格下降(3 -> 2 -> 1 -> 0)時,修復迴圈 MUST 一路跑到全綠,即使需要 3 輪
    ——舊的固定上限 2 會在收斂前放棄,這裡驗證 dashboard 最終確實出貨。"""
    events = await _post_chat(tmp_path)

    assert not [
        event
        for event in events
        if event["type"] == "STEP" and event.get("stepKey") == "dashboard_guard"
    ], events
    dashboard_events = [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert dashboard_events, events
    assert dashboard_events[-1]["html"].rstrip().endswith("</html>")


# 哨兵:若迴圈誤跑了第 2 輪,這份可辨識的完整 HTML 會被 write_file 寫入 dashboard.html。
_GUARD_REPAIR_STALL_SENTINEL_MARKER = "sentinel-round-should-not-run"
_GUARD_REPAIR_STALL_SENTINEL_HTML = (
    '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
    f'<body><div id="c" data-marker="{_GUARD_REPAIR_STALL_SENTINEL_MARKER}"></div><script>'
    'const table = window.__ERD_RESULTS__["q9"];'
    "const chart = echarts.init(document.getElementById('c'), 'erd');"
    "chart.setOption({ tooltip: {} });"
    "</script></body></html>"
)


@pytest.fixture()
def scripted_flow_guard_repair_stalls(tmp_path, monkeypatch):
    """修復輪什麼都不改(錯誤數持平)——退步/停滯偵測 MUST 讓迴圈在第 1 輪後立刻停止,不再
    嘗試第 2 輪。腳本裡留一個「第 2 輪才會用到」的 write_file 當哨兵:斷言迴圈提前停止時它
    從未被消耗、dashboard.html 內容原封不動。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": "SELECT system, COUNT(*) AS tickets FROM orders GROUP BY system",
                            "intent": "各系統工單數",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": BROKEN_DASHBOARD_HTML_CONTENT,
                        },
                    }
                ],
            ),
            AIMessage(content="初版完成。"),
            # 第 1 輪:不呼叫任何工具,dashboard.html 原封不動 -- 錯誤數持平。
            AIMessage(content=""),
            # 哨兵:若迴圈誤跑了第 2 輪,這個 write_file 會被消耗、dashboard.html 會被改成
            # 帶有哨兵標記的內容。
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-should-not-run",
                        "args": {
                            "file_path": "dashboard.html",
                            "content": _GUARD_REPAIR_STALL_SENTINEL_HTML,
                        },
                    }
                ],
            ),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


async def test_guard_repair_stops_when_error_count_stops_dropping(
    tmp_path, scripted_flow_guard_repair_stalls
) -> None:
    """錯誤數不再下降時,迴圈 MUST 在那一輪後立刻停止,不消耗下一輪的腳本(哨兵 write_file
    從未執行、dashboard.html 內容不含哨兵標記,且維持原封不動)。"""
    await _post_chat(tmp_path)

    workspace_root = tmp_path / "ws" / "user-1" / "sessions" / "sess-1"
    dashboard_html = (workspace_root / "dashboard.html").read_text(encoding="utf-8")
    assert _GUARD_REPAIR_STALL_SENTINEL_MARKER not in dashboard_html
    assert dashboard_html == BROKEN_DASHBOARD_HTML_CONTENT


# -- guard repair loop convergence: reported error count clamped by html_guard -------------
#
# `html_guard` caps the getCol-miss report to a fixed number of entries plus one summary line,
# so a file with more misses than the cap keeps reporting the same total count round after
# round even while the real number of broken bindings goes down -- fixing some of the visible
# misses just lets previously-hidden ones take their place in the next report. Comparing only
# `len(report.errors)` mistakes that pinned count for a stall and gives up early. Comparing the
# error set itself sees through it: the specific entries differ even when the count doesn't.

_CLAMP_MISS_TOTAL = 12


def _clamp_miss_line(miss_index: int, column_name: str) -> str:
    return f"const idx{miss_index} = getCol(table.columns, '{column_name}');"


def _clamp_dashboard_html(column_names: list[str]) -> str:
    calls = "\n".join(
        _clamp_miss_line(miss_index, column_name)
        for miss_index, column_name in enumerate(column_names)
    )
    return (
        '<html><head><script src="https://cdn.tailwindcss.com"></script></head>'
        '<body><div id="c"></div><script>\n'
        "function getCol(columns, ...candidates) {\n"
        "  for (const candidate of candidates) {\n"
        "    const index = columns.indexOf(candidate);\n"
        "    if (index >= 0) return index;\n"
        "  }\n"
        "  console.warn('[ERD] column not found:', candidates); return -1;\n"
        "}\n"
        "const table = window.__ERD_RESULTS__['q1'];\n"
        f"{calls}\n"
        "</script></body></html>"
    )


_CLAMP_ROUND0_HTML = _clamp_dashboard_html([f"wrong{i}" for i in range(_CLAMP_MISS_TOTAL)])


def _clamp_dashboard_html_with_fixes(fixed_miss_indices: set[int]) -> str:
    """整份重寫版:`fixed_miss_indices` 內的欄位改綁 good{index},其餘仍是 wrong{index}——
    用來組出每一輪修復後應該出現的完整 dashboard.html。"""
    column_names = [
        f"good{miss_index}" if miss_index in fixed_miss_indices else f"wrong{miss_index}"
        for miss_index in range(_CLAMP_MISS_TOTAL)
    ]
    return _clamp_dashboard_html(column_names)


# Round 1 重寫版:修掉 round 0 clamped 報告裡可見的前 3 個(索引 0-2)。
_CLAMP_ROUND1_HTML = _clamp_dashboard_html_with_fixes({0, 1, 2})
# Round 2 重寫版:再修掉 round 1 報告裡可見的 8 個(索引 3-10),累計 0-10 已修好。
_CLAMP_ROUND2_HTML = _clamp_dashboard_html_with_fixes(set(range(11)))
# Round 3 重寫版:修掉最後一個(索引 11),12 個全部修好,guard 通過。
_CLAMP_ROUND3_HTML = _clamp_dashboard_html_with_fixes(set(range(_CLAMP_MISS_TOTAL)))


@pytest.fixture()
def scripted_flow_guard_repair_clamped_count_still_converges(tmp_path, monkeypatch):
    """Initial dashboard.html binds 12 columns to the wrong query result -- more misses than
    `html_guard`'s report cap, so round 0's report shows only the first 8 plus a summary line
    (9 total). Round 1 fixes 3 of those 8 visible misses; 9 real misses remain, so the report
    still totals 9 (8 shown + "...and 1 more") even though the true count dropped from 12 to 9.
    The repair loop must not mistake that pinned total for a stall -- round 2 fixes the next 8
    visible misses, round 3 fixes the last one, and the dashboard ships."""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    select_columns = ", ".join(f"system AS good{i}" for i in range(_CLAMP_MISS_TOTAL))
    scripted = ScriptedChatModel(
        [
            _skill_read_step(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call1",
                        "args": {
                            "sql": f"SELECT {select_columns} FROM orders LIMIT 1",
                            "intent": "取得所有欄位供綁定",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call2",
                        "args": {"file_path": "dashboard.html", "content": _CLAMP_ROUND0_HTML},
                    }
                ],
            ),
            AIMessage(content="初版完成。"),
            # Round 1: rewrite in full, fixing 3 of the 8 misses visible in round 0's clamped
            # report.
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-repair-clamp-1",
                        "args": {"file_path": "dashboard.html", "content": _CLAMP_ROUND1_HTML},
                    }
                ],
            ),
            AIMessage(content=""),
            # Round 2: rewrite in full, fixing the 8 misses now visible in round 1's (still
            # 9-total) report.
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-repair-clamp-2",
                        "args": {"file_path": "dashboard.html", "content": _CLAMP_ROUND2_HTML},
                    }
                ],
            ),
            AIMessage(content=""),
            # Round 3: rewrite in full, fixing the one remaining miss -- guard passes.
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-repair-clamp-3",
                        "args": {"file_path": "dashboard.html", "content": _CLAMP_ROUND3_HTML},
                    }
                ],
            ),
        ]
    )
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)
    return scripted


async def test_guard_repair_continues_when_reported_count_is_pinned_by_the_clamp(
    tmp_path, scripted_flow_guard_repair_clamped_count_still_converges
) -> None:
    """Round 0 -> round 1 keeps the reported error count at 9 (clamp), even though the real
    miss count dropped from 12 to 9 -- the loop must keep going instead of treating the pinned
    count as a stall, and the dashboard must eventually ship once all 12 bindings are fixed."""
    events = await _post_chat(tmp_path)

    assert not [
        event
        for event in events
        if event["type"] == "STEP" and event.get("stepKey") == "dashboard_guard"
    ], events
    dashboard_events = [event for event in events if event["type"] == "DASHBOARD_HTML"]
    assert dashboard_events, events
    assert "wrong" not in dashboard_events[-1]["html"], dashboard_events[-1]["html"]


async def test_chat_empty_first_round_retries_and_uses_second_round_answer(
    tmp_path, monkeypatch
) -> None:
    # 首輪空回應(無文字、無工具啟動)時 chat() 會重新 invoke 同一份 run_input。
    # 釘住「重試確實發生」與「最終 ANSWER 取自重試那一輪」。
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel([AIMessage(content=""), AIMessage(content="重試後的結論。")])
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)

    events = await _post_chat(tmp_path)

    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert answer_events[-1]["text"] == "重試後的結論。"


async def test_chat_no_text_and_no_dashboard_falls_back_to_empty_answer_message(
    tmp_path, monkeypatch
) -> None:
    # 首輪與 FIRST_ROUND_RETRY_MAX_RUNS 兩輪重試都空、且本輪沒發出 DASHBOARD_HTML
    # → ANSWER 走 EMPTY_ANSWER_FALLBACK_MESSAGE。
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel([AIMessage(content="")])
    monkeypatch.setattr(chat_turn, "build_model", lambda: scripted)

    events = await _post_chat(tmp_path)

    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert answer_events[-1]["text"] == chat_turn.EMPTY_ANSWER_FALLBACK_MESSAGE


async def test_chat_error_terminates_stream_and_still_closes_connection(
    tmp_path, monkeypatch
) -> None:
    # 釘住兩件事,重構把 chat() 拆成 ChatTurn 之後必須仍成立：
    #   (a) ERROR 是本輪最後一個事件——之後不再有 ANSWER 或任何其他事件
    #   (b) 早退仍會執行 teardown——duckdb 連線確實被關閉
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    opened_connections: list[object] = []
    original_open = chat_turn.open_locked_connection

    def tracking_open(sources):
        connection = original_open(sources)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(chat_turn, "open_locked_connection", tracking_open)
    monkeypatch.setattr(chat_turn, "build_model", lambda: FailingChatModel())

    events = await _post_chat(tmp_path)

    error_indexes = [index for index, event in enumerate(events) if event["type"] == "ERROR"]
    assert error_indexes, "本測試需要一個會觸發 ERROR 的模型"
    assert error_indexes[-1] == len(events) - 1, "ERROR 之後不應再有任何事件"

    assert opened_connections, "本輪應該開過一個 duckdb 連線"
    with pytest.raises(duckdb.ConnectionException):
        opened_connections[0].execute("SELECT 1")


async def test_chat_aenter_failure_after_connection_open_still_closes_connection(
    tmp_path, monkeypatch
) -> None:
    # __aexit__ 只在 __aenter__ 成功 return self 時才會被呼叫——open_locked_connection 之後、
    # return self 之前的任何一步拋例外(這裡用 build_agent 模擬)時,async with 從未真正進入,
    # 不會呼叫 __aexit__。ChatTurn.__aenter__ MUST 自己在那段內 try/except 關閉連線再重新拋出。
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    opened_connections: list[object] = []
    original_open = chat_turn.open_locked_connection

    def tracking_open(sources):
        connection = original_open(sources)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(chat_turn, "open_locked_connection", tracking_open)

    def failing_build_agent(*args, **kwargs):
        raise RuntimeError("boom during agent assembly")

    monkeypatch.setattr(chat_turn, "build_agent", failing_build_agent)

    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system\nCRM\nCRM\nERP\n", encoding="utf-8")
    payload = {
        "sessionId": "sess-1",
        "userId": "user-1",
        "message": "哪個系統最需要改善?",
        "history": [],
        "sources": [{"alias": "orders", "path": str(csv_path), "fileType": "csv"}],
    }
    # fastapi.sse 的 SSE producer 用 anyio TaskGroup 包住這個 async generator,原本的
    # RuntimeError 會被包成 BaseExceptionGroup 才傳到呼叫端——用 group_contains 斷言真正的
    # 例外還在裡面,而不是被吞掉或換成別的錯誤。
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(BaseException) as exception_info:
            await client.post("/chat", json=payload)
    assert exception_info.group_contains(RuntimeError, match="boom during agent assembly")

    assert opened_connections, "本輪應該開過一個 duckdb 連線"
    with pytest.raises(duckdb.ConnectionException):
        opened_connections[0].execute("SELECT 1")


def test_build_callbacks_gate_follows_tracing_enabled_flag(monkeypatch):
    """_build_callbacks 的開關看 `tracing.is_tracing_enabled()`，不再直接看 Settings 的
    LANGFUSE_PUBLIC_KEY——runtime 完整接管建構時 client 不一定源自那兩個 key。"""
    import app.agent.tracing as tracing_module

    monkeypatch.setattr(tracing_module, "_tracing_enabled", False)
    assert chat_turn._build_callbacks() == []

    monkeypatch.setattr(tracing_module, "_tracing_enabled", True)
    callbacks = chat_turn._build_callbacks()
    assert len(callbacks) == 1


class _PersistTrackingStore:
    """包一個真的 store,把 persist() 換成可控行為(記次數、視需要拋錯)——用來測
    ChatTurn.finalize() 的 WORKSPACE_PERSIST_FAILED 分支,不必真的接 S3。也記
    cleanup_scratch() 呼叫次數,驗證 ChatTurn.__aexit__ 在任何退出路徑都會呼叫它
    (s3 模式 per-turn scratch 清理的終審修正點)。"""

    def __init__(self, delegate, persist_error: Exception | None = None) -> None:
        self._delegate = delegate
        self._persist_error = persist_error
        self.persist_calls = 0
        self.cleanup_scratch_calls = 0

    def prepare(self, user_id: str, session_id: str):
        return self._delegate.prepare(user_id, session_id)

    def persist(self, workspace) -> None:
        self.persist_calls += 1
        if self._persist_error is not None:
            raise self._persist_error

    def cleanup_scratch(self) -> None:
        self.cleanup_scratch_calls += 1


async def test_chat_persist_failure_emits_error_event_right_after_answer(
    tmp_path, scripted_flow, monkeypatch
) -> None:
    tracking_store = _PersistTrackingStore(
        LocalWorkspaceStore(tmp_path / "ws"), persist_error=WorkspacePersistError("boom")
    )
    monkeypatch.setattr(chat_turn, "build_workspace_store", lambda: tracking_store)

    events = await _post_chat(tmp_path)

    assert tracking_store.persist_calls == 1
    answer_index = next(index for index, event in enumerate(events) if event["type"] == "ANSWER")
    assert events[answer_index + 1] == {
        "type": "ERROR",
        "code": "WORKSPACE_PERSIST_FAILED",
        "message": "本輪結果未能寫入儲存空間,下一輪可能拿不到這次的變更。",
    }
    assert answer_index + 1 == len(events) - 1
    # persist 失敗(拋 WorkspacePersistError)也是 __aexit__ 的正常退出路徑之一——scratch
    # MUST 照樣被清,不能因為 persist 失敗就洩漏。
    assert tracking_store.cleanup_scratch_calls == 1


async def test_chat_persist_success_emits_no_error_event(
    tmp_path, scripted_flow, monkeypatch
) -> None:
    tracking_store = _PersistTrackingStore(LocalWorkspaceStore(tmp_path / "ws"))
    monkeypatch.setattr(chat_turn, "build_workspace_store", lambda: tracking_store)

    events = await _post_chat(tmp_path)

    assert tracking_store.persist_calls == 1
    assert not [event for event in events if event["type"] == "ERROR"]
    assert tracking_store.cleanup_scratch_calls == 1


async def test_chat_turn_aexit_calls_cleanup_scratch_on_early_error_return(
    tmp_path, monkeypatch
) -> None:
    """stream() 因 FailingChatModel 以 ErrorEvent 提前終止(main.py 的 `/chat` 端點直接
    return,finalize() 從未執行)——這正是終審點名的洩漏路徑之一;`async with ChatTurn(...)`
    保證 __aexit__ 仍會執行,cleanup_scratch() 因此仍要被呼叫到。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    tracking_store = _PersistTrackingStore(LocalWorkspaceStore(tmp_path / "ws"))
    monkeypatch.setattr(chat_turn, "build_workspace_store", lambda: tracking_store)
    monkeypatch.setattr(chat_turn, "build_model", lambda: FailingChatModel())

    events = await _post_chat(tmp_path)

    assert [event for event in events if event["type"] == "ERROR"]
    assert tracking_store.persist_calls == 0
    assert tracking_store.cleanup_scratch_calls == 1


async def test_chat_turn_aenter_failure_calls_cleanup_scratch_before_reraising(
    tmp_path, monkeypatch
) -> None:
    """__aenter__ 在 build_agent 失敗時的 except BaseException 區塊 MUST 自己呼叫
    cleanup_scratch()——__aexit__ 在這條路徑上不會被呼叫(__aenter__ 從未成功 return self),
    這是終審點名「build_agent 失敗也洩漏」的那個分支。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    tracking_store = _PersistTrackingStore(LocalWorkspaceStore(tmp_path / "ws"))
    monkeypatch.setattr(chat_turn, "build_workspace_store", lambda: tracking_store)

    def failing_build_agent(*args, **kwargs):
        raise RuntimeError("boom during agent assembly")

    monkeypatch.setattr(chat_turn, "build_agent", failing_build_agent)

    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system\nCRM\nCRM\nERP\n", encoding="utf-8")
    payload = {
        "sessionId": "sess-1",
        "userId": "user-1",
        "message": "哪個系統最需要改善?",
        "history": [],
        "sources": [{"alias": "orders", "path": str(csv_path), "fileType": "csv"}],
    }
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(BaseException) as exception_info:
            await client.post("/chat", json=payload)
    assert exception_info.group_contains(RuntimeError, match="boom during agent assembly")
    assert tracking_store.cleanup_scratch_calls == 1
