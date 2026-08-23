package com.erd.cowork.agent.model;

import java.util.List;

/**
 * @param previousArtifactHtml the {@code baseArtifactId}-resolved (or, when unspecified,
 *     most-recent) prior artifact's raw HTML, fed back so an iteration turn edits it instead of
 *     rebuilding from memory. Resolved by the orchestrator for every turn: dashboard-mode providers
 *     splice it into the LLM prompt; the analysis-mode provider ({@link
 *     com.erd.cowork.agent.provider.analysis.LangGraphAnalysisProvider}) forwards it as the {@code
 *     previousDashboardHtml} wire field. {@code null} when no prior artifact exists (first turn).
 * @param selectedGroups user-selected connector group names scoping which data-source tools the
 *     analysis-mode provider may use this turn (§11 connector selection); forwarded verbatim as the
 *     {@code selectedGroups} wire field. Empty means all groups (backward-compatible default) — see
 *     the 6-arg constructor below, used by dashboard-mode and repair call sites that predate this
 *     field and have no notion of connector scoping.
 */
public record AgentRequest(
    String userId,
    String sessionId,
    String question,
    List<HistoryMessage> history,
    List<AgentFileContext> files,
    String previousArtifactHtml,
    List<String> selectedGroups) {

  /**
   * Back-compat constructor for the pre-{@code selectedGroups} call sites (dashboard-mode
   * generation/repair, and the dozens of existing tests) — defaults {@code selectedGroups} to empty
   * (= all groups), preserving the invariant without touching every call site.
   */
  public AgentRequest(
      String userId,
      String sessionId,
      String question,
      List<HistoryMessage> history,
      List<AgentFileContext> files,
      String previousArtifactHtml) {
    this(userId, sessionId, question, history, files, previousArtifactHtml, List.of());
  }
}
