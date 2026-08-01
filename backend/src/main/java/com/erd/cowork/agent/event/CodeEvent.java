package com.erd.cowork.agent.event;

/**
 * Streaming delta event for the HTML code being generated inside the first {@code ```html} fence.
 *
 * <p>Mirrors the exact chunks appended to the extraction HTML buffer so the frontend can render a
 * live "code being written" view. Never mixed into TOKEN/answer text.
 *
 * @param delta one HTML code chunk
 */
public record CodeEvent(String delta) implements AgentEvent {}
