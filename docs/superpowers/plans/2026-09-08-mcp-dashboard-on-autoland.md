# MCP dashboard 接上 connector 自動落表 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `feat/mcp-dashboard-merge-datasource`（已含 `origin/feat/mcp-datasource` `bcb61f3` 的 merge commit）上, 把 dashboard 側的 skill gate, `check_dashboard`, SKILL.md 與 spike 依 spec 定案重新接上 datasource 的自動落表管線. 分兩段: **Phase A（本次 merge 的全部範圍）** 讓「模型能不能產出帶 `mcp()` 的 HTML+JS dashboard」可以用 spike 人工測（瀏覽器錯誤由使用者貼回對話即可）, 測試套件回綠, `check_dashboard` 以最小可用形態出貨（spec §6.1(i), 09-09 改案）; **Phase B（另開 PR, 不是本次 merge 的 gate）** 補 `check_dashboard` 依呼叫紀錄的兩條自動檢查（spec §6.1(ii)）, 做不做、何時做, 依 Checkpoint A 的人工觀察決定（spec §6.2 末段: 若主要失敗是值與權限, 先做 D9+D10 而不是 Phase B）.

**Architecture:** merge commit 已把三個衝突檔全取 datasource 側, 現況是 `check.py` import 已刪除的 `replay_manifest`, `chat_turn` 不再註冊 `check_dashboard` 也不傳 `dashboard_skill_root`（connector 模式因此 gate 在 file 模式的 dashboard skill 上）. Phase A: connector 模式改 gate 在 `mcp-data-dashboard`, `check_dashboard` 以「不依賴呼叫紀錄」的形態註冊回來（語法, 禁止 token, connector/tool 存在, CDN, theme）, `unwrap_envelope` 記下拆封路徑, wrapper 在回饋文字裡明講 raw 回傳值長什麼樣, prompt 與 SKILL.md 改到三處講法一致. Phase B: stdlib-only 的 `ConnectorCallLog`（workspace 頂層 `connector_calls.jsonl`, append-only, 跨輪）當 `check_dashboard` 的事實來源, 補 arg keys 與讀層兩條 lint.

**Tech Stack:** Python 3.11, LangChain/LangGraph（agent 層）, DuckDB, stdlib `json`/`pathlib`（engine 層）, pytest, ruff; 人工測試用 `spike/mcp-shell`（FastMCP mock server + Python bridge + `scripts/dev_chat.py`）與 OpenRouter 上的真模型.

**Status（2026-09-09）:** Phase A 已完成並經 PR #81 merge 進 `feat/mcp-dashboard`（merge commit `919be87`; head `d4d7a04`; ruff 乾淨, pytest 487 綠; opus 兩輪終審 Ready to merge）. 未做: Checkpoint A 人工測試與 `spike/out/` 快照（A6 Step 2）. Phase B 尚未開始, 另開 branch/PR, 先後順序依 Checkpoint A 觀察決定.

**Spec:** `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md`（決策 D0, D5–D8, D1–D4 已定案; D9 傳輸面, D10, D11 不在本計畫）. 相關: `docs/superpowers/specs/2026-08-30-mcp-datasource-design.md`, `docs/superpowers/plans/2026-09-06-connector-autoland-ephemeral.md`（datasource 側的行為定義, 本計畫不推翻）.

## 名詞

| 名詞 | 指的是什麼 | 在程式裡的位置 |
|---|---|---|
| **wrapper** | LangChain tool 的包裝層. 把 connector 供應層的每個 MCP tool 各包成一個 LangChain tool（名稱加 `<connector id>_` 前綴）, 每次呼叫多做: 必填檢查, 扣本輪額度, 打 MCP, 把回應**落表**成 DuckDB 表, 回給模型一段**回饋文字**（表名, 欄位, 前 20 列預覽, 以及本計畫新增的 `Raw response shape` 段）. 模型從來看不到 raw 回應, 只看得到這段回饋. | `app/agent/connectors/wrapper.py`（`build_connector_tools`, `_build_tool`, `_format_landing_feedback`） |
| **envelope（信封）** | MCP server 回傳值裡包住資料列的外層. 三種常見形狀: FastMCP 把 list 回傳值包成 `{"result": [...]}`; 業務 API 常見 `{"data": [...], "errorCode": ""}`; 兩者疊起來 `{"result": {"data": [...], "total": 9}}`. 落表前 `unwrap_envelope` 會一層層拆到列的 list; 拆掉的那些 key 就是 **unwrap path**（如 `["result"]`, `["result", "data"]`）, `data` 以外的頂層欄位（如 `errorCode`, `total`）是 **envelope fields**, 不落表但回饋文字會列出. 頂層就是 list 的回應沒有信封（path `[]`）; 不是信封的 dict（如 `{"fab": "A", "yield": 0.97}`）整包落成一列（path `None`）. 重點: **頁面的 `r.data` 拿到的是 raw（含信封）, DuckDB 表是拆封後的列**, 這一層差異正是模型寫錯 `r.data` vs `r.data.result` 的來源. | `app/engine/api_snapshot.py`（`unwrap_envelope`, `LandingResult.unwrap_path`, `envelope_fields`） |
| **handler** | dashboard HTML 裡 `mcp(connector, tool, args, handler)` 的第四個引數: 一個 JS 函式, 宿主拿到 MCP 回應後呼叫它一次, 傳入 `r`（`{data: <raw 回應>}` 或 `{error: {message}}`）. handler 負責檢查 `r.error`, 從 `r.data` 依 unwrap path 取到列（`r.data` / `r.data.result` / `r.data.data`）, 再畫圖或填表. 模型寫 handler, 瀏覽器執行 handler; `check_dashboard` 的讀層 lint 掃的就是 handler 本體對 `<param>.data` 的第一層存取. | HTML 內; 契約在 `skills/mcp-data-dashboard/SKILL.md`「Data contract -- `mcp()`」; 靜態檢查在 `app/agent/tools/check.py` |
| **landing（落表）** | wrapper 把拆封後的列寫成 JSON 檔, DuckDB `read_json_auto` 掛成本輪的表 `<connector>_<tool>_<args hash>`. 只活本輪, 輪末刪. | `app/engine/api_snapshot.py`（`land_response`） |
| **call record（呼叫紀錄）** | Phase B 新增. 每次成功的 connector 呼叫（含 0 列）在 workspace 頂層 `connector_calls.jsonl` 追加一行 metadata: connector, tool, args, unwrap path, envelope keys, 欄位名, 列數. 不含資料列. 跨輪保留, 是 `check_dashboard` 判斷「這個 (connector, tool, arg keys) 本 session 真的打過」與「handler 讀對層」的事實來源. | `app/engine/connector_call_log.py` |
| **skill gate** | middleware: 模型在 `write_file`/`edit_file` dashboard.html 之前必須先讀過指定 skill 目錄下所有 `.md`, 否則擋下工具呼叫. connector 模式要 gate 在 `mcp-data-dashboard`, file 模式 gate 在 `dashboard`. | `app/agent/middleware.py`（`DashboardSkillGateMiddleware`）, `graph.build_agent(dashboard_skill_root=)` |
| **host / bridge（宿主）** | 提供全域 `mcp()` 給 iframe 內頁面的那一側. 正式產品是前端 prelude + Java 代理 + deepagent `/tool-call`（D9, 尚未實作）; 目前唯一能跑的宿主是 spike 的 `shell.html` + `bridge.py`（Python, 直接打 mock MCP server）. | `spike/mcp-shell/` |

## 快速迭代的阻塞點（2026-09-09 盤點）

目標是「用 spike 人工測模型產出帶 `mcp()` 的 dashboard, 瀏覽器錯誤由使用者貼回對話」. 現況（merge commit `577d1ee`）deepagent **可以啟動**（`app.main` 不 import `check.py`）, 但有四件事會讓測試結果不可信或根本跑不起來, 全部落在 Phase A:

| # | 阻塞點 | 影響 | 解法 |
|---|---|---|---|
| 1 | connector 模式的 skill gate 用預設 `.skills/builtin/dashboard`（file 模式 skill） | 模型被逼著讀 file 模式 skill, 產出 `__ERD_RESULTS__` 注入式 HTML 而不是 `mcp()`; 測到的不是要測的東西 | A1（`chat_turn` 傳 `dashboard_skill_root`, 一個 kwarg） |
| 2 | `mcp-data-dashboard/SKILL.md` Workflow 第 6 步要求每次寫檔後跑 `check_dashboard`, 但 tool 沒註冊 | 模型呼叫不存在的 tool, 收到錯誤後重試或改口, 浪費輪次且污染觀察 | A1（註冊「無紀錄」形態的 `check_dashboard`: 語法/禁止 token/connector 與 tool 存在/CDN/theme, 不驗 keys 與讀層） |
| 3 | `CONNECTOR_MODE_SYSTEM_SECTION` 與 `CONNECTOR_TABLES_RESET_NOTE` 說 qN「可直接在 dashboard 引用」, 與 skill 的 `mcp()` 契約相反 | 模型被往兩個方向拉, 第二輪尤其容易改回注入式 | A2（改兩段文字） |
| 4 | 模型從沒被告知 raw 回傳值的形狀（wrapper 拆封後只給表名與預覽）, SKILL.md 又說 `r.data` 是「byte-for-byte 你看到的」 | `r.data` vs `r.data.result` 來回猶豫（spike 三張舊快照就是這個）; 這是本輪最想觀察的行為, 沒有 A3 就測不出「有沒有修好」 | A3（`unwrap_path` + `Raw response shape` 回饋段）, A4（SKILL.md） |

其他要知道但不需要程式改動的:

- **只有 spike 這一個宿主.** 前端 prelude, Java `/mcp-call`, deepagent `/tool-call` 都還沒有（D9 另開 plan）, 所以本計畫期間 dashboard 只能在 `spike/mcp-shell/shell.html` 裡看. `shell.html` 有 `window.onerror` 並把錯誤字串印在頁面 log 區, `mcp()` 回 `{error}` 時 handler 依 skill 畫錯誤卡; 使用者從這兩處複製文字貼回 `generate.sh "<訊息>"` 就是人工修復迴圈. `scripts/dev_chat.py` 會自動帶 `history` 與 `previousDashboardHtml`, `NEW=1` 重開 session.
- **需要真模型.** `run-deepagent.sh` 讀 `ONE_PROPERTIES_PATH`（預設是一條 macOS 絕對路徑, 其他機器要自己設）; README「What was actually run」列了目前模型需要的兩個 env workaround. 每輪都是真的 OpenRouter 呼叫, 一輪分鐘級.
- **測試套件目前是紅的**（`check.py` ImportError, `test_graph.py` 殘留 `ToolResultRecorder`）. 不擋人工測試, 但擋「改一行就跑 `uv run pytest` 當回歸網」與 PR gate. A1 做完即回綠（Phase A 每個 task 結尾都要求全綠）. NEVER 用 skip/xfail 讓它變綠.
- **`node` 不在 image 也不在多數開發機**: `check_dashboard` 的語法 pass 會回一條「syntax check unavailable」finding 並繼續, 不是錯誤. 本機有 node 的話會多擋一類錯誤.
- **每輪 connector 額度 50, MCP 逾時 30 s, 失敗重試 1 次**（`CONNECTOR_*` settings, datasource 側既有）: spike 的 mock server 很快, 不會碰到; 接真 server 時再調.
- **第二輪起 DuckDB 表已卸載**是 datasource 的既定行為（`CONNECTOR_TABLES_RESET_NOTE`）; 模型修 dashboard 時靠的是對話歷史裡第一輪的回饋文字（含 `Raw response shape`）, 不需要表還在. 若人工測試中發現模型在第二輪重打 connector, 先看 A2 的措辭, 不要急著加機制.
- **Phase A 期間 `check_dashboard` 不驗 keys 與讀層**, 所以「寫了沒打過的 tool」「讀錯層」只會在瀏覽器裡以空卡／錯誤卡／`TypeError` 出現——這正是人工迴圈要接住的; Phase B 把這兩類搬到寫檔當下.

