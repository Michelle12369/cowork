from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.agent.graph import build_agent, build_model
from app.agent.middleware import RENDERER_SUBAGENT_NAME
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


def test_build_agent_registers_renderer_subagent_only(tmp_path, monkeypatch) -> None:
    """具名 renderer subagent 存在時 `task` 工具會出現——general-purpose subagent 開關
    (harness profile)與具名 subagent 是否掛上互不影響(見 graph.py 註解)。task 工具的
    description 只列出 dashboard-renderer,不含 general-purpose,證明 gp 確實仍被關掉。
    用真正的 build_model()——harness profile 照 provider key "openai" 比對,
    GenericFakeChatModel 對不上。"""
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
    assert "task" in main_tools
    task_description = main_tools["task"].description
    # 工具描述裡實際列出的 agent 清單格式是 "- <name>: <description>"（見 deepagents
    # _build_task_tool）;模板本身的固定範例文字裡本來就會提到 "general-purpose" 這個詞,
    # 只斷言清單行不存在才不誤判。
    assert f"- {RENDERER_SUBAGENT_NAME}:" in task_description
    assert "- general-purpose:" not in task_description


def test_build_model_provider_routing_knobs(monkeypatch) -> None:
    from app.agent.graph import build_model

    monkeypatch.setenv("AGENT_PROVIDER_SORT", "throughput")
    monkeypatch.setenv("AGENT_PROVIDER_IGNORE", "DeepInfra, SiliconFlow")
    monkeypatch.setenv("AGENT_PROVIDER_REQUIRE_PARAMETERS", "true")
    model = build_model()
    assert model.extra_body["provider"] == {
        "sort": "throughput",
        "ignore": ["DeepInfra", "SiliconFlow"],
        "require_parameters": True,
    }


def test_build_model_all_provider_knobs_off_sends_no_extra_body(monkeypatch) -> None:
    """三個路由旋鈕全關(require_parameters 顯式 false)時不送 extra_body——內部端點不吃未知欄位。"""
    from app.agent.graph import build_model

    monkeypatch.delenv("AGENT_PROVIDER_SORT", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER_IGNORE", raising=False)
    monkeypatch.setenv("AGENT_PROVIDER_REQUIRE_PARAMETERS", "false")
    monkeypatch.setenv("AGENT_REASONING_MAX_TOKENS", "0")
    model = build_model()
    assert model.extra_body is None


def test_build_model_require_parameters_defaults_on(monkeypatch) -> None:
    """require_parameters 預設 true(使用者裁決)——未設任何路由 env 也送 provider 區塊,
    避免被路由到不支援 tools 參數的 provider。"""
    from app.agent.graph import build_model

    monkeypatch.delenv("AGENT_PROVIDER_SORT", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER_IGNORE", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER_REQUIRE_PARAMETERS", raising=False)
    model = build_model()
    assert model.extra_body["provider"] == {"require_parameters": True}


def test_openai_harness_profile_does_not_exclude_tools() -> None:
    """edit_file 重新開放:模型可見完整工具 schema,大改動改用 write_file 由 prompt 量化規則引導
    (見 SYSTEM_PROMPT),不再物理剝除。deepagents 沒有公開的 profile getter,
    `_get_harness_profile` 是原始碼裡唯一的查表入口。"""
    from deepagents.profiles.harness.harness_profiles import _get_harness_profile

    import app.agent.graph  # noqa: F401  (module import triggers register_harness_profile)

    profile = _get_harness_profile("openai")
    assert profile is not None
    assert profile.general_purpose_subagent.enabled is False
    assert not profile.excluded_tools
