# MCP dashboard 接上 connector 自動落表——決策總結（已定／未定／有意留白）

> 狀態: **總結文件, 不含新決策.** 依 [`2026-09-08-mcp-dashboard-on-autoland-design.md`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md)（決策 spec, 以下稱「主 spec」）與 [`2026-09-08-mcp-dashboard-on-autoland.md`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/plans/2026-09-08-mcp-dashboard-on-autoland.md)（plan）於 2026-09-09 的狀態整理: Phase A 已經 PR #81 merge 進 `feat/mcp-dashboard`（merge commit `919be87`, 文件更新至 `7630a14`）. 本文的用途是讓下一個 spec／plan（Phase B 或 D9+D10）不必重讀 466 行主 spec 就知道哪些已經拍板、哪些拍板了還沒做、哪些根本沒拍板. 任何條目與主 spec 不一致時以主 spec 為準, 並回來修本文.
>
> **2026-09-16 更新**: 依 [PR #83](https://github.com/Michelle12369/cowork/pull/83)（hop ①④, merged 09-11）、09-11 Checkpoint A、[PR #87](https://github.com/Michelle12369/cowork/pull/87)（`feat/mcp-dashboard` → `feat/9E`, 開中）、[PR #88](https://github.com/Michelle12369/cowork/pull/88)（hop ②③, 開中）與 [PR #85](https://github.com/Michelle12369/cowork/pull/85)（dev scripts, 開中）更新 §1、§2b、§3、§4、§5、§6. 09-09 原文只在過時處改, 其餘不動. 09-16 兩項新決策（D1–D4 (ii) 不做; node 進 image）記在 [主 spec §13](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md#13-決策紀錄), 本文只同步狀態.
>
> 決策編號 D0–D11 沿用主 spec, 不重排. 「已定案」= 主 spec §12 已勾選; 「已落地」= 程式在 `feat/mcp-dashboard` 上且測試守住; 「未定案」= 主 spec 明講「隨 plan 拍板」「另開 spec」「依觀察決定」或列在非目標而未給結論者.

## 1. 現況一句話

connector 模式的 dashboard 在檢視時經宿主提供的 `mcp()` 現抓資料, 對話期每次 connector 呼叫自動落成只活一輪的 DuckDB 表; 模型從 wrapper 回饋的 `Raw response shape` 段學到 raw 回傳值與表的差異; `check_dashboard` 以最小形態上線（不驗 arg keys 與讀層）.

**2026-09-16 現況.** deepagent 側四個 hop 中的 ①（`mcp()` prelude, `results.py` 產出時注入）與 ④（`POST /tool-call`, 五個錯誤碼）已隨 [PR #83](https://github.com/Michelle12369/cowork/pull/83) 進 `feat/mcp-dashboard`; connector 模式頁面不再帶 `__ERD_RESULTS__`（[PR #87](https://github.com/Michelle12369/cowork/pull/87) `9377860`／`85b6623`）. Checkpoint A 已於 09-11 用真模型跑過一次, 未觀察到失敗形態. 產品宿主 hop ②（前端 `useMcpBridge`）與 ③（Java `/mcp-call`）在 [PR #88](https://github.com/Michelle12369/cowork/pull/88)（開中, opus 終審 Ready to merge）; 合併前唯一能開頁的宿主仍是 spike. `/repair` 仍是 file 模式 prompt. 整條 `feat/mcp-dashboard` 以 [PR #87](https://github.com/Michelle12369/cowork/pull/87) 對 `feat/9E` 開中.

## 2. 已定案且已落地（Phase A, PR #81）

| ID | 決定 | 落在哪 | 守住它的測試 |
|---|---|---|---|
| D0 | merge 方式 A: `feat/mcp-dashboard` merge `origin/feat/mcp-datasource`（`bcb61f3`）, 三個衝突檔取 datasource 側, merge commit 先落地（紅）, dashboard 功能以獨立 commit 重接 | merge commit `577d1ee`; 之後 11 個 commit | 兩親 merge 以 `git show --cc --format= 577d1ee` 驗過只含 `graph.py`/`middleware.py` 兩處 hunk |
| D5 | `r.data` 是 raw `structuredContent`, 宿主不拆封; `unwrap_envelope` 回傳三元組含 `unwrap_path`; wrapper 回饋多一段 `Raw response shape`（四種句型: array／非信封 dict／單層路徑／多層路徑＋信封欄位）; 0 列（`EmptyLandingError`）也附形狀說明 | `app/engine/api_snapshot.py`, `app/agent/connectors/wrapper.py` | `test_api_snapshot.py`（五種形狀路徑表）, `test_connector_wrapper.py`（四種句型＋空回應） |
| D5 附 | `{"result": <非信封 dict>}` 改為整包外層落成一列（`unwrap_path=None`）, 與 `r.data` 形狀一致——merge 前落的是內層. **有意的行為改變**, opus 終審指出後定案保留 | `api_snapshot.py`; plan A3 Step 4 註記 | `test_unwrap_envelope_result_wrapping_non_envelope_dict_keeps_outer_object` |
| D6 | connector 模式 qN 只供對話回答, dashboard 一律走 `mcp()` 現抓; `CONNECTOR_MODE_SYSTEM_SECTION` 與 `CONNECTOR_TABLES_RESET_NOTE` 改講法; `inject_results` 對 connector 模式不動. **09-16 更新**: 改為 connector 模式完全不呼叫 `inject_results`, `chat_turn.py` 與 `repair_flow.py` 都只把 prelude 注入主題改寫後的 HTML（[PR #87](https://github.com/Michelle12369/cowork/pull/87) `9377860`／`85b6623`）; U11 因此結案 | `app/agent/prompts.py`, `app/agent/chat_turn.py`, `app/agent/repair_flow.py` | `test_prompts.py`, `test_chat_turn_connectors.py`, `test_repair.py` |
| D7 | connector 模式 skill gate 指向 `.skills/builtin/mcp-data-dashboard`（`build_agent(dashboard_skill_root=)`）; SKILL.md 去 `land_as`, `r.data` 段改「raw, 不是 DuckDB 表, 路徑抄 `Raw response shape`」, 鐵律 2 的「session」= 本對話任一輪實際打過的呼叫, Reading the response 三種 `const rows = ...;` | `app/agent/chat_turn.py`, `skills/mcp-data-dashboard/SKILL.md` | `test_chat_turn_connectors.py`（三條 wiring）, `test_mcp_dashboard_skill_text.py`（新增） |
| D1–D4 (i) | `check_dashboard` 最小形態: 語法（`node --check`, 缺 node 或逾時 → 報告註記而非 finding）、禁止 token、`mcp()` 三引數為字面值、connector 與 tool 存在、CDN 白名單、`'erd'` theme; 報告末尾一律 `call-record checks not enabled` | `app/agent/tools/check.py` | `test_check_dashboard.py`（16 條） |
| D8（部分） | spike 保留為 throwaway; `bridge.py` 拿掉 `UNWRAP_RESULT` 固定 raw; README 契約段改指主 spec §7, 補「Manual repair loop」與「Acceptance」三點 | `spike/mcp-shell/` | 無自動化測試（需真模型） |
| §6.0 原則 | `check_dashboard` NEVER 產生模型無法用行動消除的 finding: 事實來源不可用時讓路並說明 | `check.py`（trailing notes 機制） | node 缺席／逾時／not-found 三條測試斷言 notes |
| §9 附帶 | `2026-09-04-mcp-dashboard-verification-options.md` level 2 事實來源改 `connector_calls.jsonl` | 該 spec | — |

驗證事實（PR #81 附錄）: `uv run ruff check .` 乾淨; `uv run pytest -q` 487 passed; opus 兩輪終審 Ready to merge; backend `./mvnw test` 因沙箱無法下載 mongod 未在本機跑, diff 對 backend 為 0 行, 留 CI.

## 2b. 09-09 之後已落地（2026-09-16 補）

| ID | 決定 | 落在哪 | PR | 守住它的測試 |
|---|---|---|---|---|
| D9 hop ① | `mcp()` prelude 由 deepagent `results.py` 產出時注入（`id="erd-mcp-runtime"`, `data-erd-runtime` 版本標記）, 只在 connector 模式, 迭代／repair 時剝除重注; 09-10 團隊決定取代「前端 srcdoc 時注入」原案 | [`app/engine/results.py`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/deepagent-service/app/engine/results.py) | [PR #83](https://github.com/Michelle12369/cowork/pull/83)（merged 09-11） | `test_results.py`, `test_mcp_runtime_prelude.py`, `test_chat_turn_connectors.py` |
| D9 hop ④ | `POST /tool-call`: 重用 `mcp_adapter._call`, 不 `unwrap_envelope`, 不落表; 五個錯誤碼分類器與模板; 契約 fixture `mcp_result_examples.json` 給 Java 與前端釘住 | [`app/agent/connectors/tool_call_flow.py`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/deepagent-service/app/agent/connectors/tool_call_flow.py), [`error_codes.py`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/deepagent-service/app/agent/connectors/error_codes.py), [`main.py`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/deepagent-service/app/main.py) | [PR #83](https://github.com/Michelle12369/cowork/pull/83) | `test_tool_call_endpoint.py`, `test_mcp_result_examples_fixture.py` |
| D10 (1) | prelude 對 `TOOL_ERROR`／`INVALID_CALL` 結果另發 `erd-artifact-error`（訊息 `mcp <code>: <message>`, 只截 500 字）, 其餘三個 code 不轉發. 訊息尚未帶 connector／tool／arg keys | `results.py` 同上 | [PR #83](https://github.com/Michelle12369/cowork/pull/83) | `test_mcp_runtime_prelude.py` |
| U2 | 四項頁面面提案全部寫進 SKILL.md: `r.error.code` 五個值與頁面行為（只有 `RETRYABLE` 給 Retry, `AUTH` 整頁提示）; handler 一律非同步; runtime 不吞 handler 例外; `toolArgs` 必須 JSON 可序列化 | [`skills/mcp-data-dashboard/SKILL.md`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/deepagent-service/skills/mcp-data-dashboard/SKILL.md) | [PR #83](https://github.com/Michelle12369/cowork/pull/83)（Task 5） | `test_mcp_dashboard_skill_text.py` |
| D8（餘下） | Checkpoint A 第一次真模型跑（deepseek-v4-flash, 09-11）: handler 第一版全讀 `r.data.result`, 純改版面輪 0 次 connector 呼叫, 無不存在／沒打過的 tool, 無禁止 token; headless Chromium 經 bridge 開頁 4 張圖有畫. `out/dashboard.html` 換成真模型產出, 三張舊快照刪除 | [`spike/mcp-shell/out/`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/deepagent-service/spike/mcp-shell/out), 主 spec §13 09-11 條, plan A6 Step 2 | [PR #83](https://github.com/Michelle12369/cowork/pull/83) | 無自動化測試（需真模型） |
| D9 spike 對齊 | 七項中六項已對齊: 錯誤帶 `code`; 宿主逾時 60 s 回 `RETRYABLE`、遲到結果丟棄; 非 200 收斂; 回報通道改 `erd-artifact-error`; `bridge.py` 改打真的 `/tool-call`（不再鏡像 adapter）; `UNWRAP_RESULT` 已移除. **未對齊**: `shell.html` 不驗 `event.source`. [PR #88](https://github.com/Michelle12369/cowork/pull/88) 之後 spike 宿主半邊不再維護, 這一項不補 | [`spike/mcp-shell/`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/deepagent-service/spike/mcp-shell) | [PR #83](https://github.com/Michelle12369/cowork/pull/83)（Task 9）; [PR #85](https://github.com/Michelle12369/cowork/pull/85)（bridge 依設定轉發任一 connector, 未知 id → `INVALID_CALL`, 開中） | 無 |
| D6 補 | connector 模式不注入 `__ERD_RESULTS__`（見 §2 D6 列） | `chat_turn.py`, `repair_flow.py` | [PR #87](https://github.com/Michelle12369/cowork/pull/87)（開中） | `test_chat_turn_connectors.py`, `test_repair.py` |

## 3. 已定案但未落地

| ID | 決定 | 狀態 | 誰決定何時做 |
|---|---|---|---|
| D8（餘下） | merge 後手動重跑 spike 一次, 依 Acceptance 三點驗收, 新快照進 `out/` 並刪舊三張 | **已於 09-11 完成**, 移至 §2b | — |
| D1–D4 (ii) | `ConnectorCallLog`（engine 層 stdlib-only, workspace 頂層 `connector_calls.jsonl`, append-only, 跨輪, 只記 metadata, 0 列也記, tool 錯誤不記, 不去重）; 注入 wrapper 與 `check_dashboard`; 寫入失敗走本輪記憶體鏡像＋降級模式; arg keys 集合比對（跨輪, 不比值）; unwrap-path 讀層 lint（handler 參數名不假設 `r`, 只掃 `<param>.data` 第一層） | **09-16 撤回, 不做**（[主 spec §13](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md#13-決策紀錄) 09-16 條）: 不建呼叫紀錄也不留 raw 快照來驗 `mcp(...)`; `check_dashboard` 維持 (i) 的語法 pass 加既有契約 lint. [plan Phase B](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/plans/2026-09-08-mcp-dashboard-on-autoland.md) B1–B5 取消, 設計文字留在主 spec §6.1(ii) 供日後有證據時重開 | — |
| D1–D4 (ii) 附 | Phase B 落地時 prompt 兩句改為「`check_dashboard` validates against the calls already recorded」, SKILL.md 鐵律 2 改為「as recorded in `check_dashboard`'s call record」 | **隨 (ii) 撤回**; 現行措辭（「the tool feedback in this conversation is the record」）即為定稿 | — |
| D10 | 檢視期 `mcp()` 錯誤回到模型: (1) 前端 prelude 把 `{error}` 結果也發到既有 `erd-artifact-error` 通道（訊息含 arg keys 不含值）; (2) connector 版 `REPAIR_SYSTEM_PROMPT`（帶 connector 清單、`mcp()` 契約摘要、raw 形狀那句）, `RepairRequest.connectors`, Java `AnalysisBrowserRepairClient` 補帶 | **(1) 已落地**（[PR #83](https://github.com/Michelle12369/cowork/pull/83), 見 §2b; 訊息尚未帶 connector／tool／arg keys）. **(2) 仍延後**: `RepairRequest` 沒有 `connectors`, `run_repair` 只用 file 模式 prompt; [PR #88](https://github.com/Michelle12369/cowork/pull/88) 留意欄明列「修不好屬預期」 | (2) 由使用者決定; [PR #88](https://github.com/Michelle12369/cowork/pull/88) 合併後修復卡在產品前端出現, 屆時 file 模式 prompt 成為可觸及的缺陷 |
| D11 | deepagent 內**不**模擬瀏覽器執行（QuickJS／jsdom／Chromium 都不做）; 以 D10 讓安靜的錯誤變大聲 | 定案「不做」; 選 A 的材料留存（raw 落表 `.raw.json` 列於 D6 生命週期表, stub 比對規則＝D4） | 只在 spike 重跑或上線 Langfuse 顯示「錯層／錯 keys 仍是主要失敗且 `/repair` 修不好」時重開 |
| D9 頁面面 | `mcp(connector, tool, args, handler)` 回 `undefined`; handler 恰好一次、一個引數; `{data}`／`{error:{message}}`; 禁止 fetch／`window.parent`／自定義 `mcp`; connector 與 tool 字面值 | **已存在**（skill + spike prelude）, merge 只加 D5 的 raw; 列在本節是因為產品宿主尚未實作它 | — |
| D9 傳輸面（凍結項） | 四個 hop 各自唯一責任; postMessage 訊息名 `erd-mcp-call`／`erd-mcp-result`／`erd-artifact-error` 與欄位; `event.source === iframe.contentWindow` 驗來源; Java `POST /api/artifacts/{id}/mcp-call`（tool 層失敗一律 200＋`error`; 非 200 由前端收斂成 `AUTH`／`INVALID_CALL`／`RETRYABLE`）; deepagent `POST /tool-call`（重用 `mcp_adapter._call`, **不 `unwrap_envelope`**, 不落表）; SSO 只在 header; 錯誤碼固定集合（09-10 改為五個: `AUTH`／`RETRYABLE`／`TOOL_ERROR`／`INVALID_CALL`／`CONNECTOR_UNAVAILABLE`, 以「誰能做什麼」切）; prelude 由 deepagent 產出時注入（09-10 改案）、只在 connector 模式注入; 五條跨層不變量 | **hop ①④ 已合併**（[PR #83](https://github.com/Michelle12369/cowork/pull/83), 見 §2b）. **hop ②③ 在 [PR #88](https://github.com/Michelle12369/cowork/pull/88)**（`feat/mcp-dashboard-host` → `feat/mcp-dashboard`, 開中, opus 終審 Ready to merge）: 前端 [`useMcpBridge.ts`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard-host/frontend/src/hooks/useMcpBridge.ts)（source 驗證、75 s 逾時、重掛與自我導覽防護）; Java `ArtifactMcpCallService`＋`AnalysisToolCallClient`（ownership 兩步、`selectedConnectors` 允許清單、2xx 原樣字串直通、非 2xx 折疊、log 只記 arg keys）. 決策 H1–H9 見 [`2026-09-15-mcp-host-bridge-design.md`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard-host/docs/superpowers/specs/2026-09-15-mcp-host-bridge-design.md), plan [`2026-09-15-mcp-host-bridge.md`](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard-host/docs/superpowers/plans/2026-09-15-mcp-host-bridge.md). 契約文字未改 | 合併 [PR #88](https://github.com/Michelle12369/cowork/pull/88) 進 `feat/mcp-dashboard`, 再隨 [PR #87](https://github.com/Michelle12369/cowork/pull/87) 進 `feat/9E`; 手動端到端（前端＋Java＋deepagent＋`mock_server.py`）尚未跑 |
| D9 spike 對齊待辦 | 錯誤路徑補 `code`; 宿主驗 `event.source`; 逾時與 `RETRYABLE`; 非 200 收斂成 `AUTH`／`INVALID_CALL`／`RETRYABLE`; 回報通道改 `erd-artifact-error`; log 只記 keys; `bridge.py` 改重用 `mcp_adapter._call` | **六項已對齊**（[PR #83](https://github.com/Michelle12369/cowork/pull/83) Task 9, 移至 §2b）; `event.source` 驗證未做且不再補 | — |

## 4. 未定案

主 spec 明講留待之後決定, 或列為非目標但沒給結論的:

| # | 事項 | 主 spec 位置 | 觸發拍板的條件 |
|---|---|---|---|
| U1 | **下一個 PR 先做哪個**: Phase B（D1–D4 (ii)）或 D9 傳輸面＋D10 | §6.2 末段, §13 09-09 列, plan Checkpoint A | **09-16 結案**: Phase B 撤回（[主 spec §13](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md#13-決策紀錄) 09-16 條）, D9 傳輸面已先行（[PR #83](https://github.com/Michelle12369/cowork/pull/83)、[PR #88](https://github.com/Michelle12369/cowork/pull/88)）; 剩下的下一個 PR 只有 **D10 (2)**（connector 版 repair prompt） |
| U2 | D9 頁面面四項提案: `r.error.code` 欄位（詞彙已於 09-10 定為五個, `retryable` 布林方案不採）; 明講 handler 永遠非同步; 明講 runtime 不吞 handler 例外; 明講 `args` 必須 JSON 可序列化 | §7 第三點 | **已定案並合併**（[PR #83](https://github.com/Michelle12369/cowork/pull/83) Task 5, 見 §2b） |
| U3 | D9 留給 plan 的數值與落點: 前端 in-flight 上限與逾時（建議 6／60 s）; loading 骨架; `dataMode` 欄位落在哪個 DTO; Java `ConnectorSpec` 與 SSO header 共用抽取方式; deepagent 端點 pydantic schema 與測試 | §7「凍結／留給實作 plan 的分界」 | deepagent 部分已合併（[PR #83](https://github.com/Michelle12369/cowork/pull/83)）. 前端／Java 部分在 [PR #88](https://github.com/Michelle12369/cowork/pull/88) 定案: **不設 in-flight 上限**（H2, 接受 servlet worker 被占滿的代價, 留 U6 補）; 三層逾時由內而外 deepagent 60 s < Java 65 s < 前端 75 s（H3／H4）; SSO header 由 `AnalysisToolCallClient` 送; `data` 原樣字串直通（H6）. `dataMode` 落點與 loading 骨架未在該 PR 提及 |
| U4 | 分享頁（非 owner viewer）的 `/mcp-call` 存取規則與錯誤回報路徑 | §7 ③, §6.2, §11 | 分享功能自己的 spec; 本端點只承諾「與 `GET /api/artifacts/{id}` 同一條規則」 |
| U5 | per-artifact 允許 tool 清單（deepagent 在 `DASHBOARD_HTML` 事件旁帶 `allowedCalls`, Java 存進 Artifact） | §7 ③ 擴充點 | 未排. 原定材料 `connector_calls.jsonl` 隨 D1–D4 (ii) 撤回而不存在; 若日後要做, 材料改為產出當輪 wrapper 手上的呼叫清單, 另開 spec |
| U6 | Java 側 `/mcp-call` 額度 | §7 ③ | v1 不設; 觀察「每 artifact 每分鐘呼叫數」超標再加. [PR #88](https://github.com/Michelle12369/cowork/pull/88) H2 不設 in-flight 上限, 把 per-artifact 速率限制明列為待辦, 重要性上升 |
| U7 | level 2.5: 用紀錄的 `columns` 驗 handler 內 `row.xxx` 欄位名 | §6.1 D2／D4b「不做」, §11 | **09-16 隨 D1–D4 (ii) 撤回而不做**（[主 spec §13](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md#13-決策紀錄) 09-16 條）; 沒有紀錄就沒有 `columns` 可比 |
| U8 | D11 若重開, 選 A（QuickJS in-process）或 C（headless Chromium） | §6.3 | 需先證明缺口（spike 重跑或 Langfuse）. 09-16 D1–D4 (ii) 撤回後, A 案的 stub 比對規則（＝D4）也失去材料, 重開門檻更高 |
| U9 | 跨輪保留落表或 raw payload | §11; datasource spec §10 | datasource spec 的觀察指標達標才重開; 目前明確不做 |
| U10 | `mcp-data-dashboard/SKILL.md`（1222 行）是否拆 references | D7 末句, §11 | 未排; gate 必讀清單是整個目錄, 拆了不影響 gate |
| U11 | connector 模式 `inject_results` 是否拿掉那段空的 proxy 腳本 | D6 | **已結案**: connector 模式不再呼叫 `inject_results`（[PR #87](https://github.com/Michelle12369/cowork/pull/87) `9377860`／`85b6623`, 見 §2 D6） |
| U12 | `node` 是否進 image | §11, 09-04 spec | **09-16 定案: 進 base image**（主 spec §13 09-16 條）. Dockerfile 改動待做; 做完後 `check_dashboard` 語法 pass 在部署環境一律執行 |
| U13 | `check_dashboard` 鍵值抽取 helper（`_check_mcp_call` 內 `observed_keys` 相關）目前保留但未使用 | plan A1 Step 3 註記 | **09-16 定案清掉**（[主 spec §13](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md#13-決策紀錄) 09-16 條）: `_extract_object_keys` 與「call-record checks not enabled」註記一併移除, 待做 |

## 5. 有意接受的缺口（過渡期, 不是 bug）

主 spec §6.2「誠實的過渡期狀態」與 plan「快速迭代的阻塞點」已寫明, 這裡集中列出免得日後被當缺陷回報:

1. **錯誤回到模型只通了一半（09-16 更新）.** prelude 已把 `TOOL_ERROR`／`INVALID_CALL` 發到 `erd-artifact-error`（[PR #83](https://github.com/Michelle12369/cowork/pull/83)）, handler throw 走既有 `head-inject.vm` 捕捉; [PR #88](https://github.com/Michelle12369/cowork/pull/88) 合併後兩者都會在產品前端變成修復卡. 但 `/repair` 仍是 file 模式 prompt（第 4 點）, 且 prelude 訊息沒有 connector／tool／arg keys. keys 寫錯、讀錯層但防禦性寫法、`AUTH`／`RETRYABLE`／`CONNECTOR_UNAVAILABLE` 仍不回到模型. spike 上仍由人貼回對話（`generate.sh` 已由 [PR #85](https://github.com/Michelle12369/cowork/pull/85) 的 `dev_chat.py` 取代, 開中）.
2. **產品宿主待合併（09-16 更新）.** hop ②③ 在 [PR #88](https://github.com/Michelle12369/cowork/pull/88), 合併前 dashboard 只能在 `spike/mcp-shell/shell.html` 看; `bridge.py` 已改接真的 `/tool-call`（[PR #83](https://github.com/Michelle12369/cowork/pull/83) Task 9）. [PR #88](https://github.com/Michelle12369/cowork/pull/88) 之後 spike 的宿主半邊不再維護, `mock_server.py` 留作本機驗收.
3. **`check_dashboard` 不驗 keys 與讀層（09-16 起是定案, 不再是過渡期）.** 「寫了沒打過的 tool」「讀錯層」只會在瀏覽器以空卡／錯誤卡／`TypeError` 出現, 由 D10 的修復卡接手.
4. **`/repair` 仍是 file 模式 prompt.** 它不知道 `mcp()`、connector 清單與 raw 形狀; connector 模式下修 `r.data.map is not a function` 時不知道列在 `result` 底下（D10 延後的代價）.
5. **`node` 不在 image（09-16 已定案要加, Dockerfile 待改）.** 改好前語法 pass 只留一行註記, 契約 lint 照跑.
6. **spike 快照已換（09-16 更新, 缺口已補）.** `out/dashboard.html` 是 09-11 真模型第二輪產出, 含 `erd-mcp-runtime` 區塊; 三張舊快照已刪.
7. **spike 錯誤路徑大致對齊（09-16 更新）.** `code`、逾時、非 200 收斂、`erd-artifact-error` 通道、`/tool-call` 直通都已補（§2b）; 只剩 `shell.html` 不驗 `event.source`, 因宿主半邊停止維護而不補.
8. **第二輪起 DuckDB 表已卸載**是 datasource 既定行為; 模型修 dashboard 靠對話歷史裡第一輪的回饋文字. 若人工測試發現模型第二輪重打 connector, 先改 A2 措辭或 A3 句型, 不加機制.

## 6. 下一步與分岔

```
Checkpoint A（09-11 已跑一次, 無失敗形態, 樣本不足）
├─ D9 傳輸面: hop ①④ 已合併（PR #83）; hop ②③ PR #88 開中 → 合併進 feat/mcp-dashboard
├─ PR #87 feat/mcp-dashboard → feat/9E（開中）
├─ PR #88 合併後: 手動端到端（前端＋Java＋deepagent＋mock_server.py）; 修復卡在產品前端首次可見
└─ 之後（U1 已於 09-16 結案, Phase B 撤回）:
   ├─ D10 (2): connector 版 REPAIR_SYSTEM_PROMPT, RepairRequest.connectors, Java AnalysisBrowserRepairClient 補帶;
   │           順手讓 prelude 訊息帶 connector／tool／arg keys（主 spec §6.2 原要求）
   ├─ Dockerfile 加 node（U12）; check.py 清掉 (ii) 預留的 helper 與註記（U13）
   └─ 之後的小項見 §4 仍開放的 U3／U4／U6／U10
```

[主 spec §11](https://github.com/Michelle12369/cowork/blob/feat/mcp-dashboard/docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md#11-非目標) 非目標（D11、跨輪 raw、level 2.5、SKILL.md 拆分）維持不做, 除非有新證據並另開 spec; D1–D4 (ii) 自 09-16 起同列. node 進 image 已於 09-16 改案為做（U12）.

## 7. 來源對照

| 本文節 | 主 spec | plan |
|---|---|---|
| §2 已落地 | §4 總覽, §5 D0/D5–D8, §6.1(i), §12 | Phase A A1–A5, A6 Step 1/3/4, 自我檢查表 |
| §2b 09-09 之後已落地 | §7 hop ①④, §6.2 D10 (1), §13 09-10／09-11 條 | `2026-09-10-mcp-tool-call-endpoint.md` Task 1–9; Checkpoint A; A6 Step 2 |
| §3 未落地 | §6.1(ii)（09-16 撤回）, §6.2 D10, §6.3 D11, §7 D9 | Phase B B1–B5（撤回）, Checkpoint A, A6 Step 2 |
| §4 未定案 | §6.2 末段, §7 提案與分界, §11 非目標 | Checkpoint A 末兩點 |
| §5 過渡期 | §6.2「誠實的過渡期狀態」 | 「快速迭代的阻塞點」 |
| 名詞（wrapper／envelope／handler／landing／call record／skill gate／host） | — | 「名詞」表 |