## Global Constraints

- 分支: `feat/mcp-dashboard-merge-datasource`. 基底 merge commit 已落地, NEVER rebase, NEVER force-push. 每個 Task 結尾各自 commit; push 由使用者決定.
- 只動 `deepagent-service/`（含 `skills/`, `spike/`, `scripts/`）與 `docs/superpowers/`. Java 與前端零改動.
- engine 層（`app/engine/`）只用 stdlib + duckdb, 禁止 import LLM 框架（ruff TID251 會擋）.
- `duck.py`, `mcp_adapter.py`, skill staging, call budget, bearer token, `connection_lock` 共用管線, `inject_results` 對 connector 模式的呼叫: 不動.
- `mcp()` 既有頁面契約（簽名, handler 一次, `{data}`／`{error:{message}}`, 禁止 API, CDN 白名單, `'erd'` theme）: 不動. `r.error.code` 等 D9 傳輸面提案不進本計畫.
- 變數／參數／lambda 參數 NEVER 用 1–2 字元名稱（`id` 等 domain 語彙除外）; 迴圈計數器用 `index`/`rowIndex` 等描述性名稱.
- 註解: 1–2 行寫目的＋做法; NEVER 寫 spec 編號, commit hash, 事故敘事. 訊息語言: raise/log 英文; 模型面文字（tool 回饋, prompt, SKILL.md）英文; 使用者面文案中文.
- `check_dashboard` NEVER 產生模型無法用任何行動消除的 finding: 事實來源不可用時讓路（跳過該檢查並說明原因）, 不退件.
- 所有 tool 維持 never-raise: 呼叫紀錄的 append/load 失敗只 `logger.warning`, 不影響 tool 回傳.
- 完成條件: 在 `deepagent-service/` 下 `uv run ruff check .` 乾淨 ＋ `uv run pytest -q` 全綠. Phase A 從 A1 起每個 task 結尾都必須全綠（A1 之前是紅的, 見阻塞點）.
- 測試命名: `test_<subject>_<condition>_<expected>`; 斷言元素級行為, 不做整段字串快照.

## 檔案結構

| 檔案 | Phase | 動作 | 責任 |
|---|---|---|---|
| `app/agent/chat_turn.py` | A1, B4 | 修改 | A1: 傳 `dashboard_skill_root`, 註冊 `check_dashboard`; B4: 建 `ConnectorCallLog` 傳給 wrapper 與 check tool |
| `app/agent/tools/check.py` | A1, B3 | 修改 | A1: 拿掉 replay import, 紀錄類檢查關閉並說明; B3: 接 `call_log`, keys 與讀層兩條 lint, 降級模式 |
| `app/agent/prompts.py` | A2 | 修改 | 兩段 connector prompt 改講法 |
| `app/engine/api_snapshot.py` | A3 | 修改 | `unwrap_envelope` 多回傳 `unwrap_path`; `LandingResult`, `EmptyLandingError` 帶路徑 |
| `app/agent/connectors/wrapper.py` | A3, B2 | 修改 | A3: 回饋多 `Raw response shape` 段; B2: 接 `call_log`, 成功與 0 列各 append 一筆 |
| `skills/mcp-data-dashboard/SKILL.md` | A4 | 修改 | Workflow 第 1 步, 鐵律第 2 條, `r.data` 形狀, Reading the response 三種範例 |
| `spike/mcp-shell/bridge.py`, `README.md`, `mock_server.py` | A5, B5 | 修改 | 拿掉 `UNWRAP_RESULT`; 修 `DTZ011`; README 契約段指向 spec D9, 補驗收清單; B5 換快照 |
| `app/engine/connector_call_log.py` | B1 | 新增 | 呼叫紀錄的讀寫: 一行一筆 JSON, 損毀行跳過, 本輪記憶體鏡像, 降級旗標 |
| `docs/superpowers/specs/2026-09-04-mcp-dashboard-verification-options.md` | A4 | 修改 | level 2 表格的事實來源改 `connector_calls.jsonl` |
| `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md` | B5 | 修改 | 狀態列 |
| `tests/test_check_dashboard.py`, `tests/test_graph.py`, `tests/test_chat_turn_connectors.py` | A1, B3, B4 | 修改 | |
| `tests/test_prompts.py` | A2 | 修改 | |
| `tests/test_api_snapshot.py`, `tests/test_connector_wrapper.py` | A3, B2 | 修改 | |
| `tests/test_mcp_dashboard_skill_text.py` | A4 | 新增 | |
| `tests/test_connector_call_log.py` | B1 | 新增 | |

---

## Phase A — 讓模型產出可人工測, 測試套件回綠

### Task A1: skill gate 指向 `mcp-data-dashboard`, `check_dashboard` 以無紀錄形態註冊回來

**Files:**
- Modify: `deepagent-service/app/agent/tools/check.py`
- Modify: `deepagent-service/app/agent/chat_turn.py`
- Modify: `deepagent-service/tests/test_graph.py:104`
- Test: `deepagent-service/tests/test_check_dashboard.py`, `deepagent-service/tests/test_chat_turn_connectors.py`

**Interfaces:**
- Consumes: 既有 `build_agent(..., dashboard_skill_root=)`; 既有 `build_check_tools(workspace, connectors)`.
- Produces:
  - `build_check_tools(workspace, connectors) -> list[BaseTool]`（簽名不變; B3 再加 `call_log`）. 報告: 紀錄類檢查（arg keys, 讀層）一律不跑, 報告末尾一行 `call-record checks not enabled`; 無 finding 時整份為 `OK: no findings\ncall-record checks not enabled`.
  - `ChatTurn.prepare()` 在 connector 模式: `extra_tools` 含 `check_dashboard`; `build_agent` 收到 `dashboard_skill_root=".skills/builtin/mcp-data-dashboard"`; file 模式不傳（沿用預設）.
  - 常數 `chat_turn._MCP_DASHBOARD_SKILL_ROOT = ".skills/builtin/mcp-data-dashboard"`.

- [x] **Step 1: 改測試**

`tests/test_graph.py` 第 98–106 行的 `build_agent(...)` 拿掉 `ToolResultRecorder(),` 那一行:

```python
    agent = build_agent(
        model,
        connection,
        workspace,
        staged,
        dashboard_skill_root=".skills/builtin/mcp-data-dashboard",
    )
```

`tests/test_check_dashboard.py`: 刪 `from app.engine.replay_manifest import record_call, record_landing`; 刪 `_land_default_call`; 刪這三條測試（B3 以紀錄形態重寫）: `test_check_dashboard_tool_never_landed_reports_finding`, `test_check_dashboard_lookup_call_without_land_as_satisfies_lint`, `test_check_dashboard_arg_key_set_mismatch_reports_observed_key_sets`. 其餘呼叫 `_land_default_call(workspace)` 的測試把那行刪掉. `test_check_dashboard_valid_dashboard_returns_ok` 的斷言改成:

```python
    assert report.splitlines() == ["OK: no findings", "call-record checks not enabled"]
```

新增:

```python
def test_check_dashboard_mcp_call_without_any_record_source_does_not_report_never_called(
    tmp_path,
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),))

    assert "never called" not in report
    assert report.splitlines()[-1] == "call-record checks not enabled"
```

`tests/test_chat_turn_connectors.py` 新增（放在 `test_connectors_landing_dir_removed_after_aexit` 之後; `_connector_request(**overrides)` 用 `payload.update(overrides)`, 所以 `connectors=[]` 直接可用）:

```python
async def test_connectors_mode_registers_check_dashboard_tool(connector_turn_env) -> None:
    request = _connector_request()
    async with ChatTurn(request) as turn:
        await turn.prepare()
        tool_names = set(turn._agent.nodes["tools"].bound.tools_by_name)

    assert "check_dashboard" in tool_names


async def test_connectors_mode_gates_on_mcp_data_dashboard_skill(
    connector_turn_env, monkeypatch
) -> None:
    captured: dict[str, object] = {}
    original_build_agent = chat_turn.build_agent

    def _spy_build_agent(*args, **kwargs):
        captured.update(kwargs)
        return original_build_agent(*args, **kwargs)

    monkeypatch.setattr(chat_turn, "build_agent", _spy_build_agent)
    async with ChatTurn(_connector_request()) as turn:
        await turn.prepare()

    assert captured["dashboard_skill_root"] == ".skills/builtin/mcp-data-dashboard"


async def test_file_mode_uses_default_dashboard_skill_root(connector_turn_env, monkeypatch) -> None:
    captured: dict[str, object] = {}
    original_build_agent = chat_turn.build_agent

    def _spy_build_agent(*args, **kwargs):
        captured.update(kwargs)
        return original_build_agent(*args, **kwargs)

    monkeypatch.setattr(chat_turn, "build_agent", _spy_build_agent)
    async with ChatTurn(_connector_request(connectors=[])) as turn:
        await turn.prepare()

    assert "dashboard_skill_root" not in captured
    assert "check_dashboard" not in set(turn._agent.nodes["tools"].bound.tools_by_name)
```

- [x] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_check_dashboard.py tests/test_graph.py tests/test_chat_turn_connectors.py -q`
Expected: `test_check_dashboard.py` 仍 collection error（import）; `test_graph.py` PASS; 新增三條 FAIL（`check_dashboard` 不在 tools, `dashboard_skill_root` 不在 kwargs）.

- [x] **Step 3: 實作 `check.py`**

1. 刪 `from app.engine.replay_manifest import load_calls, load_landings`.
2. 加常數 `_CALL_RECORD_DISABLED_NOTE = "call-record checks not enabled"`.
3. `_render_report` 加 trailing notes:

```python
def _render_report(
    findings: list[tuple[int, str, str]], trailing_notes: Sequence[str] = ()
) -> str:
    if not findings:
        body_lines = ["OK: no findings"]
    else:
        ordered_findings = sorted(findings, key=lambda finding: finding[0])
        body_lines = [f"{len(ordered_findings)} finding(s):"]
        body_lines.extend(
            f"- [{kind}] line {line}: {message}" for line, kind, message in ordered_findings
        )
    return "\n".join([*body_lines, *trailing_notes])
```

4. `_check_dashboard` 結尾改 `return _render_report(findings, [_CALL_RECORD_DISABLED_NOTE])`.
5. 刪 `_group_landings_by_pair` 與 `_run_contract_pass` 裡 `landings_by_pair = ...` 那兩行（含 `calls.jsonl 記所有成功呼叫...` 註解）; `_run_contract_pass` 與 `_check_mcp_call` 的 `landings_by_pair` 參數整個拿掉; `_check_mcp_call` 在算出 `observed_keys` 之後直接 `return findings`（`observed_keys` 暫時只用來確認 object literal 可解析, B3 會用到）. `_run_contract_pass` 的 `workspace` 參數若因此無人使用也拿掉.
6. `check_dashboard_tool` docstring 的「arg keys matching a call actually made this session」改成「arg keys are an object literal (matching against recorded calls is reported as not enabled until call records are wired in)」.

- [x] **Step 4: 實作 `chat_turn.py`**

```python
from app.agent.tools.check import build_check_tools

