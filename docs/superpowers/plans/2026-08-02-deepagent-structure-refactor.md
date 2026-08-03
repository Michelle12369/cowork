# deepagent-service 結構重構 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `app/main.py`（550 行）與 `app/engine/html_guard.py`（1517 行）依職責拆開，行為一字不改。

**Architecture:** 四個階段依序進行——先補特徵測試把安全網織到即將下刀的接縫上，再精簡 docstring，然後才搬遷。搬遷分兩支：`main.py` 拆成路由／schema／workflow／prompt 四塊（workflow 用單一 `ChatTurn` class 承載一輪的狀態），`html_guard.py` 拆成 package（`__init__.py` re-export 維持 import 路徑不變）。

**Tech Stack:** Python 3.12、FastAPI、pytest（`asyncio_mode = "auto"`）、uv、ruff。

**Spec:** `docs/superpowers/specs/2026-08-02-deepagent-structure-refactor-design.md`

## Global Constraints

- **零行為改變。** 不修任何 bug、不改任何錯誤訊息文字、不改事件順序、不改例外型別。
- **不動測試斷言。** 測試檔的 diff 只允許出現在 import 行與 monkeypatch 目標。任何斷言被修改 = 停下來回報。
- **基準**：commit `b8137a2`，`uv run pytest -q` = **201 passed**，`uv run ruff check .` 乾淨。每個 task 結束都必須維持這兩項。
- **工作目錄**：`deepagent-service/`。所有指令前綴 `uv run`，NEVER 用 `pip`。
- **凍結字串**（內容一字不可改，位置可搬）：`SYSTEM_PROMPT`、`REPAIR_SYSTEM_PROMPT`、`PREVIOUS_VERSION_SYSTEM_NOTE`、`DASHBOARD_EDIT_REJECTED_MESSAGE`、guard 修復訊息、skill gate 退貨訊息、`EMPTY_ANSWER_FALLBACK_MESSAGE`、`DASHBOARD_UPDATED_FALLBACK_MESSAGE`、`DASHBOARD_REJECTED_PREFIX`、`GRAPH_RECURSION_ERROR_MESSAGE`、`html_guard` 所有錯誤訊息、`DATA_FRAME_OPEN`／`DATA_FRAME_CLOSE`、`skills/**/*.md`。
- **命名**：變數／參數 NEVER 用 1–2 字元名稱；迴圈計數器用 `index`／`rowIndex` 等描述性名稱。
- **註解風格**：1–2 行寫「目的 + 做法」。NEVER 寫 spec 編號、commit hash、事故敘事。
- **定位用符號不用行號。** 本計畫引用位置一律給符號名，行號僅供參考——本專案的行號已在近期多次位移。

---

# 階段一（PR 0）：特徵測試

只新增測試，**零產品碼改動**。這些測試釘的是「今天的行為」，包含已知 bug 的錯誤行為；日後修 C3 時會有意識地改掉其中幾條。

### Task 1: lexer 狀態機特徵測試

`_find_script_end` 與 `_mask_strings_and_comments` 的 block comment 與 template literal 分支目前**完全沒有測試覆蓋**（coverage 報告：147-148、165、173、184-189、221-222、229-230、259-264）。這是全檔最怕被搬壞的碼——有狀態、index 算術微妙。

放在**新檔** `tests/test_js_lexer.py`，不動 `tests/test_html_guard.py`，這樣階段四搬遷時只有這個新檔的 import 需要改。

**Files:**
- Create: `tests/test_js_lexer.py`

**Interfaces:**
- Consumes: `app.engine.html_guard._find_script_end(html: str, start_index: int) -> int`、`app.engine.html_guard._mask_strings_and_comments(text: str) -> str`
- Produces: 無（純測試）

- [ ] **Step 1: 寫下四條特徵測試**

建立 `tests/test_js_lexer.py`：

```python
"""lexer 狀態機的特徵測試——釘住搬遷前的既有行為，供 html_guard 拆 package 時當安全網。

刻意獨立成檔（不放 test_html_guard.py）：這些測到的是私有函式，拆 package 後 import
路徑會變，隔離在此可讓 test_html_guard.py 的頂層 import 完全不動。
"""

from app.engine.html_guard import _find_script_end, _mask_strings_and_comments


def test_find_script_end_block_comment_containing_close_tag_is_not_a_terminator() -> None:
    html = "var a=1; /* fake </script> here */ var b=2;</script>tail"

    end_index = _find_script_end(html, 0)

    assert html[end_index : end_index + 9] == "</script>"


def test_find_script_end_unterminated_block_comment_returns_full_length() -> None:
    html = "var a=1; /* never closed </script>"

    assert _find_script_end(html, 0) == len(html)


def test_mask_strings_and_comments_blanks_block_comment_body_keeping_delimiters() -> None:
    text = "const a = 1; /* xyz */ const b = 2;"

    masked = _mask_strings_and_comments(text)

    assert masked == "const a = 1; /*     */ const b = 2;"
    assert len(masked) == len(text)


def test_mask_strings_and_comments_blanks_template_literal_body() -> None:
    text = "const s = `hello {x}`; const c = 3;"

    masked = _mask_strings_and_comments(text)

    assert masked == "const s = `         `; const c = 3;"
    assert len(masked) == len(text)
```

- [ ] **Step 2: 執行，確認全部通過**

Run: `uv run pytest tests/test_js_lexer.py -v`
Expected: 4 passed。

這裡**刻意不是紅燈**——特徵測試的目的是釘住既有行為，不是驅動新功能。若有任何一條失敗，代表我對現況的理解有誤，**停下來回報，不要改測試去迎合**。

- [ ] **Step 3: 確認覆蓋率破洞被補上**

Run: `uv run --with pytest-cov python -m pytest -q --cov=app.engine.html_guard --cov-report=term-missing 2>&1 | grep html_guard`
Expected: Missing 清單中 `147-148`、`184-189`、`221-222`、`259-264` 消失。

- [ ] **Step 4: 全套測試 + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 205 passed（201 + 4），ruff 乾淨。

- [ ] **Step 5: Commit**

```bash
git add tests/test_js_lexer.py
git commit -m "test(deepagent): 補 lexer 狀態機特徵測試"
```

---

### Task 2: `main.py` 未覆蓋分支特徵測試

coverage 顯示 `212-216`（heartbeat 重發）、`313-318`（首輪重試迴圈）、`409`（空回應兜底）沒有覆蓋。

**Files:**
- Modify: `tests/test_chat.py`（只新增測試函式，不動既有測試）

**Interfaces:**
- Consumes: `tests/test_chat.py` 既有的 `_post_chat(tmp_path, previous_dashboard_html=None)` 與 `_sse_events`、`tests/fake_model.py` 的 scripted model
- Produces: 無

- [ ] **Step 1: 先讀既有 fixture 慣例**

