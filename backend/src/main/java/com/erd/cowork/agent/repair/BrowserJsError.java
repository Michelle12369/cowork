package com.erd.cowork.agent.repair;

/**
 * A JavaScript error reported by the browser iframe at runtime.
 *
 * @param message error message (already capped at 500 chars by the capture script)
 * @param line line number where the error occurred (0 if unknown)
 * @param col column number where the error occurred (0 if unknown)
 * @param sourceLine trimmed text of the offending line, extracted from the assembled HTML the
 *     browser actually rendered (empty when unknown/out of range)
 */
public record BrowserJsError(String message, int line, int col, String sourceLine) {

  /** 舊介面相容:捕捉端只給 message/line/col,sourceLine 由 repair 流程以組裝版 HTML 補上。 */
  public BrowserJsError(String message, int line, int col) {
    this(message, line, col, "");
  }
}
