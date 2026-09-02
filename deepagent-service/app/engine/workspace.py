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

# skill 名稱(已經 mcp_adapter._read_skills 透過 fastmcp.utilities.skills.list_skills 解出)
# 落地前的檔案系統 segment 護欄——純防意外(例如上游目錄名含 `..`),不是完整的路徑逃逸防禦。
_SKILL_NAME_PATTERN = re.compile(r"^[\w-]+$")

# Agent Skills spec(https://agentskills.io/specification)對 skill 識別碼(frontmatter
# `name`)的約束子集(deepagents `SkillsMiddleware._validate_skill_name` 的同款規則,唯該處
# 違規只警告不擋——見 is_valid_frontmatter_name docstring)。這裡刻意只驗證 ASCII 小寫英數字
# 與連字號(不含 spec 允許的 Unicode 小寫字母),足以涵蓋現行 connector 命名慣例、且與檔案系統
# segment 安全性天然對齊。
_AGENT_SKILLS_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_AGENT_SKILLS_NAME_LENGTH = 64

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


def is_valid_frontmatter_name(name: str) -> bool:
    """`name` 是否符合 Agent Skills spec 對 skill 識別碼的約束子集(1-64 字元、僅小寫
    ASCII 英數字與連字號、不得開頭/結尾/連續連字號)。deepagents `SkillsMiddleware` 對
    「name 與目錄名不符」只警告不擋(`_validate_skill_name` 的 docstring 明寫「warn but
    continue loading for backwards compatibility」)——但這條約束同時決定 staging 目錄名,
    repo 端選擇在寫檔前就擋掉違規名稱(loud 失敗優於讓一個不合規的名字靜靜進了索引)。
    """
    return (
        bool(name)
        and len(name) <= _MAX_AGENT_SKILLS_NAME_LENGTH
        and bool(_AGENT_SKILLS_NAME_PATTERN.match(name))
    )


