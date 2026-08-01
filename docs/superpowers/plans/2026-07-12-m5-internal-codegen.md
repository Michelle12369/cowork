# M5 InternalCodegenProvider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Worker model policy:** implementer/task reviewer 用 sonnet；Task 3（reactive 偽串流＋修復迴圈相容性）reviewer 需對抗式審。

**Goal:** 完整實作 `InternalCodegenProvider`——公司 codegen API（無 SSE、30s–1min 延遲、一次性完整回覆）接上現有 provider SPI，偽串流重播讓前端體驗與其他 provider 完全一致，且三層修復迴圈（語法／省略／瀏覽器回報）免改動直接可用。

**Architecture:** provider 收到 `AgentRequest` → `CodegenRequestMapper` 組公司 API 的 `params.inputData`（fileMeta＋fileData 樣本＋conversation）→ WebClient POST（timeout 120s，SSE heartbeat 由 MessageController 既有 15s `:ka` 覆蓋等待期）→ 完整 `answer` 切塊經 `HtmlExtractionHelper` 偽串流重播（TOKEN／CODE／fence 抽取行為與 OpenAI 路徑共用同一元件）→ `ProviderResult(events, extraction)`。修復迴圈呼叫 `provider.generate` 的語意（question＋previousArtifactHtml、空 history）由 mapper 統一處理，天然相容。

**Tech Stack:** Spring Boot 3.4／Java 17、WebFlux WebClient、Reactor、Jackson、MockWebServer（測試）、既有 `HtmlExtractionHelper`／`TokenExchangeClient`。

## Global Constraints

- Java 17（公司環境；NEVER 用 18+ API）
- 一律 constructor injection＋`@RequiredArgsConstructor`；config 一律 `@ConfigurationProperties`，NEVER hardcode URL/credentials
- **Provider 互斥不變量**：`erd.agent.provider=openai-compatible|internal-codegen` 由 `@ConditionalOnProperty` 選擇，同時只有一個 `DashboardAgentProvider` bean；預設 `openai-compatible`
- 命名分類法（CLAUDE.md 六列表）：bean 後綴／`*Utils`／`*Helper`／`*Dto`／model record／`*Exception`
- 日誌紅線：NEVER log API key、完整 prompt/HTML、使用者資料內容；controller/provider 進入點記摘要（sessionId、長度、計數）
- Secrets 一律 env vars；`.env` gitignored 不印出
- spec 既定值：`erd.agent.codegen.sample-rows` 預設 **20**；WebClient timeout **120s**；偽串流 `delayElements` **30ms**；SSE heartbeat 15s（MessageController 既有，本 plan 不動）
- 既有紅線：`OpenAICompatibleProvider`／`TokenExchangeClient`／`HtmlExtractionHelper`／orchestrator／修復迴圈行為與簽名零改動
- 測試：TDD；方法名 `methodName_condition_expectedBehavior`；MockWebServer 測 HTTP；merge 前 `./mvnw test` 全綠（`JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home`）

## File Structure

```
backend/src/main/java/com/erd/cowork/
├── config/AgentProperties.java                     [Modify] 加 Codegen nested record
├── agent/provider/                              [共用 SPI 層：DashboardAgentProvider、ProviderResult 留根]
│   ├── openai/                                     [Task 4 移入 — openai 專屬]
│   │   ├── OpenAICompatibleProvider.java           [Move] package 調整＋全引用更新
│   │   └── TokenExchangeClient.java                [Move] 同上
│   ├── codegen/                                    [New folder — codegen 專屬檔案聚居]
│   │   ├── CodegenRequestMapper.java               [Create] bean：AgentRequest → CodegenRequestDto
│   │   ├── InternalCodegenProvider.java            [Create] bean：@ConditionalOnProperty internal-codegen
│   │   └── model/
│   │       ├── CodegenRequestDto.java              [Create] wire records（request 樹）
│   │       └── CodegenResponseDto.java             [Create] wire record（answer/error）
├── agent/AgentOrchestrator.java                    [Modify Task 4] isGenerationRepairEnabled() gate
├── resources/application.yml                       [Modify] erd.agent.codegen 區段
docker-compose.yml                                  [Modify] codegen env passthrough
```

**Interfaces 現況（已驗證，任務中直接引用）：**

```java
public record AgentRequest(String userId, String sessionId, String question,
    List<HistoryMessage> history, List<AgentFileContext> files, String previousArtifactHtml) {}
public record HistoryMessage(String sender, String text) {}
public record AgentFileContext(String alias, String name, String type, FileProfile profile) {}
public record FileProfile(long rowCount, int colCount, List<String> headers,
    List<ColumnProfile> columns, List<List<String>> sampleRows) {}
public record ColumnProfile(String colName, String colType, Double min, Double max,
    Double mean, Double std, long nullCount, List<String> topValues) {}
public record ProviderResult(Flux<AgentEvent> events, Supplier<ExtractionResult> extraction) {}
public interface DashboardAgentProvider { ProviderResult generate(AgentRequest request); }
// HtmlExtractionHelper：non-bean per-stream；new 後 apply(Flux<String>) → Flux<AgentEvent>，
// 終止後 result() → ExtractionResult(answerText, html, questions)
```

**設計決策（實作者不需再抉擇；含使用者 2026-07-12 增補）：**

1. **偽串流走 `HtmlExtractionHelper`**——answer 切塊重播，TOKEN/fence 抽取與 OpenAI 路徑同一元件；但 **CODE 事件在 provider 端過濾掉**（codegen 不顯示「產生中的 HTML」面板——helper 照常累積 html 供 extraction，事件流 `filter(event -> !(event instanceof CodeEvent))`）。前端零改動。
2. **Canned step 序列**：codegen 無模型自發步驟，provider 內建 3 組文字序列輪替顯示（deterministic：`Math.floorMod(request.question().hashCode(), 3)` 選組）：
   - 組0：`整理資料摘要` → `呼叫生成服務` → `解析生成結果`
   - 組1：`準備生成請求` → `等待生成服務回應` → `組裝儀表板`
   - 組2：`分析檔案結構` → `生成儀表板` → `整理輸出`
   節奏：d1 RUNNING→SUCCESS（送出請求前）；d2 RUNNING（API 呼叫期間）→SUCCESS（收到回應）；d3 RUNNING（重播開始前）→SUCCESS（重播結束後）。公司 answer 不含 `[[step:]]`，helper 不會另發 d* 撞鍵（YAGNI 不防護）。
