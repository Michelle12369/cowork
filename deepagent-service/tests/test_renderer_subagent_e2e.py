"""renderer subagent 端到端:主 agent 委派 task → renderer(共用 scripted model)回整份 HTML →
harvest 寫檔＋短確認 → 主 agent 收尾中文回覆。斷言三件事:檔案內容、確認訊息不含 HTML、
最終 answer 是中文短句(HTML 不污染)。"""

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from app.agent.graph import build_agent
from app.agent.middleware import HARVEST_CONFIRMATION_PREFIX, RENDERER_SUBAGENT_NAME
from app.agent.tools.recording import ToolResultRecorder
from app.engine.duck import Source, open_locked_connection
from app.engine.workspace import SessionWorkspace, prepare_local_layout, stage_skills
from tests.fake_model import ScriptedChatModel

FULL_HTML = "<!DOCTYPE html>\n<html><body><div id='chart'></div></body></html>"


def _build_e2e_agent(
    tmp_path: Path, scripted_model: ScriptedChatModel
) -> tuple[SessionWorkspace, CompiledStateGraph]:
    """沿用 test_graph.py 的 e2e 構造(workspace staging + duckdb connection + build_agent),
    抽成本檔共用 helper——workspace 用 `dashboard_path` 斷言寫檔內容。"""

    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\n", encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(csv_path), "csv")])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")

    builtin_dir = tmp_path / "skills" / "dashboard"
    builtin_dir.mkdir(parents=True)
    (builtin_dir / "SKILL.md").write_text(
        "---\nname: dashboard\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    staged = stage_skills(workspace, builtin_dir.parent, tmp_path / "no-user-skills")

    agent = build_agent(scripted_model, connection, workspace, staged, ToolResultRecorder())
    return workspace, agent


async def test_dashboard_generation_flows_through_renderer_subagent(tmp_path) -> None:
    scripted_model = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "建立營收儀表板",
                            "subagent_type": RENDERER_SUBAGENT_NAME,
                        },
                        "id": "call_task_1",
                    }
                ],
            ),
            AIMessage(content=FULL_HTML),  # renderer 的單發生成(繼承同一顆 scripted model)
            AIMessage(content="儀表板已完成,請查看右側預覽。"),
        ]
    )
    workspace, agent = _build_e2e_agent(tmp_path, scripted_model)
    result = await agent.ainvoke(
        {"messages": [HumanMessage("幫我做營收儀表板")]},
        config={"configurable": {"thread_id": "e2e-renderer-1"}, "recursion_limit": 30},
    )

    assert workspace.dashboard_path.read_text(encoding="utf-8") == FULL_HTML

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    harvest_confirmations = [
        m for m in tool_messages if str(m.content).startswith(HARVEST_CONFIRMATION_PREFIX)
    ]
    assert len(harvest_confirmations) == 1
    assert "<html" not in str(harvest_confirmations[0].content)

    final_message = result["messages"][-1]
    assert isinstance(final_message, AIMessage)
    assert "儀表板已完成" in str(final_message.content)
    assert "<html" not in str(final_message.content)


async def test_dashboard_modification_flows_through_edit_file_delivery_channel(tmp_path) -> None:
    """實測小改動路徑:既有 dashboard.html 已在 workspace,renderer 用 edit_file 做精準替換
    而非整份重生成——收割 middleware 套用替換+導向收尾一句話,主 agent 仍拿到單一
    confirmation 與乾淨 ANSWER。"""
    scripted_model = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "把圖表標題改成 tickets",
                            "subagent_type": RENDERER_SUBAGENT_NAME,
                        },
                        "id": "call_task_1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "edit_file",
                        "args": {
                            "file_path": "dashboard.html",
                            "old_string": "chart",
                            "new_string": "tickets-chart",
                        },
                        "id": "call_edit_1",
                    }
                ],
            ),
            AIMessage(content="done"),  # renderer 收到 edit applied 訊息後回一句話收尾
            AIMessage(content="標題已更新,請查看右側預覽。"),
        ]
    )
    workspace, agent = _build_e2e_agent(tmp_path, scripted_model)
    workspace.dashboard_path.write_text(FULL_HTML, encoding="utf-8")
    result = await agent.ainvoke(
        {"messages": [HumanMessage("把圖表標題改成 tickets")]},
        config={"configurable": {"thread_id": "e2e-renderer-3"}, "recursion_limit": 30},
    )

    assert "tickets-chart" in workspace.dashboard_path.read_text(encoding="utf-8")

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    harvest_confirmations = [
        m for m in tool_messages if str(m.content).startswith(HARVEST_CONFIRMATION_PREFIX)
    ]
    assert len(harvest_confirmations) == 1
    assert "<html" not in str(harvest_confirmations[0].content)

    final_message = result["messages"][-1]
    assert isinstance(final_message, AIMessage)
    assert "標題已更新" in str(final_message.content)
    assert "<html" not in str(final_message.content)


async def test_dashboard_generation_flows_through_write_file_delivery_channel(tmp_path) -> None:
    """實測根因 B 的收尾路徑:renderer 習慣性 write_file(dashboard.html) 而非整份回覆——
    收割 middleware 短路寫檔+導向收尾一句話,主 agent 仍拿到單一 confirmation 與乾淨 ANSWER。"""
    scripted_model = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "建立營收儀表板",
                            "subagent_type": RENDERER_SUBAGENT_NAME,
                        },
                        "id": "call_task_1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "dashboard.html", "content": FULL_HTML},
                        "id": "call_write_1",
                    }
                ],
            ),
            AIMessage(content="done"),  # renderer 收到 saved 訊息後回一句話收尾
            AIMessage(content="儀表板已完成,請查看右側預覽。"),
        ]
    )
    workspace, agent = _build_e2e_agent(tmp_path, scripted_model)
    result = await agent.ainvoke(
        {"messages": [HumanMessage("幫我做營收儀表板")]},
        config={"configurable": {"thread_id": "e2e-renderer-2"}, "recursion_limit": 30},
    )

    assert workspace.dashboard_path.read_text(encoding="utf-8") == FULL_HTML

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    harvest_confirmations = [
        m for m in tool_messages if str(m.content).startswith(HARVEST_CONFIRMATION_PREFIX)
    ]
    assert len(harvest_confirmations) == 1
    assert "<html" not in str(harvest_confirmations[0].content)

    final_message = result["messages"][-1]
    assert isinstance(final_message, AIMessage)
    assert "儀表板已完成" in str(final_message.content)
    assert "<html" not in str(final_message.content)
