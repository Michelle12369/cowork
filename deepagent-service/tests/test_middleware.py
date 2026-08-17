"""app/agent/middleware.py 的中介層測試——併發序列化、skill gate、wiring manifest。"""

import asyncio

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from app.agent.middleware import SerializedToolCallsMiddleware
from app.engine.workspace import prepare_local_layout


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


# -- DashboardSkillGateMiddleware ------------------------------------------------------------


async def test_dashboard_write_is_blocked_before_skill_is_read(tmp_path) -> None:
    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import builtin_skills_dir, stage_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")

    middleware = DashboardSkillGateMiddleware(workspace)
    handler_called = False

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "id": "call-1",
            "args": {"file_path": "dashboard.html", "content": "<html></html>"},
        },
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert not handler_called
    assert "SKILL.md" in result.content


async def test_edit_file_dashboard_blocked_before_skill_read(tmp_path) -> None:
    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import builtin_skills_dir, stage_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")

    middleware = DashboardSkillGateMiddleware(workspace)
    handler_called = False

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="edited", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={
            "name": "edit_file",
            "id": "call-1",
            "args": {
                "file_path": "dashboard.html",
                "old_string": "<html></html>",
                "new_string": "<html><body></body></html>",
            },
        },
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert not handler_called
    assert "SKILL.md" in result.content


async def test_dashboard_write_is_allowed_after_all_skill_files_are_read(tmp_path) -> None:
    from langchain_core.messages import AIMessage

    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import builtin_skills_dir, stage_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")

    # 兩種路徑寫法都要算數:virtual_mode 把 `/a/b`(絕對)與 `a/b`(相對)正規化成同一份檔案。
    prior_reads = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "id": "r1",
                "args": {"file_path": "/.skills/builtin/dashboard/SKILL.md"},
            },
            {
                "name": "read_file",
                "id": "r2",
                "args": {"file_path": ".skills/builtin/dashboard/references/examples.md"},
            },
            {
                "name": "read_file",
                "id": "r3",
                "args": {"file_path": ".skills/builtin/dashboard/references/html-contract.md"},
            },
            {
                "name": "read_file",
                "id": "r4",
                "args": {"file_path": ".skills/builtin/dashboard/references/chart-rules.md"},
            },
        ],
    )
    middleware = DashboardSkillGateMiddleware(workspace)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "id": "call-1",
            "args": {"file_path": "dashboard.html", "content": "<html></html>"},
        },
        tool=None,
        state={"messages": [prior_reads]},
        runtime=None,
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert result.content == "written"


async def test_dashboard_write_is_blocked_when_skill_reads_are_batched_into_the_same_ai_message(
    tmp_path,
) -> None:
    """模型可能把 read_file(SKILL.md)、write_file(dashboard.html) 兩個 tool call 塞進同一則
    AI message(同一次推論一次吐出)——這種情況下 write_file 的內容是在 read_file 真的執行、
    拿到結果之前就已經產生的,即使 read_file 的路徑對得上,也 MUST 視為沒讀過 skill 而擋下。"""
    from langchain_core.messages import AIMessage

    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import builtin_skills_dir, stage_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")
    middleware = DashboardSkillGateMiddleware(workspace)
    handler_called = False

    same_turn_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "id": "r1",
                "args": {"file_path": ".skills/builtin/dashboard/SKILL.md"},
            },
            {
                "name": "write_file",
                "id": "w1",
                "args": {"file_path": "dashboard.html", "content": "<html>hardcoded</html>"},
            },
        ],
    )

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call=same_turn_message.tool_calls[1],
        tool=None,
        state={"messages": [same_turn_message]},
        runtime=None,
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert not handler_called
    assert "SKILL.md" in result.content


async def test_non_dashboard_writes_are_never_gated(tmp_path) -> None:
    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import builtin_skills_dir, stage_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")
    middleware = DashboardSkillGateMiddleware(workspace)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "id": "call-1",
            "args": {"file_path": "notes.md", "content": "note"},
        },
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    assert (await middleware.awrap_tool_call(request, handler)).content == "written"


