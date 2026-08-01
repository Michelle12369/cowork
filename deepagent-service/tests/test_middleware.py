"""app/agent/middleware.py 的中介層測試——併發序列化、skill gate、wiring manifest。"""

import asyncio

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from app.agent.middleware import SerializedToolCallsMiddleware


def _tool_call_request(tool_name: str, **arguments: object) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool_name, "id": f"call-{tool_name}", "args": dict(arguments)},
        tool=None,
        state={"messages": []},
        runtime=None,
    )


async def test_awrap_tool_call_never_runs_two_handlers_at_once() -> None:
    """兩個並發的 tool call 進到同一個 middleware 實例時，handler 的執行區間 MUST 不重疊。
    沒有鎖的話兩者會同時在 handler 裡（這正是 FilesystemBackend.edit 讀改寫互相覆蓋的窗口）。"""
    middleware = SerializedToolCallsMiddleware()
    concurrent_handler_count = 0
    max_observed_concurrency = 0

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal concurrent_handler_count, max_observed_concurrency
        concurrent_handler_count += 1
        max_observed_concurrency = max(max_observed_concurrency, concurrent_handler_count)
        await asyncio.sleep(0.02)
        concurrent_handler_count -= 1
        return ToolMessage(content="done", tool_call_id=request.tool_call["id"])

    await asyncio.gather(
        middleware.awrap_tool_call(_tool_call_request("edit_file"), handler),
        middleware.awrap_tool_call(_tool_call_request("write_file"), handler),
    )

    assert max_observed_concurrency == 1


async def test_awrap_tool_call_returns_handler_result_unchanged() -> None:
    middleware = SerializedToolCallsMiddleware()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="payload", tool_call_id=request.tool_call["id"])

    result = await middleware.awrap_tool_call(_tool_call_request("run_sql"), handler)

    assert result.content == "payload"


async def test_wiring_manifest_middleware_appends_current_results(tmp_path) -> None:
    """manifest MUST 反映「呼叫當下」workspace 上的 results——同一輪內新跑的查詢也要進去。"""
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import SystemMessage

    from app.agent.middleware import WiringManifestMiddleware
    from app.engine.results import record_query
    from app.engine.workspace import LocalWorkspaceStore

    workspace = LocalWorkspaceStore(tmp_path).prepare("user-1", "sess-1")
    record_query(workspace, "q1", "SELECT 1", "各功能使用次數", ["feature_name"], [["匯出"]], False)

    middleware = WiringManifestMiddleware(workspace)
    captured_system_messages: list[SystemMessage | None] = []

    async def handler(request: ModelRequest) -> str:
        captured_system_messages.append(request.system_message)
        return "ok"

    request = ModelRequest(model=None, messages=[], system_message=SystemMessage("BASE"))
    await middleware.awrap_model_call(request, handler)

    assert "q1" in captured_system_messages[0].content
    assert "各功能使用次數" in captured_system_messages[0].content
    assert "BASE" in captured_system_messages[0].content


async def test_wiring_manifest_middleware_passes_through_when_no_results(tmp_path) -> None:
    """沒有任何 query result 時,manifest 是空字串——MUST 直接放行,不附加空白 header。"""
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import SystemMessage

    from app.agent.middleware import WiringManifestMiddleware
    from app.engine.workspace import LocalWorkspaceStore

    workspace = LocalWorkspaceStore(tmp_path).prepare("user-1", "sess-1")
    middleware = WiringManifestMiddleware(workspace)

    original_request = ModelRequest(model=None, messages=[], system_message=SystemMessage("BASE"))
    captured_requests: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> str:
        captured_requests.append(request)
        return "ok"

    await middleware.awrap_model_call(original_request, handler)

    assert captured_requests[0] is original_request