Run: `sed -n '1,60p' tests/test_chat.py && grep -n "scripted_flow" tests/conftest.py tests/test_chat.py | head -20`

理解 `scripted_flow` 這類 fixture 怎麼組腳本，新測試要沿用同一套，NEVER 自己另造一套 mock。

- [ ] **Step 2: 寫「首輪空回應重試」測試**

在 `tests/test_chat.py` 末尾新增。以下三點沿用該檔既有 fixture 的慣例，**照抄，不要自創**：
`AGENT_WORKSPACE_ROOT` 必須 setenv（否則會去寫 `/data` 而 `OSError: Read-only file system`）、
`ScriptedChatModel` 的訊息清單是**位置參數**、patch 目標是 `main_module.build_model`。

```python
async def test_chat_empty_first_round_retries_and_uses_second_round_answer(
    tmp_path, monkeypatch
) -> None:
    # 首輪空回應(無文字、無工具啟動)時 chat() 會重新 invoke 同一份 run_input。
    # 釘住「重試確實發生」與「最終 ANSWER 取自重試那一輪」。
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel([AIMessage(content=""), AIMessage(content="重試後的結論。")])
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)

    events = await _post_chat(tmp_path)

    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert answer_events[-1]["text"] == "重試後的結論。"
```

> 已實測通過：ANSWER 為 `重試後的結論。`。`ScriptedChatModel`、`AIMessage`、`_post_chat`
> 在 `tests/test_chat.py` 頂層已 import，不需另外 import。

- [ ] **Step 3: 執行，確認通過**

Run: `uv run pytest tests/test_chat.py -k empty_first_round -v`
Expected: PASS。若失敗，先確認 fixture 慣例是否照抄，**不要改產品碼**。

- [ ] **Step 4: 寫「空回應兜底文案」測試**

`ScriptedChatModel` 腳本耗盡時**回空 content 而非拋錯**（見該類別 docstring），
所以只需給**一則**空訊息，首輪與兩輪重試都會拿到空回應：

```python
async def test_chat_no_text_and_no_dashboard_falls_back_to_empty_answer_message(
    tmp_path, monkeypatch
) -> None:
    # 首輪與 FIRST_ROUND_RETRY_MAX_RUNS 兩輪重試都空、且本輪沒發出 DASHBOARD_HTML
    # → ANSWER 走 EMPTY_ANSWER_FALLBACK_MESSAGE。
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    scripted = ScriptedChatModel([AIMessage(content="")])
    monkeypatch.setattr(main_module, "build_model", lambda: scripted)

    events = await _post_chat(tmp_path)

    answer_events = [event for event in events if event["type"] == "ANSWER"]
    assert answer_events[-1]["text"] == main_module.EMPTY_ANSWER_FALLBACK_MESSAGE
```

> 已實測通過：ANSWER 為 `本輪已完成分析步驟,但未產生文字說明——請再問一次或換個說法。`

- [ ] **Step 5: 執行兩條新測試**

Run: `uv run pytest tests/test_chat.py -k "empty_first_round or no_text_and_no_dashboard" -v`
Expected: 2 passed。

- [ ] **Step 6: 全套測試 + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 207 passed，ruff 乾淨。

- [ ] **Step 7: Commit**

```bash
git add tests/test_chat.py
git commit -m "test(deepagent): 補首輪重試與空回應兜底的特徵測試"
```

---

### Task 3: async generator 早退等價性測試（最重要的一條）

**這是整個重構風險最高的一點。** 現在 ERROR 早退是 `chat()` 裡的 `return`，直接結束單一函式；拆完之後變成 caller `return` → `turn.finalize()` generator 被 `aclose()` → 於 yield 點拋 `GeneratorExit`。淨效果應該相同，但路徑不同。

**Files:**
- Modify: `tests/test_chat.py`

**Interfaces:**
- Consumes: 既有 `_post_chat`／`_sse_events`
- Produces: 無

- [ ] **Step 1: 找出既有的 ERROR 路徑測試**

Run: `grep -n "ERROR" tests/test_chat.py | head -20`

先確認有沒有既有測試已經涵蓋，避免重複。若已有「ERROR 之後沒有 ANSWER」的斷言，本 task 只需補「teardown 有跑」那一半。

- [ ] **Step 2: 先在 `tests/fake_model.py` 新增 `FailingChatModel`**

`tests/fake_model.py` 目前只有 `ScriptedChatModel`（已確認），需要一個一被呼叫就拋例外的假模型。
**`bind_tools` 必須實作**——`create_deep_agent` 會對 model 呼叫它：

```python
class FailingChatModel(BaseChatModel):
    """non-bean: instantiate per test. 一被呼叫就拋例外,用來驅動 /chat 的 ERROR 路徑。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FailingChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError("scripted model failure")

    @property
    def _llm_type(self) -> str:
        return "failing"
```

- [ ] **Step 3: 寫「ERROR 之後不再有任何事件，且 duckdb 連線有關閉」**

**不要用 spy 包 `connection.close`**——`DuckDBPyConnection.close` 是唯讀屬性，
指派會拋 `AttributeError: object attribute 'close' is read-only`（已實測）。
改成直接觀察**結果狀態**：對已關閉的連線下查詢會拋 `duckdb.ConnectionException`。
這也是更好的測法——斷言結果而非攔截機制。

```python
async def test_chat_error_terminates_stream_and_still_closes_connection(
    tmp_path, monkeypatch
) -> None:
    # 釘住兩件事,重構把 chat() 拆成 ChatTurn 之後必須仍成立：
    #   (a) ERROR 是本輪最後一個事件——之後不再有 ANSWER 或任何其他事件
    #   (b) 早退仍會執行 teardown——duckdb 連線確實被關閉
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    opened_connections: list[object] = []
    original_open = main_module.open_locked_connection

    def tracking_open(sources):
        connection = original_open(sources)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(main_module, "open_locked_connection", tracking_open)
    monkeypatch.setattr(main_module, "build_model", lambda: FailingChatModel())

    events = await _post_chat(tmp_path)

    error_indexes = [index for index, event in enumerate(events) if event["type"] == "ERROR"]
    assert error_indexes, "本測試需要一個會觸發 ERROR 的模型"
    assert error_indexes[-1] == len(events) - 1, "ERROR 之後不應再有任何事件"

    assert opened_connections, "本輪應該開過一個 duckdb 連線"
    with pytest.raises(duckdb.ConnectionException):
        opened_connections[0].execute("SELECT 1")
```

`tests/test_chat.py` 需補 `import duckdb`、`import pytest`（若尚未 import）
與 `from tests.fake_model import FailingChatModel`。

> 已實測通過：事件序列為 `['ERROR']`（ERROR 是唯一也是最後一個事件），
> 對已關閉連線查詢拋 `ConnectionException`。

- [ ] **Step 4: 執行**

Run: `uv run pytest tests/test_chat.py -k error_terminates_stream -v`
Expected: PASS。

