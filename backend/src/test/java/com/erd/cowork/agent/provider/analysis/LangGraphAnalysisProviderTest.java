package com.erd.cowork.agent.provider.analysis;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.AnswerEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.event.TableEvent;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.model.AgentFileContext;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.model.ClarifyingQuestion;
import com.erd.cowork.agent.model.HistoryMessage;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.config.StorageProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

class LangGraphAnalysisProviderTest {

  private final ObjectMapper objectMapper = new ObjectMapper();

  // ── event translation (static toEvent) ────────────────────────────────────

  @Test
  void toEvent_tokenPayload_returnsTokenEvent() {
    var event =
        LangGraphAnalysisProvider.toEvent("{\"type\":\"TOKEN\",\"delta\":\"hi\"}", objectMapper);
    assertThat(event).isEqualTo(new TokenEvent("hi"));
  }

  @Test
  void toEvent_stepPayload_missingStatus_normalizesToRunning() {
    var event =
        LangGraphAnalysisProvider.toEvent(
            "{\"type\":\"STEP\",\"stepKey\":\"analysis\",\"title\":\"分析資料中\"}", objectMapper);
    assertThat(event).isInstanceOf(StepEvent.class);
    StepEvent stepEvent = (StepEvent) event;
    // stepKey convention deleted (spec §16.4-1): the Python-supplied key passes through unchanged.
    assertThat(stepEvent.stepKey()).isEqualTo("analysis");
    assertThat(stepEvent.title()).isEqualTo("分析資料中");
    // status is absent from the Python payload (StepEvent has 4 components; Python only
    // sends stepKey/title) — the boundary must not leak a null status downstream.
    assertThat(stepEvent.status()).isEqualTo(StepStatus.RUNNING);
  }

  @Test
  void toEvent_stepPayload_explicitStatus_preservesGivenStatus() {
    var event =
        LangGraphAnalysisProvider.toEvent(
            "{\"type\":\"STEP\",\"stepKey\":\"analysis\",\"title\":\"t\",\"status\":\"SUCCESS\"}",
            objectMapper);
    assertThat(event).isInstanceOf(StepEvent.class);
    assertThat(((StepEvent) event).status()).isEqualTo(StepStatus.SUCCESS);
  }

  @Test
  void toEvent_answerPayload_returnsAnswerEvent() {
    var event =
        LangGraphAnalysisProvider.toEvent("{\"type\":\"ANSWER\",\"text\":\"done\"}", objectMapper);
    assertThat(event).isEqualTo(new AnswerEvent("done"));
  }

  @Test
  void toEvent_malformedPayload_returnsErrorEvent() {
    var event = LangGraphAnalysisProvider.toEvent("not-json", objectMapper);
    assertThat(event).isInstanceOf(ErrorEvent.class);
  }

  @Test
  void toEvent_tablePayload_returnsTableEventWithFieldsIntact() {
    var event =
        LangGraphAnalysisProvider.toEvent(
            "{\"type\":\"TABLE\",\"tableId\":\"tbl_1\",\"intent\":\"計算各機台的不良率\","
                + "\"columns\":[\"machine_id\",\"defect_rate\"],"
                + "\"rows\":[[\"M1\",0.02]],\"truncated\":false}",
            objectMapper);
    assertThat(event).isInstanceOf(TableEvent.class);
    TableEvent tableEvent = (TableEvent) event;
    assertThat(tableEvent.tableId()).isEqualTo("tbl_1");
    assertThat(tableEvent.intent()).isEqualTo("計算各機台的不良率");
    assertThat(tableEvent.columns()).containsExactly("machine_id", "defect_rate");
    assertThat(tableEvent.rows()).hasSize(1);
    assertThat(tableEvent.rows().get(0)).containsExactly("M1", 0.02);
    assertThat(tableEvent.truncated()).isFalse();
  }

  @Test
  void toEvent_malformedTablePayload_returnsErrorEvent() {
    var event =
        LangGraphAnalysisProvider.toEvent(
            "{\"type\":\"TABLE\",\"tableId\":\"tbl_1\",\"columns\":\"not-a-list\"}", objectMapper);
    assertThat(event).isInstanceOf(ErrorEvent.class);
  }

  // ── resolveSourcePath ──────────────────────────────────────────────────────

  private static final int DEFAULT_TEST_TIMEOUT_SECONDS = 30;
  private static final int DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB = 64;

  private static LangGraphAnalysisProvider newProvider(
      MockWebServer mockWebServer, StorageProperties storageProperties) {
    return newProvider(mockWebServer, storageProperties, DEFAULT_TEST_TIMEOUT_SECONDS);
  }

