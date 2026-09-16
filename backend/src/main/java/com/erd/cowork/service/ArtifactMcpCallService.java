package com.erd.cowork.service;

import com.erd.cowork.agent.mcp.McpCallErrorWriter;
import com.erd.cowork.agent.mcp.McpErrorCode;
import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.agent.provider.analysis.AnalysisToolCallClient;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.repo.ArtifactRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;
import org.springframework.util.CollectionUtils;

/**
 * View-time mcp() proxy: ownership, session allow-list and catalog lookup happen here; the MCP call
 * itself and its data pass through AnalysisToolCallClient untouched.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@LogAnnotation
public class ArtifactMcpCallService {

  private static final String UNPARSEABLE_CODE = "UNPARSEABLE";

  private final ArtifactRepository artifacts;
  private final SessionGuard sessionGuard;
  private final ConnectorCatalogService connectorCatalogService;
  private final ObjectProvider<AnalysisToolCallClient> toolCallClientProvider;
  private final McpCallErrorWriter errorWriter;
  private final ObjectMapper objectMapper;

  /** Returns the JSON body for the page; throws NotFoundException only for ownership failures. */
  public String call(String artifactId, String connectorId, String tool, Map<String, Object> args) {
    long startedAtNanos = System.nanoTime();
    String body = resolveAndCall(artifactId, connectorId, tool, args);
    String errorCode = readErrorCode(body);
    if (UNPARSEABLE_CODE.equals(errorCode)) {
      // The page contracts for {data} or {error}; anything else becomes a retryable failure.
      body =
          errorWriter.write(
              McpErrorCode.RETRYABLE, "agent service returned a non-JSON body; retry");
      errorCode = McpErrorCode.RETRYABLE.name();
    }
    log.info(
        "mcp-call artifact={} connector={} tool={} argKeys={} ms={} ok={} code={}",
        artifactId,
        connectorId,
        tool,
        args.keySet(),
        (System.nanoTime() - startedAtNanos) / 1_000_000,
        errorCode == null,
        errorCode);
    return body;
  }

  private String resolveAndCall(
      String artifactId, String connectorId, String tool, Map<String, Object> args) {
    Artifact artifact =
        artifacts
            .findById(artifactId)
            .orElseThrow(() -> new NotFoundException("Artifact not found: " + artifactId));
    ChatSession session = sessionGuard.loadOwned(artifact.getSessionId());

    List<String> allowedConnectorIds = session.getSelectedConnectors();
    if (CollectionUtils.isEmpty(allowedConnectorIds)
        || !allowedConnectorIds.contains(connectorId)) {
      String allowed =
          CollectionUtils.isEmpty(allowedConnectorIds)
              ? "(none)"
              : String.join(", ", allowedConnectorIds);
      return errorWriter.write(
          McpErrorCode.INVALID_CALL,
          "connector '" + connectorId + "' is not enabled for this session; allowed: " + allowed);
    }

    ConnectorSpec connector;
    try {
      connector = connectorCatalogService.resolveSpecs(List.of(connectorId)).get(0);
    } catch (NotFoundException catalogMiss) {
      return errorWriter.write(McpErrorCode.CONNECTOR_UNAVAILABLE, catalogMiss.getMessage());
    }

    AnalysisToolCallClient toolCallClient = toolCallClientProvider.getIfAvailable();
    if (toolCallClient == null) {
      return errorWriter.write(
          McpErrorCode.CONNECTOR_UNAVAILABLE,
          "connector mode is not enabled on this server; ask the administrator");
    }

    // SSO values are read on the request thread and handed over as plain arguments.
    String body =
        toolCallClient
            .call(
                connector, tool, args, CoworkContextHolder.ssoToken(), CoworkContextHolder.ssoUrl())
            .block();
    if (body == null) {
      return errorWriter.write(McpErrorCode.RETRYABLE, "agent service returned no body; retry");
    }
    return body;
  }

  /** Read-only peek; returns UNPARSEABLE_CODE when the body is not JSON at all. */
  private String readErrorCode(String body) {
    try {
      return objectMapper.readTree(body).path("error").path("code").asText(null);
    } catch (JsonProcessingException unparseable) {
      return UNPARSEABLE_CODE;
    }
  }
}
