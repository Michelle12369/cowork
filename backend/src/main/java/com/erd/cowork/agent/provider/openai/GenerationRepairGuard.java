package com.erd.cowork.agent.provider.openai;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.extraction.BareHtmlUtils;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.HardenedOutput;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.provider.RepairResult;
import com.erd.cowork.config.AgentProperties;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.function.Function;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import reactor.core.publisher.Flux;

/**
 * Applies post-generation quality hardening to a newly generated HTML artifact: bare-HTML
 * promotion, then code-omission detection ({@link CodeOmissionValidator}, which takes priority and
 * triggers {@link GenerationRepairer#retryForOmission}), then JS syntax repair ({@link
 * JsSyntaxValidator} + {@link GenerationRepairer#repair}) when no omissions were found.
 *
 * <p>Returns a passthrough immediately when {@code erd.agent.repair.enabled=false} or the HTML is
 * blank after bare-HTML promotion.
 *
 * <p>Repair/retry blocking work runs on the caller's {@code Schedulers.boundedElastic} subscription
 * thread — the {@link Flux#defer} structure guarantees {@code .block()} is never called on a
 * non-blocking thread.
 */
@Component
@ConditionalOnProperty(
    prefix = "erd.agent",
    name = "provider",
    havingValue = "openai-compatible",
    matchIfMissing = true)
@RequiredArgsConstructor
@Slf4j
public class GenerationRepairGuard {

  /** Inline replacement text substituted when a bare HTML document is promoted from answer text. */
  private static final String BARE_HTML_REPLACEMENT_TEXT = "（儀表板已生成 → 右側面板）";

  /** Step key for the single repair/retry step emitted by this guard. */
  private static final String REPAIR_STEP_KEY = "r1";

  /** r1 step title while the omission retry is running. */
  private static final String REPAIR_STEP_OMISSION_RUNNING = "偵測到程式碼省略，重新生成中";

  /** r1 step title on successful omission fix. */
  private static final String REPAIR_STEP_OMISSION_SUCCESS = "程式碼省略已修復";

  /** r1 step title when the omission retry fails. */
  private static final String REPAIR_STEP_OMISSION_FAILURE = "程式碼省略修復失敗";

  /** r1 step title format while JS syntax repair is running; {@code %d} = error count. */
  private static final String REPAIR_STEP_JS_RUNNING_FORMAT = "偵測到 %d 個 JS 問題，自動修復中";

  /** r1 step title format on successful JS repair; {@code %d} = fixed error count. */
  private static final String REPAIR_STEP_JS_SUCCESS_FORMAT = "JS 問題修復完成（%d 個）";

  /** r1 step title when JS repair fails. */
  private static final String REPAIR_STEP_JS_FAILURE_LABEL = "JS 問題修復失敗";

  /** r1 step detail format showing remaining unfixed error count; {@code %d} = remaining count. */
  private static final String REPAIR_STEP_JS_REMAINING_FORMAT = "%d 個問題未修復";

  /** r1 step detail emitted when a repair or retry call throws an exception. */
  private static final String REPAIR_FAILURE_DETAIL = "修復失敗";

  private final JsSyntaxValidator jsSyntaxValidator;
  private final CodeOmissionValidator codeOmissionValidator;
  private final GenerationRepairer generationRepairer;
  private final AgentProperties agentProperties;

