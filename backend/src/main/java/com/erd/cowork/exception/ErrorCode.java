package com.erd.cowork.exception;

/** Typed catalogue of error codes carried in API error responses and SSE ErrorEvent payloads. */
public enum ErrorCode {
  /** Requested resource (session, file, artifact) does not exist or is owned by another user. */
  NOT_FOUND,

  /** The requested operation conflicts with existing state (e.g. duplicate resource). */
  CONFLICT,

  /** The agent pipeline encountered an unexpected or unhandled error. */
  AGENT_ERROR,

  /** Uploaded file could not be parsed (e.g. corrupt CSV or invalid XLSX structure). */
  PARSE_ERROR,

  /** Upload violates per-file, per-session, or per-session file-count limits. */
  UPLOAD_LIMIT,

  /** The uploaded file's extension is not supported by the platform. */
  UNSUPPORTED_TYPE,

  /**
   * Session contains one or more files removed by the 30-day retention policy. The user must delete
   * the expired file entries and re-upload before the agent pipeline can proceed.
   */
  FILES_EXPIRED,

  /**
   * Browser-error artifact repair was requested but the active provider mode has no {@code
   * DashboardAgentProvider} — the capability only applies to LLM-written HTML, not renderer-
   * produced HTML.
   */
  BROWSER_REPAIR_UNSUPPORTED
}
