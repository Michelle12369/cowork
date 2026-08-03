"""`agent.astream_events(version="v2")` → wire 事件橋接。欄位名是硬契約——Java
`LangGraphAnalysisProvider` 用 Jackson `@JsonSubTypes` 對齊，改欄位名即斷反序列化。
`EventBridge` per-request 有狀態，不可跨請求共用；`pump_agent_events` 是生產者，把事件
全量丟進 queue 讓消費者（FastAPI SSE handler）自控 heartbeat 逾時。
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.agent.tools.recording import ToolResultRecorder, ToolRunRecord
from app.api.events import StepEvent, TableEvent, TokenEvent

TABLE_EVENT_MAX_ROWS = 200

_WORK_FILE_TOOL_NAMES = {"ls", "glob", "grep"}


def step_title_for(tool_name: str, tool_input: dict) -> str:
    """人類可讀的 STEP 標題——依工具名與（file_path 類工具的）輸入路徑決定，內容不上 wire。"""
    if tool_name == "get_schema":
        return "查詢資料結構"
    if tool_name == "run_sql":
        return "查詢資料"
    if tool_name == "preview_data":
        return "預覽資料"
    if tool_name == "write_todos":
        return "規劃分析步驟"
    if tool_name in _WORK_FILE_TOOL_NAMES:
        return "檢視 workspace"
    if tool_name == "read_file":
        file_path = tool_input.get("file_path") or ""
        return "載入 dashboard skills" if ".skills/" in file_path else "檢視 workspace"
    if tool_name in ("write_file", "edit_file"):
        file_path = tool_input.get("file_path") or ""
        return "製作 dashboard" if "dashboard.html" in file_path else "整理分析筆記"
    return "處理中"


def _extract_text(content: object) -> str:
    """chunk.content 可能是純字串，也可能是 list-of-parts（多模態/reasoning 拆分格式，每個
    part 是帶 "text" 鍵的 dict）——兩種都正規化成純文字，其餘 part 型別（如 image）略過。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
        return "".join(text_parts)
    return ""


def _tool_step_key(agent_event: dict) -> str:
    return f"tool_{agent_event['name']}_{agent_event['run_id']}"


