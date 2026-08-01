# deepagent-service trace findings 修復 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉 `docs/deepagent-trace-findings-2026-08-01.md` 記載的六個缺陷——並行 edit_file 靜默覆蓋、getCol 綁錯欄位無聲、qN 別名憑記憶對應、修復迴圈無法收斂、未讀 skill 就寫檔、兩項缺失的靜態檢查。

**Architecture:** 三個施力點。(1) **LangChain `AgentMiddleware`**——新增 `app/agent/middleware.py`，放三個中介層：工具呼叫序列化（消除 lost update 整類問題）、dashboard skill 已讀 gate、wiring manifest 注入。全部經 `create_deep_agent(middleware=[...])` 掛上，只影響主 agent（deepagents 把子代理的 middleware 由 subagent spec 各自帶，不繼承主 agent 的自訂 middleware——已驗證，故 `task` 工具不會自我死鎖）。(2) **`app/engine/html_guard.py`**——sandbox 多一個 `console.warn` 收集器把既有但被丟棄的 `[ERD] column not found` 訊號轉成 guard error；錯誤訊息從拋出點改報呼叫點並列出共用 helper 的全部呼叫點；補兩條靜態檢查。(3) **`app/main.py`**——guard 修復迴圈改成「錯誤數持續下降就繼續、停滯或退步就停」。

**Tech Stack:** Python 3.11+ / FastAPI / **deepagents 0.5.5**（公司 registry 版本，開發＝生產同一版）/ langchain 1.3.14 / langgraph 1.2.10 / quickjs 1.19.4 / pytest（`asyncio_mode = "auto"`）/ ruff。

## Global Constraints

- 分支 `fix/deepagent-trace-findings`（已建立，自 master `99e8e3a`）。完成走 PR，**NEVER 自己 merge**。
- **依賴一律用公司內部版本，只有一份 `requirements.txt`。** `deepagents` 在 `pyproject.toml` 釘死 `==0.5.5`（公司 registry 僅有 0.5.x），`requirements-company.txt` 已刪除。動到依賴時 MUST 重跑 `uv lock && uv sync && uv export --no-dev --no-hashes --format requirements-txt -o requirements.txt`，NEVER 再開第二份 requirements 變體。
- 每批一個 commit（批次 1 必須是獨立且最先的 commit）。
- 每批 done criteria：`cd deepagent-service && uv run pytest -q` 全綠、`uv run ruff check .` 淨、`git status` 確認 Java（`backend/`）與前端（`frontend/`）零改動。
- 回歸測試 MUST 先確認會紅（在缺陷還在時跑會失敗），再修到綠。
- `app/engine/**` NEVER import `langchain*` / `langgraph` / `langfuse` / `deepagents`（ruff TID251 會擋）。中介層一律放 `app/agent/`。
- 跑 guard 對真實 workspace 檔案驗證時 MUST 用 `docker exec ... /app/.venv/bin/python`；容器的 `/usr/local/bin/python` 沒有 quickjs，會靜默跳過 Level 1/2 並回傳假的 `ok=True`。
- 變數/參數 NEVER 用 1–2 字元名稱；一律描述性單詞。註解 1–2 行寫「目的＋做法」，NEVER 寫 spec 編號/commit hash/事故敘事。
- 已排除的死路，不要重試：`max_concurrency=1` 對非同步路徑無效；`create_deep_agent` 沒有 tool-node 參數（不要為換 ToolNode 自組 StateGraph）；換 backend 不救（`StateBackend.edit` 同樣讀改寫）。

## 檔案結構

| 檔案 | 責任 | 批次 |
|---|---|---|
| `app/agent/middleware.py`（新增） | 三個 `AgentMiddleware`：`SerializedToolCallsMiddleware`、`WiringManifestMiddleware`、`DashboardSkillGateMiddleware` | 1、2、3 |
| `app/agent/graph.py` | `build_agent` 組裝 middleware list | 1、2、3 |
| `app/engine/html_guard.py` | sandbox warn 收集器、呼叫點錯誤訊息、資料綁定檢查、tab resize 檢查 | 2、3、4 |
| `app/engine/results.py` | `format_wiring_manifest()`（純函式，供 middleware 呼叫） | 2 |
| `app/main.py` | guard 修復迴圈的收斂策略 | 4 |
| `tests/test_middleware.py`（新增） | 三個 middleware 的單元測試（含真正併發） | 1、2、3 |
| `tests/test_chat.py` | 端到端：併發 edit_file 兩個改動都在、skill gate 擋寫、修復迴圈收斂 | 1、3、4 |
| `tests/test_html_guard.py` | guard 新檢查與新訊息格式 | 2、3、4 |
| `tests/test_results.py` | `format_wiring_manifest` | 2 |

---

## 批次 1 — 問題 1：並行 edit_file 靜默覆蓋

### Task 1: 工具呼叫序列化中介層

**Files:**
- Create: `deepagent-service/app/agent/middleware.py`
- Create: `deepagent-service/tests/test_middleware.py`
- Modify: `deepagent-service/app/agent/graph.py:93-111`（`build_agent`）
- Modify: `deepagent-service/tests/test_chat.py`（新增併發回歸測試）

**Interfaces:**
- Produces: `class SerializedToolCallsMiddleware(AgentMiddleware)`，無建構參數，`build_agent` 每次呼叫各建一個實例（`app/main.py:251` 是 per-request，所以鎖天然 per-session）。

**前置檢查（做之前先跑）:**

- [x] **Step 0: deepagents 統一到公司版 0.5.5 並驗證 middleware 路線可行**（2026-08-01 完成，見 commit `chore(deepagent): pin deepagents to the company 0.5.5 build`。已在**專案 venv 的 0.5.5 上**逐項確認：`create_deep_agent` 有 `middleware=` 參數、`AgentMiddleware` 有 `awrap_tool_call`／`awrap_model_call`、`ModelRequest.override` 存在、`deepagents.backends.filesystem.perform_string_replacement` 這個測試接縫存在、使用者自訂 middleware **不會**進到子代理的 middleware 堆疊（故 `task` 工具不會自我死鎖）。0.5.5 基線 149 tests 全綠、ruff 淨。實作者跳過此步。）

```bash
cd /tmp && uv venv .dacheck && \
  uv pip install --python .dacheck/bin/python 'deepagents==0.5.5' 2>&1 | tail -2 && \
  .dacheck/bin/python -c "
import inspect
from deepagents import create_deep_agent
print('middleware' in inspect.signature(create_deep_agent).parameters)
from langchain.agents.middleware.types import AgentMiddleware
print(hasattr(AgentMiddleware, 'awrap_tool_call'))
"
```
Expected: 兩行都 `True`。若任一為 `False`，**停下來回報**——公司 registry 只有 0.5.x，整個 middleware 路線需要換方案。

- [x] **Step 1: 寫會紅的單元測試（真正併發、不碰 LLM）**

`deepagent-service/tests/test_middleware.py`：

```python
"""app/agent/middleware.py 的中介層測試——併發序列化、skill gate、wiring manifest。"""

import asyncio

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from app.agent.middleware import SerializedToolCallsMiddleware


def _tool_call_request(tool_name: str, **arguments: object) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool_name, "id": f"call-{tool_name}", "args": dict(arguments)},
        tool=None,
        state={"messages": []},
        runtime=None,
    )


async def test_awrap_tool_call_never_runs_two_handlers_at_once() -> None:
    """兩個並發的 tool call 進到同一個 middleware 實例時，handler 的執行區間 MUST 不重疊。
    沒有鎖的話兩者會同時在 handler 裡（這正是 FilesystemBackend.edit 讀改寫互相覆蓋的窗口）。"""
    middleware = SerializedToolCallsMiddleware()
    concurrent_handler_count = 0
    max_observed_concurrency = 0

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal concurrent_handler_count, max_observed_concurrency
        concurrent_handler_count += 1
        max_observed_concurrency = max(max_observed_concurrency, concurrent_handler_count)
        await asyncio.sleep(0.02)
        concurrent_handler_count -= 1
        return ToolMessage(content="done", tool_call_id=request.tool_call["id"])

    await asyncio.gather(
        middleware.awrap_tool_call(_tool_call_request("edit_file"), handler),
        middleware.awrap_tool_call(_tool_call_request("write_file"), handler),
    )

    assert max_observed_concurrency == 1


async def test_awrap_tool_call_returns_handler_result_unchanged() -> None:
    middleware = SerializedToolCallsMiddleware()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="payload", tool_call_id=request.tool_call["id"])

    result = await middleware.awrap_tool_call(_tool_call_request("run_sql"), handler)

    assert result.content == "payload"
```

