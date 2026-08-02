# deepagent-service 結構重構 — design

**日期**：2026-08-02
**基準 commit**：`b8137a2`
**範圍**：`deepagent-service/app/main.py`（550 行）與 `app/engine/html_guard.py`（1517 行）

---

## 1. 目標與非目標

### 目標

把兩個過大的檔案依職責拆開，讓後續 27 條待修 findings 有明確的落點。

| 檔案 | 現況 | 問題 |
|---|---|---|
| `app/main.py` | 550 行 | 路由、schema、workflow、prompt 樣板混在一起；`chat()` 單一函式 164 行 |
| `app/engine/html_guard.py` | 1517 行 | lexer、sandbox、規則檢查、HTML 改寫同層；Level 2 sandbox 一區就佔 736 行 |

### 非目標（明確排除）

- **不修任何 bug。** findings 裡的 27 條全部留給後續 PR。
- **不改任何行為。** 包含錯誤訊息文字、事件順序、例外型別。
- **不動測試斷言。** 測試檔的 diff 只允許出現在 import 行與 monkeypatch 目標。
- **不改對外 API。** `/health`、`/chat`、`/repair` 的 wire 契約一字不動。

### 為什麼選「純結構、零行為改變」

替代方案是「搬到哪就順手修到哪」。否決理由：搬遷與修復混在同一個 diff 裡，
review 時分不出哪一行是搬的、哪一行是改的；測試變紅時也判斷不出是重構搬壞了還是修復修錯了。

純搬遷的驗收標準是機械的——**現有 201 條測試不改一個字元全綠**，任何一條變紅都是重構出錯。
代價是 PR 數量變多、同一區域要碰兩次，接受。

---

## 2. PR 序列

四支，順序有意義：

| PR | 內容 | 驗收 |
|---|---|---|
| **0** | 特徵測試 | 只新增測試，**零產品碼改動** |
| **0.5** | docstring 精簡 | 只改註解，**零程式碼改動**；測試不改一字全綠 |
| **1** | `main.py` 拆分 | 純搬遷；測試 diff 只有 import／patch 目標 |
| **2** | `html_guard.py` 拆分 | 純搬遷；測試頂層 import 完全不動 |

**為什麼 0.5 排在搬之前**：搬遷 PR 的 review 性質是「每一行都應該是搬過來的」。
若在同一支 PR 改寫 docstring，這個性質就沒了。先精簡、再搬，搬的時候帶的已是精簡版。

---

## 3. `main.py` 拆分

依四個職責切開：

```
app/main.py                    ~70 行   ①  FastAPI app 組裝 + 三個薄端點
app/api/schemas.py             ~45 行   ②  對外介面定義
app/agent/chat_turn.py        ~290 行   ③  一輪 /chat 的完整 workflow
app/agent/repair_flow.py       ~85 行   ③  /repair workflow
app/agent/prompts.py           +45 行   ④  prompt 樣板（既有檔）
app/engine/html_extract.py     ~25 行   ④  fence 抽取（純字串，零 LLM 相依）
```

### ① 路由與進入點 — `app/main.py`

`app` 建立、`/health`、`/chat`、`/repair`。抽乾後三個端點都是薄的。

`/chat` 的最終形狀：

```python
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

「ERROR 是本輪最後一個事件」這條契約因此集中在一處可見，不再散在三個地方。

### ② 對外介面 — `app/api/schemas.py`

`HistoryItem`、`SourceItem`、`ChatRequest`、`RepairErrorItem`、`RepairRequest` 五個 Pydantic model 整組搬走。

### ③ Workflow — `app/agent/chat_turn.py`

**選擇單一 class 而非「值物件 + 三模組」的理由**：跨階段的狀態
（workspace、agent、recorder、run_config、run_input、mtime baseline、bridge）
本來就是「一輪的狀態」。拆成多模組等於為了避免用 instance attribute 而發明參數穿線機制，
同一份狀態在模組間傳來傳去。

決定性的一點：**Python 的 async generator 不能有回傳值**（PEP 525），
而首輪重試迴圈會**替換** `bridge`（`bridge = EventBridge(recorder)`）。
若把串流抽成獨立的 async generator 函式，最終的 bridge 無法交給 finalize。
用 class 的話就是 `self.bridge`，這個問題整個消失。

class 對後續修復也是加分：

| 待修 finding | 在 class 下的落點 |
|---|---|
| F1（orphan run，需 cancel + aclosing） | `__aexit__` |
| F3（收尾無例外保護） | `finalize()` 方法內 |
| 根因 B（`to_thread` 改造） | 阻塞工作全在 `__aenter__`，一處包完 |

模組形狀：

```python
# ── module-level：純函式與常數，維持可獨立測試 ──
HEARTBEAT_INTERVAL_SECONDS / AGENT_RECURSION_LIMIT / GUARD_REPAIR_MAX_RUNS
STREAM_RETRY_MAX_RUNS / FIRST_ROUND_RETRY_MAX_RUNS
EMPTY_ANSWER_FALLBACK_MESSAGE / DASHBOARD_UPDATED_FALLBACK_MESSAGE
DASHBOARD_REJECTED_PREFIX / GRAPH_RECURSION_ERROR_MESSAGE

