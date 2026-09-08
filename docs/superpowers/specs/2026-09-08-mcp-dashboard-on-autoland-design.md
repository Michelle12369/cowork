# MCP dashboard 接上 connector 自動落表——兩條分支合流的設計決策

> 狀態: 2026-09-08 草稿, **待使用者拍板**（第 10 節逐條勾選）. 依 superpowers `brainstorming` 的 architectural 路徑撰寫: 先列現況與衝突, 再列每個決策的選項, 取捨與建議; 拍板後才走 `writing-plans` 產 plan, 拍板前不動程式.
>
> 對象分支: `feat/mcp-dashboard`（本分支, 已含 `feat/9E`）與 `feat/mcp-datasource`（PR #78 之後的 31 個 commit）. 相關文件: `2026-08-30-mcp-datasource-design.md`（datasource 分支版, 第 7/10/11 節）, `2026-09-04-mcp-dashboard-verification-options.md`, plan `2026-09-06-connector-autoland-ephemeral.md`（datasource 分支）.

## 1. 這是在做什麼

兩條分支各自往同一個終點走——「connector 模式的 dashboard 在檢視時自己透過宿主提供的 `mcp()` 打 MCP 取數, 不再把資料注入 HTML」——但中間各改了一段不同的管線:

- `feat/mcp-datasource` 改的是**對話期**: connector tool 每次呼叫自動落成 DuckDB 表, 表名用參數 hash, 表只活一輪, 並拆掉 `land_as`, replay manifest, snapshot 持久化與 `ToolResultRecorder`.
- `feat/mcp-dashboard` 改的是**產出期**: 新的 `mcp-data-dashboard` skill（`mcp()` 契約, 卡片狀態, 互動控制項）, `check_dashboard` 工具（JS 語法檢查 + `mcp()` 契約 lint）, connector 模式下 skill gate 改讀這份 skill, 以及一個 throwaway 的 `spike/mcp-shell` 端到端探針.

`check_dashboard` 的契約 lint 靠 replay manifest（`replay/landings.jsonl` + `replay/calls.jsonl`）得知「這個 session 實際打過哪些 (connector, tool, arg keys)」, 而 datasource 分支把 replay manifest 整個刪了. 所以兩邊不能機械合併: dry-run merge 三個檔案衝突（`chat_turn.py`, `connectors/wrapper.py`, `engine/replay_manifest.py` 一改一刪）, 更重要的是底下有五個語意衝突（第 3 節）. 本文的目的是把「合流要做哪些決定」一次列清楚.

## 2. 兩條分支現況

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

**S3. `r.data` 的形狀契約.** skill 說 `r.data` 是「與分析期呼叫同 tool 同參數所見的 payload byte-for-byte 相同」. 但在 datasource 分支, 模型**從未看過 raw payload**——wrapper 先 `unwrap_envelope`（拆 FastMCP `{result: ...}`, dict 只取 `data` list）, 再落表, 回給模型的是表名 + 欄位 + 前 20 列預覽. 模型學到的形狀是「一個扁平列的陣列」. 而 spike 的 bridge 預設不拆封（`UNWRAP_RESULT=1` 才拆）, spike 的三張快照正好記錄了模型在 `r.data` 與 `r.data.result` 之間來回猶豫. 這不是模型問題, 是契約沒定.

**S4. qN 與 dashboard 的關係.** datasource 的 `CONNECTOR_MODE_SYSTEM_SECTION` 與 `CONNECTOR_TABLES_RESET_NOTE` 都把 qN 講成「dashboard 直接引用的東西」; mcp-dashboard skill 把 `__ERD_RESULTS__` 列為禁止 token, `check_dashboard` 看到就退件. 兩段 prompt 同時在場會把模型往兩個方向拉. （`chat_turn` 對 connector 模式仍會呼叫 `inject_results`, 但引用集合為空時只注入 `window.__ERD_RESULTS__ = {}` 與 proxy 腳本, 無害, 不需要改.）

**S5. 機械性衝突.** `graph.build_agent` 一邊拿掉 `recorder`, 一邊加 `dashboard_skill_root`; `chat_turn.prepare` 的 connector 分支兩邊各自重寫; `test_graph.py` 同一區塊兩邊都改. 解法固定, 不需要決策, 列出只為完整.

**S6. skill 文字過期.** `mcp-data-dashboard/SKILL.md` Workflow 第 1 步仍寫「landing results with `land_as`」.

## 4. 合流方式（D0）

