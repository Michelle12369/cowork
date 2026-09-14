import shutil

from deepagents.backends.protocol import LsResult
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.graph import DashboardOverwriteBackend, build_agent, build_model
from app.agent.tools.data import build_data_tools  # noqa: F401  (型別對齊參考)
from app.engine.duck import Source, open_locked_connection
from app.engine.workspace import prepare_local_layout, stage_skills
from tests.fake_model import ScriptedChatModel


def _staged_skill_setup(tmp_path):
    connection = open_locked_connection([])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    builtin_dir = tmp_path / "skills" / "dashboard"
    builtin_dir.mkdir(parents=True)
    (builtin_dir / "SKILL.md").write_text(
        "---\nname: dashboard\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    staged = stage_skills(workspace, builtin_dir.parent, tmp_path / "no-user-skills")
    return connection, workspace, staged


async def test_build_agent_appends_extra_system_section_to_system_prompt(tmp_path) -> None:
    """connector 模式的靜態段(`prompts.CONNECTOR_MODE_SYSTEM_SECTION`)由呼叫端經
    `extra_system_section` 傳入,MUST 真的接在 SYSTEM_PROMPT 之後送給模型——用
    `ScriptedChatModel.received_message_batches` 撈出實際送進去的 SystemMessage 驗證
    (取代舊版把 connector 引導織進 user 訊息的做法,見 chat_turn.py)。middleware 只實作
    async wrap_model_call,MUST 用 ainvoke 驅動,同步 invoke 會炸 NotImplementedError。"""
    connection, workspace, staged = _staged_skill_setup(tmp_path)
    model = ScriptedChatModel([])
    agent = build_agent(
        model,
        connection,
        workspace,
        staged,
        extra_system_section="EXTRA SECTION MARKER",
    )

    await agent.ainvoke(
        {"messages": [HumanMessage("hi")]},
        config={"configurable": {"thread_id": "test-thread-with-extra-section"}},
    )

    assert model.received_message_batches
    system_messages = [
        message
        for message in model.received_message_batches[0]
        if isinstance(message, SystemMessage)
    ]
    assert system_messages
    assert "EXTRA SECTION MARKER" in system_messages[0].text


async def test_build_agent_without_extra_system_section_omits_it(tmp_path) -> None:
    connection, workspace, staged = _staged_skill_setup(tmp_path)
    model = ScriptedChatModel([])
    agent = build_agent(model, connection, workspace, staged)

    await agent.ainvoke(
        {"messages": [HumanMessage("hi")]},
        config={"configurable": {"thread_id": "test-thread-without-extra-section"}},
    )

    assert model.received_message_batches
    system_messages = [
        message
        for message in model.received_message_batches[0]
        if isinstance(message, SystemMessage)
    ]
    assert system_messages
    assert "EXTRA SECTION MARKER" not in system_messages[0].text


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
    agent = build_agent(model, connection, workspace, staged)
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
    agent = build_agent(model, connection, workspace, staged)

    main_tools = agent.nodes["tools"].bound.tools_by_name
    assert "task" not in main_tools


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


def _skill_markdown(name: str) -> str:
    return f"---\nname: {name}\ndescription: skill {name}\n---\nbody\n"


async def test_skills_rescan_reflects_directory_changes_across_turns(tmp_path) -> None:
    """同一個 thread_id 連續三輪, 每輪之間直接改 staged 來源目錄的 skill 清單——
    system prompt 裡的 skill 清單 MUST 反映當輪目錄內容, 不是第一輪的快照
    (deepagents 的 SkillsMiddleware 預設只在第一輪掃描, 見 RescanSkillsMiddleware)."""
    connection = open_locked_connection([])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    skills_source = tmp_path / "skills"
    (skills_source / "alpha").mkdir(parents=True)
    (skills_source / "alpha" / "SKILL.md").write_text(_skill_markdown("alpha"), encoding="utf-8")
    no_user_skills = tmp_path / "no-user-skills"

    model = ScriptedChatModel([])
    thread_config = {"configurable": {"thread_id": "rescan-thread"}}

    staged = stage_skills(workspace, skills_source, no_user_skills)
    agent = build_agent(model, connection, workspace, staged)
    await agent.ainvoke({"messages": [HumanMessage("hi")]}, config=thread_config)
    first_system_message = next(
        message
        for message in model.received_message_batches[0]
        if isinstance(message, SystemMessage)
    )
    assert "**alpha**" in first_system_message.text
    assert "**beta**" not in first_system_message.text

    (skills_source / "beta").mkdir(parents=True)
    (skills_source / "beta" / "SKILL.md").write_text(_skill_markdown("beta"), encoding="utf-8")
    staged = stage_skills(workspace, skills_source, no_user_skills)
    agent = build_agent(model, connection, workspace, staged)
    await agent.ainvoke({"messages": [HumanMessage("what else")]}, config=thread_config)
    second_system_message = next(
        message
        for message in model.received_message_batches[1]
        if isinstance(message, SystemMessage)
    )
    assert "**alpha**" in second_system_message.text
    assert "**beta**" in second_system_message.text

    shutil.rmtree(skills_source / "alpha")
    staged = stage_skills(workspace, skills_source, no_user_skills)
    agent = build_agent(model, connection, workspace, staged)
    await agent.ainvoke({"messages": [HumanMessage("and now")]}, config=thread_config)
    third_system_message = next(
        message
        for message in model.received_message_batches[2]
        if isinstance(message, SystemMessage)
    )
    assert "**beta**" in third_system_message.text
    assert "**alpha**" not in third_system_message.text


async def test_skills_rescan_clears_stale_load_errors_across_turns(tmp_path, monkeypatch) -> None:
    """第一輪有一個 skill 來源列不出來(source-level list error)時 system prompt 出現
    `<skill_load_warnings>`, 第二輪起恢復正常後 MUST 不再殘留上一輪的警告區塊(驗證
    RescanSkillsMiddleware 補 `skills_load_errors: []` 那段)."""
    connection = open_locked_connection([])
    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-2")
    skills_source = tmp_path / "skills"
    (skills_source / "alpha").mkdir(parents=True)
    (skills_source / "alpha" / "SKILL.md").write_text(_skill_markdown("alpha"), encoding="utf-8")
    no_user_skills = tmp_path / "no-user-skills"

    model = ScriptedChatModel([])
    thread_config = {"configurable": {"thread_id": "rescan-warning-thread"}}
    blocked_source_path = ".skills/blocked"
    original_ls = DashboardOverwriteBackend.ls

    def _patched_ls(self, path: str) -> LsResult:
        # 只攔截測試用的那個 source path, 其餘照常委派. 偽造 backend 回應而不用檔案權限,
        # 這樣以 root 跑測試也一樣成立.
        if path == blocked_source_path:
            return LsResult(error="synthetic source-level list error for test", entries=[])
        return original_ls(self, path)

    monkeypatch.setattr(DashboardOverwriteBackend, "ls", _patched_ls)

    staged = stage_skills(workspace, skills_source, no_user_skills)
    agent = build_agent(model, connection, workspace, [*staged, blocked_source_path])
    await agent.ainvoke({"messages": [HumanMessage("hi")]}, config=thread_config)
    first_system_message = next(
        message
        for message in model.received_message_batches[0]
        if isinstance(message, SystemMessage)
    )
    assert "<skill_load_warnings>" in first_system_message.text

    # 第二輪的 staged_skill_paths 不含 blocked_source_path, 所以就算 patch 還生效也不會觸發.
    staged = stage_skills(workspace, skills_source, no_user_skills)
    agent = build_agent(model, connection, workspace, staged)
    await agent.ainvoke({"messages": [HumanMessage("fixed now")]}, config=thread_config)
    second_system_message = next(
        message
        for message in model.received_message_batches[1]
        if isinstance(message, SystemMessage)
    )
    assert "<skill_load_warnings>" not in second_system_message.text


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
