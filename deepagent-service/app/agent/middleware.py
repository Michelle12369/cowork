"""主 agent 的 AgentMiddleware——deepagents 只把自訂 middleware 掛到主 agent，
子代理的 middleware 由各自的 subagent spec 帶，故此處的鎖不會與 `task` 工具互鎖。"""

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
    """同一則 AI message 的多個 tool call 一次只跑一個。

    LangGraph 的 ToolNode 預設把它們 `asyncio.gather` 併發送出，而 deepagents 的
    `write_file`/`edit_file` 是無鎖讀改寫——併發打同一檔案會靜默互相覆蓋、兩邊都回報成功。
    `build_agent` 是 per-request 建立，所以這把鎖的範圍是一次 `/chat`（含其修復輪），
    不跨 request——同一 session 併發兩個 request 仍會對同一份 workspace 檔案競爭。
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
    """每次 model call 都把「目前有哪些 qN、各自的 intent 與欄位」附在 system message 後面。

    模型原本是憑幾十個 tool call 之前的對話記憶對應 qN 編號，綁錯是常態。每次呼叫重建
    (而非每輪一次)是必要的——同一輪內先跑查詢後寫 dashboard 是主要情境，turn 開始時那些
    results 還不存在,turn-start 注入對這個情境完全無效。
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


# gate 的單位是三份檔案,不是一份:SKILL.md 講規則、examples.md 給可運作的具體寫法、
# html-contract.md 給 CDN 白名單/skeleton/erd theme 這些寫死的契約細節——CDN URL 差一個字元、
# 用舊版 tailwindcss@2、忘記套 erd theme,都是規則段落點到但沒有 html-contract.md 逐字給出的
# 具體字串就容易憑印象寫錯,guard 會直接退件重寫,三份一起讀才構成「讀過 skill」。
_REQUIRED_SKILL_RELATIVE_PATHS: tuple[str, ...] = (
    ".skills/builtin/dashboard/SKILL.md",
    ".skills/builtin/dashboard/references/examples.md",
    ".skills/builtin/dashboard/references/html-contract.md",
)
_GATED_TOOL_NAMES = frozenset({"write_file", "edit_file"})
_GATED_FILE_NAME = "dashboard.html"


def _normalized_workspace_path(file_path: str) -> str:
    """把 virtual_mode 的絕對寫法 `/a/b` 與相對寫法 `a/b` 收斂成同一種字串,好做比對。"""
    return file_path.strip().lstrip("/")


class DashboardSkillGateMiddleware(AgentMiddleware):
    """thread 內沒讀過 dashboard skill 的 SKILL.md、references/examples.md 與
    references/html-contract.md 之前,擋掉對 dashboard.html 的 write_file/edit_file,
    退貨訊息直接給路徑。

    判定掃的是 `request.state` 的訊息歷史(thread 層級,延續輪繼承先前輪次的 read),不是
    middleware 實例狀態——`build_agent` 是 per-request 建立,實例狀態記不住上一輪的 read。
    gate 只在寫檔動作上擋,不做每輪注入:四份 references 共 46KB,每輪注入會加劇這個模型
    已知的 reasoning runaway。staged skill 檔不存在(沒 stage skills 的部署)一律 fail-open。
    """

    def __init__(self, workspace: SessionWorkspace) -> None:
        super().__init__()
        self._required_paths = tuple(
            relative_path
            for relative_path in _REQUIRED_SKILL_RELATIVE_PATHS
            if (workspace.root / relative_path).is_file()
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
                "been read in this conversation. Read BOTH of these first with read_file "
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
        """只採計嚴格早於「正在被判定的這個 write/edit tool call」所在 AI message 的 read_file。

        一則 AI message 的多個 tool call 是同一次推論一次吐出的(`SerializedToolCallsMiddleware`
        的併發序列化鎖就是為了這個現象存在):模型可能把 read_file(SKILL.md)、
        read_file(examples.md)、write_file(dashboard.html) 三個 call 塞進同一則訊息,此時
        write_file 的內容在任何 read_file 真的執行前就已經產生,不能算「讀過」。做法是找出
        state 訊息歷史裡「包含目前這個 tool_call id」的那則訊息,只掃它之前的訊息——比起「丟掉
        最後一則訊息」這種位置假設更準:同一則訊息也可能只有這一個 tool call,或 write/edit 不是
        該訊息裡最後一個 tool call,位置假設在這些情況下會誤判。"""
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
