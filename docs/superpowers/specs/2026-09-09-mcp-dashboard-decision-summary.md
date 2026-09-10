# MCP dashboard 接上 connector 自動落表——決策總結（已定／未定／有意留白）

> 狀態: **總結文件, 不含新決策.** 依 `2026-09-08-mcp-dashboard-on-autoland-design.md`（決策 spec, 以下稱「主 spec」）與 `2026-09-08-mcp-dashboard-on-autoland.md`（plan）於 2026-09-09 的狀態整理: Phase A 已經 PR #81 merge 進 `feat/mcp-dashboard`（merge commit `919be87`, 文件更新至 `7630a14`）. 本文的用途是讓下一個 spec／plan（Phase B 或 D9+D10）不必重讀 466 行主 spec 就知道哪些已經拍板、哪些拍板了還沒做、哪些根本沒拍板. 任何條目與主 spec 不一致時以主 spec 為準, 並回來修本文.
>
> 決策編號 D0–D11 沿用主 spec, 不重排. 「已定案」= 主 spec §12 已勾選; 「已落地」= 程式在 `feat/mcp-dashboard` 上且測試守住; 「未定案」= 主 spec 明講「隨 plan 拍板」「另開 spec」「依觀察決定」或列在非目標而未給結論者.

## 1. 現況一句話

connector 模式的 dashboard 在檢視時經宿主提供的 `mcp()` 現抓資料, 對話期每次 connector 呼叫自動落成只活一輪的 DuckDB 表; 模型從 wrapper 回饋的 `Raw response shape` 段學到 raw 回傳值與表的差異; `check_dashboard` 以最小形態上線（不驗 arg keys 與讀層）; 唯一能開頁的宿主是 spike, 錯誤由人貼回對話. 產品前端／Java／deepagent `/tool-call` 尚未實作.

## 2. 已定案且已落地（Phase A, PR #81）

| ID | 決定 | 落在哪 | 守住它的測試 |
|---|---|---|---|
| D0 | merge 方式 A: `feat/mcp-dashboard` merge `origin/feat/mcp-datasource`（`bcb61f3`）, 三個衝突檔取 datasource 側, merge commit 先落地（紅）, dashboard 功能以獨立 commit 重接 | merge commit `577d1ee`; 之後 11 個 commit | 兩親 merge 以 `git show --cc --format= 577d1ee` 驗過只含 `graph.py`/`middleware.py` 兩處 hunk |
| D5 | `r.data` 是 raw `structuredContent`, 宿主不拆封; `unwrap_envelope` 回傳三元組含 `unwrap_path`; wrapper 回饋多一段 `Raw response shape`（四種句型: array／非信封 dict／單層路徑／多層路徑＋信封欄位）; 0 列（`EmptyLandingError`）也附形狀說明 | `app/engine/api_snapshot.py`, `app/agent/connectors/wrapper.py` | `test_api_snapshot.py`（五種形狀路徑表）, `test_connector_wrapper.py`（四種句型＋空回應） |
| D5 附 | `{"result": <非信封 dict>}` 改為整包外層落成一列（`unwrap_path=None`）, 與 `r.data` 形狀一致——merge 前落的是內層. **有意的行為改變**, opus 終審指出後定案保留 | `api_snapshot.py`; plan A3 Step 4 註記 | `test_unwrap_envelope_result_wrapping_non_envelope_dict_keeps_outer_object` |
| D6 | connector 模式 qN 只供對話回答, dashboard 一律走 `mcp()` 現抓; `CONNECTOR_MODE_SYSTEM_SECTION` 與 `CONNECTOR_TABLES_RESET_NOTE` 改講法; `inject_results` 對 connector 模式不動 | `app/agent/prompts.py` | `test_prompts.py` |
| D7 | connector 模式 skill gate 指向 `.skills/builtin/mcp-data-dashboard`（`build_agent(dashboard_skill_root=)`）; SKILL.md 去 `land_as`, `r.data` 段改「raw, 不是 DuckDB 表, 路徑抄 `Raw response shape`」, 鐵律 2 的「session」= 本對話任一輪實際打過的呼叫, Reading the response 三種 `const rows = ...;` | `app/agent/chat_turn.py`, `skills/mcp-data-dashboard/SKILL.md` | `test_chat_turn_connectors.py`（三條 wiring）, `test_mcp_dashboard_skill_text.py`（新增） |
| D1–D4 (i) | `check_dashboard` 最小形態: 語法（`node --check`, 缺 node 或逾時 → 報告註記而非 finding）、禁止 token、`mcp()` 三引數為字面值、connector 與 tool 存在、CDN 白名單、`'erd'` theme; 報告末尾一律 `call-record checks not enabled` | `app/agent/tools/check.py` | `test_check_dashboard.py`（16 條） |
| D8（部分） | spike 保留為 throwaway; `bridge.py` 拿掉 `UNWRAP_RESULT` 固定 raw; README 契約段改指主 spec §7, 補「Manual repair loop」與「Acceptance」三點 | `spike/mcp-shell/` | 無自動化測試（需真模型） |
| §6.0 原則 | `check_dashboard` NEVER 產生模型無法用行動消除的 finding: 事實來源不可用時讓路並說明 | `check.py`（trailing notes 機制） | node 缺席／逾時／not-found 三條測試斷言 notes |
| §9 附帶 | `2026-09-04-mcp-dashboard-verification-options.md` level 2 事實來源改 `connector_calls.jsonl` | 該 spec | — |

