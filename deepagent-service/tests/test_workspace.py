from pathlib import Path

import pytest

from app.engine.workspace import (
    prepare_local_layout,
    prepare_workspace,
    stage_skills,
    write_sources_doc,
)


def test_prepare_local_layout_creates_layout(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    assert workspace.root == tmp_path / "user-1" / "sessions" / "sess-1"
    assert workspace.queries_dir.is_dir()
    assert workspace.results_dir.is_dir()
    assert workspace.dashboard_path == workspace.root / "dashboard.html"


def test_prepare_local_layout_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        prepare_local_layout(tmp_path, "../evil", "sess-1")
    with pytest.raises(ValueError):
        prepare_local_layout(tmp_path, "user-1", "a/b")


def test_stage_skills_copies_builtin_and_user(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin-src" / "dashboard"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text("---\nname: dashboard\n---\n", encoding="utf-8")
    user_skills = tmp_path / "user-src"
    user_skills.mkdir()

    workspace = prepare_local_layout(tmp_path / "ws", "user-1", "sess-1")
    staged = stage_skills(workspace, builtin.parent, user_skills)

    assert staged == [".skills/builtin"]  # user 目錄空 → 不列入
    assert (workspace.skills_dir / "builtin" / "dashboard" / "SKILL.md").is_file()


def test_prepare_workspace_roots_at_env_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 每次呼叫現讀 AGENT_WORKSPACE_ROOT——凍結在 import 期會讓這裡的 setenv 失效。
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    workspace = prepare_workspace("user-1", "sess-1")

    assert workspace.root == tmp_path / "user-1" / "sessions" / "sess-1"


def test_write_sources_doc_lists_alias_without_path(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    write_sources_doc(workspace, [("orders", "csv")])
    content = workspace.sources_doc_path.read_text(encoding="utf-8")
    assert "orders" in content and "csv" in content
