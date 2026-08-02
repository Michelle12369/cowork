from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.agent.graph import build_agent, build_model
from app.agent.tools.data import build_data_tools  # noqa: F401  (型別對齊參考)
from app.agent.tools.recording import ToolResultRecorder
from app.engine.duck import Source, open_locked_connection
from app.engine.workspace import prepare_local_layout, stage_skills


def test_build_agent_compiles_with_staged_skills(tmp_path) -> None:
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

    model = GenericFakeChatModel(messages=iter([]))
    agent = build_agent(model, connection, workspace, staged, ToolResultRecorder())
    assert agent is not None
    assert (workspace.skills_dir / "builtin" / "dashboard" / "SKILL.md").is_file()


def test_build_agent_has_no_task_tool(tmp_path, monkeypatch) -> None:
    """`app.agent.graph` 註冊 harness profile 整個關掉 general-purpose subagent,不留
    `task` 工具(見該檔案註解)。用真正的 build_model()——harness profile 照 provider key
    "openai" 比對,GenericFakeChatModel 對不上。"""
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
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


def test_openai_harness_profile_excludes_edit_file() -> None:
    """single-write 補強:模型必須完全看不到 edit_file 工具schema,不只是靠退貨訊息教育。
    deepagents 沒有公開的 profile getter,`_get_harness_profile` 是原始碼裡唯一的查表入口。"""
    from deepagents.profiles.harness.harness_profiles import _get_harness_profile

    import app.agent.graph  # noqa: F401  (module import triggers register_harness_profile)

    profile = _get_harness_profile("openai")
    assert profile is not None
    assert profile.excluded_tools == frozenset({"edit_file"})
