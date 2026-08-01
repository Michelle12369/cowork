package com.erd.cowork.agent.model;

import java.util.List;

/**
 * Payload record for a single clarification question emitted via the {@code ```questions} fenced
 * block protocol.
 *
 * <p>When the LLM wants to ask clarifying questions before generating a dashboard, it emits a
 * {@code ```questions} fenced block containing a JSON array of {@code ClarifyingQuestion} objects.
 *
 * @param text the question text displayed to the user
 * @param options optional list of pre-defined answer choices; may be empty
 * @param multiSelect {@code true} if the user may select multiple options
 */
public record ClarifyingQuestion(String text, List<String> options, boolean multiSelect) {}