_MCP_DASHBOARD_SKILL_ROOT = ".skills/builtin/mcp-data-dashboard"
```

`prepare()` 的 connector 分支:

```python
        extra_tools: list[BaseTool] | None = None
        connector_tables_reset_note: str | None = None
        build_agent_options: dict[str, Any] = {}
        connection_lock = threading.Lock()
        if connector_specs:
            ...  # load_mcp_connector, stage_connector_skills, landing dir, connection: 既有不動
            extra_tools = [
                *build_connector_tools(
                    connectors,
                    self._connection,
                    connection_lock,
                    landing_path,
                    call_budget=get_settings().CONNECTOR_CALL_BUDGET,
                ),
                *build_check_tools(self._workspace, connectors),
            ]
            build_agent_options["dashboard_skill_root"] = _MCP_DASHBOARD_SKILL_ROOT
            if session_state.has_checkpoint(request.sessionId):
                connector_tables_reset_note = CONNECTOR_TABLES_RESET_NOTE
        else:
            ...  # 既有

        self._agent = build_agent(
            build_model(),
            self._connection,
            self._workspace,
            staged_skill_paths,
            extra_tools=extra_tools,
            connection_lock=connection_lock,
            extra_system_section=(
                build_connector_mode_system_section(connectors) if connector_specs else None
            ),
            **build_agent_options,
        )
```

`from typing import Any` 若尚未 import 則補. `prepare` docstring 補一句「connector 模式另註冊 check_dashboard, 並把 skill gate 指向 mcp-data-dashboard」.

- [x] **Step 5: 跑全套確認通過**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: ruff 只剩 `spike/mcp-shell/mock_server.py:21 DTZ011`（A5 修）; pytest 全綠（`test_check_dashboard.py` 重新被收集, 減三條）.

- [x] **Step 6: Commit**

```bash
git add deepagent-service/app/agent/tools/check.py deepagent-service/app/agent/chat_turn.py deepagent-service/tests/test_check_dashboard.py deepagent-service/tests/test_graph.py deepagent-service/tests/test_chat_turn_connectors.py
git commit -m "feat(deepagent): connector 模式 gate 在 mcp-data-dashboard, check_dashboard 以不依賴呼叫紀錄的形態註冊回來"
```

---

### Task A2: prompt 措辭 — qN 只供對話, dashboard 走 `mcp()`

**Files:**
- Modify: `deepagent-service/app/agent/prompts.py:136-145, 176-183`
- Test: `deepagent-service/tests/test_prompts.py:84-126`

**Interfaces:**
- Produces: `CONNECTOR_MODE_SYSTEM_SECTION`, `CONNECTOR_TABLES_RESET_NOTE` 新文字（下列逐字）.

- [x] **Step 1: 改測試**

`tests/test_prompts.py` 第 119–126 行兩條改成:

```python
def test_connector_tables_reset_note_mentions_reload_instruction() -> None:
    assert "unloaded" in CONNECTOR_TABLES_RESET_NOTE
    assert "Call the corresponding" in CONNECTOR_TABLES_RESET_NOTE


def test_connector_tables_reset_note_says_call_records_persist_and_dashboard_uses_mcp() -> None:
    assert "call records from previous turns are still available" in CONNECTOR_TABLES_RESET_NOTE
    assert "layout-only change needs no new connector call" in CONNECTOR_TABLES_RESET_NOTE
    assert "referenced in the dashboard directly" not in CONNECTOR_TABLES_RESET_NOTE
    assert "remain valid" not in CONNECTOR_TABLES_RESET_NOTE
```

新增:

```python
def test_connector_mode_system_section_says_dashboard_fetches_live_via_mcp() -> None:
    """connector 模式 qN 只供對話回答; dashboard 檢視時經 mcp() 現抓, 不嵌資料."""
    assert "The dashboard never embeds data" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "fetches live through `mcp()` at view time" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "mcp-data-dashboard skill" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "earlier in this conversation still count" in CONNECTOR_MODE_SYSTEM_SECTION
    assert "reuse the existing qN" not in CONNECTOR_MODE_SYSTEM_SECTION
```

- [x] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_prompts.py -q`
Expected: 上述三條 FAIL.

- [x] **Step 3: 實作**

`CONNECTOR_MODE_SYSTEM_SECTION` 第 139–142 行（`Landed tables live only for the current turn, but the qN results ... a new data slice is needed. `）整段換成:

```python
    "Landed tables live only for the current turn. The dashboard never embeds data: it fetches "
    "live through `mcp()` at view time (see the mcp-data-dashboard skill), so a layout-only "
    "change needs no new connector call -- the calls you already made earlier in this "
    "conversation still count. Call a connector tool again only when you need to see "
    "a new tool or a new argument shape. The qN results produced by run_sql are for answering "
    "the user in the conversation; the dashboard does not read them. "
```

`CONNECTOR_TABLES_RESET_NOTE` 整段換成:

```python
CONNECTOR_TABLES_RESET_NOTE = (
    "\n\n(System note: the tables landed by connector tools in previous turns have been "
    "unloaded; DuckDB currently holds no connector tables. The connector call records from "
    "previous turns are still available in this conversation, so a layout-only change needs "
    "no new connector call. Call the corresponding connector tool again only if this turn "
    "needs to see a new tool or a new argument shape, or needs fresh rows to answer the user.)"
)
```

（「紀錄」在 Phase A 指的是對話歷史裡的 tool 回饋, 不對模型宣稱 `check_dashboard` 會驗; Phase B 落地時把兩句改成「`check_dashboard` validates against the calls already recorded」並更新斷言.）

- [x] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_prompts.py tests/test_chat_turn_connectors.py -q && uv run ruff check app/agent/prompts.py tests/test_prompts.py`
Expected: 全部 passed（`test_second_turn_seed_message_has_connector_tables_reset_note` 只斷言常數本身在 seed 訊息裡, 不受措辭影響）.

- [x] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/prompts.py deepagent-service/tests/test_prompts.py
git commit -m "docs(deepagent): connector prompt 改講法——qN 只供對話回答, dashboard 經 mcp() 現抓, 純改版面不重打"
```

---

### Task A3: `unwrap_envelope` 記下拆封路徑, wrapper 回饋明講 raw 形狀

**Files:**
- Modify: `deepagent-service/app/engine/api_snapshot.py`
- Modify: `deepagent-service/app/agent/connectors/wrapper.py`
- Test: `deepagent-service/tests/test_api_snapshot.py`, `deepagent-service/tests/test_connector_wrapper.py`

**Interfaces:**
- Produces:
  - `unwrap_envelope(payload) -> tuple[Any, dict[str, Any], list[str] | None]`: 第三元 `unwrap_path` — list 原樣時 `[]`; 拆過的 key 依序; 非信封 dict（整包落成一列）時 `None`.
  - `LandingResult` 新增欄位 `unwrap_path: list[str] | None`.
  - `EmptyLandingError.__init__(self, table_name, unwrap_path=None, envelope_fields=None)`, 屬性 `unwrap_path`, `envelope_fields`.
  - `wrapper.describe_raw_response_shape(response, unwrap_path, envelope_fields, row_count) -> str`; 回饋文字在 `Landed table ...` 那行之後多一段 `Raw response shape: ...`.

- [x] **Step 1: 改 `tests/test_api_snapshot.py`**

既有 7 條 `unwrap_envelope` 測試（第 29–90 行）的 `data, envelope_fields = unwrap_envelope(...)` 全改成 `data, envelope_fields, unwrap_path = unwrap_envelope(...)`, 各補一行 `unwrap_path` 斷言:

| 測試 | 期望 `unwrap_path` |
|---|---|
| `..._plain_list_passes_through...` | `[]` |
| `..._dict_with_data_list_splits_out...` | `["data"]` |
| `..._non_envelope_shape_passes_through...` | `None` |
| `..._fastmcp_result_wrapper_around_list...` | `["result"]` |
| `..._fastmcp_result_wrapper_around_data_envelope...` | `["result", "data"]` |
| `..._fastmcp_result_wrapper_around_scalar_stays_single_row` | `None` |
| `..._dict_with_result_and_other_keys_is_not_treated_as_wrapper` | `None` |

新增:

```python
def test_unwrap_envelope_path_table_matches_documented_shapes() -> None:
    """五種 raw 形狀各自回正確的 unwrap_path 與 envelope keys."""
    cases = [
        ([{"a": 1}], [], []),
        ({"data": [{"a": 1}], "errorCode": ""}, ["data"], ["errorCode"]),
        ({"result": [{"a": 1}]}, ["result"], []),
        ({"result": {"data": [{"a": 1}], "total": 9}}, ["result", "data"], ["total"]),
        ({"fab": "A", "yield": 0.97}, None, []),
    ]
    for payload, expected_path, expected_envelope_keys in cases:
        _data, envelope_fields, unwrap_path = unwrap_envelope(payload)
        assert unwrap_path == expected_path, payload
        assert list(envelope_fields) == expected_envelope_keys, payload


def test_land_response_result_carries_unwrap_path(tmp_path, connection, connection_lock) -> None:
    landing_result = land_response(
        connection, connection_lock, tmp_path, "wrapped", {"result": [{"a": 1}, {"a": 2}]}
    )

    assert landing_result.unwrap_path == ["result"]
    assert landing_result.envelope_fields == {}


def test_land_response_empty_data_error_carries_unwrap_path_and_envelope(
    tmp_path, connection, connection_lock
) -> None:
    with pytest.raises(EmptyLandingError) as error_info:
        land_response(
            connection, connection_lock, tmp_path, "empty", {"data": [], "errorCode": "E1"}
        )

    assert error_info.value.unwrap_path == ["data"]
    assert error_info.value.envelope_fields == {"errorCode": "E1"}
```

- [x] **Step 2: 改 `tests/test_connector_wrapper.py`**

補 helper 與四條回饋測試:

```python
def _single_tool_connector(connector_id: str, tool_name: str, response) -> Connector:
    return Connector(
        connector_id=connector_id,
        display_name=connector_id.title(),
        tools=(
            ConnectorTool(
                name=tool_name,
                description="fixture tool",
                input_schema={
                    "type": "object",
                    "properties": {"days": {"type": "integer"}},
                    "required": [],
                },
                call=lambda args: response,
            ),
        ),
        skills={},
    )


def test_feedback_fastmcp_result_wrapper_tells_model_to_read_r_data_result(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "list_orders", {"result": [{"a": 1}, {"a": 2}]})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({"days": 30})

    assert "Raw response shape: object with keys [result]." in result
    assert "The table was built from response.result (an array of 2 objects)" in result
    assert "read the rows with `r.data.result` -- not `r.data`" in result


def test_feedback_plain_array_says_r_data_is_already_the_array(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "list_orders", [{"a": 1}])
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({})

    assert "Raw response shape: array of 1 objects." in result
    assert "r.data is already the array" in result


def test_feedback_data_envelope_names_other_fields_location(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector(
        "sales", "list_orders", {"data": [{"a": 1}], "errorCode": ""}
    )
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_list_orders"].invoke({})

    assert "Raw response shape: object with keys [data, errorCode]." in result
    assert "read the rows with `r.data.data` -- not `r.data`" in result
    assert (
        "Other top-level fields (errorCode) were not landed; in the dashboard they are at "
        "r.data.errorCode" in result
    )


def test_feedback_non_envelope_dict_says_read_fields_directly(
    tmp_path, connection, connection_lock
) -> None:
    connector = _single_tool_connector("sales", "summary", {"fab": "A", "yield": 0.97})
    tools = _tools_by_name((connector,), connection, connection_lock, tmp_path)

    result = tools["sales_summary"].invoke({})

    assert "Raw response shape: object with keys [fab, yield]; landed as a single row." in result
    assert "read fields directly (r.data.fab)" in result
```

