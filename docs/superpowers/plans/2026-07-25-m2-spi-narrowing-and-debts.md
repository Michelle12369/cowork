# M2 Part 1:SPI 收窄與兩筆技術債 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把繞著「產 HTML dashboard」長出來的 provider SPI 收窄成模式無關的介面,並償還 `stepKey` 字首編碼與「STEP 靠模型自報」兩筆技術債——讓 M2 的功能任務(profiling、結果表格、dashboard spec renderer)不必再繞過既有形狀。

**Architecture:** 對話層(session/訊息持久化、歸屬、SSE、cancel)留在 `AgentOrchestrator` 且對 agent 模式無知;生成編排各模式自帶。介面切成窄的 `AgentProvider` 與只有「LLM 直接寫 HTML」模式才實作的 `DashboardAgentProvider`(帶 `harden()` 生成期修復)。縫劃在**要不要生成期修復**,不是「產不產 artifact」——理由見 spec §16.2.1。

**Tech Stack:** Java 17 / Spring Boot 3.x / Reactor / Lombok / JUnit 5 + Mockito + AssertJ;Python 3.11+ / uv / Ruff / FastAPI / LangGraph;React 18 + TypeScript + Vitest。

## 權威文件

- Spec:`docs/superpowers/specs/2026-07-24-data-insight-agent-design.md` §16(Java 側分層)、§16.2.1(縫為何劃在修復)、§16.4(兩筆債)、§16.5(逾時與 heartbeat)、§17.4(型別同步紀律)
- 前一里程碑計畫:`docs/superpowers/plans/2026-07-24-data-insight-agent-m1.md`

## Global Constraints

- Java 17(NEVER 用 18+ API);一律 constructor injection;`@RequiredArgsConstructor`;NEVER `@Autowired` field injection
- DTO 一律 Java record;例外類放 `com.erd.cowork.exception`
- `@Slf4j`;NEVER 手寫 Logger;NEVER log API key、完整 prompt/HTML、使用者資料內容
- 變數/參數/lambda 參數 NEVER 用 1–2 字元名稱(`id` 等 domain 語彙除外);迴圈計數器用 `index`/`rowIndex`
- 測試命名 `methodName_condition_expectedBehavior`
- google-java-format 由 hook 自動執行,勿手動改格式風格
- Python:所有函式有 type hints;`uv run ruff check .` 通過才 commit;所有指令 `uv run <cmd>`
- 前端:嚴格 TypeScript,NEVER `any`;function MUST 有明確 return type;type-only import 用 `import type`
- Secrets NEVER 進設定檔;一律 env vars
- **合併 gate**:`cd backend && ./mvnw test` 全綠 + `cd agent-service && uv run pytest && uv run ruff check .` 全綠 + `cd frontend && npm test` 全綠

## 給實作者的重要提醒(來自 M1 的教訓)

M1 計畫內嵌了大量可直接複製的程式碼,結果**該計畫的範例碼本身帶有 5 個真實缺陷**(SQL 注入、契約違反、同步拋例外、事件端到端不可見、並行污染),全靠 review 才擋下。因此本計畫**刻意只給簽名、判準與測試意圖,不給大段可照抄的實作**。遇到與計畫描述不符的實際情況,**回報而非硬套**。

---

