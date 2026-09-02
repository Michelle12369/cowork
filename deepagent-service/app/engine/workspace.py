"""Per-user/per-session workspace 目錄佈局與 skills staging。store 本體(generation 快照
模型)與 build_workspace_store 工廠在 workspace_store.py。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

# stage_connector_skills 把每個 connector 的 skill 放進 skills_dir 底下的這個子目錄,回傳的
# staged path(".skills/connectors")併入 build_agent 的 skills 參數。
_CONNECTOR_SKILLS_DIRNAME = "connectors"

# skill 主文件檔名——同一份常數字面值在 mcp_adapter.py 也有一份(_SKILL_MAIN_FILE);兩邊
# 刻意不共用 import(engine 層 stdlib-only,不依賴 app.agent.connectors),各自維護同一字面值。
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

    @property
    def api_snapshots_dir(self) -> Path:
        return self.root / "api_snapshots"

    @property
    def replay_dir(self) -> Path:
        return self.root / "replay"


class WorkspacePersistError(RuntimeError):
    """persist 重試耗盡——本輪產出未寫入持久層。"""


def prepare_local_layout(workspace_root: Path, user_id: str, session_id: str) -> SessionWorkspace:
    """算出 session 目錄路徑並確保骨架目錄存在(路徑逃逸由下方 containment 檢查擋)。"""
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


def extract_frontmatter_name(skill_markdown: str) -> str | None:
    """輕量字串檢查(不引 YAML parser),驗證 `SKILL.md` 是否自帶合規 frontmatter 並抽出
    `name` 值:內容 MUST 以 `---\\n` 起頭,且能找到閉合的 `\\n---`;frontmatter 區塊內
    MUST 有一行 `name:` 欄位。任一條件不符回傳 None(呼叫端視為違反契約,整份 skill 跳過)。

    公開(無底線前綴)供本檔案 `stage_connector_skills` 驗證 frontmatter 合規性用。
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
    """把已選定 connector 的 skills 整包寫入 `skills_dir/connectors/{frontmatter_name}/…`
    (單層,目錄名=frontmatter name=SkillsMiddleware 索引 key;middleware 只掃直接子目錄的
    SKILL.md,兩層佈局掃不到)。MUST 在 `stage_skills` 之後呼叫(它每輪先清空 skills_dir)。

    frontmatter(name/description)是 server 端契約,這裡原樣寫入不合成;name 抽取失敗
    (缺 frontmatter)整份跳過+warning——沒有 name 就沒有目錄名。name 含路徑分隔符或 `..`
    同樣跳過(它要當路徑 segment 用);其餘命名風格交 middleware 自己的軟驗證。撞名=後到
    覆寫(last-wins,名稱唯一性是 server 契約)。支援檔相對路徑逐一 containment 驗證,
    逃逸跳過該檔。未選 connector 回 None(零注入)。
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