3. **無 reasoning**：codegen 不發 THINKING——前端「Working on it…」本來就只在有 thinking 時才可展開，**零改動自然滿足**（計畫內註記，不派工）。
4. **生成期修復管線對 codegen 整段跳過**（使用者定案：語法檢查／省略偵測／r1 都不需要，gpt-oss 優化只留瀏覽器確認制修復）：`AgentOrchestrator.finalize` 的 repair 區段 gate 由 `repair.enabled` 擴為 `repair.enabled && !"internal-codegen".equals(agentProperties.provider())`，抽 private 方法 `isGenerationRepairEnabled()` 並註解 rationale；瀏覽器修復（ArtifactRepairService）不受此 gate 影響、對 codegen 照常可用。
5. **auth（使用者定案：bearer）**：`erd.agent.codegen.api-key` 有值 → 帶 `Authorization: Bearer {apiKey}`；空值 → 不帶 header（本機 MockWebServer 測試用）。不做 auth-mode 欄位、不接 TokenExchangeClient（YAGNI；公司若改口再加）。
6. **公司 API `error` 欄位** → `ErrorEvent(ErrorCode.AGENT_ERROR.name(), error)`；HTTP 錯誤／timeout → Flux.error 傳播（orchestrator 既有 onErrorResume 持久化）。
7. **previousArtifactHtml**：mapper 將其以 fenced block 併入 `conversation.question` 尾端（公司 API 契約無獨立欄位；含最小變更與反省略指令）。修復迴圈的 repair prompt 也是走 question 欄位，天然一致。
8. **fileData 值型別**：樣本值以 String 原樣傳（`FileProfile.sampleRows` 即 String；公司 API 欄位型別待確認——mapper 內以常數註解標記調整點）。

---

### Task 1: Codegen 設定綁定

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/config/AgentProperties.java`
- Modify: `backend/src/main/resources/application.yml`
- Modify: `docker-compose.yml`（backend environment 區段）
- Test: `backend/src/test/java/com/erd/cowork/config/AgentPropertiesCodegenTest.java`（Create）

**Interfaces:**
- Produces: `AgentProperties.Codegen(String baseUrl, String path, String apiKey, int sampleRows, int timeoutSeconds, int chunkSize, long chunkDelayMillis)`，經 `agentProperties.codegen()` 取得

- [ ] **Step 1: 失敗測試**

```java
package com.erd.cowork.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

class AgentPropertiesCodegenTest {

  private final ApplicationContextRunner runner =
      new ApplicationContextRunner()
          .withUserConfiguration(TestConfig.class)
          .withPropertyValues(
              "erd.agent.provider=openai-compatible",
              "erd.agent.codegen.base-url=http://codegen.internal",
              "erd.agent.codegen.path=/api/v1/generate",
              "erd.agent.codegen.api-key=k",
              "erd.agent.codegen.sample-rows=20",
              "erd.agent.codegen.timeout-seconds=120",
              "erd.agent.codegen.chunk-size=24",
              "erd.agent.codegen.chunk-delay-millis=30");

  @Test
  void codegenProperties_bound_allFieldsPresent() {
    runner.run(
        context -> {
          AgentProperties.Codegen codegen = context.getBean(AgentProperties.class).codegen();
          assertThat(codegen.baseUrl()).isEqualTo("http://codegen.internal");
          assertThat(codegen.path()).isEqualTo("/api/v1/generate");
          assertThat(codegen.apiKey()).isEqualTo("k");
          assertThat(codegen.sampleRows()).isEqualTo(20);
          assertThat(codegen.timeoutSeconds()).isEqualTo(120);
          assertThat(codegen.chunkSize()).isEqualTo(24);
          assertThat(codegen.chunkDelayMillis()).isEqualTo(30L);
        });
  }

  @EnableConfigurationProperties(AgentProperties.class)
  static class TestConfig {}
}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=AgentPropertiesCodegenTest`
Expected: COMPILE ERROR（`codegen()` 不存在）

- [ ] **Step 3: 實作**

`AgentProperties.java`——record 加第四個欄位與 nested record（照既有 `OpenAiCompatible`/`Repair` 風格）：

```java
public record AgentProperties(
    String provider, OpenAiCompatible openAiCompatible, Repair repair, Codegen codegen) {

  /** 公司 codegen API 設定；apiKey 有值即帶 Authorization: Bearer header。 */
  public record Codegen(
      String baseUrl,
      String path,
      String apiKey,
      int sampleRows,
      int timeoutSeconds,
      int chunkSize,
      long chunkDelayMillis) {}
  // 既有 OpenAiCompatible / Repair 保持不動
}
```

`application.yml` 的 `erd.agent` 下新增：

```yaml
    codegen:
      base-url: ${ERD_AGENT_CODEGEN_BASE_URL:}
      path: ${ERD_AGENT_CODEGEN_PATH:/api/v1/generate}
      api-key: ${ERD_AGENT_CODEGEN_API_KEY:}
      sample-rows: ${ERD_AGENT_CODEGEN_SAMPLE_ROWS:20}
      timeout-seconds: ${ERD_AGENT_CODEGEN_TIMEOUT_SECONDS:120}
      chunk-size: ${ERD_AGENT_CODEGEN_CHUNK_SIZE:24}
      chunk-delay-millis: ${ERD_AGENT_CODEGEN_CHUNK_DELAY_MILLIS:30}
