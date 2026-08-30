package com.erd.cowork.service;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.web.dto.ConnectorInfoDto;
import java.time.Duration;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * Proxies deepagent's connector directory ({@code GET /connectors}, spec §5b) for the frontend's
 * connector picker. Reuses the same {@link AnalysisAgentProperties} base URL and bearer token as
 * {@link com.erd.cowork.agent.provider.analysis.LangGraphAnalysisProvider} — both talk to the same
 * deepagent instance.
 */
@Slf4j
@Service
@LogAnnotation
public class ConnectorCatalogService {

  private static final String CONNECTORS_PATH = "/connectors";
  private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(10);

  private final WebClient webClient;

  public ConnectorCatalogService(
      AnalysisAgentProperties analysisProperties, WebClient.Builder webClientBuilder) {
    this.webClient =
        webClientBuilder
            .baseUrl(analysisProperties.baseUrl())
            .defaultHeaders(
                headers -> {
                  if (StringUtils.hasText(analysisProperties.bearerToken())) {
                    headers.setBearerAuth(analysisProperties.bearerToken());
                  }
                })
            .build();
  }

  /**
   * Graceful-empty (spec §5b): deepagent unreachable, a non-200 response, a request timeout, or a
   * malformed body all surface as an empty list (with a warn log) rather than propagating an error
   * — the frontend hides the connector picker entirely when the catalog is empty, so there is no
   * user-visible distinction between "no connectors configured" and "catalog temporarily
   * unavailable".
   */
  public List<ConnectorInfoDto> list() {
    try {
      List<ConnectorInfoDto> connectors =
          webClient
              .get()
              .uri(CONNECTORS_PATH)
              .retrieve()
              .bodyToFlux(ConnectorInfoDto.class)
              .collectList()
              .block(REQUEST_TIMEOUT);
      return connectors == null ? List.of() : connectors;
    } catch (Exception exception) {
      log.warn(
          "connector catalog unavailable, returning empty list: {}",
          exception.getClass().getSimpleName());
      return List.of();
    }
  }
}
