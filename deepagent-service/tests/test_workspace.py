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
    """組出自帶 frontmatter 的 SKILL.md 內容——server 端提供未加前綴的原始 name,
    staging 會再拼上 connector id 前綴並改寫 name: 那一行。"""
    return f"---\nname: {name}\ndescription: 測試用 skill。\n---\n\n{body}"


def test_connector_id_skill_prefix_lowercases_and_collapses_illegal_chars() -> None:
    from app.engine.workspace import connector_id_skill_prefix

    assert connector_id_skill_prefix("MES__Gateway") == "mes-gateway"
    assert connector_id_skill_prefix("demo_quality") == "demo-quality"
    assert connector_id_skill_prefix("--leading-and-trailing--") == "leading-and-trailing"


def test_stage_connector_skills_prefixes_directory_and_rewrites_frontmatter_name_only(
    tmp_path: Path,
) -> None:
    """12a 核心案例:id `demo_quality` ＋ frontmatter name `usage` → 目錄
    `demo-quality-usage`;SKILL.md 的 `name:` 行同步改寫成合成後的名字,正文與支援檔
    逐 byte 不變(只有 name 那一行的值改變)。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    original_skill_markdown = _skill_markdown("usage", "# usage skill\n\nsome body text\n")
    supporting_file_content = "# 詳細參考資料\n保持逐字不變\n"

    staged_path = stage_connector_skills(
        workspace,
        {
            "demo_quality": {
                "usage": {
                    "SKILL.md": original_skill_markdown,
                    "references/detail.md": supporting_file_content,
                }
            }
        },
    )

    assert staged_path == ".skills/connectors"
    skill_dir = workspace.skills_dir / "connectors" / "demo-quality-usage"
    staged_markdown = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    original_lines = original_skill_markdown.splitlines(keepends=True)
    staged_lines = staged_markdown.splitlines(keepends=True)
    assert len(original_lines) == len(staged_lines)
    for original_line, staged_line in zip(original_lines, staged_lines, strict=True):
        if original_line.startswith("name:"):
            assert staged_line == "name: demo-quality-usage\n"
        else:
            assert staged_line == original_line
    assert (skill_dir / "references" / "detail.md").read_text(
        encoding="utf-8"
    ) == supporting_file_content


def test_stage_connector_skills_prefixes_using_sanitized_connector_id(tmp_path: Path) -> None:
    """connector id 含大寫與連續非法字元時, 前綴依 connector_id_skill_prefix 的規則轉換
    後才拼接, 不是原樣拼接。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    stage_connector_skills(
        workspace,
        {"MES__Gateway": {"usage": {"SKILL.md": _skill_markdown("usage", "# usage skill")}}},
    )

    assert (workspace.skills_dir / "connectors" / "mes-gateway-usage" / "SKILL.md").is_file()