- [x] **Step 3: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_api_snapshot.py tests/test_connector_wrapper.py -q`
Expected: `ValueError: too many values to unpack`, `AttributeError: ... 'unwrap_path'`, 四條回饋測試 FAIL（`Raw response shape` 缺席）.

- [x] **Step 4: 實作 `api_snapshot.py`**

```python
class EmptyLandingError(Exception):
    """payload 拆封後是 0 列時拋出, 因為 DuckDB 的 read_json_auto 推不出 schema, 落表前先擋下.
    帶著拆封路徑與信封欄位, 讓呼叫端仍能描述這次成功但無資料的回應."""

    def __init__(
        self,
        table_name: str,
        unwrap_path: list[str] | None = None,
        envelope_fields: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            f"cannot land empty response as table {table_name!r}: payload has no rows, so "
            "DuckDB read_json_auto has no schema to infer -- retry with different call "
            "arguments that return at least one row before landing"
        )
        self.unwrap_path = unwrap_path
        self.envelope_fields = envelope_fields or {}


@dataclass(frozen=True)
class LandingResult:
    table_name: str
    columns: list[str]
    row_count: int
    preview_rows: list[list]
    envelope_fields: dict[str, Any]
    unwrap_path: list[str] | None


def unwrap_envelope(payload: Any) -> tuple[Any, dict[str, Any], list[str] | None]:
    """list 直接回傳; dict 有 data (list, null 或空 dict) 就回 (data, 其餘頂層欄位); FastMCP 把非 dict
    回傳值包成 {"result": ...}, 只有這一個 key 且內容是 list, dict, null 或空字串時先拆開再套同樣規則;
    其他形狀原樣落表. 第三元是走過的 key 路徑: list 為 [], 非信封 dict 為 None."""
    if isinstance(payload, list):
        return payload, {}, []
    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        if data is None or data == {} or isinstance(data, list):
            envelope_fields = {key: value for key, value in payload.items() if key != "data"}
            return data, envelope_fields, ["data"]
    if isinstance(payload, dict) and set(payload) == {"result"}:
        inner = payload["result"]
        # 拆開的 result 是 null 或空字串: 仍算「拆過 result」, 讓呼叫端判成 0 列而不是落成一列.
        if inner is None or inner == "":
            return inner, {}, ["result"]
        if isinstance(inner, list | dict):
            inner_data, inner_envelope, inner_path = unwrap_envelope(inner)
            if inner_path is None:
                return payload, {}, None
            return inner_data, inner_envelope, ["result", *inner_path]
    return payload, {}, None
```

行為與 merge 前相同的部分: `{"result": None}`, `{"result": ""}`, `{"result": {}}` 仍走 EmptyLandingError（既有測試 `test_land_response_empty_dict_shapes_raise_and_write_no_file` 守住）. **一處有意的改變**（opus 終審指出, 09-09 定案保留）: `{"result": {"fab": "A"}}`（`result` 底下是非信封 dict）merge 前落的是**內層** dict（欄位 `fab`）, 現在整包**外層**落成一列（一個 STRUCT 欄位 `result`）, `unwrap_path` 為 `None`. 理由: 這樣 DuckDB 表與 `r.data` 形狀一致, 回饋文字「read fields directly (r.data.result)」才是對的; FastMCP 只會把非 dict 回傳值包成 `{result: ...}`, 所以這個形狀只在 server 自己回 `{"result": {...}}` 時出現. 補測試 `test_unwrap_envelope_result_wrapping_non_envelope_dict_keeps_outer_object` 釘住.

`land_response`:

```python
    data, envelope_fields, unwrap_path = unwrap_envelope(payload)
    if _is_empty_payload(data):
        raise EmptyLandingError(table_name, unwrap_path, envelope_fields)
    ...
    return LandingResult(
        table_name=table_name,
        columns=columns,
        row_count=row_count,
        preview_rows=preview_rows,
        envelope_fields=envelope_fields,
        unwrap_path=unwrap_path,
    )
```

- [x] **Step 5: 實作 `wrapper.py`**

```python
def _dotted(path: list[str]) -> str:
    return ".".join(path)


def describe_raw_response_shape(
    response: Any,
    unwrap_path: list[str] | None,
    envelope_fields: dict[str, Any],
    row_count: int,
) -> str:
    """給模型看的一段英文: raw 回傳值長什麼樣, 表是從哪一層落的, 在 dashboard 的 handler 裡該讀哪個路徑."""
    if isinstance(response, list):
        return (
            f"Raw response shape: array of {row_count} objects. In the dashboard, mcp() hands "
            "your handler the raw response as r.data, so r.data is already the array; read the "
            "rows with `r.data`."
        )
    top_level_keys = ", ".join(response.keys()) if isinstance(response, dict) else "?"
    if unwrap_path is None:
        first_key = next(iter(response), "field") if isinstance(response, dict) else "field"
        return (
            f"Raw response shape: object with keys [{top_level_keys}]; landed as a single row. "
            f"In the dashboard r.data is that object; read fields directly (r.data.{first_key})."
        )
    rows_path = _dotted(unwrap_path)
    lines = [
        f"Raw response shape: object with keys [{top_level_keys}]. The table was built from "
        f"response.{rows_path} (an array of {row_count} objects)"
        + ("; nothing else was dropped." if not envelope_fields else "."),
        "In the dashboard, mcp() hands your handler the raw response as r.data, so read the "
        f"rows with `r.data.{rows_path}` -- not `r.data`.",
    ]
    if envelope_fields:
        envelope_prefix = _dotted(["r.data", *unwrap_path[:-1]])
        field_names = ", ".join(envelope_fields)
        located = ", ".join(f"{envelope_prefix}.{name}" for name in envelope_fields)
        lines.append(
            f"Other top-level fields ({field_names}) were not landed; in the dashboard they "
            f"are at {located}."
        )
    return "\n".join(lines)
```

`_format_landing_feedback(connector_id, tool_name, args, landing_result, response)` 多接 `response`, 在 `landing_summary` 之後插入:

```python
    lines = [landing_summary]
    lines.append(
        describe_raw_response_shape(
            response,
            landing_result.unwrap_path,
            landing_result.envelope_fields,
            landing_result.row_count,
        )
    )
    if landing_result.envelope_fields:
        ...  # 既有 Other response fields 段保留
```

`_execute` 結尾把 `response` 傳進去. 既有測試 `test_call_auto_lands_table_and_feedback_has_expected_shape` 斷言 `Other response fields: errorCode=` 仍成立（段落順序: Landed table → Raw response shape → Other response fields → Preview）.

- [x] **Step 6: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_api_snapshot.py tests/test_connector_wrapper.py -q && uv run ruff check . && uv run pytest -q`
Expected: 全綠; ruff 只剩 spike 的 `DTZ011`.

- [x] **Step 7: Commit**

```bash
git add deepagent-service/app/engine/api_snapshot.py deepagent-service/app/agent/connectors/wrapper.py deepagent-service/tests/test_api_snapshot.py deepagent-service/tests/test_connector_wrapper.py
git commit -m "feat(deepagent): unwrap_envelope 回傳拆封路徑, connector 回饋明講 Raw response shape 與 r.data 讀列路徑"
```

---

### Task A4: SKILL.md 與 09-04 spec 表格對齊

**Files:**
- Modify: `deepagent-service/skills/mcp-data-dashboard/SKILL.md:19-25, 84-90, 101-104, 147-152`
- Modify: `docs/superpowers/specs/2026-09-04-mcp-dashboard-verification-options.md:34-35, 48`
- Test: `deepagent-service/tests/test_mcp_dashboard_skill_text.py`（新增）; `tests/test_middleware.py`（既有 gate 測試, 只確認仍綠）

**Interfaces:**
- Consumes: A3 回饋句型（`Raw response shape`）.

- [x] **Step 1: 寫失敗的測試**

新增 `tests/test_mcp_dashboard_skill_text.py`:

```python
"""mcp-data-dashboard SKILL.md 與工具回饋文字的一致性——skill 講的讀列路徑來源與 session 定義
必須和 wrapper 回饋、check_dashboard 的紀錄一致, 否則模型會二選一."""

from pathlib import Path

_SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "mcp-data-dashboard" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def test_skill_has_no_land_as() -> None:
    assert "land_as" not in _skill_text()


def test_skill_points_r_data_path_at_raw_response_shape_feedback() -> None:
    text = _skill_text()
    assert "Raw response shape" in text
    assert "byte-for-byte" not in text
    assert "`r.data` is the raw response" in text


def test_skill_defines_this_session_as_recorded_calls_in_any_turn() -> None:
    text = _skill_text()
    assert "any turn of this conversation" in text
    assert "the tool feedback in this conversation is the record" in text


def test_skill_reading_the_response_shows_three_paths() -> None:
    text = _skill_text()
    assert "const rows = r.data;" in text
    assert "const rows = r.data.result;" in text
    assert "const rows = r.data.data;" in text
```

- [x] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_mcp_dashboard_skill_text.py -q`
Expected: 4 FAIL.

- [x] **Step 3: 改 SKILL.md**

Workflow 第 1 步（第 21–25 行）換成:

```markdown
1. Finish the analysis first with the connector tools (`<connector id>_<tool>`). Every call
   automatically lands its response as a DuckDB table and the tool feedback gives you the
   table name, the columns, a preview, and a `Raw response shape` paragraph that says what the
   raw response looks like and which path the rows were taken from. Query the table with
   `run_sql` to understand the data. Everything the dashboard needs per dataset -- the
   **connector id**, the **tool name**, the **exact arg keys**, and the **path to the rows
   inside `r.data`** -- is copied from that feedback; never reconstruct it from memory and
   never from the DuckDB table (the table is the unwrapped rows, not the raw response).
```

`r.data` 段（第 87–89 行 `success → ...`）換成:

```markdown
  - **success** → `r.error` is `null`/`undefined` and `r.data` is the raw response, exactly
    as the connector returned it. `r.data` is **not** the DuckDB table you queried during
    analysis: the landing unwrapped one or more keys to reach the rows. The `Raw response
    shape` paragraph in each connector tool's feedback tells you the path (`r.data`,
    `r.data.result`, `r.data.data`, ...). If you never saw that paragraph for a tool, you
    have not called it -- call it first.
```

鐵律第 2 條（第 101–104 行）換成:

```markdown
2. **Every call MUST mirror an actual tool call you made in any turn of this conversation**
   (same connector, same tool, same arg keys, values of the same type) -- the tool feedback in
   this conversation is the record. Never a tool you only saw in a skill file but didn't run.
   A layout-only change does not need new calls: the earlier calls are still on record. If the
   dashboard needs a dataset you haven't fetched, fetch it with the tool first, read its `Raw
   response shape`, then write the call.
```

Reading the response 第一點（第 149–152 行）換成:

```markdown
- Normalize to `rows` at the top of the handler using the path from the tool feedback's
  `Raw response shape` paragraph -- one of these three, copied exactly:
  `const rows = r.data;` (the response is already the array),
  `const rows = r.data.result;` (the server wrapped the array in `{result: [...]}`),
  `const rows = r.data.data;` (an envelope like `{data: [...], errorCode: ""}`; the other
  envelope fields are then at `r.data.errorCode`).
  NEVER guess a key and NEVER "search" for the array
  (`Object.values(r.data).find(Array.isArray)`) -- a guess reads `undefined` and every card on
  that dataset dies silently.
```

Workflow 第 6 步（第 38–42 行）的括號內容改成 `(literal connector/tool, args as an object literal, forbidden APIs, CDN whitelist, 'erd' theme; a trailing note tells you whether arg keys and the r.data path were also checked against your recorded calls)`, 讓 Phase A 的報告末行不會被模型當成錯誤.

其餘（卡片狀態, 控制項, 佈局, ECharts 規則, `mcp()` 簽名, `{data}`／`{error:{message}}`, 禁止 API, CDN, theme）不動. 全文 grep `land_as`, `byte-for-byte`, `this session` 確認沒有漏改. （Phase B 落地時鐵律第 2 條的「the tool feedback in this conversation is the record」改回 spec D7 的「as recorded in `check_dashboard`'s call record」.）

