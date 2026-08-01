# M3 Agent 流 Implementation Plan（Cowork Data Studio）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Worker model policy:** 實作 subagent 一律 **sonnet**（複雜整合可 opus），主迴圈驗收。

**Goal:** 端到端 agent 對話流：provider 抽象層（AgentEvent 流）、AnthropicProvider 與 InternalLlmProvider（OpenAI-compatible SSE）完整實作、`POST /api/sessions/{id}/messages` SSE endpoint（heartbeat、訊息持久化、session 標題規則）、前端打字效果 + ThoughtChain 步驟軌跡 + artifact 參照卡。

**Architecture:** Controller 在同步階段值物件化 `CurrentUser`（async 規則）→ AgentOrchestrator 準備 AgentRequest（ownership、存 user 訊息、標題規則、載入檔案 profile 與歷史）→ 發 STEP 事件 → provider 產出 token 流 → `HtmlExtractingTransformer` 把 ```html fenced block 抽成 Artifact（M3 存原始 HTML，M4 才做注入/渲染）→ 完成時持久化 AI 訊息。JPA 阻塞操作一律排在 `boundedElastic`。

**Tech Stack:** 新增 `com.anthropic:anthropic-java`（Anthropic SDK）、`com.squareup.okhttp3:mockwebserver`（測試）；前端用既有 @ant-design/x ThoughtChain。

**Spec:** `docs/superpowers/specs/2026-07-05-cowork-data-studio-design.md` §5/§6
**Base branch:** `feat/m3-agent-flow`（自 master @ M2 squash）

## Global Constraints

- 沿用 CLAUDE.md 全部 rules（例外放 `com.erd.cowork.exception`；`CurrentUser` request-scoped——**controller 回傳 Flux 前必須先把 userId 讀出成值**，reactive pipeline 內 NEVER 碰 CurrentUser bean）
- AgentEvent JSON 契約（SSE data，Jackson `@JsonTypeInfo` type 欄位）：
  - `{"type":"STEP","stepKey":"s1","title":"...","description":"...|null","status":"PENDING|RUNNING|SUCCESS|ERROR"}`
  - `{"type":"TOKEN","delta":"..."}`、`{"type":"ANSWER","text":"..."}`、`{"type":"ARTIFACT","artifactId":"...","title":"..."}`、`{"type":"ERROR","code":"...","message":"..."}`
- SSE：`text/event-stream`，每 15 秒 heartbeat（SSE comment `:ka`）直到流結束；JPA 存取排 `Schedulers.boundedElastic()`
- 步驟軌跡固定四步（orchestrator 驅動，非 LLM）：`s1 Reading imported files`（description=檔名列表）→ `s2 Analyzing data profile` → `s3 Generating dashboard`（provider 串流期間 RUNNING）→ `s4 Rendering dashboard`；無 artifact 時 s4 標 SUCCESS 但 description="no dashboard produced"
- 標題規則：session 的**第一則** USER 訊息 → 標題 = question 截 30 字（超過加 `…`）
- Prompt 契約（兩個 LLM provider 共用 PromptBuilder）：system 說明 artifact 契約（HTML 讀 `window.__ERD_DATA__[alias]`、輸出包在 ```html fenced block、Tailwind+ECharts CDN、統計在瀏覽器 JS 計算）；user 內容 = question + 每檔 alias/名稱/rowCount/欄位表(名稱+型別+統計)/top values/前 20 列樣本 markdown
- 模型/端點全走 config `erd.agent.*`（env 佔位）；Anthropic 預設 `claude-haiku-4-5`
- M3 的 Artifact 只存**原始抽出 HTML**（不注入資料、不渲染右欄）——注入與 iframe 是 M4
- V4 migration：`uploaded_file` 加 `UNIQUE (session_id, alias)`（M2 終審延後項，`__ERD_DATA__` 啟用前必加）
- 環境：Java 21 portable（JAVA_HOME 同前）；docker credential workaround 同前

---

