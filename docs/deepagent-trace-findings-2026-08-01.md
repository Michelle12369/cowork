# deepagent-service trace 調查發現（2026-08-01）

一次唯讀 debug session 的完整結論。來源＝Langfuse 上 2026-08-01 05:11–05:59 的五個使用者
session（十條 trace），加上 07-30 起全部 133 條 trace 的統計分析。所有根因都經過實證重現，不是
推測。本文件供後續 session 直接施工使用。

**調查期間未修改任何程式碼**——以下全部是待辦。

---

## 摘要：六個問題，依建議施工順序

| # | 問題 | 型態 | 影響的 session |
|---|---|---|---|
| 1 | 並行 `edit_file` 靜默覆蓋彼此（lost update） | 平台 bug | F |
| 2 | `getCol` 回 `-1` 使綁錯欄位變成無聲錯誤渲染 | guard 盲區 | A、E |
| 3 | qN 別名憑記憶對應，綁錯查詢結果 | 缺確定性輸入 | A、E、F |
| 4 | 修復迴圈無法收斂（訊息指錯位置／預算太少／會退步） | guard 品質 | D |
| 5 | 未讀 dashboard skill 就寫檔 → 資料完全不綁定 | 缺 gate | B |
| 6 | 缺兩項靜態檢查（資料綁定、tab resize） | guard 缺項 | B |

問題 1 排第一不只因為它是 bug：**它會靜默破壞狀態並回報成功**，只要它還在，任何「模型修不好
X」的診斷都不可信。修它之前不要動其他項目的結論。

### session 對照表

| 代號 | thread | 首輪 trace | 使用者 prompt | 結果 |
|---|---|---|---|---|
| A | `fb754595` | `4884b998` | 把這個分析畫成dashboard | 交付，但 5 個區塊空白 |
| B | `c908cdde` | `b1ccfa12` | 幫我畫兩個tab分別是敘述這兩份資料的概況 | 交付，資料全硬編碼＋tab 2 圖表寬度錯 |
| D | `d0f02a96` | `373c8789` | 幫我畫兩個tab分別是敘述這兩份資料的概況 | **guard 退貨**，使用者拿不到 dashboard |
| E | `3bf8db32` | `b0a1a597` | 好 把這個分析畫成dashboard | 交付，洞察卡顯示 `undefined` / `NaN` |
| F | `b6c207bc` | `09d31ad4` | 幫我畫兩個tab分別是敘述這兩份資料的概況 | **guard 退貨**（實為平台 bug 所致） |

---

## 問題 1：並行 `edit_file` 靜默覆蓋（最高優先）

### 症狀

修復輪的編輯回報 `Successfully replaced 1 instance(s)`，但改動沒有落到檔案上。模型因此把修復
預算耗在重做已經「成功」過的工作，最終 guard 退貨。

### 根因

`deepagents.backends.filesystem.FilesystemBackend.edit()`（該檔 line 508）是沒有鎖、沒有
compare-and-swap 的讀改寫：

```
os.open(O_RDONLY) → 讀進整份 content
perform_string_replacement(content, old_string, new_string)
os.open(O_WRONLY | O_TRUNC) → 寫回整份
```

而 LangGraph 的 `ToolNode` 預設把同一則 AI message 的所有 tool call 並發送出：

- 非同步路徑（本服務走 `astream_events`）：`await asyncio.gather(*coros)`
  （`langgraph/prebuilt/tool_node.py:858`）
- 同步路徑：`get_executor_for_config` 執行緒池 `executor.map(...)`（同檔 line 821）

`edit_file` 函式本體是同步的，非同步路徑上會被下放到執行緒池，所以檔案 I/O 是真的多執行緒重疊。

兩個並發 `edit_file` 打同一路徑 → 讀到同一份 base → 各自寫回只含自己改動的完整檔案 → 最後寫入
者獲勝，另一個消失，**兩邊都回報成功**。

### 實證

**證據一（自我證明的重做）** — trace `737d886b`，round 2：05:58:47.062 一次發出 4 個並行
`edit_file`。接著模型在 05:58:55、05:58:59、05:59:06 用**完全相同的 `old_string`** 重做了其中
三個（每次重做前都先 `read_file`）。相同 old_string 能再次匹配，只可能是先前那次「成功」的編輯
從未落地。