def stage_connector_skills(
    workspace: SessionWorkspace, skills_by_connector_id: dict[str, dict[str, dict[str, str]]]
) -> str | None:
    """把已選定 connector 的 skills(`Connector.skills`:skill 名稱 → {相對路徑 → 內容},
    一個 connector 可供多份)整包寫入
    `skills_dir/connectors/{frontmatter_name}/{relative_path}`(**單層**,子目錄自動建立),
    供 deepagents skills 機制漸進揭露(context 只留一行索引,agent 需要時才讀全文/支援檔)——
    與 builtin skills 的「SKILL.md＋支援檔漸進揭露」同構。**MUST 在 `stage_skills` 之後
    呼叫**——`stage_skills` 每輪先清空整個 `skills_dir`,順序顛倒這裡寫的檔案會被清掉。

    **佈局是單層,目錄名＝frontmatter name,不是 `{connector_id}/{skill_name}` 兩層**——這是
    這裡先前的 bug 根因:`deepagents.middleware.skills._list_skills_with_errors` 對每個
    skills path(這裡回傳的 `".skills/connectors"`)只掃**直接子目錄**底下的 `SKILL.md`
    (`source_path/{skill-name}/SKILL.md`),不會往下探第二層;舊版兩層佈局
    (`connectors/{connector_id}/{skill_name}/SKILL.md`)因此永遠零命中,掛載的 connector
    skill 從未進過 middleware 的 system prompt 索引。目錄名同時是 Agent Skills spec
    (https://agentskills.io/specification)規定「MUST 等於含 `SKILL.md` 的父目錄名」的
    那個名字,也是 deepagents skill 索引的 key,所以直接用 frontmatter `name`(已驗證見下)
    當目錄名一次滿足兩邊契約。

    **frontmatter 責任翻轉**:YAML frontmatter(`name`/`description`)是 MCP server 端的
    契約責任——connector 供應層送來的 `SKILL.md` MUST 自帶完整 frontmatter,這裡不再代為
    合成,一律原樣寫入。只做驗證,兩層都在:(1)`SKILL.md` 內容開頭不是 `---\\n` 起頭且能
    找到閉合 `\\n---`,或 frontmatter 區塊內缺 `name:` 欄位(見 `extract_frontmatter_name`)
    ——過去由本函式代補最小 frontmatter 剛好蓋掉這個契約缺口,現在改為 loud 失敗,讓違約的
    server 被看見;(2)抽出的 `name` 本身不符 Agent Skills 命名約束(見
    `is_valid_frontmatter_name`——1-64 字元、僅小寫英數字與連字號、不得開頭/結尾/連續連字號)
    ——這條不只是規格潔癖:名稱同時是這裡的目錄名,不驗證就直接落地會把非法字元(含 `/`)
    當成路徑 segment 寫入。任一條件不符,**整份 skill(含所有支援檔)跳過並記 warning**
    (訊息點名契約要求),不中止其他 skill 的 staging。

    **撞名處理**:deepagents skill 索引以 frontmatter `name` 為 key,新佈局下撞名還會撞
    staging 目錄本身(舊佈局撞名只是索引覆寫,目錄仍分開;新佈局撞名兩個 connector 會寫
    進同一個目錄,檔案互相覆蓋)——因此改為**先到先贏,後到者整份跳過並記 warning**;名稱
    全域唯一是 Agent Skills spec 的契約責任,repo 端不代為仲裁,只在偵測到違約時提醒。

    `skill_name`(mcp_adapter 正規化後的字串,即 `Connector.skills` 的 key,新佈局下已不再是
    路徑 segment,純供 log 識別用)落地前仍先以 `^[\\w-]+$` 做一次輸入健檢——不合規者只記
    警告並跳過該份 skill,不中止整個 staging。**路徑圈禁**:支援檔的相對路徑來自 server,
    逐一驗證 `(skill_dir / relative_path).resolve()` 仍在 `skill_dir` 之內——`../` 逃逸或
    絕對路徑一律跳過該檔＋warning,不中止同一份 skill 內其他檔案的 staging(比照
    `prepare_local_layout` 的 containment 前例)。

    未選任何 connector(空字典)不建立 `connectors/` 目錄、回傳 None——維持零注入原則;
    呼叫端據此決定要不要把回傳值併入 `staged_skill_paths`。
    """
    if not skills_by_connector_id:
        return None

    connectors_skills_dir = workspace.skills_dir / _CONNECTOR_SKILLS_DIRNAME
    connectors_skills_dir.mkdir(parents=True, exist_ok=True)
    staged_frontmatter_names: dict[str, str] = {}
    for connector_id, skills in skills_by_connector_id.items():
        for skill_name, skill_files in skills.items():
            if not _SKILL_NAME_PATTERN.match(skill_name):
                logger.warning(
                    "connector %s skill name %r is invalid (does not match ^[\\w-]+$), "
                    "skipping staging",
                    connector_id,
                    skill_name,
                )
                continue

            skill_markdown = skill_files.get(_SKILL_MAIN_FILE)
            frontmatter_name = (
                extract_frontmatter_name(skill_markdown) if skill_markdown is not None else None
            )
            if frontmatter_name is None:
                logger.warning(
                    "connector %s skill %s SKILL.md is missing or has malformed YAML "
                    "frontmatter (server contract requires a '---'-delimited frontmatter "
                    "block with a 'name:' field); skipping entire skill",
                    connector_id,
                    skill_name,
                )
                continue

            skill_location = f"{connector_id}/{skill_name}"
            if not is_valid_frontmatter_name(frontmatter_name):
                logger.warning(
                    "connector %s skill (%s) frontmatter name %r violates the Agent Skills "
                    "naming constraint (1-64 chars, lowercase alphanumeric and hyphens only, "
                    "no leading/trailing/consecutive hyphen) -- this name doubles as both the "
                    "staging directory and the SkillsMiddleware index key; skipping entire skill",
                    connector_id,
                    skill_name,
                    frontmatter_name,
                )
                continue

            previous_location = staged_frontmatter_names.get(frontmatter_name)
            if previous_location is not None:
                logger.warning(
                    "duplicate skill frontmatter name %r: %s already staged under this name, "
                    "skipping %s (name MUST be globally unique per the Agent Skills spec; "
                    "first-staged wins)",
                    frontmatter_name,
                    previous_location,
                    skill_location,
                )
                continue
            staged_frontmatter_names[frontmatter_name] = skill_location

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
