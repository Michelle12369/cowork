package com.erd.cowork.agent.provider.analysis;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.AnswerEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.QuestionEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.model.ClarifyingQuestion;
import com.erd.cowork.agent.model.HistoryMessage;
import com.erd.cowork.agent.provider.AgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.logging.LogAnnotation;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.netty.handler.timeout.ReadTimeoutException;
import io.netty.handler.timeout.ReadTimeoutHandler;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicReference;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.MediaType;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.stereotype.Component;
import org.springframework.util.Assert;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.netty.http.client.HttpClient;

/**
 * Bridges the {@code agent-service} LangGraph analysis endpoint ({@code POST /chat}, SSE) into the
 * backend's {@link AgentEvent} stream. Provider id {@code langgraph-analysis} (data-analyst agent —
 * no HTML artifact).
 */
@Slf4j
@Component
@ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "langgraph-analysis")
@LogAnnotation
public class LangGraphAnalysisProvider implements AgentProvider {

  // ＝4× deepagent 端 fastapi.sse 自動 ping 間隔(15s,fastapi/routing.py _keepalive_inserter,
  // 每個宣告 response_class=EventSourceResponse 的 endpoint 皆自動套用,不可從 app 端調整)——位元組級
  // idle 偵測:ping 持續進來就不會觸發,真的卡住(連 ping 都停)在這個秒數內偵測到。這一層只偵測「有沒有
  // 位元組流入」,不是整輪時長上限——整輪上限見 generate() 內的 deadline Mono。
  private static final int IDLE_READ_TIMEOUT_SECONDS = 60;

  private final AnalysisAgentProperties analysisProperties;
  private final ObjectMapper objectMapper;
  private final WebClient webClient;
  private final StorageProperties storageProperties;