**證據二（可驗證的遺失）** — trace `e9851ed4`，round 3：兩個 `edit_file` 的 start/end 時間戳
完全相同（05:59:22.003 → .008），兩者都回報成功，但檔案裡只有第二個生效。第一個是把
`ulFeatures` 從 `if` 區塊內提到外層作用域——**正確修掉了 guard 回報的 ReferenceError**。

**證據三（決定性）** — 把遺失的那一次編輯補回最終檔案再跑 guard：`ok=True`。這個 session 本來
會成功。

### 規模

07-30 起，84 條 trace、288 次對 `dashboard.html` 的 `edit_file`：

| 每批並行數 | 批次數 |
|---|---|
| 1（安全） | 263 |
| 2 | 6 |
| 4 | 2 |
| 5 | 1 |

5/84 條 trace（6%）出現並行批次，理論上被覆蓋約 16 次編輯（占全部 6%）。比率不高但嚴重度高：
靜默、專打修復輪這個預算最稀缺的環節、可能留下半套狀態（多筆相關編輯只生效一部分，比改之前更壞）。

### 建議修法

在 `build_agent` 加一個序列化中介層。`AgentMiddleware` 有官方支援的 `awrap_tool_call` 鉤子，
`create_deep_agent` 收 `middleware=`：

```python
class SerializedToolCallsMiddleware(AgentMiddleware):
    """一次只跑一個 tool call——同檔案並發寫入會靜默覆蓋彼此。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def awrap_tool_call(self, request, handler):
        async with self._lock:
            return await handler(request)
```

`build_agent`（`app/agent/graph.py:93`）是**每個 request 各建一次**（`app/main.py:251`），
所以鎖天然是 per-session，不會跨 session 互卡，不影響既有的 throughput 優化。

只需實作 `awrap_tool_call`（非同步）；`app/main.py` 目前只走 `astream_events`。

### 為什麼選全域序列化而非針對性加鎖

`write_file` / `edit_file` 由 deepagents 的 filesystem middleware 提供，不在
`build_data_tools` 裡。針對性加鎖要靠 subclass backend（`DashboardOverwriteBackend` 已存在，
做得到），但那是持續義務：以後每加一個會寫狀態的工具都要重新判斷會不會相撞。中介層做法從結構上
消除整類問題。

ledger 記載曾為 query_id 併發碰撞加過一把鎖——同一個根因的另一個實例。序列化後那把鎖是否可退役
值得回頭確認（**要驗證，不要直接假設**）。

### 代價（實測）

- 並行 `run_sql` 省下的 wall-clock：三天總計 **6.4 秒**（349 次並行呼叫）。DuckDB 在此資料量級
  是毫秒級。
- tool 執行佔 turn 總時長：10 條 trace 中的 9 條是 **0.0–0.5%**，其餘全是 LLM 生成。
- 唯一真實成本是 `task` 子代理：唯一用到的那條 session 裡兩個子代理並行，序列化約多花 12 秒。
  133 條 trace 中只出現 2 次。

若要保留子代理並行，加一行豁免即可（`if request.tool_name == "task": return await
handler(request)`）。建議**先不加**——少一個例外少一個要解釋的分支，等子代理用量長起來再說。

### 已排除的死路（不要浪費時間）

- **`max_concurrency=1` 無效**。該設定只到同步路徑的 `get_executor_for_config(max_workers=)`
  （`langchain_core/runnables/config.py:673`）；非同步路徑的 `asyncio.gather` 不看它。
- **`create_deep_agent` 沒有 tool-node 參數**。完整簽章：model / tools / system_prompt /
  middleware / subagents / skills / memory / permissions / backend / interrupt_on /
  response_format / state_schema / context_schema / checkpointer / store / debug / name /
  cache。要換 ToolNode 就得放棄 `create_deep_agent` 自組 graph——那正是本服務刻意不做的事。
