package com.erd.cowork.agent.provider.openai;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.AnswerEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.ThinkingEvent;
import com.erd.cowork.agent.extraction.ResponseExtractionHelper;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.provider.RepairResult;
import com.erd.cowork.config.AgentProperties;
import com.erd.cowork.logging.LogAnnotation;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Consumer;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.Assert;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import reactor.core.publisher.Flux;

/**
 * Provider implementation for any OpenAI-compatible {@code /v1/chat/completions} endpoint with SSE
 * streaming. This is the default provider ({@code erd.agent.provider=openai-compatible}).
 *
 * <p>Compatible endpoints include the company internal LLM gateway, OpenRouter, and any other
 * service that exposes the OpenAI Chat Completions API with {@code stream: true} SSE.
 *
 * <p>Supports two authentication modes via {@code erd.agent.open-ai-compatible.auth-mode}:
 *
 * <ul>
 *   <li>{@code bearer} (default) — standard {@code Authorization: Bearer <apiKey>} header
 *   <li>{@code token-exchange} — j1→j2 token exchange via {@link TokenExchangeClient}; on 401 the
 *       cached token is invalidated and retried once
 * </ul>
 */
@Slf4j
@Component
@ConditionalOnProperty(
    prefix = "erd.agent",
    name = "provider",
    havingValue = "openai-compatible",
    matchIfMissing = true)
@LogAnnotation
public class OpenAICompatibleProvider implements DashboardAgentProvider {

  private final AgentProperties props;
  private final PromptAssembler promptBuilder;
  private final WebClient webClient;
  private final ObjectMapper objectMapper;

  /**
   * {@link ObjectProvider} because {@link TokenExchangeClient} only exists in token-exchange auth
   * mode ({@code @ConditionalOnProperty}); bearer-mode deployments have no such bean, so eager
   * injection would fail at startup.
   */
  private final ObjectProvider<TokenExchangeClient> tokenExchangeClientProvider;

  private final GenerationRepairGuard generationRepairGuard;

  public OpenAICompatibleProvider(
      AgentProperties props,
      PromptAssembler promptBuilder,
      WebClient.Builder webClientBuilder,
      ObjectMapper objectMapper,
      ObjectProvider<TokenExchangeClient> tokenExchangeClientProvider,
      GenerationRepairGuard generationRepairGuard) {
    Assert.notNull(
        props.openAiCompatible(),
        "erd.agent.openai-compatible config is required when provider=openai-compatible");
    Assert.notNull(tokenExchangeClientProvider, "tokenExchangeClientProvider must not be null");
    Assert.notNull(generationRepairGuard, "generationRepairGuard must not be null");
    this.props = props;
    this.promptBuilder = promptBuilder;
    this.webClient = webClientBuilder.baseUrl(props.openAiCompatible().baseUrl()).build();
    this.objectMapper = objectMapper;
    this.tokenExchangeClientProvider = tokenExchangeClientProvider;
    this.generationRepairGuard = generationRepairGuard;
  }

  @Override
  public ProviderResult generate(AgentRequest request) {
    String systemPrompt = promptBuilder.systemPrompt();
    String userPrompt = promptBuilder.userPrompt(request, props.openAiCompatible().contextWindow());
    log.info(
        "openai-compatible generate model={} promptChars={}",
        props.openAiCompatible().model(),
        userPrompt.length());
    String apiKey = props.openAiCompatible().apiKey();

    Map<String, Object> body =
        Map.of(
            "model", props.openAiCompatible().model(),
            "stream", true,
            "messages",
                List.of(
                    Map.of("role", "system", "content", systemPrompt),
                    Map.of("role", "user", "content", userPrompt)));

    String chatPath =
        StringUtils.hasText(props.openAiCompatible().chatCompletionsPath())
            ? props.openAiCompatible().chatCompletionsPath()
            : "/v1/chat/completions";

    String effectiveAuthMode =
        StringUtils.hasText(props.openAiCompatible().authMode())
            ? props.openAiCompatible().authMode()
            : "bearer";

    // Publish the raw SSE line flux so both the content transformer and the thinking extractor
    // can subscribe to the same upstream HTTP stream without making two requests.
    Flux<String> sseLines;
    if ("token-exchange".equals(effectiveAuthMode)) {
      TokenExchangeClient tokenClient = tokenExchangeClientProvider.getObject();
      String headerName = props.openAiCompatible().tokenExchange().headerName();
      sseLines =
          tokenClient
              .getToken()
              .flatMapMany(
                  token -> rawSseLines(chatPath, body, headers -> headers.add(headerName, token)))
              .onErrorResume(
                  exception ->
                      exception instanceof WebClientResponseException wcre
                          && wcre.getStatusCode().value() == 401,
                  exception -> {
                    log.warn("token-exchange 401, invalidating and retrying once");
                    tokenClient.invalidate();
                    return tokenClient
                        .getToken()
                        .flatMapMany(
                            newToken ->
                                rawSseLines(
                                    chatPath, body, headers -> headers.add(headerName, newToken)));
                  })
              .publish()
              .refCount(2);
    } else {
      sseLines =
          rawSseLines(
                  chatPath,
                  body,
                  headers -> {
                    if (StringUtils.hasText(apiKey)) {
                      headers.setBearerAuth(apiKey);
                    }
                  })
              .publish()
              .refCount(2);
    }

    return buildEvents(sseLines);
  }