- [x] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_middleware.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.agent.middleware'`

- [x] **Step 3: 實作中介層**

`deepagent-service/app/agent/middleware.py`：

```python
"""主 agent 的 AgentMiddleware——deepagents 只把自訂 middleware 掛到主 agent，
子代理的 middleware 由各自的 subagent spec 帶，故此處的鎖不會與 `task` 工具互鎖。"""

import asyncio
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

ToolCallHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


class SerializedToolCallsMiddleware(AgentMiddleware):
    """同一則 AI message 的多個 tool call 一次只跑一個。

    LangGraph 的 ToolNode 預設把它們 `asyncio.gather` 併發送出，而 deepagents 的
    `write_file`/`edit_file` 是無鎖讀改寫——併發打同一檔案會靜默互相覆蓋、兩邊都回報成功。
    `build_agent` 是 per-request 建立，所以這把鎖天然是 per-session。
    """

    def __init__(self) -> None:
        super().__init__()
        self._tool_call_lock = asyncio.Lock()

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        async with self._tool_call_lock:
            return await handler(request)
```

- [x] **Step 4: 跑測試確認綠**

Run: `cd deepagent-service && uv run pytest tests/test_middleware.py -q`
Expected: 2 passed

- [x] **Step 5: 掛上 `build_agent`**

`app/agent/graph.py` — 加 import 與 `middleware=`：

```python
from app.agent.middleware import SerializedToolCallsMiddleware
```

`create_deep_agent(...)` 加一個參數（放在 `skills=` 之後）：

```python
        # 一次只跑一個 tool call——deepagents 的檔案工具是無鎖讀改寫，併發會靜默互相覆蓋。
        middleware=[SerializedToolCallsMiddleware()],
```

- [x] **Step 6: 寫端到端回歸測試（真的觸發併發）**

在 `deepagent-service/tests/test_chat.py` 末尾新增。`perform_string_replacement` 的 monkeypatch 是關鍵：把讀改寫的窗口撐開，讓「沒有鎖就一定覆蓋」變成確定性，而不是靠 race 運氣。

```python
# -- 併發 edit_file lost-update 回歸 -------------------------------------------------------

_CONCURRENT_EDIT_BASE_HTML = (
    "<html><head></head><body>\n"
    "<div id='chart'></div>\n"
    "<!-- SLOT_A -->\n"
    "<!-- SLOT_B -->\n"
    "<script>const data = window.__ERD_RESULTS__['q1'];\n"
    "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
    "chart.setOption({ tooltip: {}, series: [] });</script>\n"
    "</body></html>"
)


async def test_concurrent_edit_file_calls_both_land(tmp_path, monkeypatch) -> None:
    """同一則 AI message 併發兩個 edit_file 改同一檔案的不相交區段時，兩個改動 MUST 都在。

    `perform_string_replacement` 加 sleep 是刻意的：把 deepagents `FilesystemBackend.edit`
    的讀改寫窗口撐開，沒有序列化中介層時後寫者一定覆蓋前寫者（本測試因此在缺陷還在時必紅）。
    """
    import time

    from deepagents.backends import filesystem as filesystem_backend

    original_replacement = filesystem_backend.perform_string_replacement

    def slow_replacement(*arguments, **keyword_arguments):
        time.sleep(0.05)
        return original_replacement(*arguments, **keyword_arguments)

    monkeypatch.setattr(filesystem_backend, "perform_string_replacement", slow_replacement)

    scripted = ScriptedChatModel(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_sql",
                        "id": "call-sql",
                        "args": {"sql": "SELECT system, COUNT(*) AS tickets FROM t GROUP BY 1",
                                 "intent": "各系統工單數"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "id": "call-write",
                        "args": {"file_path": "dashboard.html",
                                 "content": _CONCURRENT_EDIT_BASE_HTML},
                    }
                ],
            ),
            # 一則 AI message 兩個 edit_file -> ToolNode 併發送出。
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "edit_file",
                        "id": "call-edit-a",
                        "args": {"file_path": "dashboard.html",
                                 "old_string": "<!-- SLOT_A -->",
                                 "new_string": "<div id='panel-a'>A</div>"},
                    },
                    {
                        "name": "edit_file",
                        "id": "call-edit-b",
                        "args": {"file_path": "dashboard.html",
                                 "old_string": "<!-- SLOT_B -->",
                                 "new_string": "<div id='panel-b'>B</div>"},
                    },
                ],
            ),
            AIMessage(content="兩個區塊都已補上。"),
        ]
    )
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)

    await _post_chat(tmp_path, monkeypatch)

    dashboard_html = (
        tmp_path / "ws" / "user-1" / "sessions" / "sess-1" / "dashboard.html"
    ).read_text(encoding="utf-8")
    assert "panel-a" in dashboard_html
    assert "panel-b" in dashboard_html
```

> **實作者注意**：`_post_chat` 與 `ScriptedChatModel` 的既有用法照抄 `tests/test_chat.py` 現有 fixture（`scripted_flow` 那組）；上面示範的 `run_sql` SQL 要對齊該檔實際寫出的 CSV 欄位。若 `_post_chat` 的簽名不同，照現況調整，不要改它。

- [x] **Step 7: 確認測試在沒有中介層時會紅**

暫時把 `graph.py` 的 `middleware=[...]` 註解掉，跑：
Run: `cd deepagent-service && uv run pytest tests/test_chat.py::test_concurrent_edit_file_calls_both_land -q`
Expected: FAIL（`panel-a` 或 `panel-b` 其中一個不在）。確認後把 `middleware=` 改回來。

- [x] **Step 8: 全測試 + lint**

Run: `cd deepagent-service && uv run pytest -q && uv run ruff check .`
Expected: 全綠、lint 淨。

- [x] **Step 9: 確認 Java／前端零改動並 commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork"
git status --short   # 不得出現 backend/ 或 frontend/ 底下的檔案
git add deepagent-service/app/agent/middleware.py deepagent-service/app/agent/graph.py \
        deepagent-service/tests/test_middleware.py deepagent-service/tests/test_chat.py \
        docs/superpowers/plans/2026-08-01-deepagent-trace-findings-fixes.md \
        docs/deepagent-trace-findings-2026-08-01.md
git commit -m "fix(deepagent): serialize tool calls to stop concurrent edit_file lost updates"
```

---

## 批次 2 — 問題 2＋3：getCol 綁錯無聲 ＋ qN 別名憑記憶

> 兩題必須同批：manifest 降低綁錯率、warn 收集器保證綁錯被抓到，分開做量不出效果。

### Task 2: sandbox `console.warn` 收集器 → guard error

**Files:**
- Modify: `deepagent-service/app/engine/html_guard.py:405-437`（`_SANDBOX_PRELUDE`）、`:595-690`（`_execute_scripts_smoke`）
- Modify: `deepagent-service/tests/test_html_guard.py`

**Interfaces:**
- Produces: `_check_column_not_found_warnings(collected_warnings, results, html) -> list[str]`；`_resolve_stack_call_site_line(stack_text, block_start_line) -> int | None`。

**背景（實作前先讀）:** skill 強制的 `getCol` 樣板（`skills/dashboard/SKILL.md:93-96`、`references/examples.md:100-102`）找不到欄位時會 `console.warn('[ERD] column not found:', candidates)`。sandbox 目前把 `console.warn` 寫成 no-op（`html_guard.py:408`），29 個真實訊號全進垃圾桶。`getCol` 回 `-1` 的契約是 skill 規定的防禦式寫法，**NEVER 改掉這個契約**——要改的是讓 guard 聽見它已經在發的訊號。

quickjs 的 `(new Error()).stack` 實測格式（已驗證）：
```
    at warn (<input>)
    at getCol (<input>:6)
    at chartA (<input>:12)
    at <eval> (<input>:18)
