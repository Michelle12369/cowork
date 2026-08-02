# Single-Write Dashboard 實驗 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** dashboard.html 的所有修改改為「單一次 `write_file` 整份重寫」,`edit_file` 對該檔確定性封鎖,驗證單次完整寫入能降低 guard 觸發率與互動期 bug(spec: `docs/superpowers/specs/2026-08-02-single-write-dashboard-design.md`)。

**Architecture:** 三個落點——(1) `DashboardOverwriteBackend` 覆寫 `edit` 拒絕 dashboard.html(牙齒,不靠 prompt);(2) dashboard skill 的修改流程改教整份重寫;(3) guard 修復訊息改指示 `write_file`。mtime 觸發、guard 本體、修復迴圈輪數/停滯判定、`/repair`、Java/前端全部不動。

**Tech Stack:** Python 3.12 / FastAPI / deepagents 0.6.x(`FilesystemBackend`)/ pytest。

## Global Constraints

- 全部改動限 `deepagent-service/`;工作樹 `/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render`,分支 `exp/single-write-dashboard`。
- 寫 Python 前先讀 `.claude/skills/fastapi/SKILL.md`(專案規則)。
- 變數/參數 NEVER 用 1–2 字元名稱;一律描述性單詞。
- 註解 1–2 行寫目的+做法;NEVER 寫 spec 編號/commit hash/事故敘事。
- 測試指令 NEVER 接 `| tail`(pipe 吞 exit code);要看結尾就跑完再看。
- `FilesystemBackend.aedit`/`awrite` 內部 `asyncio.to_thread(self.edit/write)` 委派同步版——只覆寫同步方法即可,async 路徑自動生效(現有 `write()` overwrite 洞同款前提)。
- deepagents `EditResult`:`EditResult(error="...")` 表失敗;成功為 `EditResult(path=..., occurrences=...)`。

---

### Task 1: `DashboardOverwriteBackend` 封鎖 dashboard.html 的 edit

**Files:**
- Modify: `deepagent-service/app/agent/graph.py`(`DashboardOverwriteBackend`,現於 44–67 行)
- Test: `deepagent-service/tests/test_filesystem_jail.py`

**Interfaces:**
- Produces: `DashboardOverwriteBackend.edit(file_path, old_string, new_string, replace_all=False) -> EditResult`——dashboard.html 一律回 `EditResult(error=DASHBOARD_EDIT_REJECTED_MESSAGE)`;其他檔案照舊。
- Produces: module 層常數 `DASHBOARD_EDIT_REJECTED_MESSAGE`(Task 3 的修復訊息與其口徑一致,但不互相 import)。

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_filesystem_jail.py` 追加(沿用檔內既有 `tmp_path` + backend 建構模式):

```python
def test_dashboard_edit_rejected_with_rewrite_instruction(tmp_path) -> None:
    """dashboard.html 的 edit_file 一律退貨,錯誤訊息本身指示改用單次 write_file 整份重寫。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "dashboard.html").write_text("<html><body>OLD</body></html>", encoding="utf-8")
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    edit_result = backend.edit("dashboard.html", "OLD", "NEW")

    assert edit_result.error is not None
    assert "write_file" in edit_result.error
    assert (root / "dashboard.html").read_text(encoding="utf-8") == "<html><body>OLD</body></html>"


def test_dashboard_edit_rejected_via_absolute_style_path(tmp_path) -> None:
    """virtual_mode 會把絕對路徑重新錨定到 root 內——用絕對路徑指涉 dashboard.html 一樣被擋。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "dashboard.html").write_text("x", encoding="utf-8")
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    edit_result = backend.edit("/dashboard.html", "x", "y")

    assert edit_result.error is not None
    assert "write_file" in edit_result.error


