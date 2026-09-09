package com.erd.cowork.repo;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.PersistenceConfig;
import com.erd.cowork.domain.ConnectorCatalogEntry;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.data.mongo.DataMongoTest;
import org.springframework.context.annotation.Import;

// PersistenceConfig import required — see RepositoryTest's identical comment: @DataMongoTest does
// not scan standalone @Configuration classes, so without it the BeforeConvertCallback UUID
// generator and @EnableMongoAuditing timestamps are inactive.
@DataMongoTest
@Import(PersistenceConfig.class)
class ConnectorCatalogRepositoryTest {

  @Autowired ConnectorCatalogRepository connectorCatalogRepository;

  private static ConnectorCatalogEntry entry(String connectorId, String displayName, String url) {
    ConnectorCatalogEntry entry = new ConnectorCatalogEntry();
    entry.setConnectorId(connectorId);
    entry.setDisplayName(displayName);
    entry.setMcpUrl(url);
    return entry;
  }

  @Test
  void findByConnectorId_matchingEntry_returnsIt() {
    connectorCatalogRepository.save(
        entry("salesforce-" + System.nanoTime(), "Salesforce CRM", "https://mcp.example/sf"));
    String connectorId = "hubspot-" + System.nanoTime();
    connectorCatalogRepository.save(entry(connectorId, "HubSpot", "https://mcp.example/hs"));

    Optional<ConnectorCatalogEntry> found =
        connectorCatalogRepository.findByConnectorId(connectorId);

    assertThat(found).isPresent();
    assertThat(found.get().getDisplayName()).isEqualTo("HubSpot");
    assertThat(found.get().getMcpUrl()).isEqualTo("https://mcp.example/hs");
  }

  @Test
  void findByConnectorId_noMatch_returnsEmpty() {
    assertThat(connectorCatalogRepository.findByConnectorId("nonexistent-" + System.nanoTime()))
        .isEmpty();
  }

  @Test
  void findByConnectorIdIn_multipleIds_returnsOnlyMatchingSubset() {
    String salesforceId = "salesforce-" + System.nanoTime();
    String hubspotId = "hubspot-" + System.nanoTime();
    String unrelatedId = "unrelated-" + System.nanoTime();
    connectorCatalogRepository.save(
        entry(salesforceId, "Salesforce CRM", "https://mcp.example/sf"));
    connectorCatalogRepository.save(entry(hubspotId, "HubSpot", "https://mcp.example/hs"));
    connectorCatalogRepository.save(entry(unrelatedId, "Unrelated", "https://mcp.example/x"));

    List<ConnectorCatalogEntry> found =
        connectorCatalogRepository.findByConnectorIdIn(
            List.of(salesforceId, hubspotId, "never-inserted-" + System.nanoTime()));

    assertThat(found)
        .extracting(ConnectorCatalogEntry::getConnectorId)
        .containsExactlyInAnyOrder(salesforceId, hubspotId);
  }
}
