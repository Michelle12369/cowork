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


def _skill_markdown(name: str, body: str) -> str:
    """組出自帶 frontmatter 的 SKILL.md 內容——契約定案後 staging 不再代合成,測試 fixture
    須自行帶好 `name:`/`description:`,比照 server 端契約。"""
    return f"---\nname: {name}\ndescription: 測試用 skill。\n---\n\n{body}"


def test_stage_connector_skills_stages_each_skill_as_is_with_frontmatter_from_server(
    tmp_path: Path,
) -> None:
    """frontmatter 是 server 端契約責任,staging 不再合成——內容(含 frontmatter)原樣寫入。
    佈局是單層、目錄名＝frontmatter name(不是 `{connector_id}/{skill_name}`),見
    `stage_connector_skills` docstring 的佈局說明——這是本函式的核心契約,深度多一層
    deepagents `SkillsMiddleware` 就掃不到(見 test_stage_connector_skills_result_is_
    discovered_by_skills_middleware_index)。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    staged_path = stage_connector_skills(
        workspace,
        {
            "acme": {
                "usage": {"SKILL.md": _skill_markdown("acme-usage", "# usage skill")},
                "advanced": {"SKILL.md": _skill_markdown("acme-advanced", "# advanced skill")},
            }
        },
    )

    assert staged_path == ".skills/connectors"
    usage_path = workspace.skills_dir / "connectors" / "acme-usage" / "SKILL.md"
    advanced_path = workspace.skills_dir / "connectors" / "acme-advanced" / "SKILL.md"
    usage_content = usage_path.read_text(encoding="utf-8")
    advanced_content = advanced_path.read_text(encoding="utf-8")

    assert usage_content == _skill_markdown("acme-usage", "# usage skill")
    assert advanced_content == _skill_markdown("acme-advanced", "# advanced skill")


def test_stage_connector_skills_stages_supporting_files_at_relative_path(
    tmp_path: Path,
) -> None:
    """整包掛載:同目錄與子目錄下的支援檔照相對路徑落地,子目錄自動建立;所有內容
    (含 `SKILL.md`)原樣寫入,不做任何修改。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    staged_path = stage_connector_skills(
        workspace,
        {
            "acme": {
                "usage": {
                    "SKILL.md": _skill_markdown("acme-usage", "# usage skill"),
                    "references/detail.md": "# 詳細參考資料",
                }
            }
        },
    )

    assert staged_path == ".skills/connectors"
    skill_dir = workspace.skills_dir / "connectors" / "acme-usage"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == _skill_markdown(
        "acme-usage", "# usage skill"
    )
    assert (skill_dir / "references" / "detail.md").read_text(encoding="utf-8") == "# 詳細參考資料"


def test_stage_connector_skills_skips_escaping_relative_path_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """支援檔相對路徑來自 server,`../` 逃逸或絕對路徑一律跳過該檔＋warning,不中止同一份
    skill 內其他檔案的 staging(比照 `prepare_local_layout` 的 containment 前例)。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {
                "acme": {
                    "usage": {
                        "SKILL.md": _skill_markdown("acme-usage", "# usage skill"),
                        "../escape.md": "# should be skipped",
                    }
                }
            },
        )

    assert staged_path == ".skills/connectors"
    skill_dir = workspace.skills_dir / "connectors" / "acme-usage"
    assert (skill_dir / "SKILL.md").is_file()
    assert not (workspace.skills_dir / "connectors" / "escape.md").exists()
    assert any(
        "acme" in record.message and "../escape.md" in record.message for record in caplog.records
    )


def test_stage_connector_skills_skips_invalid_skill_name_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """URI 正規化後理應只剩 `^[\\w-]+$`,這裡驗證意外情況(例如上游 path 含 `..`)下的
    護欄:不合規名稱只跳過並記警告,不中止其他合規 skill 的 staging。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {
                "acme": {
                    "usage": {"SKILL.md": _skill_markdown("acme-usage", "# usage skill")},
                    "..-evil": {"SKILL.md": "# should be skipped"},
                }
            },
        )

    assert staged_path == ".skills/connectors"
    assert (workspace.skills_dir / "connectors" / "acme-usage" / "SKILL.md").is_file()
    assert {path.name for path in (workspace.skills_dir / "connectors").iterdir()} == {
        "acme-usage"
    }
    assert any(
        "acme" in record.message and "..-evil" in record.message for record in caplog.records
    )


