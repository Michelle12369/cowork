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
                "Connector ids to lock in on the first message. Only honored when the session is"
                    + " still undecided (no prior lock) and has no active files; otherwise ignored"
                    + " (a decided session's stored selection is authoritative, and an"
                    + " active-files session with a non-empty value is rejected with 409).",
            nullable = true,
            example = "[\"salesforce\"]")
        List<String> selectedConnectors) {}
