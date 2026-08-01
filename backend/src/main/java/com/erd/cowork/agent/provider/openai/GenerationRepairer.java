package com.erd.cowork.agent.provider.openai;

import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.ProviderResult;
import java.io.StringWriter;
import java.util.List;
import java.util.function.Function;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.velocity.VelocityContext;
import org.apache.velocity.app.VelocityEngine;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import reactor.core.publisher.Mono;

/**
 * Performs generation-time repair and omission-retry for the openai-compatible provider path.
 *
 * <p>{@link #repair} fixes JS syntax errors via a targeted prompt; {@link #retryForOmission}
 * re-generates HTML when placeholder-comment omissions are detected. Both take a {@code generator}
 * parameter (a {@link Function}{@code <AgentRequest, ProviderResult>}) instead of injecting {@link
 * com.erd.cowork.agent.provider.DashboardAgentProvider} directly, to avoid a bean cycle.
 *
 * <p>Provider events consumed during repair/retry are discarded — only {@link RepairOutcome} is
 * returned. Each returned {@link Mono} is cold and must be subscribed on {@code
 * Schedulers.boundedElastic}.
 */
@Component
@ConditionalOnProperty(
    prefix = "erd.agent",
    name = "provider",
    havingValue = "openai-compatible",
    matchIfMissing = true)
@RequiredArgsConstructor
@Slf4j
public class GenerationRepairer {

  private static final String SYNTAX_REPAIR_PROMPT_TEMPLATE =
      "templates/openai/syntax-repair-prompt.vm";

  private static final String OMISSION_RETRY_WARNING_TEMPLATE =
      "templates/openai/omission-retry-warning.vm";

  private final JsSyntaxValidator jsSyntaxValidator;
  private final CodeOmissionValidator codeOmissionValidator;
  private final VelocityEngine velocityEngine;

  /**
   * Attempts to repair {@code brokenHtml} by calling the generator with a repair instruction.
   *
   * @param generator the LLM call function (first parameter); {@code this::generate} from the
   *     provider — passed here instead of injected to avoid a bean cycle
   * @param sessionId session identifier (for logging)
   * @param brokenHtml original HTML that failed validation
   * @param errors errors found by {@link JsSyntaxValidator} — used to build the prompt
   * @param originalRequest the original request whose files/userId/sessionId are reused
   * @return a Mono that emits a single {@link RepairOutcome}
   */
  public Mono<RepairOutcome> repair(
      Function<AgentRequest, ProviderResult> generator,
      String sessionId,
      String brokenHtml,
      List<JsSyntaxError> errors,
      AgentRequest originalRequest) {

    String repairPrompt = buildRepairPrompt(errors);
    AgentRequest repairRequest =
        new AgentRequest(
            originalRequest.userId(),
            sessionId,
            repairPrompt,
            List.of(), // empty history — repair turn is self-contained
            originalRequest.files(), // carry original files so provider has schema context
            brokenHtml); // previousArtifactHtml = broken html

    // Wrap in Mono.defer so generator fires at subscription time (cold-Mono contract).
    return Mono.defer(
        () -> {
          ProviderResult providerResult = generator.apply(repairRequest);

          // Consume the entire event stream (TOKEN/THINKING/STEP all discarded — not forwarded).
          // Extraction supplier is populated as a side-effect of consuming the stream.
          return providerResult
              .events()
              .then(
                  Mono.fromCallable(
                      () -> {
                        AgentOutcome outcome = providerResult.outcome().get();
                        String repairedHtml = outcome.html();

                        if (!StringUtils.hasText(repairedHtml)) {
                          log.warn(
                              "repair session={}: provider returned no HTML, keeping original",
                              sessionId);
                          return new RepairOutcome(brokenHtml, false, errors, errors);
                        }

                        List<JsSyntaxError> errorsAfter = jsSyntaxValidator.validate(repairedHtml);
                        boolean passed = errorsAfter.isEmpty();
                        log.info(
                            "repair session={} passed={} errorsBefore={} errorsAfter={}",
                            sessionId,
                            passed,
                            errors.size(),
                            errorsAfter.size());
                        return new RepairOutcome(
                            passed ? repairedHtml : brokenHtml, passed, errors, errorsAfter);
                      }));
        });
  }

