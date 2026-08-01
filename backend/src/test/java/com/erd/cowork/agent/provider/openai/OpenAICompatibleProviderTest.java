package com.erd.cowork.agent.provider.openai;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.AnswerEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.config.AgentProperties;
import java.util.List;
import java.util.stream.Collectors;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.apache.velocity.app.VelocityEngine;
import org.apache.velocity.runtime.RuntimeConstants;
import org.apache.velocity.runtime.resource.loader.ClasspathResourceLoader;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.web.reactive.function.client.WebClient;

class OpenAICompatibleProviderTest {

  private static VelocityEngine buildVelocityEngine() {
    VelocityEngine engine = new VelocityEngine();
    engine.setProperty(RuntimeConstants.RESOURCE_LOADERS, "classpath");
    engine.setProperty("resource.loader.classpath.class", ClasspathResourceLoader.class.getName());
    engine.setProperty(RuntimeConstants.INPUT_ENCODING, "UTF-8");
    engine.init();
    return engine;
  }

  private static final int CONTEXT_WINDOW = 131072;

  // TokenExchangeClient is still lazily resolved via ObjectProvider (different reason than the
  // former guard cycle). Tests use bearer auth so the token-exchange path is never invoked.
  @SuppressWarnings("unchecked")
  private final ObjectProvider<TokenExchangeClient> mockTokenExchangeClientProvider =
      Mockito.mock(ObjectProvider.class);

  // GenerationRepairGuard is now injected directly (no ObjectProvider — cycle dissolved).
  // Tests disable repair so harden() is never called; the mock satisfies the constructor Assert.
  private final GenerationRepairGuard mockGenerationRepairGuard =
      Mockito.mock(GenerationRepairGuard.class);

  private MockWebServer mockWebServer;
  private OpenAICompatibleProvider provider;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    mockWebServer.start();

    String baseUrl = "http://localhost:" + mockWebServer.getPort();
    AgentProperties.OpenAiCompatible openAiCompatible =
        new AgentProperties.OpenAiCompatible(
            baseUrl, "k1", "test-model", CONTEXT_WINDOW, "/v1/chat/completions", "bearer", null);
    AgentProperties props =
        new AgentProperties(
            "openai-compatible", openAiCompatible, new AgentProperties.Repair(false));

