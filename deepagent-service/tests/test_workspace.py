import zipfile
from pathlib import Path

import pytest

from app.engine.object_store_fs import FilesystemObjectClient
from app.engine.workspace import prepare_local_layout, stage_connector_skills, stage_skills
from app.engine.workspace_store import WorkspaceStore, build_workspace_store


def test_prepare_local_layout_creates_layout(tmp_path: Path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    assert workspace.root == tmp_path / "user-1" / "sessions" / "sess-1"
    assert workspace.queries_dir.is_dir()
    assert workspace.results_dir.is_dir()
    assert workspace.dashboard_path == workspace.root / "dashboard.html"


def test_prepare_local_layout_rejects_path_traversal(tmp_path: Path) -> None:
    # 逃出 workspace root 的 segment 由 containment 檢查擋;含斜線但仍在 root 內者不再拒絕
    with pytest.raises(ValueError):
        prepare_local_layout(tmp_path, "../evil", "sess-1")


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


def test_stage_connector_skills_stages_each_skill_with_unique_frontmatter_name(
    tmp_path: Path,
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    staged_path = stage_connector_skills(
        workspace,
        {"acme": {"usage": "# usage skill", "advanced": "# advanced skill"}},
    )

    assert staged_path == ".skills/connectors"
    usage_path = workspace.skills_dir / "connectors" / "acme" / "usage" / "SKILL.md"
    advanced_path = workspace.skills_dir / "connectors" / "acme" / "advanced" / "SKILL.md"
    usage_content = usage_path.read_text(encoding="utf-8")
    advanced_content = advanced_path.read_text(encoding="utf-8")

    assert "name: acme-usage" in usage_content
    assert "# usage skill" in usage_content
    assert "name: acme-advanced" in advanced_content
    assert "# advanced skill" in advanced_content


def test_stage_connector_skills_skips_invalid_skill_name_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """URI 正規化後理應只剩 `^[\\w-]+$`，這裡驗證意外情況(例如上游 path 含 `..`)下的
    護欄：不合規名稱只跳過並記警告，不中止其他合規 skill 的 staging。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {"acme": {"usage": "# usage skill", "..-evil": "# should be skipped"}},
        )

    assert staged_path == ".skills/connectors"
    assert (workspace.skills_dir / "connectors" / "acme" / "usage" / "SKILL.md").is_file()
    assert not (workspace.skills_dir / "connectors" / "acme" / "..-evil").exists()
    assert any(
        "acme" in record.message and "..-evil" in record.message for record in caplog.records
    )


def test_stage_connector_skills_empty_dict_returns_none_and_creates_nothing(
    tmp_path: Path,
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    staged_path = stage_connector_skills(workspace, {})

    assert staged_path is None
    assert not (workspace.skills_dir / "connectors").exists()


def test_build_workspace_store_local_returns_workspace_store_with_filesystem_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    store = build_workspace_store()

    assert isinstance(store, WorkspaceStore)
    assert isinstance(store._object_client, FilesystemObjectClient)


def test_build_workspace_store_local_roundtrips_through_filesystem_object_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """local 模式與 s3 模式共用同一套 generation 快照佈局——persist 後單一 zip 落在
    `{AGENT_WORKSPACE_ROOT}/workspace/{userId}/sessions/{sessionId}/gen-*.zip`,
    下一次 prepare()(全新 store 實例)能拉回同一份內容,驗證磁碟佈局確實對齊 s3 模式。"""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    store = build_workspace_store()
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")
    store.persist(workspace)

    persisted_generation_zips = list(
        (tmp_path / "workspace" / "user-1" / "sessions" / "sess-1").glob("gen-*.zip")
    )
    assert persisted_generation_zips
    with zipfile.ZipFile(persisted_generation_zips[0]) as archive:
        assert "dashboard.html" in archive.namelist()

    reloaded_workspace = build_workspace_store().prepare("user-1", "sess-1")
    assert reloaded_workspace.dashboard_path.read_text(encoding="utf-8") == "<html></html>"


def test_build_workspace_store_unknown_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")

    with pytest.raises(ValueError, match="unknown STORAGE_BACKEND"):
        build_workspace_store()


def test_build_workspace_store_local_cleanup_scratch_removes_turn_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """local 模式現在走 WorkspaceStore 的 generation 模型,per-turn scratch(`.turns/{hex}/`)
    是真實存在的目錄,cleanup_scratch() MUST 清掉它——與 LocalWorkspaceStore 時代「no-op」
    的語意不同,行為驗證見 test_workspace_store.py 的對應案例(store 類別相同,行為不因
    s3_client 實作換成 FilesystemObjectClient 而改變)。這裡只驗證 local 模式接線正確。"""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    store = build_workspace_store()
    workspace = store.prepare("user-1", "sess-1")
    workspace.dashboard_path.write_text("<html></html>", encoding="utf-8")
    scratch_base = workspace.root.parents[2]
    assert scratch_base.is_dir()

    store.cleanup_scratch()

    assert not scratch_base.exists()
