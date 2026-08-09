package com.erd.cowork.agent.provider.analysis;

import com.erd.cowork.agent.repair.BrowserJsError;
import com.erd.cowork.agent.repair.BrowserRepairOutcome;
import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.exception.AnalysisBrowserRepairFailedException;
import com.erd.cowork.logging.LogAnnotation;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

/**
 * Bridges browser-error artifact repair to deepagent-service's {@code POST /repair} endpoint — the
 * {@code langgraph-analysis} provider's counterpart to {@link
 * com.erd.cowork.agent.provider.DashboardAgentProvider}-based repair.
 *
 * <p>{@code deepagent-service}'s {@code /repair} endpoint performs a single model call (no agent
 * loop, no tools) plus its own deterministic guard re-check, and reports the outcome via HTTP
 * status rather than an event stream:
 *
 * <ul>
 *   <li>{@code 200} — repair succeeded and passed the guard; response body carries the fixed HTML.
 *   <li>{@code 422} — the guard rejected the model's fix; treated identically to the dashboard
 *       path's "provider returned no usable HTML" outcome (repair attempted, not successful).
 *   <li>anything else (notably {@code 502}, model-call failure/timeout) — propagated as an error on
 *       the returned {@link Mono}, mirroring the dashboard path's un-special-cased behavior when
 *       the provider transport itself fails.
 * </ul>
 */
@Slf4j
@Component
@ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "langgraph-analysis")
@LogAnnotation
public class AnalysisBrowserRepairClient {

  private final AnalysisAgentProperties analysisProperties;
  private final WebClient webClient;

  public AnalysisBrowserRepairClient(
      AnalysisAgentProperties analysisProperties, WebClient.Builder webClientBuilder) {
    this.analysisProperties = analysisProperties;
    // Repair requests carry a full dashboard HTML document both ways (request body and 200
    // response body) — same oversized-payload concern as LangGraphAnalysisProvider's SSE
    // DASHBOARD_HTML event, so the buffer cap is raised the same way.
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

  /**
   * Requests a browser-error repair from deepagent-service.
   *
   * @param sessionId session identifier (for logging; also forwarded so the service can resolve the
   *     shared PVC workspace)
   * @param userId owning user id (forwarded for the same workspace-resolution reason)
   * @param html the current (already-injected) artifact HTML that produced the browser errors
   * @param errors runtime errors reported by the browser iframe; message and line/column are all
   *     forwarded — deepagent-service quotes the offending source line into the repair prompt
   * @return a Mono emitting the repair outcome on {@code 200}/{@code 422}, or erroring for any
   *     other response status (including {@code 502})
   */
  public Mono<BrowserRepairOutcome> repair(
      String sessionId, String userId, String html, List<BrowserJsError> errors) {
    log.info("analysis browser repair request session={} errorCount={}", sessionId, errors.size());

    Map<String, Object> requestBody = new HashMap<>();
    requestBody.put("sessionId", sessionId);
    requestBody.put("userId", userId);
    requestBody.put("html", html);
    requestBody.put(
        "errors", errors.stream()
            .map(
                error ->
                    Map.of(
                        "message", error.message(),
                        "line", error.line(),
                        "col", error.col(),
                        "sourceLine", error.sourceLine()))
            .toList());

    return webClient
        .post()
        .uri("/repair")
        .bodyValue(requestBody)
        .exchangeToMono(
            response -> {
              if (response.statusCode().is2xxSuccessful()) {
                return response
                    .bodyToMono(RepairSuccessResponse.class)
                    .map(
                        body -> {
                          log.info("analysis browser repair passed session={}", sessionId);
                          return new BrowserRepairOutcome(body.html(), true);
                        });
              }
              if (response.statusCode().equals(HttpStatus.UNPROCESSABLE_ENTITY)) {
                log.info("analysis browser repair guard rejected session={}", sessionId);
                return Mono.just(new BrowserRepairOutcome(html, false));
              }
              return Mono.error(
                  new AnalysisBrowserRepairFailedException(
                      "deepagent-service /repair failed for session "
                          + sessionId
                          + " with status "
                          + response.statusCode()));
            })
        .timeout(Duration.ofSeconds(analysisProperties.requestTimeoutSeconds()));
  }

  private record RepairSuccessResponse(String html) {}
}
