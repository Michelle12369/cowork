# deep agent guard — dashboard.html 確定性檢查清單

> deepagent-service 的 `app/engine/html_guard.py`（`check_dashboard_html`）。模型寫完
> `dashboard.html`、系統把 `DASHBOARD_HTML` 發送給前端**之前**的最後一道確定性關卡。
> engine 層 stdlib only（ruff TID251 擋掉任何 LLM 框架 import）；不是 LLM 判斷，是逐條可
> 重現的規則。錯誤訊息刻意寫成「可直接餵回模型做下一輪修復」的具體可操作句子。

---

## 何時跑、跑完怎麼辦

- **觸發時機**（`app/main.py`）：一輪 agent turn 結束後，只有 `dashboard.html` 存在**且
  mtime 有變**（本輪確實寫過檔，不是沿用前輪殘留）才進入檢查。
- **入口**：`check_dashboard_html(html, available_query_ids, results)`——回傳 `GuardReport`
  （`ok` / `errors` / `html`）。`available_query_ids` 是本 session 實際跑過的查詢結果 id；
  `results` 是真實欄名/資料（給 Level 2 sandbox 灌真值用）。
- **不 fail-fast**：規則之間互不短路，一次收集**全部**違規，讓模型一輪修完。
- **修復迴圈**：`report.ok` 為 false 時，把 `errors` 條列進 repair prompt、要模型用
  `edit_file` 修，重讀重檢，最多 `GUARD_REPAIR_MAX_RUNS`（=5）輪。實際輪數由「錯誤集合是否
  還在變化」決定（`_guard_repair_should_stop`）：集合完全相同＝卡住、數量增加＝改壞，兩者
  皆立即停。跑滿仍不過只記 warning（NEVER log HTML），不擋 dashboard 送出。
- **回傳的 html 可能被改寫**：唯一會確定性改寫原文的是 erd 主題注入（見下方第 9 條）；
  其餘規則只讀不改。

---

## 檢查清單（依 `check_dashboard_html` 執行順序）

| # | 檢查 | 觸發條件 | 判定 |
|---|---|---|---|
| 1 | 結構完整性 | 一律 | HTML 為空或不含任何 `<div>` → 退件 |
| 2 | 體積上限 | 一律 | UTF-8 位元組數 > `HTML_MAX_BYTES`（2,000,000）→ 退件 |
| 3 | CDN script 白名單 | 任何帶 `src` 的 `<script>` | host 精確比對 |
| 4 | 禁止自帶主題 | 一律 | 出現 `registerTheme(` → 退件 |
| 5 | 查詢結果引用一致性 | 一律 | 引用了 `available_query_ids` 以外的 qN → 退件 |
| 6 | inline JS 語法（Level 1） | 有 inline `<script>` | quickjs parse-only |
| 7 | sandbox 執行 smoke（Level 2） | Level 1 乾淨時才跑 | quickjs 真的 eval |
| 8 | tooltip 全缺 | 有 `echarts.init(` | 整份 HTML 無 `tooltip` 字樣 → 退件 |
| 9 | 資料綁定 | 有 `echarts.init(` | 整份 HTML 無 `__ERD_RESULTS__` → 退件 |
| 10 | tab 規範 | 僅 HTML 含 tab 結構時 | resize 防護 + Tabler 底線樣式 |
| 11 | erd 主題強制（會改寫） | 每個 `echarts.init(` | 單參補 `'erd'`；非 erd 第二參退件 |

### 1. 結構完整性（`_check_structure`）
HTML 內容為空，或整份找不到任何一個 `<div>` 元素 → 判定「內容不完整」退件。攔的是模型
只回了純文字、沒真的產出 dashboard 的情況。

### 2. 體積上限（`_check_size`）
以 UTF-8 位元組計，超過 2 MB 退件，訊息提示模型精簡（去掉冗餘註解、內嵌資料、重複樣式
定義）。

### 3. CDN `<script src>` 白名單（`_check_script_src_whitelist`）
掃出**任何**帶 `src` 的 `<script>` 開始標籤（引號有無、標籤名後接空白或 `/` 皆可），解析
URL 做 **host 邊界比對**（`_is_allowed_script_src`）：scheme 必為 `https`、host 必**精確
等於**白名單之一——
- `cdn.tailwindcss.com`
- `cdn.jsdelivr.net`（且 path 落在 `/npm/echarts@` 底下）

