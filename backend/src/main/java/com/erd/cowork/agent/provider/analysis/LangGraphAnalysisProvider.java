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
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
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
import org.springframework.stereotype.Component;
import org.springframework.util.Assert;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

/**
 * Bridges the {@code agent-service} LangGraph analysis endpoint ({@code POST /chat}, SSE) into the
 * backend's {@link AgentEvent} stream. Provider id {@code langgraph-analysis} (data-analyst agent —
 * no HTML artifact).
 */
@Slf4j
@Component
@ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "langgraph-analysis")
public class LangGraphAnalysisProvider implements AgentProvider {

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
    // Spring's default 256KB per-SSE-event buffer is far too small for a DASHBOARD_HTML event
    // (full dashboard HTML + spec JSON in one line, easily hitting DataBufferLimitException) —
    // raise the cap via analysisProperties.maxInMemorySizeMb() (default 64MB).
    this.webClient =
        webClientBuilder
            .baseUrl(analysisProperties.baseUrl())
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
            // Timeout guardrail: bounds a runaway query's wall time (memory_limit/threads don't).
            // Placed before onErrorResume so a stall flows through the ErrorEvent path.
            .timeout(Duration.ofSeconds(analysisProperties.requestTimeoutSeconds()))
            .onErrorResume(
                error -> {
                  log.warn(
                      "langgraph-analysis stream failed session={}", request.sessionId(), error);
                  String errorCode =
                      error instanceof TimeoutException
                          ? "ANALYSIS_TIMEOUT"
                          : "ANALYSIS_STREAM_FAILURE";
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
