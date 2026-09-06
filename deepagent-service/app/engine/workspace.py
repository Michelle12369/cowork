"""這裡定義每個使用者, 每個 session 的 workspace 目錄佈局, 以及 skills staging 的邏輯. store
本身(generation 快照模型)跟 build_workspace_store 工廠放在 workspace_store.py.

這是 engine 層, 只能用 stdlib, 不能 import 任何 LLM 框架(ruff 的 TID251 規則會擋下來).
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

# stage_connector_skills 會把每個 connector 的 skill 放進 skills_dir 底下的這個子目錄, 回傳
# 的 staged path(".skills/connectors")會併入 build_agent 的 skills 參數.
_CONNECTOR_SKILLS_DIRNAME = "connectors"

# skill 主文件的檔名. mcp_adapter.py 也有同一個常數值, 兩邊刻意不共用 import.
# engine 層只能用 stdlib, 不依賴 app.agent.connectors.
_SKILL_MAIN_FILE = "SKILL.md"


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


class WorkspacePersistError(RuntimeError):
    """persist 重試次數用完時拋出, 代表這一輪的產出沒有寫進持久層."""


def prepare_local_layout(workspace_root: Path, user_id: str, session_id: str) -> SessionWorkspace:
    """算出 session 目錄的路徑, 並確保骨架目錄都存在; 路徑逃逸由下面的 containment 檢查擋住."""
    resolved_workspace_root = workspace_root.resolve()
    root = (resolved_workspace_root / user_id / "sessions" / session_id).resolve()
    if resolved_workspace_root not in root.parents:
        raise ValueError(f"workspace root escapes workspace_root: {root!r}")

    workspace = SessionWorkspace(root=root)
    workspace.root.mkdir(parents=True, exist_ok=True)
    workspace.queries_dir.mkdir(parents=True, exist_ok=True)
    workspace.results_dir.mkdir(parents=True, exist_ok=True)
    workspace.skills_dir.mkdir(parents=True, exist_ok=True)
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
    """把 builtin 和 user 的 skills 複製進 workspace 的 .skills/, 每一輪先清空一次確保乾淨.
    回傳順序固定 builtin 在前, user 在後, 讓 user skill 能蓋過同名的 builtin skill."""
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


def extract_frontmatter_name(skill_markdown: str) -> str | None:
    """用輕量的字串檢查驗證 SKILL.md 是否有合規的 frontmatter, 並抽出 name 值.
    不符合格式就回傳 None, 呼叫端會視為違反契約整份 skill 跳過."""
    frontmatter_start = "---\n"
    if not skill_markdown.startswith(frontmatter_start):
        return None
    closing_index = skill_markdown.find("\n---", len(frontmatter_start))
    if closing_index == -1:
        return None
    frontmatter_body = skill_markdown[len(frontmatter_start) : closing_index]
    for line in frontmatter_body.splitlines():
        if line.startswith("name:"):
            return line[len("name:") :].strip()
    return None


def stage_connector_skills(
    workspace: SessionWorkspace, skills_by_connector_id: dict[str, dict[str, dict[str, str]]]
) -> str | None:
    """把已選定 connector 的 skills 寫進 skills_dir/connectors/{frontmatter_name}/...
    一定要在 stage_skills 之後呼叫, 因為 stage_skills 每輪都會先清空 skills_dir.
    缺 frontmatter 的 name 或 name 含路徑分隔符的 skill 會被跳過並記警告."""
    if not skills_by_connector_id:
        return None

    connectors_skills_dir = workspace.skills_dir / _CONNECTOR_SKILLS_DIRNAME
    connectors_skills_dir.mkdir(parents=True, exist_ok=True)
    for connector_id, skills in skills_by_connector_id.items():
        for skill_name, skill_files in skills.items():
            skill_markdown = skill_files.get(_SKILL_MAIN_FILE)
            frontmatter_name = (
                extract_frontmatter_name(skill_markdown) if skill_markdown is not None else None
            )
            if frontmatter_name is None:
                logger.warning(
                    "connector %s skill %s SKILL.md is missing frontmatter with a 'name:' "
                    "field (server contract); skipping entire skill",
                    connector_id,
                    skill_name,
                )
                continue
            if "/" in frontmatter_name or "\\" in frontmatter_name or ".." in frontmatter_name:
                logger.warning(
                    "connector %s skill %s frontmatter name %r contains path separators; "
                    "skipping entire skill",
                    connector_id,
                    skill_name,
                    frontmatter_name,
                )
                continue

            skill_dir = connectors_skills_dir / frontmatter_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_root = skill_dir.resolve()
            for relative_path, file_content in skill_files.items():
                destination = (skill_dir / relative_path).resolve()
                if destination == skill_root or not destination.is_relative_to(skill_root):
                    logger.warning(
                        "connector %s skill (%s) file path %r escapes the skill directory, "
                        "skipping this file",
                        connector_id,
                        skill_name,
                        relative_path,
                    )
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(file_content, encoding="utf-8")
    return f".skills/{_CONNECTOR_SKILLS_DIRNAME}"
