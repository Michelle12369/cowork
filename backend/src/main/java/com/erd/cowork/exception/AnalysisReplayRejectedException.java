package com.erd.cowork.exception;

/**
 * Thrown when deepagent-service's {@code POST /replay} reports a request-shaped replay failure
 * (stale recipe, removed source, schema drift) — a normal {@code 200} response with a populated
 * {@code error} field, not a transport failure. Mapped by {@link GlobalExceptionHandler} to a
 * {@code 502}, passing deepagent-service's {@code code}/{@code message} straight through so the
 * caller sees the same reason.
 *
 * <p>Also thrown Java-side (with a synthesised {@code INVALID_RECIPE} code) when the stored recipe
 * JSON fails to parse before a replay request is even sent — the same class of "corrupt recipe"
 * problem as deepagent-service's own {@code INVALID_RECIPE} outcome, kept on the same 502 status
 * rather than surfacing as an unrelated 500 via {@link AnalysisReplayFailedException}.
 */
public class AnalysisReplayRejectedException extends RuntimeException {

  private final String errorCode;

  public AnalysisReplayRejectedException(String errorCode, String message) {
    super(message);
    this.errorCode = errorCode;
  }

  public String getErrorCode() {
    return errorCode;
  }
}
