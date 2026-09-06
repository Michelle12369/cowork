"""這裡把 agent.astream_events(version="v2") 的事件橋接成 wire 事件. 欄位名是硬契約: Java
的 LangGraphAnalysisProvider 用 Jackson 的 JsonSubTypes 對齊這些欄位, 改了欄位名就會讓
反序列化壞掉. EventBridge 是 per-request 的有狀態物件, 不能跨請求共用.
"""

from app.api.events import StepEvent, TokenEvent

_WORK_FILE_TOOL_NAMES = {"ls", "glob", "grep"}


def step_title_for(tool_name: str, tool_input: dict) -> str:
    """回傳人類可讀的 STEP 標題, 依工具名稱以及 file_path 這類工具的輸入路徑決定; 原始
    輸入內容本身不會送上 wire, 只有算出來的標題會."""
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
        if ".skills/connectors/" in file_path:
            return "讀 connector skill"
        return "載入 dashboard skills" if ".skills/" in file_path else "檢視 workspace"
    if tool_name in ("write_file", "edit_file"):
        file_path = tool_input.get("file_path") or ""
        return "製作 dashboard" if "dashboard.html" in file_path else "整理分析筆記"
    return "處理中"


def _extract_text(content: object) -> str:
    """chunk.content 可能是純字串, 也可能是一個 list of parts(多模態或 reasoning 拆分後
    的格式, 每個 part 是帶 "text" 鍵的 dict), 這裡把兩種情況都正規化成純文字, 其他 part
    型別(例如 image)就略過."""
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
    """每個 /chat request 各自建立一個實例: 它持有 active_steps 和 token 累積狀態,
    跨請求共用會讓不同 session 的 STEP 堆疊互相污染."""

    def __init__(self) -> None:
        self.active_steps: list[StepEvent] = []
        self.tool_started = False
        self.current_text = ""
        self.last_answer_text: str | None = None

    def handle(self, agent_event: dict) -> list[StepEvent | TokenEvent]:
        event_type = agent_event["event"]
        if event_type == "on_tool_start":
            return self._handle_tool_start(agent_event)
        if event_type == "on_tool_end":
            return self._handle_tool_end(agent_event, status="SUCCESS")
        if event_type == "on_tool_error":
            return self._handle_tool_end(agent_event, status="ERROR")
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
        return [step]

    def _handle_tool_end(self, agent_event: dict, *, status: str) -> list[StepEvent]:
        step_key = _tool_step_key(agent_event)
        title = step_title_for(agent_event["name"], {})
        for index, active_step in enumerate(self.active_steps):
            if active_step.stepKey == step_key:
                title = active_step.title
                del self.active_steps[index]
                break
        return [StepEvent(stepKey=step_key, title=title, status=status)]

    def _handle_chat_model_stream(self, agent_event: dict) -> list[TokenEvent]:
        chunk = agent_event["data"]["chunk"]
        text = _extract_text(chunk.content)
        self.current_text += text
        # 工具開跑前的開場思路會轉發給使用者看; 工具開跑之後中段的 chatter 不會送上 wire,
        # 最終的答案由 ANSWER 事件承載(細節看 handle 的 event_type 分派邏輯).
        if not self.tool_started and text:
            return [TokenEvent(delta=text)]
        return []

    def _handle_chat_model_end(self, agent_event: dict) -> None:
        message = agent_event["data"]["output"]
        tool_calls = getattr(message, "tool_calls", None) or []
        text = _extract_text(getattr(message, "content", ""))
        if not tool_calls and text:
            self.last_answer_text = text

    def final_answer(self) -> str:
        return self.last_answer_text or self.current_text or ""
