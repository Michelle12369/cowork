package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import java.time.Instant;
import java.util.List;

public record SessionDetailDto(
    @Schema(description = "Session UUID", example = "3fa85f64-5717-4562-b3fc-2c963f66afa6")
        String id,
    @Schema(description = "Session title", example = "New analysis") String title,
    @Schema(description = "Session creation timestamp (ISO-8601)", example = "2026-07-05T10:30:00Z")
        Instant createdAt,
    @Schema(description = "Messages belonging to this session") List<MessageDto> messages,
    @Schema(description = "Files attached to this session") List<FileDto> files) {}
