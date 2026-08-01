package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import java.time.Instant;

public record SessionSummaryDto(
    @Schema(description = "Session UUID", example = "3fa85f64-5717-4562-b3fc-2c963f66afa6")
        String id,
    @Schema(description = "Session title", example = "New analysis") String title,
    @Schema(description = "Last updated timestamp (ISO-8601)", example = "2026-07-05T10:30:00Z")
        Instant updatedAt) {}
