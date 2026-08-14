"""dashboard-renderer subagent spec——零資料工具、唯讀檔案權限,整份回覆即 dashboard.html 內容,
由後續收割 middleware 確定性寫檔;skill 內容在 build 時整份嵌進 system prompt。"""

from typing import Any

from deepagents.middleware.filesystem import FilesystemPermission

from app.agent.middleware import WiringManifestMiddleware
from app.engine.workspace import SessionWorkspace

RENDERER_SUBAGENT_NAME = "dashboard-renderer"

_SKILL_RELATIVE_ROOT = ".skills/builtin/dashboard"

RENDERER_SUBAGENT_DESCRIPTION = (
    "Generates or updates the complete dashboard.html. Call it AFTER recording the analysis "
    "conclusions to visualize in notes.md. description = what to build or change this round, "
    "in one or two sentences. It reads notes.md and the current dashboard.html by itself -- "
    "do NOT paste query data or HTML into the description."
)

_RENDERER_CONTRACT_PROMPT = """\
You are a dashboard renderer. Your ONLY job is to produce the complete content of \
dashboard.html.

Inputs:
- The task description tells you what to build or change this round.
- Read notes.md first (read_file with limit=1000) for the analysis conclusions to visualize. \
If dashboard.html already exists, read it too (read_file with limit=1000) and treat this \
round as a modification: keep everything the request didn't ask to change.
- The wiring manifest appended to this prompt lists the available query results (qN ids, \
intents, columns). Bind charts to window.__ERD_RESULTS__ with those ids exactly; never \
invent ids.

Output contract (hard rules):
- Your final reply MUST be the complete dashboard.html document and NOTHING else: no \
markdown fences, no commentary before or after, no partial fragments. Start at \
`<!DOCTYPE html>` and end at `</html>`.
- You cannot write files; the harness saves your reply verbatim as dashboard.html.

Dashboard rules follow.
"""


def _load_skill_text(workspace: SessionWorkspace) -> str:
    """rglob 對齊 skill staging 慣例:整個資料夾底下所有 .md 依路徑排序串接;缺席時回空字串
    fail-open(renderer 仍有契約段可運作,品質靠 skill 但不因 staging 失敗整輪炸掉)。"""
    skill_root = workspace.root / _SKILL_RELATIVE_ROOT
    if not skill_root.is_dir():
        return ""
    sections = [
        markdown_path.read_text(encoding="utf-8")
        for markdown_path in sorted(skill_root.rglob("*.md"))
    ]
    return "\n\n".join(sections)


def build_renderer_subagent(workspace: SessionWorkspace) -> dict[str, Any]:
    skill_text = _load_skill_text(workspace)
    system_prompt = (
        f"{_RENDERER_CONTRACT_PROMPT}\n\n{skill_text}" if skill_text else _RENDERER_CONTRACT_PROMPT
    )
    return {
        "name": RENDERER_SUBAGENT_NAME,
        "description": RENDERER_SUBAGENT_DESCRIPTION,
        "system_prompt": system_prompt,
        "tools": [],
        "middleware": [WiringManifestMiddleware(workspace)],
        # 順序即優先序,first match wins:先 deny 全部 write,再 allow 全部 read。
        # paths 用 "/**"(非裸 "/")-- wcglob.globmatch 的裸 "/" 只匹配根目錄字面值本身,
        # 任何實際檔案路徑(如 "/dashboard.html")都不命中,deny 規則會靜默落空變成預設 allow。
        "permissions": [
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
            FilesystemPermission(operations=["read"], paths=["/**"], mode="allow"),
        ],
    }
