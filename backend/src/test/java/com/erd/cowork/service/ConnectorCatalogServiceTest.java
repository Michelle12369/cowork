package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.model.ConnectorSpec;
import com.erd.cowork.domain.ConnectorCatalogEntry;
import com.erd.cowork.exception.ConflictException;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.repo.ConnectorCatalogRepository;
import com.erd.cowork.web.dto.ConnectorInfoDto;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

/**
 * Mongo-backed connector catalog: {@link ConnectorCatalogService#listCatalog()} backs the frontend
 * picker, {@link ConnectorCatalogService#resolveSpecs} resolves the wire specs sent to deepagent,
 * and {@link ConnectorCatalogService#validateKnownIds} gates first-message locking.
 */
@ExtendWith(MockitoExtension.class)
class ConnectorCatalogServiceTest {

  @Mock private ConnectorCatalogRepository connectorCatalogRepository;

  private ConnectorCatalogService service;

  @BeforeEach
  void setUp() {
    service = new ConnectorCatalogService(connectorCatalogRepository);
  }

  private static ConnectorCatalogEntry entry(String connectorId, String displayName, String url) {
    return entry(connectorId, displayName, url, null);
  }

  private static ConnectorCatalogEntry entry(
      String connectorId, String displayName, String url, String bearerTokenKey) {
    ConnectorCatalogEntry entry = new ConnectorCatalogEntry();
    entry.setConnectorId(connectorId);
    entry.setDisplayName(displayName);
    entry.setMcpUrl(url);
    entry.setBearerTokenKey(bearerTokenKey);
    return entry;
  }

  // ── listCatalog ───────────────────────────────────────────────────────────

  @Test
  void listCatalog_entriesPresent_mapsToConnectorInfoDto() {
    when(connectorCatalogRepository.findAll())
        .thenReturn(
            List.of(
                entry("salesforce", "Salesforce CRM", "https://mcp.example/sf"),
                entry("hubspot", "HubSpot", "https://mcp.example/hs")));

    List<ConnectorInfoDto> result = service.listCatalog();

    assertThat(result)
        .containsExactly(
            new ConnectorInfoDto("salesforce", "Salesforce CRM"),
            new ConnectorInfoDto("hubspot", "HubSpot"));
  }

  @Test
  void listCatalog_emptyCatalog_returnsEmptyList() {
    when(connectorCatalogRepository.findAll()).thenReturn(List.of());

    assertThat(service.listCatalog()).isEmpty();
  }

  // ── resolveSpecs ──────────────────────────────────────────────────────────

  @Test
  void resolveSpecs_nullIds_returnsEmptyList() {
    assertThat(service.resolveSpecs(null)).isEmpty();
  }

  @Test
  void resolveSpecs_emptyIds_returnsEmptyList() {
    assertThat(service.resolveSpecs(List.of())).isEmpty();
  }

  @Test
  void resolveSpecs_knownIds_orderPreservedAcrossRepositoryReturnOrder() {
    // Repository returns entries in a different order than requested — resolveSpecs must still
    // hand back specs in the requested order, not the repository's.
    when(connectorCatalogRepository.findByConnectorIdIn(List.of("salesforce", "hubspot")))
        .thenReturn(
            List.of(
                entry("hubspot", "HubSpot", "https://mcp.example/hs"),
                entry("salesforce", "Salesforce CRM", "https://mcp.example/sf")));

    List<ConnectorSpec> specs = service.resolveSpecs(List.of("salesforce", "hubspot"));

    assertThat(specs)
        .containsExactly(
            new ConnectorSpec("salesforce", "Salesforce CRM", "https://mcp.example/sf", null),
            new ConnectorSpec("hubspot", "HubSpot", "https://mcp.example/hs", null));
  }

  @Test
  void resolveSpecs_duplicateIds_deduped() {
    when(connectorCatalogRepository.findByConnectorIdIn(List.of("salesforce")))
        .thenReturn(List.of(entry("salesforce", "Salesforce CRM", "https://mcp.example/sf")));

    List<ConnectorSpec> specs =
        service.resolveSpecs(List.of("salesforce", "salesforce", "salesforce"));

    assertThat(specs)
        .containsExactly(
            new ConnectorSpec("salesforce", "Salesforce CRM", "https://mcp.example/sf", null));
  }