`docs/superpowers/specs/2026-09-04-mcp-dashboard-verification-options.md` 第 34–35 行的 `replay/landings.jsonl` 改成 `connector_calls.jsonl`（workspace 頂層, 跨輪）; 第 48 行 level 3 段落把 `landings.jsonl`／`land_as` 改成 `connector_calls.jsonl`／`args hash`, 不重寫.

- [x] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_mcp_dashboard_skill_text.py tests/test_middleware.py -q`
Expected: 全部 passed.

- [x] **Step 5: Commit**

```bash
git add deepagent-service/skills/mcp-data-dashboard/SKILL.md deepagent-service/tests/test_mcp_dashboard_skill_text.py docs/superpowers/specs/2026-09-04-mcp-dashboard-verification-options.md
git commit -m "docs(deepagent): mcp-data-dashboard skill 對齊自動落表——r.data 是 raw, 讀列路徑抄 Raw response shape, session 定義為紀錄所及任一輪"
```

---

### Task A5: spike 對齊 — 拿掉 `UNWRAP_RESULT`, 修 lint, README 指向 spec, 補人工測試流程

**Files:**
- Modify: `deepagent-service/spike/mcp-shell/bridge.py:130-141`
- Modify: `deepagent-service/spike/mcp-shell/README.md:16-25, 44-49`
- Modify: `deepagent-service/spike/mcp-shell/mock_server.py:21`

**Interfaces:** 無（throwaway 探針, 無自動化測試）.

- [x] **Step 1: 改 `bridge.py`**

刪掉第 134–140 行的 `UNWRAP_RESULT` 分支, 保留前面「NEVER unwrap」註解; `os` import 若只剩這裡用就一併刪. `row_count` 那行改成:

```python
    if isinstance(payload, list):
        row_count = len(payload)
    elif isinstance(payload, dict) and isinstance(payload.get("result"), list):
        row_count = len(payload["result"])
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        row_count = len(payload["data"])
    else:
        row_count = "n/a"
```

- [x] **Step 2: 改 `mock_server.py`**

第 21 行 `_ANCHOR_DATE = date.today()` 改 `_ANCHOR_DATE = datetime.now(tz=UTC).date()`, import 補 `from datetime import UTC, datetime`（`date` 若他處仍用則保留）.

- [x] **Step 3: 改 README**

- 第 16 行起的「Contract assumptions (confirm before productising)」段改成一句: `The page-facing contract this spike implements is the mcp-data-dashboard skill's; the transport-side contract (frontend prelude, Java proxy, deepagent tool-call endpoint, error codes) is drafted in docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md §7 (D9) and is not implemented here.`
- 第 20–25 行 `out/` 段: 三張舊快照的敘述改成「`out/` holds the snapshots from the latest acceptance run (see Acceptance below); earlier runs' snapshots were removed.」
- 第 44–49 行「Other knobs」: 刪 `UNWRAP_RESULT=1 (...)` 那句.
- 新增「Manual repair loop」段: 開 `http://127.0.0.1:8766`, Load `/api/dashboard`; 頁面 log 區（`window.onerror`）與各卡的錯誤訊息就是回饋來源; 把文字貼回 `AGENT_API_BEARER_TOKEN=spike-token spike/mcp-shell/generate.sh "<貼上的錯誤>"`, `dev_chat.py` 會帶上一版 dashboard 與 history; `NEW=1` 重開.
- 新增「Acceptance」段, 列三點（來自 spec §10）:
  1. 模型第一版 `dashboard.html` 的 handler 就依回饋的 `Raw response shape` 讀 `r.data.result`（mock server 的 list 型 tool）, 不再在 `r.data` 與 `r.data.result` 之間來回改.
  2. 第二輪只說「把兩張圖換位置」: 模型不重打 connector, `check_dashboard` 回 OK.
  3. 故意打一個 mock server 會拒絕的參數值: 頁面該卡顯示 server 的錯誤訊息而非空白.

- [x] **Step 4: 驗證**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: ruff 乾淨（`DTZ011` 消失）; pytest 全綠.

- [x] **Step 5: Commit**

```bash
git add deepagent-service/spike/mcp-shell/bridge.py deepagent-service/spike/mcp-shell/README.md deepagent-service/spike/mcp-shell/mock_server.py
git commit -m "chore(deepagent): spike 對齊 raw 契約——拿掉 UNWRAP_RESULT, README 補人工修復迴圈與驗收三點"
```

---

### Checkpoint A: 人工 LLM 測試（Phase B 之前）

- [x] 依 README 四個終端起 mock server, bridge, deepagent（`ONE_PROPERTIES_PATH` 指到有 OpenRouter key 的 properties）, 跑 `generate.sh`. — 2026-09-11 於 `feat/mcp-tool-call`（含 deepagent `/tool-call` 與 `results.py` 注入的 prelude）跑過: 模型 deepseek-v4-flash（OpenRouter）, 第一輪 9 分鐘（其中約 5 分鐘在寫 HTML）, 第二輪 45 秒.
- [x] 觀察 Acceptance 第 1 點（第一版就讀 `r.data.result`）與第 2 點（純改版面不重打）. 第 3 點在瀏覽器裡看錯誤卡. — 第 1 點 PASS: 三個 handler 第一版全部 `r.data.result`, 無來回猶豫; 第 2 點 PASS: 「交換兩張圖位置」那輪 0 次 connector 呼叫, `check_dashboard` OK, diff 只有 section 搬位; 第 3 點以 headless Chromium 經 bridge 開頁驗: 4 張 ECharts canvas 有畫, KPI 由 connector 即時算出, 換區域下拉觸發恰好一次 `list_orders` 且 KPI 更新; 錯誤卡（TOOL_ERROR／CONNECTOR_UNAVAILABLE／RETRYABLE 重試鍵）另以手寫頁面驗過, 本輪模型產出的頁面沒有故意打錯的參數.
- [x] 記錄: 模型產出的 handler 讀了哪一層, 錯誤貼回去後幾輪修好, 有沒有呼叫不存在的 tool 或寫沒打過的 tool（這兩類是 Phase B 要自動擋的, 在這裡先用人眼計數）. — 讀層: `r.data.result` ×3; 修復輪數: 0（第一版無瀏覽器錯誤）; 不存在的 tool: 0; 沒打過的 tool: 0（對話期打了 `list_regions`／`list_orders`／`defect_summary` 各一次, dashboard 剛好用這三個）; 禁止 token: 0.
- [x] 若模型仍讀錯層或第二輪重打 connector: 先改 A2 措辭或 A3 回饋句型再測, 不要跳去 Phase B. Phase B 的 lint 只能擋, 不能教. — 未觸發（兩者都沒發生）.
- [ ] 依觀察到的主要失敗形態決定下一個 PR: keys 與讀層 → Phase B; 值不對、connector 不允許、逾時 → D9 傳輸面 + D10（另開 spec/plan）. 把結論寫進 spec §13. — 一次跑（一組 prompt）沒觀察到任何失敗形態, 樣本不足以定案; D9 的 deepagent 側（hop ①④）已先於此決定落地（`feat/mcp-tool-call`）. 觀察已記入 spec §13, 下一個 PR 由使用者決定.

---

### Task A6: 收尾 — 本次 merge 交付

**Files:**
- Modify: `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md:3`（狀態列）
- Modify: 本計畫（勾選 Phase A 的 checkbox）

- [x] **Step 1: 全套驗證**（2026-09-09: ruff 乾淨, 484 passed）

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: ruff 乾淨; 全綠. 參考基準: merge commit 當下 436 passed（排除 `test_check_dashboard.py`）; Phase A 恢復該檔（減三條）並新增約 15 條, 總數應落在 465 附近, 明顯少於這個量級代表有測試檔沒被收集.

Run（不受影響但仍跑, 專案規則）: `cd backend && ./mvnw -q test`（若本機無 Java 環境, 由 CI 跑）.

- [x] **Step 2: spike 快照**（2026-09-11: `out/dashboard.html` 換成真模型第二輪產出（含 `erd-mcp-runtime` 區塊）, 三張舊快照刪除）

Checkpoint A 那一輪產出的 `dashboard.html` 放進 `out/`, 刪舊三張（README 的 `out/` 段已在 A5 改成指向最新一次驗收）.

- [x] **Step 3: spec 狀態列**

第 3 行改為 `**merge 已於 2026-09-08 執行於 branch feat/mcp-dashboard-merge-datasource（基準 datasource bcb61f3）; D0, D5–D8 與 D1–D4 (i) 已依 plan 2026-09-08-mcp-dashboard-on-autoland.md Phase A 落地; D1–D4 (ii) 為 plan Phase B, 另開 PR.**`; 第 13 節末段同步.

- [x] **Step 4: Commit 與交付**（2026-09-09: opus 兩輪終審 Ready to merge → PR #81（含 reviewer 指南與驗證附錄）→ 使用者 merge, `919be87`; spike 快照因 Step 2 未跑而未更新）

```bash
git add deepagent-service/spike/mcp-shell/out docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md docs/superpowers/plans/2026-09-08-mcp-dashboard-on-autoland.md
git commit -m "docs(spec): mcp-dashboard-on-autoland Phase A 已落地, spike 驗收快照更新"
```

依 CLAUDE.md 多人協作規則: opus 全 branch 終審（範圍 `origin/feat/mcp-dashboard...HEAD`, 含 merge commit 之後每一個 commit）→ 終審結論寫進 PR 描述 → PR 描述附 spec 連結、第 12 節拍板結果與 09-09 改案 → 使用者觸發 merge. push 與開 PR 都等使用者指示.

---

## Phase B — `check_dashboard` 依呼叫紀錄的兩條自動檢查（另開 PR）

> 本段是 spec §6.1(ii) 的實作步驟, **不在本次 merge 範圍**. 開始前: 在 Phase A 已 merge 的主線上開新 branch, 重跑本段每個 task 的 Step 1 確認測試仍如預期失敗（Phase A 之後 `check.py` 與 `wrapper.py` 的行號會變, 以函式名為準）. 若 Checkpoint A 的結論是先做 D9+D10, 本段原樣保留, 等那個 PR 之後再回來.

### Task B1: `ConnectorCallLog` — 呼叫紀錄的讀寫物件

**Files:**
- Create: `deepagent-service/app/engine/connector_call_log.py`
- Test: `deepagent-service/tests/test_connector_call_log.py`

**Interfaces:**
- Produces:
  - `class ConnectorCallLog`: `__init__(self, path: Path)`; `append(self, record: dict[str, Any]) -> None`; `load(self) -> list[dict[str, Any]]`; property `degraded: bool`; property `path: Path`.
  - 常數 `CONNECTOR_CALL_LOG_FILENAME = "connector_calls.jsonl"`.
  - 紀錄形狀（dict, 由 B2 的 wrapper 產生, B3 的 check 讀取）:
    `{"connector_id": str, "tool_name": str, "args": dict, "unwrap_path": list[str] | None, "envelope_keys": list[str], "columns": list[str], "row_count": int, "landed": bool}`

- [ ] **Step 1: 寫失敗的測試**