若 `pytest.raises(duckdb.ConnectionException)` 沒有被觸發，**不要改產品碼**——
那代表現況真的沒關閉連線，屬於 finding 而非重構問題，停下來回報。

- [ ] **Step 5: 全套測試 + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 208 passed，ruff 乾淨。

- [ ] **Step 6: Commit**

```bash
git add tests/test_chat.py tests/fake_model.py
git commit -m "test(deepagent): 釘住 ERROR 早退的事件終止與資源清理"
```

---

# 階段二（PR 0.5）：docstring 精簡

只改註解，**零程式碼改動**。驗收極乾淨：`git diff` 應該只有註解／docstring 行，測試不改一個字元全綠。

**砍**：事故敘事、重述程式碼在做什麼、同一論點反覆論證、「為什麼不選另一個做法」的長篇。
**留（壓成 1–2 行）**：從程式碼讀不出來的外部事實——見下方逐檔清單。

### Task 4: `app/engine/` 的 docstring 精簡

**Files:**
- Modify: `app/engine/html_guard.py`、`app/engine/results.py`、`app/engine/workspace.py`、`app/engine/duck.py`、`app/engine/theme.py`

- [ ] **Step 1: 列出目標**

Run:
```bash
uv run python -c "
import ast, pathlib
for p in sorted(pathlib.Path('app/engine').rglob('*.py')):
    tree = ast.parse(p.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc and len(doc.split(chr(10))) >= 6:
                print(len(doc.split(chr(10))), p, getattr(node, 'name', '<module>'))
"
```

- [ ] **Step 2: 逐個精簡，這些知識必須保留**

| 位置 | 壓縮後必須仍講到 |
|---|---|
| `html_guard` module docstring | engine 層 stdlib only；兩層 JS 檢查的存在 |
| `_find_script_end` | port 自 `JsSyntaxValidator.findScriptEnd`（MUST-sync）；找不到終止符回傳 `len` |
| `_mask_strings_and_comments` | 遮罩後 index 與行號與原文一比一對應（呼叫端因此不需校正行號） |
| `_execute_scripts_smoke` | ReferenceError stub 後重放先前 block 的行為 |
| `check_dashboard_html` | 規則之間互不 fail-fast，全部違規一次收集 |
| `results.record_query` | 為何 `record_query` 內再正規化一次（對外 API，不假設呼叫端已做） |
| `results.strip_injected_blocks` | 冪等；只認帶 id 的區塊 |
| `results.build_results_script` | 為何逃脫每個 `<`（`<!--` 進 escaped 態後 `<script` 可讓 `</script>` 失效） |
| `theme.ERD_THEME_SCRIPT` 檔頭 | MUST-sync ↔ `head-inject.vm`，槽位順序 NEVER 重排 |

- [ ] **Step 3: 確認零程式碼改動**

Run: `git diff -U0 app/engine/ | grep '^[+-]' | grep -v '^[+-][+-]' | grep -v '^\s*[+-]\s*#' | grep -v '"""'`

Expected: 只剩 docstring 內文行。若出現任何非註解的程式碼行，**還原那一處**。

- [ ] **Step 4: 測試不改一字全綠**

Run: `git diff --stat tests/ && uv run pytest -q && uv run ruff check .`
Expected: `tests/` 零改動，208 passed，ruff 乾淨。

- [ ] **Step 5: Commit**

```bash
git add app/engine/
git commit -m "docs(deepagent): 精簡 engine 層 docstring"
```

---

### Task 5: `app/agent/` 與 `app/main.py` 的 docstring 精簡

**Files:**
- Modify: `app/main.py`、`app/agent/graph.py`、`app/agent/middleware.py`、`app/agent/tools/recording.py`、`app/agent/tools/data.py`、`app/agent/events.py`、`app/agent/session_state.py`、`app/agent/auth.py`

- [ ] **Step 1: 逐個精簡，這些知識必須保留**

| 位置 | 壓縮後必須仍講到 |
|---|---|
| `ToolResultRecorder` | LangChain 對字面名為 `callbacks` 的參數特殊處理，注入 `run_manager.get_child()`，其 `parent_run_id` 即工具的 `run_id` |
| `main.py` `run_input` 註解 | `add_messages` 依 `message.id` 去重，重建 HumanMessage 會 double-append |
| `main.py` `previousDashboardHtml` 區塊 | MUST 在 mtime 快照之前 |
| `_guard_repair_should_stop` | 為什麼比集合不比數量（guard 會 clamp 錯誤列表）；整份重寫下數量上升不代表退步 |
| `DashboardOverwriteBackend` | 為何 write 先 unlink；為何 edit 一律退貨；`excluded_tools` 是 defense-in-depth |
| `SerializedToolCallsMiddleware` | ToolNode 預設 `asyncio.gather` 併發；檔案工具是無鎖讀改寫 |
| `DashboardSkillGateMiddleware._unread_required_paths` | 只採計嚴格早於當前 tool call 所在訊息的 read；同一則訊息可批次吐出 read+write |
| `WiringManifestMiddleware` | 為何每次 model call 重建而非每輪一次 |
| `session_state` | process 生命週期單例；重啟丟失是已知且接受的降級 |
| `auth.TokenExchangeAuth` | last-write-wins，不做交換去重 |
| `data.py` module docstring | 三個工具共用一把 `connection_lock`；拿號與落檔須同一臨界區 |

- [ ] **Step 2: 確認凍結字串一字未改**

Run:
```bash
git diff --color-words=. app/ | grep -E "SYSTEM_PROMPT|REPAIR_SYSTEM_PROMPT|PREVIOUS_VERSION_SYSTEM_NOTE|DASHBOARD_EDIT_REJECTED_MESSAGE|EMPTY_ANSWER_FALLBACK|DASHBOARD_UPDATED_FALLBACK|DASHBOARD_REJECTED_PREFIX|GRAPH_RECURSION_ERROR_MESSAGE|DATA_FRAME_"
```
Expected: **無輸出**。有任何輸出代表動到凍結字串，還原。

- [ ] **Step 3: 確認零程式碼改動**

Run: `git diff -U0 app/ | grep '^[+-]' | grep -v '^[+-][+-]' | grep -v '^\s*[+-]\s*#' | grep -v '"""'`
Expected: 只剩 docstring 內文行。

- [ ] **Step 4: 測試不改一字全綠**

Run: `git diff --stat tests/ && uv run pytest -q && uv run ruff check .`
Expected: `tests/` 零改動，208 passed，ruff 乾淨。

- [ ] **Step 5: 量化結果**

Run:
```bash
uv run python -c "
import ast, pathlib
total = 0
for p in sorted(pathlib.Path('app').rglob('*.py')):
    tree = ast.parse(p.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc: total += len(doc.split(chr(10)))
print('docstring 總行數:', total)
"
```
基準是 519 行。約 250 是預期落點，但**不為湊數字砍掉「必須保留」清單裡的東西**——落在 300 也可接受。