    provider =
        new OpenAICompatibleProvider(
            props,
            new PromptAssembler(buildVelocityEngine()),
            WebClient.builder(),
            new com.fasterxml.jackson.databind.ObjectMapper(),
            mockTokenExchangeClientProvider,
            mockGenerationRepairGuard);
  }

  @AfterEach
  void tearDown() throws Exception {
    mockWebServer.shutdown();
  }

  private static String tokenConcat(List<AgentEvent> events) {
    return events.stream()
        .filter(event -> event instanceof TokenEvent)
        .map(event -> ((TokenEvent) event).delta())
        .collect(Collectors.joining());
  }

  // ── test 1: normal stream ─────────────────────────────────────────────────

  @Test
  void normalStream_producesTokenEventsAndAnswerEventAndCorrectHtml() throws Exception {
    String sseBody =
        "data: {\"choices\":[{\"delta\":{\"content\":\"Hello \"}}]}\n\n"
            + "data: {\"choices\":[{\"delta\":{\"content\":\"world. ```html\\n"
            + "<p>x</p>\\n"
            + "``` done\"}}]}\n\n"
            + "data: [DONE]\n\n";

    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(
            new AgentRequest("u1", "s1", "test question", List.of(), List.of(), null));

    List<AgentEvent> events = result.events().collectList().block();
    assertThat(events).isNotNull();

    // Token deltas join to the expected string (two spaces before "done" because
    // "world. " ends with space and " done" starts with space after closing fence)
    assertThat(tokenConcat(events)).isEqualTo("Hello world.  done");

    // Last event is the AnswerEvent with the same text
    AgentEvent last = events.get(events.size() - 1);
    assertThat(last).isInstanceOf(AnswerEvent.class);
    assertThat(((AnswerEvent) last).text()).isEqualTo("Hello world.  done");

    // Extraction supplies the inner HTML (trimmed)
    assertThat(result.outcome().get().html()).isEqualTo("<p>x</p>");
  }

  // ── test 2: role-only delta chunk (no "content" key) ─────────────────────

  @Test
  void deltaWithoutContent_roleOnlyChunk_skipped() throws Exception {
    String sseBody =
        "data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\n\n"
            + "data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n"
            + "data: [DONE]\n\n";

    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null));

    List<AgentEvent> events = result.events().collectList().block();
    assertThat(events).isNotNull();
    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);
    // Role-only chunk skipped; only "hi" token is emitted
    assertThat(tokenConcat(events)).isEqualTo("hi");
  }

  // ── test 3: 500 response → ErrorEvent, no exception thrown ───────────────

  @Test
  void serverError500_eventsEndWithErrorEvent() {
    mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("Internal Server Error"));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null));

    List<AgentEvent> events = result.events().collectList().block();
    assertThat(events).isNotNull();
    assertThat(events).hasSize(1);

    AgentEvent only = events.get(0);
    assertThat(only).isInstanceOf(ErrorEvent.class);
    assertThat(((ErrorEvent) only).code()).isEqualTo("PROVIDER_ERROR");
  }

  // ── test 4: request assertions ────────────────────────────────────────────

  @Test
  void requestAssertion_pathAndAuthHeaderAndBodyContent() throws Exception {
    String sseBody = "data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n\ndata: [DONE]\n\n";

    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    provider
        .generate(new AgentRequest("u1", "s1", "summarize this", List.of(), List.of(), null))
        .events()
        .collectList()
        .block();

    RecordedRequest req = mockWebServer.takeRequest();
    assertThat(req.getPath()).isEqualTo("/v1/chat/completions");
    assertThat(req.getHeader("Authorization")).isEqualTo("Bearer k1");

    String body = req.getBody().readUtf8();
    assertThat(body).contains("\"stream\":true");
    // system prompt signature phrase
    assertThat(body).contains("dashboard generator");
  }

  // ── test 5: empty apiKey → no Authorization header ────────────────────────

  // ── test 6: OpenRouter SSE comment line (": OPENROUTER PROCESSING") ────────
  // Spring WebFlux SSE decoder silently drops comment lines (lines starting
  // with ':') before they reach parseContentToken, so only the data chunk
  // after the comment should produce a token.

  @Test
  void openRouterCommentLine_ignoredAndTokenStillEmitted() throws Exception {
    // The comment line ": OPENROUTER PROCESSING" is a valid SSE comment and
    // must not cause an error or be treated as a data event.
    String sseBody =
        ": OPENROUTER PROCESSING\n\n"
            + "data: {\"choices\":[{\"delta\":{\"content\":\"pong\"}}]}\n\n"
            + "data: [DONE]\n\n";

    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "ping", List.of(), List.of(), null));

    List<AgentEvent> events = result.events().collectList().block();
    assertThat(events).isNotNull();
    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);
    assertThat(tokenConcat(events)).isEqualTo("pong");
  }

  // ── test 7: OpenRouter usage chunk with empty choices array ─────────────────
  // OpenRouter appends a final {"choices":[],"usage":{...}} chunk before
  // [DONE]. choices[0] is missing → MissingNode → parseContentToken skips it.

  @Test
  void openRouterEmptyChoicesUsageChunk_skippedWithoutError() throws Exception {
    String sseBody =
        "data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n\n"
            + "data: {\"choices\":[],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":5}}\n\n"
            + "data: [DONE]\n\n";

    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "hi", List.of(), List.of(), null));

    List<AgentEvent> events = result.events().collectList().block();
    assertThat(events).isNotNull();
    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);
    // Only the "hello" token must appear; the usage chunk must be silently dropped
    assertThat(tokenConcat(events)).isEqualTo("hello");
  }

  @Test
  void emptyApiKey_noAuthorizationHeader() throws Exception {
    String baseUrl = "http://localhost:" + mockWebServer.getPort();
    AgentProperties.OpenAiCompatible openAiCompatible =
        new AgentProperties.OpenAiCompatible(
            baseUrl, "", "test-model", CONTEXT_WINDOW, "/v1/chat/completions", "bearer", null);
    AgentProperties propsNoKey =
        new AgentProperties(
            "openai-compatible", openAiCompatible, new AgentProperties.Repair(false));
    OpenAICompatibleProvider providerNoKey =
        new OpenAICompatibleProvider(
            propsNoKey,
            new PromptAssembler(buildVelocityEngine()),
            WebClient.builder(),
            new com.fasterxml.jackson.databind.ObjectMapper(),
            mockTokenExchangeClientProvider,
            mockGenerationRepairGuard);

    String sseBody = "data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\ndata: [DONE]\n\n";
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    providerNoKey
        .generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null))
        .events()
        .collectList()
        .block();

    RecordedRequest req = mockWebServer.takeRequest();
    assertThat(req.getHeader("Authorization")).isNull();
  }

  // ── test 9: reasoning delta → ThinkingEvent ───────────────────────────────

  @Test
  void reasoningDelta_emitsThinkingEventAlongsideContentToken() throws Exception {
    // Chunk 1 has both reasoning and content in the same delta.
    // Chunk 2 has only reasoning (no content).
    // Chunk 3 has only content (no reasoning).
    String sseBody =
        "data: {\"choices\":[{\"delta\":{\"reasoning\":\"think \",\"content\":\"hello\"}}]}\n\n"
            + "data: {\"choices\":[{\"delta\":{\"reasoning\":\"more\"}}]}\n\n"
            + "data: {\"choices\":[{\"delta\":{\"content\":\" world\"}}]}\n\n"
            + "data: [DONE]\n\n";

    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null));

    List<AgentEvent> events = result.events().collectList().block();
    assertThat(events).isNotNull();
    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);

    // Content tokens: "hello" and " world"
    assertThat(tokenConcat(events)).isEqualTo("hello world");

    // ThinkingEvents: "think " and "more"
    List<com.erd.cowork.agent.event.ThinkingEvent> thinkingEvents =
        events.stream()
            .filter(event -> event instanceof com.erd.cowork.agent.event.ThinkingEvent)
            .map(event -> (com.erd.cowork.agent.event.ThinkingEvent) event)
            .toList();
    assertThat(thinkingEvents).hasSize(2);
    assertThat(
            thinkingEvents.stream().map(com.erd.cowork.agent.event.ThinkingEvent::delta).toList())
        .containsExactlyInAnyOrder("think ", "more");
  }

  @Test
  void reasoningDelta_nullOrMissing_noThinkingEvent() throws Exception {
    // reasoning field is present but null — should produce no ThinkingEvent
    String sseBody =
        "data: {\"choices\":[{\"delta\":{\"reasoning\":null,\"content\":\"ok\"}}]}\n\n"
            + "data: [DONE]\n\n";

    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "text/event-stream")
            .setBody(sseBody));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null));

    List<AgentEvent> events = result.events().collectList().block();
    assertThat(events).isNotNull();
    assertThat(events)
        .noneMatch(event -> event instanceof com.erd.cowork.agent.event.ThinkingEvent);
    assertThat(tokenConcat(events)).isEqualTo("ok");
  }

  // ── fix #2: thinking branch silenced on HTTP error (no onErrorDropped) ────

  @Test
  void serverError500_thinkingBranchSilenced_exactlyOneErrorEvent() {
    // With refCount(2), a 500 error propagates to both the content branch and the
    // thinking branch. Before the fix, the second error triggered onErrorDropped
    // log noise. The fix adds onErrorResume(e -> Flux.empty()) to the thinking
    // branch so only the content branch surfaces the error as an ErrorEvent.
    mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("Internal Server Error"));

    ProviderResult result =
        provider.generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null));

    List<AgentEvent> events = result.events().collectList().block();
    assertThat(events).isNotNull();
    // Exactly one ErrorEvent — no duplicate from the thinking branch.
    assertThat(events).hasSize(1);
    assertThat(events.get(0)).isInstanceOf(ErrorEvent.class);
    assertThat(((ErrorEvent) events.get(0)).code()).isEqualTo("PROVIDER_ERROR");
  }
}
