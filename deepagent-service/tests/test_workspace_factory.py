from app.engine.workspace import LocalWorkspaceStore
from app.engine.workspace_factory import build_workspace_store


def test_build_workspace_store_always_returns_local(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    store = build_workspace_store()

    assert isinstance(store, LocalWorkspaceStore)