  private static LangGraphAnalysisProvider newProvider(
      MockWebServer mockWebServer, StorageProperties storageProperties, int requestTimeoutSeconds) {
    return newProvider(
        mockWebServer,
        storageProperties,
        requestTimeoutSeconds,
        DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB);
  }

  private static LangGraphAnalysisProvider newProvider(
      MockWebServer mockWebServer,
      StorageProperties storageProperties,
      int requestTimeoutSeconds,
      int maxInMemorySizeMb) {
    AnalysisAgentProperties analysisProperties =
        new AnalysisAgentProperties(
            "http://localhost:" + mockWebServer.getPort(),
            "/data/uploads",
            requestTimeoutSeconds,
            maxInMemorySizeMb);
    return new LangGraphAnalysisProvider(
        analysisProperties, storageProperties, new ObjectMapper(), WebClient.builder());
  }

  @Test
  void resolveSourcePath_s3StorageType_buildsS3Url() throws Exception {
    try (MockWebServer mockWebServer = new MockWebServer()) {
      mockWebServer.start();
      StorageProperties storageProperties =
          new StorageProperties(
              "s3",
              "./data/files",
              null,
              null,
              null,
              new StorageProperties.S3("erd-cowork", "us-east-1", "http://minio:9000", true));
      LangGraphAnalysisProvider provider = newProvider(mockWebServer, storageProperties);

      assertThat(provider.resolveSourcePath("s1/a.csv")).isEqualTo("s3://erd-cowork/s1/a.csv");
    }
  }

  @Test
  void resolveSourcePath_localStorageType_buildsSourceRootPath() throws Exception {
    try (MockWebServer mockWebServer = new MockWebServer()) {
      mockWebServer.start();
      StorageProperties storageProperties =
          new StorageProperties("local", "./data/files", null, null, null, null);
      LangGraphAnalysisProvider provider = newProvider(mockWebServer, storageProperties);

      assertThat(provider.resolveSourcePath("s1/a.csv")).isEqualTo("/data/uploads/s1/a.csv");
    }
  }

  // ── generate(): end-to-end SSE consumption ────────────────────────────────

