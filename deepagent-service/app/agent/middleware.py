"""主 agent 的 AgentMiddleware——deepagents 只把自訂 middleware 掛到主 agent，
子代理的 middleware 由各自的 subagent spec 帶，故此處的鎖不會與 `task` 工具互鎖。"""

import asyncio
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.engine.results import format_wiring_manifest, load_all_results
from app.engine.results_guard import validate_results_contract
from app.engine.workspace import SessionWorkspace

ToolCallHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]
ModelCallHandler = Callable[[ModelRequest], Awaitable[AIMessage]]


class SerializedToolCallsMiddleware(AgentMiddleware):
    """同一則 AI message 的多個 tool call 一次只跑一個。ToolNode 預設用 `asyncio.gather`
    併發送出 tool call，而 deepagents 的 write_file/edit_file 是無鎖讀改寫——併發打同一
    檔案會靜默互相覆蓋。鎖的範圍是一次 `/chat`（per-request build_agent），不跨 request。
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
    """每次 model call 都把目前 qN 清單、intent、欄位附在 system message 後面。每次呼叫
    重建而非每輪一次:同一輪內常見「先查詢後寫 dashboard」，turn 開始時 results 還不存在，
    turn-start 注入對此情境無效。
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


# 整個 dashboard skill 資料夾底下所有 .md 都要讀過才算「讀過 skill」——SKILL.md 講規則、
# references/ 底下各檔給可運作的寫法、CDN 白名單等逐字契約。清單在 __init__ 動態掃描
# (rglob),不寫死檔名:只讀部分容易漏掉某份 reference 的細節,guard 會退件重寫;新增
# reference 檔也會自動納入必讀,不用回頭維護這裡的清單。
_DASHBOARD_SKILL_RELATIVE_ROOT = ".skills/builtin/dashboard"
_GATED_TOOL_NAMES = frozenset({"write_file", "edit_file"})
_GATED_FILE_NAME = "dashboard.html"


def _normalized_workspace_path(file_path: str) -> str:
    """把 virtual_mode 的絕對寫法 `/a/b` 與相對寫法 `a/b` 收斂成同一種字串,好做比對。"""
    return file_path.strip().lstrip("/")


class DashboardWriteFileOnlyMiddleware(AgentMiddleware):
    """dashboard.html 只能用 write_file 整檔寫入——擋掉針對它的 edit_file。弱模型的 edit_file
    常抓不到 old_string 或做出破碎的局部修改;強制整檔重寫行為更可預測,guard 每輪檢查的也是
    一份完整自洽的 HTML。其他檔案(notes.md 等)的 edit_file 不受限。"""

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        tool_call = request.tool_call
        if tool_call.get("name") == "edit_file":
            file_path = _normalized_workspace_path(
                str(tool_call.get("args", {}).get("file_path", ""))
            )
            if file_path == _GATED_FILE_NAME:
                return ToolMessage(
                    content=(
                        "Blocked: dashboard.html can only be written with write_file (a full "
                        "rewrite), never edit_file. Read the current dashboard.html first "
                        "(read_file with limit=1000), then write the complete updated file with "
                        "a single write_file call."
                    ),
                    tool_call_id=tool_call["id"],
                    status="error",
                )
        return await handler(request)


class DashboardSkillGateMiddleware(AgentMiddleware):
    """thread 內沒讀過整個 dashboard skill 資料夾(`.skills/builtin/dashboard` 底下所有
    `.md`)之前,擋掉對 dashboard.html 的 write_file/edit_file。掃的是 thread 訊息歷史
    (`request.state`),不是 middleware 實例狀態——per-request 建立的實例記不住上一輪的
    read。只在寫檔時擋,不每輪注入(references 內容量不小,會加劇已知的 reasoning
    runaway);skill 資料夾不存在或底下沒有任何 `.md` 時 fail-open。
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
        """只採計嚴格早於「這個 tool call 所在 AI message」之前的 read_file——同一則訊息
        可能一次吐出 read_file+write_file 等多個 tool call,此時 write 內容早在 read 真正
        執行前就已產生,不算「讀過」。做法是找出含目前 tool_call id 的訊息,只掃它之前的
        訊息,而非用「丟掉最後一則」這種位置假設(可能誤判)。"""
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




class DashboardResultsContractMiddleware(AgentMiddleware):
    """dashboard.html 對 `window.__ERD_RESULTS__` 的存取契約護欄——套用
    `app.engine.results_guard.validate_results_contract` 的規則,擋下非字面存取(該類 id
    永遠不會被注入)與字面數字資料陣列。

    write_file 帶著完整 `content` 參數，違規在執行前就能擋下（handler 根本不呼叫）。edit_file
    只有 `old_string`/`new_string` 片段，無法在執行前組出完整檔案內容，故放行執行、讀回結果
    驗證；違規時把 dashboard.html 還原成編輯前讀到的內容（原本不存在就整個刪掉，不留半吊子的
    壞版本），兩種情況都用同一份錯誤訊息當 tool result 回饋給模型重試。
    """

    def __init__(self, workspace: SessionWorkspace) -> None:
        super().__init__()
        self._workspace = workspace

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        tool_call = request.tool_call
        if not self._is_gated_dashboard_write(tool_call):
            return await handler(request)
        if tool_call.get("name") == "write_file":
            return await self._guard_write_file(request, handler)
        return await self._guard_edit_file(request, handler)

    def _is_gated_dashboard_write(self, tool_call: dict) -> bool:
        if tool_call.get("name") not in _GATED_TOOL_NAMES:
            return False
        file_path = tool_call.get("args", {}).get("file_path", "")
        return _normalized_workspace_path(str(file_path)) == _GATED_FILE_NAME

    async def _guard_write_file(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        content = str(request.tool_call.get("args", {}).get("content", ""))
        results = load_all_results(self._workspace)
        errors = validate_results_contract(content, set(results))
        if errors:
            return self._error_result(request, errors)
        return await handler(request)

    async def _guard_edit_file(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        dashboard_path = self._workspace.dashboard_path
        pre_edit_content = (
            dashboard_path.read_text(encoding="utf-8") if dashboard_path.is_file() else None
        )
        result = await handler(request)
        if not dashboard_path.is_file():
            return result
        post_edit_content = dashboard_path.read_text(encoding="utf-8")
        results = load_all_results(self._workspace)
        errors = validate_results_contract(post_edit_content, set(results))
        if not errors:
            return result
        if pre_edit_content is None:
            dashboard_path.unlink()
        else:
            dashboard_path.write_text(pre_edit_content, encoding="utf-8")
        return self._error_result(request, errors)

    def _error_result(self, request: ToolCallRequest, errors: list[str]) -> ToolMessage:
        return ToolMessage(
            content="\n".join(errors),
            tool_call_id=request.tool_call["id"],
            status="error",
        )
