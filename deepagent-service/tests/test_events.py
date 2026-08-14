from langchain_core.messages import AIMessage

from app.agent.events import EventBridge, step_title_for
from app.agent.tools.recording import ToolResultRecorder, ToolRunRecord
from app.api.events import StepEvent, TableEvent, TokenEvent


def _chat_model_end(run_id: str, content: str) -> dict:
    return {
        "event": "on_chat_model_end",
        "name": "ChatOpenAI",
        "run_id": run_id,
        "data": {"output": AIMessage(content=content)},
    }


def _tool_start(name: str, run_id: str, tool_input: dict | None = None) -> dict:
    return {
        "event": "on_tool_start",
        "name": name,
        "run_id": run_id,
        "data": {"input": tool_input or {}},
    }


def _tool_end(name: str, run_id: str) -> dict:
    return {"event": "on_tool_end", "name": name, "run_id": run_id, "data": {}}


def test_tool_lifecycle_maps_to_step_events() -> None:
    bridge = EventBridge(ToolResultRecorder())
    [running] = bridge.handle(_tool_start("run_sql", "r1"))
    assert running == StepEvent(stepKey="tool_run_sql_r1", title="查詢資料", status="RUNNING")
    emitted = bridge.handle(_tool_end("run_sql", "r1"))
    assert emitted[0].status == "SUCCESS"


def test_dashboard_write_gets_assembly_title() -> None:
    bridge = EventBridge(ToolResultRecorder())
    [running] = bridge.handle(
        _tool_start("write_file", "r2", {"file_path": "dashboard.html", "content": "<div>"})
    )
    assert running.title == "製作 dashboard"


def test_skill_read_gets_skill_title() -> None:
    bridge = EventBridge(ToolResultRecorder())
    [running] = bridge.handle(
        _tool_start("read_file", "r3", {"file_path": ".skills/builtin/dashboard/SKILL.md"})
    )
    assert running.title == "載入 dashboard skills"


def test_tokens_forwarded_only_before_first_tool() -> None:
    bridge = EventBridge(ToolResultRecorder())

    class _Chunk:
        content = "先看看資料"

    stream_event = {"event": "on_chat_model_stream", "data": {"chunk": _Chunk()}}
    assert bridge.handle(stream_event) == [TokenEvent(delta="先看看資料")]
    bridge.handle(_tool_start("run_sql", "r1"))
    assert bridge.handle(stream_event) == []


def test_table_event_emitted_from_recorded_run() -> None:
    recorder = ToolResultRecorder()
    record = ToolRunRecord("q1", "各系統工單數", ["system"], [["CRM"]], truncated=False)
    # Recorded under this run's own run_id -- exactly how run_sql_tool records it via the
    # `callbacks` param's parent_run_id (see app.agent.tools.recording.tool_run_id).
    recorder.record("r1", record)
    bridge = EventBridge(recorder)
    bridge.handle(_tool_start("run_sql", "r1"))
    emitted = bridge.handle(_tool_end("run_sql", "r1"))
    assert (
        TableEvent(
            tableId="q1",
            intent="各系統工單數",
            columns=["system"],
            rows=[["CRM"]],
            truncated=False,
        )
        in emitted
    )


def test_table_event_keyed_by_run_id_not_shared_across_bridges() -> None:
    """Regression for Finding 1 -- two EventBridge instances (one per /chat request, as
    app.main.chat builds them) each with their own recorder must never surface the other's
    recorded TABLE result, even when both runs use overlapping run_id-shaped strings."""
    recorder_one = ToolResultRecorder()
    recorder_one.record("r1", ToolRunRecord("q1", "第一個請求", ["a"], [[1]], truncated=False))
    recorder_two = ToolResultRecorder()
    recorder_two.record("r1", ToolRunRecord("q1", "第二個請求", ["a"], [[2]], truncated=False))

    bridge_one = EventBridge(recorder_one)
    bridge_two = EventBridge(recorder_two)

    bridge_one.handle(_tool_start("run_sql", "r1"))
    bridge_two.handle(_tool_start("run_sql", "r1"))

    emitted_one = bridge_one.handle(_tool_end("run_sql", "r1"))
    emitted_two = bridge_two.handle(_tool_end("run_sql", "r1"))

    table_one = next(event for event in emitted_one if isinstance(event, TableEvent))
    table_two = next(event for event in emitted_two if isinstance(event, TableEvent))
    assert table_one.intent == "第一個請求" and table_one.rows == [[1]]
    assert table_two.intent == "第二個請求" and table_two.rows == [[2]]


def test_heartbeat_reemits_top_running_step() -> None:
    bridge = EventBridge(ToolResultRecorder())
    bridge.handle(_tool_start("run_sql", "r1"))
    assert bridge.heartbeat_event() == StepEvent(
        stepKey="tool_run_sql_r1", title="查詢資料", status="RUNNING"
    )
    bridge.handle(_tool_end("run_sql", "r1"))
    assert bridge.heartbeat_event() is None


def test_step_title_for_task_is_dashboard_title() -> None:
    assert step_title_for("task", {"subagent_type": "dashboard-renderer"}) == "製作 dashboard"


def test_bridge_suppresses_subagent_model_events_during_task() -> None:
    """task 執行期間(dashboard-renderer subagent 內部)的 chat_model_end 不得成為
    last_answer_text——subagent 最終訊息是整份 HTML、無 tool_calls,不擋就會蓋掉主 agent
    真正的收尾文字。"""
    bridge = EventBridge(ToolResultRecorder())
    bridge.handle(_tool_start("task", "r1"))
    bridge.handle(_chat_model_end("r2", "<!DOCTYPE html><html></html>"))
    bridge.handle(_tool_end("task", "r1"))
    bridge.handle(_chat_model_end("r3", "儀表板完成"))
    assert bridge.final_answer() == "儀表板完成"


def test_bridge_suppresses_inner_tool_steps_during_task() -> None:
    bridge = EventBridge(ToolResultRecorder())
    bridge.handle(_tool_start("task", "r1"))
    inner_events = bridge.handle(_tool_start("read_file", "r2"))
    assert inner_events == []
    bridge.handle(_tool_end("task", "r1"))
    after_events = bridge.handle(_tool_start("read_file", "r3"))
    assert len(after_events) == 1