```

`docker-compose.yml` backend environment 加同名七個 `ERD_AGENT_CODEGEN_*` passthrough（照既有 `ERD_AGENT_OPENAI_COMPATIBLE_*` 形式，值 `${VAR:-預設}`）。

- [ ] **Step 4: 跑測試確認通過＋全 suite 綠**

Run: `./mvnw test -Dtest=AgentPropertiesCodegenTest`（PASS）→ `./mvnw test`（全綠——既有 AgentProperties 建構子呼叫處若有測試以 positional 建構需補 `null`／新 Codegen 參數，機械性修正）

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/config/AgentProperties.java backend/src/main/resources/application.yml docker-compose.yml backend/src/test/java/com/erd/cowork/config/AgentPropertiesCodegenTest.java <其餘被機械性修正的測試檔>
git commit -m "feat(backend): codegen provider configuration binding"
```

---

### Task 2: Wire model 與 CodegenRequestMapper

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/provider/codegen/model/CodegenRequestDto.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/provider/codegen/model/CodegenResponseDto.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/provider/codegen/CodegenRequestMapper.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/provider/codegen/CodegenRequestMapperTest.java`

**Interfaces:**
- Consumes: Task 1 的 `AgentProperties.Codegen`（只用 `sampleRows()`）；既有 `AgentRequest` 樹
- Produces: `CodegenRequestDto CodegenRequestMapper.toRequest(AgentRequest request)`；wire records 如下（Jackson 序列化後即公司 API JSON）

**Wire 契約（spec 原文結構，欄位待公司確認的調整點集中在此二檔）：**

```java
package com.erd.cowork.agent.provider.codegen.model;

import java.util.List;
import java.util.Map;

/** 公司 codegen API request wire 格式。欄位結構若公司 API 定案有出入，只改此檔與 mapper。 */
public record CodegenRequestDto(Params params) {
  public record Params(InputData inputData) {}

  public record InputData(
      String sessionId,
      List<FileMeta> fileMeta,
      Map<String, List<Map<String, String>>> fileData,
      Conversation conversation) {}

  public record FileMeta(String name, String alias, String type, Metadata metadata) {}

  public record Metadata(long rowCount, int colCount, List<Column> columns) {}

  public record Column(String colType, String colName) {}

  public record Conversation(String question, List<HistoryEntry> history) {}

  public record HistoryEntry(String sender, String text) {}
}
```

```java
package com.erd.cowork.agent.provider.codegen.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

/** 公司 codegen API response：answer 為一次性完整回覆（含 HTML），error 非空即失敗。 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record CodegenResponseDto(String answer, String error) {}
```

- [ ] **Step 1: 失敗測試**

```java
package com.erd.cowork.agent.provider.codegen;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.AgentFileContext;
import com.erd.cowork.agent.AgentRequest;
import com.erd.cowork.agent.HistoryMessage;
import com.erd.cowork.agent.provider.codegen.model.CodegenRequestDto;
import com.erd.cowork.config.AgentProperties;
import com.erd.cowork.parsing.model.ColumnProfile;
import com.erd.cowork.parsing.model.FileProfile;
import java.util.List;
import org.junit.jupiter.api.Test;

class CodegenRequestMapperTest {

  private CodegenRequestMapper mapperWithSampleRows(int sampleRows) {
    AgentProperties.Codegen codegen =
        new AgentProperties.Codegen("http://x", "/g", "", sampleRows, 120, 24, 30L);
    AgentProperties properties = new AgentProperties("internal-codegen", null, null, codegen);
    return new CodegenRequestMapper(properties);
  }

  private AgentFileContext waferFile(List<List<String>> sampleRows) {
    ColumnProfile lot = new ColumnProfile("lot", "string", null, null, null, null, 0, List.of());
    ColumnProfile vt = new ColumnProfile("vt", "number", 0.4, 0.5, 0.42, 0.01, 0, List.of());
    FileProfile profile = new FileProfile(30, 2, List.of("lot", "vt"), List.of(lot, vt), sampleRows);
    return new AgentFileContext("wafer_lots", "wafer_lots.csv", "csv", profile);
  }

  @Test
  void toRequest_singleFile_buildsFileMetaAndObjectRows() {
    AgentRequest request =
        new AgentRequest(
            "u1", "s1", "做一張圖",
            List.of(new HistoryMessage("USER", "hi")),
            List.of(waferFile(List.of(List.of("L01", "0.42"), List.of("L02", "0.43")))),
            null);

    CodegenRequestDto dto = mapperWithSampleRows(20).toRequest(request);

    CodegenRequestDto.InputData input = dto.params().inputData();
    assertThat(input.sessionId()).isEqualTo("s1");
    assertThat(input.fileMeta()).hasSize(1);
    CodegenRequestDto.FileMeta meta = input.fileMeta().get(0);
    assertThat(meta.alias()).isEqualTo("wafer_lots");
    assertThat(meta.type()).isEqualTo("table");
    assertThat(meta.metadata().rowCount()).isEqualTo(30);
    assertThat(meta.metadata().columns())
        .containsExactly(
            new CodegenRequestDto.Column("string", "lot"),
            new CodegenRequestDto.Column("number", "vt"));
    // fileData：header→值的物件列
    assertThat(input.fileData().get("wafer_lots"))
        .containsExactly(
            java.util.Map.of("lot", "L01", "vt", "0.42"),
            java.util.Map.of("lot", "L02", "vt", "0.43"));
    assertThat(input.conversation().question()).isEqualTo("做一張圖");
    assertThat(input.conversation().history())
        .containsExactly(new CodegenRequestDto.HistoryEntry("USER", "hi"));
  }

  @Test
  void toRequest_sampleRowsCapped_byConfig() {
    List<List<String>> five =
        List.of(
            List.of("a", "1"), List.of("b", "2"), List.of("c", "3"),
            List.of("d", "4"), List.of("e", "5"));
    AgentRequest request =
        new AgentRequest("u1", "s1", "q", List.of(), List.of(waferFile(five)), null);

    CodegenRequestDto dto = mapperWithSampleRows(3).toRequest(request);

    assertThat(dto.params().inputData().fileData().get("wafer_lots")).hasSize(3);
  }

  @Test
  void toRequest_previousArtifactHtml_appendedAsFencedBlockInQuestion() {
    AgentRequest request =
        new AgentRequest("u1", "s1", "改標題", List.of(), List.of(), "<html>prev</html>");

    CodegenRequestDto dto = mapperWithSampleRows(20).toRequest(request);

    String question = dto.params().inputData().conversation().question();
    assertThat(question).startsWith("改標題");
    assertThat(question).contains("```html\n<html>prev</html>\n```");
    assertThat(question).contains("最小變更");
    assertThat(question).contains("完整");
  }

  @Test
  void toRequest_nullPreviousHtml_questionUnchanged() {
    AgentRequest request = new AgentRequest("u1", "s1", "q", List.of(), List.of(), null);
    assertThat(mapperWithSampleRows(20).toRequest(request).params().inputData()
            .conversation().question())
        .isEqualTo("q");
  }
}
```

- [ ] **Step 2: 跑測試確認失敗**（COMPILE ERROR：類不存在）

- [ ] **Step 3: 實作 mapper**

```java
package com.erd.cowork.agent.provider.codegen;

import com.erd.cowork.agent.AgentFileContext;
import com.erd.cowork.agent.AgentRequest;
import com.erd.cowork.agent.provider.codegen.model.CodegenRequestDto;
import com.erd.cowork.config.AgentProperties;
import com.erd.cowork.parsing.model.FileProfile;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

/**
 * Maps the internal {@link AgentRequest} to the company codegen API wire format. Field-shape
 * adjustments after the company finalises the contract belong HERE and in the wire records only.
 */