```python
# deepagent-service/tests/test_connector_call_log.py
"""`ConnectorCallLog`(app.engine.connector_call_log)——一行一筆 JSON 的 append/load 往返、
損毀行跳過、檔案不存在回空、本輪記憶體鏡像(磁碟寫失敗仍讀得到)、降級旗標。"""

import json
import logging
from pathlib import Path

import pytest

from app.engine.connector_call_log import CONNECTOR_CALL_LOG_FILENAME, ConnectorCallLog


def _record(**overrides) -> dict:
    record = {
        "connector_id": "sales",
        "tool_name": "list_orders",
        "args": {"days": 30},
        "unwrap_path": ["result"],
        "envelope_keys": [],
        "columns": ["order_id", "amount"],
        "row_count": 412,
        "landed": True,
    }
    record.update(overrides)
    return record


def test_filename_constant_is_connector_calls_jsonl() -> None:
    assert CONNECTOR_CALL_LOG_FILENAME == "connector_calls.jsonl"


def test_load_missing_file_returns_empty_list_and_not_degraded(tmp_path) -> None:
    call_log = ConnectorCallLog(tmp_path / CONNECTOR_CALL_LOG_FILENAME)

    assert call_log.load() == []
    assert call_log.degraded is False


def test_append_then_load_round_trips_one_record_per_line(tmp_path) -> None:
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    call_log = ConnectorCallLog(path)

    call_log.append(_record())
    call_log.append(_record(args={"days": 7}, row_count=9))

    assert call_log.load() == [_record(), _record(args={"days": 7}, row_count=9)]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["args"] == {"days": 30}


def test_load_reads_records_written_by_a_previous_instance(tmp_path) -> None:
    """跨輪: 上一輪的物件寫的檔, 本輪新物件讀得到."""
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    ConnectorCallLog(path).append(_record())

    assert ConnectorCallLog(path).load() == [_record()]


def test_load_skips_corrupt_lines_and_keeps_the_rest(tmp_path, caplog) -> None:
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    path.write_text(
        json.dumps(_record()) + "\n" + "{not json\n" + "\n" + json.dumps(_record(row_count=1)) + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        records = ConnectorCallLog(path).load()

    assert records == [_record(), _record(row_count=1)]
    assert "corrupt" in caplog.text


def test_append_write_failure_keeps_record_in_memory_and_sets_degraded(
    tmp_path, monkeypatch, caplog
) -> None:
    """磁碟寫失敗: 不拋, 本輪記憶體鏡像仍讓 load() 看得到這筆, degraded 轉 True."""
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    call_log = ConnectorCallLog(path)

    def _fail_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", _fail_open)
    with caplog.at_level(logging.WARNING):
        call_log.append(_record())

    assert call_log.degraded is True
    assert "append failed" in caplog.text
    monkeypatch.undo()
    assert call_log.load() == [_record()]
    assert not path.exists()


def test_load_read_failure_returns_memory_records_and_sets_degraded(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / CONNECTOR_CALL_LOG_FILENAME
    call_log = ConnectorCallLog(path)
    call_log.append(_record())
    original_read_text = Path.read_text

    def _fail_read(self, *args, **kwargs):
        if self == path:
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _fail_read)

    assert call_log.load() == []
    assert call_log.degraded is True


def test_append_creates_parent_directory(tmp_path) -> None:
    path = tmp_path / "nested" / CONNECTOR_CALL_LOG_FILENAME
    ConnectorCallLog(path).append(_record())

    assert path.exists()


@pytest.mark.parametrize("bad_record", [None, [], "text"])
def test_append_non_dict_record_raises_type_error(tmp_path, bad_record) -> None:
    with pytest.raises(TypeError):
        ConnectorCallLog(tmp_path / CONNECTOR_CALL_LOG_FILENAME).append(bad_record)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_connector_call_log.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.engine.connector_call_log'`

- [ ] **Step 3: 實作**

```python
# deepagent-service/app/engine/connector_call_log.py
"""connector 呼叫紀錄: 一行一筆 JSON, append-only, 放在 workspace 頂層隨 session 跨輪保留.
只記 metadata (connector, tool, 參數, 拆封路徑, 欄位名, 列數), 不記資料列. engine 層只用 stdlib."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONNECTOR_CALL_LOG_FILENAME = "connector_calls.jsonl"


class ConnectorCallLog:
    """一個 ChatTurn 一個實例. 磁碟寫失敗的紀錄留在記憶體讓同一輪的 load() 仍看得到,
    任何一次 append/load 例外都把 degraded 設成 True, 讓讀端決定要不要跳過依賴紀錄的檢查."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._unwritten_records: list[dict[str, Any]] = []
        self._degraded = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def degraded(self) -> bool:
        return self._degraded

    def append(self, record: dict[str, Any]) -> None:
        """寫一行. 失敗只 warning 並把這筆留在記憶體, 不往外拋."""
        if not isinstance(record, dict):
            raise TypeError(f"call log record must be a dict, got {type(record).__name__}")
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as error:  # noqa: BLE001 -- never-raise: recording must not break the tool
            self._degraded = True
            self._unwritten_records.append(record)
            logger.warning(
                "connector call log append failed: path=%s error=%s",
                self._path,
                type(error).__name__,
            )

    def load(self) -> list[dict[str, Any]]:
        """磁碟上的全部紀錄 (跨輪) 加上本輪寫失敗的紀錄. 損毀行跳過並 warning."""
        disk_records: list[dict[str, Any]] = []
        if self._path.exists():
            try:
                raw_text = self._path.read_text(encoding="utf-8")
            except Exception as error:  # noqa: BLE001 -- never-raise, degrade instead
                self._degraded = True
                logger.warning(
                    "connector call log read failed: path=%s error=%s",
                    self._path,
                    type(error).__name__,
                )
                raw_text = ""
            for line_number, line in enumerate(raw_text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "connector call log corrupt line skipped: path=%s line=%d",
                        self._path,
                        line_number,
                    )
                    continue
                if isinstance(parsed, dict):
                    disk_records.append(parsed)
        return [*disk_records, *self._unwritten_records]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_connector_call_log.py -q && uv run ruff check app/engine/connector_call_log.py tests/test_connector_call_log.py`
Expected: 10 passed; ruff 乾淨.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/engine/connector_call_log.py deepagent-service/tests/test_connector_call_log.py
git commit -m "feat(deepagent): ConnectorCallLog——connector 呼叫紀錄 append/load, 損毀行跳過, 本輪記憶體鏡像與降級旗標"
```

---

### Task B2: wrapper 寫呼叫紀錄

**Files:**
- Modify: `deepagent-service/app/agent/connectors/wrapper.py`
- Test: `deepagent-service/tests/test_connector_wrapper.py`

**Interfaces:**
- Consumes: B1 `ConnectorCallLog.append`; A3 `LandingResult.unwrap_path`, `EmptyLandingError.unwrap_path/envelope_fields`.
- Produces: `build_connector_tools(connectors, connection, connection_lock, landing_dir, *, call_budget=50, call_log: ConnectorCallLog | None = None)`; 成功與 0 列各 append 一筆（形狀見 B1）; `ConnectorToolError`, 傳輸失敗, 額度用盡不記.

- [ ] **Step 1: 寫失敗的測試**

`tests/test_connector_wrapper.py` 補 `from app.engine.connector_call_log import ConnectorCallLog` 與:

```python
def test_successful_landing_appends_call_record_with_unwrap_path(
    tmp_path, connection, connection_lock
) -> None:
    call_log = ConnectorCallLog(tmp_path / "connector_calls.jsonl")
    connector = _single_tool_connector("sales", "list_orders", {"result": [{"a": 1}, {"a": 2}]})
    tools = _tools_by_name(
        (connector,), connection, connection_lock, tmp_path, call_log=call_log
    )

    tools["sales_list_orders"].invoke({"days": 30})

    assert call_log.load() == [
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"days": 30},
            "unwrap_path": ["result"],
            "envelope_keys": [],
            "columns": ["a"],
            "row_count": 2,
            "landed": True,
        }
    ]


def test_empty_response_appends_call_record_with_landed_false(
    tmp_path, connection, connection_lock
) -> None:
    call_log = ConnectorCallLog(tmp_path / "connector_calls.jsonl")
    connector = _single_tool_connector("sales", "list_orders", {"data": [], "errorCode": "E1"})
    tools = _tools_by_name(
        (connector,), connection, connection_lock, tmp_path, call_log=call_log
    )

    result = tools["sales_list_orders"].invoke({"days": 30})

    assert "cannot land empty response" in result
    [record] = call_log.load()
    assert record["landed"] is False
    assert record["columns"] == []
    assert record["row_count"] == 0
    assert record["unwrap_path"] == ["data"]
    assert record["envelope_keys"] == ["errorCode"]


def test_connector_tool_error_does_not_append_call_record(
    tmp_path, connection, connection_lock
) -> None:
    from app.agent.connectors.model import ConnectorToolError

    def _raise(args):
        raise ConnectorToolError("tool rejected the arguments")

    connector = Connector(
        connector_id="sales",
        display_name="Sales",
        tools=(
            ConnectorTool(
                name="list_orders",
                description="fixture",
                input_schema={"type": "object", "properties": {}, "required": []},
                call=_raise,
            ),
        ),
        skills={},
    )
    call_log = ConnectorCallLog(tmp_path / "connector_calls.jsonl")
    tools = _tools_by_name(
        (connector,), connection, connection_lock, tmp_path, call_log=call_log
    )

    tools["sales_list_orders"].invoke({})

    assert call_log.load() == []


def test_call_log_none_writes_nothing_and_still_lands(tmp_path, connection, connection_lock) -> None:
    tools = _tools_by_name((demo_connector(),), connection, connection_lock, tmp_path)

    result = tools["demo_quality_list_fabs"].invoke({})

    assert result.startswith("Landed table")
    assert not (tmp_path / "connector_calls.jsonl").exists()


def test_call_log_append_failure_does_not_change_tool_result(
    tmp_path, connection, connection_lock, monkeypatch
) -> None:
    call_log = ConnectorCallLog(tmp_path / "connector_calls.jsonl")

    def _boom(record):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(call_log, "append", _boom)
    tools = _tools_by_name(
        (demo_connector(),), connection, connection_lock, tmp_path, call_log=call_log
    )

    result = tools["demo_quality_list_fabs"].invoke({})

    assert result.startswith("Landed table")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_connector_wrapper.py -q`
Expected: 新測試 FAIL（`TypeError: ... unexpected keyword argument 'call_log'`）.

- [ ] **Step 3: 實作**

`wrapper.py` 補 `from app.engine.connector_call_log import ConnectorCallLog`. `_build_tool` 多接 `call_log: ConnectorCallLog | None`, 內部:

```python
    def _record_call(
        args: dict[str, Any],
        unwrap_path: list[str] | None,
        envelope_fields: dict[str, Any],
        columns: list[str],
        row_count: int,
        landed: bool,
    ) -> None:
        if call_log is None:
            return
        try:
            call_log.append(
                {
                    "connector_id": connector.connector_id,
                    "tool_name": connector_tool.name,
                    "args": args,
                    "unwrap_path": unwrap_path,
                    "envelope_keys": list(envelope_fields),
                    "columns": columns,
                    "row_count": row_count,
                    "landed": landed,
                }
            )
        except Exception as error:  # noqa: BLE001 -- recording is best-effort, never masks the call
            logger.warning(
                "connector call record failed: connector=%s tool=%s error=%s",
                connector.connector_id,
                connector_tool.name,
                type(error).__name__,
            )
```

`_execute`:

```python
        try:
            landing_result = land_response(
                connection, connection_lock, landing_dir, table_name, response
            )
        except EmptyLandingError as error:
            _record_call(args, error.unwrap_path, error.envelope_fields, [], 0, landed=False)
            return str(error)
        except ValueError as error:
            # table_name 沒通過驗證, 訊息本身已可行動, 原樣回傳.
            return str(error)
        except Exception as error:
            ...  # 既有 landing failed 分支不動

        _record_call(
            args,
            landing_result.unwrap_path,
            landing_result.envelope_fields,
            landing_result.columns,
            landing_result.row_count,
            landed=True,
        )
        return _format_landing_feedback(
            connector.connector_id, connector_tool.name, args, landing_result, response
        )