async def test_dashboard_gate_dynamically_requires_newly_added_reference_files(tmp_path) -> None:
    """必讀清單是在 __init__ 動態掃描 skill 資料夾算出來的,不是寫死的檔名列表——資料夾裡
    多放一份新的 reference(未來新增的規則檔)時,沒讀過它也 MUST 擋下 dashboard.html 的
    寫入,且擋下訊息要列出這份新檔案的路徑。"""
    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import builtin_skills_dir, stage_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")

    extra_reference_path = workspace.root / ".skills/builtin/dashboard/extra.md"
    extra_reference_path.write_text("# extra rule\n", encoding="utf-8")

    middleware = DashboardSkillGateMiddleware(workspace)
    handler_called = False

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "id": "call-1",
            "args": {"file_path": "dashboard.html", "content": "<html></html>"},
        },
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert not handler_called
    assert ".skills/builtin/dashboard/extra.md" in result.content


async def test_dashboard_gate_fails_open_when_staged_skill_files_are_missing(tmp_path) -> None:
    """沒 stage skills 的部署(staged skill 檔不存在)MUST 直接放行,而不是永久卡死寫檔。"""
    from app.agent.middleware import DashboardSkillGateMiddleware

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    middleware = DashboardSkillGateMiddleware(workspace)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={
            "name": "write_file",
            "id": "call-1",
            "args": {"file_path": "dashboard.html", "content": "<html></html>"},
        },
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    assert (await middleware.awrap_tool_call(request, handler)).content == "written"


# -- DashboardWriteFileOnlyMiddleware --------------------------------------------------------


async def test_edit_file_on_dashboard_is_rejected() -> None:
    """dashboard.html 只能用 write_file——針對它的 edit_file 一律退件,訊息指向 write_file。"""
    from app.agent.middleware import DashboardWriteFileOnlyMiddleware

    middleware = DashboardWriteFileOnlyMiddleware()
    handler_called = False

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="edited", tool_call_id=request.tool_call["id"])

    request = _tool_call_request(
        "edit_file", file_path="dashboard.html", old_string="a", new_string="b"
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert not handler_called
    assert result.status == "error"
    assert "write_file" in result.content


async def test_edit_file_on_dashboard_absolute_path_is_rejected() -> None:
    """virtual_mode 的絕對寫法 `/dashboard.html` 也要視為同一份檔案而擋下。"""
    from app.agent.middleware import DashboardWriteFileOnlyMiddleware

    middleware = DashboardWriteFileOnlyMiddleware()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        raise AssertionError("handler must not run for a blocked edit_file")

    request = _tool_call_request(
        "edit_file", file_path="/dashboard.html", old_string="a", new_string="b"
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert result.status == "error"
    assert "write_file" in result.content


async def test_edit_file_on_other_files_is_allowed() -> None:
    """非 dashboard.html 的 edit_file(例如 notes.md)不受此中介層限制。"""
    from app.agent.middleware import DashboardWriteFileOnlyMiddleware

    middleware = DashboardWriteFileOnlyMiddleware()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="edited", tool_call_id=request.tool_call["id"])

    request = _tool_call_request(
        "edit_file", file_path="notes.md", old_string="a", new_string="b"
    )
    assert (await middleware.awrap_tool_call(request, handler)).content == "edited"


async def test_write_file_on_dashboard_passes_through() -> None:
    """此中介層只擋 edit_file;write_file 針對 dashboard.html 照常放行。"""
    from app.agent.middleware import DashboardWriteFileOnlyMiddleware

    middleware = DashboardWriteFileOnlyMiddleware()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = _tool_call_request(
        "write_file", file_path="dashboard.html", content="<html></html>"
    )
    assert (await middleware.awrap_tool_call(request, handler)).content == "written"
