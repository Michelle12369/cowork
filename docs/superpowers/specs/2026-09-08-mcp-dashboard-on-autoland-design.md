# MCP dashboard 接上 connector 自動落表——兩條 branch merge 的設計決策

> 狀態: **已 merge.** D0, D5–D8 與 D1–D4 (i) 依 plan `2026-09-08-mcp-dashboard-on-autoland.md` Phase A 落地, 經 PR #81 於 2026-09-09 merge 進 `feat/mcp-dashboard`（merge commit `919be87`, 基準 datasource `bcb61f3`; ruff 乾淨, pytest 487 綠, opus 兩輪終審 Ready to merge）. 待辦: Checkpoint A 人工測試與 spike 快照; D1–D4 (ii) 為 plan Phase B, 另開 PR. 2026-09-08 merge 所需決策全數定案（第 12 節）; D9 傳輸面、D10、D11 為留存草案. 本文針對 `origin/feat/mcp-datasource` 的 `bcb61f3`（2026-09-08, 全 sha `bcb61f3b2214b04c7ab5cf54a8381ead3c8c573e`）撰寫; datasource branch 仍在變動, 實際 merge 前 MUST 先把本文的衝突盤點（第 3 節）與檔案影響表（第 9 節）對照當時的 HEAD 重新核對, 並在此更新基準 sha. 下一步是 writing-plans. 依 superpowers `brainstorming` 的 architectural 路徑撰寫: 先列現況與衝突, 再列每個決策的選項, 取捨與建議; 拍板後才走 `writing-plans` 產 plan, 拍板前不動程式.
>
> 對象 branch: `feat/mcp-dashboard`（本 branch, 已含 `feat/9E`）與 `feat/mcp-datasource`（基準 `bcb61f3`, PR #78 之後的 31 個 commit）. 相關文件: `2026-08-30-mcp-datasource-design.md`（datasource branch 版, 第 7/10/11 節）, `2026-09-04-mcp-dashboard-verification-options.md`, plan `2026-09-06-connector-autoland-ephemeral.md`（datasource branch）, PR #40（檢查層退場實驗, 2026-08-09）.
>
> 決策編號 D0–D11 固定不重排; 第 4 節是總覽, 第 5–7 節依主題分三群.

## 1. 這是在做什麼

兩條 branch 各自往同一個終點走——「connector 模式的 dashboard 在檢視時自己透過宿主提供的 `mcp()` 打 MCP 取數, 不再把資料注入 HTML」——但中間各改了一段不同的管線:

- `feat/mcp-datasource` 改的是**對話期**: connector tool 每次呼叫自動落成 DuckDB 表, 表名用參數 hash, 表只活一輪, 並拆掉 `land_as`, replay manifest, snapshot 持久化與 `ToolResultRecorder`.
- `feat/mcp-dashboard` 改的是**產出期**: 新的 `mcp-data-dashboard` skill（`mcp()` 契約, 卡片狀態, 互動控制項）, `check_dashboard` 工具（JS 語法檢查 + `mcp()` 契約 lint）, connector 模式下 skill gate 改讀這份 skill, 以及一個 throwaway 的 `spike/mcp-shell` 端到端探針.

`check_dashboard` 的契約 lint 靠 replay manifest（`replay/landings.jsonl` + `replay/calls.jsonl`）得知「這個 session 實際打過哪些 (connector, tool, arg keys)」, 而 datasource branch 把 replay manifest 整個刪了. 所以兩邊不能機械合併: dry-run merge 三個檔案衝突（`chat_turn.py`, `connectors/wrapper.py`, `engine/replay_manifest.py` 一改一刪）, 更重要的是底下有五個語意衝突（第 3 節）. 本文的目的是把「merge 要做哪些決定」一次列清楚.

## 2. 兩條 branch 現況

| | `feat/mcp-datasource` | `feat/mcp-dashboard` |
|---|---|---|
| 分岔點 | `45cb60d` | 同左 |
| 之後 commit 數 | 31 | 9（含 9E 合併與 spike 整理; 真正的功能 commit 是 `daffb3e` + 兩個 fix） |
| 觸及檔案 | 55, 全在 `deepagent-service/` + specs/plans + 新 `.claude/skills/evidence-review` | 9 + spike 目錄 |
| 測試 | 35 個測試檔（重寫 wrapper/api_snapshot/chat_turn_connectors 測試, 新增 retry/config 測試, 刪 replay_manifest 測試） | 36 個測試檔（新增 `test_check_dashboard.py` 18 條, `test_middleware.py` +2, `test_graph.py` +1） |
| 對 `build_agent` 簽名 | 移除 `recorder` 參數 | 新增 keyword-only `dashboard_skill_root` |
| 對 `build_connector_tools` 簽名 | `workspace` 參數換成 `landing_dir: Path`, 預設額度 50 | 不變, 但 `_build_tool` 內多一個 `record_call` hook |

## 3. 語意衝突（不是 git 衝突）

**S1. 呼叫紀錄的來源沒了.** `check_dashboard` 讀 `load_calls` + `load_landings`; datasource 刪了 `replay_manifest.py` 與 `workspace.replay_dir`, 落表檔改寫進每輪的 `tempfile.TemporaryDirectory`, 輪末整個刪掉.

**S2. 「本輪」與「本 session」打架.** datasource 的表只活一輪, 而且 prompt 明講「純改版面時沿用 qN, 不要重打 connector」. mcp-dashboard skill 第 2 條鐵律要求每個 `mcp()` 呼叫「對應本 session 實際打過的呼叫」, `check_dashboard` 據此退件. 若呼叫紀錄跟表一起活一輪, 那麼「使用者第二輪只說『把兩張圖換位置』」這種最常見的修改輪, 模型照 prompt 不重打 connector, `check_dashboard` 就會對每個 `mcp()` 報「tool was never called in this session」, 兩份指令互相矛盾, 模型只能違反其中一條.

**S3. `r.data` 的形狀契約.** skill 說 `r.data` 是「與分析期呼叫同 tool 同參數所見的 payload byte-for-byte 相同」. 但在 datasource branch, 模型**從未看過 raw payload**——wrapper 先 `unwrap_envelope`（拆 FastMCP `{result: ...}`, dict 只取 `data` list）, 再落表, 回給模型的是表名 + 欄位 + 前 20 列預覽, **沒有告訴模型它拆了什麼**. 模型學到的形狀是「一個扁平列的陣列」, 但頁面在檢視時拿到的是 raw payload, 中間差了一層它不知道存在的拆封. spike 的三張快照正好記錄了模型在 `r.data` 與 `r.data.result` 之間來回猶豫. 這不是模型問題, 是 wrapper 做了一件事卻沒說, 而寫 JS 處理 raw 回傳值的正是模型.

**S4. qN 與 dashboard 的關係.** datasource 的 `CONNECTOR_MODE_SYSTEM_SECTION` 與 `CONNECTOR_TABLES_RESET_NOTE` 都把 qN 講成「dashboard 直接引用的東西」; mcp-dashboard skill 把 `__ERD_RESULTS__` 列為禁止 token, `check_dashboard` 看到就退件. 兩段 prompt 同時在場會把模型往兩個方向拉. （`chat_turn` 對 connector 模式仍會呼叫 `inject_results`, 但引用集合為空時只注入 `window.__ERD_RESULTS__ = {}` 與 proxy 腳本, 無害, 不需要改.）

**S5. 機械性衝突.** `graph.build_agent` 一邊拿掉 `recorder`, 一邊加 `dashboard_skill_root`; `chat_turn.prepare` 的 connector branch 兩邊各自重寫; `test_graph.py` 同一區塊兩邊都改. 解法固定, 不需要決策, 列出只為完整.

**S6. skill 文字過期.** `mcp-data-dashboard/SKILL.md` Workflow 第 1 步仍寫「landing results with `land_as`」.

## 4. 決策總覽

| ID | 主題 | 群 | 狀態 |
|---|---|---|---|
| D0 | merge 方式 | merge 與資料面（§5） | **定案 09-08**（A） |
| D5 | `r.data` 是 raw, 拆封配方明講給模型 | merge 與資料面（§5） | **定案 09-08** |
| D6 | qN 只供對話; 產物生命週期表 | merge 與資料面（§5） | **定案 09-08** |
| D7 | skill gate root 與 SKILL.md 改寫範圍 | merge 與資料面（§5） | **定案 09-08** |
| D8 | spike 留作驗收 | merge 與資料面（§5） | **定案 09-08** |
| D1–D4 | 呼叫紀錄 `connector_calls.jsonl`: 位置、內容、注入、比對 | check_dashboard（§6） | **改案 09-09**: 本次 merge 以 §6.1(i) 最小形態出貨（不驗 keys 與讀層）; (ii) 設計保留, 另開 PR |
| D10 | 檢視期 `mcp()` 錯誤如何回到模型 | check_dashboard（§6） | **定案 09-08**（延後; 設計留存） |
| D11 | 在 deepagent 內模擬瀏覽器執行（QuickJS／Chromium） | check_dashboard（§6） | **定案 09-08**（本次不做） |
| D9 | 宿主契約: iframe runtime ↔ 前端 ↔ Java ↔ deepagent ↔ MCP | 宿主契約（§7） | **定案 09-08**: 頁面面契約＝既有 skill 契約, merge 不改; 傳輸面為草案, 隨實作 plan 確認 |

## 5. merge 與資料面決策

### D0. merge 方式

**2026-09-08 使用者定案: 選項 A.**

