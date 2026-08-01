package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

@Schema(description = "A single JavaScript error reported by the browser iframe at runtime")
public record BrowserErrorDto(
    @NotBlank
        @Size(max = 500)
        @Schema(
            description = "Error message (capped at 500 chars by the capture script)",
            example = "ReferenceError: showTab is not defined")
        String message,
    @Schema(description = "Line number where the error occurred (0 if unknown)", example = "42")
        int line,
    @Schema(description = "Column number where the error occurred (0 if unknown)", example = "15")
        int col) {}
