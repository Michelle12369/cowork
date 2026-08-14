# Dashboard Renderer Subagent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** dashboard.html 的生成從主 agent 的 write_file 迴圈改為專職 renderer subagent——subagent 的整份回覆就是 HTML，由確定性 middleware 收割寫檔，主 agent 直寫 dashboard.html 一律擋下。

**Architecture:** deepagents 0.5.5 的 `subagents=[...]` 註冊零資料工具、唯讀檔案權限的 `dashboard-renderer`（system prompt 內嵌完整 dashboard skill＋WiringManifestMiddleware 注入 qN manifest）。主 agent 新增兩顆 middleware：`DashboardDelegationGateMiddleware` 擋主 agent 直寫 dashboard.html、`DashboardRenderHarvestMiddleware` 攔 `task` 工具回傳（deepagents 的 task 回傳 `Command(update={"messages":[ToolMessage(субagent最終文字)]})`），驗證/去 fence 後由程式碼寫入 `workspace.dashboard_path`，回給主 agent 的 ToolMessage 換成短確認（避免整份 HTML 灌回主 context）。EventBridge 加 task 深度旗標，抑制 subagent 內部事件污染 ANSWER（subagent 最終訊息=HTML、無 tool_calls，不擋會變成 last_answer_text）。

**Tech Stack:** Python 3.12 / FastAPI / deepagents==0.5.5 / langchain 1.x / pytest（asyncio_mode=auto）

## Global Constraints

- 遵守 repo CLAUDE.md 與 `.claude/skills/fastapi/SKILL.md`；deepagent-service 內 `app/engine/` 禁 import langchain/deepagents（ruff TID251），本計畫改動全在 `app/agent/`、`app/api/`、`skills/`、`tests/`，不碰 engine
- 變數命名禁 1–2 字元；註解 1–2 行寫目的＋做法，NEVER spec 編號/commit hash/事故敘事
- 每個 task 結尾：`cd deepagent-service && uv run pytest tests/ -q` 全綠＋`uv run ruff check .` 過才 commit
- deepagents pin 0.5.5：`task` 工具簽名 `task(description: str, subagent_type: str)`，回傳 `Command(update={"messages": [ToolMessage(content, tool_call_id)]})`；`FilesystemOperation = Literal["read", "write"]`；SubAgent spec 省略 `"model"` 鍵＝繼承主 agent model 實例（測試靠這點共用 ScriptedChatModel）
- subagent 名稱固定 `dashboard-renderer`（單一常數 `RENDERER_SUBAGENT_NAME`，所有引用處 import，不散落字串）
- wire 事件欄位名是 Java Jackson 硬契約，不得改動既有欄位；本計畫只動 deepagent-service 側

---

### Task 1: Renderer subagent spec 模組 ＋ SKILL.md 交付段改寫

**Files:**
- Create: `deepagent-service/app/agent/renderer_subagent.py`
- Modify: `deepagent-service/skills/dashboard/SKILL.md`（約 line 20–30 的交付/步驟段）
- Test: `deepagent-service/tests/test_renderer_subagent_spec.py`（新檔）

**Interfaces:**
- Consumes: `SessionWorkspace`（`app/engine/workspace.py`，`workspace.root` 底下 `.skills/builtin/dashboard` 為 staged skill 目錄——staging 慣例見 `DashboardSkillGateMiddleware` 的 `_DASHBOARD_SKILL_RELATIVE_ROOT`）；`WiringManifestMiddleware`（`app/agent/middleware.py`，既有）
- Produces: `RENDERER_SUBAGENT_NAME: str = "dashboard-renderer"`；`build_renderer_subagent(workspace: SessionWorkspace) -> dict[str, Any]`（deepagents SubAgent spec dict，Task 3 塞進 `create_deep_agent(subagents=[...])`）

- [ ] **Step 1: 改寫 SKILL.md 交付段**

SKILL.md 現在指示「用單一 write_file 寫入 dashboard.html／小改用 edit_file」（約 line 22–28）。skill 讀者改為 renderer subagent（無寫檔權限、整份回覆即檔案內容），把該段改寫成生成形態中立的交付規則，其餘圖表/佈局/qN 綁定規則全部原樣保留：

```markdown
3. Produce the **complete** dashboard.html document in one piece -- your final reply IS the
   file content, saved verbatim by the harness. Start at `<!DOCTYPE html>`, end at `</html>`,
   no markdown fences, no commentary before or after.
4. When modifying an existing dashboard, read the current dashboard.html first
   (read_file, limit=1000), keep everything the request didn't ask to change, and output the
   full updated document -- never a fragment or a diff.
```