**選項 A（定案）: 以 datasource 為底, dashboard 的四個功能 commit 重新落上.** 具體做法: 在 `feat/mcp-dashboard` 上 `git merge origin/feat/mcp-datasource`, 三個衝突檔一律取 datasource 側（`replay_manifest.py` 接受刪除）, 先讓 merge commit 落地（此時 `check.py` 會 import 失敗, 測試紅, 但 merge 本身乾淨可讀）, 接著以獨立 commit 依第 5–6 節的決策把 `check_dashboard`, skill gate root, SKILL.md 與 spike 重新接上. 理由: dashboard 側只有一個實質功能 commit 加兩個 fix, 而 datasource 側是 31 個 commit 且已經歷 opus 終審; 把小的搬到大的上面, review 面積最小, 且 merge commit 之後的每個 commit 都是「有意的設計變更」, 不是「解衝突順手改的」.

**選項 B: merge 時逐 hunk 手解.** 同一個 merge commit 裡同時做「接受刪除」和「補新紀錄機制」. 結果一樣, 但 review 時看不出哪些是解衝突, 哪些是新設計; 且 merge commit 不能被 revert 成單一步驟. 不建議.

**選項 C: 反向, 保留 replay manifest.** 把 datasource 合進來但把 `replay_manifest.py` 救回來給 `check_dashboard` 用. 這等於推翻 09-06「持久化失去用途, 整套拆掉」的決策, 而且 replay manifest 記的是 `land_as` 別名與 sha256, 大半欄位在自動落表下已無意義. 否決.

不論選哪個, branch 名維持 `feat/mcp-dashboard`（開發 branch 規範）, 不 rebase 不 force-push.

### D5. `r.data` 的形狀契約: raw 到頁面, 拆封配方明講給模型

**2026-09-08 使用者定案: `r.data` 是 raw, 拆封邏輯對模型明講.** 理由: 寫 JS 處理 raw MCP 回傳值的是模型, 所以模型必須知道「DuckDB 裡那張表是 raw payload 經過哪幾步才變成的」; 把拆封藏在宿主端只是把同一個知識缺口從 deepagent 搬到 Java 與前端, 還多一份要同步的程式碼.

| 選項 | 內容 | 評估 |
|---|---|---|
| (a) **定案** raw `structuredContent` + 拆封配方明講 | 宿主原樣轉發 MCP 回傳值, 不拆封, 不加 `meta`; wrapper 在回饋文字裡把「raw 長什麼樣, 我拆了哪幾層, 表是從哪個值落的」逐字告訴模型, 同一份配方（若有呼叫紀錄, D1–D4）寫進紀錄, `check_dashboard` 據此驗 handler 讀對層 | 宿主端零邏輯, Java／前端 bridge 只是轉發; 模型看到的與頁面拿到的形狀差異被明確描述, 而不是被抹平; 配方可驗證 |
| (b) 宿主套同一支 `unwrap_envelope`, 回 `{data, meta}` | 頁面拿到的就是落表的列 | 拆封邏輯要在 deepagent 與宿主端各一份（或宿主回頭打 deepagent）; 模型仍然不知道 raw 長什麼樣, 換 server 回傳形狀時兩邊要一起改; 否決 |

**拆封配方的表示法.** 現行 `unwrap_envelope` 是三條規則的遞迴（list 原樣; dict 有 `data` 取 `data`; dict 只有 `result` 拆開再套一次）, 所以任何一次拆封都能寫成一條 key 路徑加一組信封欄位:

| raw payload | `unwrap_path` | `envelope_keys` | 落表的值 |
|---|---|---|---|
| `[{...}, ...]` | `[]` | `[]` | 整個 list |
| `{"data": [...], "errorCode": ""}` | `["data"]` | `["errorCode"]` | `data` |
| `{"result": [...]}`（FastMCP 包 list） | `["result"]` | `[]` | `result` |
| `{"result": {"data": [...], "total": 9}}` | `["result", "data"]` | `["total"]` | `result.data` |
| `{"fab": "A", "yield": 0.97}`（非信封 dict） | `null` | `[]` | 整個 dict 落成一列 |

改動: `unwrap_envelope` 回傳值從 `(data, envelope_fields)` 改為含 `unwrap_path` 的三元組（或 `LandingResult` 多兩個欄位 `unwrap_path`, `envelope_fields`）; 邏輯不變, 只是把它走過的路記下來. **這一項不論 D1–D4 是否延後都納入本次 merge**——成本零, 而且是回饋文字（下）與未來 lint 共同的輸入.

**回饋文字多一段**（`_format_landing_feedback`, 英文, 給模型看）, 放在表名那行之後:

```
Raw response shape: object with keys [result]. The table was built from response.result
(an array of 412 objects); nothing else was dropped.
In the dashboard, mcp() hands your handler the raw response as r.data, so read the rows
with `r.data.result` -- not `r.data`.
```

非信封 dict 落成一列時改寫成「Raw response shape: object with keys [fab, yield]; landed as a single row. In the dashboard r.data is that object; read fields directly (r.data.fab)」. `unwrap_path` 為 `[]` 時寫「r.data is already the array」. 信封欄位有的話多一句「Other top-level fields (errorCode) were not landed; in the dashboard they are at r.data.errorCode」.

附帶決定:

- SKILL.md 的「byte-for-byte what you saw」保留語意但改成可操作的講法: 「`r.data` 是 raw response, **不是**你在 DuckDB 看到的表; 每次 connector 呼叫的回饋都有一段 `Raw response shape`, 照它寫的路徑取列. 沒看到那段就是你沒打過這個 tool, 先打」. 「Reading the response」一節的範例改成三種: `r.data`, `r.data.result`, `r.data.data`.
- 宿主端（前端 bridge, Java 代理, deepagent 無模型 tool-call 端點）的契約: `{data: <structuredContent 原樣>}` 或 `{error: {message}}`（`code` 欄位是 D9 傳輸面的提案, 隨其 plan 定）, 沒有 `meta`. 四層各自的責任與訊息形狀見 D9.
- spike 的 `bridge.py` 拿掉 `UNWRAP_RESULT` 旋鈕, 固定 raw（它現在的預設就是 raw, 只是把選項拿掉讓它不再像個未定案）.

### D6. qN 在 connector 模式的角色, 以及兩段 prompt 的措辭

**2026-09-08 使用者定案**: 在 connector 模式, qN 是**對話回答用的**（模型算出來的數字、洞察句子）, dashboard 一律走 `mcp()` 現抓. 據此改 datasource 側的兩段文字:

- `CONNECTOR_MODE_SYSTEM_SECTION`: 「Landed tables live only for the current turn, but the qN results produced by run_sql persist across turns: when merely changing the dashboard's layout ... reuse the existing qN」→ 改為「Landed tables live only for the current turn. The dashboard never embeds data: it fetches live through `mcp()` at view time (see the mcp-data-dashboard skill), so a layout-only change needs no new connector call — `check_dashboard` validates against the calls already recorded in this session. Call a connector tool again only when you need to see a new tool or a new argument shape.」
- `CONNECTOR_TABLES_RESET_NOTE`: 拿掉「可直接在 dashboard 中引用」, 改為「先前輪次的 connector 呼叫紀錄仍在, 純改版面不必重打」.
- `chat_turn` 對 connector 模式的 `inject_results` **不動**（空集合注入無害, 見 S4）; 若日後要省那段 proxy 腳本再說.

這條與 datasource 09-06「qN 跨輪保留, 純改版面沿用 qN」的決策**不衝突**: 那條決策的目的是阻止模型改版面時重打六次 API, 在 mcp-dashboard 下達成同一目的的機制換成「呼叫紀錄跨輪 + `check_dashboard` 讀紀錄」, prompt 只是改講法.

**產物生命週期表.** merge 後 connector 模式一輪裡會產生的每一樣東西, 落在哪, 活多久, 誰用:

| 產物 | 落在哪 | 生命週期 | 寫入者 | 讀取者 | 內容含使用者資料? |
|---|---|---|---|---|---|
| connector 回應的 JSON 檔（拆封後） | 每輪暫存目錄（`tempfile.TemporaryDirectory`, `allowed_directories` 唯一允許的路徑） | **本輪**, `ChatTurn.__aexit__` 刪 | wrapper `land_response` | DuckDB `read_json_auto` 掛表 | 是 |
| connector 回應的 raw JSON 檔（`{table}.raw.json`, 只在 D11 選做時存在） | 同上 | **本輪** | wrapper | D11 的 `mcp()` stub | 是 |
| DuckDB 表 `{connector}_{tool}_{hash}` | 本輪 DuckDB 連線（記憶體 + 上列 JSON 檔） | **本輪**, 連線關閉即消失 | wrapper `mount_json_file` | 模型的 `get_schema`/`run_sql`/`preview_data` | 是 |
| tool 回饋文字（表名, 欄位, 預覽, `Raw response shape`） | LangGraph checkpoint 的訊息歷史 | session（隨對話歷史保留與壓縮） | wrapper `_format_landing_feedback` | 模型（寫 SQL 與 dashboard 時抄形狀與路徑） | 預覽含前 20 列 |
| `queries/qN.sql`, `results/qN.json` | workspace zip | **跨輪**, 隨 session 保留期 | `run_sql` tool | 模型（對話回答引用數字）; file 模式下 `inject_results` 注入 dashboard; **connector 模式下 dashboard 不用** | 是（聚合結果） |
| `connector_calls.jsonl` | workspace zip 頂層 | **跨輪**, 隨 session 保留期 | wrapper（經 `ConnectorCallLog.append`, 成功與 0 列各一筆） | `check_dashboard`（arg keys 與 `unwrap_path` 比對）; 未來 Java per-artifact tool 清單的材料 | **否**（只有 connector, tool, arg keys 與值, 欄位名, 列數, 拆封路徑） |
| `dashboard.html` | workspace zip → Java artifact 儲存 | 跨輪; artifact 依保留政策 2 年 | 模型 `write_file`/`edit_file` | `check_dashboard`（本輪）; Java 出貨; 前端 srcdoc; viewer 瀏覽器 | **否**（connector 模式不注入資料; 引用集合為空時 `inject_results` 只注入 `{}`） |
| viewer 開頁時的 `mcp()` 回應 | viewer 瀏覽器記憶體 | 該頁面存活期間 | D9 四個 hop | 頁面 handler | 是（viewer 自己權限內的即時資料） |