### Task 1:`ExtractionResult` → `AgentOutcome` 純機械更名

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/model/AgentOutcome.java`
- Delete: `backend/src/main/java/com/erd/cowork/agent/extraction/ExtractionResult.java`
- Modify: `backend/src/main/java/com/erd/cowork/agent/provider/ProviderResult.java`(欄位 `extraction` → `outcome`)
- Modify: 所有引用點(main 13 處、test 13 個檔案)——由編譯器列舉
- Test: 既有測試套件(不新增測試;本任務行為零改變)

**Interfaces:**
- Produces: `com.erd.cowork.agent.model.AgentOutcome(String answerText, String html, List<ClarifyingQuestion> questions)`;`ProviderResult(Flux<AgentEvent> events, Supplier<AgentOutcome> outcome)`

**為什麼改名**(寫進 class javadoc):analysis 模式的 `html` 由確定性 renderer 產生,不是從 LLM 文字「抽取」出來的,`ExtractionResult` 這個名字會說謊。與先前 `HtmlExtractionHelper` → `ResponseExtractionHelper` 同一個理由。

- [x] **Step 1:建立新 record 並刪除舊的**

`AgentOutcome` 放 `com.erd.cowork.agent.model`(與 `AgentRequest`/`AgentFileContext`/`HistoryMessage`/`ClarifyingQuestion` 同包)。**NEVER 留在 `agent.extraction` 包**——包名會重蹈同一個謊。欄位、順序、型別與原 `ExtractionResult` 完全相同。

- [x] **Step 2:編譯,讓編譯器列舉所有引用點**

Run: `cd backend && ./mvnw -q -o compile 2>&1 | head -40`
Expected: 出現 `ExtractionResult` 找不到符號的錯誤清單

已知 main 引用點(供交叉核對,以編譯器輸出為準):`ResponseExtractionHelper:147,150,152`、`AgentOrchestrator:8,371`、`ProviderResult:4,8`、`DashboardAgentProvider:3,36`、`RepairResult:4,40`、`HardenedOutput:9`(javadoc)、`QuestionEvent:10`(javadoc)、`LangGraphAnalysisProvider:8,140`、`InternalCodegenProvider:8,86,98`、`ArtifactRepairer:3,82`、`GenerationRepairer:3,104,181`、`OpenAICompatibleProvider:7,165`、`GenerationRepairGuard:7,107`

- [x] **Step 3:逐一改名,含 `ProviderResult.extraction` → `outcome`**

`.extraction()` 呼叫點:`AgentOrchestrator:371`、`ArtifactRepairer:82`、`GenerationRepairer:104,181`、`InternalCodegenProviderTest:89`。
javadoc 內的型別提及也要改(`HardenedOutput`、`QuestionEvent`、`RepairResult.passthrough` 的說明)。

- [x] **Step 4:改測試(13 個檔案)**

`./mvnw -q -o test-compile` 會列出全部。已知構造點數量供核對:`GenerationRepairGuardTest`(13)、`GenerationRepairerTest`(11)、`AgentOrchestratorRepairTest`(7)、`AgentOrchestratorTest`(5)、`ArtifactRepairerTest`(5)、`MessageControllerErrorTest`(1)、`MessageControllerTest`(1 個 `ProviderResult`)。

- [x] **Step 5:證明是純更名**

Run: `cd backend && git diff -U0 | grep -E '^[+-]' | grep -viE 'extraction|AgentOutcome|outcome|^[+-]{3}' | head -20`
Expected: **無輸出**(除 import 行重排)。有輸出就表示混進了行為變更——必須查清楚,不可放行。

- [x] **Step 6:全套測試 + commit**

Run: `cd backend && ./mvnw -q test`
Expected: BUILD SUCCESS,734 綠(與改名前同數字——本任務不新增也不刪除測試)

```bash
git add backend
git commit -m "refactor(backend): rename ExtractionResult to AgentOutcome"
```

---

### Task 2:SPI 收窄——`AgentProvider` + `DashboardAgentProvider`

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/provider/AgentProvider.java`
- Modify: `backend/src/main/java/com/erd/cowork/agent/provider/DashboardAgentProvider.java`
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java`(欄位型別 + `harden()` 呼叫點 ~398)
- Modify: `backend/src/main/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProvider.java`(改實作窄介面)
- Modify: `backend/src/main/java/com/erd/cowork/agent/repair/ArtifactRepairer.java`(注入改 `Optional<DashboardAgentProvider>`)
- Modify: `backend/src/main/java/com/erd/cowork/service/ArtifactRepairService.java`(不可用時拋例外)
- Create: `backend/src/main/java/com/erd/cowork/exception/BrowserRepairUnsupportedException.java`
- Modify: `backend/src/main/java/com/erd/cowork/exception/GlobalExceptionHandler.java`(對映 409)
- Test: `backend/src/test/java/com/erd/cowork/agent/provider/ProviderContextLoadTest.java`(新增)

**Interfaces:**
- Consumes: Task 1 的 `AgentOutcome`
- Produces:
  ```java
  public interface AgentProvider {
    ProviderResult generate(AgentRequest request);
  }

  public interface DashboardAgentProvider extends AgentProvider {
    String PROVIDER_INTERNAL_CODEGEN = "internal-codegen";
    default RepairResult harden(String sessionId, AgentRequest request, AgentOutcome outcome) {
      return RepairResult.passthrough(outcome);
    }
  }
  ```
  `harden()` 的 default passthrough **保留**——`InternalCodegenProvider` 是 dashboard 模式但品質由公司服務端處理,依賴此 default。

**這個任務唯一非機械的部分**:`ArtifactRepairer` 目前注入 `DashboardAgentProvider`。介面收窄後 `LangGraphAnalysisProvider` 不再實作它,**analysis 模式下該 bean 不存在,context 會啟動失敗**。語意上正確(renderer 產的 HTML 不需要瀏覽器修復,見 §16.2.1),但必須明確處理。

- [x] **Step 1:寫失敗測試——三種 provider 值都要能啟動 context**

`ProviderContextLoadTest`:三個巢狀 `@Nested` 類,各自 `@SpringBootTest(properties = "erd.agent.provider=openai-compatible" | "internal-codegen" | "langgraph-analysis")`,各斷言 context 載入成功且 `AgentProvider` bean 恰好一個。analysis 那組**額外斷言 `DashboardAgentProvider` bean 不存在**。

此測試是本任務的安全網:它會在收窄後、修好注入前紅燈。

- [x] **Step 2:跑測試確認會失敗**

Run: `cd backend && ./mvnw -q -o test -Dtest=ProviderContextLoadTest`
Expected: analysis 組 FAIL(`NoSuchBeanDefinitionException: DashboardAgentProvider`)——這正是要修的問題

- [x] **Step 3:建立 `AgentProvider`,`DashboardAgentProvider` 改為 extends**

`LangGraphAnalysisProvider` 改 `implements AgentProvider`;`OpenAICompatibleProvider` 與 `InternalCodegenProvider` 維持 `implements DashboardAgentProvider`。三者的 `@ConditionalOnProperty` 一律不動。

- [x] **Step 4:`AgentOrchestrator` 改注入窄介面並在單點分岔**

欄位型別 `DashboardAgentProvider` → `AgentProvider`(約 line 91)。`harden()` 呼叫點(約 line 398)改為 Java 17 pattern matching 單行分岔:非 dashboard 模式取 `RepairResult.passthrough(outcome)`,下游 `hardenEvents`/`output().join()` 流程**完全不動**(passthrough 的 events 為空 Flux、future 已完成)。

在該行上方留註解說明:**這處顯式 `instanceof` 是 spec §16.2 的刻意選擇,不是 smell**;模式成長到需要第三個分支時才改策略表。

- [x] **Step 5:修復堆疊改為「不可用時明確拒絕」**

`ArtifactRepairer` 注入改 `Optional<DashboardAgentProvider>`(Spring 標準支援,無 bean 時注入 `Optional.empty()`,不依賴 bean 定義順序——**NEVER 用 `@ConditionalOnBean`**,對使用者 bean 有順序脆弱性)。

`ArtifactRepairService.repairFromBrowserErrors` 在 provider 不存在時拋 `BrowserRepairUnsupportedException`;`GlobalExceptionHandler` 對映 **409 CONFLICT**(端點存在但此模式不適用,非資源不存在)。NEVER `Optional.get()` 不先 `isPresent()` 檢查。

- [x] **Step 6:三組 context 測試轉綠 + 全套**

Run: `cd backend && ./mvnw -q -o test -Dtest=ProviderContextLoadTest && ./mvnw -q test`
Expected: 三組全綠;全套 BUILD SUCCESS

- [x] **Step 7:Commit**

```bash
git add backend
git commit -m "refactor(backend): narrow provider SPI to AgentProvider, keep harden on dashboard modes"
```

---

### Task 3:刪除 `stepKey` 字首慣例(技術債 1)

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java`(刪 `DYNAMIC_STEP_KEY_PREFIX` 常數 ~line 76 與 ~line 320 的過濾條件)
- Modify: `backend/src/main/java/com/erd/cowork/agent/provider/analysis/LangGraphAnalysisProvider.java`(**刪除 `d_` 改寫、其常數與 javadoc**,約 line 41-59、223、以及 `applyDynamicStepKeyPrefix` 方法)
- Modify: `frontend/src/components/chat/MessageList.tsx:33-46`(刪 `isDisplayableStep` 與其呼叫端的過濾)
- Test: 既有 `AgentOrchestratorTest`、`LangGraphAnalysisProviderTest`(需刪除 pin 住 `d_` 改寫的測試並改為 pin 新行為)、`frontend/src/components/chat/MessageBubble.test.tsx` 或 `MessageList` 等價測試
- **NEVER 新增** `StepSource` 之類的列舉——見下方查證結果

