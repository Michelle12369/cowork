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
    rewrite dashboard.html (not a targeted edit_file patch) was rejected outright by
    deepagents 0.6.12's create-only FilesystemBackend.write(), stalling the turn with no
    recovery path."""
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


def test_dashboard_overwrite_backend_still_rejects_other_existing_files(tmp_path) -> None:
    root = tmp_path / "workspace-root"
    root.mkdir()
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    first = backend.write("notes.md", "hello")
    assert first.error is None

    second = backend.write("notes.md", "overwritten")
    assert second.error is not None
    assert (root / "notes.md").read_text(encoding="utf-8") == "hello"


def test_dashboard_overwrite_backend_still_blocks_path_traversal(tmp_path) -> None:
    root = tmp_path / "workspace-root"
    root.mkdir()
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    with pytest.raises(ValueError, match="traversal"):
        backend.write("../escape.txt", "pwned")
    assert not (tmp_path / "escape.txt").exists()


def test_dashboard_edit_rejected_with_rewrite_instruction(tmp_path) -> None:
    """dashboard.html 的 edit_file 一律退貨,錯誤訊息本身指示改用單次 write_file 整份重寫。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "dashboard.html").write_text("<html><body>OLD</body></html>", encoding="utf-8")
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    edit_result = backend.edit("dashboard.html", "OLD", "NEW")

    assert edit_result.error is not None
    assert "write_file" in edit_result.error
    assert (root / "dashboard.html").read_text(encoding="utf-8") == "<html><body>OLD</body></html>"


def test_dashboard_edit_rejected_via_absolute_style_path(tmp_path) -> None:
    """virtual_mode 會把絕對路徑重新錨定到 root 內——用絕對路徑指涉 dashboard.html 一樣被擋。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "dashboard.html").write_text("x", encoding="utf-8")
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    edit_result = backend.edit("/dashboard.html", "x", "y")

    assert edit_result.error is not None
    assert "write_file" in edit_result.error


def test_other_files_still_editable(tmp_path) -> None:
    """封鎖只針對 dashboard.html——notes.md 等其他檔案的 edit 行為不變。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "notes.md").write_text("draft", encoding="utf-8")
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    edit_result = backend.edit("notes.md", "draft", "final")

    assert edit_result.error is None
    assert (root / "notes.md").read_text(encoding="utf-8") == "final"