三個一眼要看出來的事: 對話期的資料（前三列）只活本輪, workspace 裡沒有任何原始資料列; 跨輪保留的只有 qN 結果與呼叫 metadata; connector 模式的 dashboard 不消費 qN, 檢視時的資料完全來自 viewer 自己的呼叫.

### D7. skill gate 與 SKILL.md

**2026-09-08 使用者定案.**

- `build_agent(..., dashboard_skill_root=...)` 與 `DashboardSkillGateMiddleware(skill_relative_root=...)` **原樣保留**, 疊在 datasource 拿掉 `recorder` 之後的簽名上.
- SKILL.md 改動清單: Workflow 第 1 步去 `land_as`, 改成「每次 connector 呼叫自動落表, 回饋給你表名、欄位、預覽, 以及一段 `Raw response shape` 說明 raw 回傳值與表的關係; dashboard 要用的 (connector, tool, args, 讀列路徑) 全部抄自這段回饋」; 鐵律第 2 條的「this session」明確定義為「本對話任何一輪你實際打過的呼叫, 以 `check_dashboard` 的紀錄為準; 不含只在 skill 檔看過但沒打過的 tool」; `r.data` 形狀依 D5 改; `mcp()` 簽名、handler 一次、`{data}`／`{error:{message}}`、禁止 API 等既有契約**原樣不動**（D9 的 `r.error.code` 等提案不進 merge）; 其餘（卡片狀態, 控制項, 佈局, ECharts 規則）不動.
- gate 的必讀清單仍是整個 `.skills/builtin/mcp-data-dashboard` 下所有 `.md`（目前只有 SKILL.md 一份, 1222 行; 是否拆 references 不在本 spec）.

### D8. spike 去留

**2026-09-08 使用者定案.** 保留在 `deepagent-service/spike/mcp-shell/`, 維持 THROWAWAY 標記. merge 後拿掉 bridge 的 `UNWRAP_RESULT` 旋鈕（D5 的必要對齊; D9 末段列的其他落差是對草案契約的, 建議順手做但不是 merge 前置）, 然後**手動再跑一次**作為 merge 驗收（README「實際跑法」一節的指令）, 把新一組快照放進 `out/`, 舊三張刪掉. 驗收重點就是 S3 那個坑: 模型收到 `Raw response shape` 之後, 第一版 `dashboard.html` 就該讀 `r.data.result`, 不再來回改. 不寫自動化測試（spike 需要 OpenRouter 與真模型）.

## 6. check_dashboard 決策群

### 6.0 現況與原則

**現況.** `check_dashboard`（`app/agent/tools/check.py`, 本 branch `daffb3e`）是 connector 模式專用的 agent tool, 09-04 spec 的 level 1 + level 2: 每段 inline `<script>` 過 `node --check`（image 內無 node 時報「unavailable」並繼續）, 再以 regex 與括號掃描器對 `mcp()` 呼叫做契約 lint. 它是 PR #40（2026-08-09, 檢查層退場實驗）之後**唯一**重新加回 deepagent 的生成期確定性檢查, 而且只在 connector 模式註冊. 那次退場的結論是「不在生成當下驗證退件, 讓錯誤在瀏覽器大聲炸出來, 由使用者觸發的 `/repair` 收尾」; 本群每一條決策都要回答「為什麼 connector 模式值得例外」.

**原則.** (1) `check_dashboard` NEVER 產生模型無法用任何行動消除的 finding——那是修復迴圈, 會燒光 recursion limit; 誤放行的代價是一張空白卡片, 有 `/repair` 兜底, 誤退件的代價沒有兜底. 所以: 輸入可信時嚴格, 輸入不可信時讓路（與 skill gate 找不到 skill 資料夾 fail-open、08-03 guard 非阻擋開關同一姿態）. (2) 檢查靠的是 session 已知的事實（connector 清單, 呼叫過的 tool 與參數, 拆封路徑）, 不是猜; 沒有事實來源的檢查不做. (3) connector 模式的特殊性在於**頁面的正確錯誤處理本身就是安靜的**——skill 要求 handler 檢查 `r.error` 並畫錯誤卡, 這對 viewer 是對的, 但讓「打錯 tool 名／參數」成為最安靜的失敗: 不 throw, `head-inject.vm` 的 onerror 捕捉腳本不會動, 修復卡永遠不出現. file 模式用物件列 Proxy 把「綁錯欄」變成 throw 解決了同類問題; connector 模式還沒有對應物. 這是 D10 與 D11 的由來.

**分層一覽**（哪些檢查靠什麼事實, 在哪個決策裡）:

| 檢查 | 事實來源 | 決策 | 本次 merge |
|---|---|---|---|
| JS 語法（`node --check`） | 無 | 既有 | 納入（node 可選） |
| 禁止 token（`fetch(`, `window.parent`, 自定義 `mcp`, `__ERD_RESULTS__`…） | skill 規則 | 既有 | 納入 |
| `mcp()` 三個引數是字面值／物件字面值 | skill 規則 | 既有 | 納入 |
| connector id 在 session 內; tool 屬於該 connector | `ChatRequest.connectors` → live `Connector` 物件 | 既有 | 納入 |
| CDN 白名單; `echarts.init(el,'erd')`; 至少一個 `mcp()`; handler 有 `.error` | skill 規則 | 既有 | 納入 |
| arg keys 等於某次真實呼叫的 keys | 呼叫紀錄 | **D1–D4** | 延後（09-09; 另開 PR） |
| handler 讀 `r.data` 的第一層對上 `unwrap_path` | 呼叫紀錄 | **D1–D4**（D4b） | 延後（09-09; 另開 PR） |
| 檢視期 `mcp()` 回 `{error}` 或 handler throw → 回到模型 | 瀏覽器 + 宿主 | **D10** | 延後（設計留存, 隨 D9） |
| 在 deepagent 內執行頁面 JS 抓 throw／`{error}` | raw 落表 + JS runtime | **D11** | 不做 |

### 6.1 D1–D4. 呼叫紀錄 `connector_calls.jsonl`——是否納入本次 merge, 以及納入時長什麼樣

四條原本各自獨立（位置、內容、注入、比對）, 但只有「納入」時才全部成立, 所以收成一組. **2026-09-09 使用者改案: 本次 merge 以 (i) 出貨——`check_dashboard` 是「最小可用」的工具（語法、禁止 token、connector 與 tool 存在、CDN、theme）, 不驗 arg keys 與讀層; (ii) 的設計原樣保留, 另開 PR 實作.** 理由: 第一輪開發要先看模型能不能產出帶 `mcp()` 的 dashboard, 錯誤由人在 spike 頁面（`window.onerror` 與錯誤卡）看到後貼回對話即可, 事前 lint 沒抓到的錯不會沒人看見; 而 (ii) 的紀錄機制、兩條 lint 與退路設計加起來是最大的一塊工作, 放在前面會拖慢那個觀察. （09-08 原定案為納入本次 merge, 依 (ii) 實作; 改案理由見第 13 節.）

**(i) `call_log=None` 時的行為（保留, 供測試／spike 與降級模式共用）.** `build_check_tools(workspace, connectors, call_log=None)`: `call_log` 為 `None` 時跳過 arg keys 與 unwrap path 兩條檢查, 報告末尾加一行「call-record checks not enabled」; 其餘（§6.0 表格前五列）照跑. D5 的 `Raw response shape` 回饋與 `unwrap_path` 欄位**仍納入**, 模型還是從對話裡學到 keys 與路徑, 只是沒人驗. 這個形態也是 D2 附「降級模式」的輸出形狀, 兩者共用同一段報告文字.

**(ii) 定案的設計.**

*D1 位置: workspace 頂層 `connector_calls.jsonl`, 跨輪保留.* 三個選項: (a) 跟落表一起放每輪暫存, 輪末刪——直接撞 S2, 修改輪必退件, 否決; (b) **建議** 放 workspace zip 頂層, append-only, 隨 session 保留期; 只記 metadata 不記資料列, 與 09-06「原始資料不進 workspace」不衝突; (c) 不記, 只驗 tool 存在——即 (i). 不復活 `replay/` 目錄名. 不去重（同參數重打就多一行, 讀端 group 後不影響結果）; 每行 < 1 KB, 每輪額度 50, 不需輪替.

*D2 內容.* 每筆一行 JSON:

```json
{"connector_id": "sales", "tool_name": "list_orders", "args": {"days": 30},
 "unwrap_path": ["result"], "envelope_keys": [],
 "columns": ["order_id", "region", "amount"], "row_count": 412, "landed": true}
```