**Interfaces:**
- Produces: `StepEvent` 形狀**不變**;`stepKey` 自此純粹是識別碼(`StepChain` 的 `key`、last-state-per-key 覆寫),不再編碼任何語意

**為什麼是刪除而非改成語意欄位**(spec §16.4-1 已於 2026-07-25 修正,查證結果):
1. 目前所有 `StepEvent` 發出點只用 `d1`/`d2`/`d3`(codegen 罐頭,`InternalCodegenProvider:91-111`)、`d{n}`(模型標記,`ResponseExtractionHelper:252`)、`d_analysis`、`r1`(修復,`GenerationRepairGuard:62`)——**已無任何地方發 `s*`**
2. `d` 與 `r` 除「要不要顯示」外無語意差異:`StepChain.tsx` 只依 `status` 決定圖示
3. 使用者確認不需相容既有資料

⇒ 兩處過濾器皆為恆真 no-op,刪除即可。日後真需要區分步驟來源再加欄位。

- [x] **Step 1:寫失敗測試——不帶 `d` 前綴的步驟必須被保留與顯示**

後端:在 `AgentOrchestratorTest` 加一條——provider 發出 `stepKey` 為 `analysis`(無 `d` 前綴)的 `StepEvent`,斷言它出現在持久化的 `stepsJson` 中。目前會失敗(被 line ~320 過濾掉)。

