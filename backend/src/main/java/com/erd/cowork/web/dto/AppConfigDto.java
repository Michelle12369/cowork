package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.Map;

@Schema(description = "Public application configuration")
public record AppConfigDto(
    @Schema(
            description =
                "Number of inactivity days after which session files are automatically"
                    + " purged by the retention policy",
            example = "30")
        int retentionDays,
    @Schema(description = "Maximum number of files per chat session", example = "5") int maxFiles,
    @Schema(
            description = "Maximum total bytes across all files in a session",
            example = "5368709120")
        long maxSessionBytes,
    @Schema(
            description =
                "Maximum file size in bytes per extension. Keys are lowercase file"
                    + " extensions; values are the byte limit enforced by the backend.",
            example =
                "{\"csv\":2147483648,\"txt\":2147483648,\"tsv\":2147483648,\"xlsx\":209715200}")
        Map<String, Long> singleFileLimits) {}
