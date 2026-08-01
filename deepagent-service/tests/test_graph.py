from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.agent.graph import build_agent
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
