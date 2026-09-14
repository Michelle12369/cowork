package com.erd.cowork.web.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.util.Map;

@Schema(description = "One view-time mcp() call issued by a connector-mode dashboard")
public record McpCallRequestDto(
    @NotBlank
        @Schema(
            description = "Connector id as written in the dashboard's mcp() call",
            example = "sales")
        String connector,
    @NotBlank @Schema(description = "MCP tool name on that connector", example = "list_orders")
        String tool,
    @NotNull
        @Schema(
            description = "Tool arguments, forwarded to the MCP server unchanged",
            example = "{\"days\": 30}")
        Map<String, Object> args) {}