@Component
@RequiredArgsConstructor
public class CodegenRequestMapper {

  /** spec：fileMeta.type 固定 "table"（上傳檔目前只有表格資料）。 */
  private static final String FILE_META_TYPE_TABLE = "table";

  private static final String PREVIOUS_HTML_INSTRUCTION_PREFIX =
      "\n\n以下是前一版 dashboard 的完整 HTML，請基於它做最小變更，"
          + "輸出完整 HTML、每一行程式碼都要寫出（NEVER 以註解省略程式碼）：\n```html\n";
  private static final String PREVIOUS_HTML_INSTRUCTION_SUFFIX = "\n```";

  private final AgentProperties agentProperties;

  public CodegenRequestDto toRequest(AgentRequest request) {
    List<CodegenRequestDto.FileMeta> fileMeta = new ArrayList<>();
    Map<String, List<Map<String, String>>> fileData = new LinkedHashMap<>();
    for (AgentFileContext fileContext : request.files()) {
      fileMeta.add(toFileMeta(fileContext));
      fileData.put(fileContext.alias(), toObjectRows(fileContext.profile()));
    }
    CodegenRequestDto.Conversation conversation =
        new CodegenRequestDto.Conversation(
            buildQuestion(request),
            request.history().stream()
                .map(entry -> new CodegenRequestDto.HistoryEntry(entry.sender(), entry.text()))
                .toList());
    return new CodegenRequestDto(
        new CodegenRequestDto.Params(
            new CodegenRequestDto.InputData(
                request.sessionId(), fileMeta, fileData, conversation)));
  }

  private CodegenRequestDto.FileMeta toFileMeta(AgentFileContext fileContext) {
    FileProfile profile = fileContext.profile();
    List<CodegenRequestDto.Column> columns =
        profile.columns().stream()
            .map(column -> new CodegenRequestDto.Column(column.colType(), column.colName()))
            .toList();
    return new CodegenRequestDto.FileMeta(
        fileContext.name(),
        fileContext.alias(),
        FILE_META_TYPE_TABLE,
        new CodegenRequestDto.Metadata(profile.rowCount(), profile.colCount(), columns));
  }

  /** sampleRows（List of String cells）→ header→value 物件列，上限 config sample-rows。 */
  private List<Map<String, String>> toObjectRows(FileProfile profile) {
    int limit = agentProperties.codegen().sampleRows();
    List<Map<String, String>> objectRows = new ArrayList<>();
    for (List<String> row : profile.sampleRows()) {
      if (objectRows.size() >= limit) {
        break;
      }
      Map<String, String> objectRow = new LinkedHashMap<>();
      for (int columnIndex = 0;
          columnIndex < profile.headers().size() && columnIndex < row.size();
          columnIndex++) {
        objectRow.put(profile.headers().get(columnIndex), row.get(columnIndex));
      }
      objectRows.add(objectRow);
    }
    return objectRows;
  }

  private String buildQuestion(AgentRequest request) {
    if (!StringUtils.hasText(request.previousArtifactHtml())) {
      return request.question();
    }
    return request.question()
        + PREVIOUS_HTML_INSTRUCTION_PREFIX
        + request.previousArtifactHtml()
        + PREVIOUS_HTML_INSTRUCTION_SUFFIX;
  }
}
```

（wire records 依上方 Interfaces 區塊逐字建立兩檔。）

- [ ] **Step 4: 跑測試確認通過**

Run: `./mvnw test -Dtest=CodegenRequestMapperTest`（PASS）

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/provider/codegen backend/src/test/java/com/erd/cowork/agent/provider/codegen
git commit -m "feat(backend): codegen wire model and request mapper"
```

---

