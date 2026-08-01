package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

@Schema(description = "A built-in demo dataset that can be loaded into a session with one click")
public record SampleDatasetDto(
    @Schema(
            description = "Stable identifier used in the load API path",
            example = "product-usage-feedback")
        String name,
    @Schema(description = "Human-facing title", example = "產品使用行為與回饋") String title,
    @Schema(
            description = "One-sentence explanation of what the dataset contains",
            example = "使用行為紀錄與使用者回饋，適合分析功能採用度與滿意度關聯")
        String description,
    @Schema(
            description = "Aliases the files will receive once loaded",
            example = "[\"usage_log\", \"feedback\"]")
        List<String> fileAliases) {}