def _is_transient_stream_error(error) -> bool
def _guard_repair_should_stop(previous_errors, current_errors) -> bool
def _seed_messages(request) -> list[BaseMessage]
def _build_callbacks() -> list
async def stream_agent_turn(agent, run_input, run_config, bridge) -> AsyncIterable[dict]

# ── 有狀態的一輪 ──
class ChatTurn:
    """non-bean: instantiate per /chat request."""
    async def __aenter__(self) -> "ChatTurn"
    async def __aexit__(self, *exc) -> None
    async def stream(self) -> AsyncIterable[dict]
    async def finalize(self) -> AsyncIterable[dict]
```

**四個純函式刻意留在 module-level**：它們現在有直接的單元測試
（`_guard_repair_should_stop` 在第一批止血剛加了四條）。變成 method 就得先建 instance 才能測。
`stream_agent_turn` 同理——它已是純粹的參數函式，不需要 turn 狀態。

**`connection` 刻意不放進 instance 的公開欄位**：只有 `build_agent` 需要它，
之後任何人都不該再碰。由 `__aenter__`/`__aexit__` 私有持有並關閉，消費端拿不到就不會誤用已關閉的連線。

### ③ `/repair` — `app/agent/repair_flow.py`

不併進 `ChatTurn`——它不共用任何 turn 狀態，是另一條 workflow。
含 `_invoke_repair_model`、guard 重試迴圈、`REPAIR_*` 三個常數。

### ④ prompt 樣板與 HTML 抽取

- prompt 樣板進**既有的** `app/agent/prompts.py`（`SYSTEM_PROMPT` 已在那）：
  `REPAIR_SYSTEM_PROMPT`、兩個 user message builder、`PREVIOUS_VERSION_SYSTEM_NOTE`
- fence 抽取（`_HTML_FENCE_PATTERN`、`_extract_html_block`）是純字串處理、零 LLM 相依
  → `app/engine/html_extract.py`

---

## 4. `html_guard.py` 拆分

拆成 package，**import 路徑完全不變**（`__init__.py` re-export）。
對外只有三個符號：`check_dashboard_html`、`GuardReport`、`ALLOWED_SCRIPT_SRC_PREFIXES`。

```
app/engine/html_guard/
  __init__.py           ~20   re-export 三個公開符號
  report.py             ~60   GuardReport / HTML_MAX_BYTES / 結構檢查 / 體積檢查
  js_runtime.py         ~25   quickjs 可用性探測（單一擁有者，見 §6）
  js_lexer.py          ~185   ★ MUST-sync ↔ JsSyntaxValidator.java
  js_syntax.py          ~55   Level 1 語法檢查
  rules.py             ~190   tooltip / data-binding / src 白名單 / registerTheme / 引用 id
  rules_tab.py         ~165   tab 規範（含括號配對、函式本體抽取）
  theme_rewrite.py      ~55   _apply_erd_theme + 括號配對
  checker.py            ~60   check_dashboard_html 編排
  sandbox/
    __init__.py         ~15
    prelude.py         ~165   純 JS 字串
    context.py         ~125   limits / results 字面值 / element id / context 建立
    errors.py          ~115   stack frame 解析與錯誤格式化
    console.py         ~160   console 收集、被吞掉的圖表錯誤、getCol miss
    runner.py          ~155   _execute_scripts_smoke