前端:斷言 `stepKey` 為 `analysis` 的步驟會被渲染。目前會失敗(被 `isDisplayableStep` 濾掉)。

- [x] **Step 2:跑測試確認失敗**

Run: `cd backend && ./mvnw -q -o test -Dtest=AgentOrchestratorTest`
Run: `cd frontend && npm test -- --run`
Expected: 兩邊各有一條新測試 FAIL

- [x] **Step 3:後端刪除前綴判斷**

`AgentOrchestrator`:刪 `DYNAMIC_STEP_KEY_PREFIX` 常數,line ~320 過濾條件只留 `stepKey() != null`(與 line ~408 的 harden 累積條件一致)。順手更新該處註解——目前寫「Collect dynamic step events (d* keys)」已不再屬實。

`LangGraphAnalysisProvider`:刪除 `d_` 前綴改寫、`DYNAMIC_STEP_KEY_PREFIX` 常數與整段解釋 javadoc、`applyDynamicStepKeyPrefix` 方法;`toEvent` 直接沿用 Python 送來的 `stepKey`。**status 正規化(缺 status → `RUNNING`)保留不動。**

註:`ResponseExtractionHelper:252` 產生的 `"d" + stepCounter` 維持不動——它現在只是一個唯一識別碼產生器,字母 `d` 已無語意,不值得為此改動既有行為。

- [x] **Step 4:前端刪除過濾**

`MessageList.tsx`:刪 `isDisplayableStep`,`onlyDynamicSteps` / `liveOnlyDynamicSteps` 只保留「空陣列 → null」的行為(呼叫端依賴 null 判斷),不再依 `stepKey` 過濾。`types.ts` 不需改動(`StepEvent` 形狀未變)。

- [x] **Step 6:三邊測試**

Run: `cd backend && ./mvnw -q test`
Run: `cd frontend && npm test -- --run`
Expected: 兩邊全綠

- [x] **Step 7:Commit**

```bash
git add backend frontend
git commit -m "refactor: encode step semantics in a named field instead of stepKey prefix"
```

---

