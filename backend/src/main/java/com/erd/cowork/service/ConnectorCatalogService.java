package com.erd.cowork.service;

import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.domain.ConnectorCatalogEntry;
import com.erd.cowork.exception.ConflictException;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.repo.ConnectorCatalogRepository;
import com.erd.cowork.web.dto.ConnectorInfoDto;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.util.CollectionUtils;

/**
 * Mongo-backed API connector directory (MCP datasource catalog). Replaces the earlier proxy over
 * deepagent's {@code GET /connectors} — the catalog is now Java-owned; deepagent receives the full
 * MCP server spec (id/name/url) per connector on every {@code /chat} call instead of maintaining
 * its own static registry.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@LogAnnotation
public class ConnectorCatalogService {

  private final ConnectorCatalogRepository connectorCatalogRepository;

  /** Backs {@code GET /api/connectors} (frontend connector picker). Empty catalog → empty list. */
  public List<ConnectorInfoDto> listCatalog() {
    return connectorCatalogRepository.findAll().stream()
        .map(entry -> new ConnectorInfoDto(entry.getConnectorId(), entry.getDisplayName()))
        .toList();
  }

  /**
   * Resolves connector ids (order-preserved, deduped) into wire specs {@code {id, name, url}} for
   * the deepagent {@code /chat} request body. {@code null}/empty input (undecided session or files
   * mode) returns an empty list.
   *
   * @throws NotFoundException naming the first missing id, if any requested id has no matching
   *     catalog entry — covers the case where a session locked its selection and the entry was
   *     later removed from the catalog ("資料源 X 已下架").
   */
  public List<ConnectorSpec> resolveSpecs(List<String> connectorIds) {
    if (CollectionUtils.isEmpty(connectorIds)) {
      return List.of();
    }
    List<String> dedupedIds = new ArrayList<>(new LinkedHashSet<>(connectorIds));
    Map<String, ConnectorCatalogEntry> entryByConnectorId =
        connectorCatalogRepository.findByConnectorIdIn(dedupedIds).stream()
            .collect(Collectors.toMap(ConnectorCatalogEntry::getConnectorId, entry -> entry));
    List<ConnectorSpec> specs = new ArrayList<>(dedupedIds.size());
    for (String connectorId : dedupedIds) {
      ConnectorCatalogEntry entry = entryByConnectorId.get(connectorId);
      if (entry == null) {
        throw new NotFoundException("資料源 " + connectorId + " 已下架");
      }
      specs.add(
          new ConnectorSpec(entry.getConnectorId(), entry.getDisplayName(), entry.getMcpUrl()));
    }
    return specs;
  }

  /**
   * Validates every requested id exists in the catalog — called at first-message lock time ({@link
   * com.erd.cowork.agent.AgentOrchestrator#stream}) so an unknown id is rejected up front, before
   * the selection is persisted, rather than surfacing only later at wire-build time via {@link
   * #resolveSpecs}. {@code null}/empty input is a no-op (undecided session stays undecided).
   *
   * @throws ConflictException naming every unknown id, alongside the currently available ids.
   */
  public void validateKnownIds(List<String> connectorIds) {
    if (CollectionUtils.isEmpty(connectorIds)) {
      return;
    }
    Set<String> knownIds =
        connectorCatalogRepository.findAll().stream()
            .map(ConnectorCatalogEntry::getConnectorId)
            .collect(Collectors.toSet());
    List<String> unknownIds = connectorIds.stream().filter(id -> !knownIds.contains(id)).toList();
    if (!unknownIds.isEmpty()) {
      throw new ConflictException(
          "未知資料源: " + String.join(", ", unknownIds) + "；可用資料源: " + String.join(", ", knownIds));
    }
  }
}