### Task 3: InternalCodegenProvider（HTTP＋偽串流＋修復相容）

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/provider/codegen/InternalCodegenProvider.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/provider/codegen/InternalCodegenProviderTest.java`

**Interfaces:**
- Consumes: Task 1 `AgentProperties.Codegen`、Task 2 `CodegenRequestMapper`＋wire records、既有 `HtmlExtractionHelper`（non-bean，per-stream `new`）、`ErrorCode.AGENT_ERROR`
- Produces: `DashboardAgentProvider` bean（`@ConditionalOnProperty(prefix="erd.agent", name="provider", havingValue="internal-codegen")`）

**行為規格：**
1. `generate()` 立即回 `ProviderResult`；events flux 訂閱時，以 question hash 選定 step 序列（設計決策 2 的三組），依序：
   `d1 RUNNING`→`d1 SUCCESS`→`d2 RUNNING`→（WebClient POST；`apiKey` 非空 → `Authorization: Bearer {apiKey}`）→ 收到 response：
   - `error` 非空 → `d2 ERROR`＋`ErrorEvent(AGENT_ERROR, error)`，extraction 回空（`new ExtractionResult("", null, null)`）
   - 正常 → `d2 SUCCESS`→`d3 RUNNING`→ answer 切塊（chunkSize，surrogate-pair 安全）`delayElements(chunkDelayMillis)` → `helper.apply(...)` 且 **`filter(event -> !(event instanceof CodeEvent))`** → `d3 SUCCESS`；extraction supplier = `helper::result`
2. timeout（`timeoutSeconds`）與 HTTP 錯誤 → Flux.error 傳播（orchestrator 既有路徑處理）
3. `chunkDelayMillis <= 0` 時不加 delay（測試用 0 同步重播）
4. 進入點 log：`codegen generate session={} files={} historyLen={} hasPrevHtml={}`（NEVER log 內容）

- [ ] **Step 1: 失敗測試**（核心案例；MockWebServer 起假 codegen API）

```java
package com.erd.cowork.agent.provider.codegen;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.agent.AgentRequest;
import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.CodeEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.extraction.ExtractionResult;
import com.erd.cowork.config.AgentProperties;
import java.util.List;
import java.util.concurrent.TimeUnit;
import mockwebserver3.MockResponse;
import mockwebserver3.MockWebServer;
import mockwebserver3.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class InternalCodegenProviderTest {

  private MockWebServer server;

  @BeforeEach
  void setUp() throws Exception {
    server = new MockWebServer();
    server.start();
  }

  @AfterEach
  void tearDown() throws Exception {
    server.shutdown();
  }

  private InternalCodegenProvider provider(String apiKey) {
    AgentProperties.Codegen codegen =
        new AgentProperties.Codegen(
            server.url("/").toString(), "/api/v1/generate", apiKey, 20, 5, 8, 0L);
    AgentProperties properties = new AgentProperties("internal-codegen", null, null, codegen);
    return new InternalCodegenProvider(properties, new CodegenRequestMapper(properties));
  }

  private AgentRequest simpleRequest() {
    return new AgentRequest("u1", "s1", "做一張圖", List.of(), List.of(), null);
  }

  @Test
  void generate_answerWithHtmlFence_replaysTokenAndCodeEventsAndExtractsHtml() {
    String answer = "說明文字\n```html\n<html><body>hi</body></html>\n```\n後記";
    server.enqueue(
        new MockResponse()
            .newBuilder()
            .code(200)
            .addHeader("Content-Type", "application/json")
            .body("{\"answer\":" + com.fasterxml.jackson.databind.json.JsonMapper.builder()
                .build().valueToTree(answer) + ",\"error\":null}")
            .build());

    InternalCodegenProvider codegenProvider = provider("");
    var result = codegenProvider.generate(simpleRequest());
    List<AgentEvent> events = result.events().collectList().block();

    // step 序列：d1/d2/d3 各出現 RUNNING 與 SUCCESS，且 d1 為首事件
    assertThat(events.get(0)).isInstanceOf(StepEvent.class);
    List<String> stepSignature =
        events.stream().filter(StepEvent.class::isInstance).map(StepEvent.class::cast)
            .map(step -> step.stepKey() + ":" + step.status()).toList();
    assertThat(stepSignature)
        .containsExactly(
            "d1:RUNNING", "d1:SUCCESS", "d2:RUNNING", "d2:SUCCESS", "d3:RUNNING", "d3:SUCCESS");
    // fence 外 → TOKEN；CODE 事件被過濾（codegen 不顯示產生中面板）
    String tokenConcat =
        events.stream().filter(TokenEvent.class::isInstance)
            .map(event -> ((TokenEvent) event).delta()).reduce("", String::concat);
    assertThat(tokenConcat).contains("說明文字").contains("後記").doesNotContain("<body>");
    assertThat(events).noneMatch(CodeEvent.class::isInstance);
    // extraction：html 抽出、answerText 不含 html
    ExtractionResult extraction = result.extraction().get();
    assertThat(extraction.html()).isEqualTo("<html><body>hi</body></html>");
    assertThat(extraction.answerText()).contains("說明文字");
  }

  @Test
  void generate_postsCompanyWireFormat() throws Exception {
    server.enqueue(new MockResponse.Builder().code(200)
        .addHeader("Content-Type", "application/json")
        .body("{\"answer\":\"ok\",\"error\":null}").build());

    provider("").generate(simpleRequest()).events().collectList().block();

    RecordedRequest recorded = server.takeRequest(2, TimeUnit.SECONDS);
    assertThat(recorded.getPath()).isEqualTo("/api/v1/generate");
    String body = recorded.getBody().readUtf8();
    assertThat(body).contains("\"params\"").contains("\"inputData\"")
        .contains("\"sessionId\":\"s1\"").contains("\"question\":\"做一張圖\"");
  }

  @Test
  void generate_bearerAuth_sendsAuthorizationHeader() throws Exception {
    server.enqueue(new MockResponse.Builder().code(200)
        .addHeader("Content-Type", "application/json")
        .body("{\"answer\":\"ok\",\"error\":null}").build());

    provider("secret-key").generate(simpleRequest()).events().collectList().block();

    RecordedRequest recorded = server.takeRequest(2, TimeUnit.SECONDS);
    assertThat(recorded.getHeaders().get("Authorization")).isEqualTo("Bearer secret-key");
  }

  @Test
  void generate_errorField_emitsAgentErrorEventAndEmptyExtraction() {
    server.enqueue(new MockResponse.Builder().code(200)
        .addHeader("Content-Type", "application/json")
        .body("{\"answer\":null,\"error\":\"quota exceeded\"}").build());

    var result = provider("").generate(simpleRequest());
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).contains(new ErrorEvent("AGENT_ERROR", "quota exceeded"));
    assertThat(events.stream().filter(StepEvent.class::isInstance).map(StepEvent.class::cast)
            .map(step -> step.stepKey() + ":" + step.status()).toList())
        .endsWith("d2:ERROR");
    assertThat(result.extraction().get().html()).isNull();
  }

  @Test
  void generate_http500_propagatesAsFluxError() {
    server.enqueue(new MockResponse.Builder().code(500).body("boom").build());

    assertThatThrownBy(
            () -> provider("").generate(simpleRequest()).events().collectList().block())
        .isInstanceOf(Exception.class);
  }

  @Test
  void generate_sameQuestion_picksSameStepSequence() {
    server.enqueue(new MockResponse.Builder().code(200)
        .addHeader("Content-Type", "application/json")
        .body("{\"answer\":\"ok\",\"error\":null}").build());
    server.enqueue(new MockResponse.Builder().code(200)
        .addHeader("Content-Type", "application/json")
        .body("{\"answer\":\"ok\",\"error\":null}").build());

    java.util.function.Function<Void, List<String>> runTitles =
        ignored ->
            provider("").generate(simpleRequest()).events().collectList().block().stream()
                .filter(StepEvent.class::isInstance).map(StepEvent.class::cast)
                .map(StepEvent::title).distinct().toList();
    assertThat(runTitles.apply(null)).isEqualTo(runTitles.apply(null));
  }

  @Test
  void generate_repairStyleRequest_previousHtmlReachesQuestion() throws Exception {
    server.enqueue(new MockResponse.Builder().code(200)
        .addHeader("Content-Type", "application/json")
        .body("{\"answer\":\"```html\\n<html>fixed</html>\\n```\",\"error\":null}").build());

    AgentRequest repairRequest =
        new AgentRequest("u1", "s1", "以下 HTML 中有 JavaScript 語法錯誤，請修復",
            List.of(), List.of(), "<html>broken</html>");
    provider("").generate(repairRequest).events().collectList().block();

    String body = server.takeRequest(2, TimeUnit.SECONDS).getBody().readUtf8();
    assertThat(body).contains("broken").contains("最小變更");
  }
}
```

（若專案的 MockWebServer 版本 API 與上面 builder 寫法不符，比照 `OpenAICompatibleProviderTest` 既有寫法調整——測試語意不變。）

- [ ] **Step 2: 跑測試確認失敗**（COMPILE ERROR）

- [ ] **Step 3: 實作 provider**

```java
package com.erd.cowork.agent.provider.codegen;