驗證事實（PR #81 附錄）: `uv run ruff check .` 乾淨; `uv run pytest -q` 487 passed; opus 兩輪終審 Ready to merge; backend `./mvnw test` 因沙箱無法下載 mongod 未在本機跑, diff 對 backend 為 0 行, 留 CI.

## 3. 已定案但未落地

| ID | 決定 | 狀態 | 誰決定何時做 |
|---|---|---|---|
| D8（餘下） | merge 後手動重跑 spike 一次, 依 Acceptance 三點驗收, 新快照進 `out/` 並刪舊三張 | **未跑**（plan A6 Step 2 未勾; Checkpoint A 全部未勾）. `out/` 目前仍是三張舊快照, README 已誠實註明 | 使用者（需 OpenRouter 與真模型） |
| D1–D4 (ii) | `ConnectorCallLog`（engine 層 stdlib-only, workspace 頂層 `connector_calls.jsonl`, append-only, 跨輪, 只記 metadata, 0 列也記, tool 錯誤不記, 不去重）; 注入 wrapper 與 `check_dashboard`; 寫入失敗走本輪記憶體鏡像＋降級模式; arg keys 集合比對（跨輪, 不比值）; unwrap-path 讀層 lint（handler 參數名不假設 `r`, 只掃 `<param>.data` 第一層） | 設計完整（主 spec §6.1(ii)）, 實作步驟完整（plan Phase B, B1–B5, 含測試碼）; **未開 branch** | 依 Checkpoint A 觀察決定先做它或先做 D9+D10（見 §5） |
| D1–D4 (ii) 附 | Phase B 落地時 prompt 兩句改為「`check_dashboard` validates against the calls already recorded」, SKILL.md 鐵律 2 改為「as recorded in `check_dashboard`'s call record」 | 文字已在 plan A2/A4 註明 | 隨 Phase B |
| D10 | 檢視期 `mcp()` 錯誤回到模型: (1) 前端 prelude 把 `{error}` 結果也發到既有 `erd-artifact-error` 通道（訊息含 arg keys 不含值）; (2) connector 版 `REPAIR_SYSTEM_PROMPT`（帶 connector 清單、`mcp()` 契約摘要、raw 形狀那句）, `RepairRequest.connectors`, Java `AnalysisBrowserRepairClient` 補帶 | **延後**, 設計留存主 spec §6.2; 隨 D9 實作一併開 plan | 依 Checkpoint A（若主要失敗是值／權限／可用性, 先做） |
| D11 | deepagent 內**不**模擬瀏覽器執行（QuickJS／jsdom／Chromium 都不做）; 以 D10 讓安靜的錯誤變大聲 | 定案「不做」; 選 A 的材料留存（raw 落表 `.raw.json` 列於 D6 生命週期表, stub 比對規則＝D4） | 只在 spike 重跑或上線 Langfuse 顯示「錯層／錯 keys 仍是主要失敗且 `/repair` 修不好」時重開 |
| D9 頁面面 | `mcp(connector, tool, args, handler)` 回 `undefined`; handler 恰好一次、一個引數; `{data}`／`{error:{message}}`; 禁止 fetch／`window.parent`／自定義 `mcp`; connector 與 tool 字面值 | **已存在**（skill + spike prelude）, merge 只加 D5 的 raw; 列在本節是因為產品宿主尚未實作它 | — |
| D9 傳輸面（凍結項） | 四個 hop 各自唯一責任; postMessage 訊息名 `erd-mcp-call`／`erd-mcp-result`／`erd-artifact-error` 與欄位; `event.source === iframe.contentWindow` 驗來源; Java `POST /api/artifacts/{id}/mcp-call`（tool 層失敗一律 200＋`error`; 非 200 由前端收斂成 `AUTH`／`INVALID_CALL`／`RETRYABLE`）; deepagent `POST /tool-call`（重用 `mcp_adapter._call`, **不 `unwrap_envelope`**, 不落表）; SSO 只在 header; 錯誤碼固定集合（09-10 改為五個: `AUTH`／`RETRYABLE`／`TOOL_ERROR`／`INVALID_CALL`／`CONNECTOR_UNAVAILABLE`, 以「誰能做什麼」切）; prelude 由前端在 srcdoc 組裝時注入、只在 connector 模式注入; 五條跨層不變量 | 主 spec 稱「凍結」, 但 §4 表格與 §12 同時標為「草案, 隨 D9 實作 plan 確認」——**凍結的是文字, 拍板時機是寫 plan 時**. 零程式碼 | D9 實作 plan（尚未開） |
| D9 spike 對齊待辦 | 錯誤路徑補 `code`; 宿主驗 `event.source`; 逾時與 `RETRYABLE`; 非 200 收斂成 `AUTH`／`INVALID_CALL`／`RETRYABLE`; 回報通道改 `erd-artifact-error`; log 只記 keys; `bridge.py` 改重用 `mcp_adapter._call` | 只有「拿掉 `UNWRAP_RESULT`」是 merge 前置且已做; 其餘七項未動 | 隨 D9 plan（或 D8 重跑前順手） |