  public LangGraphAnalysisProvider(
      AnalysisAgentProperties analysisProperties,
      ObjectMapper objectMapper,
      WebClient.Builder webClientBuilder,
      StorageProperties storageProperties) {
    // analysisProperties bean is always present (@ConfigurationPropertiesScan); only its fields can
    // be unbound, so the fail-fast check targets baseUrl specifically.
    Assert.hasText(
        analysisProperties.baseUrl(),
        "erd.agent.analysis.base-url is required when provider=langgraph-analysis");
    this.analysisProperties = analysisProperties;
    this.objectMapper = objectMapper;
    this.storageProperties = storageProperties;
    // 這裡 NEVER 加 HttpClient#responseTimeout(Duration)——別被名字騙了,它底層就是一個
    // ReadTimeoutHandler,由每一個 inbound byte(含 deepagent 端 fastapi.sse 每 15s 自動送的
    // keepalive ping)重置,只要 ping 沒斷就永遠不會觸發,不是真正的整輪時長上限。真·整輪上限改在
    // generate() 內用 Reactor 層共用的 deadline Mono 實作(不受任何 inbound 訊號重置)。這裡只留
    // ReadTimeoutHandler 做位元組級 idle 偵測(見 IDLE_READ_TIMEOUT_SECONDS 註解)——pooled 連線閒置
    // 超過此秒數時 handler 會關閉該連線,下輪請求自動重建連線,功能無害;若 log 出現
    // post-termination 的 ReadTimeoutException(連線已因正常完成或例外結束後才觸發),屬預期噪音。
    HttpClient httpClient =
        HttpClient.create()
            .doOnConnected(
                connection ->
                    connection.addHandlerLast(new ReadTimeoutHandler(IDLE_READ_TIMEOUT_SECONDS)));
    // Spring's default 256KB per-SSE-event buffer is far too small for a DASHBOARD_HTML event
    // (full dashboard HTML + spec JSON in one line, easily hitting DataBufferLimitException) —
    // raise the cap via analysisProperties.maxInMemorySizeMb() (default 64MB).
    this.webClient =
        webClientBuilder
            .baseUrl(analysisProperties.baseUrl())
            .clientConnector(new ReactorClientHttpConnector(httpClient))
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

  @Override
  public ProviderResult generate(AgentRequest request) {
    log.info(
        "langgraph-analysis generate session={} fileCount={} questionLength={}",
        request.sessionId(),
        request.files().size(),
        request.question().length());

    AtomicReference<String> answerText = new AtomicReference<>("");
    // DASHBOARD_HTML is a python->java internal signal (agent-service/app/main.py) — deliberately
    // NOT registered in AgentEvent's @JsonSubTypes, so it must never reach toEvent's
    // deserialization
    // (would surface as a bogus ErrorEvent). See toEventOrEmpty for where this is captured.
    AtomicReference<String> capturedDashboardHtml = new AtomicReference<>();
    // QUESTION is captured, not forwarded — finalize() is the sole emitter (mirrors dashboard
    // mode).
    AtomicReference<List<ClarifyingQuestion>> capturedQuestions = new AtomicReference<>();
    // 整輪 wall-clock 上限,在 Reactor 層實作(不是 constructor 的 HttpClient connector——那層的
    // ReadTimeoutHandler 只偵測位元組級 idle,見其註解)。deadline 是一個「只發一次訊號」且 cache() 過
    // 的共用 Mono:.cache() 確保底下的計時器只在第一次被訂閱時啟動一次(即本 Flux 被訂閱那刻),之後每次
    // 被拿來當某個事件的逾時視窗都是重新訂閱同一個已啟動的計時器,不會被重置。用法是
    // Flux#timeout(firstTimeout, itemTimeoutFactory) 且兩者都指向這同一個 deadline:第一個事件之前的
    // 等待視窗與之後每個事件之間的等待視窗全部共用同一個 absolute deadline,所以無論事件流有沒有持續
    // 流入,計時器都不會被重置——這就是「整輪」語意。若事件流在期限內正常完成,timeout 運算子會直接取消
    // 對 deadline 的訂閱,不留下卡住的計時器、也不會誤判逾時。
    Mono<Long> deadline =
        Mono.delay(Duration.ofSeconds(analysisProperties.requestTimeoutSeconds())).cache();
    // Flux.defer: buildRequestBody can throw synchronously (e.g. a null Map.of value); deferring
    // ensures that exception flows through onErrorResume like any other stream failure, instead of
    // escaping generate() itself.
    Flux<AgentEvent> events =
        Flux.defer(
                () -> {
                  Map<String, Object> requestBody = buildRequestBody(request);
                  return webClient
                      .post()
                      .uri("/chat")
                      .accept(MediaType.TEXT_EVENT_STREAM)
                      .bodyValue(requestBody)
                      .retrieve()
                      .bodyToFlux(String.class);
                })
            // concatMap (not map): a DASHBOARD_HTML payload must be dropped entirely (zero
            // elements), which map can't express — concatMap preserves per-event ordering.
            .concatMap(payload -> toEventOrEmpty(payload, capturedDashboardHtml, capturedQuestions))
            .doOnNext(
                event -> {
                  if (event instanceof AnswerEvent answerEvent) {
                    answerText.set(answerEvent.text());
                  }
                })
            // 整輪逾時:見上面 deadline 的註解。逾時丟出的是 java.util.concurrent.TimeoutException
            // (Reactor FluxTimeout 內部固定拋這個型別),isTimeoutError 第一個 candidate 就會命中。
            .timeout(deadline, event -> deadline)
            .onErrorResume(
                error -> {
                  log.warn(
                      "langgraph-analysis stream failed session={}", request.sessionId(), error);
                  String errorCode =
                      isTimeoutError(error) ? "ANALYSIS_TIMEOUT" : "ANALYSIS_STREAM_FAILURE";
                  return Flux.just(
                      new ErrorEvent(
                          errorCode,
                          Objects.requireNonNullElse(
                              error.getMessage(), error.getClass().getSimpleName())));
                });

    return new ProviderResult(
        events,
        () ->
            new AgentOutcome(
                answerText.get(), capturedDashboardHtml.get(), capturedQuestions.get()));
  }

  /**
   * 兩種逾時路徑,丟出的例外型別不同,這裡都要接住:(1) {@code generate()} 的 Reactor 層 deadline timeout——直接丟未包裝的 {@link
   * TimeoutException}(java.util.concurrent,Reactor {@code FluxTimeout} 內部固定型別);(2) constructor 那層
   * {@code ReadTimeoutHandler} 的位元組級 idle 逾時——丟 {@link ReadTimeoutException}(繼承 netty 自家 {@code
   * io.netty.handler.timeout.TimeoutException},NOT {@link TimeoutException}),且 WebClient
   * 依失敗時機再包一層({@code WebClientResponseException} 若已收到 headers 才於讀 body 時逾時,{@code
   * WebClientRequestException} 若逾時發生在收到回應之前)—— 沿 cause chain 走訪,兩種 timeout
   * 例外類型命中任一層即算逾時。Package-private so the test can exercise it with synthetic exceptions directly.
   */
  static boolean isTimeoutError(Throwable error) {
    for (Throwable candidate = error; candidate != null; candidate = candidate.getCause()) {
      if (candidate instanceof TimeoutException || candidate instanceof ReadTimeoutException) {
        return true;
      }
    }
    return false;
  }

  private Map<String, Object> buildRequestBody(AgentRequest request) {
    List<Map<String, String>> sources =
        request.files().stream()
            .map(
                file ->
                    Map.of(
                        "alias", file.alias(),
                        "path", resolveSourcePath(file.storageKey()),
                        "fileType", file.type()))
            .toList();
    List<Map<String, String>> history =
        request.history().stream().map(LangGraphAnalysisProvider::toHistoryEntry).toList();
    // Map.of rejects null values, so a mutable map is used instead — previousDashboardHtml below
    // is genuinely optional (null on the first turn of a session).
    Map<String, Object> requestBody = new HashMap<>();
    requestBody.put("sessionId", request.sessionId());
    requestBody.put("userId", request.userId());
    requestBody.put("message", request.question());
    requestBody.put("history", history);
    requestBody.put("sources", sources);
    // previousArtifactHtml is the baseArtifactId-resolved (or latest-fallback) raw HTML the
    // orchestrator resolves for every turn (AgentOrchestrator#resolveArtifactHtml) — wired here so
    // an analysis-mode iteration turn can ground itself in the actually-selected prior version.
    // Omitted (not sent as JSON null) when absent.
    if (StringUtils.hasText(request.previousArtifactHtml())) {
      requestBody.put("previousDashboardHtml", request.previousArtifactHtml());
    }
    return requestBody;
  }

  /**
   * {@link HistoryMessage#sender()} carries the persisted {@code Sender} enum name; agent-service's
   * {@code /chat} endpoint expects the OpenAI-style role vocabulary ({@code user}/{@code
   * assistant}). The mapping is exhaustive (throws on an unrecognized sender) rather than
   * defaulting to {@code "user"}, to avoid silently misrepresenting conversation history.
   */
  private static Map<String, String> toHistoryEntry(HistoryMessage message) {
    String role;
    if ("AI".equals(message.sender())) {
      role = "assistant";
    } else if ("USER".equals(message.sender())) {
      role = "user";
    } else {
      throw new IllegalStateException(
          "Unrecognized HistoryMessage sender: " + message.sender() + " (expected USER or AI)");
    }
    return Map.of("role", role, "text", message.text());
  }

  /**
   * Resolves the source path agent-service will read from. When {@code erd.storage.type=s3}, the
   * storageKey is handed back verbatim — deepagent downloads the object from S3 itself, so no local
   * path applies. Otherwise (local disk), resolves to {@code sourceRoot/storageKey}, a path on the
   * shared PVC both backend and agent-service mount.
   *
   * <p>Package-private so tests can exercise it directly without going through the full {@link
   * #generate} flow.
   */
  String resolveSourcePath(String storageKey) {
    if ("s3".equals(storageProperties.type())) {
      return storageKey;
    }
    return analysisProperties.sourceRoot() + "/" + storageKey;
  }

  /**
   * Translates one raw agent-service SSE data payload into an {@link AgentEvent}. agent-service
   * never sets {@link StepEvent#status()}, so it is normalized here to {@code RUNNING} when null.
   */
  static AgentEvent toEvent(String payload, ObjectMapper objectMapper) {
    try {
      AgentEvent event = objectMapper.readValue(payload, AgentEvent.class);
      if (event instanceof StepEvent stepEvent) {
        StepStatus status = stepEvent.status() == null ? StepStatus.RUNNING : stepEvent.status();
        return new StepEvent(
            stepEvent.stepKey(), stepEvent.title(), stepEvent.description(), status);
      }
      return event;
    } catch (Exception exception) {
      // Log the failure class only, never the exception message: Jackson embeds a snippet of the
      // offending payload, which may carry user data (CLAUDE.md: NEVER log 使用者資料內容).
      log.debug(
          "unparseable analysis event payload (length={}, failure={})",
          payload.length(),
          exception.getClass().getSimpleName());
      return new ErrorEvent("ANALYSIS_EVENT_PARSE", "unparseable analysis event payload");
    }
  }

  /**
   * Routes one raw SSE payload to {@link #toEvent}, except {@code DASHBOARD_HTML} and {@code
   * QUESTION} payloads, which are captured out-of-band (into the given {@link AtomicReference}s)
   * and emit nothing downstream — see {@link #generate}'s field comments for why.
   */
  private Flux<AgentEvent> toEventOrEmpty(
      String payload,
      AtomicReference<String> capturedDashboardHtml,
      AtomicReference<List<ClarifyingQuestion>> capturedQuestions) {
    JsonNode root = tryReadTree(payload, objectMapper);
    if (root != null && isDashboardHtmlNode(root)) {
      String html = extractDashboardHtml(root);
      if (html != null) {
        capturedDashboardHtml.set(html);
      }
      return Flux.empty();
    }
    AgentEvent event = toEvent(payload, objectMapper);
    if (event instanceof QuestionEvent questionEvent) {
      capturedQuestions.set(questionEvent.questions());
      return Flux.empty();
    }
    return Flux.just(event);
  }

  /**
   * Best-effort parse used solely to sniff the {@code type} discriminator ahead of {@link
   * #toEvent}. Returns {@code null} on any parse failure so the caller falls through to {@link
   * #toEvent}, which performs its own parse and its own malformed-payload logging/{@link
   * ErrorEvent} conversion — this method must not duplicate that logging.
   */
  private static JsonNode tryReadTree(String payload, ObjectMapper objectMapper) {
    try {
      return objectMapper.readTree(payload);
    } catch (Exception exception) {
      return null;
    }
  }

  private static boolean isDashboardHtmlNode(JsonNode root) {
    JsonNode typeNode = root.get("type");
    return typeNode != null && typeNode.isTextual() && "DASHBOARD_HTML".equals(typeNode.asText());
  }

  /**
   * Pulls the {@code html} field out of a tree already identified as {@code DASHBOARD_HTML} by
   * {@link #isDashboardHtmlNode}. NEVER log the extracted html itself (CLAUDE.md: NEVER log
   * 使用者資料內容/完整 HTML).
   */
  private static String extractDashboardHtml(JsonNode root) {
    JsonNode htmlNode = root.get("html");
    return htmlNode != null && htmlNode.isTextual() ? htmlNode.asText() : null;
  }
}
