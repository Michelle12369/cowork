# edit_file 重新啟用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** deepagent-service 重新開放 `edit_file`（全部檔案含 dashboard.html），以量化 prompt 規則引導大改動走單次 `write_file`，含 edit match-失敗斷路器。

**Architecture:** 解除兩層封印（`graph.py` harness profile 的 `excluded_tools`、`DashboardOverwriteBackend.edit()` 退貨 override）；`prompts.py` SYSTEM_PROMPT 與 `skills/dashboard/SKILL.md` 換上量化規則（>3 處／約 1/3／結構性 → write_file；match 失敗 → 不重試 edit、read 後整份重寫）；修復路徑維持 write_file-only 不動；`architecture.md` 對齊。

**Tech Stack:** Python 3.11 / deepagents 0.5.5（`FilesystemBackend.edit` 為 old_string/new_string 字串替換）/ pytest / uv。

**Spec:** `docs/superpowers/specs/2026-08-05-edit-file-reenable-design.md`。

## Global Constraints

- 寫 Python 前先讀 `.claude/skills/fastapi/SKILL.md`（本 plan 主要是 agent 層與 prompt，仍照讀）
- 修復路徑 NEVER 改動：`chat_turn.py` html_guard 退貨修正指示（single write_file）、瀏覽器修復輪、`middleware.py` `_GATED_TOOL_NAMES`、`events.py` STEP 對應
- 量化規則的三個條件與斷路器文字語意 MUST 完整出現在 SYSTEM_PROMPT 與 SKILL.md 兩處（英文行文，數字 verbatim：**3 處**、**約三分之一**）
- 每個 task 結束：`cd deepagent-service && uv run pytest -q` 全綠＋`uv run ruff check app tests`
- 執行 branch：`feat/edit-file-reenable`
- 註解精簡 1–2 行；google-java-format 不適用（Python 走 ruff）

## 現況地圖（實作者必讀）

| 位置 | 現況 | 本 plan 動作 |
|---|---|---|
| `app/agent/graph.py:46-52` | `register_harness_profile("openai", HarnessProfile(..., excluded_tools=frozenset({"edit_file"})))` | 移除 `excluded_tools` 參數（`general_purpose_subagent` 停用保留） |
| `app/agent/graph.py:30-39` | `_EDIT_REJECTED_FILE_NAME`、`DASHBOARD_EDIT_REJECTED_MESSAGE` 常數 | 刪除 |
| `app/agent/graph.py:75-91` | `DashboardOverwriteBackend.edit()` override（dashboard.html 退貨） | 整個 override 刪除（`write()` 的 unlink 保留）；class docstring 改寫 |
| `app/agent/prompts.py:16-18` | 「File changes are always full rewrites… There is no edit tool.」 | 換量化規則段落 |
| `skills/dashboard/SKILL.md:25-28` | 「Always rewrite the whole file… there is no edit tool… no "small edit" path」 | 換量化規則 bullet（29 行 overwrite-allowed、34-37 行 preserve-everything 保留） |
| `tests/test_graph.py:75-84` | 斷言 `excluded_tools == frozenset({"edit_file"})` | 反轉：斷言 excluded_tools 為空/未設 |
| `tests/test_filesystem_jail.py:87-110` | 兩個 dashboard.html edit 退貨測試 | 改寫為「edit 正常生效」測試；30/49 行過時註解一併更新 |
| `tests/test_chat.py:649-723` | 併發 edit_file lost-update 回歸（notes.md） | 不動（本來就綠） |
| `docs/architecture.md:574` | guard 修復訊息寫「with edit_file」 | 對齊現行 code（`chat_turn.py:319` 是 single write_file）——此行在 single-write 實驗後就過時了，順手修正 |
| `docs/architecture.md:24,73,169,213,636` | 描述 write_file/edit_file 並存 | 重新啟用後這些行恢復正確——逐行確認即可，預期不用改 |

`FilesystemBackend.edit` 簽名：`edit(file_path, old_string, new_string, replace_all=False) -> EditResult`（deepagents 內建；`EditResult.error` 為 None 即成功）。

---

### Task 1: 解除兩層封印（graph.py）＋測試反轉

**Files:**
- Modify: `deepagent-service/app/agent/graph.py:30-39,46-52,53-91`
- Test: `deepagent-service/tests/test_graph.py:75-84`、`deepagent-service/tests/test_filesystem_jail.py:30,49,87-110`

