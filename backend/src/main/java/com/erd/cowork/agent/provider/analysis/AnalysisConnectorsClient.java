package com.erd.cowork.agent.provider.analysis;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.web.dto.ConnectorGroupDto;
import java.time.Duration;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

/**
 * Proxies deepagent-service's {@code GET /connectors} — the connector-group catalog backing the
 * frontend's "資料源" selection modal (§11 connector selection). Config authority lives in
 * deepagent-service; this client does not parse or cache connector config itself.
 *
 * <p>Only present when {@code erd.agent.provider=langgraph-analysis} — {@link
 * com.erd.cowork.web.ConnectorController} injects it as {@link java.util.Optional} for that reason,
 * mirroring {@link AnalysisBrowserRepairClient}'s wiring in {@code ArtifactRepairer}.
 */
@Slf4j
@Component
@ConditionalOnProperty(prefix = "erd.agent", name = "provider", havingValue = "langgraph-analysis")
@LogAnnotation
public class AnalysisConnectorsClient {

  private final AnalysisAgentProperties analysisProperties;
  private final WebClient webClient;

  public AnalysisConnectorsClient(
      AnalysisAgentProperties analysisProperties, WebClient.Builder webClientBuilder) {
    this.analysisProperties = analysisProperties;
    this.webClient = webClientBuilder.baseUrl(analysisProperties.baseUrl()).build();
  }

  /**
   * Fetches the connector group catalog. Never errors: any transport failure or timeout resolves to
   * an empty list, so the selection modal degrades to "no sources available" rather than the caller
   * having to special-case a failed proxy call for a purely informational list endpoint.
   */
  public Mono<List<ConnectorGroupDto>> fetchGroups() {
    return webClient
        .get()
        .uri("/connectors")
        .retrieve()
        .bodyToFlux(ConnectorGroupDto.class)
        .collectList()
        .timeout(Duration.ofSeconds(analysisProperties.requestTimeoutSeconds()))
        .onErrorResume(
            error -> {
              log.warn("failed to fetch connector groups from deepagent-service", error);
              return Mono.just(List.of());
            });
  }
}
