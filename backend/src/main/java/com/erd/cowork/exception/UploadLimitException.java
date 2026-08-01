package com.erd.cowork.exception;

/**
 * Thrown when an upload violates a size, count, or type constraint. The {@link #getCode()} method
 * returns the string name of the {@link ErrorCode} so that callers and {@link
 * GlobalExceptionHandler} require no changes to the JSON wire format.
 */
public class UploadLimitException extends RuntimeException {
  private final ErrorCode errorCode;

  public UploadLimitException(ErrorCode errorCode, String message) {
    super(message);
    this.errorCode = errorCode;
  }

  /** Returns the string name of the error code, e.g. {@code "UPLOAD_LIMIT"}. */
  public String getCode() {
    return errorCode.name();
  }
}