```

`build_connector_tools` 簽名加 `call_log: ConnectorCallLog | None = None`, 傳進 `_build_tool`. `_build_tool` docstring 補「成功與 0 列各記一筆呼叫紀錄; tool 錯誤與傳輸失敗不記」.

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_connector_wrapper.py -q && uv run ruff check app/agent/connectors/wrapper.py tests/test_connector_wrapper.py`
Expected: 全部 passed; ruff 乾淨.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/connectors/wrapper.py deepagent-service/tests/test_connector_wrapper.py
git commit -m "feat(deepagent): connector wrapper 寫呼叫紀錄——成功與 0 列各一筆, tool 錯誤不記, 寫失敗不影響回傳"
```

---

### Task B3: `check_dashboard` 讀紀錄 — arg keys 與讀層兩條 lint, 降級模式

**Files:**
- Modify: `deepagent-service/app/agent/tools/check.py`
- Test: `deepagent-service/tests/test_check_dashboard.py`

**Interfaces:**
- Consumes: B1 `ConnectorCallLog.load()`, `.degraded`; 紀錄形狀.
- Produces: `build_check_tools(workspace, connectors, call_log: ConnectorCallLog | None = None) -> list[BaseTool]`.
- 報告文字:
  - `call_log is None`: 與 A1 相同（末尾 `call-record checks not enabled`）.
  - `call_log.degraded`: 報告開頭一行 `call record unavailable; arg-key and response-layer checks skipped`, 兩條紀錄檢查不跑.
  - 未呼叫 finding: `tool was never called in this session — call it first`.
  - keys 不合 finding: `args keys {...} do not match any recorded call — observed key sets: ...`.
  - 讀層 finding（`unwrap_path` 非空, handler 第一層不是路徑首 key 也不是 envelope key）: `rows are at <p>.data.<path> (the analysis-time landing unwrapped that key); handler reads <p>.data.<observed> instead`（裸用或直接 `.map(` 等陣列方法時 `<observed>` 寫成 `<p>.data directly`）.
  - 讀層 finding（`unwrap_path == []`, handler 讀 `<p>.data.result` 或 `<p>.data.data`）: `<p>.data is already the array — read it directly, not <p>.data.<key>`.
  - `unwrap_path is None`（非信封 dict）: 不做讀層 lint.

- [ ] **Step 1: 改測試**

`tests/test_check_dashboard.py` 補 import 與 helper:

```python
from app.engine.connector_call_log import CONNECTOR_CALL_LOG_FILENAME, ConnectorCallLog


def _call_log(workspace) -> ConnectorCallLog:
    return ConnectorCallLog(workspace.root / CONNECTOR_CALL_LOG_FILENAME)


def _record_default_call(workspace, unwrap_path=None) -> ConnectorCallLog:
    call_log = _call_log(workspace)
    call_log.append(
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"status": "open"},
            "unwrap_path": [] if unwrap_path is None else unwrap_path,
            "envelope_keys": [],
            "columns": ["status"],
            "row_count": 3,
            "landed": True,
        }
    )
    return call_log


def _check_report(workspace, connectors=(), call_log=None) -> str:
    tools = {
        tool.name: tool for tool in build_check_tools(workspace, connectors, call_log=call_log)
    }
    return tools["check_dashboard"].invoke({})
```

新增測試（A1 保留的 `call-record checks not enabled` 測試不動）:

```python
def test_check_dashboard_tool_never_called_reports_finding(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=_call_log(workspace))

    assert "tool was never called in this session — call it first" in report


def test_check_dashboard_arg_key_set_mismatch_reports_observed_key_sets(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace)
    script_body = (
        "mcp('sales', 'list_orders', { region: 'north' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "args keys {region} do not match any recorded call" in report
    assert "observed key sets: {status}" in report


def test_check_dashboard_recorded_call_with_matching_keys_passes(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace)
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "never called" not in report
    assert "do not match" not in report
    assert "call-record checks not enabled" not in report


def test_check_dashboard_zero_row_record_still_counts_as_called(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _call_log(workspace)
    call_log.append(
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"status": "open"},
            "unwrap_path": ["data"],
            "envelope_keys": [],
            "columns": [],
            "row_count": 0,
            "landed": False,
        }
    )
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "const rows = r.data.data; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "never called" not in report


def test_check_dashboard_record_from_previous_turn_satisfies_lint(tmp_path) -> None:
    """跨輪: 上一輪物件寫的紀錄, 本輪新物件讀到即可通過."""
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    _record_default_call(workspace)
    this_turn_call_log = _call_log(workspace)
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=this_turn_call_log)

    assert "never called" not in report


def test_check_dashboard_result_path_but_handler_reads_r_data_directly_reports_finding(
    tmp_path,
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace, unwrap_path=["result"])
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "const rows = r.data.map(x => x); });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at r.data.result" in report


def test_check_dashboard_result_path_and_handler_reads_r_data_result_passes(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace, unwrap_path=["result"])
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "const rows = r.data.result; console.log(rows.length); });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at" not in report
    assert "already the array" not in report


def test_check_dashboard_empty_path_but_handler_reads_r_data_result_reports_finding(
    tmp_path,
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace, unwrap_path=[])
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "const rows = r.data.result; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "r.data is already the array" in report