- **換 backend 不會救**。deepagents 預設 backend 是 `StateBackend()`（`graph.py:615`），但
  `StateBackend.edit`（`state.py:267-293`）做的是一模一樣的讀改寫，並發呼叫共用同一份 state
  快照，同樣最後寫入者獲勝。問題在讀改寫這個模式，不在儲存媒介。

### 延伸（現在不做）

行程內的鎖只在單一 process 有效。目前 `AGENT_WORKSPACE_BACKEND=s3`、workspace 會 push/pull
minio；若之後跑多 worker 或多 pod 共用 workspace，需要補 compare-and-swap（讀取時記內容 hash，
寫回前比對，不一致回「檔案已被改動，請重讀後重試」而非覆蓋）。這與 ledger 裡「minio workspace
耐久性：跨 pod 無版本控制」是同一個風險面。

### 上游

`deepagents` 0.6.12 的 backends 目錄裡沒有任何 Lock（全檔 grep 過）。並發分派 ＋ 非原子編輯的
組合可視為上游缺陷，值得回報。

---

## 問題 2：`getCol` 回 `-1` 使綁錯欄位變成無聲錯誤

### 症狀

三種變體，都不拋例外，guard 全部放行：

1. **整段空白**（session A）——`if (idx >= 0)` gate 關閉，該區塊完全不渲染
2. **畫面出現 `undefined` / `NaN`**（session E）——沒有 gate，`row[-1]` 是 `undefined`，
   `Number(undefined)` 是 `NaN`，`NaN.toFixed(2)` 完全合法地產出字串 `"NaN"`
3. **假的 `0`**（session E 的 KPI）——`dashRow ? Number(...) : 0`，看起來像真實量測值

session E 洞察卡實際渲染出來的字：

> undefined 以 NaN 分高居榜首，且零負面回饋；undefined 評分僅 NaN 分，卻是使用量第 2 名的高頻
> 功能，負面率達 28.6%，為最需優先改善的功能。整體系統成功率 97.65%，情感分析顯示 55% 為正面回饋。

skill 明文禁止第 3 種（「NEVER paper over it with `|| 0`」）並要求第 2 種顯示「（資料缺失）」。
**session E 讀完了全部四份 skill 檔，然後兩條都違反。**

### 關鍵發現：訊號一直存在，只是被丟掉

skill 強制的 `getCol` 樣板本來就會在找不到欄位時 `console.warn('[ERD] column not found:', ...)`。

我在真實瀏覽器裡載入 session E 的交付版 HTML，**收到 29 個 `[ERD] column not found` 警告**。

而 guard 的 sandbox 把 `console.warn` 寫成 no-op（`app/engine/html_guard.py` 的
`_SANDBOX_PRELUDE`，約 line 408），只有 `console.error` 有收集器（同檔 line 405-416，讀取在
`_read_collected_console_errors` line 559，判定在 `_check_swallowed_chart_errors` line 575）。
29 個訊號全部進垃圾桶。

### 建議修法

把 `console.warn` 比照 `console.error` 改成收集器，並把 `[ERD] column not found` 格式的訊息
轉成 guard error。約 5 行改動，**會同時攔下 session A 與 E**。

退貨訊息要能一次修完，建議格式：

```
Line N: getCol miss — 變數 `featureRating`（綁定 q2，欄位 sentiment/count/percentage）
沒有 'avg_rating'。該欄位存在於 q5。
```

guard 手上已經有真實 `results`（`_results_literal_for_sandbox`，同檔 line 454），所以「該欄位在
哪個 qN」是可以確定性算出來的。

### 注意

`getCol` 的 `-1` 契約本身是 skill 規定的防禦式寫法，用意是防缺欄位炸頁。**不要改掉這個契約**——
改的是讓 guard 聽見它發出的訊號。

---

## 問題 3：qN 別名憑記憶對應

### 症狀

四個 session 裡有三個是 qN 綁錯，且與有沒有讀 skill 無關：

- **session A**：q4/q5 對調。`satisfaction` 綁到實際是「類別分佈」的 q4，`categoryDist` 綁到實際
  是「滿意度」的 q5 → 3 張圖 + 洞察卡 + 1 張表全空白
