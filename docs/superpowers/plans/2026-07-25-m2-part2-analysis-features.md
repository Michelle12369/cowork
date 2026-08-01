# M2 Part 2:analysis 功能(profiling/具名方法/結果表格/dashboard renderer)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** analysis 模式從「純文字回答」升級為完整分析體驗:上傳後自動 profiling 開場、3σ/離群具名方法、對話中結構化結果表格(含查詢意圖小卡)、以及 LLM 產 spec JSON → 確定性 renderer 組 ECharts dashboard(LLM 全程不寫 HTML、不產數字)。

**Architecture:** agent-service 先固化 §15 的 `engine/`(零 langchain import,Ruff 強制)vs `agent/` 分層,功能長在正確的縫上。結果表格走新的 `TABLE` AgentEvent(python→java→前端,tableId 可定址=M3 釘選前提);dashboard HTML 走 python 內部 `DASHBOARD_HTML` 事件 → Java provider 攔截進 `AgentOutcome.html` → **複用既有** persist→ArtifactEvent→artifact 面板路徑(前端零改動)。

**Tech Stack:** 同 Part 1。前端新增元件僅表格/意圖小卡(antd Table)。

## 權威文件

- Spec §4(工具清單)、§7(品質策略:LLM 不寫 HTML、不產數字、intent 覆述)、§8(M2 UI 清單)、§9(M2 範圍)、§15(engine/agent 分層 + banned-import)、§16.2.1(renderer 產物不需 harden)、§17.2(M2 前端結構不動)、§17.3(釘選建立在 spec 區塊 id)、§17.4(型別同步紀律)
- Part 1 計畫:`docs/superpowers/plans/2026-07-25-m2-spi-narrowing-and-debts.md`(已完成,Tasks 1-4)

## 已拍板決策(controller 與使用者確認)

1. **結果表格=新結構化 `TABLE` AgentEvent**,非 markdown——M3 釘選需要可定址 id(§17.3 同一原則)
2. **查詢意圖=`run_sql` 的必填 `intent` 參數**,隨 TABLE 事件送出——工具 schema 強制模型覆述意圖(§7),不靠 prompt 懇求
3. **M2 圖表落點=既有 artifact 面板**(§17.2:不新增畫面)
4. **renderer 的資料一律以 `tableId` 引用 session 內 result store**,LLM spec 中絕不出現數字陣列(§7「LLM 絕不產出任何數字」的機制化)
5. **表格 M2 為 live-only**(不持久化;reload 後只剩文字回答,答案文字本身含數字所以資訊不失)。持久化與 M3 釘選一起設計——屆時需要 V10 migration,一次做對。公司 Oracle 禁 CLOB 對策 backlog 也在那時一併考慮。

## Global Constraints

(與 Part 1 相同,全文照抄生效)
- Java 17;constructor injection;`@RequiredArgsConstructor`;DTO 一律 record;例外類放 `com.erd.cowork.exception`
- `@Slf4j`;NEVER log API key、完整 prompt/HTML、使用者資料內容
- 變數/參數 NEVER 1–2 字元;測試命名 `methodName_condition_expectedBehavior`
- google-java-format 由 hook 自動執行
- Python:所有函式 type hints;`uv run ruff check .` 通過才 commit;所有指令 `uv run <cmd>`
- 前端:嚴格 TS,NEVER `any`;明確 return type;`import type`;元件行為測試(NEVER snapshot-only)
- Secrets NEVER 進設定檔
- **SSE JSON 欄位名=與 Java 的線上契約**;新增事件型別 MUST 同一 commit 同步 python/java/`frontend/src/types.ts` 三側 + `sseParser` 測試釘住(§17.4)
- **合併 gate**:backend `./mvnw test`(基準 464,讀 surefire aggregate)+ agent-service `uv run pytest`(基準 29)+ `ruff check` + frontend `npm test`(基準 258)全綠
- **測試 MUST by-construction hang-proof**:pytest-timeout 不在依賴中;任何 await 都要有上限;NEVER await `asyncio.all_tasks()`(Part 1 踩過 anyio 自我死鎖)
- 啟動本地服務驗證前 MUST `lsof -ti:<port>` 確認埠淨空並檢查啟動 log 無 bind 錯誤(Part 1 教訓)

