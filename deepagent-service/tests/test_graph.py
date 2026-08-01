from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.agent.graph import build_agent, build_model
from app.agent.tools.data import build_data_tools  # noqa: F401  (型別對齊參考)
from app.agent.tools.recording import ToolResultRecorder
from app.engine.duck import Source, open_locked_connection
from app.engine.workspace import LocalWorkspaceStore, stage_skills


def test_build_agent_compiles_with_staged_skills(tmp_path) -> None:
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\n", encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(csv_path), "csv")])
    workspace = LocalWorkspaceStore(tmp_path / "ws").prepare("user-1", "sess-1")

    builtin_dir = tmp_path / "skills" / "dashboard"
    builtin_dir.mkdir(parents=True)
    (builtin_dir / "SKILL.md").write_text(
        "---\nname: dashboard\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    staged = stage_skills(workspace, builtin_dir.parent, tmp_path / "no-user-skills")

    model = GenericFakeChatModel(messages=iter([]))
    agent = build_agent(model, connection, workspace, staged, ToolResultRecorder())
    assert agent is not None
    assert (workspace.skills_dir / "builtin" / "dashboard" / "SKILL.md").is_file()


def test_build_agent_has_no_task_tool(tmp_path, monkeypatch) -> None:
    """一個真實案例:general-purpose subagent 收到「用 Python 算迴歸」的委派後,呼叫
    write_file 寫了一支 .py 腳本,以為寫完會被執行(不會,這裡沒有任何執行機制),撞了
    兩次才自己想起來改用 SQL,白白繞了好幾分鐘。委派本身要切開 context window 的價值,
    配上目前模型常在委派任務描述裡寫出環境做不到的指示,淨值是負的——`app.agent.graph`
    在 module load 時註冊 harness profile 整個關掉 general-purpose subagent,不留
    `task` 工具。這個測試用真正的 build_model()(harness profile 是照 provider key
    "openai" 比對,GenericFakeChatModel 對不上)釘住這個關閉,避免日後不小心恢復。"""
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("system,tickets\nCRM,42\n", encoding="utf-8")
    connection = open_locked_connection([Source("orders", str(csv_path), "csv")])
    workspace = LocalWorkspaceStore(tmp_path / "ws").prepare("user-1", "sess-1")

    builtin_dir = tmp_path / "skills" / "dashboard"
    builtin_dir.mkdir(parents=True)
    (builtin_dir / "SKILL.md").write_text(
        "---\nname: dashboard\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    staged = stage_skills(workspace, builtin_dir.parent, tmp_path / "no-user-skills")

    model = build_model()
    agent = build_agent(model, connection, workspace, staged, ToolResultRecorder())

    main_tools = agent.nodes["tools"].bound.tools_by_name
    assert "task" not in main_tools


def test_build_model_provider_routing_knobs(monkeypatch) -> None:
    from app.agent.graph import build_model

    monkeypatch.setenv("AGENT_PROVIDER_SORT", "throughput")
    monkeypatch.setenv("AGENT_PROVIDER_IGNORE", "DeepInfra, SiliconFlow")
    model = build_model()
    assert model.extra_body["provider"] == {
        "sort": "throughput",
        "ignore": ["DeepInfra", "SiliconFlow"],
    }


def test_build_model_no_provider_routing_by_default(monkeypatch) -> None:
    from app.agent.graph import build_model

    monkeypatch.delenv("AGENT_PROVIDER_SORT", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER_IGNORE", raising=False)
    monkeypatch.setenv("AGENT_REASONING_MAX_TOKENS", "0")
    model = build_model()
    assert model.extra_body is None