`args` 是剝除 None 之後、實際送給 connector 的那份（與回饋文字印出的相同, 與表名 hash 用的相同）; `unwrap_path`/`envelope_keys` 是 D5 的配方; `columns`/`row_count` 來自 `LandingResult`, 現在不用, 記下來讓 level 2.5（驗 handler 內 `row.xxx` 欄位名）不必再改寫入端. **0 列也記**（`landed: false`, `columns: []`）: 呼叫本身成功, 模型可用同組 keys 配不同值. `ConnectorToolError`、傳輸失敗、額度用盡**不記**: 模型沒看過回應形狀.

*D3 注入: `ConnectorCallLog` 物件.* (a) `build_connector_tools` 加回 `workspace` 參數——讓 wrapper 重新依賴 workspace 整個物件, 09-06 才剛拿掉; (b) **建議** engine 層 `app/engine/connector_call_log.py`（stdlib only）的小物件, 建構時給路徑, `append(record)` 與 `load() -> list[dict]`; `build_connector_tools(..., call_log: ConnectorCallLog | None = None)`; `check_dashboard` 拿同一個物件讀; (c) `chat_turn` 從 `astream_events` 的 `on_tool_end` 記——事件非同步消費, 同一則 AI message 先打 connector 再 `check_dashboard` 時紀錄可能還沒寫, 且要反解析回饋文字, 否決.

*D2 附: 寫入失敗的退路.* `append` 失敗（磁碟滿, 權限）只 `logger.warning`, 不影響 tool 回傳（never-raise）. 但若 `check_dashboard` 因此讀到空紀錄而報「never called」, 模型重打、再失敗、再報——修復迴圈. 兩層退路: **本輪記憶體鏡像**——`ConnectorCallLog` 同時把本輪紀錄留在 list, `load()` 回磁碟 + 記憶體去重, 磁碟寫失敗只失去跨輪, 本輪的呼叫仍看得到（最常見情境是同輪呼叫＋寫 dashboard）; **降級模式**——物件記錄本輪是否有任何 append/load 例外, 有就讓 `check_dashboard` 跳過兩條紀錄檢查並在報告開頭加一行「call record unavailable; arg-key and response-layer checks skipped」, 其餘檢查照跑. 模型看到的是原因不是 finding. **不需要退路的情況**: 前幾輪的紀錄因那輪 workspace 上傳失敗而缺——本輪紀錄健康, 「never called」是真的, 模型重打一次即記錄、finding 即消, 一次額外 API 呼叫, 不是迴圈, 維持 finding.

*D4 比對.* 讀 `load()` 全部, 跨輪; 比對 **arg keys 集合**, 不比值不比型別（值來自 viewer 控制項, 型別是 level 3 的事）; 找不到紀錄的 (connector, tool) → 「tool was never called in this session — call it first」, 「session」在 SKILL.md 依 D7 定義. 讀法: 載入所有行, 以 (connector, tool) 分組成 arg-key frozenset 的 list; 對每個 `mcp(` 呼叫, 括號掃描器取出三個引數, 前兩個字面值查 live connector, 第三個物件字面值抽 key 集合, 與該組任一 frozenset 相等即通過.

*D4b unwrap-path lint（依賴 D2 的 `unwrap_path`）.* 對每個 `mcp()` 呼叫取該 (connector, tool) 紀錄的 `unwrap_path`（多筆不同——理論上不會, server 同一 tool 形狀固定——取最後一筆並 warning）; 從第四個引數的 arrow function 取 handler 參數名（不假設叫 `r`）, 掃 handler 本體對 `<param>.data` 的**第一層**存取: 路徑 `["result"]` 而 handler 寫 `r.data.map(`/`r.data.length`/`r.data[` → 退件「rows are at r.data.result (the analysis-time landing unwrapped that key)」; 路徑 `[]` 而 handler 寫 `r.data.result` 或 `r.data.data` → 退件「r.data is already the array」. regex 級, 與 forbidden token 同等級, 不建 JS parser. **不做**: 用 `columns` 驗 handler 欄位名（下一個 spec）.

### 6.2 D10. 檢視期 `mcp()` 錯誤如何回到模型

**2026-09-08 使用者定案: 延後.** 設計留存如下, 隨 D9 實作一併開 plan; 本次 merge 不改 `/repair` 鏈路. 過渡期狀態見本節末段, 是刻意接受的缺口.

**問題.** 以現有鏈路（`head-inject.vm:1` 捕捉 onerror → `ArtifactPanel.tsx:65` → `CoworkPage.tsx:135` 修復卡 → `POST /api/artifacts/{id}/repair` → deepagent `/repair` 單次模型呼叫, prompt 在 `prompts.py:173/186`）, connector 模式下各類錯誤能否回到模型:

| 錯誤 | 浮現處 | 回到模型? | 途徑 |
|---|---|---|---|
| 分析期 connector tool 失敗 | 對話輪 | 是, 立即 | wrapper 回饋文字 |
| HTML 寫了不存在的 connector／tool | `check_dashboard` 同輪 | 是 | live connector 物件 |
| arg keys 寫錯 | `check_dashboard` 同輪 | 是（D1–D4） | 呼叫紀錄的 keys 比對 |
| arg **值**不對, server 拒絕 | viewer 瀏覽器 | **否**, 見下 | 事前檢查不比值 |
| 讀錯層且 handler throw（`r.data.map is not a function`） | viewer 瀏覽器 | 是, 但晚且手動 | onerror → 修復卡 → `/repair` |
| 讀錯層但 handler 防禦性寫法 | viewer 瀏覽器 | **否** | 空圖, 無 throw |
| `mcp()` 回 `{error}`（server 拒參數, connector 不允許, 逾時, tool error） | viewer 瀏覽器 | **否** | handler 依 skill 畫錯誤卡, 無 throw, onerror 不動 |

兩個放大因素: 現有 `/repair` 是 file 模式的 prompt（說沒有外部資料, 講 `__ERD_RESULTS__`, 不知道 `mcp()`、connector 清單、raw 形狀）, 修 `r.data.map is not a function` 時不知道列在 `result` 底下; 修復卡只在編輯者自己的 session 出現, 分享頁 viewer 沒有修復路徑.

**建議（兩件事, 都小, 且維持 PR #40「讓錯誤大聲」的姿態而非「生成期攔截」）:**

1. **runtime prelude 把 `mcp()` 的 `{error}` 結果也發到既有通道.** D9 ① 的前端注入 prelude 在結果帶 `error` 時, 同時 `parent.postMessage({type:'erd-artifact-error', errors:[{message: "<connector>.<tool>(<arg keys>) → <code>: <message>"}]})`. 修復卡因此對「被拒絕的呼叫」與「throw」一視同仁, 模型拿到的是 server 自己寫的可行動訊息（howto 規矩 4）. 訊息裡放 arg **keys** 不放值（值可能含業務資料, 且修復用不到）. 這在前端 prelude, 隨 D9 實作出貨, 不在 merge PR.
2. **connector 模式的 repair prompt.** `REPAIR_SYSTEM_PROMPT` 的 connector 變體: 帶 connector 清單、`mcp()` 契約摘要、以及「r.data 是 raw structuredContent; FastMCP 把 list 包成 `{result: [...]}`」這句; `run_repair` 依 request 是否帶 connectors 選用. 這是 deepagent 側, **納入 merge PR**（`repair_flow.py`, `prompts.py`, `RepairRequest` schema 補 `connectors`, Java `AnalysisBrowserRepairClient` 補帶——後者小改, 但仍是 Java 改動, 需列進 §9）.

**誠實的過渡期狀態（09-09 更新）.** merge 之後、D1–D4 (ii) 與 D10 實作之前: 產品前端還沒有 `mcp()`（D9 傳輸面未實作）, 唯一能開頁的宿主是 spike 的 `shell.html`, 它把 `window.onerror` 印在頁面 log 區, `mcp()` 回 `{error}` 時 handler 依 skill 畫錯誤卡. 這段時間**沒有任何錯誤會自動回到模型**: 「keys 寫錯」「讀錯層」「值不對被 server 拒絕」「connector 不允許」「逾時」全部在瀏覽器裡才浮現, 由人把文字貼回對話. `check_dashboard` 只擋語法、禁止 token、connector 與 tool 不存在、CDN、theme. 這是刻意接受的: 第一輪開發的觀察對象是模型的產出, 人在迴圈裡. 寫在這裡免得日後被當 bug.

**與 D1–D4 的關係.** D10 是**事後**防線（錯誤已到 viewer 面前）, D1–D4 是**事前**防線（寫檔當下擋 keys 與層）. 兩者互補不互斥; D1–D4 (ii) 落地後, D10 補的是事前檢查原則上看不到的那一類（值、權限、可用性）. 兩者目前都延後, 先後順序由 spike 人工測試的觀察決定: 若模型最常犯的是 keys 與讀層, 先做 D1–D4 (ii); 若是值與權限, 先做 D9+D10.

### 6.3 D11. 在 deepagent 內模擬瀏覽器執行

**問題.** 能否不等 viewer 開頁, 在 deepagent 內執行頁面 JS, 直接拿到 D10 表格裡「否」的那兩列?

**共同前提.** 每次呼叫的 raw payload 要活過本輪: wrapper 手上有 `response`, 多寫一個 `{table_name}.raw.json` 到每輪暫存目錄（與落表檔同生命週期, 輪末刪; 已列入 D6 生命週期表）. stub `mcp()` 的比對規則與 D4 相同: 同 connector 同 tool 同 arg keys, 參數 hash 完全相同時取那一筆.