**Interfaces:**
- Produces: `DashboardOverwriteBackend` 只剩 `write()` override；harness profile 無 `excluded_tools`；`DASHBOARD_EDIT_REJECTED_MESSAGE` 不存在（Task 2/3 的文案不得再引用它）

- [ ] **Step 1: 先改測試（紅）**

`tests/test_graph.py` 的 `test_openai_harness_profile_excludes_edit_file` 改為：

```python
def test_openai_harness_profile_does_not_exclude_tools() -> None:
    """edit_file 重新開放:模型可見完整工具 schema,大改動改用 write_file 由 prompt 量化規則引導
    (見 SYSTEM_PROMPT),不再物理剝除。"""
    profile = get_harness_profile("openai")
    assert profile.general_purpose_subagent.enabled is False
    assert not profile.excluded_tools
```

（`get_harness_profile` 依該檔既有取用方式；若原測試直接讀 registry，照抄其模式。）

`tests/test_filesystem_jail.py:87-110` 兩個退貨測試改寫為行為測試：

```python
def test_dashboard_edit_applies_in_place(tmp_path) -> None:
    """edit_file 重新開放:dashboard.html 可局部編輯,不再退貨。"""
    backend = _build_backend(tmp_path)  # 照該檔既有 helper
    (tmp_path / "dashboard.html").write_text("<html>OLD</html>", encoding="utf-8")

    edit_result = backend.edit("dashboard.html", "OLD", "NEW")

    assert edit_result.error is None
    assert (tmp_path / "dashboard.html").read_text(encoding="utf-8") == "<html>NEW</html>"


def test_dashboard_edit_missing_old_string_returns_error(tmp_path) -> None:
    """old_string 不存在時回 error(deepagents 內建行為)——prompt 斷路器規則的觸發面。"""
    backend = _build_backend(tmp_path)
    (tmp_path / "dashboard.html").write_text("<html>OLD</html>", encoding="utf-8")

    edit_result = backend.edit("dashboard.html", "ABSENT", "NEW")

    assert edit_result.error is not None
```

（helper 名稱照該檔實際寫法；30、49 行的過時註解——「edit_file 從模型可見工具移除後」等——同步改寫成現況敘述。）

- [ ] **Step 2: 跑測試確認紅**

Run: `cd deepagent-service && uv run pytest tests/test_graph.py tests/test_filesystem_jail.py -q`
Expected: FAIL（excluded_tools 仍在、edit 仍退貨）

- [ ] **Step 3: 改 graph.py**

1. 刪 `_EDIT_REJECTED_FILE_NAME`、`DASHBOARD_EDIT_REJECTED_MESSAGE` 常數與其註解區塊。
2. harness profile 改為：

```python
# 關掉 general-purpose subagent:它曾委派子任務「用 Python 算迴歸」給自己,寫了 .py 腳本卻
# 沒有執行機制,繞了好幾分鐘才改用 SQL。
register_harness_profile(
    "openai",
    HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
)
```

3. `DashboardOverwriteBackend`：刪整個 `edit()` override 與相關 import（`EditResult` 若只被 override 使用則移除）；class docstring 改為：

```python
class DashboardOverwriteBackend(FilesystemBackend):
    """dashboard.html/notes.md 可整份覆寫:parent 預設 create-only 會擋掉已存在檔案的
    write,故先 unlink `_OVERWRITABLE_FILE_NAMES` 再委派。局部編輯走 parent 的 edit()
    (edit_file 已重新開放,大改動改用 write_file 由 prompt 引導)。"""
```

- [ ] **Step 4: 跑全套確認綠＋ruff**

Run: `cd deepagent-service && uv run pytest -q && uv run ruff check app tests`
Expected: 全綠（`test_chat.py` 併發 edit 測試本來就綠；若有其他測試引用被刪常數，一併修）

- [ ] **Step 5: Commit**

```bash
git add -A deepagent-service
git commit -m "feat: 重新開放 edit_file——移除 schema 剝除與 dashboard.html 退貨"
```

---

### Task 2: 量化引導文案（SYSTEM_PROMPT ＋ dashboard SKILL.md）

**Files:**
- Modify: `deepagent-service/app/agent/prompts.py:16-18`
- Modify: `deepagent-service/skills/dashboard/SKILL.md:25-28`（29、34-37 行保留）
- Test: `deepagent-service/tests/`（grep `There is no edit`／`full rewrites` 找既有斷言，改斷言新關鍵句；`test_dashboard_skill.py` 若驗 SKILL 內容一併更新）