  @Test
  void resolveSpecs_entryHasBearerTokenKey_specCarriesItOut() {
    when(connectorCatalogRepository.findByConnectorIdIn(List.of("salesforce")))
        .thenReturn(
            List.of(
                entry(
                    "salesforce",
                    "Salesforce CRM",
                    "https://mcp.example/sf",
                    "salesforce-token-key")));

    List<ConnectorSpec> specs = service.resolveSpecs(List.of("salesforce"));

    assertThat(specs)
        .containsExactly(
            new ConnectorSpec(
                "salesforce", "Salesforce CRM", "https://mcp.example/sf", "salesforce-token-key"));
  }

  @Test
  void resolveSpecs_blankBearerTokenKey_normalizedToNull() {
    when(connectorCatalogRepository.findByConnectorIdIn(List.of("salesforce")))
        .thenReturn(List.of(entry("salesforce", "Salesforce CRM", "https://mcp.example/sf", "  ")));

    List<ConnectorSpec> specs = service.resolveSpecs(List.of("salesforce"));

    assertThat(specs)
        .containsExactly(
            new ConnectorSpec("salesforce", "Salesforce CRM", "https://mcp.example/sf", null));
  }

  @Test
  void resolveSpecs_missingId_throwsNotFoundExceptionNamingIt() {
    when(connectorCatalogRepository.findByConnectorIdIn(anyCollection())).thenReturn(List.of());

    assertThatThrownBy(() -> service.resolveSpecs(List.of("ghostvendor")))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining("ghostvendor");
  }

  @Test
  void resolveSpecs_blankMcpUrl_throwsNotFoundExceptionNamingMisconfiguredEntry() {
    when(connectorCatalogRepository.findByConnectorIdIn(List.of("salesforce")))
        .thenReturn(List.of(entry("salesforce", "Salesforce CRM", "  ")));

    assertThatThrownBy(() -> service.resolveSpecs(List.of("salesforce")))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining("salesforce")
        .hasMessageContaining("設定不完整");
  }

  @Test
  void resolveSpecs_blankDisplayName_throwsNotFoundExceptionNamingMisconfiguredEntry() {
    when(connectorCatalogRepository.findByConnectorIdIn(List.of("salesforce")))
        .thenReturn(List.of(entry("salesforce", "", "https://mcp.example/sf")));

    assertThatThrownBy(() -> service.resolveSpecs(List.of("salesforce")))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining("salesforce")
        .hasMessageContaining("設定不完整");
  }

  // ── validateKnownIds ──────────────────────────────────────────────────────

  @Test
  void validateKnownIds_nullOrEmpty_noOp() {
    service.validateKnownIds(null);
    service.validateKnownIds(List.of());
    // No repository interaction required to reach a passing assertion — reaching here without
    // throwing is the assertion itself.
  }

  @Test
  void validateKnownIds_allKnown_doesNotThrow() {
    when(connectorCatalogRepository.findAll())
        .thenReturn(List.of(entry("salesforce", "Salesforce CRM", "https://mcp.example/sf")));

    service.validateKnownIds(List.of("salesforce"));
  }

  @Test
  void validateKnownIds_unknownId_throwsConflictExceptionListingUnknownAndAvailable() {
    when(connectorCatalogRepository.findAll())
        .thenReturn(List.of(entry("salesforce", "Salesforce CRM", "https://mcp.example/sf")));

    assertThatThrownBy(() -> service.validateKnownIds(List.of("ghostvendor")))
        .isInstanceOf(ConflictException.class)
        .hasMessageContaining("ghostvendor")
        .hasMessageContaining("salesforce");
  }

  @Test
  void validateKnownIds_unknownId_availableIdsListedInSortedOrder() {
    // Repository returns entries in an arbitrary (non-alphabetical) order — the conflict message
    // must list available ids in a stable, sorted order regardless of repository return order.
    when(connectorCatalogRepository.findAll())
        .thenReturn(
            List.of(
                entry("zeta", "Zeta", "https://mcp.example/zeta"),
                entry("alpha", "Alpha", "https://mcp.example/alpha"),
                entry("mid", "Mid", "https://mcp.example/mid")));

    assertThatThrownBy(() -> service.validateKnownIds(List.of("ghostvendor")))
        .isInstanceOf(ConflictException.class)
        .hasMessageContaining("可用資料源: alpha, mid, zeta");
  }
}
