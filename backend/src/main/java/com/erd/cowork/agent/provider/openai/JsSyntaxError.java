package com.erd.cowork.agent.provider.openai;

/**
 * A JS syntax error found in an inline script block of a generated HTML artifact.
 *
 * @param scriptIndex 0-based index of the {@code <script>} block within the HTML
 * @param line 1-based line number within the script block (-1 if unknown)
 * @param column 1-based column number within the script block (-1 if unknown)
 * @param message human-readable error description from the JS parser
 */
public record JsSyntaxError(int scriptIndex, int line, int column, String message) {}