- [ ] **Step 6: Commit**

```bash
git add app/
git commit -m "docs(deepagent): 精簡 agent 層與 main.py 的 docstring"
```

---

# 階段三（PR 1）：`main.py` 拆分

純搬遷。測試 diff 只允許 import 行與 monkeypatch 目標。

### Task 6: 抽出對外介面定義

**Files:**
- Create: `app/api/__init__.py`、`app/api/schemas.py`
- Modify: `app/main.py`、`tests/test_chat.py`

**Interfaces:**
- Consumes: 無（本 task 只搬既有 class，不依賴前面任何 task 的產出）
- Produces: `app.api.schemas.HistoryItem`、`SourceItem`、`ChatRequest`、`RepairErrorItem`、`RepairRequest`（欄位與現況完全相同）

- [ ] **Step 1: 建立 package 與 schema 模組**

`app/api/__init__.py` 留空。`app/api/schemas.py` 放五個 Pydantic model，**逐字搬移**（含 `previousDashboardHtml` 上方那段註解）：

```python
"""`/chat` 與 `/repair` 的對外請求介面定義。"""

from pydantic import BaseModel


class HistoryItem(BaseModel):
    role: str
    text: str


class SourceItem(BaseModel):
    alias: str
    path: str
    fileType: str


class ChatRequest(BaseModel):
    sessionId: str
    userId: str
    message: str
    history: list[HistoryItem] = []
    sources: list[SourceItem] = []
    previousDashboardHtml: str | None = None


class RepairErrorItem(BaseModel):
    message: str


class RepairRequest(BaseModel):
    sessionId: str
    userId: str
    html: str
    errors: list[RepairErrorItem]
```

> `ChatRequest.previousDashboardHtml` 上方的既有註解要一併帶過來（階段二精簡後的版本）。

- [ ] **Step 2: `main.py` 改為 import**

刪除 `main.py` 裡那五個 class，改成：

```python
from app.api.schemas import ChatRequest, HistoryItem, RepairErrorItem, RepairRequest, SourceItem
```

> `HistoryItem`／`SourceItem` 即使 `main.py` 內未直接使用也要 import——測試透過
> `main_module.HistoryItem` 取用。（階段三結束時可再評估是否移除。）

- [ ] **Step 3: 執行測試**

Run: `uv run pytest -q`
Expected: 208 passed。若 `main_module.ChatRequest` 相關測試失敗，代表 Step 2 的 re-import 漏了。

- [ ] **Step 4: lint**

Run: `uv run ruff check .`
Expected: 乾淨。若報 `F401 unused import`，改成在 `main.py` 明確標註用途或調整測試 import 目標（**測試只能改 import 行**）。

- [ ] **Step 5: Commit**

```bash
git add app/api/ app/main.py tests/
git commit -m "refactor(deepagent): 抽出 app/api/schemas.py"
```

---

### Task 7: 抽出 prompt 樣板與 HTML fence 抽取

**Files:**
- Create: `app/engine/html_extract.py`
- Modify: `app/agent/prompts.py`、`app/main.py`

**Interfaces:**
- Consumes: 無
- Produces: `app.engine.html_extract.extract_html_block(model_response_text: str) -> str`；`app.agent.prompts.REPAIR_SYSTEM_PROMPT`、`PREVIOUS_VERSION_SYSTEM_NOTE`、`build_repair_user_message(html: str, error_messages: list[str]) -> str`、`build_repair_retry_user_message(previous_html: str, guard_errors: list[str]) -> str`

- [ ] **Step 1: 建立 `app/engine/html_extract.py`**

把 `_HTML_FENCE_PATTERN` 與 `_extract_html_block` 搬過來，函式改為公開名（跨模組使用）：

```python
"""模型回應中的 ```html fenced block 抽取。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

import re

_HTML_FENCE_PATTERN = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL)


def extract_html_block(model_response_text: str) -> str:
    """取出模型的 ```html 區塊;沒有 fence 時退回整段 strip 後的原文——never raise,
    讓 check_dashboard_html 的結構檢查去確定性退件。"""
    fence_match = _HTML_FENCE_PATTERN.search(model_response_text)
    return fence_match.group(1).strip() if fence_match else model_response_text.strip()
```

- [ ] **Step 2: prompt 樣板搬進 `app/agent/prompts.py`**

把 `REPAIR_SYSTEM_PROMPT`、`PREVIOUS_VERSION_SYSTEM_NOTE`、`REPAIR_MAX_BROWSER_ERRORS`、
`_build_repair_user_message`、`_build_repair_retry_user_message` 搬到 `prompts.py`，
後兩者改公開名 `build_repair_user_message`／`build_repair_retry_user_message`。

**兩個 prompt 字串一字不可改**（Global Constraints 的凍結清單）。

`build_repair_user_message` 的參數型別要脫離 `RepairErrorItem`——改收 `list[str]`，
呼叫端負責取 `.message`。這樣 `prompts.py` 不必 import `app.api.schemas`：

```python
def build_repair_user_message(html: str, error_messages: list[str]) -> str:
    capped_messages = error_messages[:REPAIR_MAX_BROWSER_ERRORS]
    error_lines = "\n".join(f"- {message}" for message in capped_messages)
    return (
        "The following self-contained HTML dashboard produced these runtime JavaScript errors "
        f"in the browser:\n\n{error_lines}\n\nHTML:\n{html}"
    )
```

> 產出的字串必須與現況**逐字相同**。改的只有參數型別。

- [ ] **Step 3: `main.py` 改用新位置**

`main.py` 的 `/repair` 呼叫點改成
`build_repair_user_message(clean_html, [error.message for error in request.errors])`。

- [ ] **Step 4: 執行測試**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 208 passed，ruff 乾淨。

若 `tests/test_repair.py` 有斷言 prompt 文字，那些斷言**不可改**——它們正是凍結字串的守門員。

- [ ] **Step 5: Commit**

```bash
git add app/engine/html_extract.py app/agent/prompts.py app/main.py
git commit -m "refactor(deepagent): 抽出 prompt 樣板與 HTML fence 抽取"
```

---

### Task 8: 抽出 `/repair` workflow

**Files:**
- Create: `app/agent/repair_flow.py`
- Modify: `app/main.py`、`tests/test_repair.py`（僅 import／patch 目標）

**Interfaces:**
- Consumes（全部來自 Task 6／Task 7）：
  - `app.api.schemas.RepairRequest`
  - `app.engine.html_extract.extract_html_block(model_response_text: str) -> str`
  - `app.agent.prompts.REPAIR_SYSTEM_PROMPT`
  - `app.agent.prompts.build_repair_user_message(html: str, error_messages: list[str]) -> str`
  - `app.agent.prompts.build_repair_retry_user_message(previous_html: str, guard_errors: list[str]) -> str`
  - 既有：`app.agent.graph.build_model`、`app.engine.html_guard.check_dashboard_html`、`app.engine.results.strip_injected_blocks`／`load_all_results`／`referenced_query_ids`／`inject_results`、`app.engine.theme.inject_theme`、`app.engine.workspace.prepare_workspace`
- Produces: `app.agent.repair_flow.run_repair(request: RepairRequest) -> RepairOutcome`
- `RepairOutcome` 是 frozen dataclass：`html: str | None`、`guard_errors: list[str]`、`model_call_failed: bool`

- [ ] **Step 1: 建立 `app/agent/repair_flow.py`**

搬移 `REPAIR_GUARD_RETRY_MAX_RUNS`、`REPAIR_MODEL_CALL_TIMEOUT_SECONDS`、`_invoke_repair_model`，
並把 `/repair` 端點裡除了「組 HTTP 回應」以外的全部邏輯搬進 `run_repair`。

回傳值用 dataclass 而非直接回 `JSONResponse`——workflow 層不該知道 HTTP：

```python
@dataclass(frozen=True)
class RepairOutcome:
    html: str | None
    guard_errors: list[str]
    model_call_failed: bool
