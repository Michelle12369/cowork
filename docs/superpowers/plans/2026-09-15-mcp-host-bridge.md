# MCP 宿主 hop ②③ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓產品宿主能開 connector dashboard——前端回應 iframe 的 `erd-mcp-call`, Java 提供 `POST /api/artifacts/{id}/mcp-call` 代打 deepagent `POST /tool-call`.

**Architecture:** Java 新增一支非串流 WebClient（`AnalysisToolCallClient`, 照 `AnalysisBrowserRepairClient`）與一個 service（ownership → session 允許清單 → catalog → client）, deepagent 2xx body 以原始字串直通, 非 2xx 與例外全部折成 200 + `{error:{code,message}}`. 前端一個 hook（`useMcpBridge`）掛在 `ArtifactPanel` 與 `ArtifactFullscreenPage`, 驗 `event.source`, 打 API, 60 秒逾時, 結果原樣貼回 iframe. 不設 in-flight 上限.

**Tech Stack:** Spring Boot 3 / WebClient / okhttp MockWebServer / Mockito; React 18 / axios / Vitest + RTL.

**Spec:** `docs/superpowers/specs/2026-09-15-mcp-host-bridge-design.md`（決策 H1–H8）; 契約來源 `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md` §7, 錯誤碼 `docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md` §2.

## Global Constraints

- Java 17; constructor injection; `@RequiredArgsConstructor`; DTO 是 record 放 `web.dto`; 例外放 `exception`; `@Slf4j`; google-java-format 由 hook 自動跑.
- 變數 NEVER 1–2 字元名; 描述性單詞.
- 註解: docstring 最多 3 行、行內註解最多 2 行, 只寫用途與注意事項, 半形標點, NEVER 寫 spec 編號／commit hash／事故敘事.
- 模型讀的文字一律英文; 使用者看的維持中文. 本 plan 新增的 `message` 字串是 viewer 看的錯誤卡文字, 與 deepagent 既有模板一致用英文.
- 五條不變量: `data` 原樣直通; `args` 原樣直通; SSO 只在 header（不進 body、log、postMessage）; 成功／失敗只有 `{data}` 或 `{error:{code,message}}` 兩種形狀; 一次呼叫一次 handler.
- log NEVER 記 `args` 值、header 值、`data`; 只記 arg keys.
- 前端: `React.FC<Props>`, handler 用 `useCallback`, `useEffect` 回 cleanup, NEVER `any`, function 明確 return type, `import type`, API 走 `apiClient` 相對路徑 `/api`.
- 測試命名 `methodName_condition_expectedBehavior`（Java）; 前端斷言元素級行為, 不 snapshot.
- 驗證指令的輸出 NEVER 接 `| tail`; 要看結尾就先跑完再看.
- Java 編譯: `JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home`（release-17）. 後端測試在 `backend/` 下跑 `./mvnw test`; 單一測試 `./mvnw test -Dtest=ClassName`. 前端在 `frontend/` 下 `npm test -- <path>`.
- PR 暫不開（H8）. commit 訊息中文, 結尾帶 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File Structure

Java（`backend/src/main/java/com/erd/cowork/`）:

| 檔案 | 責任 |
|---|---|
| `agent/mcp/McpErrorCode.java` | 五個 code 的 enum |
| `agent/mcp/McpCallError.java`, `agent/mcp/McpCallFailure.java` | `{error:{code,message}}` 的兩個 record |
| `agent/mcp/McpCallErrorWriter.java` | `@Component`, 把 code + message 序列化成 JSON 字串; client 與 service 共用 |
| `config/AnalysisAgentProperties.java` | 加 `toolCallTimeoutSeconds` |
| `agent/provider/analysis/AnalysisToolCallClient.java` | `POST /tool-call` WebClient; 2xx 原樣字串; 非 2xx／例外折成 error 字串 |
| `service/ArtifactMcpCallService.java` | ownership → 允許清單 → catalog → client; log 一行 |
| `web/dto/McpCallRequestDto.java` | request record |
| `web/ArtifactController.java` | 加 `POST /{id}/mcp-call` |

前端（`frontend/src/`）:

| 檔案 | 責任 |
|---|---|
| `types.ts` | `McpErrorCode`, `McpResult`, `McpCallMessage` |
| `config/mcpBridge.ts` | `MCP_BRIDGE_TIMEOUT_MS`, `MCP_ERROR_CODES` |
| `utils/mcpResult.ts` | `foldMcpFailure(error): McpResult` 純函式 |
| `api/artifactApi.ts` | `callArtifactMcp` |
| `hooks/useMcpBridge.ts` | listener、pending map、逾時、貼回 |
| `components/artifact/ArtifactPanel.tsx` | 呼叫 hook |
| `components/artifact/ArtifactFullscreenPage.tsx` | 建 `iframeRef`, 呼叫 hook |

---

### Task 1: Java 錯誤碼 enum、record、writer 與逾時 property

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/mcp/McpErrorCode.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/mcp/McpCallError.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/mcp/McpCallFailure.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/mcp/McpCallErrorWriter.java`
- Modify: `backend/src/main/java/com/erd/cowork/config/AnalysisAgentProperties.java`
- Modify: `backend/src/main/resources/application.properties:49`（其後加一行）
- Modify: `backend/src/main/resources/application-local.properties:48`（其後加一行）
- Test: `backend/src/test/java/com/erd/cowork/agent/mcp/McpCallErrorWriterTest.java`
- Test: `backend/src/test/java/com/erd/cowork/config/AnalysisAgentPropertiesTest.java`

**Interfaces:**
- Produces: `enum McpErrorCode { AUTH, RETRYABLE, TOOL_ERROR, INVALID_CALL, CONNECTOR_UNAVAILABLE }`; `record McpCallError(String code, String message)`; `record McpCallFailure(McpCallError error)`; `McpCallErrorWriter.write(McpErrorCode code, String message): String`（JSON 字串 `{"error":{"code":"...","message":"..."}}`）; `AnalysisAgentProperties.toolCallTimeoutSeconds(): int`（預設 60）.

- [ ] **Step 1: 寫 writer 測試**

```java
package com.erd.cowork.agent.mcp;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

class McpCallErrorWriterTest {

  private final McpCallErrorWriter writer = new McpCallErrorWriter(new ObjectMapper());

  @Test
  void write_codeAndMessage_returnsErrorEnvelopeJson() {
    String body = writer.write(McpErrorCode.RETRYABLE, "host returned HTTP 503; retry");

    assertThat(body)
        .isEqualTo(
            "{\"error\":{\"code\":\"RETRYABLE\",\"message\":\"host returned HTTP 503; retry\"}}");
  }

  @Test
  void write_messageWithQuotes_escapesInsteadOfBreakingJson() {
    String body = writer.write(McpErrorCode.TOOL_ERROR, "unknown fab \"FAB_Z\"");

    assertThat(body).contains("\\\"FAB_Z\\\"");
    assertThat(body).startsWith("{\"error\":{\"code\":\"TOOL_ERROR\"");
  }
}
```

- [ ] **Step 2: 寫 properties 測試**

```java
package com.erd.cowork.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class AnalysisAgentPropertiesTest {

  @Test
  void fourArgConstructor_defaultsToolCallTimeoutTo60() {
    AnalysisAgentProperties properties =
        new AnalysisAgentProperties("http://localhost:8000", "/data/uploads", 180, 64);

    assertThat(properties.toolCallTimeoutSeconds()).isEqualTo(60);
  }

  @Test
  void sevenArgConstructor_defaultsToolCallTimeoutTo60() {
    AnalysisAgentProperties properties =
        new AnalysisAgentProperties(
            "http://localhost:8000", "/data/uploads", 180, 64, "token", "X-A", "X-B");

    assertThat(properties.toolCallTimeoutSeconds()).isEqualTo(60);
    assertThat(properties.ssoTokenHeader()).isEqualTo("X-A");
  }

  @Test
  void canonicalConstructor_keepsExplicitToolCallTimeout() {
    AnalysisAgentProperties properties =
        new AnalysisAgentProperties(
            "http://localhost:8000", "/data/uploads", 180, 64, "token", "X-A", "X-B", 15);

    assertThat(properties.toolCallTimeoutSeconds()).isEqualTo(15);
  }
}
```

- [ ] **Step 3: 跑測試確認編譯失敗**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=McpCallErrorWriterTest,AnalysisAgentPropertiesTest -q`
Expected: 編譯錯誤, `McpCallErrorWriter`／8 參數建構子不存在.

- [ ] **Step 4: 建 enum 與兩個 record**

`McpErrorCode.java`:

```java
package com.erd.cowork.agent.mcp;

/** The five mcp() error codes shared by deepagent, Java and the frontend bridge. */
public enum McpErrorCode {
  AUTH,
  RETRYABLE,
  TOOL_ERROR,
  INVALID_CALL,
  CONNECTOR_UNAVAILABLE
}
```

`McpCallError.java`:

```java
package com.erd.cowork.agent.mcp;

/** The error half of an mcp() result; code is one of McpErrorCode, message is viewer-facing. */
public record McpCallError(String code, String message) {}
```

`McpCallFailure.java`:

```java
package com.erd.cowork.agent.mcp;

/** Wire shape of a failed mcp() result: {"error": {"code", "message"}}, never carries data. */
public record McpCallFailure(McpCallError error) {}
```

- [ ] **Step 5: 建 writer**

