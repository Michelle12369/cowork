package com.erd.cowork.agent.provider.analysis;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.exception.AnalysisReplayFailedException;
import com.erd.cowork.logging.LogAnnotation;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

/**
 * Bridges owner-triggered artifact "refresh" to deepagent-service's {@code POST /replay} endpoint —
 * a zero-LLM, deterministic re-fetch-and-inject of a stored recipe (spec §7). No agent loop, no
 * model call, no persistence on the deepagent side.
 *
 * <p>{@code /replay} always answers {@code 200}; the response body itself distinguishes success
 * ({@code html}) from a request-shaped failure ({@code error.code}/{@code error.message} — stale
 * recipe, removed source, schema drift). Only a genuine transport/outage failure (non-2xx, timeout)
 * surfaces as an error on the returned {@link Mono}, mirroring {@link
 * AnalysisBrowserRepairClient}'s un-special-cased behaviour for hard provider/transport failures.
 */
@Slf4j
@Component
@ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "langgraph-analysis")
@LogAnnotation
public class AnalysisReplayClient {

  private final AnalysisAgentProperties analysisProperties;
  private final ObjectMapper objectMapper;
  private final WebClient webClient;

  public AnalysisReplayClient(
      AnalysisAgentProperties analysisProperties,
      ObjectMapper objectMapper,
      WebClient.Builder webClientBuilder) {
    this.analysisProperties = analysisProperties;
    this.objectMapper = objectMapper;
    // Replay requests carry a full dashboard HTML document both ways (request body and 200
    // response body) — same oversized-payload concern as AnalysisBrowserRepairClient, so the
    // buffer cap is raised the same way.
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
   * Requests a deterministic recipe replay from deepagent-service.
   *
   * <p>Never logs recipe or HTML content (log hygiene) — only the artifact id and the outcome code.
   *
   * @param artifactId artifact UUID (for logging only)
   * @param recipeJson the artifact's stored recipe, as raw JSON text ({@link
   *     com.erd.cowork.domain.Artifact#getRecipeJson()})
   * @param html the base HTML to re-inject fresh query results into (pre-assembly/raw HTML, same
   *     base {@code /repair} uses)
   * @return a Mono emitting the replay outcome on {@code 200} (success or request-shaped failure
   *     alike), or erroring for any other response status or a malformed stored recipe
   */
  public Mono<AnalysisReplayOutcome> replay(String artifactId, String recipeJson, String html) {
    log.info("analysis replay request artifact={}", artifactId);

    JsonNode recipe;
    try {
      recipe = objectMapper.readTree(recipeJson);
    } catch (JsonProcessingException jsonException) {
      return Mono.error(
          new AnalysisReplayFailedException(
              "Stored recipe JSON is malformed for artifact " + artifactId, jsonException));
    }

    Map<String, Object> requestBody = new HashMap<>();
    requestBody.put("recipe", recipe);
    requestBody.put("html", html);

    return webClient
        .post()
        .uri("/replay")
        .bodyValue(requestBody)
        .exchangeToMono(
            response -> {
              if (response.statusCode().is2xxSuccessful()) {
                return response
                    .bodyToMono(ReplayWireResponse.class)
                    .map(
                        body -> {
                          if (body.error() != null) {
                            log.info(
                                "analysis replay artifact={} outcome={}",
                                artifactId,
                                body.error().code());
                            return AnalysisReplayOutcome.failure(
                                body.error().code(), body.error().message());
                          }
                          log.info("analysis replay artifact={} outcome=OK", artifactId);
                          return AnalysisReplayOutcome.success(body.html());
                        });
              }
              return Mono.error(
                  new AnalysisReplayFailedException(
                      "deepagent-service /replay failed for artifact "
                          + artifactId
                          + " with status "
                          + response.statusCode()));
            })
        .timeout(Duration.ofSeconds(analysisProperties.requestTimeoutSeconds()));
  }

  private record ReplayWireResponse(String html, ReplayWireError error) {}

  private record ReplayWireError(String code, String message) {}
}