```

三種結果對應現況：模型呼叫失敗 → `model_call_failed=True`；guard 未過 →
`guard_errors` 非空；成功 → `html` 有值。

- [ ] **Step 2: `main.py` 的 `/repair` 變成薄端點**

```python
@app.post("/repair")
async def repair(request: Annotated[RepairRequest, Body()]) -> JSONResponse:
    logger.info("repair request sessionId=%s errorCount=%d", request.sessionId, len(request.errors))
    outcome = await run_repair(request)
    if outcome.model_call_failed:
        return JSONResponse(status_code=502, content={"error": "repair model call failed"})
    if outcome.guard_errors:
        return JSONResponse(status_code=422, content={"errors": outcome.guard_errors})
    return JSONResponse(status_code=200, content={"html": outcome.html})
```

> **狀態碼與 body 形狀必須與現況完全相同**：502 的 key 是 `error`、422 是 `errors`、200 是 `html`。
> 現況的 logger 呼叫位置與格式一併保留。

- [ ] **Step 3: 執行測試**

Run: `uv run pytest tests/test_repair.py -v`
Expected: 全部 PASS。

`tests/test_repair.py` 若 monkeypatch `main_module.build_model`，需改指向 `repair_flow.build_model`
——這屬於允許的 patch 目標調整。

- [ ] **Step 4: 全套 + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 208 passed，ruff 乾淨。

- [ ] **Step 5: Commit**

```bash
git add app/agent/repair_flow.py app/main.py tests/test_repair.py
git commit -m "refactor(deepagent): 抽出 /repair workflow"
```

---

### Task 9: 建立 `ChatTurn`（本階段最大一步）

**Files:**
- Create: `app/agent/chat_turn.py`
- Modify: `app/main.py`、`tests/test_chat.py`（僅 import／patch 目標）

**Interfaces:**
- Consumes: `app.api.schemas.ChatRequest`（Task 6）；既有的 `app.agent.events.EventBridge`、`app.agent.graph.build_agent`／`build_model`、`app.agent.tools.recording.ToolResultRecorder`、`app.engine.duck.Source`／`open_locked_connection`、`app.engine.workspace.prepare_workspace`／`write_sources_doc`／`stage_skills`／`builtin_skills_dir`、`app.engine.html_guard.check_dashboard_html`、`app.engine.results.*`、`app.engine.theme.inject_theme`
- Produces:
  - `app.agent.chat_turn.ChatTurn(request: ChatRequest)`，支援 `async with`
  - `ChatTurn.stream() -> AsyncIterable[dict]`
  - `ChatTurn.finalize() -> AsyncIterable[dict]`
  - module-level：`stream_agent_turn(agent, run_input, run_config, bridge) -> AsyncIterable[dict]`、`_is_transient_stream_error`、`_guard_repair_should_stop`、`_seed_messages`、`_build_callbacks`
  - module-level 常數：`HEARTBEAT_INTERVAL_SECONDS`、`AGENT_RECURSION_LIMIT`、`GUARD_REPAIR_MAX_RUNS`、`STREAM_RETRY_MAX_RUNS`、`FIRST_ROUND_RETRY_MAX_RUNS`、`GRAPH_RECURSION_ERROR_MESSAGE`、`EMPTY_ANSWER_FALLBACK_MESSAGE`、`DASHBOARD_UPDATED_FALLBACK_MESSAGE`、`DASHBOARD_REJECTED_PREFIX`

- [ ] **Step 1: 先建立骨架與 module-level 內容**

把下列項目從 `main.py` **逐字搬到** `app/agent/chat_turn.py`：常數九項、`_is_transient_stream_error`、
`_guard_repair_should_stop`、`_seed_messages`、`_build_callbacks`、`_stream_agent_turn`（更名為
`stream_agent_turn`，去掉底線——它現在跨模組使用）。

- [ ] **Step 2: 寫 `ChatTurn` 的生命週期**

`__aenter__` 承接現況 `chat()` 從 `prepare_workspace(...)` 到 `dashboard_mtime_before = ...` 的整段，
**順序一字不動**（`previousDashboardHtml` 寫回 MUST 在 mtime 快照之前）：

```python
class ChatTurn:
    """non-bean: instantiate per /chat request."""

    def __init__(self, request: ChatRequest) -> None:
        self._request = request
        self._connection = None
        self.bridge: EventBridge | None = None

    async def __aenter__(self) -> "ChatTurn":
        request = self._request
        self._workspace = prepare_workspace(request.userId, request.sessionId)
        write_sources_doc(
            self._workspace, [(item.alias, item.fileType) for item in request.sources]
        )
        staged_skill_paths = stage_skills(
            self._workspace, builtin_skills_dir(), self._workspace.root.parents[1] / "skills"
        )
        self._connection = open_locked_connection(
            [Source(item.alias, item.path, item.fileType) for item in request.sources]
        )
        self._recorder = ToolResultRecorder()
        self._agent = build_agent(
            build_model(), self._connection, self._workspace, staged_skill_paths, self._recorder
        )
        self._run_config = {
            "configurable": {"thread_id": request.sessionId},
            "recursion_limit": AGENT_RECURSION_LIMIT,
            "callbacks": _build_callbacks(),
        }
        self._run_input = {"messages": _seed_messages(request)}
        if request.previousDashboardHtml is not None:
            self._workspace.dashboard_path.write_text(
                strip_injected_blocks(request.previousDashboardHtml), encoding="utf-8"
            )
        self._dashboard_mtime_before = (
            self._workspace.dashboard_path.stat().st_mtime
            if self._workspace.dashboard_path.exists()
            else None
        )
        return self

    async def __aexit__(self, *exception_info: object) -> None:
        if self._connection is not None:
            self._connection.close()
