# 實驗:dashboard 單次完整寫入(single-write)

- 日期:2026-08-02
- 分支:`exp/single-write-dashboard`(worktree,基於 master `1677b63`)
- 定位:對照實驗。與 master baseline 同題組比較,結論寫回本檔附錄後再決定是否轉正。

## 背景與假說

deepagent 產出的 dashboard.html 品質劣化有兩個結構性成因:

1. **局部 edit 累積壞狀態**:`edit_file` 字串替換只盯著要改的幾行,不會重新審視與其餘數百行的一致性;跨多輪 edit 後,「30 行前建的契約、30 行後忘了」類 bug(例:tooltip formatter 讀物件屬性、series data 卻是陣列)持續出貨。
2. **邊取數邊寫**:HTML 在長軌跡尾端生成,context 被 skill 全文、SQL 往返稀釋,跨行一致性進一步下降。

**假說**:改成「所有取數完成 → 單一次 `write_file` 寫出完整 HTML;任何修改一律整份重寫」後,guard 觸發率、修復輪數、互動期 runtime bug 應下降。

## 設計

改動全部落在 `deepagent-service/`,四個點:

### 1. `DashboardOverwriteBackend` 確定性封鎖 edit(實驗的牙齒)

`app/agent/graph.py` 的 `DashboardOverwriteBackend` 覆寫 `edit`/`aedit`:resolved path 為 workspace root 的 `dashboard.html` 時直接回 `EditResult(error=...)`,錯誤訊息即行為指令(告知模型:dashboard.html 只能以單一次 `write_file` 整份重寫——先完成所有取數,再寫完整 HTML)。其他檔案(`notes.md`、query 檔等)的 edit 行為不變。路徑判定沿用既有 `write()` overwrite 洞的同款 resolve 邏輯。

不依賴 prompt 遵從:模型呼叫 `edit_file` 會確定性退貨,錯誤訊息本身就是恢復路徑。

### 2. dashboard skill 工作流改寫

`skills/dashboard/SKILL.md` 的產出流程改為:

- 所有 `run_sql` 取數完成 → 規劃版面/圖表 → **單一次 `write_file` 寫出完整 dashboard.html**。
- 迭代修改(含 guard 修復、使用者改需求):`read_file` 讀現版 → 整份重寫,NEVER 局部修補。
- 移除/改寫所有指向 `edit_file` 的指引。`references/*.md` 中如有同類指引一併同步。

### 3. guard 修復訊息改字

`app/main.py` 修復訊息 `"Dashboard failed quality checks. Fix dashboard.html with edit_file:"` 改為指示整份重寫(單一次 `write_file`)。修復迴圈的輪數上限(`GUARD_REPAIR_MAX_RUNS=5`)、停滯判定(`_guard_repair_should_stop`)、mtime 觸發、`/repair` 端點全部不動——保持與 baseline 可比。

### 4. 測試

- `DashboardOverwriteBackend.edit`/`aedit`:擋 `dashboard.html`(含以絕對/相對路徑指涉)、放行其他檔案、錯誤訊息含「write_file 整份重寫」指令。
- 既有測試如有釘 skill 內容或修復訊息字樣,同步更新。
- gate:`uv run pytest` 全綠(不接 pipe,顯式看 exit code)。

## 不動範圍

mtime 觸發判定、`check_dashboard_html` 本體、`/repair`、Java backend、前端、SSE 事件協定。零跨服務改動。

## 衡量

同題組(`deepagent-service/eval/questions.md` 5 題)跑 master baseline vs 本分支:

- guard 觸發率(首次檢查即過的比率)、修復輪數分佈、終敗率(`dashboard guard failed`)。
- 互動期 runtime bug 抽查:tooltip/hover 操作(本次 SPC 漂移案例的 formatter 資料形狀錯誤作為固定手動測項)。
- Langfuse trace 對照 token 成本與 turn 時長(整份重寫的修復輪成本預期高於 edit,記錄實際差距)。

## 風險與接受條件

- **tool-arg 截斷**:單次 write 的 HTML(~6-8k tokens)截斷風險比多次 edit 高;`AGENT_MAX_TOKENS=32768` 預期足夠,實驗期觀察。guard 的結構檢查會攔截斷檔。
- **修復輪 token 成本**:每輪整份重寫,成本高於 edit;實驗期接受,列入衡量。
- 實驗結論(轉正/放棄/混合)寫回本檔附錄。