**NEVER 用 `src.startswith(prefix)`**：那會被 `https://cdn.tailwindcss.com.evil.example/x.js`
這類 lookalike host 繞過（前綴合法，但真正 host 是攻擊者控制的網域）。`urlsplit().hostname`
只取真 host，不受這招影響。此關卡跑在生成期，角色是確保模型寫的 src 是 serve 期
`ArtifactCdnRewriter` 認得、能換寫成 `/vendor/` 的網址——render 正確性 + defense-in-depth
（真正安全邊界在 serve 層 CSP）。

### 4. 禁止自帶主題（`_check_no_register_theme`）
出現 `registerTheme(` 呼叫 → 退件。主題由系統在送出前用 `inject_theme` 統一注入 'erd'；
模型自帶主題定義會蓋掉它。skill 已指示「NEVER 自呼叫」，本條把指示變成強制關卡。

### 5. 查詢結果引用一致性（`_check_referenced_query_ids`）
從 HTML 抽出所有被引用的 query result id，減去 `available_query_ids`；有差集（引用了不存在
的 qN）→ 退件，列出缺的 id。攔「憑記憶綁 qN、綁到根本沒跑過的查詢」。

### 6. inline JS 語法檢查 — Level 1（`_check_js_syntax`）
逐段抽出 inline（無 `src`）`<script>` 內文，各自包進 `(function(){ ... })` 丟給
quickjs `eval`——只「定義」函式表達式（不呼叫），引擎仍對函式本體做完整語法解析但不執行，
等同 **parse-only**。只抓 `SyntaxError`，抓不到（也不該抓）未宣告變數這類 runtime 錯誤。
錯誤訊息帶 HTML 絕對行號（換算掉 script 起始行 + 包裝行偏移）。

`<script>` 內文抽取用逐字 port 自 backend `JsSyntaxValidator.java` 的狀態機
（`_find_script_end`），對字串字面值/註解裡的 `</script>` 免疫（例如 ECharts tooltip
formatter 內含這段文字不會被誤判成標籤結束）。

### 7. sandbox 執行 smoke — Level 2（`_execute_scripts_smoke`）
**只在 Level 1 乾淨時才跑**（語法已錯就不必也不該真的執行壞掉的 script）。在一個
absorb-all 的假 DOM / ECharts sandbox 裡**真的 eval** 每段 inline script，抓 Level 1
看不到的執行期錯誤：

- **未宣告變數（ReferenceError）**：**multi-mole 掃描**——單一 block 內有多個未宣告變數
  時，逐一記錄（含行號）、把該變數 stub 成 absorb proxy、重建全新 Context、把此 block 之前
  的所有 block 靜默重放、再重跑，直到不再拋新 ReferenceError 或達
  `_MAX_REFERENCE_ERROR_RETRIES_PER_BLOCK`（=8）。一次退貨列出多個，不用一輪只修一個。
- **對 undefined / null 取屬性（TypeError）**：sandbox 用 `_extract_known_element_ids`
  把整份 HTML 裡真實出現過的 `id="..."` 灌進 `getElementById`/`querySelector('#id')`；引用
  **不存在**的 id 如實回 `null`（與真瀏覽器同語意），後續對 `null` 取屬性的 TypeError 就被
  抓到。動態拼接的 id 只要拼出來的字面值在 HTML 某處真的存在就命中。
- **被 chart 自己 try/catch 吞掉的執行期錯誤**（`_check_swallowed_chart_errors`）：skill
  範本規定每張圖 init+setOption 包 try/catch、catch 裡呼叫
  `console.error('[ERD] chart <名稱> failed:', error)`。sandbox 的 `console.error` 是
  **收集器**不是 no-op；跑完所有 block 後把 `[ERD] chart ... failed:` 開頭的訊息轉成 guard
  error——讓被 try/catch 擋下、不會冒 JSException 的錯誤也能確定性攔下（try/catch 是 damage
  control，不是修好）。
- **getCol 綁錯欄位（無聲錯誤渲染）**（`_check_column_not_found_warnings`）：getCol 樣板找
  不到欄位時只 `console.warn('[ERD] column not found:', candidates)` 並回 `-1`，圖會渲染成
  空白/undefined/NaN 但不拋例外。sandbox 的 `console.warn` 同樣是收集器；把這類 warn 轉成
  guard error，並**回頭比對真實 `results` 算出「該欄位其實在哪個 qN」**，讓模型一輪綁對而
  不是猜。**只在整份 `results` 都是真實欄名時才判定**——退回泛用假欄名（`__c0`/`__c1`）時
  每個 getCol 都會 miss，轉 error 會全是誤報。一次最多列 `_MAX_REPORTED_COLUMN_MISSES`
  （=8）條，其餘一行摘要帶過。
