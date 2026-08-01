package com.erd.cowork.agent.event;

/**
 * Streaming delta event for LLM extended thinking / reasoning tokens.
 *
 * <p>Emitted by providers that support a {@code reasoning} field in the SSE delta chunk (e.g.,
 * OpenAI-compatible endpoints with extended-thinking enabled). Each event contains one reasoning
 * delta chunk, analogous to {@link TokenEvent} for content tokens.
 *
 * @param delta one reasoning token delta
 */
public record ThinkingEvent(String delta) implements AgentEvent {}
