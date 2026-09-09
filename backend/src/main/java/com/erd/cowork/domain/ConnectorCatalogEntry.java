package com.erd.cowork.domain;

import java.time.Instant;
import lombok.EqualsAndHashCode;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.Id;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.mongodb.core.mapping.Document;

/**
 * One entry in the API connector directory (MCP datasource catalog). {@code connectorId} is the
 * natural key referenced by {@link ChatSession#getSelectedConnectors()} and the frontend connector
 * picker; unique index enforced by {@code MongoIndexInitializer}.
 */
@Document(collection = "connector_catalog")
@Getter
@Setter
@EqualsAndHashCode(of = "id")
@NoArgsConstructor
public class ConnectorCatalogEntry {

  @Id private String id;
  private String connectorId;
  private String displayName;
  private String mcpUrl;
  private String bearerTokenKey;

  @CreatedDate private Instant createdAt;
  @LastModifiedDate private Instant updatedAt;
}
