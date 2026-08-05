# deepagent-service：重新啟用 edit_file（量化引導大改動走 write_file）

## 背景與目標

`edit_file` 是 deepagents 內建工具，先前被兩層機制刻意移除（single-write 實驗）：`graph.py` 的 harness profile `excluded_tools` 把它從模型可見 schema 物理剝除；`DashboardOverwriteBackend.edit()` 對 `dashboard.html` 一律確定性退貨。當時原因：模型會無視退貨訊息陷入 read→edit→退貨迴圈直到 recursion limit。

目標：重新開放 `edit_file`（**全部檔案，含 dashboard.html**），小改動用局部編輯省 token；以 **具體量化的 prompt 規則** 引導模型在大改動時改用單次 `write_file` 整份重寫，並加入 edit 失敗時的迴圈斷路器規則。

## 非目標

- 不做 backend 層的確定性改動量閥門（prompt 引導先行；若上線後觀察到 string-match 失敗迴圈再補「同檔連續 N 次 edit 失敗 → 退貨強制 write_file」的確定性斷路器）。
- 修復路徑（html_guard 退貨後的修正指示、瀏覽器錯誤修復輪）維持 write_file-only 不變。
- 不動 middleware 並發 gate 與 events 的 STEP 對應（已支援 edit_file，零改動）。

## 設計

### 1. 解除封印（`app/agent/graph.py`）

- harness profile 移除 `excluded_tools=frozenset({"edit_file"})`（`general_purpose_subagent` 停用維持不變）。
- 刪除 `DashboardOverwriteBackend.edit()` override 與 `DASHBOARD_EDIT_REJECTED_MESSAGE`、`_EDIT_REJECTED_FILE_NAME`；`write()` 的 `_OVERWRITABLE_FILE_NAMES` unlink 邏輯保留。class docstring 同步改寫（不再是 single-write invariant，改為「dashboard.html/notes.md 可整份覆寫」的說明）。

### 2. Prompt 量化引導（`app/agent/prompts.py` SYSTEM_PROMPT）

取代現有「File changes are always full rewrites … There is no edit tool.」段落，新規則（英文行文與現有 prompt 一致，語意如下）：

- 小而局部的修改（改標題、換顏色、修單一 chart option、改一段文字）→ 用 `edit_file`。
- 出現**任一**條件 → 不要用 edit，直接讀完現況後以**單次** `write_file` 整份重寫：
  1. 同一檔案這一輪需要**超過 3 處**編輯；
  2. 改動範圍超過檔案內容**約三分之一**；
  3. **結構性調整**（新增／刪除／重排 section 或 chart）。
- **迴圈斷路器**：`edit_file` 回報找不到要替換的字串（match 失敗）時，**不要重試 edit**——重新 `read_file` 後改用單次 `write_file` 整份重寫。

### 3. 維持不變（明確列出，防止實作誤動）

- `chat_turn.py` html_guard 退貨修正指示（single write_file）與修復輪（write_file 整份重寫）——修復場景天生大改動，落在規則的 write_file 側。
- `middleware.py` `_GATED_TOOL_NAMES`（已含 edit_file）。
- `events.py` `write_file`/`edit_file` 的 STEP 事件對應。
- html_guard 驗證落地後的完整檔案內容，與寫入方式無關。

### 4. 文件

- `deepagent-service/skills/dashboard/SKILL.md`（及 references 若有提及）：full-rewrite 敘述改為「小改動 edit_file／大改動整份 write_file」與量化規則摘要。
- `docs/architecture.md` 若有 single-write／no-edit 敘述，一併更新。

### 5. 測試

- graph 測試：`edit_file` 不在 excluded_tools；`DashboardOverwriteBackend` 無 edit override（對 dashboard.html 的 edit 走 parent 正常路徑，不退貨）。
- 既有退貨訊息相關測試刪除或改寫。
- prompt 內容測試（若有斷言舊句子）更新為斷言新規則關鍵句（如 "more than 3"、"one third"、斷路器句）。
- 全套 `uv run pytest -q` ＋ `uv run ruff check app tests` 綠。

## 已知風險（記錄決策）

模型（qwen3.6-35b）可能不理會 prompt 級斷路器而卡在 edit match-失敗迴圈——已知、接受；升級路徑見「非目標」第一條。`recursion limit`（`AGENT_RECURSION_LIMIT=80`）仍是最終保險。
