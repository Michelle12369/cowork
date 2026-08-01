package com.erd.cowork.agent.provider;

import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;

/**
 * {@link AgentProvider} specialization for modes where the LLM writes HTML directly. Only these
 * modes need generation-time repair ({@link #harden}) and previous-HTML feedback. Modes whose HTML
 * comes from a deterministic renderer (e.g. {@code LangGraphAnalysisProvider}) implement {@link
 * AgentProvider} directly instead, since renderer output cannot carry the JS-syntax/omission
 * failures this hook exists to fix.
 */
public interface DashboardAgentProvider extends AgentProvider {

  /**
   * Post-generation quality hardening. Default: pass the outcome through unchanged.
   *
   * @param sessionId session identifier (for logging)
   * @param request the original agent request
   * @param outcome the outcome from {@link #generate}
   * @return a {@link RepairResult} whose events flux carries live step events and whose output
   *     future completes with the final HTML and answer text
   * @implSpec Threading contract for implementers: the orchestrator subscribes the returned {@code
   *     events} flux on a blocking-capable scheduler (Reactor boundedElastic), so blocking calls
   *     inside deferred sections of the flux are legal. The {@code output} future MUST be completed
   *     (normally or exceptionally) by the time {@code events} terminates — the orchestrator joins
   *     on it right after the flux completes, and an incomplete future would block that thread
   *     indefinitely. Guard any code path that could bypass completion (see {@code
   *     GenerationRepairGuard}'s try-finally backstop).
   */
  default RepairResult harden(String sessionId, AgentRequest request, AgentOutcome outcome) {
    return RepairResult.passthrough(outcome);
  }
}
