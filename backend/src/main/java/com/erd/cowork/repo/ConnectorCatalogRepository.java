package com.erd.cowork.repo;

import com.erd.cowork.domain.ConnectorCatalogEntry;
import java.util.Collection;
import java.util.List;
import java.util.Optional;
import org.springframework.data.mongodb.repository.MongoRepository;

public interface ConnectorCatalogRepository extends MongoRepository<ConnectorCatalogEntry, String> {
  Optional<ConnectorCatalogEntry> findByConnectorId(String connectorId);

  List<ConnectorCatalogEntry> findByConnectorIdIn(Collection<String> connectorIds);
}