```
`warn` 這格沒有行號；第一個有行號的 frame 是 `getCol` 內的 warn 呼叫行；第二個才是**呼叫點**（要報的那一行）。

- [ ] **Step 1: 寫會紅的 guard 測試**

加到 `deepagent-service/tests/test_html_guard.py`：

```python
# -- getCol miss：console.warn 收集器 -------------------------------------------------------

_GET_COL_HELPER = (
    "function getCol(columns, ...candidates) {\n"
    "  for (const candidate of candidates) {\n"
    "    const index = columns.indexOf(candidate);\n"
    "    if (index >= 0) return index;\n"
    "  }\n"
    "  console.warn('[ERD] column not found:', candidates); return -1;\n"
    "}\n"
)


def _get_col_miss_html() -> str:
    return (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="chart"></div>\n'
        "<script>\n"
        + _GET_COL_HELPER
        + "const featureRating = window.__ERD_RESULTS__['q2'];\n"
        "const ratingIndex = getCol(featureRating.columns, 'avg_rating');\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "</script></body></html>"
    )


def test_get_col_miss_is_rejected_with_call_site_and_owning_query() -> None:
    """getCol 找不到欄位時只 console.warn 回 -1，不拋例外——guard MUST 把這個訊號變成退貨，
    並且指出呼叫點行號與「該欄位其實在哪個 qN」，讓模型一輪修完。"""
    results = {
        "q2": {"columns": ["sentiment", "count", "percentage"], "rows": [["正面", 3, 0.5]],
               "truncated": False},
        "q5": {"columns": ["feature_name", "avg_rating"], "rows": [["匯出", 4.2]],
               "truncated": False},
    }
    report = check_dashboard_html(_get_col_miss_html(), {"q2", "q5"}, results)

    assert not report.ok, report.errors
    miss_errors = [error for error in report.errors if "avg_rating" in error]
    assert miss_errors, report.errors
    assert "q5" in miss_errors[0], miss_errors
    # 呼叫點是 getCol(...) 那一行（第 12 行），不是 helper 裡 console.warn 的那一行。
    assert "Line 12:" in miss_errors[0], miss_errors


def test_get_col_hit_produces_no_warning_error() -> None:
    """欄位真的存在時零誤報。"""
    results = {
        "q2": {"columns": ["sentiment", "avg_rating"], "rows": [["正面", 4.2]],
               "truncated": False},
    }
    report = check_dashboard_html(_get_col_miss_html(), {"q2"}, results)

    assert not any("column not found" in error for error in report.errors), report.errors


def test_get_col_miss_without_real_results_is_not_reported() -> None:
    """沒有真實 results 時 sandbox 灌的是泛用假欄名(__c0/__c1)，每個 getCol 都會 miss——
    這種情況 MUST 整條規則跳過，否則全是誤報。"""
    report = check_dashboard_html(_get_col_miss_html(), {"q2"}, None)

    assert not any("column not found" in error for error in report.errors), report.errors
```

> 行號請以實際 HTML 字串重數一次再寫死；上面的 `Line 12` 是依 `_get_col_miss_html()` 的組法算的，若你調整了字串就要跟著改。

- [ ] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_html_guard.py -k get_col -q`
Expected: `test_get_col_miss_is_rejected_with_call_site_and_owning_query` FAIL（`report.ok` 是 True）。另兩條應該直接綠（它們是防誤報的護欄）。

- [ ] **Step 3: prelude 改成收集器**

`_SANDBOX_PRELUDE` 裡把 `warn: function () {},` 換掉，並在 `__erd_console_errors__` 旁邊多兩個全域：

```javascript
var __erd_console_errors__ = [];
// getCol 樣板找不到欄位時只 console.warn 回 -1（skill 規定的防禦式契約）——不收集就永遠
// 攔不到綁錯欄位。stack 讓 Python 端算出呼叫點行號，base 是本段 script 在 HTML 的起始行。
var __erd_console_warnings__ = [];
var __erd_block_start_line__ = 1;
var console = {
  log: function () {},
  warn: function () {
    var stringifiedArguments = [];
    for (var argumentIndex = 0; argumentIndex < arguments.length; argumentIndex++) {
      stringifiedArguments.push(String(arguments[argumentIndex]));
    }
    __erd_console_warnings__.push({
      message: stringifiedArguments.join(" "),
      stack: String((new Error()).stack || ""),
      base: __erd_block_start_line__,
    });
  },
  error: function () {
    ...原樣保留...
  },
};
```

- [ ] **Step 4: 每段 block 執行前設定 base line**

`_execute_scripts_smoke` 內，`context.eval(script_content)` 之前先設 base；ReferenceError 重建 context 後的**靜默重放**也要逐段設（否則重放的 warn 會帶錯的 base）。把 `script_contents = [content for content, _ in script_blocks_with_lines]` 改成保留行號的重放：

```python
            for earlier_content, earlier_start_line in script_blocks_with_lines[:script_index]:
                # 只求重建到「當前 block 前」該有的宣告狀態；重放時 warn 會如實重現一次。
                with contextlib.suppress(Exception):
                    context.eval(f"__erd_block_start_line__ = {earlier_start_line};")
                    context.eval(earlier_content)
```

主迴圈內：

```python
        for script_index, (script_content, html_start_line) in enumerate(script_blocks_with_lines):
            retry_count = 0
            while True:
                try:
                    context.eval(f"__erd_block_start_line__ = {html_start_line};")
                    context.eval(script_content)
                    break
```

- [ ] **Step 5: 讀出並轉成 guard error**

在 `_check_swallowed_chart_errors` 附近新增（放在它下面）：