def test_stage_connector_skills_skips_skill_missing_frontmatter_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """frontmatter 是 server 端契約責任——缺 frontmatter(或缺 `name:` 欄位)的 `SKILL.md`
    不再代為合成,整份 skill(含支援檔)跳過並記 warning,其他合規 skill 不受影響。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {
                "acme": {
                    "usage": {"SKILL.md": _skill_markdown("acme-usage", "# usage skill")},
                    "no_frontmatter": {
                        "SKILL.md": "# no frontmatter skill",
                        "references/detail.md": "# should also be skipped",
                    },
                    "malformed_frontmatter": {
                        "SKILL.md": "---\ndescription: 缺 name 欄位\n---\n\n# malformed",
                    },
                }
            },
        )

    assert staged_path == ".skills/connectors"
    assert (workspace.skills_dir / "connectors" / "acme-usage" / "SKILL.md").is_file()
    assert {path.name for path in (workspace.skills_dir / "connectors").iterdir()} == {
        "acme-usage"
    }
    warning_messages = [record.message for record in caplog.records]
    assert any(
        "acme" in message and "no_frontmatter" in message and "frontmatter" in message
        for message in warning_messages
    )
    assert any(
        "acme" in message and "malformed_frontmatter" in message and "frontmatter" in message
        for message in warning_messages
    )


def test_stage_connector_skills_skips_invalid_frontmatter_name_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """frontmatter `name` 本身違反 Agent Skills 命名約束(這裡用底線,spec 只允許小寫英數字
    與連字號)——這個名字同時是新佈局的目錄名,不驗證就落地會把不合規字元寫進路徑;整份
    skill 跳過並記警告(訊息點名 Agent Skills 約束),其他合規 skill 不受影響。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {
                "acme": {
                    "usage": {"SKILL.md": _skill_markdown("acme-usage", "# usage skill")},
                    "bad_name": {
                        "SKILL.md": _skill_markdown("acme_bad_name", "# should be skipped")
                    },
                }
            },
        )

    assert staged_path == ".skills/connectors"
    assert (workspace.skills_dir / "connectors" / "acme-usage" / "SKILL.md").is_file()
    assert {path.name for path in (workspace.skills_dir / "connectors").iterdir()} == {
        "acme-usage"
    }
    warning_messages = [record.message for record in caplog.records]
    assert any(
        "acme_bad_name" in message and "Agent Skills" in message for message in warning_messages
    )