  @Override
  public RepairResult harden(String sessionId, AgentRequest request, AgentOutcome outcome) {
    return generationRepairGuard.harden(sessionId, request, outcome, this::generate);
  }

  /**
   * Issues an HTTP POST to the LLM endpoint with the given headers setter and returns a raw SSE
   * line flux (with {@code [DONE]} lines filtered out).
   */
  private Flux<String> rawSseLines(
      String chatPath, Map<String, Object> body, Consumer<HttpHeaders> headersSetter) {
    return webClient
        .post()
        .uri(chatPath)
        .accept(MediaType.TEXT_EVENT_STREAM)
        .headers(headersSetter)
        .bodyValue(body)
        .retrieve()
        .bodyToFlux(String.class)
        .filter(line -> !"[DONE]".equals(line.trim()));
  }

  /**
   * Wires the published SSE-line flux into the content-token and thinking branches, merges them,
   * and returns a {@link ProviderResult}.
   *
   * @param publishedSseLines a {@link Flux#publish()}-ed (refCount=2) SSE-line flux
   */
  private ProviderResult buildEvents(Flux<String> publishedSseLines) {
    // Content token branch — feeds the HTML-extracting transformer.
    // concatMap (vs. flatMap) preserves SSE line order, preventing token interleaving.
    Flux<String> tokenFlux = publishedSseLines.concatMap(this::parseContentToken);

    // Thinking/reasoning branch — emits ThinkingEvent per reasoning delta. concatMap preserves
    // order; onErrorResume silences errors here so the content branch surfaces them instead,
    // avoiding Reactor's onErrorDropped noise from a second terminal error downstream.
    Flux<AgentEvent> thinkingFlux =
        publishedSseLines
            .concatMap(this::parseThinkingEvent)
            .onErrorResume(exception -> Flux.empty());

    ResponseExtractionHelper transformer = new ResponseExtractionHelper();
    Flux<AgentEvent> events =
        Flux.merge(tokenFlux.transform(transformer::apply), thinkingFlux)
            .concatWith(
                Flux.defer(() -> Flux.just(new AnswerEvent(transformer.result().answerText()))))
            .onErrorResume(
                exception -> {
                  log.warn("openai-compatible provider error", exception);
                  return Flux.just(
                      new ErrorEvent(
                          "PROVIDER_ERROR",
                          Objects.requireNonNullElse(
                              exception.getMessage(), exception.getClass().getSimpleName())));
                });

    return new ProviderResult(events, transformer::result);
  }

  private Flux<String> parseContentToken(String line) {
    try {
      JsonNode node = objectMapper.readTree(line);
      JsonNode content = node.path("choices").path(0).path("delta").path("content");
      if (content.isMissingNode() || content.isNull()) {
        return Flux.empty();
      }
      return Flux.just(content.asText());
    } catch (Exception exception) {
      // NEVER log line 本身——SSE delta content 含使用者資料片段。只記長度＋例外類別。
      log.debug(
          "Failed to parse SSE line (len={}): {}",
          line.length(),
          exception.getClass().getSimpleName());
      return Flux.empty();
    }
  }

  /**
   * Parses the {@code choices[0].delta.reasoning} field from an SSE chunk.
   *
   * <p>If the field is absent or null the returned flux is empty. When the same chunk carries both
   * {@code content} and {@code reasoning}, this method emits a {@link ThinkingEvent} while {@link
   * #parseContentToken} emits the content token — both events reach the merged downstream.
   */
  private Flux<AgentEvent> parseThinkingEvent(String line) {
    try {
      JsonNode node = objectMapper.readTree(line);
      JsonNode reasoning = node.path("choices").path(0).path("delta").path("reasoning");
      if (reasoning.isMissingNode() || reasoning.isNull()) {
        return Flux.empty();
      }
      String text = reasoning.asText();
      if (text.isEmpty()) {
        return Flux.empty();
      }
      return Flux.just(new ThinkingEvent(text));
    } catch (Exception exception) {
      // NEVER log line 本身——SSE delta reasoning 含使用者資料片段。只記長度＋例外類別。
      log.debug(
          "Failed to parse SSE reasoning line (len={}): {}",
          line.length(),
          exception.getClass().getSimpleName());
      return Flux.empty();
    }
  }
}