```python
# getCol 樣板的固定寫法:`console.warn('[ERD] column not found:', candidates)`;candidates 是
# 陣列,`String(array)` 會變成逗號串接的字串。
_COLUMN_NOT_FOUND_PATTERN = re.compile(r"^\[ERD\] column not found:\s*(.*)$", re.DOTALL)

# 一次退貨最多列幾條 getCol miss——修復 prompt 不能無限長,超出的用一行摘要帶過。
_MAX_REPORTED_COLUMN_MISSES = 8

# `(new Error()).stack` 的單一 frame:`    at <name> (<input>[:line])`。
_STACK_FRAME_PATTERN = re.compile(r"^\s*at\s+(\S+)\s+\(<input>(?::(\d+))?\)", re.MULTILINE)


def _stack_frame_lines(stack_text: str, skip_function_names: frozenset[str]) -> list[int]:
    """回傳 stack 由深到淺、帶行號的 frame 行號列表,略過指定的函式名(sandbox 自己的
    stub frame)。quickjs 對部分 frame 不給行號,那些一律略過。"""
    frame_lines: list[int] = []
    for frame_match in _STACK_FRAME_PATTERN.finditer(stack_text):
        if frame_match.group(1) in skip_function_names:
            continue
        if frame_match.group(2) is None:
            continue
        frame_lines.append(int(frame_match.group(2)))
    return frame_lines


def _owning_query_ids_for_column(column_name: str, results: dict[str, dict]) -> list[str]:
    """哪些 query result 真的有這個欄位——讓退貨訊息能直接寫出「該欄位存在於 qN」。"""
    return sorted(
        query_id
        for query_id, result in results.items()
        if column_name in (result.get("columns") or [])
    )


def _read_collected_console_warnings(context: "quickjs.Context") -> list[dict]:
    """讀出 sandbox `console.warn` 收集器目前累積的紀錄。讀取失敗記 warning、回空列表。"""
    try:
        serialized_warnings = context.eval("JSON.stringify(__erd_console_warnings__)")
        return json.loads(serialized_warnings)
    except Exception as read_error:  # noqa: BLE001 -- 驗證器掛掉不擋主流程
        logger.warning(
            "html_guard: 讀取 sandbox console.warn 收集結果失敗，跳過偵測: %s", read_error
        )
        return []


def _check_column_not_found_warnings(
    collected_warnings: list[dict], results: dict[str, dict], html_lines: list[str]
) -> list[str]:
    """把 `[ERD] column not found: ...` 的 warn 轉成 guard error。

    行號取 stack 的第二個帶行號 frame(第一個是 getCol 內 console.warn 那行,第二個才是
    真正綁錯的呼叫點);候選欄位再回頭比對真實 `results`,算出「該欄位其實在哪個 qN」,
    讓模型一輪修完而不是猜。
    """
    errors: list[str] = []
    seen_call_sites: set[tuple[int, str]] = set()
    for warning in collected_warnings:
        message_match = _COLUMN_NOT_FOUND_PATTERN.match(str(warning.get("message", "")))
        if message_match is None:
            continue
        candidate_columns = [part.strip() for part in message_match.group(1).split(",") if part.strip()]
        if not candidate_columns:
            continue

        frame_lines = _stack_frame_lines(str(warning.get("stack", "")), frozenset({"warn"}))
        block_start_line = int(warning.get("base", 1))
        relative_line = frame_lines[1] if len(frame_lines) >= 2 else (
            frame_lines[0] if frame_lines else None
        )
        html_line = block_start_line + relative_line - 1 if relative_line is not None else None

        deduplication_key = (html_line or -1, ",".join(candidate_columns))
        if deduplication_key in seen_call_sites:
            continue
        seen_call_sites.add(deduplication_key)

        location_hint = f"Line {html_line}: " if html_line is not None else ""
        source_line = (
            html_lines[html_line - 1].strip()[:120]
            if html_line is not None and 0 < html_line <= len(html_lines)
            else ""
        )
        owning_hints = []
        for candidate_column in candidate_columns:
            owning_query_ids = _owning_query_ids_for_column(candidate_column, results)
            if owning_query_ids:
                owning_hints.append(f"'{candidate_column}' exists in {', '.join(owning_query_ids)}")
        owning_text = (
            " ".join(owning_hints)
            if owning_hints
            else "None of these columns exist in any query result -- run the query you actually need."
        )
        errors.append(
            f"{location_hint}getCol found none of {candidate_columns} in the columns passed here, "
            f"so it returned -1 and this block renders blank/undefined/NaN. {owning_text}. "
            f"Bind the correct query id here. Source: {source_line}"
        )

    if len(errors) > _MAX_REPORTED_COLUMN_MISSES:
        hidden_count = len(errors) - _MAX_REPORTED_COLUMN_MISSES
        errors = errors[:_MAX_REPORTED_COLUMN_MISSES]
        errors.append(f"... and {hidden_count} more getCol misses with the same root cause.")
    return errors
```

- [ ] **Step 6: 接上 `_execute_scripts_smoke` 的尾巴**

`_execute_scripts_smoke` 需要多兩個參數才能判斷「sandbox 灌的是不是真欄名」與取原始行文字。改簽名並在尾端接：

```python
    errors.extend(_check_swallowed_chart_errors(_read_collected_console_errors(context)))
    # 只有整份 results 都是真實欄名時才判定 getCol miss——退回泛用假欄名(__c0/__c1)時
    # 每個 getCol 都會 miss，轉成 error 會全是誤報。
    if results is not None and available_query_ids <= set(results):
        errors.extend(
            _check_column_not_found_warnings(
                _read_collected_console_warnings(context), results, html.splitlines()
            )
        )
    return errors
```

`_execute_scripts_smoke` 的簽名多一個 keyword 參數 `html: str = ""`，由 `check_dashboard_html` 呼叫時傳入（`_execute_scripts_smoke(..., known_element_ids, html=html)`）——不要在函式內用 script blocks 重組 HTML。

- [ ] **Step 7: 跑測試確認綠 + 既有測試不回歸**

Run: `cd deepagent-service && uv run pytest tests/test_html_guard.py -q`
Expected: 全綠。若既有測試因為新錯誤而紅，先確認是不是真的 miss（多半是測試 HTML 用假欄名），是的話補 `results` 參數而不是放寬檢查。

### Task 3: wiring manifest 注入

**Files:**
- Modify: `deepagent-service/app/engine/results.py`
- Modify: `deepagent-service/app/agent/middleware.py`
- Modify: `deepagent-service/app/agent/graph.py`
- Modify: `deepagent-service/tests/test_results.py`、`deepagent-service/tests/test_middleware.py`

**Interfaces:**
- Consumes: `load_all_results(workspace)`（`app/engine/results.py:100`）、Task 1 的 `middleware=` list。
- Produces: `format_wiring_manifest(results: dict[str, dict]) -> str`（純函式，engine 層）；`class WiringManifestMiddleware(AgentMiddleware)`，建構參數 `workspace: SessionWorkspace`。

**設計決定（與報告的差異，實作者不要自行改回去）:** 報告寫「dashboard turn 開始時注入」。**改成每次 model call 由 middleware 重建**——session E 是同一輪跑完 11 個查詢後才寫 dashboard，turn 開始時那些 results 還不存在，turn-start 注入對這個主要情境完全無效。manifest 只有 qid/intent/columns 三欄、每個 query 一行（十幾行），與「每輪注入 46KB skill references 會加劇 reasoning runaway」不是同一個量級。

- [ ] **Step 1: 寫會紅的測試**

`deepagent-service/tests/test_results.py` 末尾：

```python
def test_format_wiring_manifest_lists_intent_and_columns() -> None:
    manifest = format_wiring_manifest(
        {
            "q2": {"intent": "各情感分佈", "columns": ["sentiment", "count"], "rows": []},
            "q1": {"intent": "各功能使用次數", "columns": ["feature_name", "usage_count"],
                   "rows": []},
        }
    )

    assert "q1" in manifest and "各功能使用次數" in manifest and "feature_name" in manifest
    # 依 qid 排序，不是 dict 順序——避免同一輪內順序抖動讓 prompt 前綴每次都不同。
    assert manifest.index("q1") < manifest.index("q2")


def test_format_wiring_manifest_empty_results_is_empty_string() -> None:
    assert format_wiring_manifest({}) == ""
```

`deepagent-service/tests/test_middleware.py` 末尾：

```python
async def test_wiring_manifest_middleware_appends_current_results(tmp_path) -> None:
    """manifest MUST 反映「呼叫當下」workspace 上的 results——同一輪內新跑的查詢也要進去。"""
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import SystemMessage

    from app.agent.middleware import WiringManifestMiddleware
    from app.engine.results import record_query
    from app.engine.workspace import LocalWorkspaceStore

    workspace = LocalWorkspaceStore(str(tmp_path)).prepare("user-1", "sess-1")
    record_query(workspace, "q1", "SELECT 1", "各功能使用次數", ["feature_name"], [["匯出"]], False)

    middleware = WiringManifestMiddleware(workspace)
    captured_system_messages: list[SystemMessage | None] = []

    async def handler(request: ModelRequest) -> str:
        captured_system_messages.append(request.system_message)
        return "ok"

    request = ModelRequest(model=None, messages=[], system_message=SystemMessage("BASE"))
    await middleware.awrap_model_call(request, handler)

    assert "q1" in captured_system_messages[0].content
    assert "各功能使用次數" in captured_system_messages[0].content
    assert "BASE" in captured_system_messages[0].content
```

> `LocalWorkspaceStore` 的建構方式照 `tests/test_workspace.py` 現況；`record_query` 的參數順序照 `app/engine/results.py:64`。

