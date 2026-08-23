from app.agent.events import EventBridge
from app.api.events import StepEvent, TokenEvent


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
    bridge = EventBridge()
    [running] = bridge.handle(_tool_start("run_sql", "r1"))
    assert running == StepEvent(stepKey="tool_run_sql_r1", title="查詢資料", status="RUNNING")
    emitted = bridge.handle(_tool_end("run_sql", "r1"))
    assert emitted[0].status == "SUCCESS"


def test_dashboard_write_gets_assembly_title() -> None:
    bridge = EventBridge()
    [running] = bridge.handle(
        _tool_start("write_file", "r2", {"file_path": "dashboard.html", "content": "<div>"})
    )
    assert running.title == "製作 dashboard"


def test_skill_read_gets_skill_title() -> None:
    bridge = EventBridge()
    [running] = bridge.handle(
        _tool_start("read_file", "r3", {"file_path": ".skills/builtin/dashboard/SKILL.md"})
    )
    assert running.title == "載入 dashboard skills"


def test_tokens_forwarded_only_before_first_tool() -> None:
    bridge = EventBridge()

    class _Chunk:
        content = "先看看資料"

    stream_event = {"event": "on_chat_model_stream", "data": {"chunk": _Chunk()}}
    assert bridge.handle(stream_event) == [TokenEvent(delta="先看看資料")]
    bridge.handle(_tool_start("run_sql", "r1"))
    assert bridge.handle(stream_event) == []
