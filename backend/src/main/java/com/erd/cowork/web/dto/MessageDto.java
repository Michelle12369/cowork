package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import java.time.Instant;

public record MessageDto(
    @Schema(description = "Message UUID", example = "3fa85f64-5717-4562-b3fc-2c963f66afa6")
        String id,
    @Schema(description = "Sender role", example = "USER") String sender,
    @Schema(description = "Message text", example = "Run an SPC analysis on Vt.") String text,
    @Schema(description = "Serialised steps JSON; null when no tool calls") String stepsJson,
    @Schema(description = "Linked artifact UUID; null when not yet generated") String artifactId,
    @Schema(description = "Creation timestamp (ISO-8601)", example = "2026-07-05T10:30:00Z")
        Instant createdAt,
    @Schema(description = "Artifact title if any") String artifactTitle,
    @Schema(
            description =
                "Serialised clarification questions JSON array; non-null when the AI requested"
                    + " clarification via the ```questions protocol",
            nullable = true)
        String questionsJson) {}