def test_stage_connector_skills_skips_the_later_duplicate_frontmatter_name_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """frontmatter `name` 值撞名——新佈局下撞名會撞到同一個 staging 目錄(不像舊的兩層
    佈局那樣各自獨立),因此改為先到先贏、後到者整份跳過並記警告(名稱全域唯一是 Agent
    Skills spec 的契約責任,repo 端不仲裁只提醒)。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {
                "acme": {"usage": {"SKILL.md": _skill_markdown("shared-name", "# acme usage")}},
                "beta": {"usage": {"SKILL.md": _skill_markdown("shared-name", "# beta usage")}},
            },
        )

    assert staged_path == ".skills/connectors"
    shared_dir = workspace.skills_dir / "connectors" / "shared-name"
    assert (shared_dir / "SKILL.md").is_file()
    # 先到先贏:dict 迭代順序＝插入順序,acme 先處理,內容應為 acme 版本。
    assert "# acme usage" in (shared_dir / "SKILL.md").read_text(encoding="utf-8")
    assert {path.name for path in (workspace.skills_dir / "connectors").iterdir()} == {
        "shared-name"
    }
    assert any(
        "shared-name" in record.message
        and "acme/usage" in record.message
        and "beta/usage" in record.message
        for record in caplog.records
    )


def test_stage_connector_skills_over_file_count_limit_still_stages_skill_md(
    tmp_path: Path,
) -> None:
    """量上限由 mcp_adapter 端強制(見 test_mcp_adapter.py),staging 端只需正確落地
    `Connector.skills` 已經套過上限的字典——這裡驗證即使字典帶著大量支援檔,SKILL.md
    仍照常落地、每份支援檔各自落在自己的相對路徑。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    supporting_files = {f"notes/note{index:02d}.md": f"# note {index}" for index in range(19)}

    staged_path = stage_connector_skills(
        workspace,
        {
            "acme": {
                "usage": {
                    "SKILL.md": _skill_markdown("acme-usage", "# usage skill"),
                    **supporting_files,
                }
            }
        },
    )

    assert staged_path == ".skills/connectors"
    skill_dir = workspace.skills_dir / "connectors" / "acme-usage"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "notes" / "note00.md").read_text(encoding="utf-8") == "# note 0"
    assert (skill_dir / "notes" / "note18.md").read_text(encoding="utf-8") == "# note 18"


def test_stage_connector_skills_empty_dict_returns_none_and_creates_nothing(
    tmp_path: Path,
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    staged_path = stage_connector_skills(workspace, {})

    assert staged_path is None
    assert not (workspace.skills_dir / "connectors").exists()


def test_stage_connector_skills_result_is_discovered_by_skills_middleware_index(
    tmp_path: Path,
) -> None:
    """回歸測試——本 bug 的根因正是缺這條:證明 staging 出的 connector skill 真的會出現在
    deepagents `SkillsMiddleware` 的探索結果裡(用 middleware 實際的探索函式 `_list_skills`
    對 staging 出的 workspace 掃描,而不只是斷言檔案落在哪裡)。過去的兩層佈局
    (`connectors/{connector_id}/{skill_name}/SKILL.md`)在這裡會得到空清單,因為
    `_list_skills` 只掃 source_path 的直接子目錄。"""
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.skills import _list_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    staged_path = stage_connector_skills(
        workspace,
        {
            "demo_quality": {
                "usage": {
                    "SKILL.md": _skill_markdown("demo-quality-usage", "# demo quality usage skill")
                }
            }
        },
    )
    assert staged_path == ".skills/connectors"

    backend = FilesystemBackend(root_dir=str(workspace.root), virtual_mode=True)
    discovered_skills = _list_skills(backend, staged_path)

    discovered_names = {skill["name"] for skill in discovered_skills}
    assert "demo-quality-usage" in discovered_names
    matched_skill = next(
        skill for skill in discovered_skills if skill["name"] == "demo-quality-usage"
    )
    assert matched_skill["description"] == "測試用 skill。"


def test_stage_connector_skills_underscore_name_is_absent_from_middleware_index(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """反向回歸:frontmatter name 含底線(違反 Agent Skills 命名約束)的 skill 不進 staging
    目錄,因此也不會出現在 `SkillsMiddleware` 的探索結果裡;staging 端另有 warning 記錄。"""
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.skills import _list_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {
                "demo_quality": {
                    "usage": {
                        "SKILL.md": _skill_markdown(
                            "demo_quality_usage", "# demo quality usage skill"
                        )
                    }
                }
            },
        )
    assert staged_path == ".skills/connectors"

    backend = FilesystemBackend(root_dir=str(workspace.root), virtual_mode=True)
    discovered_skills = _list_skills(backend, staged_path)

    assert discovered_skills == []
    assert any(
        "demo_quality_usage" in record.message and "Agent Skills" in record.message
        for record in caplog.records
    )


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
