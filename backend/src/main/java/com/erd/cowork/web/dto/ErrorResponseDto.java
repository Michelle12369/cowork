package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;

/**
 * Uniform error response body returned by {@link com.erd.cowork.exception.GlobalExceptionHandler}
 * for all handled exceptions. Field names intentionally match the legacy {@code Map.of("code", …,
 * "message", …)} shape so the JSON wire format is byte-identical and the frontend requires zero
 * changes.
 */
public record ErrorResponseDto(
    @Schema(description = "Machine-readable error code.", example = "NOT_FOUND") String code,
    @Schema(description = "Human-readable error description.", example = "Session not found.")
        String message) {}