- [ ] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_results.py tests/test_middleware.py -q`
Expected: FAIL（`ImportError: cannot import name 'format_wiring_manifest'` / `WiringManifestMiddleware`）

- [ ] **Step 3: 實作 `format_wiring_manifest`**

`app/engine/results.py` 末尾：

```python
# 綁定 manifest 的標題——模型看到的第一行，明講「不要憑記憶對編號」。
_WIRING_MANIFEST_HEADER = (
    "Query results currently available in window.__ERD_RESULTS__ "
    "(bind dashboard blocks by these ids and columns -- NEVER guess a q-number from memory):"
)


def format_wiring_manifest(results: dict[str, dict]) -> str:
    """把 `load_all_results` 的結果攤成 `qid -- intent -- columns` 的逐行清單；空結果回空字串。

    依 qid 排序而非 dict 順序，讓同一輪內重複呼叫產生一致的字串。
    """
    if not results:
        return ""
    manifest_lines = [_WIRING_MANIFEST_HEADER]
    for query_id in sorted(results):
        result = results[query_id]
        column_names = ", ".join(result.get("columns") or [])
        manifest_lines.append(
            f"- {query_id} -- intent: {result.get('intent', '')} -- columns: {column_names}"
        )
    return "\n".join(manifest_lines)
```

- [ ] **Step 4: 實作 middleware**

`app/agent/middleware.py` 追加：

```python
class WiringManifestMiddleware(AgentMiddleware):
    """每次 model call 都把「目前有哪些 qN、各自的 intent 與欄位」附在 system message 後面。

    模型原本是憑幾十個 tool call 之前的對話記憶對應 qN 編號，綁錯是常態。每次呼叫重建
    (而非每輪一次)是必要的——同一輪內先跑查詢後寫 dashboard 是主要情境。
    """

    def __init__(self, workspace: SessionWorkspace) -> None:
        super().__init__()
        self._workspace = workspace

    async def awrap_model_call(
        self, request: ModelRequest, handler: ModelCallHandler
    ) -> AIMessage:
        manifest_text = format_wiring_manifest(load_all_results(self._workspace))
        if not manifest_text:
            return await handler(request)
        existing_text = request.system_message.content if request.system_message else ""
        return await handler(
            request.override(system_message=SystemMessage(f"{existing_text}\n\n{manifest_text}"))
        )
```

需要的 import（加到檔頭）：

```python
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from app.engine.results import format_wiring_manifest, load_all_results
from app.engine.workspace import SessionWorkspace

ModelCallHandler = Callable[[ModelRequest], Awaitable[AIMessage]]
```

- [ ] **Step 5: 掛上 `build_agent`**

`app/agent/graph.py` 的 `middleware=` 改成：

```python
        middleware=[SerializedToolCallsMiddleware(), WiringManifestMiddleware(workspace)],