  private MockWebServer mockWebServer;
  private LangGraphAnalysisProvider provider;
  private StorageProperties localStorageProperties;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    mockWebServer.start();
    localStorageProperties = new StorageProperties("local", "./data/files", null, null, null, null);
    provider = newProvider(mockWebServer, localStorageProperties);
  }

  @AfterEach
  void tearDown() throws Exception {
    mockWebServer.shutdown();
  }

  @Test
  void generate_normalStream_producesStepTokenAndAnswerEvents() {
    String sseBody =
        "data: {\"type\":\"STEP\",\"stepKey\":\"analysis\",\"title\":\"分析資料中\"}\n\n"
            + "data: {\"type\":\"TOKEN\",\"delta\":\"Hello \"}\n\n"
            + "data: {\"type\":\"TOKEN\",\"delta\":\"world\"}\n\n"
            + "data: {\"type\":\"ANSWER\",\"text\":\"Hello world\"}\n\n";
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).hasSize(4);
    assertThat(events.get(0)).isInstanceOf(StepEvent.class);
    assertThat(events.get(1)).isEqualTo(new TokenEvent("Hello "));
    assertThat(events.get(2)).isEqualTo(new TokenEvent("world"));
    assertThat(events.get(3)).isEqualTo(new AnswerEvent("Hello world"));

    assertThat(result.outcome().get().answerText()).isEqualTo("Hello world");
    assertThat(result.outcome().get().html()).isNull();
    assertThat(result.outcome().get().questions()).isNull();
  }

  @Test
  void generate_streamStalls_timesOutWithErrorEventInsteadOfHanging() {
    // Fix 4: no wall-clock bound previously existed anywhere in this pipeline — a runaway
    // LLM-generated query on the agent-service side (e.g. an unbounded cross-join) would hold
    // this flux, its executor thread, and the SSE connection open indefinitely. A short
    // requestTimeoutSeconds against a response body that never completes proves the timeout
    // converts into an ErrorEvent rather than the flux hanging or a raw TimeoutException
    // escaping generate().
    LangGraphAnalysisProvider stallingProvider =
        newProvider(mockWebServer, localStorageProperties, 1);
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBodyDelay(5, java.util.concurrent.TimeUnit.SECONDS)
            .setBody("data: {\"type\":\"ANSWER\",\"text\":\"too late\"}\n\n"));

    ProviderResult result =
        stallingProvider.generate(
            new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).hasSize(1);
    assertThat(events.get(0)).isInstanceOf(ErrorEvent.class);
    assertThat(((ErrorEvent) events.get(0)).code()).isEqualTo("ANALYSIS_TIMEOUT");
  }

  // ── DASHBOARD_HTML interception ─────────────────────────────────────────────

  @Test
  void generate_dashboardHtmlPayload_noSpaceForm_capturedInOutcomeAndNotEmittedDownstream() {
    // No-space form ("type":"DASHBOARD_HTML") — kept alongside the real-wire-form test below to
    // pin that the discriminator match is whitespace-immune in both directions, not just the one
    // that happens to match the real wire bytes.
    String sseBody =
        "data: {\"type\":\"STEP\",\"stepKey\":\"analysis\",\"title\":\"分析資料中\"}\n\n"
            + "data: {\"type\":\"DASHBOARD_HTML\",\"html\":\"<div"
            + " data-block-id=\\\"stat-1\\\"></div>\"}\n\n"
            + "data: {\"type\":\"STEP\",\"stepKey\":\"analysis\",\"title\":\"分析資料中\","
            + "\"status\":\"SUCCESS\"}\n\n"
            + "data: {\"type\":\"ANSWER\",\"text\":\"儀表板已完成\"}\n\n";
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    // The DASHBOARD_HTML payload must contribute nothing to the downstream flux: exactly the
    // two STEP events and the ANSWER event remain, none of them an ErrorEvent.
    assertThat(events).hasSize(3);
    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);
    assertThat(events.get(0)).isInstanceOf(StepEvent.class);
    assertThat(events.get(1)).isInstanceOf(StepEvent.class);
    assertThat(events.get(2)).isEqualTo(new AnswerEvent("儀表板已完成"));

    AgentOutcome outcome = result.outcome().get();
    assertThat(outcome.answerText()).isEqualTo("儀表板已完成");
    assertThat(outcome.html()).isEqualTo("<div data-block-id=\"stat-1\"></div>");
  }

  @Test
  void generate_dashboardHtmlPayload_realWireSpaceForm_capturedInOutcomeAndNotEmittedDownstream() {
    // Reproduces the ACTUAL wire bytes agent-service emits: FastAPI serializes SSE data via
    // json.dumps(jsonable_encoder(...)) with the default separators, which put a space after
    // every colon — {"type": "DASHBOARD_HTML", "html": "..."}. A raw substring sniff for
    // "\"type\":\"DASHBOARD_HTML\"" (no space) never matches this payload, so interception would
    // silently never fire in production: the payload falls into toEvent, fails discriminator
    // resolution, and surfaces as a bogus ErrorEvent while outcome.html stays null. This test is
    // the missing assertion that would have caught that regression.
    String sseBody =
        "data: {\"type\": \"STEP\", \"stepKey\": \"analysis\", \"title\": \"分析資料中\"}\n\n"
            + "data: {\"type\": \"DASHBOARD_HTML\", \"html\": \"<div"
            + " data-block-id=\\\"stat-1\\\"></div>\"}\n\n"
            + "data: {\"type\": \"STEP\", \"stepKey\": \"analysis\", \"title\": \"分析資料中\", "
            + "\"status\": \"SUCCESS\"}\n\n"
            + "data: {\"type\": \"ANSWER\", \"text\": \"儀表板已完成\"}\n\n";
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    // The DASHBOARD_HTML payload must contribute nothing to the downstream flux, and critically,
    // it must not turn into an ErrorEvent (the regression this test pins).
    assertThat(events).hasSize(3);
    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);
    assertThat(events.get(0)).isInstanceOf(StepEvent.class);
    assertThat(events.get(1)).isInstanceOf(StepEvent.class);
    assertThat(events.get(2)).isEqualTo(new AnswerEvent("儀表板已完成"));

    AgentOutcome outcome = result.outcome().get();
    assertThat(outcome.answerText()).isEqualTo("儀表板已完成");
    assertThat(outcome.html()).isEqualTo("<div data-block-id=\"stat-1\"></div>");
  }

  @Test
  void generate_noDashboardHtmlPayload_outcomeHtmlIsNull() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody("data: {\"type\":\"ANSWER\",\"text\":\"no dashboard here\"}\n\n"));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).hasSize(1);
    assertThat(result.outcome().get().html()).isNull();
  }

  @Test
  void generate_dashboardHtmlEventOver256Kb_streamCompletesAndHtmlCaptured() {
    // Repro: a real DASHBOARD_HTML event can exceed Spring WebClient's default 256KB
    // maxInMemorySize; this ~1MB html field proves the provider's own exchangeStrategies cap
    // (not just luck) is what lets the stream complete.
    String oversizedHtml = "<div>" + "a".repeat(1024 * 1024) + "</div>";
    String sseBody =
        "data: {\"type\":\"DASHBOARD_HTML\",\"html\":\""
            + oversizedHtml
            + "\"}\n\n"
            + "data: {\"type\":\"ANSWER\",\"text\":\"儀表板已完成\"}\n\n";
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);
    assertThat(events).hasSize(1);
    assertThat(events.get(0)).isEqualTo(new AnswerEvent("儀表板已完成"));

    AgentOutcome outcome = result.outcome().get();
    assertThat(outcome.html()).isEqualTo(oversizedHtml);
  }

  @Test
  void generate_dashboardHtmlEventOver256Kb_withDefaultWebClientBuffer_failsWithBufferLimitError() {
    // Negative control: with maxInMemorySizeMb=0, the same oversized payload must fail the stream
    // (an ErrorEvent, per onErrorResume) rather than complete — pinning that the fix above is
    // actually exercising the buffer limit, not merely coincidental.
    LangGraphAnalysisProvider tinyBufferProvider =
        newProvider(mockWebServer, localStorageProperties, DEFAULT_TEST_TIMEOUT_SECONDS, 0);
    String oversizedHtml = "<div>" + "a".repeat(1024 * 1024) + "</div>";
    String sseBody = "data: {\"type\":\"DASHBOARD_HTML\",\"html\":\"" + oversizedHtml + "\"}\n\n";
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        tinyBufferProvider.generate(
            new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).hasSize(1);
    assertThat(events.get(0)).isInstanceOf(ErrorEvent.class);
  }

  // ── QUESTION interception (ask_user) ────────────────────────────────────────

  @Test
  void generate_questionPayload_capturedInOutcomeAndNotEmittedDownstream() {
    // Unlike DASHBOARD_HTML, QUESTION is ALREADY a registered AgentEvent subtype, so it parses
    // cleanly through toEvent -- but must still be captured (not forwarded) here so finalize()
    // stays the ONLY place either provider mode ever emits a QuestionEvent to the frontend.
    String sseBody =
        "data: {\"type\":\"STEP\",\"stepKey\":\"tool_ask_user_1\",\"title\":\"釐清問題\"}\n\n"
            + "data: {\"type\":\"QUESTION\",\"questions\":"
            + "[{\"text\":\"你想看哪個指標?\",\"options\":[\"不良率\",\"產量\"],"
            + "\"multiSelect\":false}]}\n\n"
            + "data: {\"type\":\"ANSWER\",\"text\":\"這份資料欄位較多,我想先確認方向。\"}\n\n";
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    // The QUESTION payload must contribute nothing to the downstream flux: exactly the STEP and
    // ANSWER events remain, and neither is an ErrorEvent (proves it parsed, not just vanished).
    assertThat(events).hasSize(2);
    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);
    assertThat(events.get(0)).isInstanceOf(StepEvent.class);
    assertThat(events.get(1)).isEqualTo(new AnswerEvent("這份資料欄位較多,我想先確認方向。"));

    AgentOutcome outcome = result.outcome().get();
    assertThat(outcome.questions())
        .containsExactly(new ClarifyingQuestion("你想看哪個指標?", List.of("不良率", "產量"), false));
  }

  @Test
  void generate_noQuestionPayload_outcomeQuestionsStaysNull() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody("data: {\"type\":\"ANSWER\",\"text\":\"no question here\"}\n\n"));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    result.events().collectList().block();

    assertThat(result.outcome().get().questions()).isNull();
  }

  @Test
  void generate_serverError_eventsEndWithErrorEvent() {
    mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("boom"));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).hasSize(1);
    assertThat(events.get(0)).isInstanceOf(ErrorEvent.class);
  }

  @Test
  void generate_historyMessageWithNullText_doesNotThrowAndYieldsErrorEvent() {
    // HistoryMessage.text() == null trips Map.of("text", null) inside buildRequestBody, which
    // runs eagerly today. generate() itself must not throw — only the returned Flux may carry
    // the failure, as an ErrorEvent.
    List<HistoryMessage> historyWithNullText = List.of(new HistoryMessage("USER", null));

    ProviderResult result =
        provider.generate(
            new AgentRequest("u1", "s1", "question", historyWithNullText, List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).hasSize(1);
    assertThat(events.get(0)).isInstanceOf(ErrorEvent.class);
  }

  @Test
  void generate_fileContextWithNullAlias_doesNotThrowAndYieldsErrorEvent() {
    // AgentFileContext.alias() == null trips Map.of("alias", null) inside the sources mapper.
    var fileContextWithNullAlias =
        new AgentFileContext(null, "sales.csv", "text/csv", "session-1/sales.csv", null);

    ProviderResult result =
        provider.generate(
            new AgentRequest(
                "u1", "s1", "question", List.of(), List.of(fileContextWithNullAlias), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).hasSize(1);
    assertThat(events.get(0)).isInstanceOf(ErrorEvent.class);
  }

  @Test
  void generate_historyMessageWithUnrecognizedSender_doesNotThrowAndYieldsErrorEvent() {
    // toHistoryEntry throws for any sender other than "USER"/"AI" (Fix 3) rather than silently
    // mapping it to "user"; that throw must surface as an ErrorEvent, not escape generate().
    List<HistoryMessage> historyWithUnknownSender = List.of(new HistoryMessage("SYSTEM", "hi"));

    ProviderResult result =
        provider.generate(
            new AgentRequest("u1", "s1", "question", historyWithUnknownSender, List.of(), null));
    List<AgentEvent> events = result.events().collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).hasSize(1);
    assertThat(events.get(0)).isInstanceOf(ErrorEvent.class);
  }

  @Test
  void generate_requestBody_mapsHistorySenderToPythonRoleVocabulary() throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody("data: {\"type\":\"ANSWER\",\"text\":\"ok\"}\n\n"));

    List<HistoryMessage> history =
        List.of(new HistoryMessage("USER", "hi"), new HistoryMessage("AI", "hello"));

    provider
        .generate(new AgentRequest("u1", "s1", "question", history, List.of(), null))
        .events()
        .collectList()
        .block();

    RecordedRequest request = mockWebServer.takeRequest();
    assertThat(request.getPath()).isEqualTo("/chat");
    String body = request.getBody().readUtf8();
    // HistoryMessage.sender() carries "USER"/"AI"; Python expects "user"/"assistant".
    assertThat(body).contains("\"role\":\"user\"");
    assertThat(body).contains("\"role\":\"assistant\"");
    assertThat(body).doesNotContain("\"role\":\"USER\"");
    assertThat(body).doesNotContain("\"role\":\"AI\"");
  }

  @Test
  void generate_requestBody_includesResolvedSourcePath() throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody("data: {\"type\":\"ANSWER\",\"text\":\"ok\"}\n\n"));

    var fileContext =
        new AgentFileContext("f1", "sales.csv", "text/csv", "session-1/sales.csv", null);

    provider
        .generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(fileContext), null))
        .events()
        .collectList()
        .block();

    RecordedRequest request = mockWebServer.takeRequest();
    String body = request.getBody().readUtf8();
    assertThat(body).contains("\"path\":\"/data/uploads/session-1/sales.csv\"");
    assertThat(body).contains("\"alias\":\"f1\"");
    assertThat(body).contains("\"fileType\":\"text/csv\"");
  }

  // ── previousDashboardHtml feedback (baseArtifactId iteration, spec §16.2.1) ─────

  @Test
  void generate_requestBody_withPreviousArtifactHtml_includesPreviousDashboardHtmlField()
      throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody("data: {\"type\":\"ANSWER\",\"text\":\"ok\"}\n\n"));
    String previousHtml = "<html><body><p>first</p></body></html>";

    provider
        .generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), previousHtml))
        .events()
        .collectList()
        .block();

    RecordedRequest request = mockWebServer.takeRequest();
    String body = request.getBody().readUtf8();
    assertThat(body).contains("\"previousDashboardHtml\"");
    assertThat(body).contains("<p>first</p>");
  }

  @Test
  void generate_requestBody_withNullPreviousArtifactHtml_omitsFieldEntirely() throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody("data: {\"type\":\"ANSWER\",\"text\":\"ok\"}\n\n"));

    provider
        .generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), null))
        .events()
        .collectList()
        .block();

    RecordedRequest request = mockWebServer.takeRequest();
    String body = request.getBody().readUtf8();
    assertThat(body).doesNotContain("previousDashboardHtml");
  }

  @Test
  void generate_requestBody_withBlankPreviousArtifactHtml_omitsFieldEntirely() throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody("data: {\"type\":\"ANSWER\",\"text\":\"ok\"}\n\n"));

    provider
        .generate(new AgentRequest("u1", "s1", "question", List.of(), List.of(), "   "))
        .events()
        .collectList()
        .block();

    RecordedRequest request = mockWebServer.takeRequest();
    String body = request.getBody().readUtf8();
    assertThat(body).doesNotContain("previousDashboardHtml");
  }
}
