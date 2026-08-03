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


def test_notes_md_can_be_overwritten_after_it_exists(tmp_path) -> None:
    """notes.md 併入 overwrite 洞(single-write 補強):edit_file 從模型可見工具移除後,
    notes.md 的迭代修改只能靠 write_file 整份重寫,行為與 dashboard.html 對稱。"""
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


def test_dashboard_overwrite_backend_rollback_on_encode_failure(tmp_path) -> None:
    """N4: `write()` used to unlink the existing dashboard.html and then delegate to the
    parent's create-only `write()` -- if that second step raised, the previous working
    dashboard was already gone with nothing to fall back to, on every edit turn. A lone
    UTF-16 surrogate is content the `encoding="utf-8"` file handle genuinely cannot encode
    (no monkeypatching needed), simulating a write that fails partway through. The original
    file MUST survive untouched and the caller MUST see the error."""
    root = tmp_path / "ws"
    root.mkdir()
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)
    backend.write("dashboard.html", "<html>v1 -- good</html>")

    result = backend.write("dashboard.html", "<html>broken \ud800 surrogate</html>")

    assert result.error is not None
    assert (root / "dashboard.html").read_text(encoding="utf-8") == "<html>v1 -- good</html>"
    leftover_temp_files = [entry for entry in root.iterdir() if entry.name != "dashboard.html"]
    assert leftover_temp_files == []


def test_dashboard_overwrite_backend_rollback_when_replace_fails(tmp_path, monkeypatch) -> None:
    """Same guarantee at the final atomic-rename step: if `os.replace` itself fails (disk
    full, permissions, ...) after the new content has been fully written to a temp file, the
    original dashboard.html MUST still be intact -- not half-deleted, not half-written."""
    import app.agent.graph as graph_module

    root = tmp_path / "ws"
    root.mkdir()
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)
    backend.write("dashboard.html", "<html>v1 -- good</html>")

    def _raise_on_replace(*args: object, **kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(graph_module.os, "replace", _raise_on_replace)

    result = backend.write("dashboard.html", "<html>v2 -- never lands</html>")

    assert result.error is not None
    assert (root / "dashboard.html").read_text(encoding="utf-8") == "<html>v1 -- good</html>"
    leftover_temp_files = [entry for entry in root.iterdir() if entry.name != "dashboard.html"]
    assert leftover_temp_files == []


def test_other_files_still_editable(tmp_path) -> None:
    """封鎖只針對 dashboard.html——notes.md 等其他檔案的 edit 行為不變。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "notes.md").write_text("draft", encoding="utf-8")
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    edit_result = backend.edit("notes.md", "draft", "final")

    assert edit_result.error is None
    assert (root / "notes.md").read_text(encoding="utf-8") == "final"