import com.erd.cowork.agent.AgentRequest;
import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.CodeEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.extraction.ExtractionResult;
import com.erd.cowork.agent.extraction.HtmlExtractionHelper;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.provider.codegen.model.CodegenResponseDto;
import com.erd.cowork.config.AgentProperties;
import com.erd.cowork.exception.ErrorCode;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/**
 * Company codegen API provider. No SSE upstream; the full answer is replayed as a pseudo-stream
 * through {@link HtmlExtractionHelper}. CODE events are filtered out (no live-HTML panel for
 * codegen); progress is conveyed by canned d1–d3 step sequences (question-hash rotation).
 * Keep-alive during the 30s–120s wait is covered by MessageController's 15s {@code :ka} heartbeat.
 */
@Component
@ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "internal-codegen")
@Slf4j
public class InternalCodegenProvider implements DashboardAgentProvider {

  /** Canned step-title sequences；以 question hash 輪替，讓等待期步驟不至於千篇一律。 */
  private static final List<List<String>> STEP_SEQUENCES =
      List.of(
          List.of("整理資料摘要", "呼叫生成服務", "解析生成結果"),
          List.of("準備生成請求", "等待生成服務回應", "組裝儀表板"),
          List.of("分析檔案結構", "生成儀表板", "整理輸出"));

  private final AgentProperties.Codegen codegenProperties;
  private final CodegenRequestMapper requestMapper;
  private final WebClient webClient;

  public InternalCodegenProvider(
      AgentProperties agentProperties, CodegenRequestMapper requestMapper) {
    this.codegenProperties = agentProperties.codegen();
    this.requestMapper = requestMapper;
    this.webClient = WebClient.builder().baseUrl(codegenProperties.baseUrl()).build();
  }

  @Override
  public ProviderResult generate(AgentRequest request) {
    log.info(
        "codegen generate session={} files={} historyLen={} hasPrevHtml={}",
        request.sessionId(),
        request.files().size(),
        request.history().size(),
        StringUtils.hasText(request.previousArtifactHtml()));

    List<String> stepTitles =
        STEP_SEQUENCES.get(Math.floorMod(request.question().hashCode(), STEP_SEQUENCES.size()));
    HtmlExtractionHelper extractionHelper = new HtmlExtractionHelper();
    AtomicReference<ExtractionResult> errorExtraction = new AtomicReference<>();

    Flux<AgentEvent> events =
        Flux.concat(
            Flux.just(
                step("d1", stepTitles.get(0), StepStatus.RUNNING),
                step("d1", stepTitles.get(0), StepStatus.SUCCESS),
                step("d2", stepTitles.get(1), StepStatus.RUNNING)),
            Flux.defer(() -> callApi(request))
                .flatMapMany(
                    response -> {
                      if (StringUtils.hasText(response.error())) {
                        errorExtraction.set(new ExtractionResult("", null, null));
                        return Flux.just(
                            step("d2", stepTitles.get(1), StepStatus.ERROR),
                            new ErrorEvent(ErrorCode.AGENT_ERROR.name(), response.error()));
                      }
                      String answer = response.answer() == null ? "" : response.answer();
                      return Flux.concat(
                          Flux.just(
                              step("d2", stepTitles.get(1), StepStatus.SUCCESS),
                              step("d3", stepTitles.get(2), StepStatus.RUNNING)),
                          extractionHelper
                              .apply(toChunkFlux(answer))
                              .filter(event -> !(event instanceof CodeEvent)),
                          Flux.just(step("d3", stepTitles.get(2), StepStatus.SUCCESS)));
                    }));

    return new ProviderResult(
        events,
        () ->
            errorExtraction.get() != null ? errorExtraction.get() : extractionHelper.result());
  }