（實作時以現檔內容為準對齊條號與上下文；只改「怎麼交付」的句子，凡提及 write_file/edit_file 的交付指令都要消失。）

- [ ] **Step 2: 寫 failing test**

```python
"""tests/test_renderer_subagent_spec.py"""
from pathlib import Path

from app.agent.renderer_subagent import RENDERER_SUBAGENT_NAME, build_renderer_subagent
from app.engine.workspace import SessionWorkspace


def _workspace_with_skill(tmp_path: Path) -> SessionWorkspace:
    workspace = SessionWorkspace(root=tmp_path)
    skill_dir = tmp_path / ".skills" / "builtin" / "dashboard"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("CHART RULES SENTINEL", encoding="utf-8")
    return workspace


def test_build_renderer_subagent_embedsSkillContent_inSystemPrompt(tmp_path: Path) -> None:
    spec = build_renderer_subagent(_workspace_with_skill(tmp_path))
    assert spec["name"] == RENDERER_SUBAGENT_NAME
    assert "CHART RULES SENTINEL" in spec["system_prompt"]
    assert "<!DOCTYPE html>" in spec["system_prompt"]


def test_build_renderer_subagent_deniesWrites_allowsReads(tmp_path: Path) -> None:
    spec = build_renderer_subagent(_workspace_with_skill(tmp_path))
    permissions = spec["permissions"]
    assert permissions[0].mode == "deny" and permissions[0].operations == ["write"]
    assert permissions[1].mode == "allow" and permissions[1].operations == ["read"]


def test_build_renderer_subagent_noDataTools_inheritsMainModel(tmp_path: Path) -> None:
    spec = build_renderer_subagent(_workspace_with_skill(tmp_path))
    assert spec["tools"] == []
    assert "model" not in spec  # 省略 model 鍵＝繼承主 agent model（測試共用 ScriptedChatModel 靠這點）


def test_build_renderer_subagent_missingSkillDir_failsOpenWithContract(tmp_path: Path) -> None:
    workspace = SessionWorkspace(root=tmp_path)  # 無 .skills 目錄
    spec = build_renderer_subagent(workspace)
    assert "<!DOCTYPE html>" in spec["system_prompt"]  # 契約段仍在,skill 缺席不炸
```

註：`SessionWorkspace` 的實際建構方式以 `app/engine/workspace.py` 為準——若非 `SessionWorkspace(root=...)` 而是其他 factory，照 `tests/test_middleware.py` 既有 workspace fixture 的寫法改 `_workspace_with_skill`，測試斷言不變。

- [ ] **Step 3: 跑測試確認 fail**

Run: `cd deepagent-service && uv run pytest tests/test_renderer_subagent_spec.py -q`
Expected: FAIL（ModuleNotFoundError: app.agent.renderer_subagent）

- [ ] **Step 4: 實作 renderer_subagent.py**

```python
"""dashboard-renderer subagent spec——零資料工具、唯讀檔案權限,整份回覆即 dashboard.html 內容,
由 DashboardRenderHarvestMiddleware 確定性收割寫檔。skill 內容在 build 時整份嵌進 system
prompt(單發生成不走 read-skill-gate round trip);qN manifest 由 WiringManifestMiddleware
每次 model call 注入。"""

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
        # 樣式必須是 "/**"——deepagents 用 wcmatch globmatch,裸 "/" 只匹配字面根路徑,
        # 匹配不到 /dashboard.html 等實際目標,deny 會形同虛設。
        "permissions": [
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
            FilesystemPermission(operations=["read"], paths=["/**"], mode="allow"),
        ],
    }
```

- [ ] **Step 5: 跑測試確認 pass**

Run: `cd deepagent-service && uv run pytest tests/test_renderer_subagent_spec.py -q`
Expected: PASS（4 tests）。若 `FilesystemPermission` 欄位/import 路徑與 0.5.5 實際不符（以 `.venv/lib/python3.12/site-packages/deepagents/middleware/filesystem.py:76` 為準），修實作不改斷言語意。

- [ ] **Step 6: 全套測試＋ruff＋commit**

Run: `cd deepagent-service && uv run pytest tests/ -q && uv run ruff check .`
Expected: 全綠（SKILL.md 改寫不影響既有測試——若 `test_dashboard_skill.py` 斷言到被改寫的交付句，同步更新該斷言為新句子）

