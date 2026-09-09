package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.domain.ConnectorCatalogEntry;
import com.erd.cowork.repo.ConnectorCatalogRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DuplicateKeyException;

/**
 * Covers the unique index {@code MongoIndexInitializer} creates on {@code
 * connector_catalog.connectorId} (fired via {@code ApplicationReadyEvent}, hence
 * {@code @SpringBootTest} rather than a {@code @DataMongoTest} slice, which would skip it) — a
 * second entry with the same {@code connectorId} must be rejected rather than silently shadowing
 * the first.
 */
@SpringBootTest
class ConnectorCatalogUniqueIndexTest {

  @Autowired ConnectorCatalogRepository connectorCatalogRepository;

  private static ConnectorCatalogEntry entry(String connectorId) {
    ConnectorCatalogEntry entry = new ConnectorCatalogEntry();
    entry.setConnectorId(connectorId);
    entry.setDisplayName("Salesforce CRM");
    entry.setMcpUrl("https://mcp.example/salesforce");
    return entry;
  }

  @Test
  void save_duplicateConnectorId_throwsDuplicateKeyException() {
    String connectorId = "salesforce-" + System.nanoTime();
    connectorCatalogRepository.save(entry(connectorId));

    assertThatThrownBy(() -> connectorCatalogRepository.save(entry(connectorId)))
        .isInstanceOf(DuplicateKeyException.class);
  }
}
