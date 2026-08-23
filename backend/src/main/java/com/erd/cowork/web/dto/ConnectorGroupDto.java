package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;

/** One connector group entry from deepagent-service's {@code GET /connectors} catalog. */
public record ConnectorGroupDto(
    @Schema(description = "Connector group name (unique)", example = "mes") String name,
    @Schema(description = "Human-readable display label", example = "MES 系統") String display,
    @Schema(description = "Group description", example = "產線批次與良率資料") String description) {}
