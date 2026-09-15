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
 * View-time bridge to deepagent's POST /tool-call: a 2xx body is returned as the raw string so data
 * reaches the page byte-for-byte; every other outcome becomes a 200-shaped error envelope.
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
      ConnectorSpec connector,
      String tool,
      Map<String, Object> args,
      String ssoToken,
      String ssoUrl) {
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