def test_other_files_still_editable(tmp_path) -> None:
    """封鎖只針對 dashboard.html——notes.md 等其他檔案的 edit 行為不變。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "notes.md").write_text("draft", encoding="utf-8")
    backend = DashboardOverwriteBackend(root_dir=str(root), virtual_mode=True)

    edit_result = backend.edit("notes.md", "draft", "final")

    assert edit_result.error is None
    assert (root / "notes.md").read_text(encoding="utf-8") == "final"
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render/deepagent-service"
uv run pytest tests/test_filesystem_jail.py -v
echo "EXIT=$?"
```

預期:三條新測試 FAIL(edit 目前直接成功,`error is None`);既有四條 PASS。

- [ ] **Step 3: 實作封鎖**

`app/agent/graph.py`:import 補 `EditResult`(`from deepagents.backends.protocol import EditResult, WriteResult`),`_OVERWRITABLE_FILE_NAME` 常數旁新增:

```python
# edit_file 對 dashboard.html 的確定性退貨訊息——錯誤訊息即行為指令,模型看到後改走
# 單次 write_file 整份重寫(single-write 實驗的核心不變量)。
DASHBOARD_EDIT_REJECTED_MESSAGE = (
    "dashboard.html must NOT be edited in place. Rewrite it in full instead: finish all "
    "run_sql data gathering first, then produce the complete corrected HTML with a single "
    "write_file call (overwriting dashboard.html is allowed)."
)
```

`DashboardOverwriteBackend` 追加方法(路徑判定與既有 `write()` 同款):

```python
    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002 -- 簽名對齊 FilesystemBackend.edit
    ) -> EditResult:
        try:
            resolved_path = self._resolve_path(file_path)
        except (OSError, RuntimeError) as error:
            return EditResult(error=f"Error editing file '{file_path}': {error}")

        dashboard_path = (self.cwd / _OVERWRITABLE_FILE_NAME).resolve()
        if resolved_path == dashboard_path:
            return EditResult(error=DASHBOARD_EDIT_REJECTED_MESSAGE)

        return super().edit(file_path, old_string, new_string, replace_all)
```

並更新 class docstring:說明這個 backend 現在有兩個特例——dashboard.html 可被 write 覆寫、不可被 edit。

- [ ] **Step 4: 跑測試確認通過**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render/deepagent-service"
uv run pytest tests/test_filesystem_jail.py -v
echo "EXIT=$?"
```

預期:全數 PASS。

- [ ] **Step 5: Commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render"
git add deepagent-service/app/agent/graph.py deepagent-service/tests/test_filesystem_jail.py
git commit -m "feat(deepagent): reject edit_file on dashboard.html, force single-write rewrite"
```

---

### Task 2: dashboard skill 修改流程改教整份重寫

**Files:**
- Modify: `deepagent-service/skills/dashboard/SKILL.md`(Workflow 第 4 節,現於 24–71 行)

**Interfaces:**
- Consumes: Task 1 的封鎖行為(skill 敘述必須與工具實際行為一致,不能再教 edit_file)。
- Produces: 無程式介面;`DashboardSkillGateMiddleware` 只驗證「讀過 SKILL.md」,與內容無關。

- [ ] **Step 1: 改寫 Workflow 第 4 節**

把 SKILL.md 第 4 節(`4. Modifying an existing dashboard.html ...` 起、到 `## Data contract` 前一行止)整段替換為:

```markdown
4. Modifying an existing dashboard.html (the user asks to adjust an already-produced
   chart/layout, or a repair round reports quality-check errors):
   - **Always rewrite the whole file with a single `write_file` call** -- `edit_file` on
     dashboard.html is rejected by the system. There is no "small edit" path: read the current
     version, apply the change mentally, and write out the complete updated HTML in one pass.
     Overwriting dashboard.html with `write_file` is allowed (it is the only overwritable
     file; `queries/*.sql`, `results/*.json`, `SOURCES.md` etc. remain create-only).
   - **Read the current version in one call first**: `read_file(file_path="dashboard.html",
     limit=1000)` to load the whole file at once. **NEVER** page-scan it with the default
     limit=100 (4-7 calls, each a full generation pass), and **NEVER** rewrite from memory
     without reading -- your memory of the file may differ from its actual content.
   - **Preserve everything the user didn't ask to change**: the rewrite must carry over all
     unchanged sections verbatim -- markup, chart configs, data references, styling. A rewrite
     that silently drops or alters unrelated charts is a defect.
   - **Self-check before writing**: in the version you are about to write, every variable and
     element id that is referenced must also be declared/present in that same version
     (especially `const xxx = window.__ERD_RESULTS__[...]` bindings and
     `getElementById('...')` targets). In a real browser `getElementById` returns `null` for
     a removed id and the immediate property access throws, killing the whole
     `DOMContentLoaded` handler and blanking every chart -- the guard's execution check
     reproduces exactly this, so a self-check up front saves a repair round.
```

保留第 4 節以外的所有內容(第 1–3 節的「初版單次 write_file」規則本來就與實驗一致,不動;`## Data contract` 之後全部不動)。被刪除的內容:small-edit/large-refactor 分流、grep 定位、`edit_file` 失敗恢復策略(`grep` 恢復策略中僅與 edit_file 相關的部分刪除;如有純 `grep` 讀檔建議可併入上文)。

- [ ] **Step 2: 驗證 skill 相關測試與 guard 範例仍過**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render/deepagent-service"
uv run pytest tests/test_dashboard_skill.py tests/test_middleware.py -v
echo "EXIT=$?"
```

預期:PASS(frontmatter/examples 檢查與內容改動無關;紅了就是改壞 frontmatter 或誤刪區塊,回頭修)。

- [ ] **Step 3: 全文一致性掃描**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render/deepagent-service"
grep -rn "edit_file" skills/
echo "EXIT=$?"
```

預期:`skills/` 下唯一殘留的 `edit_file` 字樣只出現在「`edit_file` ... is rejected」這句新敘述;其他出現處都要清掉。

- [ ] **Step 4: Commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render"
git add deepagent-service/skills/dashboard/SKILL.md
git commit -m "docs(skill): dashboard modifications always single-write full rewrite"
```

---

### Task 3: guard 修復訊息改指示整份重寫 + test_chat.py 腳本對齊

**Files:**
- Modify: `deepagent-service/app/main.py:331`(修復訊息字串)
- Modify: `deepagent-service/tests/test_chat.py`(所有對 dashboard.html 用 `edit_file` 的腳本)

**Interfaces:**
- Consumes: Task 1 的封鎖(測試腳本裡對 dashboard.html 的 `edit_file` 呼叫從此會拿到 error 結果,不再實際改檔——這正是本 task 要把腳本改成 `write_file` 的原因)。
- Produces: 修復訊息新字串(下方 Step 1 全文);測試腳本一律以 `write_file` 對 dashboard.html 做整份重寫。

- [ ] **Step 1: 改修復訊息**

`app/main.py` 修復迴圈內(現 330–333 行):

```python
                repair_message = HumanMessage(
                    "Dashboard failed quality checks. Rewrite dashboard.html in full with a "
                    "single write_file call (edit_file on dashboard.html is rejected), "
                    "fixing:\n- " + "\n- ".join(report.errors)
                )
```

- [ ] **Step 2: 跑 test_chat.py 看破哪些**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render/deepagent-service"
uv run pytest tests/test_chat.py -v
echo "EXIT=$?"
```

預期 FAIL 的測試(腳本用 `edit_file` 改 dashboard.html,如今被 Task 1 退貨、檔案不再變化):
- `test_chat_previous_dashboard_html_becomes_editing_base`(fixture `scripted_flow_previous_version`)
- `test_concurrent_edit_file_calls_both_land`(fixture 在 ~520 行先 write dashboard.html 再併發 edit)
- `test_chat_repair_round_edit_file_allowed_without_rereading_skill`
- 修復迴圈停滯/哨兵測試(~760–960 行,以 `edit_file` 當修復動作與哨兵)

以實際紅名單為準——凡是「腳本對 dashboard.html 發 `edit_file`」的測試都在本 task 範圍。

- [ ] **Step 3: 逐一改寫腳本**

改寫規則(每處同款):把

```python
{"name": "edit_file", "id": ..., "args": {"file_path": "dashboard.html", "old_string": OLD, "new_string": NEW}}
```

換成

```python
{"name": "write_file", "id": ..., "args": {"file_path": "dashboard.html", "content": FULL_HTML_WITH_NEW_APPLIED}}
```

其中 `FULL_HTML_WITH_NEW_APPLIED` = 該測試腳本先前寫入的完整 HTML 常數,把 OLD 段替換成 NEW 後的完整內容(在測試檔內定義新常數,命名照該檔既有慣例)。各測試的語意對齊:

- `test_chat_previous_dashboard_html_becomes_editing_base`:斷言 (b) 的註解「模型只是用 edit_file 局部修改」改為「模型整份重寫但保留基底標記」;腳本 write_file 的 content 必須仍含 `id="version-marker-v2"` 標記,斷言本身不變(驗證的是基底沿用,與工具無關)。
- `test_concurrent_edit_file_calls_both_land`:守的是 `SerializedToolCallsMiddleware` 下併發 `edit_file` 的 lost-update 行為,與 dashboard.html 無涉——目標檔改為 `notes.md`(腳本先 `write_file` 建立含 `<!-- SLOT_A -->`/`<!-- SLOT_B -->` 的 notes.md,再併發兩個 `edit_file`),斷言改讀 workspace 的 notes.md,測試名與 docstring 同步改。
- `test_chat_repair_round_edit_file_allowed_without_rereading_skill`:改名 `test_chat_repair_round_write_file_allowed_without_rereading_skill`;修復輪腳本改發 `write_file`(完整修正版 HTML),守的不變量照舊——gate 沿用同 thread 歷史放行,不需重讀 skill。
- 停滯/哨兵測試:修復動作與哨兵一律換成 `write_file`(哨兵 write_file 的 content 用一份可辨識的完整 HTML;斷言「哨兵未被消耗」改成檢查 dashboard.html 內容不含哨兵標記,語意不變)。

- [ ] **Step 4: 跑測試確認通過**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render/deepagent-service"
uv run pytest tests/test_chat.py -v
echo "EXIT=$?"
```

預期:全數 PASS。

- [ ] **Step 5: Commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render"
git add deepagent-service/app/main.py deepagent-service/tests/test_chat.py
git commit -m "feat(deepagent): guard repair instructs full single-write rewrite"
```

---

### Task 4: 全量 gate

**Files:**
- 無新改動;全套驗證。

- [ ] **Step 1: 全量測試 + lint**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render/deepagent-service"
uv run pytest
echo "EXIT=$?"
uv run ruff check .
echo "EXIT=$?"
```

預期:兩者 exit 0。紅了回對應 task 修,不得跳過。

- [ ] **Step 2: 手動煙霧(可選,依環境)**

worktree 起本地服務跑一輪 dashboard 產出(或留待 eval 階段與 baseline 一起跑)。實驗衡量(5 題對照、guard 觸發率統計)按 spec 屬後續階段,不在本 plan 範圍。

- [ ] **Step 3: 最終 commit(如有殘餘改動)與 ledger 記帳**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork-single-call-render"
git status
git log --oneline master..HEAD
```

確認分支上為:spec + Task 1–3 共四個 commit(外加本 plan 檔的 commit)。主 repo 的 `.superpowers/sdd/progress.md` 由主迴圈記帳,不在 worktree 內 commit。