```java
package com.erd.cowork.agent.mcp;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

/** Serialises an mcp() failure into the JSON string that goes back to the page unchanged. */
@Component
@RequiredArgsConstructor
public class McpCallErrorWriter {

  private final ObjectMapper objectMapper;

  public String write(McpErrorCode code, String message) {
    try {
      return objectMapper.writeValueAsString(new McpCallFailure(new McpCallError(code.name(), message)));
    } catch (JsonProcessingException serializationError) {
      throw new IllegalStateException("cannot serialise mcp() failure " + code, serializationError);
    }
  }
}
```

- [ ] **Step 6: properties 加欄位**

把 `AnalysisAgentProperties` 改成:

```java
package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.ConstructorBinding;

/**
 * @param baseUrl agent-service 位址(如 http://agent-service:8000)
 * @param sourceRoot agent-service 視角的上傳檔根目錄;storageKey 接在其後組成完整路徑
 * @param requestTimeoutSeconds SSE 事件間的閒置逾時秒數(非總時長)。語意為 {@code Flux#timeout(Duration)},計時器隨每個事件重置;
 *     逾時由 {@code LangGraphAnalysisProvider} 轉為 {@code ErrorEvent},避免 {@code TimeoutException} 直接傳播
 * @param maxInMemorySizeMb WebClient 單一 SSE data line 緩衝上限(MB)。{@code DASHBOARD_HTML} 事件把完整
 *     dashboard HTML 與 spec JSON 塞進同一行,遠超 Spring 預設的 256KB,故需調大
 * @param bearerToken 打 agent-service 全部請求附帶的固定 Bearer token;空字串=不附帶(dev 預設)。 值一律由環境變數注入,NEVER 寫死於
 *     properties 檔
 * @param ssoTokenHeader 出站 {@code /chat} 請求上,ssoToken 值所附的 HTTP header 名稱;預設 {@code
 *     X-SSO-Token},internal 環境的 gateway 若要求不同名稱可另外配置(需與 deepagent 端 {@code SSO_TOKEN_HEADER} 保持一致)
 * @param ssoUrlHeader 出站 {@code /chat} 請求上,ssoUrl 值所附的 HTTP header 名稱;預設 {@code X-SSO-Url},語意同
 *     {@code ssoTokenHeader}
 * @param toolCallTimeoutSeconds 檢視期 {@code /tool-call} 單次呼叫的總逾時秒數;必須短於前端 bridge 的 60 秒逾時, 預設 60
 */
@ConfigurationProperties(prefix = "erd.agent.analysis")
public record AnalysisAgentProperties(
    String baseUrl,
    String sourceRoot,
    int requestTimeoutSeconds,
    int maxInMemorySizeMb,
    String bearerToken,
    String ssoTokenHeader,
    String ssoUrlHeader,
    int toolCallTimeoutSeconds) {

  private static final String DEFAULT_SSO_TOKEN_HEADER = "X-SSO-Token";
  private static final String DEFAULT_SSO_URL_HEADER = "X-SSO-Url";
  private static final int DEFAULT_TOOL_CALL_TIMEOUT_SECONDS = 60;

  /** 多建構子下指定 Spring 綁定用 canonical——不標會被當 JavaBean 找無參建構子而炸。 */
  @ConstructorBinding
  public AnalysisAgentProperties {}

  /** 既有 4 參數建構——bearerToken 預設空(不附帶)、header 名稱預設值,既有呼叫端與測試零改動。 */
  public AnalysisAgentProperties(
      String baseUrl, String sourceRoot, int requestTimeoutSeconds, int maxInMemorySizeMb) {
    this(baseUrl, sourceRoot, requestTimeoutSeconds, maxInMemorySizeMb, "");
  }

  /** 既有 5 參數建構(帶 bearerToken)——header 名稱預設值,既有呼叫端與測試零改動。 */
  public AnalysisAgentProperties(
      String baseUrl,
      String sourceRoot,
      int requestTimeoutSeconds,
      int maxInMemorySizeMb,
      String bearerToken) {
    this(
        baseUrl,
        sourceRoot,
        requestTimeoutSeconds,
        maxInMemorySizeMb,
        bearerToken,
        DEFAULT_SSO_TOKEN_HEADER,
        DEFAULT_SSO_URL_HEADER);
  }

  /** 既有 7 參數建構(帶 header 名稱)——tool-call 逾時預設 60 秒,既有測試零改動。 */
  public AnalysisAgentProperties(
      String baseUrl,
      String sourceRoot,
      int requestTimeoutSeconds,
      int maxInMemorySizeMb,
      String bearerToken,
      String ssoTokenHeader,
      String ssoUrlHeader) {
    this(
        baseUrl,
        sourceRoot,
        requestTimeoutSeconds,
        maxInMemorySizeMb,
        bearerToken,
        ssoTokenHeader,
        ssoUrlHeader,
        DEFAULT_TOOL_CALL_TIMEOUT_SECONDS);
  }
}
```

`application.properties` 第 49 行之後加:

```properties
erd.agent.analysis.tool-call-timeout-seconds=${ERD_AGENT_ANALYSIS_TOOL_CALL_TIMEOUT_SECONDS:60}
```

`application-local.properties` 第 48 行之後加:

```properties
erd.agent.analysis.tool-call-timeout-seconds=60
```

- [ ] **Step 7: 跑測試確認通過, 再跑全套確認既有 7 參數呼叫端沒壞**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=McpCallErrorWriterTest,AnalysisAgentPropertiesTest,LangGraphAnalysisProviderTest,CurrentUserFilterTest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/mcp backend/src/main/java/com/erd/cowork/config/AnalysisAgentProperties.java backend/src/main/resources/application.properties backend/src/main/resources/application-local.properties backend/src/test/java/com/erd/cowork/agent/mcp backend/src/test/java/com/erd/cowork/config/AnalysisAgentPropertiesTest.java
git commit -m "feat(backend): mcp() 錯誤碼 enum、失敗 envelope writer 與 tool-call 逾時 property"
```

---

### Task 2: `AnalysisToolCallClient`——`POST /tool-call` 直通與折疊

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/provider/analysis/AnalysisToolCallClient.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/provider/analysis/AnalysisToolCallClientTest.java`

**Interfaces:**
- Consumes: `McpCallErrorWriter.write(McpErrorCode, String)`, `AnalysisAgentProperties.toolCallTimeoutSeconds()`, `ConnectorSpec(id, name, url, bearerTokenKey)`.
- Produces: `Mono<String> call(ConnectorSpec connector, String tool, Map<String, Object> args, String ssoToken, String ssoUrl)`——永遠 emit 一個 JSON 字串, 永不 error.

- [ ] **Step 1: 寫測試**

```java
package com.erd.cowork.agent.provider.analysis;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.mcp.McpCallErrorWriter;
import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.config.AnalysisAgentProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Iterator;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

class AnalysisToolCallClientTest {

  private static final ConnectorSpec SALES =
      new ConnectorSpec("sales", "Sales", "http://mcp.local/sales", null);
  private static final String SSO_TOKEN = "secret-sso-token";
  private static final String SSO_URL = "http://sso.local";
  private static final Path FIXTURE =
      Path.of("..", "deepagent-service", "tests", "fixtures", "mcp_result_examples.json");

  private final ObjectMapper objectMapper = new ObjectMapper();
  private MockWebServer mockWebServer;
  private AnalysisToolCallClient client;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    mockWebServer.start();
    client = newClient("http://localhost:" + mockWebServer.getPort(), 30);
  }

  @AfterEach
  void tearDown() throws Exception {
    mockWebServer.shutdown();
  }

  private AnalysisToolCallClient newClient(String baseUrl, int toolCallTimeoutSeconds) {
    AnalysisAgentProperties properties =
        new AnalysisAgentProperties(
            baseUrl, "/data/uploads", 180, 64, "agent-bearer", "X-SSO-Token", "X-SSO-Url",
            toolCallTimeoutSeconds);
    return new AnalysisToolCallClient(
        properties, new McpCallErrorWriter(objectMapper), WebClient.builder());
  }

  private String call() {
    return client.call(SALES, "list_orders", Map.of("days", 30), SSO_TOKEN, SSO_URL).block();
  }

  private static MockResponse json(int status, String body) {
    return new MockResponse()
        .setResponseCode(status)
        .addHeader("Content-Type", "application/json")
        .setBody(body);
  }

  @Test
  void call_200_returnsBodyByteForByteIncludingEnvelopeAndFloatLiterals() {
    String body = "{\"data\":{\"result\":[{\"qty\":1.10,\"id\":\"A-1\"}],\"total\":1.0}}";
    mockWebServer.enqueue(json(200, body));

    assertThat(call()).isEqualTo(body);
  }

  @Test
  void call_200_fixtureExamplesPassThroughUnchanged() throws Exception {
    JsonNode examples = objectMapper.readTree(Files.readString(FIXTURE, StandardCharsets.UTF_8));
    Iterator<Map.Entry<String, JsonNode>> fields = examples.fields();
    while (fields.hasNext()) {
      String exampleBody = objectMapper.writeValueAsString(fields.next().getValue());
      mockWebServer.enqueue(json(200, exampleBody));

      assertThat(call()).isEqualTo(exampleBody);
    }
  }

  @Test
  void call_requestBody_carriesConnectorToolArgsAndSsoOnlyInHeaders() throws Exception {
    mockWebServer.enqueue(json(200, "{\"data\":[]}"));

    call();

    RecordedRequest request = mockWebServer.takeRequest();
    assertThat(request.getPath()).isEqualTo("/tool-call");
    assertThat(request.getHeader("Authorization")).isEqualTo("Bearer agent-bearer");
    assertThat(request.getHeader("X-SSO-Token")).isEqualTo(SSO_TOKEN);
    assertThat(request.getHeader("X-SSO-Url")).isEqualTo(SSO_URL);
    String body = request.getBody().readUtf8();
    assertThat(body).contains("\"connector\":{");
    assertThat(body).contains("\"id\":\"sales\"");
    assertThat(body).contains("\"url\":\"http://mcp.local/sales\"");
    assertThat(body).contains("\"tool\":\"list_orders\"");
    assertThat(body).contains("\"args\":{\"days\":30}");
    assertThat(body).doesNotContain(SSO_TOKEN);
    assertThat(body).doesNotContain(SSO_URL);
  }

  @Test
  void call_422_returnsInvalidCall() throws Exception {
    mockWebServer.enqueue(json(422, "{\"detail\":[{\"loc\":[\"body\",\"tool\"]}]}"));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("INVALID_CALL");
    assertThat(result.path("error").path("message").asText()).contains("422");
    assertThat(result.has("data")).isFalse();
  }

  @Test
  void call_503_returnsRetryableWithStatusInMessage() throws Exception {
    mockWebServer.enqueue(json(503, "upstream down"));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("RETRYABLE");
    assertThat(result.path("error").path("message").asText()).contains("503");
  }

  @Test
  void call_401_returnsConnectorUnavailable() throws Exception {
    mockWebServer.enqueue(json(401, "{\"detail\":\"bad bearer\"}"));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("CONNECTOR_UNAVAILABLE");
    assertThat(result.path("error").path("message").asText()).contains("401");
  }

  @Test
  void call_timeout_returnsRetryable() throws Exception {
    client = newClient("http://localhost:" + mockWebServer.getPort(), 1);
    mockWebServer.enqueue(json(200, "{\"data\":[]}").setBodyDelay(3, TimeUnit.SECONDS));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("RETRYABLE");
    assertThat(result.path("error").path("message").asText()).contains("timed out");
  }

  @Test
  void call_connectionRefused_returnsRetryable() throws Exception {
    int closedPort = mockWebServer.getPort();
    mockWebServer.shutdown();
    client = newClient("http://localhost:" + closedPort, 5);

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("RETRYABLE");
  }

  @Test
  void call_anyFailure_messageNeverContainsSsoValues() throws Exception {
    mockWebServer.enqueue(json(500, "boom " + SSO_TOKEN));

    String result = call();

    assertThat(result).doesNotContain(SSO_TOKEN);
    assertThat(result).doesNotContain(SSO_URL);
  }

  @Test
  void call_200EmptyBody_returnsRetryable() throws Exception {
    mockWebServer.enqueue(new MockResponse().setResponseCode(200));

    JsonNode result = objectMapper.readTree(call());

    assertThat(result.path("error").path("code").asText()).isEqualTo("RETRYABLE");
  }
}
```