- **session E**：q2/q4/q5 三方輪轉

  | 變數 | 綁到 | 實際內容 | 應該綁 |
  |---|---|---|---|
  | `featureRating` | q2 | `sentiment, count, percentage` | q5 |
  | `sentimentDist` | q4 | `feature_name, sentiment, count` | q2 |
  | `featureSentiment` | q5 | `feature_name, feedback_count, avg_rating...` | q4 |

- **session F 第一輪**：直接把資料表名當 query id 用（`__ERD_RESULTS__['feedback']` /
  `['usage_log']`），而且 `write_file` 發生在需要的 `run_sql` **之前**

讀 skill 決定的是**形式**（會不會用 `__ERD_RESULTS__`、會不會寫 `getCol`、會不會包 try/catch），
但沒有任何東西告訴模型「此刻要綁的 q5 到底是什麼」。session E 跑了 11 個查詢，寫綁定時是憑幾十個
tool call 之前的對話記憶在對應編號。

### 建議修法

dashboard turn 開始時注入 wiring manifest（`qid → intent + columns`）。資料現成存在
`<session>/results/*.json`，每個檔案都有 `intent` 與 `columns` 欄位。

這與問題 2 互補：manifest 降低綁錯機率，`console.warn` 收集器保證綁錯一定被抓到。兩個都要做。

### 既有先例

guard 已經有「referenced query result id 不存在」的檢查，session F 第一輪就是被它接住的
（錯誤訊息：`The HTML references query result id(s) that don't exist: feedback, usage_log`）。
這證明這類確定性檢查在本專案是可行且有效的。

---

## 問題 4：修復迴圈無法收斂

以 session D 為例。原始檔其實只差**兩個機械式修改**就能通過，但兩輪修復不但沒修好，還把對的改壞。

### 三個獨立缺陷，逐個浮現

1. **`getCol` 從未定義**——原始檔呼叫它 5 次，`function getCol` 出現 0 次
2. **`.rows` 拆包後又取 `.columns`**——7 個別名全寫成 `const q4 = d.q4.rows`，然後
   `getCol(q4.columns, ...)`；陣列沒有 `.columns`，`undefined.indexOf` 爆掉
3. **修復輪自己製造的迴歸**——原始檔 `q4` 語意上是對的，模型在 13 次編輯裡把它改成 `q3`

### 三個結構性成因

**成因一：錯誤訊息指向拋出點而非呼叫點。** guard 回報 `Line 112: TypeError: cannot read
property 'indexOf' of undefined`，而 line 112 在 `getCol` 函式體內。`getCol` 是 skill 強制每份
dashboard 都要有的共用 helper，**全檔任何一次欄位解析失敗都塌縮到同一行**。模型完全拿不到「哪個
綁定錯了」的資訊，只能猜——它猜「q 編號接錯」，於是花 13 次編輯在 q4↔q5↔q6↔q7 之間玩大風吹。

相關程式碼：`_resolve_html_error_line`（`html_guard.py:526`）、`_format_execution_error`
（同檔 line 536）。

**成因二：錯誤逐個浮現，但修復預算只有 2 輪。** sandbox 遇到第一個例外就停，每輪只揭露下一個錯。
三個連續執行期錯誤的檔案，就算模型完美也不可能在兩輪內收斂。實測過：修好第 2 個錯之後，第 3 個
錯（`Line 135`）立刻冒出來。

`GUARD_REPAIR_MAX_RUNS = 2` 定義在 `app/main.py:63`，迴圈在 `app/main.py:309-327`。

**成因三：沒有退步偵測。** 沒有任何機制比對編輯前後的錯誤數，模型可以把對的改壞——它就是這麼做的。

### 建議修法

1. 錯誤訊息報**呼叫點**行號而非拋出點。共用 helper 內的例外要往上回溯到呼叫它的那一行。
2. **一次列出同類的全部錯誤**，而不是一輪一個。（`exp/custom-chart-only` 的 gate 已經做過
   「一次列出全部違規 block」的改造，可參考同樣手法。）
3. **修復輪加退步偵測**：編輯後錯誤數沒下降就不要讓它繼續同方向猜。
4. `GUARD_REPAIR_MAX_RUNS` 從固定 2 改成「錯誤數持續下降就繼續，停滯才放棄」。

