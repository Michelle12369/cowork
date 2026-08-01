package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;
import java.util.List;

@Schema(description = "Request to repair an artifact based on browser-reported runtime errors")
public record RepairRequestDto(
    @NotEmpty
        @Size(max = 10)
        @Schema(
            description = "Runtime JavaScript errors reported by the browser (maximum 10)",
            minLength = 1,
            maxLength = 10)
        List<@Valid BrowserErrorDto> errors) {}