- [ ] **Step 2: 跑測試確認編譯失敗**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=AnalysisToolCallClientTest -q`
Expected: 編譯錯誤, `AnalysisToolCallClient` 不存在.

- [ ] **Step 3: 實作 client**

```java
package com.erd.cowork.agent.provider.analysis;

import com.erd.cowork.agent.mcp.McpCallErrorWriter;
import com.erd.cowork.agent.mcp.McpErrorCode;
import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.logging.LogAnnotation;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeoutException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

/**
 * View-time bridge to deepagent's POST /tool-call: a 2xx body is returned as the raw string so
 * data reaches the page byte-for-byte; every other outcome becomes a 200-shaped error envelope.
 */
@Slf4j
@Component
@ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "langgraph-analysis")
@LogAnnotation
public class AnalysisToolCallClient {

  private final AnalysisAgentProperties analysisProperties;
  private final McpCallErrorWriter errorWriter;
  private final WebClient webClient;

  public AnalysisToolCallClient(
      AnalysisAgentProperties analysisProperties,
      McpCallErrorWriter errorWriter,
      WebClient.Builder webClientBuilder) {
    this.analysisProperties = analysisProperties;
    this.errorWriter = errorWriter;
    this.webClient =
        webClientBuilder
            .baseUrl(analysisProperties.baseUrl())
            .defaultHeaders(
                headers -> {
                  if (StringUtils.hasText(analysisProperties.bearerToken())) {
                    headers.setBearerAuth(analysisProperties.bearerToken());
                  }
                })
            .exchangeStrategies(
                ExchangeStrategies.builder()
                    .codecs(
                        configurer ->
                            configurer
                                .defaultCodecs()
                                .maxInMemorySize(
                                    analysisProperties.maxInMemorySizeMb() * 1024 * 1024))
                    .build())
            .build();
  }

  /** SSO values go only into headers; the returned Mono never errors, it always emits JSON. */
  public Mono<String> call(
      ConnectorSpec connector, String tool, Map<String, Object> args, String ssoToken, String ssoUrl) {
    Map<String, Object> requestBody = new HashMap<>();
    requestBody.put("connector", connector);
    requestBody.put("tool", tool);
    requestBody.put("args", args);

    return webClient
        .post()
        .uri("/tool-call")
        .headers(
            headers -> {
              if (StringUtils.hasText(ssoToken)) {
                headers.set(analysisProperties.ssoTokenHeader(), ssoToken);
              }
              if (StringUtils.hasText(ssoUrl)) {
                headers.set(analysisProperties.ssoUrlHeader(), ssoUrl);
              }
            })
        .bodyValue(requestBody)
        .exchangeToMono(
            response -> {
              HttpStatusCode status = response.statusCode();
              if (status.is2xxSuccessful()) {
                return response
                    .bodyToMono(String.class)
                    .defaultIfEmpty("")
                    .map(body -> StringUtils.hasText(body) ? body : emptyBodyFailure());
              }
              return response.releaseBody().thenReturn(foldStatus(status));
            })
        .timeout(Duration.ofSeconds(analysisProperties.toolCallTimeoutSeconds()))
        .onErrorResume(throwable -> Mono.just(foldThrowable(connector.id(), tool, throwable)));
  }

  private String foldStatus(HttpStatusCode status) {
    int statusValue = status.value();
    if (status.equals(HttpStatus.UNPROCESSABLE_ENTITY)) {
      return errorWriter.write(
          McpErrorCode.INVALID_CALL,
          "agent service rejected the call shape (HTTP 422); fix the dashboard");
    }
    if (status.is4xxClientError()) {
      return errorWriter.write(
          McpErrorCode.CONNECTOR_UNAVAILABLE,
          "agent service rejected this server's request (HTTP "
              + statusValue
              + "); ask the administrator");
    }
    return errorWriter.write(
        McpErrorCode.RETRYABLE, "agent service returned HTTP " + statusValue + "; retry");
  }

  private String foldThrowable(String connectorId, String tool, Throwable throwable) {
    // Only the exception class goes into the message: exception text can echo the request URL.
    String cause = throwable.getClass().getSimpleName();
    log.warn("tool-call transport failure connector={} tool={} cause={}", connectorId, tool, cause);
    if (throwable instanceof TimeoutException) {
      return errorWriter.write(
          McpErrorCode.RETRYABLE,
          "agent service timed out after "
              + analysisProperties.toolCallTimeoutSeconds()
              + " s; retry");
    }
    return errorWriter.write(
        McpErrorCode.RETRYABLE, "agent service unreachable (" + cause + "); retry");
  }