```bash
git add deepagent-service/app/agent/renderer_subagent.py deepagent-service/tests/test_renderer_subagent_spec.py deepagent-service/skills/dashboard/SKILL.md
git commit -m "feat(deepagent): dashboard-renderer subagent spec——整份回覆即 HTML,skill 內嵌+唯讀權限"
```

---

### Task 2: Harvest 與 Delegation gate middleware；移除兩顆舊 gate

**Files:**
- Modify: `deepagent-service/app/agent/middleware.py`（新增兩類別；刪除 `DashboardWriteFileOnlyMiddleware`、`DashboardSkillGateMiddleware` 與 `_DASHBOARD_SKILL_RELATIVE_ROOT`/`_GATED_TOOL_NAMES` 常數——`_normalized_workspace_path`、`_GATED_FILE_NAME` 保留給新 gate 用）
- Modify: `deepagent-service/tests/test_middleware.py`（刪除兩舊類別的測試——`test_dashboard_write_is_blocked_before_skill_is_read` 等 skill-gate 系列與 `test_edit_file_on_dashboard_*` 系列；新增下方測試）
- Modify: `deepagent-service/app/agent/graph.py`（僅刪 import 與被註解的 `# DashboardWriteFileOnlyMiddleware()` 行、`DashboardSkillGateMiddleware(workspace)` 行——正式接線在 Task 3，本 task 保持 build_agent 可運作）

**Interfaces:**
- Consumes: `RENDERER_SUBAGENT_NAME`（Task 1）；`SessionWorkspace.dashboard_path`（chat_turn.py 既用）；deepagents `Command`（`langgraph.types`）
- Produces: `DashboardDelegationGateMiddleware()`（無參建構）；`DashboardRenderHarvestMiddleware(workspace: SessionWorkspace)`；`HARVEST_CONFIRMATION_PREFIX = "dashboard.html updated"`（Task 6 e2e 斷言用）

- [ ] **Step 1: 寫 failing tests（追加到 test_middleware.py）**

```python
import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agent.middleware import (
    HARVEST_CONFIRMATION_PREFIX,
    DashboardDelegationGateMiddleware,
    DashboardRenderHarvestMiddleware,
)
from app.agent.renderer_subagent import RENDERER_SUBAGENT_NAME

FULL_HTML = "<!DOCTYPE html>\n<html><body><h1>Revenue</h1></body></html>"


def _tool_request(tool_name: str, args: dict, tool_call_id: str = "call_1"):
    """對齊本檔既有測試的 ToolCallRequest 構造慣例(直接沿用既有 helper 或同款 stub)。"""
    ...  # 依 test_middleware.py 既有寫法——skill-gate 系列刪除前先抄它的 request 構造


async def _passthrough_handler(request):
    raise AssertionError("handler should not be reached when blocked")


async def test_delegation_gate_blocksWriteFile_onDashboard(tmp_path) -> None:
    middleware = DashboardDelegationGateMiddleware()
    request = _tool_request("write_file", {"file_path": "dashboard.html", "content": "<p>x</p>"})
    result = await middleware.awrap_tool_call(request, _passthrough_handler)
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert RENDERER_SUBAGENT_NAME in str(result.content)


async def test_delegation_gate_blocksEditFile_onDashboard(tmp_path) -> None:
    middleware = DashboardDelegationGateMiddleware()
    request = _tool_request("edit_file", {"file_path": "/dashboard.html"})
    result = await middleware.awrap_tool_call(request, _passthrough_handler)
    assert isinstance(result, ToolMessage) and result.status == "error"


async def test_delegation_gate_allowsNotesWrites(tmp_path) -> None:
    middleware = DashboardDelegationGateMiddleware()
    request = _tool_request("write_file", {"file_path": "notes.md", "content": "findings"})
    sentinel = ToolMessage(content="ok", tool_call_id="call_1")

    async def handler(_request):
        return sentinel

    assert await middleware.awrap_tool_call(request, handler) is sentinel


async def test_harvest_writesHtml_andReplacesToolMessage(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)  # 沿用本檔既有 workspace fixture 寫法
    middleware = DashboardRenderHarvestMiddleware(workspace)
    request = _tool_request(
        "task", {"description": "build revenue dashboard", "subagent_type": RENDERER_SUBAGENT_NAME}
    )

    async def handler(_request):
        return Command(update={"messages": [ToolMessage(FULL_HTML, tool_call_id="call_1")]})

    result = await middleware.awrap_tool_call(request, handler)
    assert workspace.dashboard_path.read_text(encoding="utf-8") == FULL_HTML
    harvested = result.update["messages"][0]
    assert str(harvested.content).startswith(HARVEST_CONFIRMATION_PREFIX)
    assert "<html" not in str(harvested.content)


async def test_harvest_stripsMarkdownFences(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    middleware = DashboardRenderHarvestMiddleware(workspace)
    request = _tool_request("task", {"subagent_type": RENDERER_SUBAGENT_NAME})
    fenced = f"```html\n{FULL_HTML}\n```"

    async def handler(_request):
        return Command(update={"messages": [ToolMessage(fenced, tool_call_id="call_1")]})

    await middleware.awrap_tool_call(request, handler)
    assert workspace.dashboard_path.read_text(encoding="utf-8") == FULL_HTML


async def test_harvest_rejectsNonHtml_withErrorToolMessage(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    middleware = DashboardRenderHarvestMiddleware(workspace)
    request = _tool_request("task", {"subagent_type": RENDERER_SUBAGENT_NAME})

    async def handler(_request):
        return Command(update={"messages": [ToolMessage("抱歉我需要更多資訊", tool_call_id="call_1")]})

    result = await middleware.awrap_tool_call(request, handler)
    assert not workspace.dashboard_path.exists()
    error_message = result.update["messages"][0]
    assert error_message.status == "error"


async def test_harvest_ignoresOtherSubagents(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    middleware = DashboardRenderHarvestMiddleware(workspace)
    request = _tool_request("task", {"subagent_type": "general-purpose"})
    passthrough = Command(update={"messages": [ToolMessage("done", tool_call_id="call_1")]})

    async def handler(_request):
        return passthrough

    assert await middleware.awrap_tool_call(request, handler) is passthrough
```