## 給實作者的重要提醒

同 Part 1:本計畫只給簽名、判準與測試意圖,不給大段可照抄實作。與描述不符的實際情況,回報而非硬套。特別是 LangGraph/langchain 行為一律以安裝版實測為準——本 repo 已兩次發現計畫假設與庫實際行為不符。

---

### Task 1:§15 分層固化——engine/ 與 agent/(純搬移 + Ruff 強制)

**Files:**
- Create: `agent-service/app/engine/__init__.py`、`agent-service/app/agent/__init__.py`(空)
- Move: `app/duck.py` → `app/engine/duck.py`;`app/tools.py` → `app/engine/query.py`
- Create: `app/agent/graph.py`(自 `app/agent.py` 搬 `build_agent`)、`app/agent/toolbelt.py`(自 `app/agent.py` 搬 @tool 包裝與 `TOOL_STEP_TITLES`)、`app/agent/prompts.py`(搬 `SYSTEM_PROMPT`)
- Delete: `app/agent.py`、`app/tools.py`
- Modify: `app/main.py`(import 更新)、`agent-service/pyproject.toml`(Ruff banned-api)
- Test: 既有測試全部照過(import 路徑更新);新增 `tests/test_layering.py`

**Interfaces:**
- Produces: `app.engine.duck`(原樣)、`app.engine.query.get_schema/run_sql`(原樣,Task 2 再改)、`app.agent.graph.build_agent`、`app.agent.toolbelt`、`app.agent.prompts.SYSTEM_PROMPT`
- **單向依賴規則(§15.2)**:`agent/` 可 import `engine/`;`engine/` NEVER import `agent/`、langchain、langgraph、langfuse

- [x] **Step 1:搬移與 import 更新(行為零改變)**

純搬移:函式體逐字不動。`main.py` 的 import 改指新路徑。注意 `app/agent.py`(檔案)變成 `app/agent/`(package)——確認無殘留 `import app.agent` 歧義。

- [x] **Step 2:Ruff banned-api 強制**

`pyproject.toml` 增加 `[tool.ruff.lint.flake8-tidy-imports.banned-api]`:全域禁 `langchain`、`langchain_core`、`langchain_openai`、`langgraph`、`langfuse`(訊息註明「engine 層禁用——§15.2」),再以 `[tool.ruff.lint.per-file-ignores]` 對 `app/agent/**`、`app/main.py`、`tests/**` 放行 `TID251`。並在 `lint.select` 加入 `TID`。以「在 `engine/duck.py` 塞一行 `import langchain`」驗證 ruff 會紅,驗完移除。

- [x] **Step 3:layering 測試**

`tests/test_layering.py`:走訪 `app/engine/**` 的 AST(`ast.parse`),斷言無任何 import 以 `langchain`/`langgraph`/`langfuse`/`app.agent` 開頭——CI 之外的第二道防線,並涵蓋 banned-api 管不到的 `app.agent` 內部依賴。

- [x] **Step 4:全套 + commit**

Run: `cd agent-service && uv run pytest -q && uv run ruff check .`
Expected: 29+1 綠(僅新增 layering 測試;既有測試只改 import)

```bash
git add agent-service && git commit -m "refactor(agent-service): solidify engine/agent layering with ruff-enforced one-way imports"
```

---

### Task 2:結構化 QueryResult + preview_data

**Files:**
- Modify: `app/engine/query.py`
- Modify: `app/agent/toolbelt.py`(markdown 渲染移入此處)
- Test: `tests/test_query.py`(自 `test_tools.py` 改名擴充)

