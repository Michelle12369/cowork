import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from app import main as main_module
from app.agent.events import EventBridge
from app.agent.tools.recording import ToolResultRecorder
from tests.fake_model import ScriptedChatModel


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


@pytest.fixture()
def scripted_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
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
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_dashboard_updated_empty_answer(tmp_path, monkeypatch):
    """儀表板成功寫入且過 guard,但模型最終一輪沒有文字說明(content="")——回歸 Task
    「dashboard 已更新但文字空」修正:此時 ANSWER 應為 DASHBOARD_UPDATED_FALLBACK_MESSAGE,
    不是誤導性的 EMPTY_ANSWER_FALLBACK_MESSAGE(工作明明成功了)。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
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
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_guard_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
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
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_guard_failure_empty_answer(tmp_path, monkeypatch):
    """guard 終敗且原始分析輪也沒有文字說明——驗證 ANSWER 這時只有警示句本身(不接
    `\\n\\n` 或掉回 EMPTY_ANSWER_FALLBACK_MESSAGE),見 main.py 的 DASHBOARD_REJECTED_PREFIX
    說明。"""
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
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
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)
    return scripted


@pytest.fixture()
def scripted_flow_previous_version(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel(
        [
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
                        "name": "edit_file",
                        "id": "call2",
                        "args": {
                            "file_path": "dashboard.html",
                            "old_string": '<div id="version-marker-v2">v2 content</div>',
                            "new_string": '<div id="version-marker-v2">v2 content updated</div>',
                        },
                    }
                ],
            ),
            AIMessage(content="已依歷史版本基底更新完成。"),
        ]
    )
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)
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
    assert answer_events[0]["text"].startswith(main_module.DASHBOARD_REJECTED_PREFIX)


async def test_chat_guard_failure_with_empty_original_answer_is_only_the_warning(
    tmp_path, scripted_flow_guard_failure_empty_answer
) -> None:
    events = await _post_chat(tmp_path)

    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert len(answer_events) == 1
    # 原文字為空:ANSWER 恰好是警示句本身,不接 "\n\n"、也不落回 EMPTY_ANSWER_FALLBACK_MESSAGE
    # (guard 失敗跟「整輪什麼都沒做出來」是不同原因,不該混用同一句兜底文案)。
    assert answer_events[0]["text"] == main_module.DASHBOARD_REJECTED_PREFIX


async def test_chat_previous_dashboard_html_becomes_editing_base(
    tmp_path, scripted_flow_previous_version
) -> None:
    events = await _post_chat(
        tmp_path, previous_dashboard_html=PREVIOUS_VERSION_DASHBOARD_HTML_CONTENT
    )

    workspace_root = tmp_path / "ws" / "user-1" / "sessions" / "sess-1"
    workspace_dashboard_html = (workspace_root / "dashboard.html").read_text(encoding="utf-8")
    # (a) 進場基底重建已把 previousDashboardHtml 剝掉帶 id 的注入區塊、寫進 workspace 當這輪
    # 的編輯基底 -- 標記字串留著,注入 script 不見了。之後全程沒有其他步驟會把注入 script 寫
    # 回這個檔案(注入只發生在送出 DASHBOARD_HTML 事件前、對記憶體中的字串做,見 app/main.py
    # 呼叫 inject_results/inject_theme 的位置),所以就算在整輪跑完後才讀檔驗證也成立。
    assert 'id="version-marker-v2"' in workspace_dashboard_html
    assert 'id="erd-results-data"' not in workspace_dashboard_html
    assert 'id="erd-theme"' not in workspace_dashboard_html

    # (b) 最終送出的 DASHBOARD_HTML 事件仍帶著同一個標記字串 -- 基底確實被沿用、模型只是用
    # edit_file 局部修改,不是從零重寫整份 dashboard.html。
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
        "text": main_module.DASHBOARD_UPDATED_FALLBACK_MESSAGE,
    }


# -- STREAM_RETRY_MAX_RUNS / _is_transient_stream_error（Task「串流斷線 turn 級自動重試」）------


def test_is_transient_stream_error_matches_connection_keywords() -> None:
    assert main_module._is_transient_stream_error(ConnectionError("Network connection lost."))
    assert main_module._is_transient_stream_error(Exception("Read timed out"))
    assert main_module._is_transient_stream_error(httpx.ConnectError("connect failed"))
    # str(exc) 為空但類名本身透露連線性質的情況也算。
    assert main_module._is_transient_stream_error(ConnectionResetError())


def test_is_transient_stream_error_rejects_unrelated_errors() -> None:
    assert not main_module._is_transient_stream_error(ValueError("bad input"))
    assert not main_module._is_transient_stream_error(RuntimeError("something else broke"))


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
    monkeypatch.setattr(main_module, "pump_agent_events", fake_pump)
    bridge = EventBridge(ToolResultRecorder())

    events = [event async for event in main_module._stream_agent_turn(None, {}, {}, bridge)]

    assert not [event for event in events if event["type"] == "ERROR"]
    assert bridge.final_answer() == "重試後正常回答"
    assert fake_pump.calls == 2


async def test_stream_agent_turn_does_not_retry_non_transient_error(monkeypatch) -> None:
    fake_pump = _CountingFakePump([[("error", ValueError("bad input"))]])
    monkeypatch.setattr(main_module, "pump_agent_events", fake_pump)
    bridge = EventBridge(ToolResultRecorder())

    events = [event async for event in main_module._stream_agent_turn(None, {}, {}, bridge)]

    assert events == [{"type": "ERROR", "code": "AGENT_FAILURE", "message": "bad input"}]
    assert fake_pump.calls == 1


# -- 併發 edit_file lost-update 回歸 -------------------------------------------------------

_CONCURRENT_EDIT_BASE_HTML = (
    "<html><head></head><body>\n"
    "<div id='chart'></div>\n"
    "<!-- SLOT_A -->\n"
    "<!-- SLOT_B -->\n"
    "<script>const data = window.__ERD_RESULTS__['q1'];\n"
    "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
    "chart.setOption({ tooltip: {}, series: [] });</script>\n"
    "</body></html>"
)


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
                            "content": _CONCURRENT_EDIT_BASE_HTML,
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
                            "file_path": "dashboard.html",
                            "old_string": "<!-- SLOT_A -->",
                            "new_string": "<div id='panel-a'>A</div>",
                        },
                    },
                    {
                        "name": "edit_file",
                        "id": "call-edit-b",
                        "args": {
                            "file_path": "dashboard.html",
                            "old_string": "<!-- SLOT_B -->",
                            "new_string": "<div id='panel-b'>B</div>",
                        },
                    },
                ],
            ),
            AIMessage(content="兩個區塊都已補上。"),
        ]
    )
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)
    return scripted


async def test_concurrent_edit_file_calls_both_land(
    tmp_path, scripted_flow_concurrent_edit
) -> None:
    """同一則 AI message 併發兩個 edit_file 改同一檔案的不相交區段時,兩個改動 MUST 都在。"""
    await _post_chat(tmp_path)

    dashboard_html = (
        tmp_path / "ws" / "user-1" / "sessions" / "sess-1" / "dashboard.html"
    ).read_text(encoding="utf-8")
    assert "panel-a" in dashboard_html
    assert "panel-b" in dashboard_html