（`_tool_request`/`_make_workspace` 不是佔位符：實作時直接沿用 test_middleware.py 既有 skill-gate 測試的同名構造程式碼——刪除舊測試前先把該構造抄出來共用。）

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd deepagent-service && uv run pytest tests/test_middleware.py -q`
Expected: FAIL（ImportError: DashboardDelegationGateMiddleware）

- [ ] **Step 3: 實作兩顆 middleware＋刪兩顆舊 gate**

```python
HARVEST_CONFIRMATION_PREFIX = "dashboard.html updated"


class DashboardDelegationGateMiddleware(AgentMiddleware):
    """主 agent 直寫 dashboard.html 一律擋下(write_file 與 edit_file 都擋)——dashboard 只由
    dashboard-renderer subagent 生成、由 harvest middleware 寫檔。notes.md 等其他檔不受限。"""

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        tool_call = request.tool_call
        if tool_call.get("name") in ("write_file", "edit_file"):
            file_path = _normalized_workspace_path(
                str(tool_call.get("args", {}).get("file_path", ""))
            )
            if file_path == _GATED_FILE_NAME:
                return ToolMessage(
                    content=(
                        "Blocked: dashboard.html is generated by the dashboard-renderer "
                        "subagent, never written directly. First record the conclusions to "
                        "visualize in notes.md, then call the task tool with "
                        f"subagent_type='{RENDERER_SUBAGENT_NAME}' and a one-two sentence "
                        "description of what to build or change."
                    ),
                    tool_call_id=tool_call["id"],
                    status="error",
                )
        return await handler(request)


def _strip_html_fences(reply_text: str) -> str:
    """renderer 偶爾違反契約包 ```html fence——剝掉首尾 fence 行,其餘內容原樣。"""
    stripped = reply_text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines[1:]).strip()


def _looks_like_full_html(candidate: str) -> bool:
    lowered = candidate.lower()
    return lowered.startswith("<!doctype") or lowered.startswith("<html")