| 層級 | 做法 | 抓得到 | 抓不到 | 成本 | 先例 |
|---|---|---|---|---|---|
| A. QuickJS in-process | 沿用被移除的 `html_guard/sandbox/`（`git show 32a080c^`）: absorb-all 的 `window`/`document`/`echarts` proxy, 同步觸發 `DOMContentLoaded`, 收集 `console.error`; 加一個 `mcp` stub: 查 raw 落表, 呼叫 handler 一次, 記 throw 與 `{error}` | handler 內 TypeError（S3 錯層）, 未宣告變數, keys 對不上任何落表（stub 自己報）, handler 吞掉的 `{error}` | layout, ECharts option 合法性, 真 timer, 控制項互動; null deref 只靠 known-id 啟發式 | 毫秒; quickjs 仍在 pyproject; 無 image 變更 | PR #40 於 08-10 移除, 理由見下 |
| B. node + jsdom | — | 比 A 多不了多少 | 無 canvas, ECharts 掛不起來 | node 不在 image | 09-04 已否決 |
| C. headless Chromium | 09-04 spec 的 level 3: Playwright 注入同一個 stub, 收 pageerror／console.error, 斷言無 loading／error slot, 對每個 `<select>` dispatch change | 全部, 含 viewer 真正看得到的那類 | — | ~300 MB 進 python-slim image, 每次 1–3 s; 快照比對政策是產品決策 | 09-04 deferred |

**兩層都有的極限.** 落表只活本輪. 純改版面的輪次沒有 raw 可餵, handler 跑不起來, 只剩靜態檢查——S2 再現; 除非 raw 跨輪持久化, 而那正是 datasource 刻意拆掉的.

**為什麼上一次拆掉（PR #40, 2026-08-09, opus Ready to merge）, 以及為什麼 Chromium 從未做.** 08-03 spec 量過: guard 本身 35 ms, 貴的是 finding 觸發的最多 5 輪整份重寫（每輪約 18K tokens, 分鐘級）; sandbox 為了逼近瀏覽器不斷疊啟發式（absorb proxy 分不出缺元素、chart 的 try/catch 吞掉它想抓的錯、setTimeout 重拋要特別處理）; 物件列 Proxy 用零成本在真瀏覽器涵蓋了最有價值的那類（綁錯欄）; 同 branch 的行號 A/B 實測「無感」. 結論寫進 architecture.md:「不在生成當下驗證退件, 讓錯誤不可能安靜, 瀏覽器修復兜底」, 並明列取捨「無錯誤形態的缺陷無防線, 首次出貨可能帶錯」. Chromium 則卡在 image 體積、每次秒級、快照比對政策、以及「先看 on-prem 模型過不過得了 level 1+2」的先後順序. 四週後在同一個服務再加一個更重的 JS runtime, 需要先證明有缺口.

**2026-09-08 使用者定案: 本次不做, 以 D10 補洞.** D10 第 1 點用 PR #40 同一種手法（讓安靜的錯誤變大聲）補上 connector 模式的對應物, 成本是 prelude 幾行; D11 A 層則是把生成期執行檢查請回來, 姿態相反. 若 D8 的 spike 重跑與上線後的 Langfuse 顯示「錯層／錯 keys」仍是主要失敗形態且 `/repair` 修不好, 再回頭選 A（材料: raw 落表已在 D6 表列, stub 比對規則 = D4）. 選 A 時它可在**本輪**內取代 D1–D4 的兩條檢查（行為驗證而非靜態比對）, 但取代不了跨輪.

### 6.4 設計影響總表

| 決策 → 影響 | wrapper | prompts | SKILL.md | check.py | chat_turn | 前端 | Java | spike |
|---|---|---|---|---|---|---|---|---|
| D5 raw + 配方 | `LandingResult.unwrap_path`; 回饋多一段 | — | `r.data` 段重寫 | — | — | — | — | 拿掉 `UNWRAP_RESULT` |
| D6 qN 角色 | — | 兩段措辭 | — | — | `inject_results` 不動 | — | — | — |
| D7 gate | — | — | Workflow/鐵律 2 | — | `dashboard_skill_root` | — | — | — |
| D1–D4（本次 (i); (ii) 另開 PR） | `call_log` 參數; 兩處 append | 「validates against recorded calls」 | 「session」= 紀錄 | `ConnectorCallLog.load()`; keys + path 兩條 lint; `call_log=None`／降級模式跳過兩條 + 一行說明 | 建 `ConnectorCallLog`, 傳兩處 | — | — | — |
| D10（延後） | — | connector 版 `REPAIR_SYSTEM_PROMPT` | 「錯誤卡會回報」 | — | `run_repair` 選 prompt; `RepairRequest.connectors` | prelude 發 `erd-artifact-error`（隨 D9） | `AnalysisBrowserRepairClient` 帶 connectors | — |
| D11（不做） | （多寫 `.raw.json`） | — | — | （執行 pass + `mcp` stub） | — | — | — | — |
| D9（傳輸面草案, 隨 plan） | — | — | （`r.error.code` 隨 plan） | — | — | prelude + bridge | `/mcp-call` 端點 | bridge 固定 raw（merge）; 其餘對齊（plan） |

## 7. D9. 宿主契約: iframe runtime ↔ 前端 ↔ Java ↔ deepagent ↔ MCP server

**2026-09-08 使用者定案（狀態更正）.** 這一節分兩層, merge 只依賴第一層:

- **頁面面契約（page-facing surface）＝既有 skill 契約, 已實作, merge 不改.** 頁面能觀察到的只有: 宿主提供的全域 `mcp(connectorName, toolName, toolArgs, handler)` 回傳 `undefined`; handler 恰好一次、恰好一個引數; 成功 `{data}`、失敗 `{error:{message}}`, 以 `r.error` 判斷; 頁面自己遵守的禁止事項（不 fetch、不碰 `window.parent`、不定義 `mcp`、connector 與 tool 是字面值）. 這些在 `skills/mcp-data-dashboard/SKILL.md` 與 `spike/mcp-shell/shell.html` 的 prelude 裡已經存在且跑過, datasource branch 一行都沒碰. merge 唯一改的是 D5: `data` 是 raw（spike 預設本來就是）, 且模型現在會被告知拆封路徑. SKILL.md、回饋文字與 `check_dashboard` 只依賴這一層.
- **傳輸面契約（transport）＝草案, 隨 D9 實作 plan 確認.** 下面從「四個 hop 與各自唯一的責任」起的全部內容（postMessage 訊息名、`event.source` 驗證、Java 端點、deepagent `/tool-call`、SSO header、逾時、錯誤碼集合、不變量）是為將來的 Java／前端／deepagent 實作先寫好的設計, 頁面看不到, merge 不依賴. 保留在本 spec 是為了讓 datasource spec §11 說的「另開 spec」有落點; 拍板時機是寫該 plan 時.
- **對頁面面的四項提案（進 plan, 不進 merge）**: (1) `r.error` 多一個 `code` 欄位與固定詞彙（或退一步只給 `retryable` 布林）——建議保留 `code` 在線上, 但只教模型兩種行為: 一律顯示 `message`; `CONNECTOR_UNREACHABLE`／`TIMEOUT` 才給重試按鈕; (2) 明講 handler 永遠非同步呼叫, 不在 `mcp()` 回傳前執行; (3) 明講 runtime 不吞 handler 例外, 讓它到 `window.onerror`（`head-inject.vm` 與 D10 都靠這點）; (4) 明講 `args` 必須 JSON 可序列化, `undefined` 會被丟掉、`NaN` 變 `null`. 四項都是對既有契約的加強, 不是 merge 的前提.

以下為傳輸面草案原文（D5 定了「`r.data` 是 raw」, 這裡把 raw 從 MCP server 一路送到頁面的每一個 hop 寫成契約; Java 與前端的實作另開 plan）.

四個 hop 與各自唯一的責任:

| hop | 誰 | 做什麼 | **不做**什麼 |
|---|---|---|---|
| ① iframe 內 `mcp()` runtime | 前端注入的一段固定 JS | 把呼叫編號, postMessage 給宿主頁, 收到結果找回 handler 呼叫一次; 結果帶 `error` 時另發 `erd-artifact-error`（D10） | 不碰 `data`, 不重試, 不快取 |
| ② 宿主頁 bridge | 前端 `ArtifactFrame` 的父層 | 驗訊息來源是自己的 iframe, 打 Java 代理端點, 把結果原樣 post 回去 | 不看 `data` 內容, 不改形狀 |
| ③ Java 代理端點 | `ArtifactController` | 驗 artifact 存取權, 由 artifact → session → `selectedConnectors` → catalog 查出 connector 位址, 帶 viewer 的 SSO 轉發給 deepagent | 不解析 `data`, 不落 DB, 不快取 |
| ④ deepagent tool-call 端點 | `main.py` 新端點, **不經過模型** | 用 `mcp_adapter` 同一支 `_call`（同逾時, 同重試）打 MCP, 回 `structured_content` 原樣 | **不 `unwrap_envelope`**, 不落表, 不開 DuckDB |

