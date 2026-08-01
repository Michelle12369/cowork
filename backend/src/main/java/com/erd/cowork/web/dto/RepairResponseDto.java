package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;

@Schema(description = "Result of an artifact repair attempt")
public record RepairResponseDto(
    @Schema(
            description =
                "true if the repair passed syntax validation and the artifact was updated;"
                    + " false if the LLM produced no improvement",
            example = "true")
        boolean repaired) {}