  private String emptyBodyFailure() {
    return errorWriter.write(McpErrorCode.RETRYABLE, "agent service returned an empty body; retry");
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=AnalysisToolCallClientTest -q`
Expected: PASS（10 條）. 若 `call_timeout_returnsRetryable` 出現 `ReadTimeoutException` 而非 `TimeoutException`, 表示 Netty 的讀逾時先到; 把測試的 `setBodyDelay` 改成 `setHeadersDelay(3, TimeUnit.SECONDS)` 再跑, 兩者都應落在 `RETRYABLE`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/provider/analysis/AnalysisToolCallClient.java backend/src/test/java/com/erd/cowork/agent/provider/analysis/AnalysisToolCallClientTest.java
git commit -m "feat(backend): AnalysisToolCallClient——/tool-call 2xx 原樣直通, 非 2xx 與逾時折成 mcp() 錯誤 envelope"
```

---

### Task 3: `ArtifactMcpCallService`——ownership、允許清單、catalog、client

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/service/ArtifactMcpCallService.java`
- Test: `backend/src/test/java/com/erd/cowork/service/ArtifactMcpCallServiceTest.java`

**Interfaces:**
- Consumes: `ArtifactRepository.findById`, `SessionGuard.loadOwned(String): ChatSession`, `ChatSession.getSelectedConnectors(): List<String>`, `ConnectorCatalogService.resolveSpecs(List<String>): List<ConnectorSpec>`（缺 → `NotFoundException`）, `AnalysisToolCallClient.call(...)`, `McpCallErrorWriter.write`, `CoworkContextHolder.ssoToken()/ssoUrl()`.
- Produces: `String call(String artifactId, String connectorId, String tool, Map<String, Object> args)`——回 JSON 字串; 只在 artifact 不存在／非本人時拋 `NotFoundException`.

- [ ] **Step 1: 寫測試**

```java
package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.erd.cowork.agent.mcp.McpCallErrorWriter;
import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.agent.provider.analysis.AnalysisToolCallClient;
import com.erd.cowork.context.CoworkContext;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.repo.ArtifactRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import reactor.core.publisher.Mono;

@ExtendWith(MockitoExtension.class)
class ArtifactMcpCallServiceTest {

  private static final ConnectorSpec SALES =
      new ConnectorSpec("sales", "Sales", "http://mcp.local/sales", null);
  private static final Map<String, Object> ARGS = Map.of("days", 30, "region", "TW");

  @Mock ArtifactRepository artifacts;
  @Mock SessionGuard sessionGuard;
  @Mock ConnectorCatalogService connectorCatalogService;
  @Mock ObjectProvider<AnalysisToolCallClient> toolCallClientProvider;
  @Mock AnalysisToolCallClient toolCallClient;

  private final ObjectMapper objectMapper = new ObjectMapper();
  private ArtifactMcpCallService service;
  private ListAppender<ILoggingEvent> logAppender;

  @BeforeEach
  void setUp() {
    service =
        new ArtifactMcpCallService(
            artifacts,
            sessionGuard,
            connectorCatalogService,
            toolCallClientProvider,
            new McpCallErrorWriter(objectMapper),
            objectMapper);
    CoworkContextHolder.set(CoworkContext.external("user-1", "http://sso.local", "sso-secret"));
    logAppender = new ListAppender<>();
    logAppender.start();
    ((Logger) LoggerFactory.getLogger(ArtifactMcpCallService.class)).addAppender(logAppender);
  }

  @AfterEach
  void tearDown() {
    CoworkContextHolder.clear();
    ((Logger) LoggerFactory.getLogger(ArtifactMcpCallService.class)).detachAppender(logAppender);
  }

  private void givenOwnedArtifactWithConnectors(List<String> selectedConnectors) {
    Artifact artifact = new Artifact();
    artifact.setId("art-1");
    artifact.setSessionId("session-1");
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setSelectedConnectors(selectedConnectors);
    when(sessionGuard.loadOwned("session-1")).thenReturn(session);
  }

  private JsonNode callAsJson() throws Exception {
    return objectMapper.readTree(service.call("art-1", "sales", "list_orders", ARGS));
  }

  @Test
  void call_ownedArtifactWithAllowedConnector_forwardsToClientWithSsoAndReturnsBodyUnchanged()
      throws Exception {
    givenOwnedArtifactWithConnectors(List.of("sales", "crm"));
    when(connectorCatalogService.resolveSpecs(List.of("sales"))).thenReturn(List.of(SALES));
    when(toolCallClientProvider.getIfAvailable()).thenReturn(toolCallClient);
    String body = "{\"data\":{\"result\":[{\"qty\":1.10}]}}";
    when(toolCallClient.call(eq(SALES), eq("list_orders"), eq(ARGS), eq("sso-secret"), eq("http://sso.local")))
        .thenReturn(Mono.just(body));

    String result = service.call("art-1", "sales", "list_orders", ARGS);

    assertThat(result).isEqualTo(body);
  }

  @Test
  void call_artifactMissing_throwsNotFound() {
    when(artifacts.findById("missing")).thenReturn(Optional.empty());

    assertThatThrownBy(() -> service.call("missing", "sales", "list_orders", ARGS))
        .isInstanceOf(NotFoundException.class);
    verify(toolCallClient, never()).call(any(), anyString(), any(), any(), any());
  }

  @Test
  void call_foreignSession_throwsNotFound() {
    Artifact artifact = new Artifact();
    artifact.setId("art-1");
    artifact.setSessionId("session-1");
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    when(sessionGuard.loadOwned("session-1")).thenThrow(new NotFoundException("session not found"));

    assertThatThrownBy(() -> service.call("art-1", "sales", "list_orders", ARGS))
        .isInstanceOf(NotFoundException.class);
  }

  @Test
  void call_connectorNotInSession_returnsInvalidCallListingAllowedIds() throws Exception {
    givenOwnedArtifactWithConnectors(List.of("crm", "hr"));

    JsonNode result = callAsJson();

    assertThat(result.path("error").path("code").asText()).isEqualTo("INVALID_CALL");
    assertThat(result.path("error").path("message").asText()).contains("sales").contains("crm, hr");
    verify(connectorCatalogService, never()).resolveSpecs(anyList());
  }

  @Test
  void call_fileModeSession_returnsInvalidCall() throws Exception {
    givenOwnedArtifactWithConnectors(null);

    JsonNode result = callAsJson();

    assertThat(result.path("error").path("code").asText()).isEqualTo("INVALID_CALL");
  }

  @Test
  void call_catalogEntryRemoved_returnsConnectorUnavailableWithCatalogMessage() throws Exception {
    givenOwnedArtifactWithConnectors(List.of("sales"));
    when(connectorCatalogService.resolveSpecs(List.of("sales")))
        .thenThrow(new NotFoundException("資料源 sales 已下架"));

    JsonNode result = callAsJson();

    assertThat(result.path("error").path("code").asText()).isEqualTo("CONNECTOR_UNAVAILABLE");
    assertThat(result.path("error").path("message").asText()).contains("已下架");
  }

  @Test
  void call_clientBeanAbsent_returnsConnectorUnavailable() throws Exception {
    givenOwnedArtifactWithConnectors(List.of("sales"));
    when(connectorCatalogService.resolveSpecs(List.of("sales"))).thenReturn(List.of(SALES));
    when(toolCallClientProvider.getIfAvailable()).thenReturn(null);

    JsonNode result = callAsJson();

    assertThat(result.path("error").path("code").asText()).isEqualTo("CONNECTOR_UNAVAILABLE");
  }

  @Test
  void call_anyOutcome_logsArgKeysAndCodeButNeverValuesOrSso() throws Exception {
    givenOwnedArtifactWithConnectors(List.of("sales"));
    when(connectorCatalogService.resolveSpecs(List.of("sales"))).thenReturn(List.of(SALES));
    when(toolCallClientProvider.getIfAvailable()).thenReturn(toolCallClient);
    when(toolCallClient.call(any(), anyString(), any(), any(), any()))
        .thenReturn(Mono.just("{\"error\":{\"code\":\"TOOL_ERROR\",\"message\":\"bad fab\"}}"));

    service.call("art-1", "sales", "list_orders", ARGS);

    String joinedLogs =
        String.join("\n", logAppender.list.stream().map(ILoggingEvent::getFormattedMessage).toList());
    assertThat(joinedLogs).contains("connector=sales").contains("tool=list_orders");
    assertThat(joinedLogs).contains("days").contains("region");
    assertThat(joinedLogs).contains("code=TOOL_ERROR").contains("ok=false");
    assertThat(joinedLogs).doesNotContain("TW").doesNotContain("sso-secret");
  }
}
```

- [ ] **Step 2: 跑測試確認編譯失敗**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=ArtifactMcpCallServiceTest -q`
Expected: 編譯錯誤, `ArtifactMcpCallService` 不存在.

- [ ] **Step 3: 實作 service**

```java
package com.erd.cowork.service;

import com.erd.cowork.agent.mcp.McpCallErrorWriter;
import com.erd.cowork.agent.mcp.McpErrorCode;
import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.agent.provider.analysis.AnalysisToolCallClient;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.repo.ArtifactRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;
import org.springframework.util.CollectionUtils;

/**
 * View-time mcp() proxy: ownership, session allow-list and catalog lookup happen here; the MCP
 * call itself and its data pass through AnalysisToolCallClient untouched.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@LogAnnotation
public class ArtifactMcpCallService {

  private final ArtifactRepository artifacts;
  private final SessionGuard sessionGuard;
  private final ConnectorCatalogService connectorCatalogService;
  private final ObjectProvider<AnalysisToolCallClient> toolCallClientProvider;
  private final McpCallErrorWriter errorWriter;
  private final ObjectMapper objectMapper;

  /** Returns the JSON body for the page; throws NotFoundException only for ownership failures. */
  public String call(String artifactId, String connectorId, String tool, Map<String, Object> args) {
    long startedAtNanos = System.nanoTime();
    String body = resolveAndCall(artifactId, connectorId, tool, args);
    String errorCode = readErrorCode(body);
    log.info(
        "mcp-call artifact={} connector={} tool={} argKeys={} ms={} ok={} code={}",
        artifactId,
        connectorId,
        tool,
        args.keySet(),
        (System.nanoTime() - startedAtNanos) / 1_000_000,
        errorCode == null,
        errorCode);
    return body;
  }

  private String resolveAndCall(
      String artifactId, String connectorId, String tool, Map<String, Object> args) {
    Artifact artifact =
        artifacts
            .findById(artifactId)
            .orElseThrow(() -> new NotFoundException("Artifact not found: " + artifactId));
    ChatSession session = sessionGuard.loadOwned(artifact.getSessionId());

    List<String> allowedConnectorIds = session.getSelectedConnectors();
    if (CollectionUtils.isEmpty(allowedConnectorIds) || !allowedConnectorIds.contains(connectorId)) {
      String allowed =
          CollectionUtils.isEmpty(allowedConnectorIds)
              ? "(none)"
              : String.join(", ", allowedConnectorIds);
      return errorWriter.write(
          McpErrorCode.INVALID_CALL,
          "connector '" + connectorId + "' is not enabled for this session; allowed: " + allowed);
    }

    ConnectorSpec connector;
    try {
      connector = connectorCatalogService.resolveSpecs(List.of(connectorId)).get(0);
    } catch (NotFoundException catalogMiss) {
      return errorWriter.write(McpErrorCode.CONNECTOR_UNAVAILABLE, catalogMiss.getMessage());
    }

    AnalysisToolCallClient toolCallClient = toolCallClientProvider.getIfAvailable();
    if (toolCallClient == null) {
      return errorWriter.write(
          McpErrorCode.CONNECTOR_UNAVAILABLE,
          "connector mode is not enabled on this server; ask the administrator");
    }

    // SSO values are read on the request thread and handed over as plain arguments.
    String body =
        toolCallClient
            .call(connector, tool, args, CoworkContextHolder.ssoToken(), CoworkContextHolder.ssoUrl())
            .block();
    if (body == null) {
      return errorWriter.write(McpErrorCode.RETRYABLE, "agent service returned no body; retry");
    }
    return body;
  }

  /** Read-only peek for the log line; the body itself is returned untouched. */
  private String readErrorCode(String body) {
    try {
      return objectMapper.readTree(body).path("error").path("code").asText(null);
    } catch (JsonProcessingException unparseable) {
      return "UNPARSEABLE";
    }
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=ArtifactMcpCallServiceTest -q`
Expected: PASS（8 條）.

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/service/ArtifactMcpCallService.java backend/src/test/java/com/erd/cowork/service/ArtifactMcpCallServiceTest.java
git commit -m "feat(backend): ArtifactMcpCallService——ownership、session 允許清單、catalog 解析後代打 /tool-call"
```

---

### Task 4: Controller endpoint `POST /api/artifacts/{id}/mcp-call`

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/web/dto/McpCallRequestDto.java`
- Modify: `backend/src/main/java/com/erd/cowork/web/ArtifactController.java`
- Modify: `backend/src/test/java/com/erd/cowork/web/ArtifactControllerTest.java:42-51`（加 `@MockitoBean ArtifactMcpCallService`）
- Modify: `backend/src/test/java/com/erd/cowork/web/ArtifactRepairControllerTest.java:28-37`（同上）
- Test: `backend/src/test/java/com/erd/cowork/web/ArtifactMcpCallControllerTest.java`

**Interfaces:**
- Consumes: `ArtifactMcpCallService.call(String, String, String, Map<String, Object>): String`.
- Produces: `POST /api/artifacts/{id}/mcp-call`, body `McpCallRequestDto(connector, tool, args)`, 200 `application/json` 字串; 404 `NOT_FOUND`; 400 驗證失敗.

- [ ] **Step 1: 寫 controller 測試**

```java
package com.erd.cowork.web;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.service.ArtifactMcpCallService;
import com.erd.cowork.service.ArtifactRepairService;
import com.erd.cowork.service.ArtifactService;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(ArtifactController.class)
@Import(CurrentUserFilter.class)
@EnableConfigurationProperties(AnalysisAgentProperties.class)
class ArtifactMcpCallControllerTest {

  private static final String VALID_REQUEST =
      "{\"connector\":\"sales\",\"tool\":\"list_orders\",\"args\":{\"days\":30}}";

  @Autowired MockMvc mockMvc;

  @MockitoBean ArtifactService artifactService;
  @MockitoBean ArtifactRepairService artifactRepairService;
  @MockitoBean ArtifactMcpCallService artifactMcpCallService;

  @Test
  void mcpCall_serviceReturnsBody_returns200JsonUnchanged() throws Exception {
    String body = "{\"data\":{\"result\":[{\"qty\":1.10}]}}";
    when(artifactMcpCallService.call(eq("art-1"), eq("sales"), eq("list_orders"), eq(Map.of("days", 30))))
        .thenReturn(body);

    mockMvc
        .perform(
            post("/api/artifacts/art-1/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isOk())
        .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
        .andExpect(content().string(body));
  }

  @Test
  void mcpCall_artifactNotFound_returns404() throws Exception {
    when(artifactMcpCallService.call(anyString(), anyString(), anyString(), any()))
        .thenThrow(new NotFoundException("Artifact not found: missing"));

    mockMvc
        .perform(
            post("/api/artifacts/missing/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }

  @Test
  void mcpCall_blankTool_returns400() throws Exception {
    mockMvc
        .perform(
            post("/api/artifacts/art-1/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"connector\":\"sales\",\"tool\":\"\",\"args\":{}}"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void mcpCall_argsIsArray_returns400() throws Exception {
    mockMvc
        .perform(
            post("/api/artifacts/art-1/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"connector\":\"sales\",\"tool\":\"list_orders\",\"args\":[1,2]}"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void mcpCall_missingArgs_returns400() throws Exception {
    mockMvc
        .perform(
            post("/api/artifacts/art-1/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"connector\":\"sales\",\"tool\":\"list_orders\"}"))
        .andExpect(status().isBadRequest());
  }
}
```

- [ ] **Step 2: 兩個既有 controller test 加 mock bean**

`ArtifactControllerTest` 的 `@MockitoBean com.erd.cowork.service.ArtifactRepairService artifactRepairService;` 之後加:

```java
  @MockitoBean com.erd.cowork.service.ArtifactMcpCallService artifactMcpCallService;
```

`ArtifactRepairControllerTest` 的 `@MockitoBean ArtifactRepairService artifactRepairService;` 之後加（並補 import `com.erd.cowork.service.ArtifactMcpCallService`）:

```java
  @MockitoBean ArtifactMcpCallService artifactMcpCallService;
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=ArtifactMcpCallControllerTest -q`
Expected: 200 案例 404（route 不存在）, 其餘也非預期.

- [ ] **Step 4: 建 DTO**

```java
package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.util.Map;

@Schema(description = "One view-time mcp() call issued by a connector-mode dashboard")
public record McpCallRequestDto(
    @NotBlank
        @Schema(description = "Connector id as written in the dashboard's mcp() call", example = "sales")
        String connector,
    @NotBlank
        @Schema(description = "MCP tool name on that connector", example = "list_orders")
        String tool,
    @NotNull
        @Schema(
            description = "Tool arguments, forwarded to the MCP server unchanged",
            example = "{\"days\": 30}")
        Map<String, Object> args) {}
```

- [ ] **Step 5: Controller 加 endpoint**

在 `ArtifactController` 加 import `com.erd.cowork.service.ArtifactMcpCallService`、`com.erd.cowork.web.dto.McpCallRequestDto`, 欄位加 `private final ArtifactMcpCallService artifactMcpCallService;`, class 末尾 `repair` 之後加:

```java
  @PostMapping(
      value = "/{id}/mcp-call",
      consumes = MediaType.APPLICATION_JSON_VALUE,
      produces = MediaType.APPLICATION_JSON_VALUE)
  @Operation(
      summary = "Proxy one view-time mcp() call for a connector-mode dashboard",
      description =
          "Checks artifact ownership and that the connector belongs to the artifact's session,"
              + " then forwards the tool call to the agent service. Tool-level failures are"
              + " always 200 with an error envelope so the page can degrade per card.")
  @ApiResponse(
      responseCode = "200",
      description = "{data: <raw structuredContent>} or {error: {code, message}}")
  @ApiResponse(responseCode = "400", description = "connector/tool blank or args not an object")
  @ApiResponse(responseCode = "404", description = "Artifact not found or does not belong to user")
  public ResponseEntity<String> mcpCall(
      @PathVariable String id, @Valid @RequestBody McpCallRequestDto request) {
    log.info(
        "POST mcp-call artifact={} connector={} tool={} argKeys={}",
        id,
        request.connector(),
        request.tool(),
        request.args().keySet());
    String body =
        artifactMcpCallService.call(id, request.connector(), request.tool(), request.args());
    return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body(body);
  }
```

- [ ] **Step 6: 跑三個 controller test 與全套**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -Dtest=ArtifactMcpCallControllerTest,ArtifactControllerTest,ArtifactRepairControllerTest -q`
Expected: PASS.

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -q; echo "EXIT=$?"`
Expected: `EXIT=0`, 全綠.

- [ ] **Step 7: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/web/dto/McpCallRequestDto.java backend/src/main/java/com/erd/cowork/web/ArtifactController.java backend/src/test/java/com/erd/cowork/web/ArtifactMcpCallControllerTest.java backend/src/test/java/com/erd/cowork/web/ArtifactControllerTest.java backend/src/test/java/com/erd/cowork/web/ArtifactRepairControllerTest.java
git commit -m "feat(backend): POST /api/artifacts/{id}/mcp-call——connector dashboard 檢視期代打端點"
```

---

### Task 5: 前端型別、設定與折疊純函式

**Files:**
- Modify: `frontend/src/types.ts`（檔尾）
- Create: `frontend/src/config/mcpBridge.ts`
- Create: `frontend/src/utils/mcpResult.ts`
- Test: `frontend/src/utils/mcpResult.test.ts`

**Interfaces:**
- Produces: `type McpErrorCode`, `interface McpResult`（`{ data: unknown } | { error: { code: McpErrorCode; message: string } }`）, `interface McpCallMessage { type: 'erd-mcp-call'; id: string; connector: string; tool: string; args: Record<string, unknown> }`, `MCP_BRIDGE_TIMEOUT_MS = 60_000`, `MCP_ERROR_CODES: readonly McpErrorCode[]`, `foldMcpFailure(error: unknown): McpResult`.

- [ ] **Step 1: 寫測試**

```ts
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { AxiosError, type AxiosResponse } from 'axios';
import { describe, expect, test } from 'vitest';
import { MCP_ERROR_CODES } from '@/config/mcpBridge';
import { foldMcpFailure } from './mcpResult';

function axiosErrorWithStatus(status: number): AxiosError {
  return new AxiosError('request failed', 'ERR_BAD_RESPONSE', undefined, undefined, {
    status,
  } as AxiosResponse);
}

describe('foldMcpFailure', () => {
  test.each([401, 403, 404])('HTTP %i folds to AUTH with status in message', (status) => {
    const result = foldMcpFailure(axiosErrorWithStatus(status));
    expect(result).toEqual({
      error: { code: 'AUTH', message: expect.stringContaining(`HTTP ${status}`) },
    });
  });

  test.each([400, 422])('HTTP %i folds to INVALID_CALL', (status) => {
    const result = foldMcpFailure(axiosErrorWithStatus(status));
    expect(result).toEqual({
      error: { code: 'INVALID_CALL', message: expect.stringContaining(`HTTP ${status}`) },
    });
  });

  test.each([500, 502, 503])('HTTP %i folds to RETRYABLE', (status) => {
    const result = foldMcpFailure(axiosErrorWithStatus(status));
    expect(result).toEqual({
      error: { code: 'RETRYABLE', message: expect.stringContaining(`HTTP ${status}`) },
    });
  });

  test('network failure without a response folds to RETRYABLE', () => {
    const result = foldMcpFailure(new AxiosError('Network Error', 'ERR_NETWORK'));
    expect(result).toEqual({
      error: { code: 'RETRYABLE', message: expect.stringContaining('network') },
    });
  });

  test('non-axios error folds to RETRYABLE', () => {
    const result = foldMcpFailure(new TypeError('boom'));
    expect(result).toEqual({ error: { code: 'RETRYABLE', message: expect.any(String) } });
  });

  test('folded results never carry a data field', () => {
    const result = foldMcpFailure(axiosErrorWithStatus(500));
    expect('data' in result).toBe(false);
  });
});

describe('contract fixture', () => {
  const fixturePath = fileURLToPath(
    new URL('../../../deepagent-service/tests/fixtures/mcp_result_examples.json', import.meta.url),
  );
  const examples = JSON.parse(readFileSync(fixturePath, 'utf8')) as Record<
    string,
    { error?: { code: string } }
  >;

  test('every error example uses one of the five known codes', () => {
    const codesInFixture = Object.values(examples)
      .filter((example) => example.error)
      .map((example) => example.error?.code);
    expect(codesInFixture).toHaveLength(5);
    for (const code of codesInFixture) {
      expect(MCP_ERROR_CODES).toContain(code);
    }
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npm test -- src/utils/mcpResult.test.ts`
Expected: FAIL, 模組不存在.

- [ ] **Step 3: 加型別**

`types.ts` 檔尾加:

```ts
/** The five mcp() error codes; the page only branches on RETRYABLE (retry) and AUTH (banner). */
export type McpErrorCode = 'AUTH' | 'RETRYABLE' | 'TOOL_ERROR' | 'INVALID_CALL' | 'CONNECTOR_UNAVAILABLE';

export interface McpError {
  code: McpErrorCode;
  message: string;
}

/** Exactly one of the two shapes; the bridge never reads or reshapes data. */
export type McpResult = { data: unknown } | { error: McpError };

/** Posted by the erd-mcp-runtime prelude inside a connector-mode dashboard iframe. */
export interface McpCallMessage {
  type: 'erd-mcp-call';
  id: string;
  connector: string;
  tool: string;
  args: Record<string, unknown>;
}
```

- [ ] **Step 4: 加設定**

`frontend/src/config/mcpBridge.ts`:

```ts
import type { McpErrorCode } from '@/types';

/** Per-call host timeout, counted from the moment the bridge sends the request; includes
 *  browser connection queueing, accepted. Not part of the contract. */
export const MCP_BRIDGE_TIMEOUT_MS = 60_000;

export const MCP_ERROR_CODES: readonly McpErrorCode[] = [
  'AUTH',
  'RETRYABLE',
  'TOOL_ERROR',
  'INVALID_CALL',
  'CONNECTOR_UNAVAILABLE',
];
```

- [ ] **Step 5: 加折疊函式**

`frontend/src/utils/mcpResult.ts`:

```ts
import axios from 'axios';
import type { McpResult } from '@/types';

/** Folds a failed /mcp-call request into the error envelope the page expects. Only a safety net:
 *  Java already folds agent-service failures, so this sees Java's own 404/400/5xx and network. */
export function foldMcpFailure(error: unknown): McpResult {
  if (!axios.isAxiosError(error)) {
    return { error: { code: 'RETRYABLE', message: 'unexpected host failure; retry' } };
  }
  const status = error.response?.status;
  if (status === undefined) {
    return { error: { code: 'RETRYABLE', message: 'network error reaching the host; retry' } };
  }
  if (status === 401 || status === 403 || status === 404) {
    return {
      error: { code: 'AUTH', message: `host returned HTTP ${status}; sign in again or reload` },
    };
  }
  if (status === 400 || status === 422) {
    return {
      error: { code: 'INVALID_CALL', message: `host rejected the call (HTTP ${status}); fix the dashboard` },
    };
  }
  return { error: { code: 'RETRYABLE', message: `host returned HTTP ${status}; retry` } };
}
```

- [ ] **Step 6: 跑測試確認通過, 並跑 typecheck**

Run: `cd frontend && npm test -- src/utils/mcpResult.test.ts`
Expected: PASS（12 條）.

Run: `cd frontend && npx tsc -b`
Expected: 無錯誤. 若 `import.meta.url` 或 `node:fs` 在測試檔型別報錯, 在 `tsconfig` 的測試 include 已涵蓋 node types 的前提下應無; 否則改用 `process.cwd()` 組路徑.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/config/mcpBridge.ts frontend/src/utils/mcpResult.ts frontend/src/utils/mcpResult.test.ts
git commit -m "feat(frontend): mcp() 結果型別、bridge 逾時設定與 HTTP 失敗折疊純函式"
```

---

### Task 6: `callArtifactMcp` API 函式

**Files:**
- Modify: `frontend/src/api/artifactApi.ts`（檔尾）
- Test: `frontend/src/api/artifactApi.test.ts`（檔尾加一個 describe）

**Interfaces:**
- Consumes: `McpResult` 型別.
- Produces: `callArtifactMcp(artifactId: string, call: { connector: string; tool: string; args: Record<string, unknown> }): Promise<McpResult>`.

- [ ] **Step 1: 寫測試**

在 `frontend/src/api/artifactApi.test.ts` 檔尾加（沿用該檔既有的 `vi.spyOn(apiClient, ...)` 模式; 若該檔頂部尚未 import `apiClient` 與 `callArtifactMcp`, 補上）:

```ts
describe('callArtifactMcp', () => {
  test('posts connector, tool and args unchanged and returns the body as-is', async () => {
    const body = { data: { result: [{ qty: 1.1 }] } };
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: body });

    const result = await callArtifactMcp('art-1', {
      connector: 'sales',
      tool: 'list_orders',
      args: { days: 30, region: 'TW' },
    });

    expect(postSpy).toHaveBeenCalledWith('/artifacts/art-1/mcp-call', {
      connector: 'sales',
      tool: 'list_orders',
      args: { days: 30, region: 'TW' },
    });
    expect(result).toBe(body);
    postSpy.mockRestore();
  });

  test('encodes the artifact id in the path', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { data: [] } });

    await callArtifactMcp('a b', { connector: 'sales', tool: 'list_orders', args: {} });

    expect(postSpy).toHaveBeenCalledWith('/artifacts/a%20b/mcp-call', expect.anything());
    postSpy.mockRestore();
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npm test -- src/api/artifactApi.test.ts`
Expected: FAIL, `callArtifactMcp` 不是函式.

- [ ] **Step 3: 實作**

`artifactApi.ts` 的 import 改成 `import type { BrowserJsError, McpResult } from '@/types';`, 檔尾加:

```ts
export interface McpCallPayload {
  connector: string;
  tool: string;
  args: Record<string, unknown>;
}

/** Forwards one mcp() call to the Java proxy; the body comes back untouched (data or error). */
export async function callArtifactMcp(artifactId: string, call: McpCallPayload): Promise<McpResult> {
  const response = await apiClient.post<McpResult>(
    `/artifacts/${encodeURIComponent(artifactId)}/mcp-call`,
    call,
  );
  return response.data;
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npm test -- src/api/artifactApi.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/artifactApi.ts frontend/src/api/artifactApi.test.ts
git commit -m "feat(frontend): callArtifactMcp——打 /artifacts/{id}/mcp-call, 回應原樣回傳"
```

---

### Task 7: `useMcpBridge` hook 與 `ArtifactPanel` 接線

**Files:**
- Create: `frontend/src/hooks/useMcpBridge.ts`
- Modify: `frontend/src/components/artifact/ArtifactPanel.tsx:54`（`iframeRef` 宣告之後呼叫 hook）
- Modify: `frontend/src/components/artifact/ArtifactPanel.test.tsx:11-15`（mock 加 `callArtifactMcp`）
- Test: `frontend/src/components/artifact/ArtifactPanel.mcpBridge.test.tsx`

**Interfaces:**
- Consumes: `callArtifactMcp`, `foldMcpFailure`, `MCP_BRIDGE_TIMEOUT_MS`, `McpCallMessage`, `McpResult`.
- Produces: `useMcpBridge(iframeRef: RefObject<HTMLIFrameElement>, artifactId: string | undefined): void`.

- [ ] **Step 1: 既有 ArtifactPanel.test 的 mock 加一行**

`ArtifactPanel.test.tsx` 第 11–15 行的 `vi.mock` 改成:

```ts
vi.mock('@/api/artifactApi', () => ({
  fetchArtifactRawHtml: vi.fn(),
  fetchArtifactHtml: vi.fn().mockResolvedValue('<head></head><body>DASH</body>'),
  repairArtifact: vi.fn(),
  callArtifactMcp: vi.fn(),
}));
```

- [ ] **Step 2: 寫 bridge 測試**

`frontend/src/components/artifact/ArtifactPanel.mcpBridge.test.tsx`:

```tsx
import { render, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Suspense } from 'react';
import { AxiosError, type AxiosResponse } from 'axios';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import ArtifactPanel from './ArtifactPanel';
import type { Props as ArtifactPanelProps } from './ArtifactPanel';
import * as artifactApiModule from '@/api/artifactApi';
import { MCP_BRIDGE_TIMEOUT_MS } from '@/config/mcpBridge';
import type { McpResult } from '@/types';

vi.mock('@/api/artifactApi', () => ({
  fetchArtifactRawHtml: vi.fn(),
  fetchArtifactHtml: vi.fn().mockResolvedValue('<head></head><body>DASH</body>'),
  repairArtifact: vi.fn(),
  callArtifactMcp: vi.fn(),
}));

const ARTIFACT = { artifactId: 'art-42', title: 'Dashboard' };

function renderPanel(props: ArtifactPanelProps): ReturnType<typeof render> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Suspense fallback={<div>loading</div>}>
        <ArtifactPanel {...props} />
      </Suspense>
    </QueryClientProvider>,
  );
}

async function findIframe(container: HTMLElement): Promise<HTMLIFrameElement> {
  return waitFor(() => {
    const iframe = container.querySelector('iframe');
    if (!iframe) throw new Error('iframe not yet mounted');
    return iframe as HTMLIFrameElement;
  });
}

function mcpCall(iframe: HTMLIFrameElement, callId = '1'): MessageEvent {
  return new MessageEvent('message', {
    data: { type: 'erd-mcp-call', id: callId, connector: 'sales', tool: 'list_orders', args: { days: 30 } },
    source: iframe.contentWindow,
  });
}

async function dispatch(event: MessageEvent): Promise<void> {
  await act(async () => {
    window.dispatchEvent(event);
  });
}

describe('useMcpBridge via ArtifactPanel', () => {
  beforeEach(() => {
    vi.mocked(artifactApiModule.callArtifactMcp).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test('erd-mcp-call from the iframe is forwarded and the result is posted back once', async () => {
    const body: McpResult = { data: { result: [{ qty: 1.1 }] } };
    vi.mocked(artifactApiModule.callArtifactMcp).mockResolvedValue(body);
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);
    const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');

    await dispatch(mcpCall(iframe));

    expect(artifactApiModule.callArtifactMcp).toHaveBeenCalledWith('art-42', {
      connector: 'sales',
      tool: 'list_orders',
      args: { days: 30 },
    });
    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy).toHaveBeenCalledWith({ type: 'erd-mcp-result', id: '1', result: body }, '*');
  });

  test('HTTP failure is folded and posted back as an error envelope', async () => {
    vi.mocked(artifactApiModule.callArtifactMcp).mockRejectedValue(
      new AxiosError('nope', 'ERR_BAD_RESPONSE', undefined, undefined, { status: 404 } as AxiosResponse),
    );
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);
    const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');

    await dispatch(mcpCall(iframe));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    const [message] = postSpy.mock.calls[0];
    expect(message).toMatchObject({ type: 'erd-mcp-result', id: '1', result: { error: { code: 'AUTH' } } });
  });

  test('message with wrong type is ignored', async () => {
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);

    await dispatch(
      new MessageEvent('message', { data: { type: 'erd-artifact-error', errors: [] }, source: iframe.contentWindow }),
    );

    expect(artifactApiModule.callArtifactMcp).not.toHaveBeenCalled();
  });

  test('message from a foreign source is ignored', async () => {
    const { container } = renderPanel({ artifact: ARTIFACT });
    await findIframe(container);

    await dispatch(
      new MessageEvent('message', {
        data: { type: 'erd-mcp-call', id: '1', connector: 'sales', tool: 'list_orders', args: {} },
      }),
    );

    expect(artifactApiModule.callArtifactMcp).not.toHaveBeenCalled();
  });

  test('timeout posts RETRYABLE and a late result is dropped', async () => {
    let resolveLate: (value: McpResult) => void = () => undefined;
    vi.mocked(artifactApiModule.callArtifactMcp).mockImplementation(
      () => new Promise<McpResult>((resolve) => { resolveLate = resolve; }),
    );
    const { container } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);
    const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');
    vi.useFakeTimers();

    await dispatch(mcpCall(iframe));
    await act(async () => {
      vi.advanceTimersByTime(MCP_BRIDGE_TIMEOUT_MS);
    });

    expect(postSpy).toHaveBeenCalledTimes(1);
    expect(postSpy.mock.calls[0][0]).toMatchObject({
      type: 'erd-mcp-result',
      id: '1',
      result: { error: { code: 'RETRYABLE', message: expect.stringContaining('timeout') } },
    });

    await act(async () => {
      resolveLate({ data: [] });
      await Promise.resolve();
    });
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  test('result for a call issued to a previous iframe instance is dropped after remount', async () => {
    let resolveLate: (value: McpResult) => void = () => undefined;
    vi.mocked(artifactApiModule.callArtifactMcp).mockImplementation(
      () => new Promise<McpResult>((resolve) => { resolveLate = resolve; }),
    );
    const { container, rerender } = renderPanel({ artifact: ARTIFACT, reloadNonce: 0 });
    const firstIframe = await findIframe(container);
    const firstPostSpy = vi.spyOn(firstIframe.contentWindow as Window, 'postMessage');

    await dispatch(mcpCall(firstIframe));

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={queryClient}>
        <Suspense fallback={<div>loading</div>}>
          <ArtifactPanel artifact={ARTIFACT} reloadNonce={1} />
        </Suspense>
      </QueryClientProvider>,
    );
    const secondIframe = await waitFor(() => {
      const iframe = container.querySelector('iframe');
      if (!iframe || iframe === firstIframe) throw new Error('remounted iframe not yet present');
      return iframe as HTMLIFrameElement;
    });
    const secondPostSpy = vi.spyOn(secondIframe.contentWindow as Window, 'postMessage');

    await act(async () => {
      resolveLate({ data: [] });
      await Promise.resolve();
    });

    expect(firstPostSpy).not.toHaveBeenCalled();
    expect(secondPostSpy).not.toHaveBeenCalled();
  });

  test('unmount removes the listener so later calls are not forwarded', async () => {
    const { container, unmount } = renderPanel({ artifact: ARTIFACT });
    const iframe = await findIframe(container);
    const event = mcpCall(iframe);
    unmount();

    await dispatch(event);

    expect(artifactApiModule.callArtifactMcp).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `cd frontend && npm test -- src/components/artifact/ArtifactPanel.mcpBridge.test.tsx`
Expected: FAIL, `callArtifactMcp` 未被呼叫（bridge 不存在）.

- [ ] **Step 4: 實作 hook**

`frontend/src/hooks/useMcpBridge.ts`:

```ts
import { useEffect, type RefObject } from 'react';
import { callArtifactMcp } from '@/api/artifactApi';
import { MCP_BRIDGE_TIMEOUT_MS } from '@/config/mcpBridge';
import { foldMcpFailure } from '@/utils/mcpResult';
import type { McpCallMessage, McpResult } from '@/types';

interface PendingCall {
  /** The iframe window that issued the call; a remounted iframe gets a new one and old
   *  results must not reach it (call ids restart at 1 per document). */
  sourceWindow: Window;
  timer: ReturnType<typeof setTimeout>;
}

function isMcpCallMessage(data: unknown): data is McpCallMessage {
  if (typeof data !== 'object' || data === null) return false;
  const candidate = data as Record<string, unknown>;
  return (
    candidate.type === 'erd-mcp-call' &&
    typeof candidate.id === 'string' &&
    typeof candidate.connector === 'string' &&
    typeof candidate.tool === 'string' &&
    typeof candidate.args === 'object' &&
    candidate.args !== null
  );
}

/** Host half of mcp(): answers erd-mcp-call from the sandboxed dashboard iframe by calling the
 *  Java proxy and posting erd-mcp-result back. Never reads data; one result per call id. */
export function useMcpBridge(
  iframeRef: RefObject<HTMLIFrameElement>,
  artifactId: string | undefined,
): void {
  useEffect(() => {
    const pendingById = new Map<string, PendingCall>();

    const postResult = (callId: string, result: McpResult): void => {
      const pending = pendingById.get(callId);
      if (!pending) return;
      pendingById.delete(callId);
      clearTimeout(pending.timer);
      const currentWindow = iframeRef.current?.contentWindow;
      if (!currentWindow || currentWindow !== pending.sourceWindow) return;
      currentWindow.postMessage({ type: 'erd-mcp-result', id: callId, result }, '*');
    };

    const handleMessage = (event: MessageEvent): void => {
      if (!artifactId) return;
      const sourceWindow = iframeRef.current?.contentWindow;
      if (!sourceWindow || event.source !== sourceWindow) return;
      if (!isMcpCallMessage(event.data)) return;

      const { id: callId, connector, tool, args } = event.data;
      const previous = pendingById.get(callId);
      if (previous) clearTimeout(previous.timer);
      const timer = setTimeout(() => {
        postResult(callId, {
          error: {
            code: 'RETRYABLE',
            message: `host timeout after ${MCP_BRIDGE_TIMEOUT_MS / 1000} s`,
          },
        });
      }, MCP_BRIDGE_TIMEOUT_MS);
      pendingById.set(callId, { sourceWindow, timer });

      callArtifactMcp(artifactId, { connector, tool, args })
        .then((result) => postResult(callId, result))
        .catch((error: unknown) => postResult(callId, foldMcpFailure(error)));
    };

    window.addEventListener('message', handleMessage);
    return (): void => {
      window.removeEventListener('message', handleMessage);
      for (const pending of pendingById.values()) clearTimeout(pending.timer);
      pendingById.clear();
    };
  }, [iframeRef, artifactId]);
}
```

- [ ] **Step 5: ArtifactPanel 接線**

`ArtifactPanel.tsx` import 加 `import { useMcpBridge } from '@/hooks/useMcpBridge';`; 第 54 行 `const iframeRef = useRef<HTMLIFrameElement>(null);` 之後加:

```ts
  useMcpBridge(iframeRef, artifact?.artifactId);
```

- [ ] **Step 6: 跑新舊 ArtifactPanel 測試**

Run: `cd frontend && npm test -- src/components/artifact/ArtifactPanel`
Expected: PASS, 含既有 `ArtifactPanel.test.tsx`.

若 `timeout posts RETRYABLE` 在 fake timers 下 `waitFor` 卡住: 該案例已避開 `waitFor`, 只用 `act` + `advanceTimersByTime`; 若仍失敗, 把 `vi.useFakeTimers()` 改為 `vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/hooks/useMcpBridge.ts frontend/src/components/artifact/ArtifactPanel.tsx frontend/src/components/artifact/ArtifactPanel.test.tsx frontend/src/components/artifact/ArtifactPanel.mcpBridge.test.tsx
git commit -m "feat(frontend): useMcpBridge——回應 iframe erd-mcp-call, 60 秒逾時, 重掛後舊結果丟棄; ArtifactPanel 接線"
```

---

### Task 8: 全螢幕頁接 bridge

**Files:**
- Modify: `frontend/src/components/artifact/ArtifactFullscreenPage.tsx`
- Modify: `frontend/src/components/artifact/ArtifactFullscreenPage.test.tsx`

**Interfaces:**
- Consumes: `useMcpBridge`, `ArtifactFrame` 的 `iframeRef` prop.

- [ ] **Step 1: 改測試**

`ArtifactFullscreenPage.test.tsx` 整檔改成:

```tsx
import { render, screen, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, test, vi } from 'vitest';
import ArtifactFullscreenPage from './ArtifactFullscreenPage';
import * as artifactApiModule from '@/api/artifactApi';

vi.mock('@/api/artifactApi', () => ({
  fetchArtifactHtml: vi.fn().mockResolvedValue('<head></head><body>FULL</body>'),
  callArtifactMcp: vi.fn(),
}));

function renderPage(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ArtifactFullscreenPage artifactId="artifact-9" />
    </QueryClientProvider>,
  );
}

test('renders full-viewport sandboxed frame for the artifact', async () => {
  renderPage();
  const iframe = (await screen.findByTitle('Dashboard')) as HTMLIFrameElement;
  expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
  expect(iframe.getAttribute('srcdoc')).toContain('FULL');
});

test('answers erd-mcp-call from its own iframe', async () => {
  vi.mocked(artifactApiModule.callArtifactMcp).mockResolvedValue({ data: [] });
  renderPage();
  const iframe = (await screen.findByTitle('Dashboard')) as HTMLIFrameElement;
  const postSpy = vi.spyOn(iframe.contentWindow as Window, 'postMessage');

  await act(async () => {
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'erd-mcp-call', id: '1', connector: 'sales', tool: 'list_orders', args: {} },
        source: iframe.contentWindow,
      }),
    );
  });

  expect(artifactApiModule.callArtifactMcp).toHaveBeenCalledWith('artifact-9', {
    connector: 'sales',
    tool: 'list_orders',
    args: {},
  });
  await waitFor(() =>
    expect(postSpy).toHaveBeenCalledWith(
      { type: 'erd-mcp-result', id: '1', result: { data: [] } },
      '*',
    ),
  );
});
```

- [ ] **Step 2: 跑測試確認第二條失敗**

Run: `cd frontend && npm test -- src/components/artifact/ArtifactFullscreenPage.test.tsx`
Expected: 第二條 FAIL（`callArtifactMcp` 未被呼叫）.

- [ ] **Step 3: 改元件**

`ArtifactFullscreenPage.tsx` 整檔改成:

```tsx
import React, { useRef } from 'react';
import ErrorBoundary from '@/components/common/ErrorBoundary';
import SuspenseLoader from '@/components/common/SuspenseLoader';
import { useMcpBridge } from '@/hooks/useMcpBridge';
import ArtifactFrame from './ArtifactFrame';

interface ArtifactFullscreenPageProps {
  artifactId: string;
}

/** 全螢幕殼頁：app 自身的頁面（可帶 auth header），內部仍以 sandbox srcdoc 關住 artifact。
 *  取代直接 window.open /api HTML——導覽請求帶不了 auth header，blob 又會同源逃逸。
 *  只接 mcp() 呼叫, 不接修復卡. */
const ArtifactFullscreenPage: React.FC<ArtifactFullscreenPageProps> = ({ artifactId }) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  useMcpBridge(iframeRef, artifactId);