  private static AgentEvent step(String stepKey, String title, StepStatus status) {
    return new StepEvent(stepKey, title, null, status);
  }

  private Mono<CodegenResponseDto> callApi(AgentRequest request) {
    WebClient.RequestBodySpec spec =
        webClient.post().uri(codegenProperties.path()).contentType(MediaType.APPLICATION_JSON);
    if (StringUtils.hasText(codegenProperties.apiKey())) {
      spec = spec.header("Authorization", "Bearer " + codegenProperties.apiKey());
    }
    return spec.bodyValue(requestMapper.toRequest(request))
        .retrieve()
        .bodyToMono(CodegenResponseDto.class)
        .timeout(Duration.ofSeconds(codegenProperties.timeoutSeconds()));
  }

  /** answer 依 chunkSize（surrogate-pair 安全）切塊；chunkDelayMillis<=0 時不加 delay（測試用）。 */
  private Flux<String> toChunkFlux(String answer) {
    List<String> chunks = new ArrayList<>();
    int index = 0;
    while (index < answer.length()) {
      int end = Math.min(index + codegenProperties.chunkSize(), answer.length());
      if (end < answer.length() && Character.isHighSurrogate(answer.charAt(end - 1))) {
        end--;
      }
      chunks.add(answer.substring(index, end));
      index = end;
    }
    Flux<String> chunkFlux = Flux.fromIterable(chunks);
    return codegenProperties.chunkDelayMillis() > 0
        ? chunkFlux.delayElements(Duration.ofMillis(codegenProperties.chunkDelayMillis()))
        : chunkFlux;
  }
}
```

注意：`ExtractionResult` 建構子簽名以現檔為準（`(answerText, html, questions)`）；`step()` 靜態工廠讓序列宣告可讀。

- [ ] **Step 4: 跑測試確認通過＋全 suite 綠**

Run: `./mvnw test -Dtest=InternalCodegenProviderTest`（PASS）→ `./mvnw test`（全綠；確認 openai 預設 context 不受影響——`internal-codegen` conditional 下該 bean 不建立）

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/provider/codegen backend/src/test/java/com/erd/cowork/agent/provider/codegen
git commit -m "feat(backend): internal codegen provider with pseudo-streaming"
```

---

### Task 4: 生成期修復 gate＋provider 包分類

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java`（repair 區段 gate）
- Move: `backend/src/main/java/com/erd/cowork/agent/provider/OpenAICompatibleProvider.java` → `provider/openai/`
- Move: `backend/src/main/java/com/erd/cowork/agent/provider/TokenExchangeClient.java` → `provider/openai/`
- Test: `AgentOrchestratorRepairTest`（Modify，加 gate 案例）；兩個被移動類的測試檔 package 同步移動

**Interfaces:**
- Consumes: `AgentProperties.provider()`（既有）
- Produces: `AgentOrchestrator` private `boolean isGenerationRepairEnabled()`；`provider/openai/` 子包

- [ ] **Step 1: 失敗測試**——`AgentOrchestratorRepairTest` 加案例：

```java
@Test
void finalize_codegenProvider_skipsGenerationRepairEntirely() {
  // fixture：AgentProperties.provider() 回 "internal-codegen"、repair.enabled=true、
  // html 含明確 JS 語法錯誤（既有壞 html fixture 重用）
  // 斷言：jsSyntaxValidator 與 codeOmissionValidator 皆 never() 被呼叫、
  //       無任何 r1 StepEvent、artifact 以原樣 html 持久化
}
```

（fixture 與斷言寫法完全比照該測試檔既有 repair 案例的 mock 結構，僅 provider 值不同；完整寫出，不留註解虛碼。）

- [ ] **Step 2: 跑測試確認失敗**（gate 尚不存在，validator 仍被呼叫）

- [ ] **Step 3: 實作 gate**——finalize 內既有 `boolean repairEnabled = agentProperties.repair() != null && agentProperties.repair().enabled();` 改為呼叫：

```java
/**
 * Generation-time repair (syntax + omission) only applies to streaming LLM providers whose
 * output needs hardening (gpt-oss). The company codegen service handles quality server-side —
 * for it the only repair path is the user-confirmed browser repair, which is independent.
 */
