package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;

@Schema(
    description =
        "Freshly re-injected artifact HTML from a recipe replay. Transient — no new artifact"
            + " version is persisted.")
public record RefreshResponseDto(
    @Schema(
            description = "Self-contained dashboard HTML with the latest source data injected.",
            example = "<!DOCTYPE html>...")
        String html) {}
