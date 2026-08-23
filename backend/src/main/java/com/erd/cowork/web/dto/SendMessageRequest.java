package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import java.util.List;

public record SendMessageRequest(
    @NotBlank @Schema(description = "User question", example = "Run an SPC analysis on Vt")
        String question,
    @Schema(
            description =
                "Optional artifact UUID to use as the base for iterative refinement."
                    + " When supplied, the provider receives the raw HTML of the specified artifact"
                    + " instead of the most-recent one."
                    + " If the artifact does not belong to this session the most-recent is used.",
            nullable = true,
            example = "3fa85f64-5717-4562-b3fc-2c963f66afa6")
        String baseArtifactId,
    @Schema(
            description =
                "User-selected connector group names scoping which data-source tools the"
                    + " analysis provider may use this turn. Null or empty means all groups"
                    + " (backward-compatible default).",
            nullable = true,
            example = "[\"mes\"]")
        List<String> selectedGroups) {}
