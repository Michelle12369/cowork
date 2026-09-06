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

# 這是 skill 主文件的檔名. mcp_adapter.py 裡也有同一個常數字面值(_SKILL_MAIN_FILE), 兩邊
# 刻意不共用 import, 因為 engine 層只能用 stdlib, 不能依賴 app.agent.connectors, 所以各自
# 維護同一個字面值.
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
    """把 builtin 和 user 的 skills 複製進 workspace 的 .skills/, 因為 deepagents 的
    filesystem backend 要求 skills 路徑要在它的 root 之下. 每一輪先清空一次, 確保 stage
    是乾淨的; 回傳存在且非空(至少含一個 */SKILL.md)的相對路徑, 順序固定是 builtin 在前,
    因為 deepagents 對同名 skill 是後者覆寫前者, 這樣個人 skill 才能蓋過內建的.
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


def extract_frontmatter_name(skill_markdown: str) -> str | None:
    """用輕量的字串檢查(不引入 YAML parser)驗證 SKILL.md 是否帶有合規的 frontmatter, 並
    抽出 name 值. 內容一定要以 ---\\n 開頭, 而且要能找到對應的結尾 \\n---; frontmatter
    區塊裡一定要有一行 name: 欄位. 任何一個條件不符合就回傳 None, 呼叫端會把這種情況視為
    違反契約, 整份 skill 跳過.

    這個函式故意不加底線前綴, 因為 stage_connector_skills 要用它來驗證 frontmatter 合規性.
    """
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
    """把已經選定的 connector 的 skills 整包寫進 skills_dir/connectors/{frontmatter_name}/...
    這是單層目錄結構, 目錄名就是 frontmatter 的 name, 也是 SkillsMiddleware 拿來索引的
    key; middleware 只會掃直接子目錄下的 SKILL.md, 兩層的目錄佈局它掃不到. 一定要在
    stage_skills 之後呼叫, 因為 stage_skills 每一輪都會先清空 skills_dir.

    frontmatter 裡的 name 和 description 是 server 端的契約內容, 這裡只是原樣寫入, 不會
    自己合成. 如果抽不出 name(缺 frontmatter), 整份 skill 就跳過並記一筆警告, 因為沒有
    name 就沒有目錄名可以用. name 裡如果含路徑分隔符或 .., 也一樣跳過, 因為它要直接當成
    路徑 segment 用; 其餘的命名風格交給 middleware 自己做軟驗證. 撞名的話是後到的覆寫先
    到的, 名稱的唯一性本身是 server 端的契約. 每個支援檔的相對路徑都會逐一做 containment
    驗證, 逃逸的檔案會被跳過. 沒有選任何 connector 就回傳 None, 代表零注入.
    """
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