## 4. 未定案

主 spec 明講留待之後決定, 或列為非目標但沒給結論的:

| # | 事項 | 主 spec 位置 | 觸發拍板的條件 |
|---|---|---|---|
| U1 | **下一個 PR 先做哪個**: Phase B（D1–D4 (ii)）或 D9 傳輸面＋D10 | §6.2 末段, §13 09-09 列, plan Checkpoint A | Checkpoint A 人工測試觀察到的主要失敗形態: keys／讀層 → Phase B; 值／權限／逾時 → D9+D10. 結論寫進主 spec §13 |
| U2 | D9 頁面面四項提案: `r.error.code` 欄位（詞彙已於 09-10 定為五個, `retryable` 布林方案不採）; 明講 handler 永遠非同步; 明講 runtime 不吞 handler 例外; 明講 `args` 必須 JSON 可序列化 | §7 第三點 | D9 實作 plan |
| U3 | D9 留給 plan 的數值與落點: 前端 in-flight 上限與逾時（建議 6／60 s）; loading 骨架; `dataMode` 欄位落在哪個 DTO; Java `ConnectorSpec` 與 SSO header 共用抽取方式; deepagent 端點 pydantic schema 與測試 | §7「凍結／留給實作 plan 的分界」 | D9 實作 plan |
| U4 | 分享頁（非 owner viewer）的 `/mcp-call` 存取規則與錯誤回報路徑 | §7 ③, §6.2, §11 | 分享功能自己的 spec; 本端點只承諾「與 `GET /api/artifacts/{id}` 同一條規則」 |
| U5 | per-artifact 允許 tool 清單（deepagent 在 `DASHBOARD_HTML` 事件旁帶 `allowedCalls`, Java 存進 Artifact） | §7 ③ 擴充點 | 未排; 材料是 D2 的 `connector_calls.jsonl`, 所以至少在 Phase B 之後 |
| U6 | Java 側 `/mcp-call` 額度 | §7 ③ | v1 不設; 觀察「每 artifact 每分鐘呼叫數」超標再加 |
| U7 | level 2.5: 用紀錄的 `columns` 驗 handler 內 `row.xxx` 欄位名 | §6.1 D2／D4b「不做」, §11 | 「下一個 spec」; D2 已把 `columns` 記進紀錄, 寫入端不必再改 |
| U8 | D11 若重開, 選 A（QuickJS in-process）或 C（headless Chromium） | §6.3 | 需先證明缺口（spike 重跑或 Langfuse） |
| U9 | 跨輪保留落表或 raw payload | §11; datasource spec §10 | datasource spec 的觀察指標達標才重開; 目前明確不做 |
| U10 | `mcp-data-dashboard/SKILL.md`（1222 行）是否拆 references | D7 末句, §11 | 未排; gate 必讀清單是整個目錄, 拆了不影響 gate |
| U11 | connector 模式 `inject_results` 是否拿掉那段空的 proxy 腳本 | D6 | 「若日後要省再說」; 現況無害 |
| U12 | `node` 是否進 image | §11, 09-04 spec | 既有結論「不進」, 本輪未重議; `check_dashboard` 缺 node 時語法 pass 只留註記 |
| U13 | `check_dashboard` 鍵值抽取 helper（`_check_mcp_call` 內 `observed_keys` 相關）目前保留但未使用 | plan A1 Step 3 註記 | Phase B B3 會用; 若 U1 選 D9+D10 先做, 這段 dead code 留到 Phase B 為止 |

