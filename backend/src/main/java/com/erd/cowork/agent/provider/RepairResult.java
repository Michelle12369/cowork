package com.erd.cowork.agent.provider;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.model.AgentOutcome;
import java.util.concurrent.CompletableFuture;
import reactor.core.publisher.Flux;

/**
 * Result produced by {@link DashboardAgentProvider#harden}.
 *
 * <p>Two-channel shape mirrors {@link ProviderResult}:
 *
 * <ul>
 *   <li>{@code events} — live {@link com.erd.cowork.agent.event.StepEvent}s (e.g. r1 RUNNING →
 *       terminal) that the orchestrator forwards into the SSE stream. May be {@link Flux#empty()}
 *       when no repair is attempted.
 *   <li>{@code output} — a {@link CompletableFuture} that completes when (or before) {@code events}
 *       completes and carries the final HTML and answer text after hardening.
 * </ul>
 *
 * @apiNote Single-subscription contract: {@code events} is a cold Flux that drives side effects
 *     (blocking repair calls, future completion) and must be subscribed to at most once. The
 *     orchestrator subscribes exactly once by concatenating it into the outgoing SSE flux.
 * @implSpec {@code output} MUST complete (normally or exceptionally) no later than {@code events}
 *     termination; the consumer joins on it immediately afterwards. {@code events} is subscribed on
 *     a blocking-capable scheduler — see {@link DashboardAgentProvider#harden} for the full
 *     threading contract.
 */
public record RepairResult(Flux<AgentEvent> events, CompletableFuture<HardenedOutput> output) {

  /**
   * Creates a passthrough result that leaves the outcome unchanged. The {@code events} flux is
   * empty and the {@code output} future is already completed with the outcome's html and
   * answerText.
   *
   * <p>Used by the {@link DashboardAgentProvider#harden} default implementation and by the {@link
   * com.erd.cowork.agent.provider.openai.GenerationRepairGuard} when repair is disabled or no
   * issues are detected.
   */
  public static RepairResult passthrough(AgentOutcome outcome) {
    return new RepairResult(
        Flux.empty(),
        CompletableFuture.completedFuture(
            new HardenedOutput(outcome.html(), outcome.answerText())));
  }
}