### Task 1: Agent SPI + 事件模型 + config + V4 migration

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/AgentEvent.java`（sealed interface + 5 records 同檔或分檔皆可，分檔較佳）
- Create: `backend/src/main/java/com/erd/cowork/agent/StepEvent.java` / `TokenEvent.java` / `AnswerEvent.java` / `ArtifactEvent.java` / `ErrorEvent.java` / `StepStatus.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/AgentRequest.java` / `AgentFileContext.java` / `HistoryMessage.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/DashboardAgentProvider.java` / `ProviderResult.java` / `ExtractionResult.java`
- Create: `backend/src/main/java/com/erd/cowork/config/AgentProperties.java`
- Create: `backend/src/main/resources/db/migration/V4__uploaded_file_alias_unique.sql`
- Modify: `backend/src/main/resources/application.yml`（`erd.agent.*`）
- Test: `backend/src/test/java/com/erd/cowork/agent/AgentEventJsonTest.java`

**Interfaces（後續 task 依賴，精確）:**

```java
// package com.erd.cowork.agent
@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, include = JsonTypeInfo.As.PROPERTY, property = "type")
@JsonSubTypes({
  @JsonSubTypes.Type(value = StepEvent.class, name = "STEP"),
  @JsonSubTypes.Type(value = TokenEvent.class, name = "TOKEN"),
  @JsonSubTypes.Type(value = AnswerEvent.class, name = "ANSWER"),
  @JsonSubTypes.Type(value = ArtifactEvent.class, name = "ARTIFACT"),
  @JsonSubTypes.Type(value = ErrorEvent.class, name = "ERROR")
})
public sealed interface AgentEvent permits StepEvent, TokenEvent, AnswerEvent, ArtifactEvent, ErrorEvent {}

public enum StepStatus { PENDING, RUNNING, SUCCESS, ERROR }
public record StepEvent(String stepKey, String title, String description, StepStatus status) implements AgentEvent {}
public record TokenEvent(String delta) implements AgentEvent {}
public record AnswerEvent(String text) implements AgentEvent {}
public record ArtifactEvent(String artifactId, String title) implements AgentEvent {}
public record ErrorEvent(String code, String message) implements AgentEvent {}

public record HistoryMessage(String sender, String text) {}
public record AgentFileContext(String alias, String name, String type, com.erd.cowork.parsing.FileProfile profile) {}
public record AgentRequest(String userId, String sessionId, String question, java.util.List<HistoryMessage> history, java.util.List<AgentFileContext> files) {}

public record ExtractionResult(String answerText, String html) {} // html 可為 null

public record ProviderResult(
    reactor.core.publisher.Flux<AgentEvent> events,
    java.util.function.Supplier<ExtractionResult> extraction) {}

public interface DashboardAgentProvider {
  boolean supportsStreaming();
  ProviderResult generate(AgentRequest request);
}
```

```java
// package com.erd.cowork.config
@ConfigurationProperties(prefix = "erd.agent")
public record AgentProperties(String provider, Anthropic anthropic, InternalLlm internalLlm) {
  public record Anthropic(String apiKey, String model, int maxTokens) {}
  public record InternalLlm(String baseUrl, String apiKey, String model) {}
}
```

`application.yml` 追加（併入既有 `erd:`）：

```yaml
  agent:
    provider: ${ERD_AGENT_PROVIDER:anthropic}
    anthropic:
      api-key: ${ANTHROPIC_API_KEY:}
      model: ${ERD_AGENT_ANTHROPIC_MODEL:claude-haiku-4-5}
      max-tokens: 16000
    internal-llm:
      base-url: ${ERD_AGENT_INTERNAL_LLM_BASE_URL:}
      api-key: ${ERD_AGENT_INTERNAL_LLM_API_KEY:}
      model: ${ERD_AGENT_INTERNAL_LLM_MODEL:gpt-oss-120b}
```

`V4__uploaded_file_alias_unique.sql`：

```sql
ALTER TABLE uploaded_file ADD CONSTRAINT uq_uploaded_file_alias UNIQUE (session_id, alias);
```

**Steps:** TDD——`AgentEventJsonTest` 用注入/`new ObjectMapper()` 對五種事件做序列化斷言（例 `{"type":"TOKEN","delta":"hi"}`；STEP 的 status 為 `"RUNNING"` 字串）與反序列化 round-trip → 編譯失敗 → 實作 → `./mvnw test` 全綠（V4 由既有測試的 Flyway 啟動覆蓋）→ Commit `feat(backend): agent SPI, event model and config`。

---

### Task 2: PromptBuilder

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/PromptBuilder.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/PromptBuilderTest.java`

