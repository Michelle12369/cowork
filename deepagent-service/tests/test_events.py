from app.agent.events import EventBridge
from app.agent.tools.recording import ToolResultRecorder, ToolRunRecord
from app.api.events import StepEvent, TableEvent, TokenEvent


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
    # #3: active_steps is now empty, but a STEP already went out on the wire this turn, so
    # heartbeat_event() must keep re-sending it (status included) instead of falling back to
    # None -- see test_heartbeat_after_tool_end_reemits_last_step_event_not_none below.
    assert bridge.heartbeat_event() == StepEvent(
        stepKey="tool_run_sql_r1", title="查詢資料", status="SUCCESS"
    )


def test_heartbeat_before_any_step_is_none() -> None:
    """No STEP has gone out on the wire yet -- TOKENs are still flowing at this point (see
    `_handle_chat_model_stream`), so the wire isn't silent and heartbeat_event() correctly
    stays None. This is the only case where None is still the right answer."""
    bridge = EventBridge(ToolResultRecorder())
    assert bridge.heartbeat_event() is None


def test_heartbeat_after_tool_end_reemits_last_step_event_not_none() -> None:
    """#3: after on_tool_end empties active_steps, every subsequent model generation this
    turn -- including the one that writes the entire dashboard, the longest one -- stops
    emitting TOKENs (`tool_started` is True) and previously left the wire with zero events
    until the next tool call. FastAPI's automatic `: ping` comment doesn't save it: it carries
    no `data`, so it never resets Java's per-event idle timeout (180s). Re-sending the last
    STEP verbatim is safe: `useAgentStream.ts` upserts steps by stepKey, so a repeated
    identical STEP is invisible to the user, and it's a real SSE `data:` element."""
    bridge = EventBridge(ToolResultRecorder())
    bridge.handle(_tool_start("run_sql", "r1"))
    bridge.handle(_tool_end("run_sql", "r1"))

    assert bridge.heartbeat_event() == StepEvent(
        stepKey="tool_run_sql_r1", title="查詢資料", status="SUCCESS"
    )


def test_flush_active_steps_emits_terminal_event_and_clears() -> None:
    """F2: a tool START that already went out on the wire, with no matching on_tool_end/
    on_tool_error yet, must not linger in active_steps across a retry -- otherwise
    heartbeat_event() keeps re-sending it as RUNNING forever."""
    bridge = EventBridge(ToolResultRecorder())
    bridge.handle(_tool_start("run_sql", "r1"))

    flushed = bridge.flush_active_steps()

    assert flushed == [StepEvent(stepKey="tool_run_sql_r1", title="查詢資料", status="ERROR")]
    assert bridge.active_steps == []
    # #3: the flushed STEP also went out on the wire (the retry loop yields it), so it's the
    # remembered event now -- heartbeat must keep re-sending it rather than fall silent while
    # the retry's next model generation runs.
    assert bridge.heartbeat_event() == flushed[0]


def test_flush_active_steps_flushes_every_entry_in_order() -> None:
    bridge = EventBridge(ToolResultRecorder())
    bridge.handle(_tool_start("run_sql", "r1"))
    bridge.handle(_tool_start("preview_data", "r2"))

    flushed = bridge.flush_active_steps()

    assert [step.stepKey for step in flushed] == ["tool_run_sql_r1", "tool_preview_data_r2"]
    assert all(step.status == "ERROR" for step in flushed)


def test_flush_active_steps_noop_when_nothing_active() -> None:
    bridge = EventBridge(ToolResultRecorder())
    assert bridge.flush_active_steps() == []