```mermaid
sequenceDiagram
    participant P as dashboard.html (iframe, opaque origin)
    participant R as mcp() runtime (① 前端注入)
    participant H as 宿主頁 bridge (②)
    participant J as Java /api/artifacts/{id}/mcp-call (③)
    participant D as deepagent POST /tool-call (④)
    participant M as MCP server

    P->>R: mcp('sales','list_orders',{days:30}, handler)
    R->>H: postMessage {type:'erd-mcp-call', id, connector, tool, args}
    H->>H: event.source === iframe.contentWindow ?
    H->>J: POST body {connector, tool, args} (axios, 帶 X-User-Id / SSO)
    J->>J: artifact 存取權; connector ∈ session.selectedConnectors; catalog → url
    J->>D: POST {connector:{id,name,url,bearerTokenKey?}, tool, args} + X-SSO-* + bearer
    D->>M: tools/call (帶 SSO header, 逾時/重試同對話期)
    M-->>D: structuredContent
    D-->>J: {data: <原樣>} 或 {error:{code,message}}
    J-->>H: 同上, HTTP 200
    H->>R: postMessage {type:'erd-mcp-result', id, result}
    R->>P: handler(result)  (恰好一次)
```

**① iframe runtime.**

- 簽名固定: `mcp(connectorName: string, toolName: string, toolArgs: object, handler: (r) => void): void`. 回傳 `undefined`; 不是 Promise.
- `handler` 恰好呼叫一次, 引數恰好一個: 成功 `{data: <raw structuredContent>}`; 失敗 `{error: {code: string, message: string}}`, 此時沒有 `data`. 頁面判斷成功與否只看 `r.error`（skill 現有寫法）.
- 注入點: **前端**在 `ArtifactFrame` 組 srcdoc 時, 緊接 CSP `<meta>` 之後、頁面任何 `<script>` 之前插入（與 spike `composeSrcdoc` 相同位置）. 不由 Java 出貨前寫進儲存的 HTML: 儲存的 artifact 維持模型產出的原樣, runtime 有 bug 修前端一次, 所有已發布頁面下次開啟就吃到, 不必重生 dashboard（09-02 options 文件 C 案的維護論點, 在這裡用得上）. Java 既有的 `head-inject.vm` onerror 捕捉腳本維持原位; 兩段腳本同一個 iframe 內並存, prelude 沿用它的 `erd-artifact-error` 訊息型別（D10）.
- 只在 artifact 所屬 session 是 connector 模式時注入（Java 在 `GET /api/artifacts/{id}` 的 response 或 artifact DTO 帶 `dataMode: "file" | "connector"`; 前端據此決定）. file 模式的頁面不會有 `mcp` 這個全域, 與現況相同.
- `check_dashboard` 已禁止頁面自己定義 `mcp`, 所以 runtime 與頁面不會撞名.

**② 宿主頁 bridge（postMessage 協定）.**

| 方向 | 訊息 | 欄位 |
|---|---|---|
| iframe → 宿主 | `erd-mcp-call` | `id: string`（頁面內唯一, runtime 自增）, `connector: string`, `tool: string`, `args: object` |
| 宿主 → iframe | `erd-mcp-result` | `id: string`, `result: {data} \| {error:{code,message}}` |
| iframe → 宿主 | `erd-artifact-error` | 既有（`head-inject.vm`）; D10 讓 prelude 也用它回報 `{error}` 結果 |

- iframe 是 `sandbox="allow-scripts"` 的 opaque origin, `event.origin` 是 `"null"`, 所以宿主**以 `event.source === iframeRef.current.contentWindow` 驗來源**, 不驗 origin; 回傳用 `contentWindow.postMessage(message, "*")`（對 opaque origin 只能 `"*"`）. 兩邊都忽略 `type` 不認得的訊息.
- 宿主頁對同一個 iframe 的 in-flight 上限與逾時（建議 6 與 60 秒）由前端定, 超過上限先排隊; 逾時回 `{error:{code:"TIMEOUT"}}`. 這兩個數字不進契約, 進前端設定.
- 宿主頁不解讀 `args`, 原樣序列化. 型別轉換是頁面的責任（skill 的 `Number(...)` 規則）.

**③ Java 代理端點.**

- `POST /api/artifacts/{id}/mcp-call`, body `{connector: string, tool: string, args: object}`, 回 `200` 與 `{data}` 或 `{error:{code,message}}`. **tool 層級的失敗一律 200 + `error`**, 讓頁面逐卡降級; 只有 artifact 不存在／無權（`404`, 與 `GET /api/artifacts/{id}` 同一條 ownership 規則）與 body 驗證失敗（`400`）走 HTTP 錯誤——這兩種前端 bridge 也轉成 `{error:{code:"HTTP_<status>"}}` 回給頁面, 頁面永遠只看到一種形狀.
- 允許範圍: `connector` 必須在 artifact 所屬 session 的 `selectedConnectors` 內, 否則 `CONNECTOR_NOT_ALLOWED`; catalog 查不到（已下架）→ `CONNECTOR_UNAVAILABLE`. **tool 層級不在 Java 白名單**（v1）: catalog 收錄的 tool 依 howto 規矩 3 全部唯讀且無副作用, 資料權限由下游 API 憑 SSO 決定; 要做 per-artifact tool 清單時, 材料就是 D2 的 `connector_calls.jsonl`（deepagent 在 `DASHBOARD_HTML` 事件旁多帶一份 `allowedCalls`, Java 存進 Artifact）——留作擴充點, 本 spec 不做.
- 身分: viewer 的 SSO 從 `CoworkContextHolder.ssoToken()/ssoUrl()` 取, 以與 `/chat` 相同的 `X-SSO-Token`/`X-SSO-Url` header 送 deepagent（`LangGraphAnalysisProvider.addSsoHeaders` 抽成共用）; deepagent 的 bearer 同 `/chat`. 不進 body, 不進 log（既有規則）.
- log: `artifactId`, `connector`, `tool`, **arg keys**（不記值）, 耗時, `ok`/`code`. 與 controller 進入點日誌規範一致.
- 額度: v1 不設 Java 側額度（對話期的 `CONNECTOR_CALL_BUDGET` 是每輪模型呼叫上限, 語意不同）; MCP server 端上限照舊. 觀察指標: 每 artifact 每分鐘呼叫數, 超標再加.

**④ deepagent tool-call 端點.**

- `POST /tool-call`（bearer 同 `/chat`; SSO 兩個 header 缺一即 `{error:{code:"AUTH"}}`, 與 `/chat` 的 `CHAT_INIT_FAILED` 同一條規則）. body `{connector: {id, name, url, bearerTokenKey?}, tool: string, args: object}`——connector 物件與 `/chat` 的 `connectors[]` 元素同形狀, Java 沿用同一個 `ConnectorSpec` 序列化.
- 實作只呼叫 `mcp_adapter` 現有的 `_call` / `_extract_tool_payload`（含 `CONNECTOR_REQUEST_TIMEOUT_SECONDS` 與 `CONNECTOR_CALL_RETRIES`）, 回 `{data: structured_content}` 原樣. **不呼叫 `unwrap_envelope`**——這是 D5 的核心: 對話期拆封只發生在落表那一條路, 頁面拿到的永遠是 raw. 不建 workspace, 不開 DuckDB, 不寫 `connector_calls.jsonl`.
- 錯誤碼固定集合（Java 原樣透傳, 前端不改寫）:

| `code` | 何時 | 頁面該怎麼做 |
|---|---|---|
| `AUTH` | SSO header 缺 | 整頁提示重新登入 |
| `CONNECTOR_NOT_ALLOWED` | connector 不在 session 清單（Java 產） | 該卡顯示錯誤, 不重試 |
| `CONNECTOR_UNAVAILABLE` | catalog 已下架（Java 產） | 同上 |
| `CONNECTOR_UNREACHABLE` | 連不上／協定錯誤, 重試後仍失敗 | 該卡錯誤, 可提供重試按鈕 |
| `TIMEOUT` | deepagent 逾時, 或宿主頁等待逾時 | 同上 |
| `TOOL_ERROR` | MCP server 回 `is_error`, `message` 是 server 的原文 | 該卡顯示 `message`（server 依 howto 規矩 4 寫的可行動文字） |
| `NO_STRUCTURED_CONTENT` | tool 回純文字（違反 howto 規矩 2） | 該卡錯誤, 不重試 |
| `HTTP_<status>` | 前端 bridge 產, Java 回非 200 | 整頁提示 |

**跨層不變量**（任何一層違反就是 bug）:

1. **`data` 原樣直通**: ④ 之後沒有任何一層讀、改、包裝 `data`. 頁面看到的就是 `structuredContent`.
2. **`args` 原樣直通**: 頁面給什麼 JSON, MCP server 收什麼; 沒有任何一層做型別轉換或補預設值.
3. **SSO 只在 header**: 四個 hop 都不進 body, 不進 log, 不進 postMessage.
4. **成功／失敗只有一種形狀**: 頁面永遠收到 `{data}` 或 `{error:{code,message}}`, 沒有第三種; HTTP 層錯誤在 ② 收斂成同形狀.
5. **一次呼叫一次 handler**: runtime 保證; 逾時後遲到的結果丟棄.

**本 spec 凍結／留給實作 plan 的分界.** 凍結: 訊息名稱與欄位, 端點路徑與 body/回應形狀, 錯誤碼集合, prelude 注入點與注入條件, 五條不變量, D10 的錯誤回報. 留給 plan: 前端 in-flight 上限與逾時數值、loading 骨架、`dataMode` 欄位落在哪個 DTO、Java 端 `ConnectorSpec` 與 SSO header 的共用抽取方式、deepagent 端點的 pydantic schema 與測試、分享頁（非 owner 的 viewer）的存取規則——後者是分享功能自己的 spec, 本端點只承諾「與 `GET /api/artifacts/{id}` 同一條規則」, 分享功能改那條規則時這裡自動跟著.

**SKILL.md 隨 plan 補的兩句（不進 merge）**: `r.error.code` 存在且是上表之一, 頁面可依 code 決定要不要給重試按鈕; `TOOL_ERROR` 的 `message` 要原樣顯示給 viewer, 不要吞掉.

