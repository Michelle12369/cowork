package com.erd.cowork.agent.provider.analysis;

/**
 * Result of a deepagent-service {@code POST /replay} call — the recipe-replay analog of {@link
 * com.erd.cowork.agent.repair.BrowserRepairOutcome}.
 *
 * <p>Unlike the {@code /repair} contract, {@code /replay} never surfaces its own request-shaped
 * failures (stale recipe, source removed, schema drift) as an HTTP error status — those all come
 * back as a plain {@code 200} whose body carries either {@code html} (success) or {@code
 * errorCode}/{@code errorMessage} (failure). See {@link AnalysisReplayClient}.
 *
 * @param html freshly re-injected dashboard HTML; {@code null} on failure
 * @param errorCode deepagent-service's machine-readable failure code (e.g. {@code SOURCE_GONE},
 *     {@code SOURCE_SCHEMA_CHANGED}, {@code INVALID_RECIPE}); {@code null} on success
 * @param errorMessage human-readable failure description; {@code null} on success
 */
public record AnalysisReplayOutcome(String html, String errorCode, String errorMessage) {

  public static AnalysisReplayOutcome success(String html) {
    return new AnalysisReplayOutcome(html, null, null);
  }

  public static AnalysisReplayOutcome failure(String errorCode, String errorMessage) {
    return new AnalysisReplayOutcome(null, errorCode, errorMessage);
  }

  public boolean isSuccess() {
    return html != null;
  }
}