### 驗證線索

session D 的 13 次編輯**全部是循序的**（每則 AI message 一個 tool call），所以這條與問題 1 無關，
是真的模型收斂失敗。但**修完問題 1 之後要重新確認這個結論**——並行寫入存在時，任何「模型修不好」
的判斷都要打折扣。

---

## 問題 5：未讀 skill 就寫檔

### 資料

32 個「從零重寫 `dashboard.html`」的輪次（07-30 起，排除迭代修改；skill 是否讀過按 **thread
層級**判定，因為同一 thread 內先前輪次讀過就留在 context 裡）：

| 契約進入 context 的程度 | 輪數 | 需修復率 | 終敗率 | 有綁定 `__ERD_RESULTS__` |
|---|---|---|---|---|
| 從未進 context | 3 | 33% | 0% | **1/3（33%）** |
| 只有 SKILL.md | 4 | 75% | 25% | 4/4（100%） |
| SKILL.md + references | 25 | 28% | 12% | 25/25（100%） |

最右欄是決定性的：沒讀過 skill 的輪次三次有兩次**整份 dashboard 完全不綁資料、把數字硬編進
HTML**；只要 skill 進過 context，29/29 全部正確綁定，無例外。

### 兩個反直覺的點（施工前務必理解）

**「只讀 SKILL.md」是所有分組裡最糟的**（75% 需修復、25% 終敗，比完全沒讀還差）。樣本只有 4 筆
不能當定論，但機制清楚：讀了 SKILL.md 之後模型**知道**必須用 `__ERD_RESULTS__` 和 `getCol`，卻
沒看過可運作的範例，於是把形狀搞錯、又忘了 `getCol` 要自己定義（session D 就是這樣）。半套知識
比沒有知識更容易寫出會爆的程式碼。

因此 **gate 的單位必須是 SKILL.md ＋ examples.md**，不能只要求 SKILL.md——否則只是把輪次從「靜默
交付錯誤」推進「最高當機率」那一格。

**「從未進 context」終敗率 0% 是假象**：它的失敗是靜默的。沒讀 skill 的模型退回自己的通用畫法
（資料寫死、單一自足檔案），結構簡單所以不太拋例外，順利通過 guard，然後交給使用者一份每個數字
都可能過期的儀表板。**「需修復率」這個指標衡量的是會不會爆炸，不是對不對**——所以問題 6 的資料
綁定檢查必須一起做，否則你連 gate 有沒有效都量不出來。

### 建議修法

thread 內沒讀過 `SKILL.md` 與 `references/examples.md`，就擋掉對 `dashboard.html` 的
`write_file` / `edit_file`，退貨訊息直接給路徑。

**gate 在寫檔動作上，不要每輪注入。** 成本：SKILL.md 9,620 bytes + examples.md 16,479 bytes
≈ 26KB（約 7–8k tokens），只在真的要寫 dashboard 時付一次，thread 內只付一次。資料證實延續輪
繼承 context 完全正常（「加一個 feedback tab」那類輪次沒有重讀也 100% 正確綁定）。

全部四份 references 共 46KB，每輪注入會加劇 qwen3.6 已知的 reasoning runaway 問題，**不要這麼做**。

---

## 問題 6：兩項缺失的靜態檢查

### 6a：有 `echarts.init` 但全檔零次 `__ERD_RESULTS__` → 退貨

session B 與 `ed59c81b` 兩輪把所有數字硬編進 HTML，順利通過 guard。這類違規目前在指標上是隱形的。

這是 regex 級成本，且是問題 5 的可觀測性前提。

### 6b：有 tab 切換就必須有 resize 派發 → 退貨

session B 的 tab 2 四張圖在 `display:none` 容器裡就 `echarts.init()`，容器寬 0 → ECharts 退回
**100px** fallback。模型自寫的 `switchTab()` 只切 CSS class，沒有派發 resize。

瀏覽器實測：

| 時點 | 容器寬 | ECharts 實際寬 |
|---|---|---|
| 載入後（tab 2 隱藏） | 0 | 100px |
| 點擊切到 tab 2 | 870 | **100px**（仍錯） |
| 派發一次 resize | 870 | 870px（正確） |

