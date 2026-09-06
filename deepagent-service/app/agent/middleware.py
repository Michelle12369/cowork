"""這裡是主 agent 用的 AgentMiddleware. deepagents 只會把自訂 middleware 掛在主 agent 上,
子代理的 middleware 是各自的 subagent spec 帶的, 所以這裡的鎖不會跟 task 工具互鎖."""

import asyncio
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.engine.results import format_wiring_manifest, load_all_results
from app.engine.workspace import SessionWorkspace

ToolCallHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]
ModelCallHandler = Callable[[ModelRequest], Awaitable[AIMessage]]


class SerializedToolCallsMiddleware(AgentMiddleware):
    """同一則 AI message 裡的多個 tool call 一次只跑一個. ToolNode 預設用 asyncio.gather
    併發送出 tool call, 但 deepagents 的 write_file 和 edit_file 是沒有鎖的讀改寫, 併發打
    同一個檔案會靜默地互相覆蓋. 這個鎖的範圍是一次 /chat 請求(每個 request 各自
    build_agent), 不會跨 request 共用.
    """

    def __init__(self) -> None:
        super().__init__()
        self._tool_call_lock = asyncio.Lock()

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        async with self._tool_call_lock:
            return await handler(request)


class WiringManifestMiddleware(AgentMiddleware):
    """每次呼叫模型都把目前的 qN 清單, intent, 欄位附在 system message 後面. 這是每次呼叫
    都重建, 不是每一輪只做一次, 因為同一輪裡常見先查詢再寫 dashboard 的流程, 輪次剛開始時
    results 還不存在, 如果只在輪次開始時注入一次就對這種情境沒用.
    """

    def __init__(self, workspace: SessionWorkspace) -> None:
        super().__init__()
        self._workspace = workspace

    async def awrap_model_call(self, request: ModelRequest, handler: ModelCallHandler) -> AIMessage:
        manifest_text = format_wiring_manifest(load_all_results(self._workspace))
        if not manifest_text:
            return await handler(request)
        existing_text = request.system_message.content if request.system_message else ""
        return await handler(
            request.override(system_message=SystemMessage(f"{existing_text}\n\n{manifest_text}"))
        )


# 要把整個 dashboard skill 資料夾底下所有的 .md 都讀過才算讀過 skill: SKILL.md 講規則,
# references/ 底下每個檔案給的是可以直接用的寫法, CDN 白名單這類逐字契約也在裡面. 這份
# 清單是在 __init__ 用 rglob 動態掃出來的, 不寫死檔名: 只讀部分內容很容易漏掉某份
# reference 的細節, guard 會因此退件要求重寫; 新增 reference 檔也會自動被納入必讀, 不用
# 回頭維護這裡的清單.
_DASHBOARD_SKILL_RELATIVE_ROOT = ".skills/builtin/dashboard"
_GATED_TOOL_NAMES = frozenset({"write_file", "edit_file"})
_GATED_FILE_NAME = "dashboard.html"


def _normalized_workspace_path(file_path: str) -> str:
    """把 virtual_mode 底下絕對寫法的 /a/b 跟相對寫法的 a/b 收斂成同一種字串, 方便比對."""
    return file_path.strip().lstrip("/")


class DashboardSkillGateMiddleware(AgentMiddleware):
    """在這個 thread 裡還沒讀過整個 dashboard skill 資料夾(.skills/builtin/dashboard 底下
    所有 .md)之前, 擋掉對 dashboard.html 的 write_file 和 edit_file. 檢查的方式是掃
    thread 的訊息歷史(request.state), 不是看 middleware 實例自己的狀態, 因為這個實例是
    per-request 建立的, 記不住上一輪讀過什麼. 只在寫檔的時候擋, 不會每一輪都主動注入,
    因為 references 內容量不小, 會加劇已知的 reasoning 跑飛問題; 如果 skill 資料夾不存在
    或底下完全沒有 .md 檔, 就直接放行.
    """

    def __init__(self, workspace: SessionWorkspace) -> None:
        super().__init__()
        skill_root = workspace.root / _DASHBOARD_SKILL_RELATIVE_ROOT
        self._required_paths = tuple(
            sorted(
                _normalized_workspace_path(str(markdown_path.relative_to(workspace.root)))
                for markdown_path in skill_root.rglob("*.md")
            )
        )

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        if not self._is_gated_dashboard_write(request):
            return await handler(request)
        unread_paths = self._unread_required_paths(request)
        if not unread_paths:
            return await handler(request)
        required_list = "\n".join(f"- {path}" for path in self._required_paths)
        return ToolMessage(
            content=(
                "Blocked: dashboard.html MUST NOT be written before the dashboard skill has "
                "been read in this conversation. Read ALL of these first with read_file "
                f"(pass limit=1000, the 100-line default truncates them):\n{required_list}\n"
                "Then retry this write."
            ),
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def _is_gated_dashboard_write(self, request: ToolCallRequest) -> bool:
        if not self._required_paths:
            return False
        if request.tool_call["name"] not in _GATED_TOOL_NAMES:
            return False
        file_path = request.tool_call.get("args", {}).get("file_path", "")
        return _normalized_workspace_path(str(file_path)) == _GATED_FILE_NAME

    def _unread_required_paths(self, request: ToolCallRequest) -> list[str]:
        """只算嚴格早於這個 tool call 所在的 AI message 之前執行過的 read_file. 同一則訊息
        可能一次吐出 read_file 加 write_file 等多個 tool call, 這種情況下 write 的內容早
        在 read 真正執行前就已經產生了, 不算讀過. 做法是找出含有目前 tool_call id 的那則
        訊息, 只掃它之前的訊息, 而不是用丟掉最後一則這種位置假設去猜, 那樣容易誤判."""
        current_tool_call_id = request.tool_call.get("id")
        messages = request.state.get("messages", []) if isinstance(request.state, dict) else []
        read_paths: set[str] = set()
        for message in messages:
            tool_calls = getattr(message, "tool_calls", None) or []
            if any(tool_call.get("id") == current_tool_call_id for tool_call in tool_calls):
                break
            for tool_call in tool_calls:
                if tool_call.get("name") != "read_file":
                    continue
                read_paths.add(
                    _normalized_workspace_path(str(tool_call.get("args", {}).get("file_path", "")))
                )
        return [path for path in self._required_paths if path not in read_paths]