- **執行逾時 / 無窮迴圈**：每段 script 各自 `_SANDBOX_TIME_LIMIT_SECONDS`（=2.0s）CPU 預算，
  逾時回報「possible infinite loop」並停該段。

sandbox 用真實欄名/前幾列真值灌 `window.__ERD_RESULTS__`（`_results_literal_for_sandbox`），
讓「按真實欄名查找」的閘門真的打開、閘門後的圖表初始化程式碼才會被執行到。共享 helper
（如 skill 規定的 `getCol`，被每張圖呼叫）拋錯時，錯誤訊息回報**呼叫點**而非 throw 點，並列
出該 helper 的所有呼叫點——同一 defect 通常打中全部，一輪修完（`_format_execution_error`）。

### 8. tooltip 全缺（`_check_tooltip`）
粗粒度規則：HTML 含至少一個 `echarts.init(` 呼叫，就整份 HTML 必須出現過 `tooltip` 字樣。
正確做法（依圖表類型設對 trigger）交給 skill 教，guard 只擋「整份完全沒有任何 tooltip
設定」。

### 9. 資料綁定（`_check_data_binding`）
有 `echarts.init(` 但整份 HTML 零次引用 `__ERD_RESULTS__` → 退件。代表數字被硬編進 HTML
——不會拋例外、能過其他檢查，但交付的每個數字都可能是過期的。每張圖/KPI/表格都 MUST 從
`window.__ERD_RESULTS__['<query id>']` 讀資料。

### 10. tab 規範（`_check_tab_conventions`）
**僅在 HTML 含 tab 結構時觸發**（`_has_tab_structure`：`showTab(` / `id="panel-0"` /
`role="tab"`，或 `onclick="...Tab("` + 多個 `panel-N` 容器）。一般 dashboard 零檢查、零誤報。
- **resize 防護**：切 tab 時沒在**切換函式體內** dispatch resize event（或呼叫
  `.resize()`）→ 退件。hidden panel 裡的 ECharts 建立時量到 0 寬容器，會卡在 100px fallback
  變空白。resize 片語 MUST 出現在切換函式**體內**（`_tab_switch_function_bodies` 依
  onclick 綁定 > `*Tab` 命名慣例的優先順序找候選）；寫在模組層級的
  `window.addEventListener('resize', ...)` 救不了這個 bug，只在使用者手動縮放視窗時巧合生效。
- **Tabler 底線樣式**：缺 `border-b-2` class → 退件。skill 的 tabs 範本一律用底線式 active
  態，缺它代表偏離規範（藥丸/segmented 樣式）。

### 11. erd 主題強制 —— 會改寫原文（`_apply_erd_theme`）
掃描每個 `echarts.init(...)`（對括號做深度平衡掃描，正確處理引數本身含括號如
`document.getElementById("chart")`）：
- **單參數** `echarts.init(X)` → 確定性改寫為 `echarts.init(X, 'erd')`（不退件，直接補上）。
- **雙參數且第二參非** `'erd'`/`"erd"` → 記 error 退件、原樣保留（不強改模型的自訂主題，
  要模型自己移除或改成 erd）。

改寫後的 HTML 就是 `GuardReport.html`，是實際會被送出/存檔的版本。

---

## 設計原則

- **驗證器掛掉不連累主流程**：quickjs 是選配依賴，import 失敗時 Level 1/2 都只記 warning、
  整條跳過，不擋 dashboard 送出（比照 backend `JsSyntaxValidator` 的哲學）。sandbox 初始化
  失敗、context 重建失敗、收集器讀取失敗——一律 warning + 降級，不拋。
- **一次收齊全部違規**：規則互不 fail-fast，讓模型一輪看到所有問題。唯一例外是 Level 2
  只在 Level 1 乾淨時才跑（不對語法已壞的 script 硬 eval）。
- **錯誤訊息即修復 prompt**：每條訊息都寫成具體可操作、帶 HTML 絕對行號、指向根因（哪個
  欄位在哪個 qN、哪個 helper 的哪些呼叫點）的句子，直接餵回模型。
- **確定性 > LLM 判斷**：模型（gpt-oss 級，不可升級）品質靠這種可重現的結構性關卡兜底，
  不靠模型自律。skill 指示只是「教」，guard 才是「強制」。