**Interfaces:**
- `@Component`，方法：`String systemPrompt()`、`String userPrompt(AgentRequest request)`
- systemPrompt 固定文案，必含（reviewer 可檢查的關鍵句）：讀取 `window.__ERD_DATA__[alias]`（格式 `{ columns: string[], rows: unknown[][] }`）、輸出完整 HTML 於 ```html fenced block、Tailwind CDN + ECharts CDN、所有統計由 HTML 內 JS 計算、fenced block 外可寫簡短說明文字
- userPrompt：question + 每檔區塊——`## file: {alias} ({name})`、`rows: {rowCount}`、欄位 markdown 表（colName/colType/min/max/mean/std/nullCount）、非數值欄 top values、`### sample (first N rows)` markdown 表（用 profile.sampleRows + headers）
- history 非空時附 `## conversation so far`（sender: text 逐行，供 regenerate/追問語境）

**Steps:** TDD（斷言 system 含關鍵句；user 含 alias/欄位/樣本/歷史）→ 實作 → 全綠 → Commit `feat(backend): prompt builder with data profile serialization`。

---

### Task 3: HtmlExtractingTransformer（token 流抽 HTML）

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/HtmlExtractingTransformer.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/HtmlExtractingTransformerTest.java`

**Interfaces:**

```java
// ExtractionResult(String answerText, String html) 已於 Task 1 建立（html 可為 null）

public class HtmlExtractingTransformer {
  public HtmlExtractingTransformer() {}
  /**
   * 輸入 raw token 流，輸出 TokenEvent 流（fenced html 內容不外流），
   * 完成時可透過 result() 取得完整 answer 文字（不含 fenced block）與 html。
   * 用法：flux.transform(t::apply) 後訂閱完成才呼叫 result()。
   */
  public Flux<AgentEvent> apply(Flux<String> tokens);
  public ExtractionResult result();
}
```

**行為規格（測試必須覆蓋）:**
1. fence 標記 ```` ```html ```` 與收尾 ```` ``` ```` 可能**跨 token 切開**（如 "``" + "`ht" + "ml\n<div>"）——需以緩衝狀態機處理，不可假設標記在單一 token 內
2. fenced 內容不得產生 TokenEvent；fence 外文字逐 token 發 TokenEvent
3. result().answerText = fence 外全部文字（trim）；result().html = fenced 內容（無 fence 標記；無 block 時 null）
4. 多個 ```html block 時取第一個，其餘按一般文字流出（附測試）
5. 未收尾的 fence（流結束仍在 html 內）→ html = 已收集內容（容錯）
6. 空流 → answerText=""、html=null

實作提示：維護 `StringBuilder pending`（未判定緩衝）+ 狀態 enum(TEXT, IN_HTML, DONE_HTML)；`concatMap` 逐 token 餵狀態機、輸出 0..n 個確定的 TokenEvent；`materialize` 不需要——狀態機物件屬單次訂閱，non-thread-safe 標註 javadoc。掃描時保留 pending 尾端最多 7 字元（```` ```html ```` 長度）不外流以偵測跨界標記。

**Steps:** TDD（含跨 token fence 案例，用 `Flux.just("前言``", "`html\n<div>", "hi</div>``", "`後記")` 類切法）→ 實作 → 全綠 → Commit `feat(backend): streaming html fence extractor`。

---

### Task 4: InternalLlmProvider（OpenAI-compatible SSE）

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/InternalLlmProvider.java`
- Modify: `backend/pom.xml`（test scope `com.squareup.okhttp3:mockwebserver:4.12.0`）
- Test: `backend/src/test/java/com/erd/cowork/agent/InternalLlmProviderTest.java`

**Interfaces:**
- `@Component @ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "internal-llm")`
- constructor：`InternalLlmProvider(AgentProperties props, PromptBuilder promptBuilder, WebClient.Builder webClientBuilder)`
- `supportsStreaming()` → true
- `generate(request)`：POST `{baseUrl}/v1/chat/completions`，headers `Authorization: Bearer {apiKey}`（apiKey 空則不帶）、body `{"model":..., "stream":true, "messages":[{"role":"system","content":systemPrompt},{"role":"user","content":userPrompt(request)}]}`；`retrieve().bodyToFlux(String.class)`（WebClient 對 text/event-stream 自動逐 data 行解碼）→ 過濾 `[DONE]` → Jackson 解析 `choices[0].delta.content`（null 跳過）→ 得 token Flux → `HtmlExtractingTransformer`（每次 generate new 一個 transformer）→ 完成時 concat `AnswerEvent(result.answerText)` 與（html 非 null 時）由上層處理的 html——**注意**：artifact 的持久化與 ArtifactEvent 由 Task 6 orchestrator 做；provider 完成時把 html 放進 `AnswerEvent` 之外的通道？——**設計決定：provider 輸出 TokenEvent 流 + 終端 `AnswerEvent`，html 經 transformer 的 result() 由 orchestrator 取得**。因此 `generate()` 簽名不足；改為：

（`ProviderResult`/`ExtractionResult` 已由 Task 1 定義：provider 輸出 TokenEvent 流 + 終端 AnswerEvent，html 經 extraction supplier 由 orchestrator 取得。）
- 非 2xx / 連線錯誤 → events 流以 `ErrorEvent("PROVIDER_ERROR", message)` 結尾（onErrorResume），不拋出
- **測試（MockWebServer）**：模擬 SSE 回應（`data: {...delta.content:"Hello "}\n\n`、`data: {...content:"```html\n<p>x</p>\n```"}`、`data: [DONE]`）→ 斷言 TokenEvent 序列（html 不外流）、AnswerEvent 文字、extraction.get().html()；500 回應 → ErrorEvent

**Steps:** TDD → 實作 → 全綠 → Commit `feat(backend): internal LLM provider with OpenAI-compatible SSE`。

---

### Task 5: AnthropicProvider

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/AnthropicProvider.java`
- Modify: `backend/pom.xml`（`com.anthropic:anthropic-java:2.34.0`）
- Test: `backend/src/test/java/com/erd/cowork/agent/AnthropicProviderTest.java`

