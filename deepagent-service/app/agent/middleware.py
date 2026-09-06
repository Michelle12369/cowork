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
    """同一則 AI message 裡的多個 tool call 一次只跑一個, 避免併發寫檔互相覆蓋.
    鎖的範圍是一次 /chat 請求, 不會跨 request 共用."""

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


# 要讀過整個 dashboard skill 資料夾下所有 .md 才算讀過 skill, 名單在 __init__ 用 rglob 動態掃出來.
# 新增 reference 檔會自動被納入, 不用手動維護這份清單.
_DASHBOARD_SKILL_RELATIVE_ROOT = ".skills/builtin/dashboard"
_GATED_TOOL_NAMES = frozenset({"write_file", "edit_file"})
_GATED_FILE_NAME = "dashboard.html"


def _normalized_workspace_path(file_path: str) -> str:
    """把 virtual_mode 底下絕對寫法的 /a/b 跟相對寫法的 a/b 收斂成同一種字串, 方便比對."""
    return file_path.strip().lstrip("/")


class DashboardSkillGateMiddleware(AgentMiddleware):
    """擋掉還沒讀過整個 dashboard skill 資料夾就寫 dashboard.html 的 write_file 和 edit_file.
    讀取紀錄看 thread 的訊息歷史, 不是這個 middleware 實例自己的狀態.
    skill 資料夾不存在或沒有 .md 檔就直接放行."""

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
        """只算嚴格早於這個 tool call 所在訊息之前執行過的 read_file, 同一則訊息裡的不算.
        做法是找到含有目前 tool_call id 的訊息, 只掃它之前的歷史."""
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