### Task 4:STEP 改由 LangGraph 真實編排事件驅動 + heartbeat(技術債 2)

**Files:**
- Modify: `agent-service/app/main.py`(`/chat` 事件迴圈)
- Modify: `agent-service/app/agent.py`(工具名稱 → 步驟標題對照)
- Test: `agent-service/tests/test_chat.py`

**Interfaces:**
- Consumes: Task 3 的成果——`stepKey` 不再編碼語意,Python 可自由選用有意義的 key(如 `tool_run_sql_1`),Java 端不再改寫
- Produces: `/chat` SSE 於每次工具呼叫發 `STEP`(開始 `RUNNING`、結束 `SUCCESS`),`stepKey` 對每次呼叫唯一且穩定(同一次呼叫的 RUNNING/SUCCESS 用同一個 key,靠 last-state-per-key 覆寫)

**債在哪**:目前 dashboard 模式靠模型自報 `[[step:]]` 標記、analysis 模式只發一個罐頭步驟。既然已有 LangGraph,真實執行事實可直接取得,不必信任模型的自我描述。順帶解決 §16.5 的閒置逾時風險。

- [x] **Step 1:寫失敗測試**

沿用 `test_chat.py` 既有的 fake model + `TestClient` streaming 形態。腳本化一次工具呼叫,斷言事件序列中該工具對應的 `STEP` 出現且**最終狀態為 `SUCCESS`**,並斷言步驟標題是人類可讀的中文(非工具函式名)。

第二個測試:斷言**靜默期間會送出 heartbeat**——同 `stepKey`、狀態仍 `RUNNING` 的重複 STEP。以短 heartbeat 間隔注入,避免測試等待數秒。

- [x] **Step 2:跑測試確認失敗**

Run: `cd agent-service && uv run pytest tests/test_chat.py -v`
Expected: FAIL

- [x] **Step 3:改用真實工具事件**

從 LangGraph 取得工具開始/結束事件(`astream_events` 的 `on_tool_start`/`on_tool_end`,或 `astream` 的 `updates` 模式——**以實際 langgraph 版本的行為為準,若與此描述不符請回報**)。工具名 → 標題對照放 `agent.py`,與工具定義同處(`get_schema` → 讀取資料結構、`run_sql` → 執行查詢)。

保留開場的 `analysis` 步驟作為總括步驟,並在 ANSWER 前收成 `SUCCESS`(M1 既有行為,不要弄丟)。

- [x] **Step 4:heartbeat**

靜默超過設定秒數時,重送目前進行中步驟的 `RUNNING` STEP。**NEVER 新增事件型別**——重送既有 STEP 可完全複用 last-state-per-key 累積邏輯,前端零改動。間隔以模組常數表達並可由測試注入。

依 §16.5:Java 端 `ERD_AGENT_ANALYSIS_REQUEST_TIMEOUT_SECONDS` 是**事件間閒置**逾時,heartbeat 間隔 MUST 明顯小於它(預設 180s)。

- [x] **Step 5:測試 + lint**

Run: `cd agent-service && uv run pytest -q && uv run ruff check .`
Expected: 全綠 + All checks passed

- [x] **Step 6:Commit**

```bash
git add agent-service
git commit -m "feat(agent-service): emit STEP from real tool events and add heartbeat"
```

---

### Task 5:`previousArtifactHtml` 收進 dashboard 專屬巢狀 record(可延後)