  return (
    <div className="relative h-screen w-screen">
      <ErrorBoundary>
        <SuspenseLoader>
          <ArtifactFrame
            artifactId={artifactId}
            reloadNonce={0}
            title="Dashboard"
            iframeRef={iframeRef}
          />
        </SuspenseLoader>
      </ErrorBoundary>
    </div>
  );
};

export default ArtifactFullscreenPage;
```

- [ ] **Step 4: 跑前端全套、lint 與 typecheck**

Run: `cd frontend && npm test; echo "EXIT=$?"`
Expected: `EXIT=0`.

Run: `cd frontend && npm run lint && npx tsc -b; echo "EXIT=$?"`
Expected: `EXIT=0`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/artifact/ArtifactFullscreenPage.tsx frontend/src/components/artifact/ArtifactFullscreenPage.test.tsx
git commit -m "feat(frontend): 全螢幕頁接 useMcpBridge, connector dashboard 全螢幕也能現抓"
```

---

### Task 9: 文件同步

**Files:**
- Modify: `docs/superpowers/specs/2026-09-09-mcp-dashboard-decision-summary.md`（§3 D9 傳輸面列、§5 第 2 點）
- Modify: `CLAUDE.md`（「狀態（2026-09-10，本 branch）」那一條）
- Modify: `deepagent-service/README.md`（`/tool-call` 段末加一句誰在呼叫它）