skill 其實寫了正確做法——`references/html-contract.md` 的 Tabs 章節明講 resize dispatch 是必要
的，`references/examples.md:253` 有 `window.dispatchEvent(new Event('resize'))` 的完整範本。
session B 沒讀 skill（`read_file` 零次）。

檢查條件：HTML 出現 `onclick="...Tab(` 或多個 panel 容器 → 切換函式體內必須含
`dispatchEvent(new Event('resize'))` 或 `.resize()`。

quickjs 沒有 CSS box model，0 寬容器在執行期檢查中結構上不可見，所以只能靜態檢查。

### session B 的其他違規（同一個根因，供參考）

`switchTab` 用隱式全域 `event`（程式化呼叫直接 `TypeError`）、emoji 標題、無 insight 卡、
init/setOption 無 try/catch（導致 guard 的 `console.error` 收集器沒有訊號可收）、series 硬寫
`color` 陣列蓋掉 serve 時注入的 erd 主題、無 `getCol`、非 Tailwind。這些都由問題 5 的 gate 治，
不需要各自寫檢查。

---

## 驗證方法

### 對任意 workspace 檔案跑 guard

```bash
docker exec -i erd-cowork-deepagent-service-1 /app/.venv/bin/python -c "
import json,pathlib,sys
sys.path.insert(0,'/app')
from app.engine.html_guard import check_dashboard_html
html=sys.stdin.read()
base=pathlib.Path('<session workspace 絕對路徑>')
results={p.stem:json.loads(p.read_text()) for p in (base/'results').glob('*.json')}
r=check_dashboard_html(html,set(results),results)
print('ok=',r.ok)
for e in r.errors: print(' -',e)
" < dashboard.html
```

**必須用 `/app/.venv/bin/python`**；容器裡的 `/usr/local/bin/python` 沒有 quickjs，會靜默跳過
Level 1/2 檢查並回傳假的 `ok=True`。

### 產出使用者實際看到的 HTML

```python
from app.engine.html_guard import check_dashboard_html, referenced_query_ids
from app.engine.results import inject_results
from app.engine.theme import inject_theme
report = check_dashboard_html(html, set(results), results)
final = inject_theme(inject_results(report.html, {q: results[q] for q in referenced_query_ids(report.html)}))
```

注意 `report.html` 已經過 `_apply_erd_theme` 確定性改寫（`echarts.init(X)` → `echarts.init(X, 'erd')`），
與 workspace 上的原始檔不同。

### Langfuse 查詢

```bash
# 單條 trace
curl -s -u pk-lf-erd-cowork-dev:sk-lf-erd-cowork-dev \
  "http://localhost:3010/api/public/traces/<traceId>"

# 依工具名撈 observations（可分頁，meta.totalPages）
curl -s -u pk-lf-erd-cowork-dev:sk-lf-erd-cowork-dev \
  "http://localhost:3010/api/public/observations?name=edit_file&fromStartTime=2026-07-30T00:00:00Z&page=1&limit=100"
```

判定並行批次：同一 traceId 內 `startTime` 落在同一個 100ms 桶的 observations，即同一則 AI
message 並發送出。

### 找 session workspace

```bash
docker exec erd-cowork-deepagent-service-1 sh -c \
  'ls -d /data/workspace/*/sessions/<thread_id>'
```

thread_id 在 trace 的 `metadata.thread_id`。

---

## 施工建議

問題 1 單獨一個 commit 先進，並在真實 session 上重跑一次 session F 的情境確認。它是其他所有結論
的前提。

問題 2 與 3 建議同一批做——manifest 降低綁錯率、warn 收集器保證綁錯被抓到，分開做會看不出效果。

問題 5 與 6a 必須同批：沒有 6a 的檢查，就量不出 gate 有沒有效。

問題 4 可以獨立進行，但要在問題 1 之後重新確認 session D 的診斷仍然成立。

每一項都應該有回歸測試。問題 1 的測試要能實際觸發並發（兩個 `edit_file` 對同一檔案的不相交區段，
斷言兩個改動都在），否則測不到東西。