**Interfaces:**
- `@Component @ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "anthropic", matchIfMissing = true)`
- constructor：`AnthropicProvider(AgentProperties props, PromptBuilder promptBuilder)`——內部以 `AnthropicOkHttpClient.builder().apiKey(props.anthropic().apiKey()).build()` 建 client（lazy 單例欄位）
- `generate(request)`：`MessageCreateParams.builder().model(props.anthropic().model()).maxTokens((long) props.anthropic().maxTokens()).system(promptBuilder.systemPrompt()).addUserMessage(promptBuilder.userPrompt(request)).build()`；`client.messages().createStreaming(params)` 是阻塞 iterator → `Flux.<String>create(sink -> {...})` 包裝並 `subscribeOn(Schedulers.boundedElastic())`，逐 event 取 `contentBlockDelta → delta → text → text()` emit；try-with-resources 關 StreamResponse；sink.onDispose 中斷
- token Flux → HtmlExtractingTransformer → 同 Task 4 的 ProviderResult 形狀；例外 → ErrorEvent("PROVIDER_ERROR", ...)
- **測試界線**：SDK 呼叫不打真網路——測 (a) params 組裝（抽 package-private `MessageCreateParams buildParams(AgentRequest)` 方法直接斷言 model/maxTokens/system 含契約關鍵句）、(b) 以 package-private constructor/`Function<MessageCreateParams, Stream<String>>` seam 注入假 token 供應（建構子重載注入 `tokenStreamFactory`），斷言事件流與 extraction。真實 API 串接留待 e2e（Task 8）用真 key 手動驗證

**Steps:** TDD → 實作 → 全綠 → Commit `feat(backend): anthropic provider with streaming SDK`。

---

