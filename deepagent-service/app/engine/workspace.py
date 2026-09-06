"""這裡定義每個使用者, 每個 session 的 workspace 目錄佈局, 以及 skills staging 的邏輯. store
本身(generation 快照模型)跟 build_workspace_store 工廠放在 workspace_store.py.

這是 engine 層, 只能用 stdlib, 不能 import 任何 LLM 框架(ruff 的 TID251 規則會擋下來).
"""

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

# 合成後的 skill 目錄名(前綴＋frontmatter name)超過這個長度就整份跳過, 不截斷.
_MAX_SKILL_NAME_LENGTH = 64

_NON_SKILL_ID_CHARS = re.compile(r"[^a-z0-9]+")

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


def replace_frontmatter_name(skill_markdown: str, new_name: str) -> str:
    """只改寫 frontmatter 裡 name: 那一行的值, 其餘 byte(含換行符與其他欄位)不動.
    呼叫端須先用 extract_frontmatter_name 確認 frontmatter 合規再呼叫本函式."""
    frontmatter_start = "---\n"
    closing_index = skill_markdown.find("\n---", len(frontmatter_start))
    frontmatter_body = skill_markdown[len(frontmatter_start) : closing_index]

    rewritten_lines = []
    for line in frontmatter_body.splitlines(keepends=True):
        if line.startswith("name:"):
            line_ending = "\n" if line.endswith("\n") else ""
            rewritten_lines.append(f"name: {new_name}{line_ending}")
        else:
            rewritten_lines.append(line)

    return (
        skill_markdown[: len(frontmatter_start)]
        + "".join(rewritten_lines)
        + skill_markdown[closing_index:]
    )


def connector_id_skill_prefix(connector_id: str) -> str:
    """把 connector id 轉成 deepagents 合法的 skill 名片段: 全部小寫, 非英數字元換成
    連字號, 連續連字號壓成一個, 去頭尾連字號."""
    lowered = connector_id.lower()
    collapsed = _NON_SKILL_ID_CHARS.sub("-", lowered)
    return collapsed.strip("-")


def stage_connector_skills(
    workspace: SessionWorkspace, skills_by_connector_id: dict[str, dict[str, dict[str, str]]]
) -> str | None:
    """把已選定 connector 的 skills 寫進 skills_dir/connectors/{connector id 前綴}-{frontmatter
    name}/, 一定要在 stage_skills 之後呼叫(它每輪會先清空 skills_dir). 目錄只能單層,
    巢狀 SkillsMiddleware 掃不到; 同一台 server 內合成後撞名則後到覆寫並記 warning."""
    if not skills_by_connector_id:
        return None

    connectors_skills_dir = workspace.skills_dir / _CONNECTOR_SKILLS_DIRNAME
    connectors_skills_dir.mkdir(parents=True, exist_ok=True)
    staged_final_names: set[str] = set()
    for connector_id, skills in skills_by_connector_id.items():
        connector_prefix = connector_id_skill_prefix(connector_id)
        for skill_name, skill_files in skills.items():
            skill_markdown = skill_files.get(_SKILL_MAIN_FILE)
            frontmatter_name = (
                extract_frontmatter_name(skill_markdown) if skill_markdown is not None else None
            )
            # 空字串或全空白的 name 視同缺少, 否則會滑過檢查產生開頭連字號的合成名.
            if not frontmatter_name:
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

            final_name = f"{connector_prefix}-{frontmatter_name}"
            if len(final_name) > _MAX_SKILL_NAME_LENGTH:
                logger.warning(
                    "connector %s skill %s: final name after adding the connector id prefix "
                    "exceeds %d characters (original frontmatter name %r); skipping entire "
                    "skill instead of truncating",
                    connector_id,
                    skill_name,
                    _MAX_SKILL_NAME_LENGTH,
                    frontmatter_name,
                )
                continue
            if final_name in staged_final_names:
                logger.warning(
                    "connector %s skill %s: final name %r collides with an already staged "
                    "skill (connector=%s), overwriting (last wins)",
                    connector_id,
                    skill_name,
                    final_name,
                    connector_id,
                )
            staged_final_names.add(final_name)

            skill_dir = connectors_skills_dir / final_name
            # 撞名時整個目錄重建, 避免前一份的支援檔留在贏家旁邊(last-wins 是取代不是合併).
            shutil.rmtree(skill_dir, ignore_errors=True)
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_root = skill_dir.resolve()
            rewritten_skill_markdown = replace_frontmatter_name(skill_markdown, final_name)
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
                content_to_write = (
                    rewritten_skill_markdown if relative_path == _SKILL_MAIN_FILE else file_content
                )
                destination.write_text(content_to_write, encoding="utf-8")
    return f".skills/{_CONNECTOR_SKILLS_DIRNAME}"
