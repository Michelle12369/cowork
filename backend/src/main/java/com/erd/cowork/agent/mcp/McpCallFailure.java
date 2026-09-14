package com.erd.cowork.agent.mcp;

/** Wire shape of a failed mcp() result: {"error": {"code", "message"}}, never carries data. */
public record McpCallFailure(McpCallError error) {}