**選項 A（建議）: 以 datasource 為底, dashboard 的四個功能 commit 重新落上.** 具體做法: 在 `feat/mcp-dashboard` 上 `git merge origin/feat/mcp-datasource`, 三個衝突檔一律取 datasource 側（`replay_manifest.py` 接受刪除）, 先讓 merge commit 落地（此時 `check.py` 會 import 失敗, 測試紅, 但 merge 本身乾淨可讀）, 接著以獨立 commit 依第 5 節的決策把 `check_dashboard`, skill gate root, SKILL.md 與 spike 重新接上. 理由: dashboard 側只有一個實質功能 commit 加兩個 fix, 而 datasource 側是 31 個 commit 且已經歷 opus 終審; 把小的搬到大的上面, review 面積最小, 且 merge commit 之後的每個 commit 都是「有意的設計變更」, 不是「解衝突順手改的」.

**選項 B: merge 時逐 hunk 手解.** 同一個 merge commit 裡同時做「接受刪除」和「補新紀錄機制」. 結果一樣, 但 review 時看不出哪些是解衝突, 哪些是新設計; 且 merge commit 不能被 revert 成單一步驟. 不建議.

**選項 C: 反向, 保留 replay manifest.** 把 datasource 合進來但把 `replay_manifest.py` 救回來給 `check_dashboard` 用. 這等於推翻 09-06「持久化失去用途, 整套拆掉」的決策, 而且 replay manifest 記的是 `land_as` 別名與 sha256, 大半欄位在自動落表下已無意義. 否決.

不論選哪個, 分支名維持 `feat/mcp-dashboard`（開發分支規範）, 不 rebase 不 force-push.

## 5. 設計決策

每一條都是「合流後 connector 模式的 dashboard 產出線要長什麼樣」的決定. 標 **建議** 的是本文推薦, 拍板欄在第 10 節.

### D1. 呼叫紀錄放哪: 每輪暫存, 還是跨輪保留

| 選項 | 內容 | 後果 |
|---|---|---|
| (a) 跟落表一起放每輪暫存目錄 | 輪末一起刪 | 直接撞 S2: 修改輪的 `check_dashboard` 必退件, 除非放寬檢查或叫模型每輪重打（違反 datasource prompt, 也多花 API 呼叫） |
| (b) **建議** 放 workspace, 跨輪保留 | 一個 append-only 的 `connector_calls.jsonl`（位置見 D2）, 隨 workspace zip 走, 但**只記 metadata, 不記資料** | 修改輪沿用前幾輪的紀錄; 與 09-06「原始資料不進 workspace」不衝突, 因為紀錄裡沒有資料列 |
| (c) 不記, 改由 `check_dashboard` 只驗「tool 存在於 connector」 | 放棄 arg keys 比對 | 失去 lint 最有價值的一條（模型從 schema 猜 arg keys 而非從實際呼叫抄）, 09-04 spec 的 level 2 退化 |

選 (b) 的附帶決定: 紀錄的生命週期 = session（與 qN 相同）. 不做去重（同參數重打就多一行; `check_dashboard` 讀進來先 group 再比, 行數不影響結果）. 檔案大小每行 < 1 KB, 一個 session 額度 50 次/輪, 不需要輪替.

### D2. 記什麼, 記在哪個檔

**建議** 每筆一行 JSON:

```json
{"connector_id": "sales", "tool_name": "list_orders", "args": {"days": 30},
 "columns": ["order_id", "region", "amount"], "row_count": 412, "landed": true}
```

- `args` 是剝除 None 之後、實際送給 connector 的那份（與回饋文字印出的相同, 與表名 hash 用的相同）.
- `columns` / `row_count` 來自 `LandingResult`. 現在 `check_dashboard` 不用它們, 記下來是為了下一步（level 2.5: 驗 handler 裡 `row.xxx` 的欄位名是否存在）不必再改寫入端. 成本零.
- **0 列也記**（`landed: false`, `columns: []`）: 呼叫本身成功, 模型可以在 dashboard 裡用同一組 arg keys 配不同的值. 不記會讓「分析時剛好選到空區間」的合法用法被退件.
- `ConnectorToolError`, 傳輸失敗, 額度用盡: **不記**. 模型沒看過回應形狀, 不該寫進 dashboard.
- 檔案位置: `workspace.root / "connector_calls.jsonl"`（workspace 頂層, 與 `queries/`, `results/` 並列）. 不復活 `replay/` 目錄名——那個名字綁著已拆掉的 replay 設計.

### D3. 誰寫紀錄: wrapper 怎麼拿到寫入端

