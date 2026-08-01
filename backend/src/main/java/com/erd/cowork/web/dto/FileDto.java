package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;

public record FileDto(
    @Schema(description = "File UUID", example = "3fa85f64-5717-4562-b3fc-2c963f66afa6") String id,
    @Schema(description = "Original file name", example = "wafer_data.csv") String name,
    @Schema(description = "User-facing alias", example = "file1") String alias,
    @Schema(description = "File size in bytes", example = "204800") long sizeBytes,
    @Schema(description = "File extension / type", example = "csv") String type,
    @Schema(description = "Parsed data row count", example = "24") Long rowCount,
    @Schema(description = "True when purged by retention cleanup") boolean expired) {}