```

> **重要（本段初版寫錯，已更正）**：現況的 `connection = open_locked_connection(...)` 在 `try:`
> **之外**，`try/finally` 只包住其後的內容。改成 `__aenter__`／`__aexit__` 之後：
>
> - `open_locked_connection` 自己拋例外 → `__aexit__` 不會被呼叫，**與現況相同**（現況也不進 finally）
> - `open_locked_connection` **之後**的任何一步拋例外（`build_model`／`build_agent`／
>   `_seed_messages`／`write_text`／`stat`）→ 現況會進 finally 關閉連線，
>   但 **`async with` 不會**：Python 的 context manager 協定只在 `__aenter__`
>   **成功返回**後才呼叫 `__aexit__`，`self._connection` 有沒有被賦值無關。**這是連線洩漏。**
>
> 初版此處誤寫成「新版由 `async with` 的語意保證同樣關閉」，實測為假。
> 因此 `__aenter__` 內從取得連線到 `return self` 之間 MUST 包一層
> `try / except BaseException: self._connection.close(); raise`——用 `BaseException` 而非
> `Exception`，因為舊的 `finally` 對 `CancelledError` 同樣會執行，而 `/chat` 在 client
> 斷線時例行被取消。
>
> Task 3 的測試**守不住這一條**：它讓錯誤發生在串流階段（`__aenter__` 早已成功返回），
> 涵蓋不到「`__aenter__` 內、取得連線之後才失敗」。需另補一條 regression 測試。

- [ ] **Step 3: 寫 `stream()`**

承接現況「第一次 `_stream_agent_turn` + 首輪重試迴圈」，`self.bridge` 保存最終那個：

```python
    async def stream(self) -> AsyncIterable[dict]:
        self.bridge = EventBridge(self._recorder)
        async for wire_event in stream_agent_turn(
            self._agent, self._run_input, self._run_config, self.bridge
        ):
            yield wire_event
            if wire_event["type"] == "ERROR":
                return
        retry_runs = 0
        while (
            not self.bridge.final_answer().strip()
            and not self.bridge.tool_started
            and retry_runs < FIRST_ROUND_RETRY_MAX_RUNS
        ):
            retry_runs += 1
            self.bridge = EventBridge(self._recorder)
            async for wire_event in stream_agent_turn(
                self._agent, self._run_input, self._run_config, self.bridge
            ):
                yield wire_event
                if wire_event["type"] == "ERROR":
                    return
```

- [ ] **Step 4: 寫 `finalize()`**

承接現況從 `dashboard_html_emitted = False` 到 `yield ... ANSWER` 的整段。
**guard 修復迴圈裡的 `repair_bridge` 維持獨立**，且 ANSWER 仍讀 `self.bridge`（pre-repair 那個）
——現況的註解已說明理由，搬過來時保留。

- [ ] **Step 5: `main.py` 的 `/chat` 變成薄端點**

```python
@app.post("/chat", response_class=EventSourceResponse)
async def chat(request: Annotated[ChatRequest, Body()]) -> AsyncIterable[ServerSentEvent]:
    logger.info(
        "chat request sessionId=%s message_length=%d source_count=%d",
        request.sessionId,
        len(request.message),
        len(request.sources),
    )
    async with ChatTurn(request) as turn:
        async for wire_event in turn.stream():
            yield ServerSentEvent(data=wire_event)
            if wire_event["type"] == "ERROR":
                return
        async for wire_event in turn.finalize():
            yield ServerSentEvent(data=wire_event)
            if wire_event["type"] == "ERROR":
                return
```

- [ ] **Step 6: 更新測試的 patch 目標（只改這些行）**

已知 22 處引用、8 個符號：

| 符號 | 引用數 | 新目標 |
|---|---|---|
| `_is_transient_stream_error` | 6 | `chat_turn._is_transient_stream_error` |
| `_guard_repair_should_stop` | 4 | `chat_turn._guard_repair_should_stop` |
| `_seed_messages` | 3 | `chat_turn._seed_messages` |
| `_stream_agent_turn` | 2 | `chat_turn.stream_agent_turn`（**注意更名**） |
| `DASHBOARD_REJECTED_PREFIX` | 2 | `chat_turn.DASHBOARD_REJECTED_PREFIX` |
| `DASHBOARD_UPDATED_FALLBACK_MESSAGE` | 1 | `chat_turn.DASHBOARD_UPDATED_FALLBACK_MESSAGE` |
| `strip_injected_blocks`（spy） | 1 | `chat_turn.strip_injected_blocks` |
| `main_module.app` | 3 | 不動 |

`main_module.build_model` 若有被 patch，也要改指向 `chat_turn.build_model`。

**`strip_injected_blocks` 那條特別注意**（`tests/test_chat.py` 的
`test_chat_previous_dashboard_html_becomes_editing_base`）：它斷言
`len(entry_rebuild_calls) == 1`。patch 目標若沒改對，會變成 0 而失敗——這是預期的保護機制，
**改 patch 目標，不要改斷言**。

- [ ] **Step 7: 執行全套測試**

Run: `uv run pytest -q`
Expected: 208 passed。

任何測試變紅且**不是** patch 目標問題 → 搬遷改到行為了，停下來回報。

- [ ] **Step 8: 確認測試 diff 只有 import／patch 行**

Run: `git diff tests/ | grep '^[+-]' | grep -v '^[+-][+-]'`
Expected: 只有 import 行與 `monkeypatch.setattr(...)` 的第一個參數改動。**沒有任何 assert 行**。

- [ ] **Step 9: lint 與行數檢查**

Run: `uv run ruff check . && wc -l app/main.py app/agent/chat_turn.py`
Expected: ruff 乾淨；`main.py` ≤ 100 行。

- [ ] **Step 10: Commit**

```bash
git add app/agent/chat_turn.py app/main.py tests/test_chat.py
git commit -m "refactor(deepagent): chat() 拆成 ChatTurn，main.py 只剩路由"
```

---

# 階段四（PR 2）：`html_guard.py` 拆分

拆成 package，`__init__.py` re-export 讓 `tests/test_html_guard.py` 與
`tests/test_dashboard_skill.py` 的頂層 import **完全不動**。

> **獨立性**：階段四與階段三互不相依，兩者都只依賴階段一與階段二。順序可對調。

### Task 10: 建立 package 骨架與最底層模組

**Files:**
- Create: `app/engine/html_guard/__init__.py`、`report.py`、`js_runtime.py`
- Delete: `app/engine/html_guard.py`（內容移入 package）

**Interfaces:**
- Consumes: 無
- Produces: `js_runtime.QUICKJS_AVAILABLE: bool`、`js_runtime.quickjs`（模組或 `None`）；`report.GuardReport`、`report.HTML_MAX_BYTES`、`report.check_structure(html: str, errors: list[str]) -> None`、`report.check_size(html: str, errors: list[str]) -> None`

- [ ] **Step 1: 建立 `js_runtime.py`（quickjs 可用性的唯一擁有者）**

```python
"""quickjs 選配相依的可用性探測——本 package 唯一的擁有者。

消費端 MUST 以模組屬性存取（`js_runtime.QUICKJS_AVAILABLE`），NEVER 用
`from .js_runtime import QUICKJS_AVAILABLE`——後者在 import 期就把值快照下來，
測試的 monkeypatch 會靜默失效。
"""

