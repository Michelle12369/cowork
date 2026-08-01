package com.erd.cowork.agent.provider.openai;

import java.util.List;

/**
 * Result of a single generation-repair attempt on a broken HTML artifact.
 *
 * <p>This record is used exclusively by the openai-compatible generation-repair pipeline ({@link
 * GenerationRepairer}). Browser-error repair uses {@link
 * com.erd.cowork.agent.repair.BrowserRepairOutcome} instead.
 *
 * @param html the best available HTML — repaired version if {@code passed}, original otherwise
 * @param passed {@code true} if re-validation after repair found zero errors
 * @param errorsBefore errors found in the original HTML (before repair)
 * @param errorsAfter errors found after repair (empty list when {@code passed == true})
 */
public record RepairOutcome(
    String html,
    boolean passed,
    List<JsSyntaxError> errorsBefore,
    List<JsSyntaxError> errorsAfter) {}