- [ ] **Step 1: 決策總結 §3 D9 傳輸面列**

該列「hop ②（前端 bridge）與 hop ③（Java `/mcp-call`）**仍是零程式碼**.」改成:

```
hop ②（前端 `useMcpBridge`, 掛在 `ArtifactPanel` 與 `ArtifactFullscreenPage`）與 hop ③（Java `POST /api/artifacts/{id}/mcp-call`, `ArtifactMcpCallService` + `AnalysisToolCallClient`）**已落地於 `feat/mcp-dashboard-host`**（2026-09-15, spec `2026-09-15-mcp-host-bridge-design.md`, 決策 H1–H8: Java 折疊 deepagent 非 2xx, 前端不設 in-flight 上限, 60 秒逾時含瀏覽器排隊）.
```

同列「誰決定何時做」欄改成 `四個 hop 齊; PR 暫不開（H8）`.

§5 第 2 點「**只有 spike 一個宿主, 現在少一塊.**」整點改成:

```
2. **產品宿主已齊（2026-09-15）.** 前端 `useMcpBridge` 與 Java `/mcp-call` 落地於 `feat/mcp-dashboard-host`; spike 的 `shell.html`／`bridge.py` 不再是唯一宿主, 只剩 `mock_server.py` 在本機驗收有用, 宿主半邊不再維護.
```