class EventBridge:
    """non-bean: instantiate per /chat request — 持有 active_steps/token 累積狀態，跨請求
    共用會讓不同 session 的 STEP 堆疊互相污染。`recorder` 同樣 MUST 是本次請求專屬實例。"""

    def __init__(self, recorder: ToolResultRecorder) -> None:
        self.active_steps: list[StepEvent] = []
        self.tool_started = False
        self.current_text = ""
        self.last_answer_text: str | None = None
        # 任一次 on_chat_model_end 的 response_metadata 出現過 finish_reason == "length"
        # 就恆為 True -- 截斷可能發生在稍早的工具呼叫輪,不是只有最後一輪算數。
        self.saw_truncated_finish_reason = False
        self._recorder = recorder
        # #3: last StepEvent this bridge has actually put on the wire (via _handle_tool_start/
        # _handle_tool_end/flush_active_steps), regardless of whether it's still "active". See
        # heartbeat_event() -- this is what keeps the wire from going silent once tool_started.
        self._last_emitted_step: StepEvent | None = None

    def handle(self, agent_event: dict) -> list[StepEvent | TokenEvent | TableEvent]:
        event_type = agent_event["event"]
        if event_type == "on_tool_start":
            return self._handle_tool_start(agent_event)
        if event_type == "on_tool_end":
            return self._handle_tool_end(agent_event, status="SUCCESS", pop_record=True)
        if event_type == "on_tool_error":
            return self._handle_tool_end(agent_event, status="ERROR", pop_record=False)
        if event_type == "on_chat_model_start":
            self.current_text = ""
            return []
        if event_type == "on_chat_model_stream":
            return self._handle_chat_model_stream(agent_event)
        if event_type == "on_chat_model_end":
            self._handle_chat_model_end(agent_event)
            return []
        return []

    def _handle_tool_start(self, agent_event: dict) -> list[StepEvent]:
        tool_input = agent_event.get("data", {}).get("input") or {}
        step = StepEvent(
            stepKey=_tool_step_key(agent_event),
            title=step_title_for(agent_event["name"], tool_input),
            status="RUNNING",
        )
        self.active_steps.append(step)
        self.tool_started = True
        self._last_emitted_step = step
        return [step]

    def _handle_tool_end(
        self, agent_event: dict, *, status: str, pop_record: bool
    ) -> list[StepEvent | TableEvent]:
        step_key = _tool_step_key(agent_event)
        title = step_title_for(agent_event["name"], {})
        for index, active_step in enumerate(self.active_steps):
            if active_step.stepKey == step_key:
                title = active_step.title
                del self.active_steps[index]
                break
        events: list[StepEvent | TableEvent] = [
            StepEvent(stepKey=step_key, title=title, status=status)
        ]
        self._last_emitted_step = events[0]
        if not pop_record:
            return events
        # on_tool_end 一律 pop（不只 run_sql）——其他工具結束時 pop 回 None 無害,能順便清掉
        # 殘留。on_tool_error 不 pop:run_sql 失敗走 SQL_ERROR 字串回傳,不會觸發 on_tool_error。
        record: ToolRunRecord | None = self._recorder.pop(agent_event.get("run_id"))
        if record is not None:
            rows = record.rows[:TABLE_EVENT_MAX_ROWS]
            events.append(
                TableEvent(
                    tableId=record.query_id,
                    intent=record.intent,
                    columns=record.columns,
                    rows=rows,
                    truncated=record.truncated or len(record.rows) > TABLE_EVENT_MAX_ROWS,
                )
            )
        return events

    def _handle_chat_model_stream(self, agent_event: dict) -> list[TokenEvent]:
        chunk = agent_event["data"]["chunk"]
        text = _extract_text(chunk.content)
        self.current_text += text
        # 開場思路（工具開跑前）轉發給使用者看；工具開跑後中段 chatter 不上 wire，終局由
        # ANSWER 承載（見 handle 的 event_type 分派與 brief 的單迴圈 deep agent 語意）。
        if not self.tool_started and text:
            return [TokenEvent(delta=text)]
        return []

    def _handle_chat_model_end(self, agent_event: dict) -> None:
        message = agent_event["data"]["output"]
        tool_calls = getattr(message, "tool_calls", None) or []
        text = _extract_text(getattr(message, "content", ""))
        if not tool_calls and text:
            self.last_answer_text = text
        # `parse_partial_json` repairs a cut-off tool-call argument (e.g. a half-written
        # dashboard.html) into a structurally valid dict on the streaming path, so
        # `invalid_tool_calls` never surfaces the truncation -- `finish_reason` is the only
        # authoritative signal left.
        response_metadata = getattr(message, "response_metadata", None) or {}
        if response_metadata.get("finish_reason") == "length":
            self.saw_truncated_finish_reason = True

    def final_answer(self) -> str:
        return self.last_answer_text or self.current_text or ""

    def flush_active_steps(self, status: str = "ERROR") -> list[StepEvent]:
        """F2: 回傳 `active_steps` 現存每筆的終態版本(同一 stepKey/title,status 覆寫成
        `status`)並清空 `active_steps`。呼叫端 MUST 在同一輪重試前呼叫一次:重試若沿用同一個
        `EventBridge`,前一次嘗試已送出 RUNNING 的工具若因為 checkpoint resume 不會再跑一次,
        就永遠不會有 on_tool_end/on_tool_error 替它送終態——`heartbeat_event()` 會把它當成
        `active_steps[-1]` 每 `HEARTBEAT_INTERVAL_SECONDS` 重送一次,使用者看到一個永遠不會
        停的 spinner。"""
        flushed_steps = [
            StepEvent(stepKey=step.stepKey, title=step.title, status=status)
            for step in self.active_steps
        ]
        self.active_steps.clear()
        if flushed_steps:
            # These terminal STEPs go out on the wire too (the retry loop yields them) --
            # remember the last one so heartbeat_event() doesn't fall silent right after a
            # retry, same as any other STEP this bridge emits.
            self._last_emitted_step = flushed_steps[-1]
        return flushed_steps

    def heartbeat_event(self) -> StepEvent | None:
        """#3: `active_steps` alone used to gate this -- once `on_tool_end` removed the last
        active step, `heartbeat_event()` returned None and stayed silent for every subsequent
        model generation this turn (TOKENs also stop once `tool_started`, see
        `_handle_chat_model_stream`), including the one that writes the entire dashboard, the
        longest generation of the turn. FastAPI's automatic `: ping` comment doesn't cover for
        it: it carries no `data`, so it never resets Java's per-event idle timeout (180s).

        Fix: fall back to `_last_emitted_step`, the last STEP this bridge actually put on the
        wire (verbatim, status included), once `active_steps` is empty. Re-sending an
        already-delivered STEP is safe -- the frontend (`useAgentStream.ts`) upserts by
        stepKey, so a repeated identical STEP is invisible to the user -- and unlike a comment
        it's a real SSE `data:` element, so it does reset the Java timeout. Only returns None
        when nothing has been emitted yet this turn, i.e. TOKENs are still flowing and the wire
        genuinely isn't silent.
        """
        if self.active_steps:
            return self.active_steps[-1]
        return self._last_emitted_step


async def pump_agent_events(
    agent: Any,
    run_input: dict,
    run_config: dict,
    event_queue: "asyncio.Queue[Any]",
) -> None:
    """non-bean: instantiate per /chat request via asyncio.create_task.

    把 `agent.astream_events` 全量丟進 queue，讓消費者的 heartbeat timeout 只命中 queue
    讀取，不會打斷 LangGraph 執行中的 tool call。"""
    try:
        agent_event_stream: AsyncIterator[dict[str, Any]] = agent.astream_events(
            run_input, config=run_config, version="v2"
        )
        async for agent_event in agent_event_stream:
            await event_queue.put(agent_event)
    except BaseException as error:  # forwarded to the consumer via the queue, not swallowed.
        await event_queue.put(("error", error))
        if isinstance(error, asyncio.CancelledError):
            raise
    finally:
        await event_queue.put(None)