**spike 與草案契約的落差（2026-09-08 盤點; 列為 plan 待辦, 非 merge 前置）.** 先說清楚: spike 完整實作了**它當初對照的契約**（既有 skill 的頁面面契約）, 而且跑通了; 下表的落差是對**本節傳輸面草案**的, 是文件長過了 spike, 不是 spike 退步. `spike/mcp-shell/shell.html`（① + ②）與 `bridge.py`（③ + ④ 合成一個 hop）目前只實作了「成功路徑」的形狀; 草案新增的錯誤路徑整段缺. 已對齊的: `mcp()` 簽名與回傳 `undefined`, handler 恰好一次, 訊息名稱與欄位（`erd-mcp-call`/`erd-mcp-result`）, prelude 由宿主頁在 `<head>` 後注入, `sandbox="allow-scripts"`, tool 層級失敗回 200 + `error`, raw 直通（預設）, 三條失敗路徑（connector 不存在／`is_error`／無 structuredContent）都存在. 未對齊的:

| 契約 | spike 現況 | 落差 |
|---|---|---|
| 錯誤是 `{error: {code, message}}` | 兩個檔都只有 `{error: {message}}` | 沒有任何 `code`; 頁面分不出 `TOOL_ERROR` 與 `CONNECTOR_UNREACHABLE` |
| 宿主以 `event.source === iframe.contentWindow` 驗來源 | `shell.html` 只看 `type` | 缺 |
| 宿主有逾時, 回 `TIMEOUT`, 遲到結果丟棄 | 無逾時, 無 in-flight 上限 | 缺 |
| 代理端非 200 → `HTTP_<status>` | 不看 status 直接 `response.json()`; fetch 失敗回無 code 的 error | 缺 |
| 錯誤回報走既有 `erd-artifact-error` | 自創 `erd-iframe-error` | 通道名不同, 真正的 `ArtifactPanel` 會忽略 |
| log 只記 arg keys | `bridge.py` 印完整 `args` | 違反日誌規範 |
| ④ 重用 `mcp_adapter` 的 `_call`/`_extract_tool_payload` | `bridge.py` 自己重寫一份 FastMCP client「鏡像」adapter | 邏輯重複——unwrap 漂移正是這樣發生的 |
| raw 固定, 無拆封選項 | `UNWRAP_RESULT` 旋鈕仍在 | D8 已排定移除 |
| SSO 走 header; Java 與 deepagent 是兩個 hop | 單一程序, 無 SSO | throwaway 對 mock server 可接受, 但要知道它沒驗過這段 |

**待辦（進 D9 實作 plan; 若在 D8 整理 commit 順手做也可, 但只有拿掉 `UNWRAP_RESULT` 是 merge 前置）:** 每條錯誤路徑 補 `code`（對應上表）; 宿主頁驗 `event.source`; 加逾時與 `TIMEOUT`; 非 200 映射成 `HTTP_<status>`; 錯誤回報改名 `erd-artifact-error`; log 改記 keys; `bridge.py` 改 `from app.agent.connectors.mcp_adapter import ...` 重用 `_call`（spike 本來就從 `deepagent-service/` 以 uv 執行, 可直接 import）; 拿掉 `UNWRAP_RESULT`. 這些做完, spike 才算「照傳輸面草案實作」, 重跑才能同時驗成功與失敗兩條路. SSO 與兩個 hop 分離不在 spike 範圍.

## 8. merge 後的一輪（只畫有變的部分）

```mermaid
sequenceDiagram
    participant C as deepagent /chat
    participant W as tool wrapper
    participant G as ConnectorCallLog (workspace)
    participant K as check_dashboard
    participant L as LLM

    C->>C: 開每輪暫存目錄 (落表), 建 ConnectorCallLog(workspace.root/connector_calls.jsonl)
    L->>W: sales_list_orders(days=30)
    W->>W: call → unwrap_envelope (記下走過的 path) → land_response (暫存目錄)
    W->>G: append {connector, tool, args, unwrap_path, envelope_keys, columns, row_count, landed}
    W-->>L: 表名, 欄位, 前 20 列預覽, Raw response shape (讀列路徑)
    L->>L: run_sql ... 寫 dashboard.html (mcp('sales','list_orders',{days}, r => r.data.result...))
    L->>K: check_dashboard
    K->>G: load() (磁碟 + 本輪記憶體鏡像, 本 session 所有輪)
    K-->>L: OK 或 findings (語法/禁止 token/connector 與 tool 存在/CDN/theme/keys/讀層)
    Note over C: 輪末刪暫存目錄; connector_calls.jsonl 隨 workspace zip 保留
```

## 9. 檔案影響（依已定案的 D0–D8, D11; D1–D4 以 (i) 出貨, 表中 `connector_call_log.py`、`call_log` 參數、兩條 lint 與其測試屬 (ii), 另開 PR）

| 檔案 | 動作 |
|---|---|
| `app/engine/api_snapshot.py` | `unwrap_envelope` 回傳多帶 `unwrap_path`; `LandingResult` 多 `unwrap_path`（邏輯不變） |
| `app/engine/connector_call_log.py` | 新增: `ConnectorCallLog(path)`, `append`, `load`（單行損毀跳過; 本輪記憶體鏡像; 降級旗標） |
| `app/agent/connectors/wrapper.py` | 取 datasource 版; `_format_landing_feedback` 多一段 `Raw response shape`; `build_connector_tools` 加 `call_log`, `_execute` 成功與 `EmptyLandingError` 兩條路徑各 append 一筆 |
| `app/agent/tools/check.py` | 移除 `replay_manifest` import; `build_check_tools(..., call_log)`; 改讀 `ConnectorCallLog.load()`, keys 與 unwrap-path 兩條 lint; `call_log=None` 或降級時跳過兩條並附一行說明 |
| `app/agent/chat_turn.py` | 取 datasource 版; `build_agent` 加 `dashboard_skill_root`; 建 `ConnectorCallLog` 傳給 `build_connector_tools` 與 `build_check_tools` |
| `app/agent/graph.py`, `middleware.py` | datasource 版 + `dashboard_skill_root` / `skill_relative_root` |
| `app/agent/prompts.py` | 依 D6 改兩段文字（D10 的 connector 版 `REPAIR_SYSTEM_PROMPT` 延後） |
| `app/agent/repair_flow.py`, `app/api/schemas.py` | （延後, D10） |
| `skills/mcp-data-dashboard/SKILL.md` | 依 D5, D7 改文字; `mcp()` 既有契約不動（D9 的 `r.error.code` 與 D10「錯誤卡會回報」隨 plan） |
| `spike/mcp-shell/shell.html`, `bridge.py`, `README.md`, `out/` | 依 D9 末段待辦對齊契約（錯誤碼、`event.source`、逾時、`HTTP_<status>`、`erd-artifact-error`、log keys、重用 `mcp_adapter._call`、拿掉 `UNWRAP_RESULT`）; 依 D8 重跑換快照; README 的「Contract assumptions」段改指向本 spec D9 |
| `tests/test_api_snapshot.py` | 補: 五種 raw 形狀各自回正確的 `unwrap_path` 與 `envelope_keys`（D5 表格逐列） |
| `tests/test_check_dashboard.py` | 改 fixture 用 `ConnectorCallLog`; `call_log=None` 時兩條檢查不出現且有說明行; 0 列紀錄可通過; 跨輪紀錄可通過; `unwrap_path=["result"]` 時 `r.data.map(` 退件而 `r.data.result.map(` 通過; `unwrap_path=[]` 時 `r.data.result` 退件; handler 參數名非 `r` 也能掃; 降級模式跳過兩條並有說明行 |
| `tests/test_connector_wrapper.py` | 回饋文字含 `Raw response shape` 且路徑句與 raw 形狀一致（三種形狀各一）; 成功落表寫一筆（含 `unwrap_path`）; 0 列寫 `landed:false`; `ConnectorToolError` 不寫; `call_log=None` 不寫; append 失敗不影響回傳 |
| `tests/test_chat_turn_connectors.py` | connector 模式 workspace 下有 `connector_calls.jsonl`; 第二輪仍讀得到第一輪的紀錄 |
| `tests/test_prompts.py` | 依 D6 改斷言 |
| `tests/test_graph.py` | 兩邊合併: 無 `recorder`, 有 `dashboard_skill_root` 案例 |
| `tests/test_connector_call_log.py` | 新增: append/load 往返, 損毀行跳過, 檔案不存在回空, 記憶體鏡像, 降級旗標 |
| 本 spec 與 `2026-09-04-mcp-dashboard-verification-options.md` | 後者的 level 2 表格把 `replay/landings.jsonl` 改成 `connector_calls.jsonl` |
| Java `AnalysisBrowserRepairClient`, `RepairRequestDto` | （延後, D10; 屆時與 D9 實作一起出） |

前端與 Java: **本 spec 的 merge PR 零改動**. D9 的四個 hop 契約已凍結, 實作與 D10 另開 plan.

## 10. 測試與完成條件

