"""Per-request tool-result recorder -- correlates a LangChain tool `run_id` with the
`ToolRunRecord` its `run_sql` invocation produced, so `EventBridge.on_tool_end` (running on the
event loop, driven by `astream_events`) can find and pop the matching record without racing a
concurrent `/chat` request's own `run_sql` calls. Each `/chat` request gets its own
`ToolResultRecorder` instance (built in `app.main.chat`), so there is nothing shared across
requests to race.
"""

import threading
from dataclasses import dataclass

from langchain_core.callbacks import Callbacks


@dataclass(frozen=True)
class ToolRunRecord:
    query_id: str
    intent: str
    columns: list[str]
    rows: list[list]
    truncated: bool


class ToolResultRecorder:
    """non-bean: instantiate per /chat request.

    Thread-safe channel from a tool wrapper's executor thread (`record()`) to `EventBridge`'s
    `on_tool_end` handling on the event loop (`pop()`). LangChain runs a sync `@tool` function
    via `run_in_executor` even inside an async graph, so `record()` executes on a worker thread
    while `pop()` executes on the event loop thread -- a plain dict would not be safe here.

    Primary key is the LangChain tool run_id, read inside the tool wrapper via a parameter
    literally named `callbacks` (LangChain special-cases and excludes it from the LLM-visible
    args schema, injecting `run_manager.get_child()`; that child's `parent_run_id` equals the
    tool's own `run_id` as seen in `astream_events`).

    Fallback: if `callbacks` ever comes back without a usable run_id, both methods degrade to
    FIFO order -- a reasonable last-resort approximation given the same lock also serializes
    `record()` ordering and the event loop consumes `on_tool_end` in completion order.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records_by_run_id: dict[str, ToolRunRecord] = {}
        self._fifo_fallback: list[ToolRunRecord] = []

    def record(self, run_id: str | None, record: ToolRunRecord) -> None:
        with self._lock:
            if run_id is None:
                self._fifo_fallback.append(record)
                return
            self._records_by_run_id[run_id] = record

    def pop(self, run_id: str | None) -> ToolRunRecord | None:
        with self._lock:
            if run_id is not None and run_id in self._records_by_run_id:
                return self._records_by_run_id.pop(run_id)
            if self._fifo_fallback:
                return self._fifo_fallback.pop(0)
            return None


def tool_run_id(callbacks: Callbacks) -> str | None:
    """A param literally named `callbacks` is special-cased by LangChain's `StructuredTool._run`
    (excluded from the LLM-visible args schema, injected via `run_manager.get_child()`); its
    `parent_run_id` equals the tool's `run_id` from `astream_events` -- the correlation key
    `ToolResultRecorder` is keyed by. Returns None if `callbacks` carries no `parent_run_id`."""
    if callbacks is None:
        return None
    parent_run_id = getattr(callbacks, "parent_run_id", None)
    return str(parent_run_id) if parent_run_id is not None else None