### Task 6: AgentOrchestrator + SSE MessageController + 持久化 + 標題規則

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java`
- Create: `backend/src/main/java/com/erd/cowork/web/MessageController.java`
- Create: `backend/src/main/java/com/erd/cowork/web/dto/SendMessageRequest.java`（record，`@NotBlank String question`，@Schema）
- Test: `backend/src/test/java/com/erd/cowork/web/MessageControllerTest.java`（+ `@TestConfiguration` FakeProvider）

**Interfaces:**
- `POST /api/sessions/{id}/messages`，consumes JSON `{"question":"..."}`，produces `text/event-stream`；回傳 `Flux<ServerSentEvent<AgentEvent>>`（event name = 事件 type 小寫不必要——統一 data JSON 即可，`event()` 不設）；controller **先同步讀 `currentUser.getUserId()` 再組 Flux**
- Orchestrator `Flux<AgentEvent> stream(String userId, String sessionId, String question)`：
  1. `Mono.fromCallable`（boundedElastic）prepare：`sessionGuard.loadOwned`（注意：SessionGuard 現在讀 CurrentUser——**新增 overload `loadOwnedAs(String userId, String sessionId)`** 供 async 路徑，原方法委派之）；存 USER ChatMessage；標題規則（該 session 首則 USER 訊息時 `title = truncate(question, 30)` 存檔）；載入 files（`findBySessionIdAndExpiredFalse` → AgentFileContext，metadataJson 反序列化 FileProfile，失敗檔跳過）與 history（先前訊息 → HistoryMessage）
  2. 事件流組裝：`s1 RUNNING→SUCCESS`（desc=檔名逗號串）→ `s2 RUNNING→SUCCESS` → `s3 RUNNING` → provider.generate(request).events（Token/Answer/Error）→ 完成後（boundedElastic）：extraction 取 html——非 null 時建 Artifact(title=truncate(question,50), html=原始html) 存檔 → `s3 SUCCESS`、`s4 SUCCESS`、`ArtifactEvent(id,title)`；無 html → `s3 SUCCESS`、`s4 SUCCESS(desc="no dashboard produced")`；存 AI ChatMessage（text=answerText、stepsJson=四步最終狀態 JSON、artifactId）
  3. provider ErrorEvent 或任何例外：存 AI 訊息（text=錯誤訊息、steps 標 ERROR）並以 ErrorEvent 結束流（onErrorResume 統一 `ErrorEvent("AGENT_ERROR", msg)`）
  4. heartbeat：`Flux.interval(15s)` → `ServerSentEvent.builder().comment("ka")`，`mergeWith` 主流、主流完成即止（`takeUntilOther`）——heartbeat 在 controller 層 merge（orchestrator 回純 AgentEvent 流）
- FakeProvider（測試 `@TestConfiguration` + `@Primary`）：回固定 token 序列（含 html fence）→ 整合測試用 `WebTestClient`（`spring-boot-starter-webflux` 已在）或 TestRestTemplate 讀 SSE：斷言事件序列（STEP s1..s4、TOKEN 數、ARTIFACT）、DB 斷言（user+ai 訊息、title 更新、artifact row、stepsJson 非空、第二則訊息不改 title）、他人 session → 404（一般 JSON error）

**Steps:** TDD（controller 測試先行）→ 實作 → `./mvnw test` 全綠 → Commit `feat(backend): agent orchestrator with SSE endpoint, persistence and title rule`。

---

### Task 7: 前端 useAgentStream

**Files:**
- Create: `frontend/src/hooks/useAgentStream.ts`
- Create: `frontend/src/utils/sseParser.ts`
- Modify: `frontend/src/types.ts`（AgentEvent union / StepItem）
- Modify: `frontend/src/api/apiClient.ts`（export `getUserId`）
- Test: `frontend/src/utils/sseParser.test.ts`
- Test: `frontend/src/hooks/useAgentStream.test.ts`

**Interfaces:**

```ts
// types.ts 追加
export type StepStatus = 'PENDING' | 'RUNNING' | 'SUCCESS' | 'ERROR';
export interface StepItem { stepKey: string; title: string; description: string | null; status: StepStatus }
export type AgentEvent =
  | { type: 'STEP'; stepKey: string; title: string; description: string | null; status: StepStatus }
  | { type: 'TOKEN'; delta: string }
  | { type: 'ANSWER'; text: string }
  | { type: 'ARTIFACT'; artifactId: string; title: string }
  | { type: 'ERROR'; code: string; message: string };

// sseParser.ts — 純函式，可增量餵 chunk
export function createSseParser(onEvent: (e: AgentEvent) => void): { feed(chunk: string): void; flush(): void }
// 規則：以空行分隔 event；只取 data: 行（可多行 join）；忽略以 ':' 開頭的 comment（heartbeat）；JSON.parse 失敗忽略該 event

