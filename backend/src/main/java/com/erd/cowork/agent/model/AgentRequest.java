package com.erd.cowork.agent.model;

import java.util.List;

/**
 * @param previousArtifactHtml the {@code baseArtifactId}-resolved (or, when unspecified,
 *     most-recent) prior artifact's raw HTML, fed back so an iteration turn edits it instead of
 *     rebuilding from memory. Resolved by the orchestrator for every turn: dashboard-mode providers
 *     splice it into the LLM prompt; the analysis-mode provider ({@link
 *     com.erd.cowork.agent.provider.analysis.LangGraphAnalysisProvider}) forwards it as the {@code
 *     previousDashboardHtml} wire field. {@code null} when no prior artifact exists (first turn).
 * @param connectorSpecs the session's locked-in connector ids, already resolved into wire specs
 *     {@code {id, name, url}} — read from {@code ChatSession#getSelectedConnectors()} and resolved
 *     via {@link com.erd.cowork.service.ConnectorCatalogService#resolveSpecs} by the orchestrator
 *     (in {@code prepare()}, before the async/reactive webClient call), never re-resolved by the
 *     provider itself. {@code null}/empty when the session is undecided or in files mode.
 * @param ssoToken the caller's SSO token, captured from {@link
 *     com.erd.cowork.context.CoworkContext#ssoToken()} on the request thread before the async/SSE
 *     boundary (the ThreadLocal-backed holder does not cross threads). Secret: NEVER logged —
 *     {@link #toString()} masks it, mirroring {@code CoworkContext#toString()}. Forwarded to
 *     deepagent as an HTTP header (name configurable, see {@link
 *     com.erd.cowork.config.AnalysisAgentProperties#ssoTokenHeader()}, default {@code X-SSO-Token})
 *     — never the JSON body.
 * @param ssoUrl the caller's SSO gateway URL, captured from {@link
 *     com.erd.cowork.context.CoworkContext#ssoUrl()} alongside {@code ssoToken}. Less sensitive
 *     than the token, but masked the same way in {@link #toString()} for consistency. Forwarded to
 *     deepagent as an HTTP header (name configurable, see {@link
 *     com.erd.cowork.config.AnalysisAgentProperties#ssoUrlHeader()}, default {@code X-SSO-Url}).
 */
public record AgentRequest(
    String userId,
    String sessionId,
    String question,
    List<HistoryMessage> history,
    List<AgentFileContext> files,
    String previousArtifactHtml,
    List<ConnectorSpec> connectorSpecs,
    String ssoToken,
    String ssoUrl) {

  /**
   * Back-compat constructor for callers built before the connector/SSO wire fields existed (repair
   * flows that never touch connectors/SSO): defaults {@code connectorSpecs} to empty and both
   * {@code ssoToken}/{@code ssoUrl} to {@code null}.
   */
  public AgentRequest(
      String userId,
      String sessionId,
      String question,
      List<HistoryMessage> history,
      List<AgentFileContext> files,
      String previousArtifactHtml) {
    this(userId, sessionId, question, history, files, previousArtifactHtml, List.of(), null, null);
  }

  /**
   * Mirrors the default record format, except {@code ssoToken}/{@code ssoUrl} — MUST NEVER ride a
   * log line.
   */
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
        + ", connectorSpecs="
        + connectorSpecs
        + ", ssoToken="
        + (ssoToken == null ? "null" : "***")
        + ", ssoUrl="
        + (ssoUrl == null ? "null" : "***")
        + "]";
  }
}