private boolean isGenerationRepairEnabled() {
  return agentProperties.repair() != null
      && agentProperties.repair().enabled()
      && !"internal-codegen".equals(agentProperties.provider());
}
```

- [ ] **Step 4: package 移動**——兩個 openai 專屬類移至 `provider/openai/`（IDE-style move：package 行＋所有 import 引用＋對應測試檔同步；`git mv` 保 rename 歷史）

- [ ] **Step 5: 全 suite 綠＋Commit**

Run: `./mvnw test`（全綠）

```bash
git add -A backend
git commit -m "refactor(backend): generation-repair provider gate and openai provider package"
```

---

### Task 5: 整合驗證＋文件

**Files:**
- Test: `backend/src/test/java/com/erd/cowork/web/MessageControllerCodegenTest.java`（Create）
- Modify: `docs/architecture.md`：
  1. 整體架構圖 Codegen 節點註記「M5 完整實作」
  2. 「生成品質管線」開頭加一句適用範圍：「以下生成期檢查僅適用 openai-compatible（串流 LLM）路徑；internal-codegen 由服務端把關品質，生成期修復整段跳過，僅保留瀏覽器確認制修復」
  3. 新增「Provider 檔案分類地圖」小節：`provider/` 根＝共用 SPI（DashboardAgentProvider/ProviderResult）；`provider/openai/`＝OpenAICompatibleProvider/TokenExchangeClient＋`agent/prompt/`（system-prompt.vm 只服務此路徑）；`provider/codegen/`＝mapper/wire model/provider；共用＝`extraction/`（HtmlExtractionHelper/BareHtmlUtils）、`repair/`（僅瀏覽器修復對兩者共用；生成期修復 openai 專屬）
- Modify: `CLAUDE.md`（LLM providers 句：InternalCodegenProvider 從「v1 僅骨架 stub」改為「M5 完整實作（bearer auth、canned steps、無 CODE 面板、生成期修復跳過）」；狀態行補 M5 完成）

**Interfaces:**
- Consumes: Task 1–3 全部

- [ ] **Step 1: 整合測試（失敗）**——`@SpringBootTest` with `erd.agent.provider=internal-codegen` + MockWebServer via `@DynamicPropertySource`，走真 MessageController SSE 端到端：

```java
package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import mockwebserver3.MockResponse;
import mockwebserver3.MockWebServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class MessageControllerCodegenTest {

  static MockWebServer codegenServer;

  @Autowired WebTestClient webTestClient;

  @BeforeAll
  static void startServer() throws Exception {
    codegenServer = new MockWebServer();
    codegenServer.start();
  }

  @AfterAll
  static void stopServer() throws Exception {
    codegenServer.shutdown();
  }

  @DynamicPropertySource
  static void codegenProperties(DynamicPropertyRegistry registry) {
    registry.add("erd.agent.provider", () -> "internal-codegen");
    registry.add("erd.agent.codegen.base-url", () -> codegenServer.url("/").toString());
    registry.add("erd.agent.codegen.chunk-delay-millis", () -> "0");
  }

  @Test
  void streamMessage_codegenProvider_endToEndProducesArtifactEvent() {
    codegenServer.enqueue(new MockResponse.Builder().code(200)
        .addHeader("Content-Type", "application/json")
        .body("{\"answer\":\"好的\\n```html\\n<html><head></head><body>d</body></html>\\n```\",\"error\":null}")
        .build());

    // 建 session（X-User-Id 走既有慣例）
    String sessionId =
        webTestClient.post().uri("/api/sessions").header("X-User-Id", "cg-user")
            .exchange().expectStatus().isOk()
            .returnResult(java.util.Map.class).getResponseBody().blockFirst()
            .get("id").toString();

    String sse =
        webTestClient.post().uri("/api/sessions/" + sessionId + "/messages")
            .header("X-User-Id", "cg-user")
            .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
            .bodyValue("{\"question\":\"做個 dashboard\"}")
            .exchange().expectStatus().isOk()
            .returnResult(String.class).getResponseBody()
            .collectList().block(Duration.ofSeconds(30)).toString();

    assertThat(sse).contains("\"type\":\"STEP\"");
    assertThat(sse).doesNotContain("\"type\":\"CODE\"");
    assertThat(sse).contains("\"type\":\"ARTIFACT\"");
  }
}
```

（session 建立回應欄位與既有 `MessageControllerTest` 寫法對齊——以現檔慣例為準改寫此測試的 plumbing，斷言語意不變：STEP d1／CODE／ARTIFACT 三事件出現。）

- [ ] **Step 2: 跑整合測試**（先 FAIL 於 plumbing／後 PASS）

Run: `./mvnw test -Dtest=MessageControllerCodegenTest`

- [ ] **Step 3: 文件更新**（CLAUDE.md 兩處字句＋architecture.md 兩處，如 Files 段所述；一字不多改）

- [ ] **Step 4: 全 suite**

Run: `./mvnw test`（全綠）

- [ ] **Step 5: Commit**

```bash
git add backend/src/test/java/com/erd/cowork/web/MessageControllerCodegenTest.java docs/architecture.md CLAUDE.md
git commit -m "feat(backend): codegen end-to-end integration test and docs"
```

---

## 完成定義

- [ ] `ERD_AGENT_PROVIDER=internal-codegen`＋MockWebServer 假 API 下：SSE 端到端出現 d1–d3 canned steps／TOKEN 打字／ARTIFACT，**且無 CODE 事件**；artifact 正常入庫（storage key＋Version 標題＋asset profile 全部沿用既有 writer）
- [ ] 生成期修復（語法／省略／r1）對 codegen 整段跳過（Task 4 gate 測試釘住）；瀏覽器確認制修復照常可用（repair 走 `provider.generate`，Task 3 repair-style 測試釘住 prevHtml 進 question）
- [ ] codegen 回應無 THINKING → 前端 Working on it 不可展開（既有條件式，無需改動）
- [ ] `error` 欄位 → AGENT_ERROR 事件＋訊息持久化（orchestrator 既有路徑）；timeout 120s
- [ ] 預設 openai-compatible 行為零變化；全 suite 綠
- [ ] 公司 API 欄位若有出入，調整點只在 `codegen/model/` 兩檔＋mapper（plan 內已標記）

## 明確不做（本輪）

- 公司 API 真實連線驗證（無外網可達的公司端點；上線時以 env 設定切換即可）
- codegen 專屬 system prompt／行為規則注入（公司 API 服務端自帶；若產出品質需要 client 端補 prompt，另開一波）
- `[[step:]]`／```questions 對 codegen answer 的鍵位防碰撞（目前公司 API 不產這些標記，YAGNI）