import logging

logger = logging.getLogger(__name__)

try:
    import quickjs

    QUICKJS_AVAILABLE = True
except ImportError:  # pragma: no cover
    quickjs = None
    QUICKJS_AVAILABLE = False
```

- [ ] **Step 2: 建立 `report.py`**

搬 `GuardReport`、`HTML_MAX_BYTES`、`_check_structure`、`_check_size`，後兩者改公開名
`check_structure`／`check_size`。**錯誤訊息字串一字不可改**（凍結清單）。

- [ ] **Step 3: 其餘內容暫時整塊搬進 `__init__.py`**

先讓 package 能動：把 `html_guard.py` 的剩餘內容全部搬進 `__init__.py`，
從 `report.py`／`js_runtime.py` import 已抽出的部分，並把 `_QUICKJS_AVAILABLE` 的所有使用點
改成 `js_runtime.QUICKJS_AVAILABLE`。

- [ ] **Step 4: 更新 3 處 monkeypatch 目標**

`tests/test_html_guard.py` 的三處
`monkeypatch.setattr(html_guard, "_QUICKJS_AVAILABLE", False)` 改為：

```python
from app.engine.html_guard import js_runtime

monkeypatch.setattr(js_runtime, "QUICKJS_AVAILABLE", False)
```

- [ ] **Step 5: 執行測試**

Run: `uv run pytest -q`
Expected: 208 passed。

那三個 patch 若沒改對會**大聲失敗**（quickjs 實際可用 → 語法錯誤被抓到 →
「應該跳過檢查」的斷言不成立），不會靜默通過。

- [ ] **Step 6: 確認頂層 import 零改動**

Run: `git diff tests/test_html_guard.py | grep '^[+-]' | grep -v '^[+-][+-]' | head -20`
Expected: 只有那三處 patch 相關的行，**第 6 行的頂層 import 未出現在 diff 中**。

- [ ] **Step 7: Commit**

```bash
git add app/engine/html_guard/ tests/test_html_guard.py
git rm app/engine/html_guard.py
git commit -m "refactor(deepagent): html_guard 改為 package，抽出 report 與 js_runtime"
```

---

### Task 11: 抽出 lexer 與 Level 1 語法檢查

**Files:**
- Create: `app/engine/html_guard/js_lexer.py`、`js_syntax.py`
- Modify: `app/engine/html_guard/__init__.py`、`tests/test_js_lexer.py`

**Interfaces:**
- Consumes: `js_runtime.QUICKJS_AVAILABLE`（Task 10）；Task 1 建立的 `tests/test_js_lexer.py`
- Produces: `js_lexer.find_script_end(html: str, start_index: int) -> int`、`js_lexer.mask_strings_and_comments(text: str) -> str`、`js_lexer.extract_inline_scripts_with_lines(html: str) -> list[tuple[str, int]]`；`js_syntax.check_js_syntax(html: str, errors: list[str]) -> None`

- [ ] **Step 1: 建立 `js_lexer.py`**

搬 `_JS_STATE_*` 六個常數、`_SCRIPT_OPEN_TAG_PATTERN`、`_SRC_ATTR_PATTERN`、
`_SRC_ATTR_VALUE_PATTERN`、`_find_script_end`、`_mask_strings_and_comments`、
`_extract_inline_scripts_with_lines`，三個函式改公開名。

檔頭 docstring MUST 標明 MUST-sync：

```python
"""inline `<script>` 內文抽取與字串／註解遮罩的狀態機。

`find_script_end` 逐字 port 自 backend `JsSyntaxValidator.java` 的 `findScriptEnd`,
兩邊 MUST 同步修改。
"""
```

- [ ] **Step 2: 建立 `js_syntax.py`**

搬 `_QUICKJS_ERROR_LOCATION_PATTERN`、`_JS_SYNTAX_CHECK_WRAPPER_LINE_OFFSET`、`_check_js_syntax`。
以 `from . import js_runtime` + `js_runtime.QUICKJS_AVAILABLE` 存取旗標。

- [ ] **Step 3: 更新 `tests/test_js_lexer.py` 的 import**

```python
from app.engine.html_guard.js_lexer import find_script_end, mask_strings_and_comments
```

同步把四個測試裡的呼叫改成公開名。**斷言一字不動。**

- [ ] **Step 4: 執行**

Run: `uv run pytest tests/test_js_lexer.py -v && uv run pytest -q`
Expected: 4 passed，總計 208 passed。

- [ ] **Step 5: Commit**

```bash
git add app/engine/html_guard/ tests/test_js_lexer.py
git commit -m "refactor(deepagent): 抽出 js_lexer 與 js_syntax"
```

---

### Task 12: 抽出 sandbox 子套件

`Level 2 sandbox` 是全檔最大一區（736 行），拆成五個模組。

**Files:**
- Create: `app/engine/html_guard/sandbox/__init__.py`、`prelude.py`、`context.py`、`errors.py`、`console.py`、`runner.py`
- Modify: `app/engine/html_guard/__init__.py`

**Interfaces:**
- Consumes: `js_runtime.QUICKJS_AVAILABLE`（Task 10）；**`js_lexer.mask_strings_and_comments`（Task 11）——`_helper_call_site_lines` 用它掃 helper 呼叫點，現況在 `html_guard.py:703`，搬進 `sandbox/errors.py` 後這條相依必須跟著過去**
- Produces: `sandbox.execute_scripts_smoke(inline_scripts, available_query_ids, results, known_element_ids, html) -> list[str]`（參數順序與現況 `_execute_scripts_smoke` 完全相同）

- [ ] **Step 1: `prelude.py`** — 只放 `_SANDBOX_PRELUDE` 這個 JS 字串常數（凍結，一字不改）。

- [ ] **Step 2: `context.py`** — 搬 `_SANDBOX_TIME_LIMIT_SECONDS`、`_SANDBOX_MEMORY_LIMIT_BYTES`、`_SANDBOX_MAX_STACK_SIZE_BYTES`、`_SANDBOX_GLOBAL_DEADLINE_SECONDS`、`_SANDBOX_ERROR_MESSAGE_MAX_LENGTH`、`_SANDBOX_SEED_ROW_LIMIT`、`_results_literal_for_sandbox`、`_extract_known_element_ids`、`_build_sandbox_context`、`_ELEMENT_ID_ATTRIBUTE_PATTERN`。

- [ ] **Step 3: `errors.py`** — 搬 `_SANDBOX_INTERNAL_FRAME_NAME_PREFIX`、`_is_sandbox_internal_frame_name`、`_resolve_error_frames`、`_helper_call_site_lines`、`_format_execution_error`、`_STACK_FRAME_PATTERN`、`_stack_frame_lines`、`_resolve_stack_call_site_line`、`_REFERENCE_ERROR_VAR_PATTERN`、`_JS_IDENTIFIER_PATTERN`、`_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK`。

- [ ] **Step 4: `console.py`** — 搬 `_CHART_CONSOLE_ERROR_PATTERN`、`_read_collected_console_errors`、`_check_swallowed_chart_errors`、`_COLUMN_NOT_FOUND_PATTERN`、`_MAX_REPORTED_COLUMN_MISSES`、`_owning_query_ids_for_column`、`_read_collected_console_warnings`、`_check_column_not_found_warnings`。

- [ ] **Step 5: `runner.py`** — 搬 `_execute_scripts_smoke`，改公開名 `execute_scripts_smoke`。

- [ ] **Step 6: `sandbox/__init__.py`** — 只 re-export `execute_scripts_smoke`。

- [ ] **Step 7: 執行測試**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 208 passed，ruff 乾淨。

sandbox 的測試在 `tests/test_html_guard.py`，全部走公開 API，**不需要任何改動**。

- [ ] **Step 8: Commit**

```bash
git add app/engine/html_guard/
git commit -m "refactor(deepagent): sandbox 抽成子套件"
```

---

### Task 13: 抽出規則檢查、主題改寫與 entry

**Files:**
- Create: `app/engine/html_guard/rules.py`、`rules_tab.py`、`theme_rewrite.py`、`checker.py`
- Modify: `app/engine/html_guard/__init__.py`

**Interfaces:**
- Consumes: `report.GuardReport`／`check_structure`／`check_size`（Task 10）、`js_lexer.mask_strings_and_comments`／`extract_inline_scripts_with_lines`（Task 11）、`js_syntax.check_js_syntax`（Task 11）、`sandbox.execute_scripts_smoke`（Task 12）、`app.engine.results.referenced_query_ids`（既有）
- Produces: `checker.check_dashboard_html(html: str, available_query_ids: set[str], results: dict[str, dict] | None = None) -> GuardReport`（簽名與現況完全相同）

- [ ] **Step 1: `rules.py`** — 搬 `_check_tooltip`、`_check_data_binding`、`_is_allowed_script_src`、`_check_script_src_whitelist`、`_check_no_register_theme`、`_check_referenced_query_ids`、`ALLOWED_SCRIPT_SRC_PREFIXES`、`_ALLOWED_TAILWIND_HOST`、`_ALLOWED_JSDELIVR_HOST`、`_ALLOWED_JSDELIVR_ECHARTS_PATH_PREFIX`、`_ECHARTS_INIT_CALL_PREFIX`、`_REGISTER_THEME_CALL_PREFIX`。

- [ ] **Step 2: `rules_tab.py`** — 搬 `_TAB_ONCLICK_PATTERN`、`_PANEL_CONTAINER_PATTERN`、`_TAB_SWITCH_FUNCTION_PATTERN`、`_ONCLICK_HANDLER_PATTERN`、`_FUNCTION_CALL_NAME_PATTERN`、`_FUNCTION_KEYWORD_DECLARATION_TEMPLATE`、`_FUNCTION_EXPRESSION_ASSIGNMENT_TEMPLATE`、`_RESIZE_DISPATCH_PATTERN`、`_RESIZE_METHOD_SNIPPET`、`_TABLER_STYLE_MARKER`、`_has_tab_structure`、`_find_matching_close_brace`、`_onclick_wired_function_names`、`_function_body_by_name`、`_tab_switch_function_bodies`、`_check_tab_conventions`。遮罩改用 `js_lexer.mask_strings_and_comments`。

- [ ] **Step 3: `theme_rewrite.py`** — 搬 `_find_matching_close_paren`、`_split_top_level_arguments`、`_apply_erd_theme`。

> **不要順手修 C1。** `_apply_erd_theme` 目前在未遮罩的原文上掃描是已知 bug（C1），
> 但本階段是純搬遷。搬過去之後行為必須完全相同，修復留給後續 PR。

- [ ] **Step 4: `checker.py`** — 搬 `check_dashboard_html`，從各模組 import 檢查函式。呼叫順序一字不動。

- [ ] **Step 5: `__init__.py` 收斂成純 re-export**

```python
"""dashboard.html 確定性檢查——DASHBOARD_HTML 發送前的最後一道關卡。

engine 層——stdlib only,禁止 import 任何 LLM 框架(ruff TID251 會擋)。
"""

