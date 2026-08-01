package com.erd.cowork.agent.repair;

/**
 * Result of a browser-error-driven repair attempt on a broken HTML artifact.
 *
 * <p>Success criterion: {@code passed = true} when the provider returned non-blank HTML. No GraalJS
 * re-validation is performed — runtime browser errors represent a different class of failure than
 * static syntax errors, and "got non-blank HTML back" is the appropriate success bar for this path.
 *
 * <p>For generation-repair outcomes (JS syntax / code omission), see {@link
 * com.erd.cowork.agent.provider.openai.RepairOutcome}.
 *
 * @param html the best available HTML — repaired version if {@code passed}, original otherwise
 * @param passed {@code true} if the provider returned non-blank HTML
 */
public record BrowserRepairOutcome(String html, boolean passed) {}
