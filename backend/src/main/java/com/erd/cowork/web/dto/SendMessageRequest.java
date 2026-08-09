package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record SendMessageRequest(
    @NotBlank
        @Size(max = 16000, message = "question must not exceed 16000 characters")
        @Schema(
            description =
                "User question. Max 16,000 characters — at 4 bytes/char worst case (utf8mb4) this"
                    + " stays under the 65,535-byte MariaDB TEXT column limit for"
                    + " chat_message.text.",
            example = "Run an SPC analysis on Vt")
        String question,
    @Schema(
            description =
                "Optional artifact UUID to use as the base for iterative refinement."
                    + " When supplied, the provider receives the raw HTML of the specified artifact"
                    + " instead of the most-recent one."
                    + " If the artifact does not belong to this session the most-recent is used.",
            nullable = true,
            example = "3fa85f64-5717-4562-b3fc-2c963f66afa6")
        String baseArtifactId) {}