## 5. 有意接受的缺口（過渡期, 不是 bug）

主 spec §6.2「誠實的過渡期狀態」與 plan「快速迭代的阻塞點」已寫明, 這裡集中列出免得日後被當缺陷回報:

1. **沒有任何錯誤自動回到模型.** keys 寫錯、讀錯層、值不對被 server 拒、connector 不允許、逾時, 全在瀏覽器裡才浮現, 由人把 `window.onerror` log 或錯誤卡文字貼回對話（`generate.sh "<訊息>"`）. 人在迴圈裡是第一輪開發的刻意選擇.
2. **只有 spike 一個宿主.** 產品前端沒有 `mcp()`, Java 沒有 `/mcp-call`, deepagent 沒有 `/tool-call`. dashboard 只能在 `spike/mcp-shell/shell.html` 看.
3. **`check_dashboard` 不驗 keys 與讀層.** 「寫了沒打過的 tool」「讀錯層」只會在瀏覽器以空卡／錯誤卡／`TypeError` 出現.
4. **`/repair` 仍是 file 模式 prompt.** 它不知道 `mcp()`、connector 清單與 raw 形狀; connector 模式下修 `r.data.map is not a function` 時不知道列在 `result` 底下（D10 延後的代價）.
5. **`node` 不在 image.** 語法 pass 只留一行註記, 契約 lint 照跑.
6. **spike 快照過期.** `out/` 三張是 merge 前的模型行為（`r.data` vs `r.data.result` 來回猶豫）, 正是 Phase A 要修的; 新快照等 Checkpoint A.
7. **spike 錯誤路徑未對齊傳輸面草案.** 無 `code`、無來源驗證、無逾時、回報通道名不同（§3 表末列）; 對 mock server 跑成功路徑足夠, 但要知道它沒驗過失敗路徑.
8. **第二輪起 DuckDB 表已卸載**是 datasource 既定行為; 模型修 dashboard 靠對話歷史裡第一輪的回饋文字. 若人工測試發現模型第二輪重打 connector, 先改 A2 措辭或 A3 句型, 不加機制.

## 6. 下一步與分岔

```
Checkpoint A（人工, 真模型, spike）
├─ 觀察 Acceptance 三點; 記錄 handler 讀了哪層, 幾輪修好, 有無寫沒打過的 tool
├─ 仍讀錯層或第二輪重打 → 先改 prompt 措辭／回饋句型（A2/A3）, 再測; 不跳 Phase B
├─ 主要失敗 = keys／讀層 → Phase B（plan B1–B5, 另開 branch/PR）
└─ 主要失敗 = 值／權限／逾時 → D9 傳輸面 + D10（另開 spec/plan; 主 spec §7 草案為起點）
之後: 把結論寫進主 spec §13; 換 out/ 快照; 勾 plan A6 Step 2
```

不論走哪條, 主 spec §11 非目標（D11、跨輪 raw、level 2.5、SKILL.md 拆分、node 進 image）維持不做, 除非有新證據並另開 spec.

## 7. 來源對照

| 本文節 | 主 spec | plan |
|---|---|---|
| §2 已落地 | §4 總覽, §5 D0/D5–D8, §6.1(i), §12 | Phase A A1–A5, A6 Step 1/3/4, 自我檢查表 |
| §3 未落地 | §6.1(ii), §6.2 D10, §6.3 D11, §7 D9 | Phase B B1–B5, Checkpoint A, A6 Step 2 |
| §4 未定案 | §6.2 末段, §7 提案與分界, §11 非目標 | Checkpoint A 末兩點 |
| §5 過渡期 | §6.2「誠實的過渡期狀態」 | 「快速迭代的阻塞點」 |
| 名詞（wrapper／envelope／handler／landing／call record／skill gate／host） | — | 「名詞」表 |
