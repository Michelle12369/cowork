package com.erd.cowork.agent.mcp;

/** The five mcp() error codes shared by deepagent, Java and the frontend bridge. */
public enum McpErrorCode {
  AUTH,
  RETRYABLE,
  TOOL_ERROR,
  INVALID_CALL,
  CONNECTOR_UNAVAILABLE
}
