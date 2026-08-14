"""app/agent/middleware.py 的中介層測試——併發序列化、wiring manifest、委派 gate、renderer 收割。"""

import asyncio

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.agent.middleware import (
    HARVEST_CONFIRMATION_PREFIX,
    DashboardDelegationGateMiddleware,
    DashboardRenderHarvestMiddleware,
    SerializedToolCallsMiddleware,
)
from app.agent.renderer_subagent import RENDERER_SUBAGENT_NAME
from app.engine.workspace import prepare_local_layout

FULL_HTML = "<!DOCTYPE html>\n<html><body><h1>Revenue</h1></body></html>"


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

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
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

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    middleware = WiringManifestMiddleware(workspace)

    original_request = ModelRequest(model=None, messages=[], system_message=SystemMessage("BASE"))
    captured_requests: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> str:
        captured_requests.append(request)
        return "ok"

    await middleware.awrap_model_call(original_request, handler)

    assert captured_requests[0] is original_request


# -- DashboardDelegationGateMiddleware / DashboardRenderHarvestMiddleware --------------------


def _tool_request(
    tool_name: str, args: dict | None = None, tool_call_id: str = "call_1"
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool_name, "id": tool_call_id, "args": args or {}},
        tool=None,
        state={"messages": []},
        runtime=None,
    )


def _make_workspace(tmp_path):
    return prepare_local_layout(tmp_path, "user-1", "sess-1")


async def _passthrough_handler(request: ToolCallRequest) -> ToolMessage:
    raise AssertionError("handler should not be reached when blocked")


async def test_delegation_gate_blocksWriteFile_onDashboard(tmp_path) -> None:
    middleware = DashboardDelegationGateMiddleware()
    request = _tool_request("write_file", {"file_path": "dashboard.html", "content": "<p>x</p>"})
    result = await middleware.awrap_tool_call(request, _passthrough_handler)
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert RENDERER_SUBAGENT_NAME in str(result.content)


async def test_delegation_gate_blocksEditFile_onDashboard(tmp_path) -> None:
    middleware = DashboardDelegationGateMiddleware()
    request = _tool_request("edit_file", {"file_path": "/dashboard.html"})
    result = await middleware.awrap_tool_call(request, _passthrough_handler)
    assert isinstance(result, ToolMessage) and result.status == "error"


async def test_delegation_gate_allowsNotesWrites(tmp_path) -> None:
    middleware = DashboardDelegationGateMiddleware()
    request = _tool_request("write_file", {"file_path": "notes.md", "content": "findings"})
    sentinel = ToolMessage(content="ok", tool_call_id="call_1")

    async def handler(_request):
        return sentinel

    assert await middleware.awrap_tool_call(request, handler) is sentinel


async def test_harvest_writesHtml_andReplacesToolMessage(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    middleware = DashboardRenderHarvestMiddleware(workspace)
    request = _tool_request(
        "task", {"description": "build revenue dashboard", "subagent_type": RENDERER_SUBAGENT_NAME}
    )

    async def handler(_request):
        return Command(update={"messages": [ToolMessage(FULL_HTML, tool_call_id="call_1")]})

    result = await middleware.awrap_tool_call(request, handler)
    assert workspace.dashboard_path.read_text(encoding="utf-8") == FULL_HTML
    harvested = result.update["messages"][0]
    assert str(harvested.content).startswith(HARVEST_CONFIRMATION_PREFIX)
    assert "<html" not in str(harvested.content)


async def test_harvest_stripsMarkdownFences(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    middleware = DashboardRenderHarvestMiddleware(workspace)
    request = _tool_request("task", {"subagent_type": RENDERER_SUBAGENT_NAME})
    fenced = f"```html\n{FULL_HTML}\n```"

    async def handler(_request):
        return Command(update={"messages": [ToolMessage(fenced, tool_call_id="call_1")]})

    await middleware.awrap_tool_call(request, handler)
    assert workspace.dashboard_path.read_text(encoding="utf-8") == FULL_HTML


async def test_harvest_rejectsNonHtml_withErrorToolMessage(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    middleware = DashboardRenderHarvestMiddleware(workspace)
    request = _tool_request("task", {"subagent_type": RENDERER_SUBAGENT_NAME})

    async def handler(_request):
        return Command(
            update={"messages": [ToolMessage("抱歉我需要更多資訊", tool_call_id="call_1")]}
        )

    result = await middleware.awrap_tool_call(request, handler)
    assert not workspace.dashboard_path.exists()
    error_message = result.update["messages"][0]
    assert error_message.status == "error"


async def test_harvest_ignoresOtherSubagents(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    middleware = DashboardRenderHarvestMiddleware(workspace)
    request = _tool_request("task", {"subagent_type": "general-purpose"})
    passthrough = Command(update={"messages": [ToolMessage("done", tool_call_id="call_1")]})

    async def handler(_request):
        return passthrough

    assert await middleware.awrap_tool_call(request, handler) is passthrough
