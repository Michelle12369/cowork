package com.erd.cowork.agent.provider.openai;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.config.AgentProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.stream.Collectors;
import okhttp3.mockwebserver.Dispatcher;
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

class TokenExchangeProviderTest {

  private static VelocityEngine buildVelocityEngine() {
    VelocityEngine engine = new VelocityEngine();
    engine.setProperty(RuntimeConstants.RESOURCE_LOADERS, "classpath");
    engine.setProperty("resource.loader.classpath.class", ClasspathResourceLoader.class.getName());
    engine.setProperty(RuntimeConstants.INPUT_ENCODING, "UTF-8");
    engine.init();
    return engine;
  }

  private static final String EXCHANGE_PATH = "/exchange";
  private static final String HEADER_NAME = "X-Test-Token-Header";
  private static final String SERVICE_KEY = "j1-service-key";
  private static final int CONTEXT_WINDOW = 131072;

  private MockWebServer mockWebServer;
  private ConcurrentLinkedQueue<MockResponse> exchangeResponses;
  private ConcurrentLinkedQueue<MockResponse> llmResponses;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    exchangeResponses = new ConcurrentLinkedQueue<>();
    llmResponses = new ConcurrentLinkedQueue<>();

    mockWebServer.setDispatcher(
        new Dispatcher() {
          @Override
          public MockResponse dispatch(RecordedRequest request) {
            if (EXCHANGE_PATH.equals(request.getPath())) {
              MockResponse response = exchangeResponses.poll();
              return response != null ? response : new MockResponse().setResponseCode(500);
            }
            MockResponse response = llmResponses.poll();
            return response != null ? response : new MockResponse().setResponseCode(500);
          }
        });
    mockWebServer.start();
  }

  @AfterEach
  void tearDown() throws Exception {
    mockWebServer.shutdown();
  }

  @SuppressWarnings("unchecked")
  private OpenAICompatibleProvider buildProvider(MockWebServer server, int ttlSeconds) {
    String baseUrl = "http://localhost:" + server.getPort();
    AgentProperties.OpenAiCompatible.TokenExchange tokenExchange =
        new AgentProperties.OpenAiCompatible.TokenExchange(
            baseUrl + EXCHANGE_PATH, SERVICE_KEY, null, HEADER_NAME, ttlSeconds);
    AgentProperties.OpenAiCompatible openAiCompatible =
        new AgentProperties.OpenAiCompatible(
            baseUrl,
            null,
            "test-model",
            CONTEXT_WINDOW,
            "/v1/chat/completions",
            "token-exchange",
            tokenExchange);
    AgentProperties props =
        new AgentProperties(
            "openai-compatible", openAiCompatible, new AgentProperties.Repair(false));

    ObjectMapper objectMapper = new ObjectMapper();
    WebClient.Builder webClientBuilder = WebClient.builder();

    // TokenExchangeClient is constructed first so its WebClient is built with no base URL.
    // OpenAICompatibleProvider is constructed second and mutates the builder to add base URL.
    TokenExchangeClient tokenClient =
        new TokenExchangeClient(props, webClientBuilder, objectMapper);

    ObjectProvider<TokenExchangeClient> tokenProvider = Mockito.mock(ObjectProvider.class);
    Mockito.when(tokenProvider.getObject()).thenReturn(tokenClient);

    // These tests exercise generate() only; harden() is never invoked so the guard is mocked
    // but never called — it must be non-null to satisfy the constructor Assert.
    GenerationRepairGuard mockGuard = Mockito.mock(GenerationRepairGuard.class);
    return new OpenAICompatibleProvider(
        props,
        new PromptAssembler(buildVelocityEngine()),
        webClientBuilder,
        objectMapper,
        tokenProvider,
        mockGuard);
  }

  private List<RecordedRequest> drainAllRequests() throws InterruptedException {
    int totalRequestCount = mockWebServer.getRequestCount();
    List<RecordedRequest> recordedRequests = new ArrayList<>();
    for (int index = 0; index < totalRequestCount; index++) {
      recordedRequests.add(mockWebServer.takeRequest());
    }
    return recordedRequests;
  }

  private static String tokenConcat(List<AgentEvent> events) {
    return events.stream()
        .filter(event -> event instanceof TokenEvent)
        .map(event -> ((TokenEvent) event).delta())
        .collect(Collectors.joining());
  }

  private static MockResponse jsonExchangeResponse(String token) {
    return new MockResponse()
        .setResponseCode(200)
        .addHeader("Content-Type", "application/json")
        .setBody("{\"token\":\"" + token + "\"}");
  }

  private static MockResponse sseResponse(String content) {
    return new MockResponse()
        .setResponseCode(200)
        .addHeader("Content-Type", "text/event-stream")
        .setBody(
            "data: {\"choices\":[{\"delta\":{\"content\":\""
                + content
                + "\"}}]}\n\ndata: [DONE]\n\n");
  }

  // ── test 1: exchange success with correct header and j1 payload ────────────

  @Test
  void tokenExchange_success_correctHeaderAndPayload() throws Exception {
    exchangeResponses.add(jsonExchangeResponse("j2-abc"));
    llmResponses.add(sseResponse("hello"));

    OpenAICompatibleProvider provider = buildProvider(mockWebServer, 300);
    List<AgentEvent> events =
        provider
            .generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null))
            .events()
            .collectList()
            .block();

    assertThat(events).isNotNull();

    List<RecordedRequest> recordedRequests = drainAllRequests();
    RecordedRequest exchangeRequest =
        recordedRequests.stream()
            .filter(httpRequest -> EXCHANGE_PATH.equals(httpRequest.getPath()))
            .findFirst()
            .orElseThrow();
    RecordedRequest llmRequest =
        recordedRequests.stream()
            .filter(httpRequest -> !EXCHANGE_PATH.equals(httpRequest.getPath()))
            .findFirst()
            .orElseThrow();

    // Exchange request body must contain the "key" field and the service account key value
    String exchangeBody = exchangeRequest.getBody().readUtf8();
    assertThat(exchangeBody).contains("\"key\"");
    assertThat(exchangeBody).contains(SERVICE_KEY);

    // LLM request must carry the j2 token in the custom header, not a Bearer token
    assertThat(llmRequest.getHeader(HEADER_NAME)).isEqualTo("j2-abc");
    assertThat(llmRequest.getHeader("Authorization")).isNull();

    assertThat(tokenConcat(events)).isEqualTo("hello");
  }

  // ── test 2: cache — two generate() calls only exchange once ───────────────

  @Test
  void tokenExchange_cache_twoCallsOnlyExchangeOnce() throws Exception {
    exchangeResponses.add(jsonExchangeResponse("j2-cached"));
    llmResponses.add(sseResponse("ok"));
    llmResponses.add(sseResponse("ok"));

    OpenAICompatibleProvider provider = buildProvider(mockWebServer, 300);
    AgentRequest agentRequest = new AgentRequest("u1", "s1", "test", List.of(), List.of(), null);

    provider.generate(agentRequest).events().collectList().block();
    provider.generate(agentRequest).events().collectList().block();

    List<RecordedRequest> recordedRequests = drainAllRequests();
    long exchangeCount =
        recordedRequests.stream()
            .filter(httpRequest -> EXCHANGE_PATH.equals(httpRequest.getPath()))
            .count();
    // Token is cached after the first exchange; the second generate() reuses it
    assertThat(exchangeCount).isEqualTo(1);
  }

  // ── test 3: 401 triggers invalidate + retry success ───────────────────────

  @Test
  void tokenExchange_401_invalidatesAndRetries() throws Exception {
    exchangeResponses.add(jsonExchangeResponse("j2-first"));
    exchangeResponses.add(jsonExchangeResponse("j2-second"));
    llmResponses.add(new MockResponse().setResponseCode(401)); // first attempt
    llmResponses.add(sseResponse("ok")); // retry succeeds

    OpenAICompatibleProvider provider = buildProvider(mockWebServer, 300);
    List<AgentEvent> events =
        provider
            .generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null))
            .events()
            .collectList()
            .block();

    assertThat(events).isNotNull();

    List<RecordedRequest> recordedRequests = drainAllRequests();
    long exchangeCount =
        recordedRequests.stream()
            .filter(httpRequest -> EXCHANGE_PATH.equals(httpRequest.getPath()))
            .count();
    assertThat(exchangeCount).isEqualTo(2);

    // The retry must use the fresh j2-second token
    RecordedRequest lastLlmRequest =
        recordedRequests.stream()
            .filter(httpRequest -> !EXCHANGE_PATH.equals(httpRequest.getPath()))
            .reduce((previous, current) -> current)
            .orElseThrow();
    assertThat(lastLlmRequest.getHeader(HEADER_NAME)).isEqualTo("j2-second");

    assertThat(events).noneMatch(event -> event instanceof ErrorEvent);
    assertThat(tokenConcat(events)).isEqualTo("ok");
  }

  // ── test 4: 401 on both attempts → ErrorEvent ─────────────────────────────

  @Test
  void tokenExchange_401BothAttempts_errorEvent() throws Exception {
    exchangeResponses.add(jsonExchangeResponse("j2-tok"));
    exchangeResponses.add(jsonExchangeResponse("j2-tok"));
    llmResponses.add(new MockResponse().setResponseCode(401)); // first attempt
    llmResponses.add(new MockResponse().setResponseCode(401)); // retry also fails

    OpenAICompatibleProvider provider = buildProvider(mockWebServer, 300);
    List<AgentEvent> events =
        provider
            .generate(new AgentRequest("u1", "s1", "test", List.of(), List.of(), null))
            .events()
            .collectList()
            .block();

    assertThat(events).isNotNull();
    long errorCount = events.stream().filter(event -> event instanceof ErrorEvent).count();
    assertThat(errorCount).isEqualTo(1);
    ErrorEvent errorEvent =
        events.stream()
            .filter(event -> event instanceof ErrorEvent)
            .map(event -> (ErrorEvent) event)
            .findFirst()
            .orElseThrow();
    assertThat(errorEvent.code()).isEqualTo("PROVIDER_ERROR");
  }
}
