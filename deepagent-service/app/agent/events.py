"""`agent.astream_events(version="v2")` → wire 事件橋接。欄位名是硬契約——Java
`LangGraphAnalysisProvider` 用 Jackson `@JsonSubTypes` 對齊，改欄位名即斷反序列化。
`EventBridge` per-request 有狀態，不可跨請求共用；`pump_agent_events` 是生產者，把事件
全量丟進 queue 讓消費者（FastAPI SSE handler）自控 heartbeat 逾時。
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from app.agent.tools.recording import ToolResultRecorder, ToolRunRecord
from app.api.events import StepEvent, TableEvent, TokenEvent

logger = logging.getLogger(__name__)

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
        self._recorder = recorder
        self._tool_started_at: dict[str, float] = {}
        self._model_started_at: float | None = None

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
            self._model_started_at = time.monotonic()
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
        self._tool_started_at[str(agent_event.get("run_id"))] = time.monotonic()
        logger.debug("tool start name=%s", agent_event["name"])
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
        started_at = self._tool_started_at.pop(str(agent_event.get("run_id")), None)
        duration_seconds = time.monotonic() - started_at if started_at is not None else -1.0
        logger.info(
            "tool done name=%s status=%s duration=%.2fs",
            agent_event["name"],
            status,
            duration_seconds,
        )
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
        duration_seconds = (
            time.monotonic() - self._model_started_at
            if self._model_started_at is not None
            else -1.0
        )
        usage = getattr(message, "usage_metadata", None) or {}
        logger.info(
            "model call done duration=%.2fs tool_calls=%d input_tokens=%s output_tokens=%s",
            duration_seconds,
            len(tool_calls),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )

    def final_answer(self) -> str:
        return self.last_answer_text or self.current_text or ""

    def heartbeat_event(self) -> StepEvent | None:
        """重發 active_steps 頂端（最後 push 的）RUNNING STEP——同一物件再 yield 一次；Java
        端把重複 STEP 視為狀態更新，安全。無進行中 step 時回 None。"""
        if not self.active_steps:
            return None
        return self.active_steps[-1]


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