  /**
   * Applies quality hardening to the outcome for a newly generated HTML artifact.
   *
   * @param sessionId session identifier (for logging)
   * @param request the original agent request (files and previous HTML are reused by repair calls)
   * @param outcome the outcome from the provider's generate() call
   * @param generator the LLM call function from the provider (passed through to {@link
   *     GenerationRepairer} to avoid a bean cycle — the provider passes {@code this::generate})
   * @return a {@link RepairResult} whose events flux carries live r1 step events and whose output
   *     future completes when (or before) events completes
   */
  public RepairResult harden(
      String sessionId,
      AgentRequest request,
      AgentOutcome outcome,
      Function<AgentRequest, ProviderResult> generator) {

    // Step 1: bare-HTML fallback, applied before validators. A model that emits a full HTML
    // document without a ```html fence has its output promoted to the artifact channel and
    // stripped from the answer text.
    String html = outcome.html();
    String answerText = outcome.answerText();
    if (!StringUtils.hasText(html)) {
      String bareHtml = BareHtmlUtils.extract(answerText);
      if (bareHtml != null) {
        html = bareHtml;
        answerText = answerText.replace(bareHtml, BARE_HTML_REPLACEMENT_TEXT).trim();
      }
    }

    // Step 2: when repair is disabled or there is still no HTML, return passthrough immediately.
    if (!StringUtils.hasText(html)
        || agentProperties.repair() == null
        || !agentProperties.repair().enabled()) {
      return new RepairResult(
          Flux.empty(), CompletableFuture.completedFuture(new HardenedOutput(html, answerText)));
    }

    // Step 3: run validators. Exceptions are swallowed (warn log) so a broken GraalVM or
    // misconfigured validator never blocks the main flow.
    List<JsSyntaxError> jsErrors;
    try {
      jsErrors = jsSyntaxValidator.validate(html);
    } catch (Exception validatorException) {
      log.warn(
          "JS validator threw exception for session {}, skipping repair: {}",
          sessionId,
          validatorException.getMessage());
      jsErrors = List.of();
    }

    List<CodeOmissionFinding> omissionFindings;
    try {
      omissionFindings = codeOmissionValidator.validate(html, request.previousArtifactHtml());
    } catch (Exception validatorException) {
      log.warn(
          "code omission validator threw exception for session {}, skipping: {}",
          sessionId,
          validatorException.getMessage());
      omissionFindings = List.of();
    }

    // Step 4: omission takes priority over JS errors — a full re-generation fixes both.
    if (!omissionFindings.isEmpty()) {
      return buildOmissionRepairResult(
          sessionId, request, html, answerText, omissionFindings, generator);
    }

    // Step 5: JS syntax repair.
    if (!jsErrors.isEmpty()) {
      return buildJsRepairResult(sessionId, request, html, answerText, jsErrors, generator);
    }

    // Step 6: no issues detected — passthrough with possibly bare-html-promoted values.
    return new RepairResult(
        Flux.empty(), CompletableFuture.completedFuture(new HardenedOutput(html, answerText)));
  }

  private RepairResult buildOmissionRepairResult(
      String sessionId,
      AgentRequest request,
      String html,
      String answerText,
      List<CodeOmissionFinding> omissionFindings,
      Function<AgentRequest, ProviderResult> generator) {

    log.info(
        "code omission detected session={} findingCount={}", sessionId, omissionFindings.size());

    StepEvent r1Running =
        new StepEvent(REPAIR_STEP_KEY, REPAIR_STEP_OMISSION_RUNNING, null, StepStatus.RUNNING);

    CompletableFuture<HardenedOutput> outputFuture = new CompletableFuture<>();

    // Emit r1 RUNNING immediately, then defer the blocking retry work.
    // The deferred lambda runs on the caller's boundedElastic subscription, so .block() is legal.
    Flux<AgentEvent> events =
        Flux.concat(
            Flux.just((AgentEvent) r1Running),
            Flux.defer(
                () -> {
                  String resultHtml = html;
                  StepEvent terminalR1;

                  // Outer try-finally guarantees the output future completes even if a
                  // java.lang.Error (e.g. OOM, Reactor's BlockingNotPermittedException) bypasses
                  // catch(Exception) below — otherwise the orchestrator's output().join() blocks a
                  // boundedElastic thread forever.
                  try {
                    try {
                      RepairOutcome outcome =
                          generationRepairer
                              .retryForOmission(
                                  generator, sessionId, omissionFindings, request, html)
                              .block();
                      if (outcome != null && outcome.passed()) {
                        resultHtml = outcome.html();
                        terminalR1 =
                            new StepEvent(
                                REPAIR_STEP_KEY,
                                REPAIR_STEP_OMISSION_SUCCESS,
                                null,
                                StepStatus.SUCCESS);
                      } else {
                        terminalR1 =
                            new StepEvent(
                                REPAIR_STEP_KEY,
                                REPAIR_STEP_OMISSION_FAILURE,
                                null,
                                StepStatus.ERROR);
                      }
                    } catch (Exception repairException) {
                      log.error(
                          "retryForOmission failed for session {}: {}",
                          sessionId,
                          repairException.getMessage(),
                          repairException);
                      terminalR1 =
                          new StepEvent(
                              REPAIR_STEP_KEY,
                              REPAIR_STEP_OMISSION_FAILURE,
                              REPAIR_FAILURE_DETAIL,
                              StepStatus.ERROR);
                    }

                    // Complete the output future BEFORE emitting the terminal step so the
                    // orchestrator can read hardenedOutput as soon as events completes.
                    outputFuture.complete(new HardenedOutput(resultHtml, answerText));
                    return Flux.just((AgentEvent) terminalR1);
                  } finally {
                    if (!outputFuture.isDone()) {
                      outputFuture.completeExceptionally(
                          new IllegalStateException(
                              "generation repair aborted before completing output"));
                    }
                  }
                }));

    return new RepairResult(events, outputFuture);
  }

