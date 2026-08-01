package com.erd.cowork.exception;

/**
 * Thrown when deepagent-service's {@code POST /repair} endpoint fails outright (e.g. a {@code 502}
 * from a failed/timed-out model call), as opposed to a {@code 422} guard rejection (a normal
 * "repair not passed" outcome, not an exception).
 *
 * <p>Deliberately left unmapped in {@link GlobalExceptionHandler} — mirrors the dashboard repair
 * path, where a hard provider/transport failure has no bespoke handling and simply surfaces as a
 * 500.
 */
public class AnalysisBrowserRepairFailedException extends RuntimeException {

  public AnalysisBrowserRepairFailedException(String message) {
    super(message);
  }
}