**Interfaces:**
- Consumes: Task 1 已刪退貨機制（文案不再提退貨）
- Produces: 兩處文案含 verbatim 關鍵句 "more than 3 separate edits"、"about one third"、"do NOT retry another edit"

- [ ] **Step 1: SYSTEM_PROMPT 段落替換**

`prompts.py` 原「File changes are always full rewrites: read the current file, then write the complete updated content with a single write_file call. There is no edit tool. dashboard.html and notes.md may be overwritten this way.」整段換成：

```
- File edits: use edit_file for small, localized changes (retitle, recolor, fix one chart \
option, tweak a sentence). Rewrite the whole file with a single write_file call instead \
when ANY of these holds: the turn needs more than 3 separate edits to the same file, the \
change touches more than about one third of the file, or the layout is restructured \
(sections or charts added/removed/reordered). If an edit_file call fails to find its old \
string, do NOT retry another edit: read the current file again, then produce one full \
write_file rewrite. dashboard.html and notes.md may be overwritten either way.
```

- [ ] **Step 2: SKILL.md bullet 替換**

`skills/dashboard/SKILL.md` 25-28 行（「Always rewrite the whole file with a single `write_file` call -- there is no edit tool; … no "small edit" path …」）換成：

```
   - **Small, localized changes** (retitle, recolor, fix one chart option) may use
     `edit_file`. **Rewrite the whole file with a single `write_file` call instead** when
     any of these holds: the turn needs more than 3 separate edits to dashboard.html, the
     change touches more than about one third of the file, or the layout is restructured
     (sections/charts added/removed/reordered). If an `edit_file` fails to find its old
     string, do NOT retry another edit -- read the file again and do one full `write_file`
     rewrite.
```

（縮排與前後 bullet 對齊；29 行 overwrite-allowed、34-37 行「NEVER rewrite from memory／Preserve everything」原樣保留——後者對 write_file 路徑仍然成立。）

- [ ] **Step 3: 測試 sweep**

Run: `cd deepagent-service && grep -rn "There is no edit\|always full rewrites\|no \"small edit\"" app tests skills`
Expected: 零筆。既有測試若斷言舊句子（grep `full rewrite` in tests/），改斷言新關鍵句（例：`assert "more than 3 separate edits" in SYSTEM_PROMPT`）。

- [ ] **Step 4: 跑全套＋ruff**

Run: `cd deepagent-service && uv run pytest -q && uv run ruff check app tests`
Expected: 全綠

- [ ] **Step 5: Commit**

```bash
git add -A deepagent-service
git commit -m "feat: edit/write 量化引導規則進 SYSTEM_PROMPT 與 dashboard skill"
```

---

### Task 3: architecture.md 對齊

**Files:**
- Modify: `docs/architecture.md:574`（必改）；`:24,73,169,181,213,636`（逐行確認，預期恢復正確不用改）

**Interfaces:**
- Consumes: Tasks 1–2 落地後的實際行為

- [ ] **Step 1: 574 行修正**

「回餵錯誤清單給模型（`"Dashboard failed quality checks. Fix dashboard.html with edit_file:\n- ..."`）」——對照 `app/agent/chat_turn.py` 實際字串（現行為 single write_file 指示），把引號內文案改成與 code 一致（引 `chat_turn.py:319` 附近實際訊息），並確認前後文語意不再暗示修復輪走 edit。

- [ ] **Step 2: 其餘行逐一確認**

`24,73,169,181,213,636` 各行描述 write_file/edit_file 並存或 edit_file 工作副本語意——edit_file 重新開放後應全部恢復正確。逐行讀過：正確→不動；發現仍與現況矛盾→最小修正。181 行（repair message「請用 edit_file 修正」）與 574 同源，一併對齊 code 實際字串。

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: architecture 對齊 edit_file 重新開放與 guard 修復訊息現況"
```

---

## Self-Review 紀錄

- Spec 覆蓋：§1 解封（T1）、§2 量化引導＋斷路器（T2）、§3 不變項（Global Constraints 明列）、§4 文件（T2 SKILL＋T3 architecture）、§5 測試（T1 反轉＋T2 sweep）——齊。
- 型別一致：`DashboardOverwriteBackend` 僅剩 `write()`；文案關鍵句兩處 verbatim 同步。
- 已知妥協：斷路器為 prompt 級（spec 已載明風險與升級路徑）。