  private RepairResult buildJsRepairResult(
      String sessionId,
      AgentRequest request,
      String html,
      String answerText,
      List<JsSyntaxError> jsErrors,
      Function<AgentRequest, ProviderResult> generator) {

    int errorCount = jsErrors.size();
    log.info("JS syntax repair triggered session={} errorCount={}", sessionId, errorCount);

    StepEvent r1Running =
        new StepEvent(
            REPAIR_STEP_KEY,
            String.format(REPAIR_STEP_JS_RUNNING_FORMAT, errorCount),
            null,
            StepStatus.RUNNING);

    CompletableFuture<HardenedOutput> outputFuture = new CompletableFuture<>();

    // Emit r1 RUNNING immediately, then defer the blocking repair work.
    // The deferred lambda runs on the caller's boundedElastic subscription, so .block() is legal.
    Flux<AgentEvent> events =
        Flux.concat(
            Flux.just((AgentEvent) r1Running),
            Flux.defer(
                () -> {
                  String resultHtml = html;
                  StepEvent terminalR1;

                  // Outer try-finally guarantees the output future completes even if a
                  // java.lang.Error (e.g. OOM, Reactor's BlockingNotPermittedException) bypasses
                  // catch(Exception) below — otherwise the orchestrator's output().join() blocks a
                  // boundedElastic thread forever.
                  try {
                    try {
                      RepairOutcome outcome =
                          generationRepairer
                              .repair(generator, sessionId, html, jsErrors, request)
                              .block();
                      if (outcome != null && outcome.passed()) {
                        resultHtml = outcome.html();
                        terminalR1 =
                            new StepEvent(
                                REPAIR_STEP_KEY,
                                String.format(REPAIR_STEP_JS_SUCCESS_FORMAT, errorCount),
                                null,
                                StepStatus.SUCCESS);
                      } else {
                        int remaining =
                            (outcome != null && outcome.errorsAfter() != null)
                                ? outcome.errorsAfter().size()
                                : errorCount;
                        terminalR1 =
                            new StepEvent(
                                REPAIR_STEP_KEY,
                                REPAIR_STEP_JS_FAILURE_LABEL,
                                String.format(REPAIR_STEP_JS_REMAINING_FORMAT, remaining),
                                StepStatus.ERROR);
                      }
                    } catch (Exception repairException) {
                      log.error(
                          "Repair call failed for session {}: {}",
                          sessionId,
                          repairException.getMessage(),
                          repairException);
                      terminalR1 =
                          new StepEvent(
                              REPAIR_STEP_KEY,
                              REPAIR_STEP_JS_FAILURE_LABEL,
                              REPAIR_FAILURE_DETAIL,
                              StepStatus.ERROR);
                    }

                    // Complete the output future BEFORE emitting the terminal step so the
                    // orchestrator can read hardenedOutput as soon as events completes.
                    outputFuture.complete(new HardenedOutput(resultHtml, answerText));
                    return Flux.just((AgentEvent) terminalR1);
                  } finally {
                    if (!outputFuture.isDone()) {
                      outputFuture.completeExceptionally(
                          new IllegalStateException(
                              "generation repair aborted before completing output"));
                    }
                  }
                }));

    return new RepairResult(events, outputFuture);
  }
}
