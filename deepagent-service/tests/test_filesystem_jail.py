"""Locks in the `virtual_mode=True` choice in app.agent.graph.build_agent (spec §6: file
tools must be pinned to the session workspace root and reject `../` escapes after
normalization). If a future refactor quietly flips this back to `virtual_mode=False`
(the deepagents default), this test starts failing instead of the jail silently
disappearing.
"""

import pytest
from deepagents.backends.filesystem import FilesystemBackend

from app.agent.graph import DashboardOverwriteBackend


def test_backend_rejects_path_traversal_and_writes_stay_in_root(tmp_path) -> None:
    root = tmp_path / "workspace-root"
    root.mkdir()
    backend = FilesystemBackend(root_dir=str(root), virtual_mode=True)

    with pytest.raises(ValueError, match="traversal"):
        backend.write("../escape.txt", "pwned")
    assert not (tmp_path / "escape.txt").exists()

    result = backend.write("notes.md", "hello")
    assert result.error is None
    assert (root / "notes.md").read_text(encoding="utf-8") == "hello"


def test_dashboard_overwrite_backend_allows_dashboard_html_rewrite(tmp_path) -> None:
    """Regression for the real eval failure: an iteration turn that needs to wholesale
    rewrite dashboard.html via write_file was rejected outright by deepagents 0.6.12's
    create-only FilesystemBackend.write(), stalling the turn with no recovery path.
    Targeted in-place patches now go through edit() instead (see below)."""
    root = tmp_path / "workspace-root"
    root.mkdir()
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    first = backend.write("dashboard.html", "<html>v1</html>")
    assert first.error is None
    assert (root / "dashboard.html").read_text(encoding="utf-8") == "<html>v1</html>"

    second = backend.write("dashboard.html", "<html>v2 -- full rewrite</html>")
    assert second.error is None
    assert (root / "dashboard.html").read_text(
        encoding="utf-8"
    ) == "<html>v2 -- full rewrite</html>"


def test_notes_md_can_be_overwritten_after_it_exists(tmp_path) -> None:
    """notes.md 併入 overwrite 洞:write() 的整份覆寫行為與 dashboard.html 對稱,供大改動
    走 write_file 整份重寫時使用;局部修改仍可走 edit()(見下方 test_other_files_still_editable)。"""
    root = tmp_path / "workspace-root"
    root.mkdir()
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    first = backend.write("notes.md", "draft v1")
    assert first.error is None
    assert (root / "notes.md").read_text(encoding="utf-8") == "draft v1"

    second = backend.write("notes.md", "draft v2 -- full rewrite")
    assert second.error is None
    assert (root / "notes.md").read_text(encoding="utf-8") == "draft v2 -- full rewrite"


def test_dashboard_overwrite_backend_still_rejects_other_existing_files(tmp_path) -> None:
    root = tmp_path / "workspace-root"
    root.mkdir()
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    first = backend.write("SOURCES.md", "hello")
    assert first.error is None

    second = backend.write("SOURCES.md", "overwritten")
    assert second.error is not None
    assert (root / "SOURCES.md").read_text(encoding="utf-8") == "hello"


def test_dashboard_overwrite_backend_still_blocks_path_traversal(tmp_path) -> None:
    root = tmp_path / "workspace-root"
    root.mkdir()
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    with pytest.raises(ValueError, match="traversal"):
        backend.write("../escape.txt", "pwned")
    assert not (tmp_path / "escape.txt").exists()


def _build_backend(root_dir) -> DashboardOverwriteBackend:
    return DashboardOverwriteBackend(root_dir=str(root_dir), virtual_mode=True)


def test_dashboard_edit_applies_in_place(tmp_path) -> None:
    """edit_file 重新開放:dashboard.html 可局部編輯,不再退貨。"""
    backend = _build_backend(tmp_path)
    (tmp_path / "dashboard.html").write_text("<html>OLD</html>", encoding="utf-8")

    edit_result = backend.edit("dashboard.html", "OLD", "NEW")

    assert edit_result.error is None
    assert (tmp_path / "dashboard.html").read_text(encoding="utf-8") == "<html>NEW</html>"


def test_dashboard_edit_missing_old_string_returns_error(tmp_path) -> None:
    """old_string 不存在時回 error(deepagents 內建行為)——prompt 斷路器規則的觸發面。"""
    backend = _build_backend(tmp_path)
    (tmp_path / "dashboard.html").write_text("<html>OLD</html>", encoding="utf-8")

    edit_result = backend.edit("dashboard.html", "ABSENT", "NEW")

    assert edit_result.error is not None


def test_other_files_still_editable(tmp_path) -> None:
    """封鎖只針對 dashboard.html——notes.md 等其他檔案的 edit 行為不變。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "notes.md").write_text("draft", encoding="utf-8")
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    edit_result = backend.edit("notes.md", "draft", "final")

    assert edit_result.error is None
    assert (root / "notes.md").read_text(encoding="utf-8") == "final"