```

最大檔約 190 行。sandbox 做成**子套件**而非平行檔——它是有內部結構的子系統，階層擺出來比攤平好認。

### 兩個非「檔案變小」的理由

**`js_lexer.py` 是最有價值的一刀。** 根因 C（C1、C3、M4）的本質是
`_apply_erd_theme` 是唯一會改寫原文、也是唯一沒走遮罩的掃描器。
lexer 獨立成模組並公開明確 API 之後，`theme_rewrite.py` 對它的相依變成**寫在 import 裡的事實**，
而不是「作者記得要用」。之後修 C1 是加一行 import 加一個呼叫，不是在 1500 行裡找遮罩函式。

**MUST-sync 從註解升級成檔案對檔案。** 現在是檔案中段一段註解說「port 自 `JsSyntaxValidator.java`」。
拆完之後 `js_lexer.py` ↔ `JsSyntaxValidator.java` 一對一，核對同步只需比兩個檔。

---

## 5. docstring 精簡（PR 0.5）

現況：`app/` 共 519 行 docstring，36 個 ≥6 行，最長 26 行。

### 砍掉

- 事故敘事（「a real incident: a repair round reshuffled the KPI cards, deleted one card's `<div>`…」）
- 重述程式碼在做什麼（`prepare_local_layout` 的「驗證、算路徑、建目錄」——程式碼自己說得清楚）
- 同一論點反覆論證（`_guard_repair_should_stop` 現在用三種說法講同一件事）
- 「為什麼不選另一個做法」的長篇（`strip_injected_blocks` 花 8 行解釋為何不處理舊格式）

### 壓縮成 1–2 行但**必須留**

從程式碼讀不出來的外部事實：

| 位置 | 不能丟的知識 |
|---|---|
| `ToolResultRecorder` | LangChain 對字面名為 `callbacks` 的參數做特殊處理，注入 `run_manager.get_child()` |
| `run_input` 附近 | `add_messages` 依 `message.id` 去重，重建 HumanMessage 會 double-append |
| `previousDashboardHtml` 區塊 | MUST 在 mtime 快照之前——順序錯了本輪未改動也會被誤判成有改 |
| `theme.py` / `js_lexer.py` | MUST-sync 對應到哪個 backend 檔 |
| `_guard_repair_should_stop` | 為什麼比集合不比數量（guard 會 clamp 錯誤列表） |
| `DashboardOverwriteBackend` | 為什麼 write 要先 unlink；為什麼 edit 一律退貨 |

### 絕對凍結

分界線是：**給人看的 docstring／註解可以修，會送給模型或使用者的字串一律凍結。**

**「凍結」指的是內容，不是位置。** 這些字串在 PR 1／PR 2 會隨所屬職責搬到新模組
（例如 `REPAIR_SYSTEM_PROMPT` → `prompts.py`、`DASHBOARD_REJECTED_PREFIX` → `chat_turn.py`），
搬動是允許且必要的；**不允許的是改動字元**——一個空白、一個標點都不行。
驗收方式：搬遷 PR 對這些常數的 diff 應該是純粹的位置移動，`git diff --color-words` 看不到任何字元增刪。

```
SYSTEM_PROMPT / REPAIR_SYSTEM_PROMPT / PREVIOUS_VERSION_SYSTEM_NOTE
DASHBOARD_EDIT_REJECTED_MESSAGE / guard 修復訊息 / skill gate 退貨訊息
EMPTY_ANSWER_FALLBACK_MESSAGE / DASHBOARD_UPDATED_FALLBACK_MESSAGE
DASHBOARD_REJECTED_PREFIX / GRAPH_RECURSION_ERROR_MESSAGE
html_guard 所有錯誤訊息（會餵回模型做下一輪修復）
DATA_FRAME_OPEN / DATA_FRAME_CLOSE
skills/**/*.md
```

預期 519 → 約 250 行。**這是結果不是目標**，不為湊數字砍掉該留的。

---

## 6. 測試策略

### PR 0 — 特徵測試

補在**即將下刀的接縫上**，不做全面覆蓋。目前 `html_guard` 88%、`main.py` 92%，
但破洞剛好落在最危險處。

| 目標 | 補什麼 | 為什麼 |
|---|---|---|
| lexer | 兩個狀態機各自的 `/* */` 分支、`_mask_*` 的 template literal 進入、未閉合 block comment（`return length` 那條） | 有狀態、index 算術微妙，最怕搬壞；且 C3 說它有 bug |
| `main.py` | 閒置時重發 heartbeat、首輪重試迴圈、空回應兜底 | 都是未覆蓋分支 |
| **async generator 早退等價性** | ERROR 之後不再有任何事件 **且** teardown 有跑（`connection.close()` 被呼叫） | 見下方風險 §7 |

**特徵測試釘的是「今天的行為」**，包含 C3 那個 bug 的錯誤行為。
之後修 C3 時會有意識地改掉其中幾條——那時候是刻意的行為改變，不是重構事故。

### PR 1 — 已知要改的 patch 目標

測試目前經 `main_module.X` 抓 8 個內部符號、共 22 處引用：

| 符號 | 引用數 | 搬到 |
|---|---|---|
| `_is_transient_stream_error` | 6 | `chat_turn` |
| `_guard_repair_should_stop` | 4 | `chat_turn` |
| `_seed_messages` | 3 | `chat_turn` |
| `_stream_agent_turn` | 2 | `chat_turn`（更名 `stream_agent_turn`） |
| `DASHBOARD_REJECTED_PREFIX` | 2 | `chat_turn` |
| `DASHBOARD_UPDATED_FALLBACK_MESSAGE` | 1 | `chat_turn` |
| `ChatRequest` / `HistoryItem` | 3 | `api.schemas` |
| `strip_injected_blocks`（spy） | 1 | 見下 |
| `app` | 3 | 不動 |

### PR 2 — 頂層 import 不動

`test_html_guard.py`、`test_dashboard_skill.py` 的頂層 import 靠 `__init__.py` re-export **完全不變**。
只有 3 處 `monkeypatch.setattr(html_guard, "_QUICKJS_AVAILABLE", ...)` 要改指向 `js_runtime`。

### 硬性設計要求：quickjs 可用性旗標

探測**只能有一個擁有者**（`js_runtime.py`），消費端必須以**模組屬性存取**讀它：

```python
from . import js_runtime
if js_runtime.QUICKJS_AVAILABLE:            # ✅ 呼叫時取值，monkeypatch 有效
```

```python
from .js_runtime import QUICKJS_AVAILABLE   # ❌ import 期快照，patch 永遠打不到
```

寫錯的話 monkeypatch 靜默失效——這是正確性要求，不是風格偏好。

---

## 7. 風險

### R1 — async generator 早退語意改變（最高）

現在 ERROR 早退是 `chat()` 裡的 `return`，直接結束單一函式。
拆完之後變成 caller `return` → `turn.finalize()` generator 被 `aclose()` → 於 yield 點拋 `GeneratorExit`。

淨效果相同，但**路徑不同**。這正是 F1 那類「async generator 早退」的地雷區。

**緩解**：PR 0 必須先釘住「ERROR 之後不再有任何事件」與「teardown 確實執行」兩條，再動手搬。

### R2 — monkeypatch 目標失效

兩處已確認，且**都會大聲失敗、不會靜默通過**：

- `_QUICKJS_AVAILABLE`（3 處）：patch 打空 → quickjs 其實可用 → 語法錯誤被抓到 →
  那三個「應該跳過檢查」的測試變紅
- `strip_injected_blocks` spy（`test_chat.py:383`）：斷言 `len(entry_rebuild_calls) == 1`，
  patch 打空就是 0 → 變紅

該測試的註解已載明這個 spy 是刻意耦合到那段程式碼的，因為事後斷言反推不出
「entry-rebuild 真的跑過」。搬遷後要改成 patch `chat_turn.strip_injected_blocks`。

### R3 — docstring 砍過頭

**緩解**：§5 的「必須留」清單逐項對照；PR 0.5 的 diff 只有註解行，
review 時可逐條檢查有沒有砍掉外部事實。

### R4 — `app/api/` 是新目錄

`app/` 下目前只有 `agent/`、`engine/`。新增 `api/` 是新的分層概念。
替代方案是把 schemas 放 `app/schemas.py`（平的）。
選 `app/api/`，因為「對外介面定義」本來就是獨立職責，之後若有 response model 也有地方放。

### 不構成風險的一項

**ruff TID251（engine 層禁止 import LLM 框架）不需要調整。**
`chat_turn.py`／`repair_flow.py` 落在 `app/agent/`（已在 per-file-ignores 白名單）；
`html_extract.py` 與 `html_guard/**` 是純字串／stdlib，不碰 LLM 框架。
`theme.py`／`duck.py`／`results.py` 那批 backend 複本繼續受保護。

---

## 8. 驗收標準

| PR | 標準 |
|---|---|
| 0 | 新測試全綠；`git diff` 不含任何 `app/` 下的產品碼改動 |
| 0.5 | 201 條測試**不改一個字元**全綠；`git diff` 只有註解／docstring 行；§5 凍結清單零改動 |
| 1 | 全部測試綠；測試 diff 只有 import 行與 monkeypatch 目標；`main.py` ≤ 100 行 |
| 2 | 全部測試綠；`test_html_guard.py` 頂層 import 零改動；最大檔 ≤ 200 行 |

每支都要 `uv run ruff check .` 乾淨。

**跨 PR 的總驗收**：`app/main.py` 550 → ~70 行，`html_guard` 單檔 1517 → 最大 190 行，
全程沒有一條測試的斷言被修改過。
