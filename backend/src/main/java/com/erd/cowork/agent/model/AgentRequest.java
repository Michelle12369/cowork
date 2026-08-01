package com.erd.cowork.agent.model;

import java.util.List;

/**
 * @param previousArtifactHtml the {@code baseArtifactId}-resolved (or, when unspecified,
 *     most-recent) prior artifact's raw HTML, fed back so an iteration turn edits it instead of
 *     rebuilding from memory. Resolved by the orchestrator for every turn: dashboard-mode providers
 *     splice it into the LLM prompt; the analysis-mode provider ({@link
 *     com.erd.cowork.agent.provider.analysis.LangGraphAnalysisProvider}) forwards it as the {@code
 *     previousDashboardHtml} wire field. {@code null} when no prior artifact exists (first turn).
 */
public record AgentRequest(
    String userId,
    String sessionId,
    String question,
    List<HistoryMessage> history,
    List<AgentFileContext> files,
    String previousArtifactHtml) {}
