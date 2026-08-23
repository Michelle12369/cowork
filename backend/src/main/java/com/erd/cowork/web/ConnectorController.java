package com.erd.cowork.web;

import com.erd.cowork.agent.provider.analysis.AnalysisConnectorsClient;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.web.dto.ConnectorGroupDto;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Proxies deepagent-service's connector-group catalog for the upload-area "資料源" selection modal
 * (§11 connector selection). Graceful-empty on any failure — deepagent unreachable, the connector
 * feature disabled server-side, or the active provider not being {@code langgraph-analysis} — this
 * is a purely informational list endpoint feeding an optional UI affordance, so an empty list is a
 * better degrade than a 502 the frontend would have to special-case.
 */
@Slf4j
@RestController
@RequestMapping("/api/connectors")
@RequiredArgsConstructor
@Validated
@Tag(name = "Connectors", description = "Connector group catalog for data-source selection")
@LogAnnotation
public class ConnectorController {

  private final Optional<AnalysisConnectorsClient> connectorsClient;

  @GetMapping
  @Operation(summary = "List available connector groups")
  @ApiResponse(
      responseCode = "200",
      description = "Connector groups (empty when unavailable or the feature is disabled)")
  public List<ConnectorGroupDto> listConnectors() {
    if (connectorsClient.isEmpty()) {
      log.info("GET connectors — no analysis connectors client bound, returning empty list");
      return List.of();
    }
    List<ConnectorGroupDto> groups =
        Objects.requireNonNullElse(connectorsClient.get().fetchGroups().block(), List.of());
    log.info("GET connectors groupCount={}", groups.size());
    return groups;
  }
}
