package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;

/**
 * One entry in the API connector directory (Mongo-backed catalog, see {@code
 * ConnectorCatalogEntry}).
 */
public record ConnectorInfoDto(
    @Schema(description = "Connector id", example = "salesforce") String id,
    @Schema(description = "Connector display name", example = "Salesforce CRM") String name) {}