from app.engine.html_guard.checker import check_dashboard_html
from app.engine.html_guard.report import HTML_MAX_BYTES, GuardReport
from app.engine.html_guard.rules import ALLOWED_SCRIPT_SRC_PREFIXES

__all__ = [
    "ALLOWED_SCRIPT_SRC_PREFIXES",
    "HTML_MAX_BYTES",
    "GuardReport",
    "check_dashboard_html",
]
```

- [ ] **Step 6: 執行測試**

Run: `uv run pytest -q && uv run ruff check .`
Expected: 208 passed，ruff 乾淨。

- [ ] **Step 7: 驗收檔案大小**

Run: `wc -l app/engine/html_guard/*.py app/engine/html_guard/sandbox/*.py | sort -n`
Expected: 最大檔 ≤ 200 行。

- [ ] **Step 8: 最終確認測試 diff**

Run: `git diff master --stat tests/`
Expected: 只有 `tests/test_js_lexer.py`（新檔）、`tests/test_chat.py`、`tests/test_repair.py`、
`tests/test_html_guard.py` 的 import／patch 行改動。

Run: `git diff master tests/ | grep '^[+-]' | grep -v '^[+-][+-]' | grep 'assert'`
Expected: **只有階段一新增的測試裡的 assert，沒有任何既有 assert 被修改。**

- [ ] **Step 9: Commit**

```bash
git add app/engine/html_guard/
git commit -m "refactor(deepagent): 抽出規則檢查、主題改寫與 entry"
```

---

## 總驗收

- [ ] `uv run pytest -q` → 208 passed
- [ ] `uv run ruff check .` → 乾淨
- [ ] `wc -l app/main.py` → ≤ 100
- [ ] `wc -l app/engine/html_guard/**/*.py` → 最大 ≤ 200
- [ ] `git diff master tests/` 不含任何既有 assert 的修改
- [ ] 凍結字串檢查：`git diff --color-words=. master app/ | grep -E "SYSTEM_PROMPT|DASHBOARD_REJECTED_PREFIX|DATA_FRAME_"` 只顯示位置移動，無字元增刪