**⚠️ 本任務可延後**,由執行者與使用者確認後再決定是否進行。理由:目前 dashboard 專屬輸入只有一個欄位,包成巢狀 record 的收益是「語意分群 + 未來擴充點」,但代價是動到 8 個測試檔約 40 處 `AgentRequest` 建構點。若 M2 後續確定會加入第二個 dashboard 專屬輸入(如 `previousSpec` 的對稱設計),一起做更划算。**跳過本任務不影響 Task 1–4 的成果。**

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/model/AgentRequest.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/model/DashboardContext.java`
- Modify: 7 處 main 讀取點與 5 處 main 建構點(見下)、8 個測試檔

**Interfaces:**
- Produces: `DashboardContext(String previousArtifactHtml)`;`AgentRequest(String userId, String sessionId, String question, List<HistoryMessage> history, List<AgentFileContext> files, DashboardContext dashboard)`——analysis 模式傳 `null`

- [ ] **Step 1:改 record 並讓編譯器列舉**

Run: `cd backend && ./mvnw -q -o compile 2>&1 | head -40`

已知 main 讀取點:`CodegenRequestMapper:77,82`、`PromptAssembler:73`、`GenerationRepairGuard:146`、`GenerationRepairer:170,197`、`InternalCodegenProvider:81`。
已知 main 建構點:`AgentOrchestrator:298-304`、`ArtifactRepairService:101-102`、`ArtifactRepairer:65-72`、`GenerationRepairer:83-90,163-170`。

- [ ] **Step 2:逐點改寫,null 安全**

讀取端一律先檢查 `dashboard()` 是否為 null(analysis 模式恆為 null)。既有以 `StringUtils.hasText(...)` 判斷的地方要一併涵蓋 context 本身為 null 的情況。

- [ ] **Step 3:測試檔改建構點**

8 個檔案:`LangGraphAnalysisProviderTest`(8)、`CodegenRequestMapperTest`(4)、`InternalCodegenProviderTest`(2)、`GenerationRepairerTest`(5)、`GenerationRepairGuardTest`(3)、`OpenAICompatibleProviderTest`(10)、`TokenExchangeProviderTest`(4)、`PromptAssemblerTest`(12)、`ArtifactRepairerTest`(2)。

- [ ] **Step 4:全套 + commit**

Run: `cd backend && ./mvnw -q test`

```bash
git add backend
git commit -m "refactor(backend): group dashboard-only input into DashboardContext"
```

---

## 驗收(整個 Part 1 完成後)

**可證偽的重構驗收條件**——這些沒有畫面,所以驗收寫成可執行的判準:

1. **加第四種 agent 只需實作窄介面**:`ProviderContextLoadTest` 證明 analysis 模式下 `DashboardAgentProvider` bean 不存在而 context 正常啟動,即證明新模式不必實作 `harden()`。
2. **`stepKey` 不再靠字串前綴**:`grep -rn 'startsWith("d")' backend/src frontend/src` 應無步驟相關命中;`LangGraphAnalysisProvider` 內的 `d_` 改寫與 `applyDynamicStepKeyPrefix` 已刪除;不帶 `d` 前綴的步驟(如 `analysis`)可正常持久化與渲染(由 Task 3 的新測試釘住)。
3. **步驟條反映真實執行**:啟動 agent-service 與 backend,問一個需要多次查詢的問題,步驟條顯示「讀取資料結構 → 執行查詢」等真實步驟並各自收成打勾,而非單一罐頭步驟。
4. **三邊測試全綠**:backend `./mvnw test`、agent-service `uv run pytest` + `ruff check`、frontend `npm test`。
5. **端到端煙霧測試**:沿用 M1 形態(local 與 MinIO 兩輪),確認 SPI 收窄未破壞既有流程。

## Self-Review 紀錄

- **Spec 覆蓋**:§16.2 收窄介面 → Task 2;§16.2 結果型別更名 → Task 1;§16.2 dashboard 專屬輸入 → Task 5;§16.4-1 stepKey → Task 3;§16.4-2 STEP 真實事件 → Task 4;§16.5 heartbeat → Task 4 Step 4;§17.4 型別同步 → Task 3 Step 5。**§15 的 `engine/`/`agent/` 分層固化與 Ruff banned-import 不在本計畫**——屬 M2 Part 2(功能任務)一併處理,因為要與 `methods/`、`profiling.py` 同時落地才有意義。
- **型別一致**:`AgentOutcome`(Task 1)→ Task 2 的 `harden()` 簽名 → Task 5 不動結果型別 ✔;Task 3 不改 `StepEvent` 形狀,故 Task 4 的 Python 端可自由選 `stepKey` ✔
- **非機械處已標明**:Task 2 Step 5 的 `Optional<DashboardAgentProvider>`(且明確禁用 `@ConditionalOnBean`)、Task 4 Step 3 的 langgraph 版本行為差異
- **刻意不給大段可照抄程式碼**:見開頭「給實作者的重要提醒」
