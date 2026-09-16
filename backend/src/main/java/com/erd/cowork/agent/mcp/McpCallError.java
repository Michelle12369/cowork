package com.erd.cowork.agent.mcp;

/** The error half of an mcp() result; code is one of McpErrorCode, message is viewer-facing. */
public record McpCallError(String code, String message) {}