// useAgentStream.ts
export interface AgentStreamState {
  isStreaming: boolean;
  steps: StepItem[];          // 以 stepKey upsert
  liveText: string;           // TOKEN 累積
  answer: string | null;      // ANSWER
  artifact: { artifactId: string; title: string } | null;
  error: { code: string; message: string } | null;
}
export function useAgentStream(sessionId: string): { state: AgentStreamState; send(question: string): Promise<void>; reset(): void }
// send: fetch(`/api/sessions/${sessionId}/messages`, { method:'POST', headers:{ 'Content-Type':'application/json', 'X-User-Id': getUserId(), Accept:'text/event-stream' }, body: JSON.stringify({ question }) })
// response.body.getReader() + TextDecoder 迴圈餵 parser；完成/錯誤時 isStreaming=false；完成後 invalidateQueries ['session', sessionId] 與 ['sessions']（標題可能變）
// 非 2xx：讀 json message → error state
```

**Steps:** TDD（parser：多 event 單 chunk、event 跨 chunk 切開、comment 忽略、壞 JSON 忽略；hook：以 mock fetch + ReadableStream 模擬串流斷言 state 演進與 invalidate 呼叫）→ 實作 → `npm test && npm run build` 全綠 → Commit `feat(frontend): agent SSE stream hook and parser`。

---

### Task 8: 前端聊天 UI 接線 + e2e

**Files:**
- Create: `frontend/src/components/MessageList.tsx` / `MessageBubble.tsx` / `StepChain.tsx` / `ArtifactCard.tsx`
- Modify: `frontend/src/components/ChatPanel.tsx`（thread 區接 MessageList + 串流氣泡；PromptSender/QuickChips 接 send）
- Test: `frontend/src/components/StepChain.test.tsx` / `MessageBubble.test.tsx`

**Interfaces / 行為:**
- `MessageBubble { sender: 'USER'|'AI'; text: string; steps?: StepItem[] | null; artifact?: { artifactId: string; title: string } | null; streaming?: boolean }`：USER 右側藍底、AI 左側灰底含 `eRD AI` 標頭（mockup 1054–1068）；AI 的 steps 摺疊列「Worked through N steps」點開 StepChain；artifact 參照卡（appstore icon + title + `shown right →`）
- `StepChain { steps: StepItem[] }`：用 `@ant-design/x` 的 ThoughtChain（status 對映 pending/loading/success/error）；若 antd-x API 與版本不合，自建簡單清單（icon per status）並於報告註明
- `ChatPanel`：歷史訊息來自 `session.messages`（stepsJson JSON.parse 成 StepItem[]，artifactId 有值時卡片 title 用訊息文字截 50）；`useAgentStream(sessionId)`；send 流程——樂觀 append user 氣泡（本地 state）→ `send(question)`；串流中顯示 AI 氣泡（Working on it… + StepChain live + liveText 打字）；完成後 invalidate 讓歷史接手、清 live state（`reset()`）；error → antd message.error + 氣泡顯示錯誤文字；QuickChips onPick 與 PromptSender onSend 都走同一 send；串流中 PromptSender disabled
- 標題更新：invalidate ['sessions'] 已由 hook 做——sidebar 標題自動刷新
- **e2e**：(a) 後端以 FakeProvider profile？——生產碼不放 fake；改用 `erd.agent.provider=internal-llm` + 本機起一個假 SSE server？太重。**e2e 方案：真 Anthropic key**（`ANTHROPIC_API_KEY` 環境變數存在時）：`./mvnw spring-boot:run` + `npm run dev` → 上傳小 csv → 送「Run an SPC analysis...」→ 觀察 SSE 事件（curl -N 直打 API 亦可）→ 斷言回覆含步驟與（大概率）artifact row；key 不存在時：以 curl 對 controller 打（provider 會回 PROVIDER_ERROR ErrorEvent）驗證 SSE 通道與持久化仍正確，UI 互動註明待使用者驗收。compose：backend env 加 `ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}`（compose 修改屬本 task）；有 key 時經 nginx 驗一輪 SSE（`curl -N http://localhost:3000/api/sessions/{id}/messages ...`）確認 heartbeat/串流過 proxy
- Modify: `docker-compose.yml`（backend env 傳遞 ANTHROPIC_API_KEY 與 ERD_AGENT_*）

**Steps:** TDD 元件測試 → 實作接線 → `npm test && npm run build` 全綠 → e2e（結果進報告）→ Commit `feat(frontend): chat flow with typing effect, thought chain and artifact card`。

---

## M3 完成定義（驗收清單）

- [ ] 後端/前端測試全綠
- [ ] 送出 prompt：SSE 依序收到 s1→s4 STEP、TOKEN 打字、（有 LLM key 時）ARTIFACT；heartbeat 不干擾前端 parser
- [ ] user/AI 訊息與 stepsJson/artifactId 正確持久化；重新整理後歷史完整重現（含步驟摺疊）
- [ ] session 標題 = 第一個問題截 30 字，sidebar 即時更新
- [ ] 跨 user 隔離不回歸；nginx 路徑 SSE 正常（proxy_buffering off 已在）
- [ ] Anthropic 與 internal-llm 兩個 provider 依 config 切換（internal-llm 以 MockWebServer 驗證；真環境待公司網路）

## 後續

- M4 Artifact：`__ERD_DATA__` 注入（抽樣）、ArtifactController、iframe 渲染、全螢幕頁、Regenerate——M3 驗收後撰寫
