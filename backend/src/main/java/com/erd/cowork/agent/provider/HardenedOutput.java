package com.erd.cowork.agent.provider;

/**
 * The output produced by {@link DashboardAgentProvider#harden}. Carries the (possibly repaired)
 * HTML and the answer text, both of which may have been transformed by the provider's hardening
 * logic (e.g. bare-HTML promotion or JS repair).
 *
 * <p>Questions are deliberately excluded — they originate from the original {@link
 * com.erd.cowork.agent.model.AgentOutcome} and are handled separately by the orchestrator.
 */
public record HardenedOutput(String html, String answerText) {}
