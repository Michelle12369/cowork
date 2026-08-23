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
    agent = build_agent(
        model, connection, workspace, staged, ToolResultRecorder(), selected_groups=[]
    )
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
    agent = build_agent(
        model, connection, workspace, staged, ToolResultRecorder(), selected_groups=[]
    )

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


def test_build_agent_noConnectorsFile_promptAndToolsUnchanged(tmp_path, monkeypatch) -> None:
    """不變式(Task 6 brief):AGENT_CONNECTORS_FILE 未設定 -> registry 為空 -> graph 接線
    不掛 fetch_api_data、SYSTEM_PROMPT 不被附加任何 connector 段。"""
    monkeypatch.delenv("AGENT_CONNECTORS_FILE", raising=False)
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
    agent = build_agent(
        model, connection, workspace, staged, ToolResultRecorder(), selected_groups=[]
    )

    main_tools = agent.nodes["tools"].bound.tools_by_name
    assert "fetch_api_data" not in main_tools


_GROUPED_CONNECTORS_YAML = """\
connector_groups:
  - name: mes
    display: "MES 製造執行系統"
    members:
      - name: mes_yield
        kind: data
        description: 產線良率
        endpoint: http://connector.internal/yield
        method: GET
        params: {}
  - name: erp
    display: "ERP 企業資源規劃"
    members:
      - name: erp_orders
        kind: data
        description: 訂單清單
        endpoint: http://connector.internal/orders
        method: GET
        params: {}
"""


def _write_grouped_connectors_config(tmp_path):
    config_path = tmp_path / "connectors.yaml"
    config_path.write_text(_GROUPED_CONNECTORS_YAML, encoding="utf-8")
    return config_path


def _build_agent_with_grouped_connectors(tmp_path, selected_groups):
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
    return build_agent(
        model, connection, workspace, staged, ToolResultRecorder(), selected_groups=selected_groups
    )


def test_build_agent_selectedGroups_filtersToChosenGroupOnly(tmp_path, monkeypatch) -> None:
    """selectedGroups=["mes"] -> filter_by_groups 之後 erp 整組從 fetch_api_data 可用清單
    消失(§11 Task 2)。用「未知 connector 名」的錯誤訊息反查可用清單,不需要真的打 API。"""
    monkeypatch.setenv("AGENT_CONNECTORS_FILE", str(_write_grouped_connectors_config(tmp_path)))
    agent = _build_agent_with_grouped_connectors(tmp_path, selected_groups=["mes"])

    main_tools = agent.nodes["tools"].bound.tools_by_name
    result = main_tools["fetch_api_data"].invoke(
        {"connector": "erp_orders", "params": {}, "alias": "x"}
    )
    available = result.split("可用: ", 1)[1]
    assert "erp_orders" not in available
    assert "mes_yield" in available


def test_build_agent_selectedGroups_empty_includesAllGroups(tmp_path, monkeypatch) -> None:
    """selectedGroups=[] 不變式:全部 group 皆可見,行為與未接選組功能前相同。"""
    monkeypatch.setenv("AGENT_CONNECTORS_FILE", str(_write_grouped_connectors_config(tmp_path)))
    agent = _build_agent_with_grouped_connectors(tmp_path, selected_groups=[])

    main_tools = agent.nodes["tools"].bound.tools_by_name
    result = main_tools["fetch_api_data"].invoke(
        {"connector": "no_such_connector", "params": {}, "alias": "x"}
    )
    available = result.split("可用: ", 1)[1]
    assert "mes_yield" in available
    assert "erp_orders" in available


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