class DashboardRenderHarvestMiddleware(AgentMiddleware):
    """攔 dashboard-renderer 的 task 回傳:整份回覆驗證為完整 HTML 後由這裡(程式碼,非模型)
    寫入 dashboard.html,回給主 agent 的 ToolMessage 換成短確認——整份 HTML 不回灌主 context。
    非 HTML 回覆改成 error ToolMessage 讓主 agent 重試,不寫檔。"""

    def __init__(self, workspace: SessionWorkspace) -> None:
        super().__init__()
        self._workspace = workspace

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        tool_call = request.tool_call
        is_renderer_task = tool_call.get("name") == "task" and tool_call.get("args", {}).get(
            "subagent_type"
        ) == RENDERER_SUBAGENT_NAME
        result = await handler(request)
        if not is_renderer_task:
            return result
        reply_message = self._extract_tool_message(result)
        if reply_message is None:
            return result
        html_candidate = _strip_html_fences(str(reply_message.content))
        if not _looks_like_full_html(html_candidate):
            return self._replace_message(
                result,
                reply_message,
                content=(
                    "Renderer reply was not a complete HTML document (must start at "
                    "<!DOCTYPE html>). Nothing was written. Add the missing context to "
                    "notes.md if needed, then call task again with "
                    f"subagent_type='{RENDERER_SUBAGENT_NAME}'."
                ),
                status="error",
            )
        self._workspace.dashboard_path.write_text(html_candidate, encoding="utf-8")
        confirmation = (
            f"{HARVEST_CONFIRMATION_PREFIX} ({len(html_candidate)} chars). Do NOT paste HTML "
            "in your reply; give the user a short Traditional-Chinese summary of what the "
            "dashboard now shows."
        )
        return self._replace_message(result, reply_message, content=confirmation, status="success")

    def _extract_tool_message(self, result: ToolMessage | Command) -> ToolMessage | None:
        if isinstance(result, ToolMessage):
            return result
        if isinstance(result, Command):
            messages = (result.update or {}).get("messages") or []
            for message in messages:
                if isinstance(message, ToolMessage):
                    return message
        return None

    def _replace_message(
        self,
        result: ToolMessage | Command,
        original: ToolMessage,
        *,
        content: str,
        status: str,
    ) -> ToolMessage | Command:
        replacement = ToolMessage(
            content=content, tool_call_id=original.tool_call_id, status=status
        )
        if isinstance(result, ToolMessage):
            return replacement
        replaced_messages = [
            replacement if message is original else message
            for message in (result.update or {}).get("messages", [])
        ]
        return Command(update={**result.update, "messages": replaced_messages})
```

新 import：`from langgraph.types import Command`（既有）、`from app.agent.renderer_subagent import RENDERER_SUBAGENT_NAME`。注意循環 import：renderer_subagent.py import 了 middleware 的 `WiringManifestMiddleware`——middleware.py 反向 import RENDERER_SUBAGENT_NAME 會成環。解法：`RENDERER_SUBAGENT_NAME` 常數移到無依賴小模組不必要——直接在 middleware.py 定義 `RENDERER_SUBAGENT_NAME = "dashboard-renderer"` 並讓 renderer_subagent.py 從 middleware import（單向：renderer_subagent → middleware）。Task 1 的測試 import 路徑（`from app.agent.renderer_subagent import RENDERER_SUBAGENT_NAME`）維持有效——renderer_subagent.py re-export 即可。

- [ ] **Step 4: 跑測試確認 pass；刪除舊 gate 的測試與 graph.py 殘留引用**

Run: `cd deepagent-service && uv run pytest tests/test_middleware.py -q`
Expected: 新測試 PASS；舊 skill-gate/write-only 測試已刪；`uv run pytest tests/ -q` 若 test_graph.py 等處引用了被刪類別，同步移除那些引用（graph.py 的 middleware 清單暫時剩 `[SerializedToolCallsMiddleware(), WiringManifestMiddleware(workspace)]`，Task 3 補齊新接線）

- [ ] **Step 5: 全套測試＋ruff＋commit**

```bash
git add -A deepagent-service
git commit -m "feat(deepagent): delegation gate+harvest middleware——dashboard 寫入改走 renderer 收割,移除 skill/write-only 兩舊 gate"
```

---

### Task 3: Runtime 與 graph 接線

**Files:**
- Modify: `deepagent-service/app/agent/runtime/base.py`（`build_agent` 協定加 `subagents` 參數）
- Modify: `deepagent-service/app/agent/runtime/deepagents_runtime.py`（透傳 `subagents=` 給 `create_deep_agent`）
- Modify: `deepagent-service/app/agent/graph.py`（wire renderer spec＋新 middleware 清單）
- Test: `deepagent-service/tests/test_graph.py`、`deepagent-service/tests/test_runtime.py`（更新既有斷言）

**Interfaces:**
- Consumes: `build_renderer_subagent(workspace)`（Task 1）；`DashboardDelegationGateMiddleware`/`DashboardRenderHarvestMiddleware`（Task 2）
- Produces: `build_agent(...)` 組出的 graph 具備 `task` 工具與 `dashboard-renderer` subagent（Task 6 e2e 依賴）

- [ ] **Step 1: base.py 協定加參數**

```python
def build_agent(
    self,
    *,
    model: BaseChatModel,
    tools: list[Any],
    system_prompt: str,
    backend: FilesystemBackend,
    skills: list[str],
    checkpointer: BaseCheckpointSaver,
    middleware: list[Any],
    subagents: list[dict[str, Any]],
) -> CompiledStateGraph: ...
```

- [ ] **Step 2: deepagents_runtime.py 透傳**

`build_agent` 簽名同步加 `subagents: list[dict[str, Any]]`，`create_deep_agent(...)` 呼叫加 `subagents=subagents`。

- [ ] **Step 3: graph.py 接線**

```python
middleware=[
    SerializedToolCallsMiddleware(),
    WiringManifestMiddleware(workspace),
    DashboardDelegationGateMiddleware(),
    DashboardRenderHarvestMiddleware(workspace),
],
subagents=[build_renderer_subagent(workspace)],
```

middleware 清單上方的說明註解同步改寫（一句講 serialized＋manifest 不變、一句講「dashboard.html 只由 renderer subagent 生成、harvest 收割寫檔、主 agent 直寫被 gate 擋」）。`register_harness_profile`（GP subagent disabled）維持不動——named subagent 與 GP 開關互不影響。

- [ ] **Step 4: 更新 test_graph.py / test_runtime.py**

test_runtime.py 若以假 runtime 實作協定，加上 `subagents` 參數；test_graph.py 加一條組裝斷言：

```python
def test_build_agent_registersRendererSubagent(tmp_path) -> None:
    agent = _build_test_agent(tmp_path)  # 沿用本檔既有 build_agent 測試的構造慣例
    tool_names = {tool.name for tool in _collect_tools(agent)}  # 既有測試若有工具收集 helper 沿用
    assert "task" in tool_names
