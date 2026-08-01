"""主 agent 的 AgentMiddleware——deepagents 只把自訂 middleware 掛到主 agent，
子代理的 middleware 由各自的 subagent spec 帶，故此處的鎖不會與 `task` 工具互鎖。"""

import asyncio
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

ToolCallHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


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
