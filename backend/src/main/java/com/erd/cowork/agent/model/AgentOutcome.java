package com.erd.cowork.agent.model;

import java.util.List;

/**
 * Final outcome of a provider's response to an {@link AgentRequest}.
 *
 * <p>In the dashboard-generation mode this is produced by {@code ResponseExtractionHelper} after a
 * token stream terminates. In the analysis mode the {@code html} field is produced by a
 * deterministic renderer rather than extracted from LLM text, so this type intentionally does not
 * carry an "extraction" name.
 *
 * <ul>
 *   <li>{@code answerText} — trimmed plain-text answer (text outside all fenced blocks).
 *   <li>{@code html} — content of the first {@code ```html} fenced block (trimmed), or {@code null}
 *       if no such block was present.
 *   <li>{@code questions} — parsed list of clarification questions from the first {@code
 *       ```questions} fenced block, or {@code null} if no block was found or JSON parsing failed.
 *       An empty list ({@code []}) means the block was present and valid but contained no
 *       questions.
 * </ul>
 */
public record AgentOutcome(String answerText, String html, List<ClarifyingQuestion> questions) {}