```

- [ ] **Step 6: 跑全測試 + lint**

Run: `cd deepagent-service && uv run pytest -q && uv run ruff check .`
Expected: 全綠、lint 淨。

- [ ] **Step 7: 對真實 workspace 驗一次 guard（batch 2 的實證）**

挑一個既有 session workspace（`docker exec erd-cowork-deepagent-service-1 sh -c 'ls -d /data/workspace/*/sessions/*' | head`），照 report「驗證方法」那段跑 guard——**MUST 用 `/app/.venv/bin/python`**。要看到 getCol miss 被列出來。注意容器跑的是既有 image，需要先 `docker compose build deepagent-service && docker compose up -d deepagent-service` 或直接把改過的 `html_guard.py` `docker cp` 進去測。

- [ ] **Step 8: Commit**

```bash
git add deepagent-service/app deepagent-service/tests
git commit -m "fix(deepagent): surface getCol column misses and inject qid wiring manifest"
```

---

## 批次 3 — 問題 5＋6a：未讀 skill 就寫檔 ＋ 資料綁定靜態檢查

> 必須同批：沒有 6a 的檢查就量不出 gate 有沒有效（沒讀 skill 的失敗是靜默的——資料寫死、順利過 guard）。

### Task 4: 6a——有 echarts.init 但全檔零次 `__ERD_RESULTS__` → 退貨

**Files:**
- Modify: `deepagent-service/app/engine/html_guard.py`（`_check_tooltip` 附近新增 `_check_data_binding`，並在 `check_dashboard_html` 呼叫）
- Modify: `deepagent-service/tests/test_html_guard.py`

- [ ] **Step 1: 寫會紅的測試**

```python
def test_charts_without_any_erd_results_reference_fail() -> None:
    """把數字硬編進 HTML、完全不讀 __ERD_RESULTS__ 的 dashboard 目前能順利過 guard——
    這類違規在指標上是隱形的，MUST 退貨。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="chart"></div>'
        "<script>const chart = echarts.init(document.getElementById(\"chart\"), 'erd'); "
        'chart.setOption({ tooltip: {}, series: [{ data: [42, 7] }] });</script>'
        "</body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert not report.ok
    assert any("__ERD_RESULTS__" in error for error in report.errors), report.errors


def test_html_without_charts_is_not_required_to_bind_results() -> None:
    """純文字/表格 dashboard 沒有 echarts.init，零檢查零誤報。"""
    html = "<html><head></head><body><div>純文字結論</div></body></html>"
    report = check_dashboard_html(html, {"q1"})

    assert not any("__ERD_RESULTS__" in error for error in report.errors), report.errors
```

- [ ] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_html_guard.py -k erd_results -q`
Expected: 第一條 FAIL。

- [ ] **Step 3: 實作檢查**

`html_guard.py`，`_check_tooltip` 下方：

```python
def _check_data_binding(html: str, errors: list[str]) -> None:
    """有圖表就一定要從 `window.__ERD_RESULTS__` 取資料。全檔零次引用代表數字被硬編進
    HTML——不會拋例外、順利過其他檢查，但交付的每個數字都可能是過期的。"""
    if _ECHARTS_INIT_CALL_PREFIX in html and "__ERD_RESULTS__" not in html:
        errors.append(
            "The dashboard initializes ECharts but never reads window.__ERD_RESULTS__ -- the "
            "numbers are hard-coded. Every chart, KPI and table MUST read its data from "
            "window.__ERD_RESULTS__['<query id>'] (see the dashboard skill)."
        )
```

在 `check_dashboard_html` 的 `_check_tooltip(html, errors)` 下一行加 `_check_data_binding(html, errors)`。

- [ ] **Step 4: 跑全 guard 測試**

Run: `cd deepagent-service && uv run pytest tests/test_html_guard.py -q`
Expected: 全綠。既有測試若有「只有 echarts.init 沒有 `__ERD_RESULTS__`」的 HTML 字串，補上 `const data = window.__ERD_RESULTS__["q1"];`——不要放寬檢查。

### Task 5: dashboard skill 已讀 gate

**Files:**
- Modify: `deepagent-service/app/agent/middleware.py`
- Modify: `deepagent-service/app/agent/graph.py`
- Modify: `deepagent-service/tests/test_middleware.py`、`deepagent-service/tests/test_chat.py`

**Interfaces:**
- Produces: `class DashboardSkillGateMiddleware(AgentMiddleware)`，建構參數 `workspace: SessionWorkspace`。

**設計要點（報告裡有數據，不要自行簡化）:**
- gate 的單位 MUST 是 `SKILL.md` **與** `references/examples.md` 兩份。只 gate SKILL.md 會讓情況變糟——「只讀 SKILL.md」那組的當機率(75%)比「完全沒讀」(33%)還高：知道要用 `__ERD_RESULTS__`／`getCol` 卻沒看過可運作範例。
- gate 在**寫檔動作**上，NEVER 改成每輪注入（46KB references 每輪注入會加劇 qwen3.6 的 reasoning runaway）。
- 判定單位是 **thread**（同 thread 先前輪次讀過就留在 context 裡）→ 掃 `request.state["messages"]` 的歷史 `read_file` tool call，而不是 middleware 實例狀態（實例是 per-request 的）。
- 找不到 staged skill 檔（沒 stage skills 的部署）→ **fail-open**，直接放行。

- [ ] **Step 1: 寫會紅的單元測試**

`tests/test_middleware.py` 追加：

```python
async def test_dashboard_write_is_blocked_before_skill_is_read(tmp_path) -> None:
    from langchain_core.messages import AIMessage

    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import LocalWorkspaceStore, builtin_skills_dir, stage_skills

    workspace = LocalWorkspaceStore(str(tmp_path)).prepare("user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")

    middleware = DashboardSkillGateMiddleware(workspace)
    handler_called = False

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={"name": "write_file", "id": "call-1",
                   "args": {"file_path": "dashboard.html", "content": "<html></html>"}},
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert not handler_called
    assert "SKILL.md" in result.content and "examples.md" in result.content


async def test_dashboard_write_is_allowed_after_both_skill_files_are_read(tmp_path) -> None:
    from langchain_core.messages import AIMessage

    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import LocalWorkspaceStore, builtin_skills_dir, stage_skills

    workspace = LocalWorkspaceStore(str(tmp_path)).prepare("user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")

    prior_reads = AIMessage(
        content="",
        tool_calls=[
            {"name": "read_file", "id": "r1",
             "args": {"file_path": "/.skills/builtin/dashboard/SKILL.md"}},
            {"name": "read_file", "id": "r2",
             "args": {"file_path": ".skills/builtin/dashboard/references/examples.md"}},
        ],
    )
    middleware = DashboardSkillGateMiddleware(workspace)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={"name": "write_file", "id": "call-1",
                   "args": {"file_path": "dashboard.html", "content": "<html></html>"}},
        tool=None,
        state={"messages": [prior_reads]},
        runtime=None,
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert result.content == "written"


async def test_non_dashboard_writes_are_never_gated(tmp_path) -> None:
    from app.agent.middleware import DashboardSkillGateMiddleware
    from app.engine.workspace import LocalWorkspaceStore, builtin_skills_dir, stage_skills

    workspace = LocalWorkspaceStore(str(tmp_path)).prepare("user-1", "sess-1")
    stage_skills(workspace, builtin_skills_dir(), tmp_path / "no-user-skills")
    middleware = DashboardSkillGateMiddleware(workspace)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="written", tool_call_id=request.tool_call["id"])

    request = ToolCallRequest(
        tool_call={"name": "write_file", "id": "call-1",
                   "args": {"file_path": "notes.md", "content": "note"}},
        tool=None,
        state={"messages": []},
        runtime=None,
    )
    assert (await middleware.awrap_tool_call(request, handler)).content == "written"
```

- [ ] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_middleware.py -k skill -q`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: 實作 gate**

`app/agent/middleware.py` 追加：

```python
# gate 的兩份檔案:只要求 SKILL.md 會讓情況變糟——知道契約卻沒看過可運作範例的模型，
# 當機率比完全沒讀還高(見 docs/deepagent-trace-findings-2026-08-01.md 問題 5)。
_REQUIRED_SKILL_RELATIVE_PATHS: tuple[str, ...] = (
    ".skills/builtin/dashboard/SKILL.md",
    ".skills/builtin/dashboard/references/examples.md",
)
_GATED_TOOL_NAMES = frozenset({"write_file", "edit_file"})
_GATED_FILE_NAME = "dashboard.html"


def _normalized_workspace_path(file_path: str) -> str:
    """把 virtual_mode 的 `/a/b` 與相對的 `a/b` 收斂成同一種寫法,好做比對。"""
    return file_path.strip().lstrip("/")


class DashboardSkillGateMiddleware(AgentMiddleware):
    """thread 內沒讀過 dashboard skill 的 SKILL.md 與 references/examples.md 之前，
    擋掉對 dashboard.html 的 write_file/edit_file，退貨訊息直接給路徑。

    判定掃的是 `request.state` 的訊息歷史(thread 層級,延續輪繼承先前輪次的 read)，
    不是實例狀態——`build_agent` 是 per-request 的。staged skill 檔不存在時一律放行。
    """

    def __init__(self, workspace: SessionWorkspace) -> None:
        super().__init__()
        self._required_paths = tuple(
            relative_path
            for relative_path in _REQUIRED_SKILL_RELATIVE_PATHS
            if (workspace.root / relative_path).is_file()
        )

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: ToolCallHandler
    ) -> ToolMessage | Command:
        if not self._is_gated_dashboard_write(request):
            return await handler(request)
        unread_paths = self._unread_required_paths(request.state)
        if not unread_paths:
            return await handler(request)
        required_list = "\n".join(f"- {path}" for path in self._required_paths)
        return ToolMessage(
            content=(
                "Blocked: dashboard.html MUST NOT be written before the dashboard skill has "
                "been read in this conversation. Read BOTH of these first with read_file "
                f"(pass limit=1000, the 100-line default truncates them):\n{required_list}\n"
                "Then retry this write."
            ),
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def _is_gated_dashboard_write(self, request: ToolCallRequest) -> bool:
        if not self._required_paths:
            return False
        if request.tool_call["name"] not in _GATED_TOOL_NAMES:
            return False
        file_path = request.tool_call.get("args", {}).get("file_path", "")
        return _normalized_workspace_path(str(file_path)) == _GATED_FILE_NAME

    def _unread_required_paths(self, state: object) -> list[str]:
        read_paths: set[str] = set()
        messages = state.get("messages", []) if isinstance(state, dict) else []
        for message in messages:
            for tool_call in getattr(message, "tool_calls", None) or []:
                if tool_call.get("name") != "read_file":
                    continue
                read_paths.add(
                    _normalized_workspace_path(str(tool_call.get("args", {}).get("file_path", "")))
                )
        return [path for path in self._required_paths if path not in read_paths]
```

- [ ] **Step 4: 掛上 `build_agent`**

```python
        middleware=[
            SerializedToolCallsMiddleware(),
            WiringManifestMiddleware(workspace),
            DashboardSkillGateMiddleware(workspace),
        ],
```

- [ ] **Step 5: 端到端測試——gate 真的擋住 /chat 的寫檔**

在 `tests/test_chat.py` 加一條：腳本第一則就 `write_file` dashboard.html（不先 read skill），斷言最後 workspace 上**沒有** dashboard.html，且事件流沒有 `DASHBOARD_HTML`。第二條：腳本先兩個 `read_file`（兩份 skill 路徑）再 `write_file`，斷言有 `DASHBOARD_HTML`。

> 既有的 `scripted_flow` fixture 多半是直接 `write_file` 的——它們會因為這個 gate 而紅。**照 gate 的要求在腳本前面補兩個 `read_file` tool call**，不要為了讓測試過而放寬 gate。

- [ ] **Step 6: 跑全測試 + lint + commit**

```bash
cd deepagent-service && uv run pytest -q && uv run ruff check .
cd .. && git status --short && git add deepagent-service && \
  git commit -m "fix(deepagent): gate dashboard writes on skill reads and reject unbound charts"
```

---

## 批次 4 — 問題 4＋6b：修復迴圈收斂 ＋ tab resize 檢查

### Task 6: 重新確認 session D 的診斷（施工前置）

批次 1 修掉並行覆蓋之後，原本歸因於「模型收斂失敗」的行為要重新驗證。報告主張 session D 的 13 次編輯全部是循序的（每則 AI message 一個 tool call），與問題 1 無關。

- [ ] **Step 1: 用 Langfuse 重驗**

```bash
curl -s -u pk-lf-erd-cowork-dev:sk-lf-erd-cowork-dev \
  "http://localhost:3010/api/public/observations?name=edit_file&fromStartTime=2026-08-01T00:00:00Z&page=1&limit=100" \
  | python3 -c "
import json,sys,collections
data=json.load(sys.stdin)['data']
buckets=collections.Counter((o['traceId'], o['startTime'][:22]) for o in data)
for key,count in buckets.items():
    if count > 1: print('PARALLEL BATCH', key, count)
print('total edit_file observations:', len(data))
"
```

判定：同一 traceId 內 `startTime` 落在同一 100ms 桶的 observations 即為併發批次。session D 的 thread 是 `d0f02a96`、首輪 trace `373c8789`。

- [ ] **Step 2: 記錄結論**

在本檔這一行下面寫一句結論（診斷成立／不成立）。若**不成立**（session D 其實也有併發批次），停下來回報——批次 4 的前提要重新評估。

> 結論：（實作者填寫）

### Task 7: 錯誤訊息報呼叫點、並列出共用 helper 的全部呼叫點

**Files:**
- Modify: `deepagent-service/app/engine/html_guard.py:526-551`（`_resolve_html_error_line`／`_format_execution_error`）、`:633-687`（呼叫處）
- Modify: `deepagent-service/tests/test_html_guard.py`

**背景:** guard 目前回報 `Line 112: TypeError: cannot read property 'indexOf' of undefined`，而 line 112 在 `getCol` 函式體內。`getCol` 是 skill 強制每份 dashboard 都要有的共用 helper，**全檔任何一次欄位解析失敗都塌縮到同一行**——模型拿不到「哪個綁定錯了」，只能猜。實測 quickjs stack 有完整 frame 行號（見 Task 2 背景）。

- [ ] **Step 1: 寫會紅的測試**

```python
def test_error_inside_shared_helper_reports_call_site_and_all_call_sites() -> None:
    """共用 helper 內拋的例外 MUST 報呼叫點行號,並列出該 helper 的全部呼叫點——
    否則全檔的欄位解析失敗都塌縮到 helper 那一行,模型只能猜。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><div id="chart"></div>\n'
        "<script>\n"
        "function getCol(columns, candidate) {\n"
        "  return columns.indexOf(candidate);\n"
        "}\n"
        "const first = window.__ERD_RESULTS__['q1'].rows;\n"
        "const firstIndex = getCol(first.columns, 'a');\n"
        "const secondIndex = getCol(first.columns, 'b');\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "</script></body></html>"
    )
    results = {"q1": {"columns": ["a", "b"], "rows": [["x", 1]], "truncated": False}}
    report = check_dashboard_html(html, {"q1"}, results)

    assert not report.ok, report.errors
    type_errors = [error for error in report.errors if "TypeError" in error]
    assert type_errors, report.errors
    # 呼叫點是第 8 行(第一個 getCol 呼叫)，不是 helper 內的第 5 行。
    assert "Line 8:" in type_errors[0], type_errors
    assert "getCol" in type_errors[0], type_errors
    # 同類的另一個呼叫點也要一併列出，讓模型一輪修完。
    assert "9" in type_errors[0], type_errors