- `cd deepagent-service && uv run ruff check . && uv run pytest -q` 全綠; datasource 側 35 個測試檔與 dashboard 側新增的測試都在. `check_dashboard` 以 §6.1(i) 形態註冊於 connector 模式（報告末尾註明紀錄類檢查未啟用）; keys 與讀層兩條 lint **不是**本次完成條件.
- 手動: 依 D8 跑一次 spike（先拿掉 `UNWRAP_RESULT`）, 確認 (1) 模型產出的 handler 依回饋的 `Raw response shape` 讀 `r.data.result`（mock server 的 list 型 tool）且第一版就對, (2) 第二輪只改版面時不重打 connector 且 `check_dashboard` 回 OK, (3) 故意打一個 mock server 會拒絕的參數值, 頁面該卡顯示 server 的錯誤訊息而非空白（`code` 的顯示只在 D9 plan 對齊後才驗）.
- merge PR 描述附本 spec 連結與第 12 節的拍板結果; gate 照專案規則（`./mvnw test` 不受影響但仍跑, opus 全 branch 終審）.

## 11. 非目標

- 宿主端四個 hop 的**實作**（前端 prelude 與 bridge, Java 代理端點, deepagent `/tool-call`）——契約在 D9 凍結, 實作另開 plan. per-artifact 的允許 tool 清單（D9 ③ 提到的擴充點）與分享頁 viewer 的存取規則也不在本文.
- level 3 headless render（09-04 spec 已 deferred; D11 維持不做）.
- 用 `columns` 驗 handler 欄位名（D2 只記, D4 不驗）.
- 跨輪保留落表或 raw payload（datasource §10 的觀察指標未達）.
- 拆 `mcp-data-dashboard/SKILL.md` 成 references.
- node 進 image（09-04 spec 的既有結論, 不變）.
- D10 檢視期錯誤回報（延後, 隨 D9 實作）; 分享頁 viewer 的錯誤回報路徑（D10 也只覆蓋編輯者自己的 session）.

## 12. 待拍板

- [x] **D0** merge 方式: A（datasource 為底, dashboard 重落）——**2026-09-08 使用者定案**
- [x] **D5** `r.data` = raw `structuredContent`, 宿主不拆封; wrapper 回饋明講拆封配方（`Raw response shape` 段）; spike bridge 固定 raw ——**2026-09-08 使用者定案**
- [x] **D6** 兩段 prompt 改措辭: qN 給對話用, dashboard 走 `mcp()`; `inject_results` 不動; 產物生命週期表見 D6 ——**2026-09-08 使用者定案**
- [x] **D7** 保留 `dashboard_skill_root`; SKILL.md 依 D5/D7 改 ——**2026-09-08 使用者定案**
- [x] **D8** spike 保留為 throwaway, merge 後手動重跑一次換快照 ——**2026-09-08 使用者定案**
- [x] **D1–D4** 本次 merge 以 §6.1(i) 最小形態出貨（不驗 keys 與讀層）; (ii)（workspace 頂層 `connector_calls.jsonl`, `ConnectorCallLog` 注入, 記憶體鏡像 + 降級模式, 兩條 lint）設計保留, 另開 PR——**2026-09-09 使用者改案**（09-08 原定案為納入本次 merge）
- [x] **D10** 檢視期錯誤回到模型: 延後, 設計留存 §6.2, 隨 D9 實作開 plan ——**2026-09-08 使用者定案**
- [x] **D11** deepagent 內模擬執行: 本次不做; 設計留在 §6.3 供日後選 A 或 C ——**2026-09-08 使用者定案**
- [x] **D9** 頁面面契約＝既有 skill 契約, merge 不改（只加 D5 的 raw）; 傳輸面（前端 prelude 與 bridge、Java `/mcp-call`、deepagent `/tool-call`、SSO、錯誤碼、不變量）與四項頁面面提案（`code`／非同步／不吞例外／JSON args）為草案, 隨 D9 實作 plan 拍板 ——**2026-09-08 使用者定案**

拍板後: 本節改成「已定案」並把結果寫進第 13 節, 再用 `writing-plans` 產 `docs/superpowers/plans/2026-09-XX-mcp-dashboard-on-autoland.md`.

## 13. 決策紀錄

| 日期 | 決定 | 理由 |
|---|---|---|
| 09-08 | `r.data` 到頁面是 raw, 拆封配方由 wrapper 明講給模型（並在納入紀錄時記進呼叫紀錄）, `check_dashboard` 據此驗 handler 讀對層（D5） | 寫 JS 處理 raw 回傳值的是模型, 它必須知道 DuckDB 的表是 raw 經過什麼處理來的; 把拆封藏在宿主端只是把知識缺口搬到 Java／前端, 還多一份要同步的程式碼 |
| 09-08 | 宿主四個 hop 的契約在本 spec 凍結（D9）, 實作另開 plan | deepagent 側的 SKILL.md／回饋文字／`check_dashboard` 現在就要照契約寫, 不能等 Java／前端實作時再定 |
| 09-08 | runtime prelude 由前端在 srcdoc 組裝時注入, 不寫進儲存的 HTML | runtime 修一次全部頁面生效, 儲存的 artifact 維持模型原樣 |
| 09-08 | connector 模式 qN 只供對話回答, dashboard 走 `mcp()` 現抓; 對話期資料只活本輪, 跨輪只留 qN 結果與呼叫 metadata（D6） | 三種產物三種生命週期要一眼分得開, 否則 prompt 與 skill 會再次互相拉扯 |
| 09-08 | 保留 `dashboard_skill_root` 讓 connector 模式 gate 在 `mcp-data-dashboard` skill; SKILL.md 去 `land_as`, 「this session」定義為 `check_dashboard` 紀錄所及的任一輪, `r.data` 依 D5 改寫（D7） | gate 是唯一強制模型讀對 skill 的機制; skill 文字若與回饋文字講的不一樣, 模型會二選一 |
| 09-08 | spike 保留為 throwaway, 拿掉 `UNWRAP_RESULT`, merge 後手動重跑一次換快照當驗收（D8） | 它是唯一能看見「模型收到 Raw response shape 後第一版是否就讀對層」的地方; 不寫自動化測試, 因為要真模型 |
| 09-08 | 文件重組: 決策分三群（merge 與資料面／check_dashboard／宿主契約）, D1–D4 收成一組並補「延後」形態、寫入失敗退路; 新增 D10、D11 | check_dashboard 相關的取捨（紀錄、事後回報、模擬執行）互相牽動, 分開看會漏掉「延後紀錄後靠什麼補洞」這個問題 |
| 09-08 | merge 方式選 A: merge datasource 進 `feat/mcp-dashboard`, 三個衝突檔取 datasource 側, dashboard 功能以獨立 commit 重落（D0） | dashboard 側只有一個實質 commit; merge commit 之後每個 commit 都是有意的設計變更, review 面積最小 |
| 09-08 | deepagent 內不模擬瀏覽器執行, 以 D10 讓檢視期錯誤變大聲（D11） | 維持 PR #40 的姿態; 落表只活本輪, 模擬對純改版面輪無效; 有缺口證據再回頭選 A（材料與比對規則已留） |
| 09-08 | 呼叫紀錄 `connector_calls.jsonl` 納入本次 merge: workspace 頂層、跨輪、只記 metadata; `ConnectorCallLog` 注入 wrapper 與 `check_dashboard`; 寫入失敗走記憶體鏡像 + 降級模式; keys 與 unwrap-path 兩條 lint（D1–D4） | 事前擋住小模型最常犯的兩類（抄 schema 而非抄呼叫、讀錯層）; 退路設計已把修復迴圈風險排除 |
| 09-09 | D1–D4 改案: 本次 merge 只出 §6.1(i) 的最小 `check_dashboard`（語法、禁止 token、connector 與 tool 存在、CDN、theme）, 不驗 keys 與讀層; (ii) 保留設計另開 PR | 第一輪開發要先觀察模型能否產出帶 `mcp()` 的 dashboard; 錯誤由人在 spike 頁面看到後貼回對話即可, 沒有「沒人看見的錯」; (ii) 是最大的一塊工作, 不該擋在那個觀察前面. 做 (ii) 或先做 D9+D10, 依人工測試觀察到的主要失敗形態決定 |
| 09-08 | 檢視期錯誤回報延後, 隨 D9 實作（D10） | 前端 prelude 尚未存在, 單獨改 repair prompt 收益有限; D1–D4 納入後過渡期漏掉的只剩值／權限／可用性三類 |
| 09-08 | spike 盤點: 成功路徑的形狀已與 D9 一致, 錯誤路徑（code、來源驗證、逾時、HTTP 映射、回報通道、log、重用 adapter）全缺; 列為 D8 整理 commit 的待辦, 重跑驗收前先對齊 | spike 是契約的活文件; 不對齊, D8 的重跑只能驗一半 |
| 09-08 | D9 狀態更正: 頁面面契約早已由 skill 與 spike 實作且跑通, merge 只加 D5 的 raw, 不需新決策; 傳輸面與四項頁面面提案（`code`、非同步、不吞例外、JSON args）降為草案, 隨實作 plan 拍板 | 先前把草案的加強項列成 merge 待決事項是文件越寫越大造成的錯覺; datasource branch 只動 connector 落表, 沒碰頁面契約 |

**所有 merge 所需決策已於 2026-09-08 定案; Phase A 已於 2026-09-09 經 PR #81 merge 進 `feat/mcp-dashboard`（`919be87`; merge commit `577d1ee` 基準 datasource `bcb61f3`, 有意留白的項目列在該 commit 訊息裡, 之後 11 個 commit 重接）.** plan 已產出: `docs/superpowers/plans/2026-09-08-mcp-dashboard-on-autoland.md`（本次 merge 範圍: D0、D5–D8、D1–D4 的 (i); plan 的 Phase B 即 D1–D4 (ii), 另開 PR; D9 傳輸面、D10、D11 不在內）; 下一步依 plan Phase A 逐 task 實作.
