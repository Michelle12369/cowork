"""tests/test_renderer_subagent_spec.py"""

from pathlib import Path

from deepagents.middleware.filesystem import _check_fs_permission

from app.agent.renderer_subagent import RENDERER_SUBAGENT_NAME, build_renderer_subagent
from app.engine.workspace import SessionWorkspace


def _workspace_with_skill(tmp_path: Path) -> SessionWorkspace:
    workspace = SessionWorkspace(root=tmp_path)
    skill_dir = tmp_path / ".skills" / "builtin" / "dashboard"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("CHART RULES SENTINEL", encoding="utf-8")
    return workspace


def test_build_renderer_subagent_embeds_skill_content_in_system_prompt(tmp_path: Path) -> None:
    spec = build_renderer_subagent(_workspace_with_skill(tmp_path))
    assert spec["name"] == RENDERER_SUBAGENT_NAME
    assert "CHART RULES SENTINEL" in spec["system_prompt"]
    assert "<!DOCTYPE html>" in spec["system_prompt"]


def test_build_renderer_subagent_denies_writes_allows_reads(tmp_path: Path) -> None:
    spec = build_renderer_subagent(_workspace_with_skill(tmp_path))
    permissions = spec["permissions"]
    assert permissions[0].mode == "deny" and permissions[0].operations == ["write"]
    assert permissions[1].mode == "allow" and permissions[1].operations == ["read"]


def test_build_renderer_subagent_no_data_tools_inherits_main_model(tmp_path: Path) -> None:
    spec = build_renderer_subagent(_workspace_with_skill(tmp_path))
    assert spec["tools"] == []
    assert (
        "model" not in spec
    )  # 省略 model 鍵＝繼承主 agent model（測試共用 ScriptedChatModel 靠這點）


def test_build_renderer_subagent_missing_skill_dir_fails_open_with_contract(tmp_path: Path) -> None:
    workspace = SessionWorkspace(root=tmp_path)  # 無 .skills 目錄
    spec = build_renderer_subagent(workspace)
    assert "<!DOCTYPE html>" in spec["system_prompt"]  # 契約段仍在,skill 缺席不炸


def test_build_renderer_subagent_permission_rules_deny_write_allow_read_on_real_path(
    tmp_path: Path,
) -> None:
    # 欄位值本身不保證行為——之前用裸 "/" 當 pattern,wcglob 只匹配根目錄字面值,任何真實
    # 檔案路徑都不命中,deny 規則靜默落空。這裡直接跑 deepagents 的比對函式驗證真實效果。
    spec = build_renderer_subagent(_workspace_with_skill(tmp_path))
    permissions = spec["permissions"]
    assert _check_fs_permission(permissions, "write", "/dashboard.html") == "deny"
    assert _check_fs_permission(permissions, "read", "/notes.md") == "allow"