```

若既有 test_graph.py 沒有可沿用的工具收集 helper，改為最小斷言「`build_agent(...)` 正常回傳 CompiledStateGraph 不拋例外」＋Task 6 的 e2e 實際行為驗證補足。

- [ ] **Step 5: 全套測試＋ruff＋commit**

```bash
git add -A deepagent-service
git commit -m "feat(deepagent): graph 接線 renderer subagent——runtime 協定加 subagents 透傳"
```

（記帳注意：internal 環境若有 out-of-tree runtime 實作 `AgentRuntime` 協定，`subagents` 參數是 breaking change——完成後在 ledger 記一筆提醒。）

---

### Task 4: EventBridge——task 步驟標題與 subagent 事件抑制

**Files:**
- Modify: `deepagent-service/app/agent/events.py`
- Test: `deepagent-service/tests/test_events.py`（追加）

**Interfaces:**
- Consumes: 無（獨立於 Task 1–3）
- Produces: `step_title_for("task", ...) == "製作 dashboard"`；EventBridge 在 task 執行期間抑制內部 chat_model/tool 事件（Task 6 e2e 斷言 ANSWER 不含 HTML 依賴此行為）

- [ ] **Step 1: 寫 failing tests（追加到 test_events.py，沿用本檔既有事件 dict 構造慣例）**

```python
def test_step_title_for_task_isDashboardTitle() -> None:
    assert step_title_for("task", {"subagent_type": "dashboard-renderer"}) == "製作 dashboard"


def test_bridge_suppressesSubagentModelEvents_duringTask() -> None:
    bridge = EventBridge(ToolResultRecorder())
    bridge.handle({"event": "on_tool_start", "name": "task", "run_id": "r1", "data": {"input": {}}})
    # subagent 內部模型事件:最終訊息=整份 HTML、無 tool_calls——不得成為 last_answer_text
    bridge.handle(
        {
            "event": "on_chat_model_end",
            "name": "ChatOpenAI",
            "run_id": "r2",
            "data": {"output": AIMessage(content="<!DOCTYPE html><html></html>")},
        }
    )
    bridge.handle({"event": "on_tool_end", "name": "task", "run_id": "r1", "data": {}})
    bridge.handle(
        {
            "event": "on_chat_model_end",
            "name": "ChatOpenAI",
            "run_id": "r3",
            "data": {"output": AIMessage(content="儀表板完成")},
        }
    )
    assert bridge.final_answer() == "儀表板完成"


