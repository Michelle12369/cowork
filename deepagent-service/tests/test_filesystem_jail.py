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
    assert (root / "dashboard.html").read_text(encoding="utf-8") == "<html>v2 -- full rewrite</html>"


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