**Interfaces:**
- Produces:
  - `QueryResult`(frozen dataclass:`columns: list[str]`、`rows: list[list[object]]`、`truncated: bool`、`error: str | None`)——`error` 非 None 時其餘為空
  - `engine.query.run_sql(connection, sql) -> QueryResult`(**不再回傳 markdown**;never-raise 契約改為「一律回傳 QueryResult,失敗時 error 欄位帶 `SQL_ERROR:` 前綴訊息」)
  - `engine.query.preview_data(connection, table_name, limit=10) -> QueryResult`(前 N 列;table_name 以 identifier 白名單驗證,同 duck.py 的 `\w+` 規則——LLM 可控輸入,NEVER 裸插字串)
  - `agent.toolbelt.render_markdown(result: QueryResult) -> str`(LLM 看的格式:表格或 `SQL_ERROR:` 字串;截斷標注 `(truncated to 200 rows)`——**與現行 run_sql 輸出逐字相同**,模型行為不受影響)

- [x] **Step 1:TDD**——既有 test_tools 的 4 條斷言改斷 `QueryResult` 欄位;新增 preview_data 的正常/超限/惡意表名(`"`;`;`)測試;render_markdown 輸出與現行字串逐字節相等(拿 Part 1 的輸出當 golden)
- [x] **Step 2:實作**——`fetchmany(MAX+1)` 截斷邊界、per-call cursor(並行安全,Part 1 修過的)全部保留
- [x] **Step 3:`uv run pytest -q && uv run ruff check .` 綠 + commit**

---

### Task 3:engine/methods 具名方法 + profiling

**Files:**
- Create: `app/engine/methods/__init__.py`、`app/engine/methods/trend_3sigma.py`、`app/engine/methods/flag_outliers.py`
- Create: `app/engine/profiling.py`
- Test: `tests/test_methods.py`、`tests/test_profiling.py`

**Interfaces:**
- Produces(全部純函式,connection in / QueryResult 或 str out,零 LLM 依賴):
  - `trend_3sigma(connection, table, value_column, time_column) -> QueryResult`(依時間欄排序後計算均值±3σ,標記越界列;SQL 由函式內建模板組成,table/column 名走 `\w+` 白名單)
  - `flag_outliers(connection, table, column, sigma=2.5) -> QueryResult`(z-score 超過 sigma 的列)
  - `profile_source(connection, table) -> str`(人類可讀摘要:列數、每欄 dtype/null 數/distinct 數,數值欄 min/max/mean,類別欄 top 3 值——全部由 SQL 算,單次呼叫掃描次數 O(欄數) 上限)
- 失敗語意同 run_sql:回傳 error 欄位或 `PROFILE_ERROR:` 字串,絕不拋例外

- [x] **Step 1:TDD**——固定 CSV fixture(含一個明顯離群值、一個含 null 的欄),斷言離群列被標出、profile 摘要含正確列數與欄名;惡意 table/column 名被拒
- [x] **Step 2:實作 + 綠 + commit**

---

### Task 4:toolbelt 掛新工具 + intent 必填 + 開場 prompt

**Files:**
- Modify: `app/agent/toolbelt.py`、`app/agent/prompts.py`、`app/agent/graph.py`
- Test: `tests/test_chat.py`(scripted-model 形態擴充)

**Interfaces:**
- Produces(LLM 可見工具面,名稱即契約):
  - `run_sql(sql: str, intent: str)`——**intent 必填**:「用一句使用者語言覆述這條 SQL 要查什麼」(§7 判準:供人抓語意錯);docstring 明示
  - `preview_data(table: str)`、`profile_source(table: str)`、`trend_3sigma(table, value_column, time_column)`、`flag_outliers(table, column, sigma)`
  - `TOOL_STEP_TITLES` 補齊新工具的中文標題(預覽資料/剖析資料/趨勢 3σ/標記離群)