```

> 行號請以實際字串重數一次再寫死。

- [ ] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_html_guard.py -k shared_helper -q`
Expected: FAIL（報的是 helper 內的行號、沒有呼叫點清單）

- [ ] **Step 3: 實作**

重用 Task 2 已經加好的 `_STACK_FRAME_PATTERN`（`^\s*at\s+(\S+)\s+\(<input>(?::(\d+))?\)`，group 1 是函式名、group 2 是行號）——**不要再定義第二顆同樣的 regex**。改寫 `_resolve_html_error_line` 為回傳整條 frame 鏈，並在 `_format_execution_error` 組訊息：

```python
def _resolve_error_frames(message: str, html_start_line: int) -> list[tuple[str, int]]:
    """把 quickjs 例外訊息的 stack 轉成 [(函式名, HTML 絕對行號)]，由深到淺；
    沒有行號的 frame 略過。空 stack(純語法錯誤等)回空列表。"""
    frames: list[tuple[str, int]] = []
    for frame_match in _STACK_FRAME_PATTERN.finditer(message):
        if frame_match.group(2) is None:
            continue
        frames.append((frame_match.group(1), html_start_line + int(frame_match.group(2)) - 1))
    return frames


# 共用 helper 的呼叫點:`name(` 出現處,排除 `function name(` 這個定義本身。
def _helper_call_site_lines(html: str, helper_name: str) -> list[int]:
    call_pattern = re.compile(rf"(?<![\w$.]){re.escape(helper_name)}\s*\(")
    definition_pattern = re.compile(rf"function\s+{re.escape(helper_name)}\s*\(")
    call_site_lines: list[int] = []
    for line_index, line_text in enumerate(html.splitlines(), start=1):
        if definition_pattern.search(line_text):
            continue
        if call_pattern.search(line_text):
            call_site_lines.append(line_index)
    return call_site_lines
```

`_format_execution_error` 改成收 `frames` 與 `html`：

```python
def _format_execution_error(
    frames: list[tuple[str, int]], script_index: int, first_line: str, html: str
) -> str:
    """餵回修復 prompt 的錯誤訊息。例外在共用 helper 內拋出時,標題行號用**呼叫點**而非
    拋出點,並列出該 helper 的全部呼叫點——同一個缺陷通常同時打中每一個,一輪列完才修得完。"""
    if not frames:
        truncated_message = first_line[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
        return f"script#{script_index} execution error: {truncated_message}"

    throwing_function_name, throw_line = frames[0]
    call_site_line = frames[1][1] if len(frames) >= 2 else throw_line

    variable_match = _REFERENCE_ERROR_VAR_PATTERN.search(first_line)
    headline = (
        f"ReferenceError '{variable_match.group(1)}' is not defined"
        if variable_match
        else first_line[:_SANDBOX_ERROR_MESSAGE_MAX_LENGTH]
    )

    if len(frames) < 2 or throwing_function_name == "<eval>":
        return f"Line {throw_line}: {headline}"

    call_site_lines = _helper_call_site_lines(html, throwing_function_name)
    shared_helper_hint = ""
    if len(call_site_lines) >= 2:
        shared_helper_hint = (
            f" `{throwing_function_name}` is a shared helper called at lines "
            f"{', '.join(str(line) for line in call_site_lines)} -- the same defect very likely "
            "affects every one of them; fix them all in this round."
        )
    return (
        f"Line {call_site_line}: {headline} (thrown inside `{throwing_function_name}` "
        f"at line {throw_line}).{shared_helper_hint}"
    )
```

呼叫端（`_execute_scripts_smoke` 內）改成：

```python
                frames = _resolve_error_frames(message, html_start_line)
                errors.append(_format_execution_error(frames, script_index, first_line, html))
```

`_resolve_html_error_line` 若已無其他呼叫者就刪掉（連同它的測試一起調整）。

- [ ] **Step 4: 跑全 guard 測試**

Run: `cd deepagent-service && uv run pytest tests/test_html_guard.py -q`
Expected: 全綠。既有測試多半是 top-level 單 frame（訊息格式不變）；若有斷言 helper 內行號的，改成新格式——那正是這個 task 要修的行為。

### Task 8: 修復迴圈——退步偵測 ＋ 錯誤數持續下降就繼續

**Files:**
- Modify: `deepagent-service/app/main.py:63`（`GUARD_REPAIR_MAX_RUNS`）、`:309-327`（迴圈）
- Modify: `deepagent-service/tests/test_chat.py`

- [ ] **Step 1: 寫會紅的測試**

兩條，都在 `tests/test_chat.py`：

1. `test_guard_repair_continues_while_error_count_drops`——腳本讓每一輪修掉一個錯（用 `edit_file` 逐次補），第 3 輪才全綠。現行 `GUARD_REPAIR_MAX_RUNS = 2` 會在第 2 輪放棄 → 斷言事件流最後有 `DASHBOARD_HTML`（現在會紅）。
2. `test_guard_repair_stops_when_error_count_stops_dropping`——腳本讓修復輪不改任何東西（錯誤數持平），斷言只跑了 1 輪就停（用 `_RecordingChatModel` 或計算腳本被消耗的則數）。

- [ ] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_chat.py -k guard_repair -q`

- [ ] **Step 3: 實作**

`app/main.py:63` 換成：

```python
# dashboard.html 未過 check_dashboard_html 時的修復輪硬上限。實際輪數由「錯誤數是否還在
# 下降」決定(見下方迴圈)——sandbox 遇到第一個例外就停,連續三個執行期錯誤的檔案本來就
# 不可能兩輪收斂;但停滯或退步就立刻停,不讓模型繼續往同一個方向猜。
GUARD_REPAIR_MAX_RUNS = 5
```

迴圈改成：

```python
            repair_runs = 0
            previous_error_count = len(report.errors)
            while not report.ok and repair_runs < GUARD_REPAIR_MAX_RUNS:
                repair_runs += 1
                ...（repair_message / repair_bridge / _stream_agent_turn 原樣不動）...
                # 修復輪跑完 -- 重讀 dashboard.html、重新讀結果、重新 check。
                html = workspace.dashboard_path.read_text(encoding="utf-8")
                results = load_all_results(workspace)
                report = check_dashboard_html(html, set(results), results)
                if report.ok:
                    break
                if len(report.errors) >= previous_error_count:
                    logger.info(
                        "dashboard guard repair stalled session=%s round=%d errors=%d->%d",
                        request.sessionId,
                        repair_runs,
                        previous_error_count,
                        len(report.errors),
                    )
                    break
                previous_error_count = len(report.errors)
```

- [ ] **Step 4: 跑測試確認綠**

Run: `cd deepagent-service && uv run pytest tests/test_chat.py -q`

### Task 9: 6b——tab 切換必須在切換函式體內派發 resize

**Files:**
- Modify: `deepagent-service/app/engine/html_guard.py:701-732`（`_check_tab_conventions`）
- Modify: `deepagent-service/tests/test_html_guard.py`

**背景:** 既有檢查只認 `showTab(` / `id="panel-0"` / `role="tab"` 三個 marker，且 resize 片語只要**整份 HTML 任一處**出現就算過。session B 自寫的 `switchTab()` 兩者都躲過：marker 不命中，而且就算命中，只在別處有 `chart.resize()` 也會誤放。瀏覽器實測：不派發 resize 時 tab 2 的圖表永遠停在 ECharts 的 100px fallback。quickjs 沒有 CSS box model，0 寬容器在執行期檢查中結構上不可見，只能靜態檢查。

- [ ] **Step 1: 寫會紅的測試**

```python
def test_self_named_tab_switcher_without_resize_dispatch_fails() -> None:
    """模型自寫的 switchTab()（不叫 showTab）只切 CSS class、不派發 resize——
    hidden panel 裡的 ECharts 會永遠停在 100px fallback。"""
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="switchTab(1)" class="border-b-2">Tab 2</button>'
        '<div id="panel-0"></div><div id="panel-1"></div>'
        '<div id="chart"></div>'
        "<script>const data = window.__ERD_RESULTS__['q1'];\n"
        "function switchTab(index) {\n"
        "  document.getElementById('panel-' + index).classList.remove('hidden');\n"
        "}\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "window.addEventListener('resize', function () { chart.resize(); });\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert not report.ok
    assert any("resize" in error for error in report.errors), report.errors


def test_tab_switcher_with_resize_inside_the_function_passes() -> None:
    html = (
        '<html><head><script src="' + ALLOWED_SCRIPT_SRC_PREFIXES[0] + '"></script></head>'
        '<body><button onclick="switchTab(1)" class="border-b-2">Tab 2</button>'
        '<div id="panel-0"></div><div id="panel-1"></div><div id="chart"></div>'
        "<script>const data = window.__ERD_RESULTS__['q1'];\n"
        "function switchTab(index) {\n"
        "  document.getElementById('panel-' + index).classList.remove('hidden');\n"
        "  window.dispatchEvent(new Event('resize'));\n"
        "}\n"
        "const chart = echarts.init(document.getElementById('chart'), 'erd');\n"
        "chart.setOption({ tooltip: {}, series: [] });\n"
        "</script></body></html>"
    )
    report = check_dashboard_html(html, {"q1"})

    assert not any("resize" in error for error in report.errors), report.errors
```

- [ ] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_html_guard.py -k tab -q`
Expected: 第一條 FAIL（`resize` 片語在別處出現就被放過了）。

- [ ] **Step 3: 實作**

`html_guard.py`：

```python
# tab 結構的辨識訊號:skill 範本的 `showTab(`／`id="panel-0"`／`role="tab"`,加上模型自己
# 命名的切換函式(`onclick="...Tab("`)與多個 panel 容器。
_TAB_STRUCTURE_MARKERS: tuple[str, ...] = ("showTab(", 'id="panel-0"', 'role="tab"')
_TAB_ONCLICK_PATTERN = re.compile(r"""onclick\s*=\s*["'][^"']*Tab\s*\(""", re.IGNORECASE)
_PANEL_CONTAINER_PATTERN = re.compile(r"""id\s*=\s*["']panel-\d+["']""", re.IGNORECASE)
# 切換函式:名稱以 Tab 結尾的具名函式宣告。
_TAB_SWITCH_FUNCTION_PATTERN = re.compile(r"function\s+(\w*Tab)\s*\(")
_RESIZE_DISPATCH_SNIPPET = "dispatchEvent(new Event('resize'))"
_RESIZE_METHOD_SNIPPET = ".resize()"


def _has_tab_structure(html: str) -> bool:
    if any(marker in html for marker in _TAB_STRUCTURE_MARKERS):
        return True
    if _TAB_ONCLICK_PATTERN.search(html):
        return True
    return len(_PANEL_CONTAINER_PATTERN.findall(html)) >= 2


def _find_matching_close_brace(text: str, open_brace_index: int) -> int | None:
    """回傳 `text[open_brace_index]`(必為 `{`)對應的閉大括號 index;不平衡則回 None。
    對字串字面值中的大括號免疫。"""
    depth = 0
    quote_char: str | None = None
    index = open_brace_index
    while index < len(text):
        character = text[index]
        if quote_char is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote_char:
                quote_char = None
            index += 1
            continue
        if character in ("'", '"', "`"):
            quote_char = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _tab_switch_function_bodies(html: str) -> list[str]:
    """抽出所有名稱以 Tab 結尾的具名函式的函式體原始碼。"""
    bodies: list[str] = []
    for function_match in _TAB_SWITCH_FUNCTION_PATTERN.finditer(html):
        open_brace_index = html.find("{", function_match.end())
        if open_brace_index == -1:
            continue
        close_brace_index = _find_matching_close_brace(html, open_brace_index)
        if close_brace_index is None:
            continue
        bodies.append(html[open_brace_index : close_brace_index + 1])
    return bodies
```

`_check_tab_conventions` 的 resize 分支改成：

```python
    if not _has_tab_structure(html):
        return []

    errors: list[str] = []
    switch_function_bodies = _tab_switch_function_bodies(html)
    # 有具名切換函式時,resize 派發 MUST 在函式體內——寫在別處(例如只綁 window resize
    # listener)救不了 hidden panel 的 0 寬容器。找不到具名函式才退回整份 HTML 檢查。
    resize_search_targets = switch_function_bodies or [html]
    if not any(
        _RESIZE_DISPATCH_SNIPPET in target or _RESIZE_METHOD_SNIPPET in target
        for target in resize_search_targets
    ):
        errors.append(
            "The tab switch function never dispatches a resize -- ECharts instances created in "
            "a hidden panel measured a 0-width container and stay stuck at the 100px fallback. "
            "Add window.dispatchEvent(new Event('resize')) (or call chart.resize()) inside the "
            "switch function body. Use the skill's showTab template verbatim."
        )
```

（`_TABLER_STYLE_MARKER` 那條原樣保留。）

- [ ] **Step 4: 跑全測試 + lint**

Run: `cd deepagent-service && uv run pytest -q && uv run ruff check .`
Expected: 全綠、lint 淨。

- [ ] **Step 5: 確認 Java／前端零改動並 commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork"
git status --short
git add deepagent-service docs/superpowers/plans/2026-08-01-deepagent-trace-findings-fixes.md
git commit -m "fix(deepagent): converge guard repair loop and enforce tab resize dispatch"
```

---

## 收尾

- [ ] 整支分支交 opus 終審（`superpowers:requesting-code-review`）
- [ ] `gh pr create`，PR 描述 MUST 寫進 opus 終審結論
- [ ] **NEVER 自己 merge**——由使用者觸發
