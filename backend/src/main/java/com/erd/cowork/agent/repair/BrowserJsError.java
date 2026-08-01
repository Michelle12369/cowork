package com.erd.cowork.agent.repair;

/**
 * A JavaScript error reported by the browser iframe at runtime.
 *
 * @param message error message (already capped at 500 chars by the capture script)
 * @param line line number where the error occurred (0 if unknown)
 * @param col column number where the error occurred (0 if unknown)
 */
public record BrowserJsError(String message, int line, int col) {}