def test_stage_connector_skills_same_skill_name_across_different_connectors_stay_separate(
    tmp_path: Path,
) -> None:
    """兩台 server 各自的 skill 都叫 `usage`, 前綴不同就不會撞名, 兩個目錄並存。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    stage_connector_skills(
        workspace,
        {
            "acme": {"usage": {"SKILL.md": _skill_markdown("usage", "# acme usage")}},
            "beta": {"usage": {"SKILL.md": _skill_markdown("usage", "# beta usage")}},
        },
    )

    assert {path.name for path in (workspace.skills_dir / "connectors").iterdir()} == {
        "acme-usage",
        "beta-usage",
    }
    assert "# acme usage" in (
        workspace.skills_dir / "connectors" / "acme-usage" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "# beta usage" in (
        workspace.skills_dir / "connectors" / "beta-usage" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_stage_connector_skills_stages_supporting_files_at_relative_path(
    tmp_path: Path,
) -> None:
    """整包掛載:同目錄與子目錄下的支援檔照相對路徑落地,子目錄自動建立;支援檔原樣
    寫入,只有 `SKILL.md` 的 `name:` 那一行改寫成合成後的名字。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    staged_path = stage_connector_skills(
        workspace,
        {
            "acme": {
                "usage": {
                    "SKILL.md": _skill_markdown("usage", "# usage skill"),
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
                        "SKILL.md": _skill_markdown("usage", "# usage skill"),
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


def test_stage_connector_skills_ignores_skill_name_key_style(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """skills dict 的 key(mcp 端 skill 名)不再做風格驗證——目錄名來自「前綴＋frontmatter
    name」,key 僅供 log 識別;奇怪的 key 只要 frontmatter 正常照樣 staged。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    staged_path = stage_connector_skills(
        workspace,
        {"acme": {"weird key!": {"SKILL.md": _skill_markdown("usage", "# usage skill")}}},
    )

    assert staged_path == ".skills/connectors"
    assert (workspace.skills_dir / "connectors" / "acme-usage" / "SKILL.md").is_file()


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
                    "usage": {"SKILL.md": _skill_markdown("usage", "# usage skill")},
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
    assert {path.name for path in (workspace.skills_dir / "connectors").iterdir()} == {"acme-usage"}
    warning_messages = [record.message for record in caplog.records]
    assert any(
        "acme" in message and "no_frontmatter" in message and "frontmatter" in message
        for message in warning_messages
    )
    assert any(
        "acme" in message and "malformed_frontmatter" in message and "frontmatter" in message
        for message in warning_messages
    )


def test_stage_connector_skills_path_separator_name_skipped_style_violations_staged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """frontmatter name 只擋路徑分隔符/`..`(它是目錄名的一部分);風格違規(如底線)不再
    由 staging 把關,加了前綴後底線依然原樣保留——middleware 對命名風格本有軟驗證,
    repo 端不重複操心。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {
                "acme": {
                    "usage": {"SKILL.md": _skill_markdown("under_score_name", "# staged fine")},
                    "evil": {"SKILL.md": _skill_markdown("../escape", "# must skip")},
                }
            },
        )

    assert staged_path == ".skills/connectors"
    assert (workspace.skills_dir / "connectors" / "acme-under_score_name" / "SKILL.md").is_file()
    assert {path.name for path in (workspace.skills_dir / "connectors").iterdir()} == {
        "acme-under_score_name"
    }
    assert any("../escape" in record.message for record in caplog.records)


def test_stage_connector_skills_duplicate_final_name_within_same_connector_last_wins(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """同一台 server 內兩份 skill 合成後同名:後到覆寫並記 warning(server 端契約=同一台
    內唯一,repo 端仍需優雅處理並留下可觀測的 log)。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    with caplog.at_level("WARNING"):
        stage_connector_skills(
            workspace,
            {
                "acme": {
                    "usage_v1": {"SKILL.md": _skill_markdown("usage", "# first usage")},
                    "usage_v2": {"SKILL.md": _skill_markdown("usage", "# second usage")},
                }
            },
        )

    shared_dir = workspace.skills_dir / "connectors" / "acme-usage"
    assert "# second usage" in (shared_dir / "SKILL.md").read_text(encoding="utf-8")
    assert any(
        "acme" in record.message and "acme-usage" in record.message for record in caplog.records
    )


def test_stage_connector_skills_skips_when_final_name_exceeds_64_chars_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """合成後超過 64 字元:跳過整份 skill 並記 warning(訊息含 connector id 與原
    frontmatter name),不截斷——截斷會讓兩份 skill 撞名。"""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    long_connector_id = "a" * 40
    long_frontmatter_name = "b" * 30

    with caplog.at_level("WARNING"):
        staged_path = stage_connector_skills(
            workspace,
            {
                long_connector_id: {
                    "usage": {"SKILL.md": _skill_markdown(long_frontmatter_name, "# too long")}
                }
            },
        )

    assert staged_path == ".skills/connectors"
    assert not list((workspace.skills_dir / "connectors").iterdir())
    assert any(
        long_connector_id in record.message and long_frontmatter_name in record.message
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
                    "SKILL.md": _skill_markdown("usage", "# usage skill"),
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
                "usage": {"SKILL.md": _skill_markdown("usage", "# demo quality usage skill")}
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


def test_stage_connector_skills_underscore_name_staged_and_discoverable(
    tmp_path: Path,
) -> None:
    """風格違規(底線)的 frontmatter name 照樣 staged(加了前綴後底線依然原樣保留),且
    middleware 探索得到——它對命名風格是軟驗證(warn but load);staging 端不重複把關。"""
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.skills import _list_skills

    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")

    staged_path = stage_connector_skills(
        workspace,
        {
            "demo_quality": {
                "usage": {
                    "SKILL.md": _skill_markdown("under_score_style", "# demo quality usage skill")
                }
            }
        },
    )
    assert staged_path == ".skills/connectors"

    backend = FilesystemBackend(root_dir=str(workspace.root), virtual_mode=True)
    discovered_skills = _list_skills(backend, staged_path)

    assert [skill["name"] for skill in discovered_skills] == ["demo-quality-under_score_style"]


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