- [ ] **Step 2: CLAUDE.md 狀態條目**

「狀態（2026-09-10，本 branch `feat/mcp-dashboard`，PR #87 開進 `feat/9E`）」那一條的「前端 bridge（hop ②）與 Java `/mcp-call`（hop ③）未落地」改成「前端 bridge（hop ②）與 Java `/mcp-call`（hop ③）已於 `feat/mcp-dashboard-host`（2026-09-15，自本 branch 分出，PR 暫不開）落地，spec `2026-09-15-mcp-host-bridge-design.md`」.

- [ ] **Step 3: deepagent README**

`/tool-call` 段末加一行:

```
Called by Java's `POST /api/artifacts/{id}/mcp-call` (ArtifactMcpCallService), never by the browser directly.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-09-mcp-dashboard-decision-summary.md CLAUDE.md deepagent-service/README.md
git commit -m "docs: hop ②③ 落地狀態同步——決策總結、CLAUDE.md、deepagent README"
```

---

### Task 10: 全分支驗證

- [ ] **Step 1: 後端全套**

Run: `cd backend && JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home ./mvnw test -q; echo "EXIT=$?"`
Expected: `EXIT=0`.

- [ ] **Step 2: 前端全套、lint、typecheck**

Run: `cd frontend && npm test; echo "EXIT=$?"` → `EXIT=0`
Run: `cd frontend && npm run lint && npx tsc -b; echo "EXIT=$?"` → `EXIT=0`

- [ ] **Step 3: deepagent 未動確認**

Run: `git diff --stat feat/mcp-dashboard...HEAD -- deepagent-service/app deepagent-service/tests`
Expected: 空（只有 README 一檔在 Task 9 動過）.

- [ ] **Step 4: opus 全分支終審**

以 opus dispatch code-reviewer 審 `feat/mcp-dashboard..HEAD`, 重點: 五條不變量（尤其 `data` 原樣字串、SSO 不進 body/log/message）、hook 的 pending 清理與重掛防護、controller 400/404 路徑. 結論記進 `.superpowers/sdd/progress.md`; PR 不開.
