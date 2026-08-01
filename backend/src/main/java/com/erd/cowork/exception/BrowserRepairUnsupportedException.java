package com.erd.cowork.exception;

/**
 * Thrown when browser-error artifact repair is requested but the currently active {@code
 * AgentProvider} is not a {@code DashboardAgentProvider} (e.g. {@code langgraph-analysis} mode).
 * Browser repair fixes JS-syntax/omission failures specific to LLM-written HTML; renderer-produced
 * HTML has no such failure class to repair, so the capability genuinely does not apply — this is a
 * 409 conflict (endpoint exists, mode does not support it), not a 404.
 */
public class BrowserRepairUnsupportedException extends RuntimeException {

  public BrowserRepairUnsupportedException(String message) {
    super(message);
  }
}
