package com.erd.cowork.agent.model;

import java.util.List;

/**
 * @param previousArtifactHtml the {@code baseArtifactId}-resolved (or, when unspecified,
 *     most-recent) prior artifact's raw HTML, fed back so an iteration turn edits it instead of
 *     rebuilding from memory. Resolved by the orchestrator for every turn: dashboard-mode providers
 *     splice it into the LLM prompt; the analysis-mode provider ({@link
 *     com.erd.cowork.agent.provider.analysis.LangGraphAnalysisProvider}) forwards it as the {@code
 *     previousDashboardHtml} wire field. {@code null} when no prior artifact exists (first turn).
 * @param selectedConnectors the session's locked-in connector ids (spec §5), read from {@code
 *     ChatSession#getSelectedConnectors()} by the orchestrator — authoritative, never the raw
 *     per-request value. {@code null}/empty when the session is undecided or in files mode.
 * @param ssoToken the caller's SSO token, captured from {@link
 *     com.erd.cowork.context.CoworkContext#ssoToken()} on the request thread before the async/SSE
 *     boundary (the ThreadLocal-backed holder does not cross threads — same rationale as {@code
 *     userId} being threaded explicitly rather than re-read from the holder downstream). Secret:
 *     NEVER logged — {@link #toString()} masks it, mirroring {@code CoworkContext#toString()}.
 */
public record AgentRequest(
    String userId,
    String sessionId,
    String question,
    List<HistoryMessage> history,
    List<AgentFileContext> files,
    String previousArtifactHtml,
    List<String> selectedConnectors,
    String ssoToken) {

  /**
   * Back-compat constructor for callers built before the connector/SSO wire fields existed (repair
   * flows that never touch connectors/SSO): defaults {@code selectedConnectors} to empty and {@code
   * ssoToken} to {@code null}.
   */
  public AgentRequest(
      String userId,
      String sessionId,
      String question,
      List<HistoryMessage> history,
      List<AgentFileContext> files,
      String previousArtifactHtml) {
    this(userId, sessionId, question, history, files, previousArtifactHtml, List.of(), null);
  }

  /** Mirrors the default record format, except {@code ssoToken} — MUST NEVER ride a log line. */
  @Override
  public String toString() {
    return "AgentRequest[userId="
        + userId
        + ", sessionId="
        + sessionId
        + ", question="
        + question
        + ", history="
        + history
        + ", files="
        + files
        + ", previousArtifactHtml="
        + previousArtifactHtml
        + ", selectedConnectors="
        + selectedConnectors
        + ", ssoToken="
        + (ssoToken == null ? "null" : "***")
        + "]";
  }
}