datasource 的 plan 明訂 wrapper 不該自己決定目錄, 目錄由呼叫端注入（「未來接縫」第一條）. 紀錄也照這個原則.

| 選項 | 內容 | 評估 |
|---|---|---|
| (a) `build_connector_tools` 加回 `workspace` 參數 | wrapper 直接 append 到 workspace | 讓 wrapper 重新依賴 workspace 整個物件, 09-06 才剛拿掉 |
| (b) **建議** 注入一個 `ConnectorCallLog` | engine 層（`app/engine/connector_call_log.py`, stdlib only）的小物件, 建構時給路徑, 提供 `append(record)` 與 `load() -> list[dict]`; `build_connector_tools(..., call_log: ConnectorCallLog | None = None)`, `None` 就不記（測試與 spike 方便） | wrapper 只多一個 protocol 依賴; `check_dashboard` 拿同一個物件讀; 單行損毀跳過並 warning 的邏輯搬進 `load()` |
| (c) `chat_turn` 從 `astream_events` 的 `on_tool_end` 事件記 | wrapper 零改動 | 事件是非同步消費, `check_dashboard` 在同一個 graph 內同步執行, 同一則 AI message 先打 connector 再 `check_dashboard` 時紀錄可能還沒寫; 而且 tool 回饋是文字, 要反解析才能拿到 args 與 columns. 否決 |

選 (b) 的附帶決定: `append` 失敗（磁碟滿, 權限）只 `logger.warning`, 不影響 tool 回傳（維持 never-raise, 與現有 `record_call` 的 best-effort 相同）.

### D4. `check_dashboard` 的比對範圍與粒度

- 讀 `ConnectorCallLog.load()` 全部, 跨輪（承 D1）.
- 比對 **arg keys 集合**, 不比值, 不比型別（值來自 viewer 控制項, 本來就會變; 型別檢查是 level 3 的事）. 與現況相同.
- 找不到任何紀錄的 (connector, tool) → 退件文字維持「tool was never called in this session — call it first」; 但要在 SKILL.md 把「session」定義清楚（見 D7）.
- **不做**: 用 `columns` 驗 handler 內欄位名. 留給下一個 spec; 寫入端已就位（D2）.

### D5. `r.data` 的形狀契約: raw, 還是與落表相同的拆封結果

這是唯一會影響 **deepagent 以外**（未來 Java 代理端點與前端 bridge）的決定, 也是 spike 三張快照暴露出來的坑.

| 選項 | 內容 | 評估 |
|---|---|---|
| (a) raw `structuredContent` | 宿主原樣轉發 MCP 回傳值; FastMCP 的 `{result: [...]}` 包裝原樣到頁面 | 模型在對話期沒看過這個形狀（wrapper 拆掉了）, 只能靠 skill 文字教它「list 型工具的 r.data 其實在 r.data.result」——這正是 spike 裡模型來回改三次的原因 |
| (b) **建議** 與 wrapper 同一支 `unwrap_envelope` | 宿主端（未來 deepagent 的無模型 tool-call 端點, datasource spec §11）呼叫 MCP 後套同一支 `unwrap_envelope`, 回 `{data: <拆封後的 list 或 dict>, meta: <信封其他欄位>}` 或 `{error: {message}}` | 模型在對話期看到的表（列, 欄位）就是頁面拿到的 `r.data`; `errorCode` 之類的信封欄位進 `meta`, 與回饋文字的「回應其他欄位」一一對應. 拆封規則只存在一處 |

選 (b) 的附帶決定:

- SKILL.md 的「byte-for-byte what you saw」改寫為「與該次呼叫落成的表相同的列（同欄位, 同型別）; 信封其他欄位在 `r.meta`」. 「Reading the response」一節的 `r.data.items` 範例改成只講 list 形狀與非信封 dict 的一列形狀.
- 現在還沒有宿主端點, 這條決定先落在三個地方: SKILL.md 文字, `check_dashboard` 新增一條 lint（`r.data.result` → 退件, 訊息說明宿主已拆封）, spike 的 `bridge.py` 改成預設拆封並改用 `app.engine.api_snapshot.unwrap_envelope`（spike 仍是 throwaway, 只是把它當契約的活文件）.
- 未來 Java 端點與前端 bridge 的 spec 引用本條, 不另定形狀.

### D6. qN 在 connector 模式的角色, 以及兩段 prompt 的措辭

**建議**: 在 connector 模式, qN 是**對話回答用的**（模型算出來的數字、洞察句子）, dashboard 一律走 `mcp()` 現抓. 據此改 datasource 側的兩段文字:

