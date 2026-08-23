package com.erd.cowork.exception;

/**
 * Thrown when deepagent-service's {@code POST /replay} endpoint fails outright — a hard
 * transport/outage failure (non-2xx status, timeout) or a malformed stored recipe — as opposed to a
 * request-shaped replay failure (stale recipe, removed source, schema drift), which {@code /replay}
 * reports as a normal {@code 200} body and is carried as an {@link
 * com.erd.cowork.agent.provider.analysis.AnalysisReplayOutcome} failure, not an exception.
 *
 * <p>Deliberately left unmapped in {@link GlobalExceptionHandler} — mirrors {@link
 * AnalysisBrowserRepairFailedException}: a hard provider/transport failure has no bespoke handling
 * and simply surfaces as a 500.
 */
public class AnalysisReplayFailedException extends RuntimeException {

  public AnalysisReplayFailedException(String message) {
    super(message);
  }

  public AnalysisReplayFailedException(String message, Throwable cause) {
    super(message, cause);
  }
}