def test_bridge_suppressesInnerToolSteps_duringTask() -> None:
    bridge = EventBridge(ToolResultRecorder())
    bridge.handle({"event": "on_tool_start", "name": "task", "run_id": "r1", "data": {"input": {}}})
    inner_events = bridge.handle(
        {"event": "on_tool_start", "name": "read_file", "run_id": "r2", "data": {"input": {}}}
    )
    assert inner_events == []
    bridge.handle({"event": "on_tool_end", "name": "task", "run_id": "r1", "data": {}})
    after_events = bridge.handle(
        {"event": "on_tool_start", "name": "read_file", "run_id": "r3", "data": {"input": {}}}
    )
    assert len(after_events) == 1
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd deepagent-service && uv run pytest tests/test_events.py -q`
Expected: FAIL（title 落到 "處理中"；HTML 污染 final_answer）

- [ ] **Step 3: 實作**

`step_title_for` 加分支（放 `write_todos` 分支後）：

```python
if tool_name == "task":
    return "製作 dashboard"
```

`EventBridge.__init__` 加 `self._active_task_depth = 0`。`handle` 分派前置處理：

```python
if event_type == "on_tool_start" and agent_event.get("name") == "task":
    self._active_task_depth += 1
elif event_type in ("on_tool_end", "on_tool_error") and agent_event.get("name") == "task":
    self._active_task_depth = max(0, self._active_task_depth - 1)
elif self._active_task_depth > 0:
    # task 執行期間的內部事件(subagent 的模型/工具活動)不上 wire、不碰 answer/recorder 狀態
    # ——renderer 最終訊息是整份 HTML,流進 _handle_chat_model_end 會污染 ANSWER。
    return []