- `CONNECTOR_MODE_SYSTEM_SECTION`: 「Landed tables live only for the current turn, but the qN results produced by run_sql persist across turns: when merely changing the dashboard's layout ... reuse the existing qN」→ 改為「Landed tables live only for the current turn. The dashboard never embeds data: it fetches live through `mcp()` at view time (see the mcp-data-dashboard skill), so a layout-only change needs no new connector call — `check_dashboard` validates against the calls already recorded in this session. Call a connector tool again only when you need to see a new tool or a new argument shape.」
- `CONNECTOR_TABLES_RESET_NOTE`: 拿掉「可直接在 dashboard 中引用」, 改為「先前輪次的 connector 呼叫紀錄仍在, 純改版面不必重打」.
- `chat_turn` 對 connector 模式的 `inject_results` **不動**（空集合注入無害, 見 S4）; 若日後要省那段 proxy 腳本再說.

這條與 datasource 09-06「qN 跨輪保留, 純改版面沿用 qN」的決策**不衝突**: 那條決策的目的是阻止模型改版面時重打六次 API, 在 mcp-dashboard 下達成同一目的的機制換成「呼叫紀錄跨輪 + `check_dashboard` 讀紀錄」, prompt 只是改講法.

### D7. skill gate 與 SKILL.md

- `build_agent(..., dashboard_skill_root=...)` 與 `DashboardSkillGateMiddleware(skill_relative_root=...)` **原樣保留**, 疊在 datasource 拿掉 `recorder` 之後的簽名上.
- SKILL.md 改動清單: Workflow 第 1 步去 `land_as`, 改成「每次 connector 呼叫自動落表, 回饋給你表名、欄位與預覽; 這就是 dashboard 要用的 (connector, tool, args, 形狀)」; 鐵律第 2 條的「this session」明確定義為「本對話任何一輪你實際打過的呼叫, 以 `check_dashboard` 的紀錄為準; 不含只在 skill 檔看過但沒打過的 tool」; `r.data` 形狀依 D5 改; 其餘（卡片狀態, 控制項, 佈局, ECharts 規則）不動.
- gate 的必讀清單仍是整個 `.skills/builtin/mcp-data-dashboard` 下所有 `.md`（目前只有 SKILL.md 一份, 1222 行; 是否拆 references 不在本 spec）.

### D8. spike 去留

保留在 `deepagent-service/spike/mcp-shell/`, 維持 THROWAWAY 標記. 合流後依 D5 改 bridge 的拆封預設, 然後**手動再跑一次**作為合流驗收（README「實際跑法」一節的指令）, 把新一組快照放進 `out/`, 舊三張刪掉. 不寫自動化測試（spike 需要 OpenRouter 與真模型）.

## 6. 合流後的一輪（只畫有變的部分）

```mermaid
sequenceDiagram
    participant C as deepagent /chat
    participant W as tool wrapper
    participant G as ConnectorCallLog (workspace)
    participant K as check_dashboard
    participant L as LLM

    C->>C: 開每輪暫存目錄 (落表), 建 ConnectorCallLog(workspace.root/connector_calls.jsonl)
    L->>W: sales_list_orders(days=30)
    W->>W: call → unwrap_envelope → land_response (暫存目錄)
    W->>G: append {connector, tool, args, columns, row_count, landed}
    W-->>L: 表名, 欄位, 前 20 列預覽
    L->>L: run_sql ... 寫 dashboard.html (mcp('sales','list_orders',{days},...))
    L->>K: check_dashboard
    K->>G: load() (本 session 所有輪)
    K-->>L: OK 或 findings (含 r.data.result 退件)
    Note over C: 輪末刪暫存目錄; connector_calls.jsonl 隨 workspace zip 保留
```

## 7. 檔案影響（選項 A + 全部建議值）

