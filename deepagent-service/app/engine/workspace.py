"""Per-user/per-session workspace 目錄佈局與 skills staging。store 本體(generation 快照
模型)與 build_workspace_store 工廠在 workspace_store.py。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

# stage_connector_skills 把每個 connector 的 skill 放進 skills_dir 底下的這個子目錄,回傳的
# staged path(".skills/connectors")併入 build_agent 的 skills 參數。
_CONNECTOR_SKILLS_DIRNAME = "connectors"

# skill 名稱(已經 mcp_adapter._normalize_skill_uri 正規化)落地前的檔案系統 segment 護欄——
# 純防意外(例如上游 URI path 含 `..`),不是完整的路徑逃逸防禦。
_SKILL_NAME_PATTERN = re.compile(r"^[\w-]+$")


@dataclass(frozen=True)
class SessionWorkspace:
    root: Path

    @property
    def queries_dir(self) -> Path:
        return self.root / "queries"

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    @property
    def dashboard_path(self) -> Path:
        return self.root / "dashboard.html"

    @property
    def skills_dir(self) -> Path:
        return self.root / ".skills"

    @property
    def sources_manifest_path(self) -> Path:
        return self.root / ".sources-manifest.json"

    @property
    def api_snapshots_dir(self) -> Path:
        return self.root / "api_snapshots"

    @property
    def replay_dir(self) -> Path:
        return self.root / "replay"


class WorkspacePersistError(RuntimeError):
    """persist 重試耗盡——本輪產出未寫入持久層。"""


def prepare_local_layout(workspace_root: Path, user_id: str, session_id: str) -> SessionWorkspace:
    """算出 session 目錄路徑並確保骨架目錄存在（路徑逃逸由下方 containment 檢查擋）。"""
    resolved_workspace_root = workspace_root.resolve()
    root = (resolved_workspace_root / user_id / "sessions" / session_id).resolve()
    if resolved_workspace_root not in root.parents:
        raise ValueError(f"workspace root escapes workspace_root: {root!r}")

    workspace = SessionWorkspace(root=root)
    workspace.root.mkdir(parents=True, exist_ok=True)
    workspace.queries_dir.mkdir(parents=True, exist_ok=True)
    workspace.results_dir.mkdir(parents=True, exist_ok=True)
    workspace.skills_dir.mkdir(parents=True, exist_ok=True)
    workspace.api_snapshots_dir.mkdir(parents=True, exist_ok=True)
    workspace.replay_dir.mkdir(parents=True, exist_ok=True)
    return workspace


def resolve_workspace_root() -> Path:
    return Path(get_settings().AGENT_WORKSPACE_ROOT)


def builtin_skills_dir() -> Path:
    override = get_settings().AGENT_BUILTIN_SKILLS_DIR
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "skills"


def _has_skill(directory: Path) -> bool:
    return directory.is_dir() and any(directory.glob("*/SKILL.md"))


def stage_skills(
    workspace: SessionWorkspace, builtin_dir: Path, user_skills_dir: Path
) -> list[str]:
    """把 builtin/user skills 複製進 workspace 的 `.skills/`(deepagents filesystem backend
    要求 skills 路徑在其 root 之下)。每 turn 先清空以保證乾淨 stage;回傳存在且非空
    (含至少一個 `*/SKILL.md`)者的相對路徑,順序固定 builtin 在前——deepagents 同名 skill
    後者覆寫前者,個人 skill 蓋內建。
    """
    shutil.rmtree(workspace.skills_dir, ignore_errors=True)
    workspace.skills_dir.mkdir(parents=True, exist_ok=True)

    staged: list[str] = []
    for source_dir, name in ((builtin_dir, "builtin"), (user_skills_dir, "user")):
        if not _has_skill(source_dir):
            continue
        destination = workspace.skills_dir / name
        shutil.copytree(source_dir, destination, dirs_exist_ok=True)
        staged.append(f".skills/{name}")
    return staged


def stage_connector_skills(
    workspace: SessionWorkspace, skills_by_connector_id: dict[str, dict[str, str]]
) -> str | None:
    """把已選定 connector 的 skills(`Connector.skills`:skill 名稱 → markdown,一個
    connector 可供多份)逐一寫入 `skills_dir/connectors/{connector_id}/{skill_name}/SKILL.md`,
    供 deepagents skills 機制漸進揭露(context 只留一行索引,agent 需要時才讀全文)。
    **MUST 在 `stage_skills` 之後呼叫**——`stage_skills` 每輪先清空整個 `skills_dir`,
    順序顛倒這裡寫的檔案會被清掉。

    connector 供應層給的 markdown 只有 skill 正文,不含 deepagents SKILL.md 格式要求的 YAML
    frontmatter(`name`/`description`)——deepagents `SkillsMiddleware` 對缺 frontmatter 的
    SKILL.md 是整份跳過(不進索引),這裡代 connector 補上最小 frontmatter:`name` 用
    `{connector_id}-{skill_name}`(同一 connector 多份 skill 需要唯一 name)、`description`
    用固定樣板。

    `skill_name` 是 mcp_adapter 正規化後的字串,落地前仍以 `^[\\w-]+$` 做一次檔案系統
    segment 護欄——不合規者只記警告並跳過該份 skill,不中止整個 staging。

    未選任何 connector(空字典)不建立 `connectors/` 目錄、回傳 None——維持零注入原則;
    呼叫端據此決定要不要把回傳值併入 `staged_skill_paths`。
    """
    if not skills_by_connector_id:
        return None

    connectors_skills_dir = workspace.skills_dir / _CONNECTOR_SKILLS_DIRNAME
    connectors_skills_dir.mkdir(parents=True, exist_ok=True)
    for connector_id, skills in skills_by_connector_id.items():
        for skill_name, skill_markdown in skills.items():
            if not _SKILL_NAME_PATTERN.match(skill_name):
                logger.warning(
                    "connector %s 的 skill 名稱 %r 不合法(不符 ^[\\w-]+$),略過 staging",
                    connector_id,
                    skill_name,
                )
                continue
            skill_dir = connectors_skills_dir / connector_id / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            frontmatter_name = f"{connector_id}-{skill_name}"
            frontmatter = (
                "---\n"
                f"name: {frontmatter_name}\n"
                f"description: connector `{connector_id}` 的操作劇本(`{skill_name}`)——"
                "查詢/落表前必讀,涵蓋 tools 清單與語意、呼叫順序與相依、參數來源、範例。\n"
                "---\n\n"
            )
            (skill_dir / "SKILL.md").write_text(frontmatter + skill_markdown, encoding="utf-8")
    return f".skills/{_CONNECTOR_SKILLS_DIRNAME}"