```

（放在既有 event_type 分派之前；task 自身的 start/end 照舊走 `_handle_tool_start`/`_handle_tool_end` 產生 STEP。）

- [ ] **Step 4: 跑測試確認 pass＋全套＋ruff＋commit**

```bash
git add deepagent-service/app/agent/events.py deepagent-service/tests/test_events.py
git commit -m "feat(deepagent): EventBridge 抑制 task 期間 subagent 內部事件——ANSWER 不被 renderer HTML 污染"
```

---

### Task 5: 主 agent prompt 改委派

**Files:**
- Modify: `deepagent-service/app/agent/prompts.py`（`SYSTEM_PROMPT` 的 File edits 與 visual evidence 兩個 bullet；`PREVIOUS_VERSION_SYSTEM_NOTE`）
- Test: `deepagent-service/tests/test_prompts.py`（更新既有斷言）

**Interfaces:**
- Consumes: 名稱字面值 `dashboard-renderer`（與 `RENDERER_SUBAGENT_NAME` 同值；prompts.py 保持純文字模組，不 import agent 模組）
- Produces: 無下游依賴

- [ ] **Step 1: 改寫 SYSTEM_PROMPT 兩個 bullet**

File edits bullet 改為：

```text
- Dashboard delivery: dashboard.html is produced by the dashboard-renderer subagent, NEVER \
written by you -- write_file/edit_file on dashboard.html are blocked. To create or change \
the dashboard: (1) record the conclusions to visualize in notes.md (write_file or \
edit_file), (2) call the task tool with subagent_type='dashboard-renderer' and a one-two \
sentence description of what to build or change. The renderer reads notes.md and the \
current dashboard.html by itself -- do NOT paste query data or HTML into the description. \
(edit_file still works for other files such as notes.md.)
```

visual evidence bullet（原「When a conclusion needs visual evidence...」）改為：

```text
- When a conclusion needs visual evidence, delegate to the dashboard-renderer subagent as \
described above. NEVER paste dashboard HTML (or a ```html block) into your reply text; \
your reply is a short Traditional-Chinese explanation only, never the page markup.
```

`PREVIOUS_VERSION_SYSTEM_NOTE` 改為：

```python
PREVIOUS_VERSION_SYSTEM_NOTE = (
    "\n\n(System note: the user has selected a historical dashboard version as the editing "
    "base for this turn. dashboard.html already contains that version's content. To modify "
    "it, record what should change in notes.md if helpful, then call the task tool with "
    "subagent_type='dashboard-renderer' -- the renderer reads the current dashboard.html "
    "itself.)"
)
```

- [ ] **Step 2: 更新 test_prompts.py 既有斷言（write_file/edit_file 相關句已變）＋加一條**

```python
def test_system_prompt_delegatesDashboard_toRendererSubagent() -> None:
    assert "dashboard-renderer" in SYSTEM_PROMPT
    assert "write_file/edit_file on dashboard.html are blocked" in SYSTEM_PROMPT
```

- [ ] **Step 3: 全套測試＋ruff＋commit**

```bash
git add deepagent-service/app/agent/prompts.py deepagent-service/tests/test_prompts.py
git commit -m "feat(deepagent): 主 agent prompt 改 dashboard 委派——notes.md 先行,task 呼叫 renderer"
```

---

### Task 6: e2e 整合測試＋全套驗收

**Files:**
- Create: `deepagent-service/tests/test_renderer_subagent_e2e.py`

**Interfaces:**
- Consumes: Task 1–5 全部；`ScriptedChatModel`（`tests/fake_model.py`——subagent spec 無 `model` 鍵故繼承同一顆 scripted model，腳本序列涵蓋主 agent＋renderer 的呼叫）；`build_agent`（`app/agent/graph.py`）；workspace/duckdb 構造沿用 `tests/test_graph.py` 既有 e2e 慣例
- Produces: 無

- [ ] **Step 1: 寫 e2e 測試**

```python
"""renderer subagent 端到端:主 agent 委派 task → renderer(共用 scripted model)回整份 HTML →
harvest 寫檔＋短確認 → 主 agent 收尾中文回覆。斷言三件事:檔案內容、確認訊息不含 HTML、
最終 answer 是中文短句(HTML 不污染)。"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.graph import build_agent, ...  # workspace/connection 構造沿用 test_graph.py 既有 e2e 寫法
from app.agent.middleware import HARVEST_CONFIRMATION_PREFIX
from tests.fake_model import ScriptedChatModel

FULL_HTML = "<!DOCTYPE html>\n<html><body><div id='chart'></div></body></html>"


async def test_dashboard_generation_flowsThroughRendererSubagent(tmp_path) -> None:
    scripted_model = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "建立營收儀表板",
                            "subagent_type": "dashboard-renderer",
                        },
                        "id": "call_task_1",
                    }
                ],
            ),
            AIMessage(content=FULL_HTML),  # renderer 的單發生成(繼承同一顆 scripted model)
            AIMessage(content="儀表板已完成,請查看右側預覽。"),
        ]
    )
    workspace, connection, agent = _build_e2e_agent(tmp_path, scripted_model)  # 沿用 test_graph.py 構造
    result = await agent.ainvoke(
        {"messages": [HumanMessage("幫我做營收儀表板")]},
        config={"configurable": {"thread_id": "e2e-renderer-1"}, "recursion_limit": 30},
    )

    assert workspace.dashboard_path.read_text(encoding="utf-8") == FULL_HTML

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    harvest_confirmations = [
        m for m in tool_messages if str(m.content).startswith(HARVEST_CONFIRMATION_PREFIX)
    ]
    assert len(harvest_confirmations) == 1
    assert "<html" not in str(harvest_confirmations[0].content)

    final_message = result["messages"][-1]
    assert isinstance(final_message, AIMessage)
    assert "儀表板已完成" in str(final_message.content)
    assert "<html" not in str(final_message.content)
```

（`_build_e2e_agent` 不是佔位符：test_graph.py 既有 e2e 測試已有 workspace＋duckdb connection＋`build_agent` 的完整構造，抄該構造抽成本檔 helper；若 test_graph.py 無現成 e2e，最小構造＝`SessionWorkspace(tmp_path)` staging 一份含 SKILL.md 的 `.skills/builtin/dashboard`、`duckdb.connect()` 空連線、`ToolResultRecorder()`。）

- [ ] **Step 2: 跑 e2e 確認 pass**

Run: `cd deepagent-service && uv run pytest tests/test_renderer_subagent_e2e.py -q`
Expected: PASS。若 renderer subagent 先發出 read_file（scripted 腳本被 read 消耗），依實際呼叫序在腳本插入對應訊息或在 workspace 預放 notes.md——斷言不變。

- [ ] **Step 3: 全套驗收＋commit**

Run: `cd deepagent-service && uv run pytest tests/ -q && uv run ruff check .`
Expected: 全綠、零 lint

```bash
git add deepagent-service/tests/test_renderer_subagent_e2e.py
git commit -m "test(deepagent): renderer subagent e2e——委派/收割/ANSWER 隔離三斷言"
```

---

## 驗收與收尾（主迴圈執行,不在 task 內）

- `uv run pytest tests/ -q` 全綠＋`ruff check` 過（各 task 已各自把關,此處終驗）
- ledger `.superpowers/sdd/progress.md` 記帳:renderer subagent 上線、internal runtime 若有 out-of-tree 實作需補 `subagents` 參數、SKILL.md 交付段已改為 renderer 契約
- 後續(不在本計畫):同批題目 A/B 對照(全包 vs 委派)比生成耗時/修復觸發率/成品錯誤數;Java 側無改動(wire 契約未動)