- prompts:SYSTEM_PROMPT 增補——首輪(history 為空)先對每個掛載表呼叫 `profile_source` 並以摘要開場;所有數字一律出自工具結果,NEVER 自行計算(原句保留)

- [x] **Step 1:TDD**——scripted model 依序呼叫 profile_source→run_sql(帶 intent),斷言:兩個工具 STEP 各自 RUNNING→SUCCESS、標題正確;run_sql 缺 intent 時 LangChain 參數驗證產生 on_tool_error → 該步 ERROR(Part 1 已有此路徑測試形態可仿)
- [x] **Step 2:實作 + 全綠 + commit**

---

### Task 5:TABLE 事件(python 端)+ result store

**Files:**
- Modify: `app/main.py`、`app/agent/toolbelt.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Produces(wire 契約,Task 6 三側同步的依據):
  ```json
  {"type":"TABLE","tableId":"tbl_<runid>","intent":"<一句話>","columns":[...],"rows":[[...]],"truncated":false}
  ```
  - 發射時機:`run_sql`/`trend_3sigma`/`flag_outliers` 的 `on_tool_end` 且結果非 error(具名方法 intent 用固定描述,如「3σ 趨勢分析」);`preview_data`/`profile_source` **不發**(探查類,結果進 LLM 上下文即可,UI 不需表格)
  - rows 上限沿用 MAX_RESULT_ROWS=200(wire 與 UI 同限)
  - **result store**:request-scoped `dict[tableId, QueryResult]`(掛在本次 /chat 的執行 context,隨請求結束丟棄)——Task 8 的 renderer 憑 tableId 取真實資料
- 實作機制注意:tool 執行在 executor 執行緒,事件迴圈在主線程——結構化結果從 tool 傳到 SSE 層的通道 MUST 執行緒安全(建議:toolbelt 把 QueryResult 存進 thread-safe dict,keyed by tool run 的穩定 id,on_tool_end 時由主迴圈取出發射;實測 astream_events 的 on_tool_end payload 是否帶得到對應 key,不符就回報)

- [x] **Step 1:TDD**——scripted 工具呼叫後,SSE 事件序列含 TABLE(欄位齊全、rows 與 fixture 一致、intent 傳遞正確);error 的 run_sql 不發 TABLE;profile_source 不發 TABLE
- [x] **Step 2:實作 + 全綠 + commit**

---

### Task 6:TABLE 三側同步(Java + 前端)

**Files:**
- Create: `backend/.../agent/event/TableEvent.java`
- Modify: `backend/.../agent/event/AgentEvent.java`(sealed permits + @JsonSubTypes 註冊 `TABLE`)
- Modify: `frontend/src/types.ts`、`frontend/src/utils/sseParser.ts`(若 union 窮舉需要)
- Create: `frontend/src/components/chat/ResultTable.tsx`(antd Table + 意圖小卡:intent 一句話顯示在表格上方)
- Modify: `frontend/src/components/chat/MessageBubble.tsx` 或 `MessageList.tsx`(live 訊息流中渲染 TABLE 事件;掛進 `useAgentStream` 的事件累積)
- Test: `backend` TableEvent 反序列化測試(仿 LangGraphAnalysisProviderTest 的 toEvent 形態)、`frontend/src/utils/sseParser.test.ts` 釘 TABLE 形態、`ResultTable.test.tsx` 行為測試(渲染欄/列/intent/truncated 標注)

**Interfaces:**
- Consumes: Task 5 的 wire 契約
- Produces: `TableEvent(String tableId, String intent, List<String> columns, List<List<Object>> rows, boolean truncated)`;前端 `StreamEvent` union 增 TABLE
- Java 側零翻譯邏輯:@JsonSubTypes 註冊後 provider 的 `toEvent` 原樣通過;dashboard 模式 provider 永不發 TABLE,無影響
- **live-only(決策 5)**:orchestrator 不持久化 TABLE(不動 stepsJson/訊息 schema);前端只在 live 流渲染,history 重載無表格——`useAgentStream` 累積即可,不進 sessionDetail

- [x] **Step 1:TDD 三側**(同一 commit:§17.4)
- [x] **Step 2:實作 + 三邊全綠 + commit**

---

### Task 7:dashboard spec schema + renderer(純 python)

**Files:**
- Create: `app/engine/dashboard_spec.py`(pydantic models)、`app/engine/renderer.py`
- Test: `tests/test_renderer.py`

**Interfaces:**
- Produces:
  - spec(pydantic,`DashboardSpec`):`title: str`、`blocks: list[Block]`;Block 為 discriminated union:`ChartBlock(id, type: Literal["bar","line","pie"], title, source_table_id, x_column, y_column)`、`TableBlock(id, title, source_table_id)`、`StatBlock(id, title, source_table_id, value_column, agg: Literal["sum","mean","max","min","count"])`。**區塊 id 必填且唯一**(§17.3 釘選前提)
  - `render_dashboard_html(spec: DashboardSpec, tables: dict[str, QueryResult]) -> str`——自含 HTML(Tailwind + ECharts **CDN 版本與 java 端 CdnRewriter resolveRules 認得的一致**,實作時現場核對 `artifact/CdnRewriter`);資料由 `tables[source_table_id]` 注入為 inline JSON;每個區塊渲染為帶 `data-block-id="<id>"` 的節點
  - `validate_spec(raw: dict, known_table_ids: set[str]) -> DashboardSpec`——pydantic 驗證失敗或引用不存在的 tableId 時,拋帶人類可讀訊息的 `SpecValidationError`(Task 8 轉成 `SPEC_ERROR:` 字串回饋模型)
- **HTML 內 NEVER 引用 `window.__ERD_DATA__`**——資料已 inline;此不變量正是 Task 9 assembler 跳過注入的判準

- [x] **Step 1:TDD**——合法 spec 渲染出含正確 ECharts option JSON 與 data-block-id 的 HTML;非法 spec(未知 type/缺欄/未知 tableId/重複 block id)各自產生可讀錯誤;渲染輸出不含 `__ERD_DATA__` 字樣
- [x] **Step 2:實作 + 綠 + commit**

---

### Task 8:render_dashboard 工具 + HTML 回流 Java

**Files:**
- Modify: `app/agent/toolbelt.py`、`app/agent/prompts.py`、`app/main.py`
- Modify: `backend/.../agent/provider/analysis/LangGraphAnalysisProvider.java`
- Test: `tests/test_chat.py`、`LangGraphAnalysisProviderTest.java`

**Interfaces:**
- LLM 工具:`render_dashboard(spec: dict)`——docstring 說明區塊型別與「`source_table_id` 必須是本次對話中 run_sql/具名方法產生的 tableId;NEVER 在 spec 內放數字資料」。驗證失敗回傳 `SPEC_ERROR: <訊息>` 字串(模型自我修復,同 SQL_ERROR 模式,重試上限交給既有 recursion limit)
- python→java 內部事件(**不進前端**):`{"type":"DASHBOARD_HTML","html":"<...>"}`,在 ANSWER 前發射(成功渲染時)
- Java 端:`LangGraphAnalysisProvider.toEvent` 攔截 `DASHBOARD_HTML`——**不**反序列化為 AgentEvent、不往下游 Flux 發;存入 AtomicReference,`outcome` supplier 回傳 `new AgentOutcome(answerText, capturedHtml, null)`。此後 orchestrator 既有路徑自動接手(persist → ArtifactEvent(artifactId,title) → 前端 artifact 面板),**前端零改動**
- 注意:攔截需要在 `.map(toEvent)` 之前或之中過濾——實作時確認不破壞既有 malformed-payload → ErrorEvent 行為;DASHBOARD_HTML 的 html 欄位 NEVER 進 log

- [x] **Step 1:TDD python**——scripted model 呼叫 render_dashboard(引用先前 run_sql 的 tableId),斷言 DASHBOARD_HTML 事件在 ANSWER 前出現且 html 含 data-block-id;spec 引用未知 tableId 時工具回傳 SPEC_ERROR 字串且無 DASHBOARD_HTML 事件
- [x] **Step 2:TDD java**——DASHBOARD_HTML payload 不產生下游事件、outcome.html 攜帶內容;既有 malformed/TOKEN/STEP 測試不變
- [x] **Step 3:實作兩側 + 兩邊全綠 + commit**

---

### Task 9:assembler 條件跳過注入 + E2E 煙霧

**Files:**
- Modify: `backend/.../artifact/ArtifactAssembler.java`(或其呼叫端,現場判斷)
- Test: `ArtifactAssemblerTest`(既有類擴充)

**Interfaces:**
- 判準:`rawHtml` 不含字串 `__ERD_DATA__` 時**跳過資料注入**(theme/vendor 相關注入照舊——實作時先讀 `head-inject.vm` 與 `CdnRewriter` 確認哪些部分屬資料、哪些屬主題,只跳過資料部分並以測試釘住兩種 HTML 的行為)
- 理由:analysis renderer 的 HTML 自含資料(Task 7 不變量),再注入全量原始檔案資料是純浪費(50-300KB 級)且概念錯誤;dashboard 模式 HTML 一律含 `__ERD_DATA__` 引用,行為不變

- [x] **Step 1:TDD**——含 `__ERD_DATA__` 的 html:注入照舊(既有測試不動);不含者:輸出不含資料 script block、theme 注入照舊
- [x] **Step 2:實作 + backend 全綠**
- [x] **Step 3:E2E 煙霧(真 LLM,兩輪)**——(a) 上傳 CSV 首輪:自動 profiling 開場(回答含欄位摘要);(b) 問分析問題:TABLE 事件出現、意圖小卡內容合理、表格數字與獨立 DuckDB ground truth 一致;(c) 要求畫圖:artifact 面板出現 dashboard、圖表數字正確、HTML 內無 `__ERD_DATA__`;(d) 前端 UI 實際點開驗證。埠淨空紀律照 Global Constraints。
- [x] **Step 4:三邊 gate 全綠 + commit + push**

---

## 驗收(使用者可操作)

1. 上傳 CSV → agent 不用等問題就以資料摘要開場(欄位/分佈/列數)
2. 問「哪台機台不良率最高」→ 意圖小卡(一句話覆述)+ 結構化表格,數字可信(工具算的)
3. 「幫我標離群值」→ flag_outliers 具名方法步驟 + 表格
4. 「畫成圖表」→ artifact 面板出現 ECharts dashboard;LLM 全程沒寫過 HTML、沒複述過數字
5. 步驟條全程真實步驟(Part 1 成果延續)

## Self-Review 紀錄

- Spec 覆蓋:§9 M2 行的 profiling(T3/T4)、preview/具名方法(T2/T3/T4)、結果表格 UI(T5/T6)、dashboard spec renderer(T7/T8/T9)、§15 分層+banned-import(T1)✔;§8 M2 三列(結果表格/查詢意圖小卡/isDisplayableStep 刪除)→ T6/T6/Part1 已完成 ✔
- 型別一致:`QueryResult`(T2)→ T3 方法回傳 → T5 result store → T7 renderer 輸入 ✔;`tableId`(T5 wire)→ T6 TableEvent → T7 source_table_id → T8 known_table_ids ✔
- 決策 5(live-only)已在開頭標明給使用者可見
- 刻意不給大段代碼;langgraph 行為一律實測(T5 的執行緒通道、T8 的攔截點都標了「不符就回報」)