  /**
   * Re-generates the HTML to fix code omissions detected by {@link CodeOmissionValidator}.
   *
   * <p>Unlike {@link #repair}, replays the original request verbatim with a concise omission
   * warning appended, rather than sending a targeted syntax fix. Passes only when both {@link
   * JsSyntaxValidator} and {@link CodeOmissionValidator} find zero issues afterward; on failure
   * {@code htmlWithOmissions} is returned unchanged.
   *
   * @param generator the LLM call function; passed instead of injected to avoid a bean cycle
   * @param sessionId session identifier (for logging)
   * @param findings omission findings that triggered this retry
   * @param originalRequest the original agent request whose fields are reused unchanged
   * @param htmlWithOmissions the HTML that contained omission placeholders; returned as-is on
   *     failure so callers always receive a usable artifact
   * @return a Mono that emits a single {@link RepairOutcome}
   */
  public Mono<RepairOutcome> retryForOmission(
      Function<AgentRequest, ProviderResult> generator,
      String sessionId,
      List<CodeOmissionFinding> findings,
      AgentRequest originalRequest,
      String htmlWithOmissions) {

    String omissionWarning = buildOmissionRetryWarning(findings);
    AgentRequest retryRequest =
        new AgentRequest(
            originalRequest.userId(),
            sessionId,
            originalRequest.question() + omissionWarning,
            originalRequest.history(), // original conversation history preserved
            originalRequest.files(), // original files preserved
            originalRequest.previousArtifactHtml()); // original previous artifact preserved

    // Wrap in Mono.defer so generator fires at subscription time (cold-Mono contract).
    return Mono.defer(
        () -> {
          ProviderResult providerResult = generator.apply(retryRequest);
          return providerResult
              .events()
              .then(
                  Mono.fromCallable(
                      () -> {
                        AgentOutcome outcome = providerResult.outcome().get();
                        String retriedHtml = outcome.html();

                        if (!StringUtils.hasText(retriedHtml)) {
                          log.warn(
                              "retryForOmission session={}: provider returned no HTML, keeping"
                                  + " original",
                              sessionId);
                          return new RepairOutcome(htmlWithOmissions, false, List.of(), List.of());
                        }

                        // Dual validation: zero syntax errors AND zero omissions → passed.
                        List<JsSyntaxError> syntaxErrorsAfter =
                            jsSyntaxValidator.validate(retriedHtml);
                        List<CodeOmissionFinding> omissionsAfter =
                            codeOmissionValidator.validate(
                                retriedHtml, originalRequest.previousArtifactHtml());
                        boolean passed = syntaxErrorsAfter.isEmpty() && omissionsAfter.isEmpty();
                        log.info(
                            "retryForOmission session={} passed={} findingsBefore={}"
                                + " syntaxErrorsAfter={} omissionsAfter={}",
                            sessionId,
                            passed,
                            findings.size(),
                            syntaxErrorsAfter.size(),
                            omissionsAfter.size());
                        return new RepairOutcome(
                            passed ? retriedHtml : htmlWithOmissions,
                            passed,
                            List.of(),
                            syntaxErrorsAfter);
                      }));
        });
  }

  // ── prompt builders ───────────────────────────────────────────────────────

  /**
   * Renders the syntax-repair prompt via Velocity, listing each error in {@code script#N 第 X
   * 行：message} format. The {@code scriptNum} (1-based) is computed inside the template via {@code
   * #set($scriptNum = $error.scriptIndex() + 1)}.
   */
  private String buildRepairPrompt(List<JsSyntaxError> errors) {
    VelocityContext ctx = new VelocityContext();
    ctx.put("errors", errors);
    StringWriter writer = new StringWriter();
    velocityEngine.mergeTemplate(SYNTAX_REPAIR_PROMPT_TEMPLATE, "UTF-8", ctx, writer);
    return writer.toString();
  }

  /**
   * Renders the omission-retry warning via Velocity. The first finding's {@link
   * CodeOmissionFinding#commentText()} is truncated to 80 characters in Java before being passed to
   * the template as {@code $firstFindingSnippet}; {@code $findingCount} is the total count.
   */
  private String buildOmissionRetryWarning(List<CodeOmissionFinding> findings) {
    String firstFindingSnippet =
        findings.isEmpty()
            ? ""
            : findings.get(0).commentText().length() > 80
                ? findings.get(0).commentText().substring(0, 80)
                : findings.get(0).commentText();
    VelocityContext ctx = new VelocityContext();
    ctx.put("firstFindingSnippet", firstFindingSnippet);
    ctx.put("findingCount", findings.size());
    StringWriter writer = new StringWriter();
    velocityEngine.mergeTemplate(OMISSION_RETRY_WARNING_TEMPLATE, "UTF-8", ctx, writer);
    return writer.toString();
  }
}
