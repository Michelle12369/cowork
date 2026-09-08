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

**S3. `r.data` 的形狀契約.** skill 說 `r.data` 是「與分析期呼叫同 tool 同參數所見的 payload byte-for-byte 相同」. 但在 datasource 分支, 模型**從未看過 raw payload**——wrapper 先 `unwrap_envelope`（拆 FastMCP `{result: ...}`, dict 只取 `data` list）, 再落表, 回給模型的是表名 + 欄位 + 前 20 列預覽, **沒有告訴模型它拆了什麼**. 模型學到的形狀是「一個扁平列的陣列」, 但頁面在檢視時拿到的是 raw payload, 中間差了一層它不知道存在的拆封. spike 的三張快照正好記錄了模型在 `r.data` 與 `r.data.result` 之間來回猶豫. 這不是模型問題, 是 wrapper 做了一件事卻沒說, 而寫 JS 處理 raw 回傳值的正是模型.

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
 "unwrap_path": ["result"], "envelope_keys": [],
 "columns": ["order_id", "region", "amount"], "row_count": 412, "landed": true}
```

- `args` 是剝除 None 之後、實際送給 connector 的那份（與回饋文字印出的相同, 與表名 hash 用的相同）.
- `unwrap_path` / `envelope_keys` 是 D5 定義的拆封配方: raw payload 往下走哪幾個 key 才到落表的那個值, 以及信封層有哪些其他欄位. 這是 `check_dashboard` 驗 handler 讀對層的依據.
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
- **新增一條 lint: handler 讀的層要對上 `unwrap_path`.** 對每個 `mcp()` 呼叫, 取該 (connector, tool) 紀錄的 `unwrap_path`（同一對若多筆紀錄路徑不同——理論上不會, server 同一個 tool 形狀固定——取最後一筆並 warning）. 掃 handler 本體對 `r.data` 的第一層存取: 路徑為 `["result"]` 而 handler 寫 `r.data.map(` / `r.data.length` / `r.data[` → 退件「rows are at r.data.result (the analysis-time landing unwrapped that key)」; 路徑為 `[]` 而 handler 寫 `r.data.result` 或 `r.data.data` → 退件「r.data is already the array」. 掃描仍是 regex 級（與現有 forbidden token 同等級）, 只看 `r.data` 後面接的第一個 `.key` 或 `[`／`.map(`, 不建 JS parser; handler 參數名不叫 `r` 時, 從 `mcp(` 第四個引數的 arrow function 參數名取.
- **不做**: 用 `columns` 驗 handler 內欄位名. 留給下一個 spec; 寫入端已就位（D2）.

### D5. `r.data` 的形狀契約: raw 到頁面, 拆封配方明講給模型

這是唯一會影響 **deepagent 以外**（未來 Java 代理端點與前端 bridge）的決定, 也是 spike 三張快照暴露出來的坑. **2026-09-08 使用者定案: `r.data` 是 raw, 拆封邏輯對模型明講.** 理由: 寫 JS 處理 raw MCP 回傳值的是模型, 所以模型必須知道「DuckDB 裡那張表是 raw payload 經過哪幾步才變成的」; 把拆封藏在宿主端只是把同一個知識缺口從 deepagent 搬到 Java 與前端, 還多一份要同步的程式碼.

| 選項 | 內容 | 評估 |
|---|---|---|
| (a) **定案** raw `structuredContent` + 拆封配方明講 | 宿主原樣轉發 MCP 回傳值, 不拆封, 不加 `meta`; wrapper 在回饋文字裡把「raw 長什麼樣, 我拆了哪幾層, 表是從哪個值落的」逐字告訴模型, 同一份配方寫進呼叫紀錄（D2）, `check_dashboard` 據此驗 handler 讀對層（D4） | 宿主端零邏輯, Java／前端 bridge 只是轉發; 模型看到的與頁面拿到的形狀差異被明確描述, 而不是被抹平; 配方可驗證 |
| (b) 宿主套同一支 `unwrap_envelope`, 回 `{data, meta}` | 頁面拿到的就是落表的列 | 拆封邏輯要在 deepagent 與宿主端各一份（或宿主回頭打 deepagent）; 模型仍然不知道 raw 長什麼樣, 換 server 回傳形狀時兩邊要一起改; 否決 |

**拆封配方的表示法.** 現行 `unwrap_envelope` 是三條規則的遞迴（list 原樣; dict 有 `data` 取 `data`; dict 只有 `result` 拆開再套一次）, 所以任何一次拆封都能寫成一條 key 路徑加一組信封欄位:

| raw payload | `unwrap_path` | `envelope_keys` | 落表的值 |
|---|---|---|---|
| `[{...}, ...]` | `[]` | `[]` | 整個 list |
| `{"data": [...], "errorCode": ""}` | `["data"]` | `["errorCode"]` | `data` |
| `{"result": [...]}`（FastMCP 包 list） | `["result"]` | `[]` | `result` |
| `{"result": {"data": [...], "total": 9}}` | `["result", "data"]` | `["total"]` | `result.data` |
| `{"fab": "A", "yield": 0.97}`（非信封 dict） | `null` | `[]` | 整個 dict 落成一列 |

改動: `unwrap_envelope` 回傳值從 `(data, envelope_fields)` 改為含 `unwrap_path` 的三元組（或 `LandingResult` 多兩個欄位 `unwrap_path`, `envelope_fields`）; 邏輯不變, 只是把它走過的路記下來.

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
- 宿主端（前端 bridge, Java 代理, deepagent 無模型 tool-call 端點）的契約: `{data: <structuredContent 原樣>}` 或 `{error: {code, message}}`, 沒有 `meta`. 四層各自的責任與訊息形狀見 D9.
- spike 的 `bridge.py` 拿掉 `UNWRAP_RESULT` 旋鈕, 固定 raw（它現在的預設就是 raw, 只是把選項拿掉讓它不再像個未定案）.

### D6. qN 在 connector 模式的角色, 以及兩段 prompt 的措辭

**2026-09-08 使用者定案**: 在 connector 模式, qN 是**對話回答用的**（模型算出來的數字、洞察句子）, dashboard 一律走 `mcp()` 現抓. 據此改 datasource 側的兩段文字:

- `CONNECTOR_MODE_SYSTEM_SECTION`: 「Landed tables live only for the current turn, but the qN results produced by run_sql persist across turns: when merely changing the dashboard's layout ... reuse the existing qN」→ 改為「Landed tables live only for the current turn. The dashboard never embeds data: it fetches live through `mcp()` at view time (see the mcp-data-dashboard skill), so a layout-only change needs no new connector call — `check_dashboard` validates against the calls already recorded in this session. Call a connector tool again only when you need to see a new tool or a new argument shape.」
- `CONNECTOR_TABLES_RESET_NOTE`: 拿掉「可直接在 dashboard 中引用」, 改為「先前輪次的 connector 呼叫紀錄仍在, 純改版面不必重打」.
- `chat_turn` 對 connector 模式的 `inject_results` **不動**（空集合注入無害, 見 S4）; 若日後要省那段 proxy 腳本再說.

這條與 datasource 09-06「qN 跨輪保留, 純改版面沿用 qN」的決策**不衝突**: 那條決策的目的是阻止模型改版面時重打六次 API, 在 mcp-dashboard 下達成同一目的的機制換成「呼叫紀錄跨輪 + `check_dashboard` 讀紀錄」, prompt 只是改講法.

**2026-09-08 使用者定案.** 合流後 connector 模式一輪裡會產生的每一樣東西, 落在哪, 活多久, 誰用:

| 產物 | 落在哪 | 生命週期 | 寫入者 | 讀取者 | 內容含使用者資料? |
|---|---|---|---|---|---|
| connector 回應的 JSON 檔 | 每輪暫存目錄（`tempfile.TemporaryDirectory`, `allowed_directories` 唯一允許的路徑） | **本輪**, `ChatTurn.__aexit__` 刪 | wrapper `land_response` | DuckDB `read_json_auto` 掛表 | 是 |
| DuckDB 表 `{connector}_{tool}_{hash}` | 本輪 DuckDB 連線（記憶體 + 上列 JSON 檔） | **本輪**, 連線關閉即消失 | wrapper `mount_json_file` | 模型的 `get_schema`/`run_sql`/`preview_data` | 是 |
| tool 回饋文字（表名, 欄位, 預覽, `Raw response shape`） | LangGraph checkpoint 的訊息歷史 | session（隨對話歷史保留與壓縮） | wrapper `_format_landing_feedback` | 模型（寫 SQL 與 dashboard 時抄形狀與路徑） | 預覽含前 20 列 |
| `queries/qN.sql`, `results/qN.json` | workspace zip | **跨輪**, 隨 session 保留期 | `run_sql` tool | 模型（對話回答引用數字）; file 模式下 `inject_results` 注入 dashboard; **connector 模式下 dashboard 不用** | 是（聚合結果） |
| `connector_calls.jsonl` | workspace zip 頂層 | **跨輪**, 隨 session 保留期 | wrapper（經 `ConnectorCallLog.append`, 成功與 0 列各一筆） | `check_dashboard`（arg keys 與 `unwrap_path` 比對）; 未來 Java per-artifact tool 清單的材料 | **否**（只有 connector, tool, arg keys 與值, 欄位名, 列數, 拆封路徑） |
| `dashboard.html` | workspace zip → Java artifact 儲存 | 跨輪; artifact 依保留政策 2 年 | 模型 `write_file`/`edit_file` | `check_dashboard`（本輪）; Java 出貨; 前端 srcdoc; viewer 瀏覽器 | **否**（connector 模式不注入資料; 引用集合為空時 `inject_results` 只注入 `{}`） |
| viewer 開頁時的 `mcp()` 回應 | viewer 瀏覽器記憶體 | 該頁面存活期間 | D9 四跳 | 頁面 handler | 是（viewer 自己權限內的即時資料） |

三個一眼要看出來的事: 對話期的資料（前兩列）只活本輪, workspace 裡沒有任何原始資料列; 跨輪保留的只有 qN 結果與呼叫 metadata; connector 模式的 dashboard 不消費 qN, 檢視時的資料完全來自 viewer 自己的呼叫.

### D7. skill gate 與 SKILL.md

- `build_agent(..., dashboard_skill_root=...)` 與 `DashboardSkillGateMiddleware(skill_relative_root=...)` **原樣保留**, 疊在 datasource 拿掉 `recorder` 之後的簽名上.
- SKILL.md 改動清單: Workflow 第 1 步去 `land_as`, 改成「每次 connector 呼叫自動落表, 回饋給你表名、欄位、預覽, 以及一段 `Raw response shape` 說明 raw 回傳值與表的關係; dashboard 要用的 (connector, tool, args, 讀列路徑) 全部抄自這段回饋」; 鐵律第 2 條的「this session」明確定義為「本對話任何一輪你實際打過的呼叫, 以 `check_dashboard` 的紀錄為準; 不含只在 skill 檔看過但沒打過的 tool」; `r.data` 形狀依 D5 改; 其餘（卡片狀態, 控制項, 佈局, ECharts 規則）不動.
- gate 的必讀清單仍是整個 `.skills/builtin/mcp-data-dashboard` 下所有 `.md`（目前只有 SKILL.md 一份, 1222 行; 是否拆 references 不在本 spec）.

### D8. spike 去留

保留在 `deepagent-service/spike/mcp-shell/`, 維持 THROWAWAY 標記. 合流後依 D5 拿掉 bridge 的 `UNWRAP_RESULT` 旋鈕（固定 raw）, 然後**手動再跑一次**作為合流驗收（README「實際跑法」一節的指令）, 把新一組快照放進 `out/`, 舊三張刪掉. 驗收重點就是 S3 那個坑: 模型收到 `Raw response shape` 之後, 第一版 `dashboard.html` 就該讀 `r.data.result`, 不再來回改. 不寫自動化測試（spike 需要 OpenRouter 與真模型）.

### D9. 宿主契約: iframe runtime ↔ 前端 ↔ Java ↔ deepagent ↔ MCP server

D5 定了「`r.data` 是 raw」, 這一節把 raw 從 MCP server 一路送到頁面的每一跳寫成契約. **本 spec 只凍結契約, 不含實作**——Java 與前端的實作另開 plan（datasource spec §11 說的「另開 spec」就是指這裡定的東西）. 凍結的理由: deepagent 側現在就要照這份契約寫 SKILL.md, 回饋文字與 `check_dashboard`, 兩邊不能各寫各的.

四跳與各自唯一的責任:

| 跳 | 誰 | 做什麼 | **不做**什麼 |
|---|---|---|---|
| ① iframe 內 `mcp()` runtime | 前端注入的一段固定 JS | 把呼叫編號, postMessage 給宿主頁, 收到結果找回 handler 呼叫一次 | 不碰 `data`, 不重試, 不快取 |
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
- 注入點: **前端**在 `ArtifactFrame` 組 srcdoc 時, 緊接 CSP `<meta>` 之後、頁面任何 `<script>` 之前插入（與 spike `composeSrcdoc` 相同位置）. 不由 Java 出貨前寫進儲存的 HTML: 儲存的 artifact 維持模型產出的原樣, runtime 有 bug 修前端一次, 所有已發布頁面下次開啟就吃到, 不必重生 dashboard（09-02 options 文件 C 案的維護論點, 在這裡用得上）. 同一段 prelude 也包含現有的 `erd-iframe-error` 回報.
- 只在 artifact 所屬 session 是 connector 模式時注入（Java 在 `GET /api/artifacts/{id}` 的 response 或 artifact DTO 帶 `dataMode: "file" | "connector"`; 前端據此決定）. file 模式的頁面不會有 `mcp` 這個全域, 與現況相同.
- `check_dashboard` 已禁止頁面自己定義 `mcp`, 所以 runtime 與頁面不會撞名.

**② 宿主頁 bridge（postMessage 協定）.**

| 方向 | 訊息 | 欄位 |
|---|---|---|
| iframe → 宿主 | `erd-mcp-call` | `id: string`（頁面內唯一, runtime 自增）, `connector: string`, `tool: string`, `args: object` |
| 宿主 → iframe | `erd-mcp-result` | `id: string`, `result: {data} \| {error:{code,message}}` |
| iframe → 宿主 | `erd-iframe-error` | 既有, 不變 |

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
3. **SSO 只在 header**: 四跳都不進 body, 不進 log, 不進 postMessage.
4. **成功／失敗只有一種形狀**: 頁面永遠收到 `{data}` 或 `{error:{code,message}}`, 沒有第三種; HTTP 層錯誤在 ② 收斂成同形狀.
5. **一次呼叫一次 handler**: runtime 保證; 逾時後遲到的結果丟棄.

**本 spec 凍結／留給實作 plan 的分界.** 凍結: 訊息名稱與欄位, 端點路徑與 body/回應形狀, 錯誤碼集合, prelude 注入點與注入條件, 五條不變量. 留給 plan: 前端 in-flight 上限與逾時數值、loading 骨架、`dataMode` 欄位落在哪個 DTO、Java 端 `ConnectorSpec` 與 SSO header 的共用抽取方式、deepagent 端點的 pydantic schema 與測試、分享頁（非 owner 的 viewer）的存取規則——後者是分享功能自己的 spec, 本端點只承諾「與 `GET /api/artifacts/{id}` 同一條規則」, 分享功能改那條規則時這裡自動跟著.

**SKILL.md 據此補的兩句**: `r.error.code` 存在且是上表之一, 頁面可依 code 決定要不要給重試按鈕; `TOOL_ERROR` 的 `message` 要原樣顯示給 viewer, 不要吞掉.

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
    W->>W: call → unwrap_envelope (記下走過的 path) → land_response (暫存目錄)
    W->>G: append {connector, tool, args, unwrap_path, envelope_keys, columns, row_count, landed}
    W-->>L: 表名, 欄位, 前 20 列預覽, Raw response shape (讀列路徑)
    L->>L: run_sql ... 寫 dashboard.html (mcp('sales','list_orders',{days}, r => r.data.result...))
    L->>K: check_dashboard
    K->>G: load() (本 session 所有輪)
    K-->>L: OK 或 findings (含 handler 讀錯層退件)
    Note over C: 輪末刪暫存目錄; connector_calls.jsonl 隨 workspace zip 保留
```

## 7. 檔案影響（選項 A + 全部建議值）

| 檔案 | 動作 |
|---|---|
| `app/engine/connector_call_log.py` | 新增: `ConnectorCallLog(path)`, `append`, `load`（單行損毀跳過） |
| `app/engine/api_snapshot.py` | `unwrap_envelope` 回傳多帶 `unwrap_path`; `LandingResult` 多 `unwrap_path`（邏輯不變） |
| `app/agent/connectors/wrapper.py` | 取 datasource 版; `build_connector_tools` 加 `call_log: ConnectorCallLog \| None`; `_execute` 成功與 `EmptyLandingError` 兩條路徑各 append 一筆; `_format_landing_feedback` 多一段 `Raw response shape` |
| `app/agent/tools/check.py` | 改讀 `ConnectorCallLog.load()`; 新增「handler 讀的層對上 `unwrap_path`」lint; 移除 `replay_manifest` import |
| `app/agent/chat_turn.py` | 取 datasource 版; connector 分支建 `ConnectorCallLog`, 傳給 `build_connector_tools` 與 `build_check_tools`; `build_agent` 加 `dashboard_skill_root` |
| `app/agent/graph.py`, `middleware.py` | datasource 版 + `dashboard_skill_root` / `skill_relative_root` |
| `app/agent/prompts.py` | 依 D6 改兩段文字 |
| `skills/mcp-data-dashboard/SKILL.md` | 依 D5, D7 改文字 |
| `spike/mcp-shell/bridge.py`, `README.md`, `out/` | 依 D5 拿掉 `UNWRAP_RESULT`; 依 D8 重跑換快照 |
| `tests/test_api_snapshot.py` | 補: 五種 raw 形狀各自回正確的 `unwrap_path` 與 `envelope_keys`（D5 表格逐列） |
| `tests/test_check_dashboard.py` | 改用 `ConnectorCallLog` 建 fixture; 新增: 0 列紀錄可通過、跨輪紀錄可通過、`unwrap_path=["result"]` 時 `r.data.map(` 退件而 `r.data.result.map(` 通過、`unwrap_path=[]` 時 `r.data.result` 退件、handler 參數名非 `r` 也能掃 |
| `tests/test_connector_wrapper.py` | 新增: 成功落表寫一筆（含 `unwrap_path`）、0 列寫 `landed:false`、`ConnectorToolError` 不寫、`call_log=None` 不寫、append 失敗不影響回傳; 回饋文字含 `Raw response shape` 且路徑句與 raw 形狀一致（三種形狀各一） |
| `tests/test_chat_turn_connectors.py` | 新增: connector 模式 workspace 下有 `connector_calls.jsonl`, 第二輪仍讀得到第一輪的紀錄 |
| `tests/test_prompts.py` | 依 D6 改斷言 |
| `tests/test_graph.py` | 兩邊合併: 無 `recorder`, 有 `dashboard_skill_root` 案例 |
| `tests/test_connector_call_log.py` | 新增: append/load 往返, 損毀行跳過, 檔案不存在回空 |
| 本 spec 與 `2026-09-04-mcp-dashboard-verification-options.md` | 後者的 level 2 表格把 `replay/landings.jsonl` 改成 `connector_calls.jsonl` |

Java 與前端: **本 spec 的合流 PR 零改動**. D9 的四跳（前端 prelude 與 bridge, Java 代理端點, deepagent `/tool-call`）契約已凍結, 實作另開 plan; deepagent 側的 SKILL.md、回饋文字與 `check_dashboard` 在合流 PR 裡就照 D9 的形狀寫.

## 8. 測試與完成條件

- `cd deepagent-service && uv run ruff check . && uv run pytest -q` 全綠; datasource 側 35 個測試檔與 dashboard 側新增的測試都在.
- 手動: 依 D8 跑一次 spike, 確認 (1) 模型產出的 handler 依回饋的 `Raw response shape` 讀 `r.data.result`（mock server 的 list 型 tool）且第一版就對, (2) 第二輪只改版面時不重打 connector 且 `check_dashboard` 回 OK.
- 合流 PR 描述附本 spec 連結與第 10 節的拍板結果; gate 照專案規則（`./mvnw test` 不受影響但仍跑, opus 全分支終審）.

## 9. 非目標

- 宿主端四跳的**實作**（前端 prelude 與 bridge, Java 代理端點, deepagent `/tool-call`）——契約在 D9 凍結, 實作另開 plan. per-artifact 的允許 tool 清單（D9 ③ 提到的擴充點）與分享頁 viewer 的存取規則也不在本文.
- level 3 headless render（09-04 spec 已 deferred, 維持）.
- 用 `columns` 驗 handler 欄位名（D2 只記, D4 不驗）.
- 跨輪保留落表（datasource §10 的觀察指標未達）.
- 拆 `mcp-data-dashboard/SKILL.md` 成 references.
- node 進 image（09-04 spec 的既有結論, 不變）.

## 10. 待拍板

- [ ] **D0** 合流方式: A（datasource 為底, dashboard 重落）/ B / C
- [ ] **D1** 呼叫紀錄跨輪保留在 workspace（b）
- [ ] **D2** 紀錄欄位含 `unwrap_path`/`envelope_keys`/`columns`/`row_count`/`landed`; 0 列也記; 失敗不記; 檔名 `connector_calls.jsonl` 放 workspace 頂層
- [ ] **D3** 以 `ConnectorCallLog` 注入 wrapper（b）, `None` 不記
- [ ] **D4** `check_dashboard` 跨輪比 arg keys, 加驗 handler 讀的層對上 `unwrap_path`, 不驗欄位
- [x] **D5** `r.data` = raw `structuredContent`, 宿主不拆封; wrapper 回饋明講拆封配方（`Raw response shape` 段）並記進紀錄; spike bridge 固定 raw ——**2026-09-08 使用者定案**
- [x] **D6** 兩段 prompt 改措辭: qN 給對話用, dashboard 走 `mcp()`; `inject_results` 不動; 產物生命週期表見 D6 ——**2026-09-08 使用者定案**
- [ ] **D7** 保留 `dashboard_skill_root`; SKILL.md 依 D5/D7 改
- [ ] **D8** spike 保留為 throwaway, 合流後手動重跑一次換快照
- [ ] **D9** 宿主契約: 前端注入 runtime 與 bridge（`erd-mcp-call`/`erd-mcp-result`, 驗 `event.source`）; Java `POST /api/artifacts/{id}/mcp-call`（connector 層級白名單, tool 層級 v1 不擋, viewer SSO 轉發）; deepagent `POST /tool-call` 不經模型、不拆封; 固定錯誤碼集合; `data`/`args` 原樣直通

拍板後: 本節改成「已定案」並把結果寫進第 11 節, 再用 `writing-plans` 產 `docs/superpowers/plans/2026-09-XX-mcp-dashboard-on-autoland.md`.

## 11. 決策紀錄

| 日期 | 決定 | 理由 |
|---|---|---|
| 09-08 | `r.data` 到頁面是 raw, 拆封配方由 wrapper 明講給模型並記進呼叫紀錄, `check_dashboard` 據此驗 handler 讀對層（D5） | 寫 JS 處理 raw 回傳值的是模型, 它必須知道 DuckDB 的表是 raw 經過什麼處理來的; 把拆封藏在宿主端只是把知識缺口搬到 Java／前端, 還多一份要同步的程式碼 |
| 09-08 | 宿主四跳的契約在本 spec 凍結（D9）, 實作另開 plan | deepagent 側的 SKILL.md／回饋文字／`check_dashboard` 現在就要照契約寫, 不能等 Java／前端實作時再定 |
| 09-08 | runtime prelude 由前端在 srcdoc 組裝時注入, 不寫進儲存的 HTML | runtime 修一次全部頁面生效, 儲存的 artifact 維持模型原樣 |
| 09-08 | connector 模式 qN 只供對話回答, dashboard 走 `mcp()` 現抓; 對話期資料只活本輪, 跨輪只留 qN 結果與呼叫 metadata（D6） | 三種產物三種生命週期要一眼分得開, 否則 prompt 與 skill 會再次互相拉扯 |
| 09-08 | 其餘 D0–D4, D7–D8（待填） | |