| 檔案 | 動作 |
|---|---|
| `app/engine/connector_call_log.py` | 新增: `ConnectorCallLog(path)`, `append`, `load`（單行損毀跳過） |
| `app/agent/connectors/wrapper.py` | 取 datasource 版; `build_connector_tools` 加 `call_log: ConnectorCallLog \| None`; `_execute` 成功與 `EmptyLandingError` 兩條路徑各 append 一筆 |
| `app/agent/tools/check.py` | 改讀 `ConnectorCallLog.load()`; 新增 `r.data.result` lint; 移除 `replay_manifest` import |
| `app/agent/chat_turn.py` | 取 datasource 版; connector 分支建 `ConnectorCallLog`, 傳給 `build_connector_tools` 與 `build_check_tools`; `build_agent` 加 `dashboard_skill_root` |
| `app/agent/graph.py`, `middleware.py` | datasource 版 + `dashboard_skill_root` / `skill_relative_root` |
| `app/agent/prompts.py` | 依 D6 改兩段文字 |
| `skills/mcp-data-dashboard/SKILL.md` | 依 D5, D7 改文字 |
| `spike/mcp-shell/bridge.py`, `README.md`, `out/` | 依 D5, D8 |
| `tests/test_check_dashboard.py` | 改用 `ConnectorCallLog` 建 fixture; 新增 0 列紀錄可通過、`r.data.result` 退件、跨輪紀錄可通過三條 |
| `tests/test_connector_wrapper.py` | 新增: 成功落表寫一筆、0 列寫 `landed:false`、`ConnectorToolError` 不寫、`call_log=None` 不寫、append 失敗不影響回傳 |
| `tests/test_chat_turn_connectors.py` | 新增: connector 模式 workspace 下有 `connector_calls.jsonl`, 第二輪仍讀得到第一輪的紀錄 |
| `tests/test_prompts.py` | 依 D6 改斷言 |
| `tests/test_graph.py` | 兩邊合併: 無 `recorder`, 有 `dashboard_skill_root` 案例 |
| `tests/test_connector_call_log.py` | 新增: append/load 往返, 損毀行跳過, 檔案不存在回空 |
| 本 spec 與 `2026-09-04-mcp-dashboard-verification-options.md` | 後者的 level 2 表格把 `replay/landings.jsonl` 改成 `connector_calls.jsonl` |

Java 與前端: **零改動**（宿主端點與 bridge 是後續 spec）.

## 8. 測試與完成條件

- `cd deepagent-service && uv run ruff check . && uv run pytest -q` 全綠; datasource 側 35 個測試檔與 dashboard 側新增的測試都在.
- 手動: 依 D8 跑一次 spike, 確認 (1) 模型產出的 `mcp()` 讀 `r.data` 不讀 `r.data.result`, (2) 第二輪只改版面時不重打 connector 且 `check_dashboard` 回 OK.
- 合流 PR 描述附本 spec 連結與第 10 節的拍板結果; gate 照專案規則（`./mvnw test` 不受影響但仍跑, opus 全分支終審）.

## 9. 非目標

- 宿主端的 `mcp()` runtime, Java 代理端點, 前端 postMessage 橋接, artifact 的允許呼叫清單——datasource spec §11 列的東西, 另開 spec. 本文只把 `r.data` 形狀定下來給它引用.
- level 3 headless render（09-04 spec 已 deferred, 維持）.
- 用 `columns` 驗 handler 欄位名（D2 只記, D4 不驗）.
- 跨輪保留落表（datasource §10 的觀察指標未達）.
- 拆 `mcp-data-dashboard/SKILL.md` 成 references.
- node 進 image（09-04 spec 的既有結論, 不變）.

## 10. 待拍板

- [ ] **D0** 合流方式: A（datasource 為底, dashboard 重落）/ B / C
- [ ] **D1** 呼叫紀錄跨輪保留在 workspace（b）
- [ ] **D2** 紀錄欄位含 `columns`/`row_count`/`landed`; 0 列也記; 失敗不記; 檔名 `connector_calls.jsonl` 放 workspace 頂層
- [ ] **D3** 以 `ConnectorCallLog` 注入 wrapper（b）, `None` 不記
- [ ] **D4** `check_dashboard` 跨輪比 arg keys, 不驗欄位
- [ ] **D5** `r.data` = 宿主套同一支 `unwrap_envelope` 的結果, 信封欄位進 `r.meta`（b）; 新增 `r.data.result` lint; spike bridge 預設拆封
- [ ] **D6** 兩段 prompt 改措辭: qN 給對話用, dashboard 走 `mcp()`; `inject_results` 不動
- [ ] **D7** 保留 `dashboard_skill_root`; SKILL.md 依 D5/D7 改
- [ ] **D8** spike 保留為 throwaway, 合流後手動重跑一次換快照

拍板後: 本節改成「已定案」並把結果寫進第 11 節, 再用 `writing-plans` 產 `docs/superpowers/plans/2026-09-XX-mcp-dashboard-on-autoland.md`.

## 11. 決策紀錄

| 日期 | 決定 | 理由 |
|---|---|---|
| 09-08 | （待填） | |
