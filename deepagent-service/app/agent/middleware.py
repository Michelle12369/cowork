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