def test_check_dashboard_handler_parameter_named_other_than_r_is_scanned(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _record_default_call(workspace, unwrap_path=["result"])
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, (response) => { "
        "if (response.error) return; const rows = response.data; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at response.data.result" in report


def test_check_dashboard_envelope_key_access_at_first_level_is_allowed(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _call_log(workspace)
    call_log.append(
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"status": "open"},
            "unwrap_path": ["data"],
            "envelope_keys": ["errorCode"],
            "columns": ["status"],
            "row_count": 3,
            "landed": True,
        }
    )
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "if (r.data.errorCode) return; const rows = r.data.data; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at" not in report


def test_check_dashboard_null_path_record_skips_layer_lint(tmp_path) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _call_log(workspace)
    call_log.append(
        {
            "connector_id": "sales",
            "tool_name": "list_orders",
            "args": {"status": "open"},
            "unwrap_path": None,
            "envelope_keys": [],
            "columns": ["fab", "yield"],
            "row_count": 1,
            "landed": True,
        }
    )
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; "
        "console.log(r.data.fab); });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert "rows are at" not in report
    assert "already the array" not in report


def test_check_dashboard_degraded_call_log_skips_record_checks_with_leading_note(
    tmp_path, monkeypatch
) -> None:
    workspace = prepare_local_layout(tmp_path, "user-1", "sess-1")
    call_log = _call_log(workspace)
    monkeypatch.setattr(type(call_log), "degraded", property(lambda self: True))
    script_body = (
        "mcp('sales', 'list_orders', { status: 'open' }, r => { if (r.error) return; });\n"
    )
    workspace.dashboard_path.write_text(_build_dashboard_html(script_body), encoding="utf-8")

    report = _check_report(workspace, (_sales_connector(),), call_log=call_log)

    assert report.splitlines()[0] == (
        "call record unavailable; arg-key and response-layer checks skipped"
    )
    assert "never called" not in report
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_check_dashboard.py -q`
Expected: 新增測試 FAIL（`TypeError: ... 'call_log'`）; A1 的測試仍 PASS.

- [ ] **Step 3: 實作**

`check.py`:

1. `from app.engine.connector_call_log import ConnectorCallLog`.
2. 常數:

```python
_CALL_RECORD_UNAVAILABLE_NOTE = (
    "call record unavailable; arg-key and response-layer checks skipped"
)
_ARROW_PARAMETER_PATTERN = re.compile(
    r"^\(?\s*([A-Za-z_$][\w$]*)\s*\)?\s*=>|^(?:async\s+)?function\s*\w*\s*\(\s*([A-Za-z_$][\w$]*)"
)
_ARRAY_METHOD_NAMES = frozenset(
    {"map", "forEach", "filter", "length", "slice", "reduce", "find", "some", "every", "sort", "flatMap"}
)
```

3. 紀錄分組:

```python
@dataclass(frozen=True)
class _CallRecordGroup:
    arg_key_sets: list[frozenset[str]]
    unwrap_path: list[str] | None
    envelope_keys: list[str]
    layer_known: bool


def _group_call_records(records: list[dict]) -> dict[tuple[str, str], _CallRecordGroup]:
    """同一 (connector, tool) 的紀錄收成一組: keys 集合全部保留, 拆封路徑取最後一筆 (多筆不一致時 warning)."""
    grouped: dict[tuple[str, str], _CallRecordGroup] = {}
    for record in records:
        pair_key = (record.get("connector_id"), record.get("tool_name"))
        record_args = record.get("args") or {}
        previous = grouped.get(pair_key)
        arg_key_sets = [*(previous.arg_key_sets if previous else []), frozenset(record_args)]
        unwrap_path = record.get("unwrap_path")
        if previous is not None and previous.layer_known and previous.unwrap_path != unwrap_path:
            logger.warning(
                "connector call records disagree on unwrap_path: connector=%s tool=%s",
                pair_key[0],
                pair_key[1],
            )
        grouped[pair_key] = _CallRecordGroup(
            arg_key_sets=arg_key_sets,
            unwrap_path=unwrap_path,
            envelope_keys=list(record.get("envelope_keys") or []),
            layer_known="unwrap_path" in record,
        )
    return grouped
```

4. `build_check_tools(workspace, connectors, call_log: ConnectorCallLog | None = None)`; `_check_dashboard(workspace, connectors, call_log)`:

```python
    record_groups: dict[tuple[str, str], _CallRecordGroup] | None = None
    leading_notes: list[str] = []
    trailing_notes: list[str] = []
    if call_log is None:
        trailing_notes.append(_CALL_RECORD_DISABLED_NOTE)
    else:
        records = call_log.load()
        if call_log.degraded:
            leading_notes.append(_CALL_RECORD_UNAVAILABLE_NOTE)
        else:
            record_groups = _group_call_records(records)

    findings: list[tuple[int, str, str]] = []
    findings.extend(_run_syntax_pass(script_blocks))
    findings.extend(_run_contract_pass(html_text, script_blocks, connectors, record_groups))
    return _render_report(findings, leading_notes, trailing_notes)
```

`_render_report(findings, leading_notes: Sequence[str] = (), trailing_notes: Sequence[str] = ())` 回 `"\n".join([*leading_notes, *body_lines, *trailing_notes])`. `record_groups is None` 代表兩條紀錄檢查關閉; `_run_contract_pass` 與 `_check_mcp_call` 接 `record_groups`. `_check_mcp_call` 在 `observed_keys` 之後:

```python
    if record_groups is None:
        return findings
    group = record_groups.get((connector_id, tool_name))
    if group is None:
        findings.append(
            (call_line, "contract", "tool was never called in this session — call it first")
        )
        return findings
    if frozenset(observed_keys) not in group.arg_key_sets:
        observed_sets_text = "; ".join(
            "{" + ", ".join(sorted(key_set)) + "}" for key_set in dict.fromkeys(group.arg_key_sets)
        )
        call_keys_text = ", ".join(sorted(observed_keys)) or "(none)"
        findings.append(
            (
                call_line,
                "contract",
                f"args keys {{{call_keys_text}}} do not match any recorded call — observed key "
                f"sets: {observed_sets_text}",
            )
        )
    if len(arguments) >= 4 and group.layer_known and group.unwrap_path is not None:
        findings.extend(_check_response_layer(call_line, arguments[3].strip(), group))
    return findings
```

5. 讀層 lint:

```python
def _handler_parameter_name(handler_text: str) -> str | None:
    match = _ARROW_PARAMETER_PATTERN.match(handler_text)
    if match is None:
        return None
    return match.group(1) or match.group(2)


def _first_level_accesses(handler_text: str, parameter_name: str) -> list[str | None]:
    """handler 本體裡每一次 `<param>.data` 存取的第一層 key; 沒有接 `.key` (直接 .map/[ /裸用) 記 None."""
    pattern = re.compile(rf"\b{re.escape(parameter_name)}\.data\b(?:\.([A-Za-z_$][\w$]*))?")
    return [match.group(1) for match in pattern.finditer(handler_text)]


def _check_response_layer(
    call_line: int, handler_text: str, group: _CallRecordGroup
) -> list[tuple[int, str, str]]:
    parameter_name = _handler_parameter_name(handler_text)
    if parameter_name is None:
        return []
    unwrap_path = group.unwrap_path or []
    findings: list[tuple[int, str, str]] = []
    for first_key in _first_level_accesses(handler_text, parameter_name):
        if unwrap_path:
            expected_key = unwrap_path[0]
            allowed_keys = {expected_key, *(group.envelope_keys if len(unwrap_path) == 1 else [])}
            if first_key in allowed_keys:
                continue
            observed_text = (
                f"{parameter_name}.data directly"
                if first_key is None or first_key in _ARRAY_METHOD_NAMES
                else f"{parameter_name}.data.{first_key}"
            )
            findings.append(
                (
                    call_line,
                    "contract",
                    f"rows are at {parameter_name}.data.{'.'.join(unwrap_path)} (the "
                    f"analysis-time landing unwrapped that key); handler reads {observed_text} "
                    "instead",
                )
            )
            break
        if first_key in ("result", "data"):
            findings.append(
                (
                    call_line,
                    "contract",
                    f"{parameter_name}.data is already the array — read it directly, not "
                    f"{parameter_name}.data.{first_key}",
                )
            )
            break
    return findings
```

6. `check_dashboard_tool` docstring 改成「arg keys and response-layer access matching a connector call recorded in this session (any turn)」.

- [ ] **Step 4: 跑測試確認通過**

Run: `cd deepagent-service && uv run pytest tests/test_check_dashboard.py -q && uv run ruff check app/agent/tools/check.py tests/test_check_dashboard.py`
Expected: 全部 passed; ruff 乾淨.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/tools/check.py deepagent-service/tests/test_check_dashboard.py
git commit -m "feat(deepagent): check_dashboard 依呼叫紀錄驗 arg keys 與 r.data 讀層, 紀錄不可用時跳過並說明"
```

---

### Task B4: `chat_turn` 建紀錄物件, 傳給 wrapper 與 check tool

**Files:**
- Modify: `deepagent-service/app/agent/chat_turn.py`
- Test: `deepagent-service/tests/test_chat_turn_connectors.py`

**Interfaces:**
- Consumes: B1 `ConnectorCallLog`, `CONNECTOR_CALL_LOG_FILENAME`; B2 `build_connector_tools(..., call_log=)`; B3 `build_check_tools(..., call_log=)`.
- Produces: `ChatTurn._call_log: ConnectorCallLog | None`（檔案 `workspace.root / "connector_calls.jsonl"`, 只在 connector 模式建立）.

- [ ] **Step 1: 寫失敗的測試**

`tests/test_chat_turn_connectors.py` 新增（`json` 已 import）:

```python
async def test_connector_call_writes_record_at_workspace_root(connector_turn_env) -> None:
    """connector tool 打一次, workspace 頂層就有 connector_calls.jsonl, 內容不含資料列."""
    async with ChatTurn(_connector_request()) as turn:
        await turn.prepare()
        tools_by_name = turn._agent.nodes["tools"].bound.tools_by_name
        tools_by_name["demo_quality_list_fabs"].invoke({})
        record_path = turn._workspace.root / "connector_calls.jsonl"

        assert record_path.exists()
        [record] = [json.loads(line) for line in record_path.read_text().splitlines()]
        assert record["connector_id"] == "demo_quality"
        assert record["tool_name"] == "list_fabs"
        assert record["landed"] is True
        assert "rows" not in record and "preview_rows" not in record


async def test_connector_call_record_is_visible_to_check_dashboard_in_next_turn(
    connector_turn_env,
) -> None:
    """第二輪 (同 session, 新 ChatTurn) 讀得到第一輪的紀錄: 純改版面不用重打 connector."""
    request = _connector_request()
    async with ChatTurn(request) as first_turn:
        await first_turn.prepare()
        first_turn._agent.nodes["tools"].bound.tools_by_name["demo_quality_list_fabs"].invoke({})
        # 正常流程由 stream() 結尾 persist; 這裡不驅動模型, 所以手動推一代 zip.
        first_turn._store.persist(first_turn._workspace)

    async with ChatTurn(request) as second_turn:
        await second_turn.prepare()
        assert (second_turn._workspace.root / "connector_calls.jsonl").exists()
        assert second_turn._call_log is not None
        records = second_turn._call_log.load()

    assert [(record["connector_id"], record["tool_name"]) for record in records] == [
        ("demo_quality", "list_fabs")
    ]


async def test_file_mode_has_no_call_log(connector_turn_env) -> None:
    async with ChatTurn(_connector_request(connectors=[])) as turn:
        await turn.prepare()
        assert turn._call_log is None
```

跨輪測試依賴的機制（`app/engine/workspace_store.py:66-77`）: 每一輪 `store.prepare()` 開一個新的 scratch 目錄, 再把最新一代 zip 解壓進去; zip 由 `_build_zip` 打包整個 `workspace.root`, 只排除 `.skills`, 所以頂層的 `connector_calls.jsonl` 會隨 zip 跨輪. 兩輪的 `workspace.root` 不同, 斷言的是檔案與紀錄內容, 不是路徑.

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd deepagent-service && uv run pytest tests/test_chat_turn_connectors.py -q`
Expected: 三條 FAIL（檔案不存在, `_call_log` 屬性不存在）.

- [ ] **Step 3: 實作**

```python
from app.engine.connector_call_log import CONNECTOR_CALL_LOG_FILENAME, ConnectorCallLog
```

`__init__` 補 `self._call_log: ConnectorCallLog | None = None`. `prepare()` connector 分支（A1 的版本上加三行）:

```python
            self._call_log = ConnectorCallLog(self._workspace.root / CONNECTOR_CALL_LOG_FILENAME)
            extra_tools = [
                *build_connector_tools(
                    connectors,
                    self._connection,
                    connection_lock,
                    landing_path,
                    call_budget=get_settings().CONNECTOR_CALL_BUDGET,
                    call_log=self._call_log,
                ),
                *build_check_tools(self._workspace, connectors, call_log=self._call_log),
            ]
```

`prepare` docstring 補「呼叫紀錄物件同一個實例給 wrapper 寫, 給 check_dashboard 讀」.

- [ ] **Step 4: 跑全套確認通過**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: 全綠.

- [ ] **Step 5: Commit**

```bash
git add deepagent-service/app/agent/chat_turn.py deepagent-service/tests/test_chat_turn_connectors.py
git commit -m "feat(deepagent): connector 模式建 ConnectorCallLog——wrapper 寫, check_dashboard 讀, 隨 workspace zip 跨輪"
```

---

### Task B5: 收尾 — spike 驗收重跑（含紀錄檢查）, spec 狀態, 交付

**Files:**
- Modify: `deepagent-service/spike/mcp-shell/out/`（換快照）
- Modify: `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md:3`
- Modify: 本計畫（勾選 Phase B 的 checkbox）

- [ ] **Step 1: 全套驗證**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: ruff 乾淨; 全綠. Phase B 新增約 27 條（B1: 10, B2: 5, B3: 12）.

- [ ] **Step 2: spike 驗收重跑（需要 OpenRouter 與真模型, 不在 CI）**

依 README 跑一輪, 對 Acceptance 三點; 這次 `check_dashboard` 已驗 keys 與讀層, 額外確認: 故意在對話裡要求「幫我加一張用 `defect_summary` 的圖」但不先打該 tool 時, 模型會先打 tool 再寫（finding 有效）. 把新一組 `dashboard.html` 快照放進 `out/`, 刪舊的. Commit: `chore(deepagent): spike 驗收快照——呼叫紀錄檢查上線後重跑`. 若驗收失敗, 回報使用者, 不自行改 prompt.

- [ ] **Step 3: spec 狀態列**

第 3 行的「D1–D4 (ii) 為 plan Phase B, 另開 PR」改為「D1–D4 (ii) 已依 plan Phase B 落地」; §4 表格與 §6.0 表格的「延後（09-09; 另開 PR）」改為「已落地」; 第 13 節補一列.

- [ ] **Step 4: Commit 與交付**

```bash
git add deepagent-service/spike/mcp-shell/out docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md docs/superpowers/plans/2026-09-08-mcp-dashboard-on-autoland.md
git commit -m "docs(spec): mcp-dashboard-on-autoland D1–D4 (ii) 已落地, plan Phase B checkbox 勾選"
```

交付流程同 A6（opus 全 branch 終審 → PR 描述 → 使用者觸發 merge）.

---

## 自我檢查（對 spec 逐節核對）

| spec 項目 | 對應 Task |
|---|---|
| D0 merge 方式 A（merge commit 已落地, dashboard 功能獨立 commit 重落） | merge commit `577d1ee`; A1–A5（本次）; B1–B4（另開 PR） |
| D5 `unwrap_path` 三元組 / `LandingResult` 欄位 | A3 |
| D5 `Raw response shape` 回饋段（四種句型） | A3 |
| D5 SKILL.md `r.data` 段 + Reading the response 三範例 | A4 |
| D5 spike 拿掉 `UNWRAP_RESULT` | A5 |
| D6 兩段 prompt 措辭; `inject_results` 不動 | A2 |
| D7 `dashboard_skill_root` 在 connector 模式傳入; SKILL.md Workflow 1 / 鐵律 2 | A1, A4 |
| D8 spike 保留, README, 人工重跑換快照 | A5, Checkpoint A, A6; B5 |
| D1–D4 (i) 最小 `check_dashboard`, 紀錄類檢查未啟用並註明（09-09 改案, 本次 merge） | A1 |
| D1 位置 workspace 頂層 `connector_calls.jsonl`, 跨輪, 不去重（(ii), 另開 PR） | B1, B4 |
| D2 內容（0 列 `landed:false`; tool 錯誤不記）; 寫入失敗退路（記憶體鏡像 + 降級）（(ii)） | B1, B2, B3 |
| D3 `ConnectorCallLog` 注入 wrapper 與 check（(ii)） | B2, B3, B4 |
| D4 keys 比對跨輪（(ii)） | B3 |
| D4b unwrap-path lint（handler 參數名不假設 `r`; 多筆不一致 warning）（(ii)） | B3 |
| §9 `test_graph.py` 兩邊合併 | A1 |
| §9 09-04 spec level 2 表格 | A4 |
| §10 測試與完成條件（09-09: 兩條 lint 不是本次完成條件） | A1 起每 task; A6; B5 |
| §6.2 過渡期狀態（人在迴圈, 錯誤不自動回模型） | Checkpoint A（人工修復迴圈） |
| D9 傳輸面, D10, D11 | 非本計畫（spec 已定案延後／不做） |

型別一致性: `unwrap_path: list[str] | None` 在 A3（回傳值, `LandingResult`, `EmptyLandingError`, `describe_raw_response_shape` 參數）, B1/B2（紀錄 dict）, B3（`_CallRecordGroup.unwrap_path`）同名同型; `envelope_keys` 在紀錄裡是 `list[str]`, 由 B2 以 `list(envelope_fields)` 產生, B3 以 `record.get("envelope_keys") or []` 讀. `build_check_tools` 在 A1 是 `(workspace, connectors)`, B3 加 keyword `call_log=None`, A1 的呼叫端不需改.
