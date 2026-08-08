package com.erd.cowork.agent.repair;

import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.provider.analysis.AnalysisBrowserRepairClient;
import com.erd.cowork.exception.BrowserRepairUnsupportedException;
import com.erd.cowork.logging.LogAnnotation;
import java.io.StringWriter;
import java.util.List;
import java.util.Optional;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.velocity.VelocityContext;
import org.apache.velocity.app.VelocityEngine;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import reactor.core.publisher.Mono;

/**
 * Repairs a broken HTML artifact based on runtime browser JavaScript errors, routing to whichever
 * repair path ({@link DashboardAgentProvider} or {@link AnalysisBrowserRepairClient}) is currently
 * active.
 *
 * <p>Success criterion: the provider returned non-blank HTML. No GraalJS re-validation is performed
 * — runtime browser errors are a different failure class than static syntax errors, so "got
 * non-blank HTML back" is the success bar here. JS-syntax/code-omission repair is
 * openai-compatible–specific and lives in {@code agent/provider/openai/}; the analysis-mode path's
 * success criterion is deepagent-service's own guard re-check instead (see {@link
 * AnalysisBrowserRepairClient}).
 *
 * <p>Both {@code DashboardAgentProvider} and {@link AnalysisBrowserRepairClient} are injected as
 * {@link Optional} since each is only present under its respective provider mode. {@link
 * #isBrowserRepairSupported()} lets callers check availability before invoking {@link
 * #repairWithBrowserErrors}.
 *
 * <p>The returned {@link Mono} is cold: subscribe to start the work; blocking is safe only on
 * {@code Schedulers.boundedElastic} threads.
 */
@Component
@RequiredArgsConstructor
@Slf4j
@LogAnnotation
public class ArtifactRepairer {

  private static final String BROWSER_REPAIR_PROMPT_TEMPLATE =
      "templates/repair/browser-repair-prompt.vm";

  private final Optional<DashboardAgentProvider> provider;
  private final Optional<AnalysisBrowserRepairClient> analysisBrowserRepairClient;
  private final VelocityEngine velocityEngine;

  /**
   * Whether browser-error repair is available in the currently active provider mode: {@code true}
   * when either a {@link DashboardAgentProvider} or an {@link AnalysisBrowserRepairClient} is
   * present, {@code false} otherwise.
   */
  public boolean isBrowserRepairSupported() {
    return provider.isPresent() || analysisBrowserRepairClient.isPresent();
  }

  /**
   * Attempts to repair {@code brokenHtml} based on runtime browser JavaScript errors.
   *
   * <p>Builds a repair prompt listing each browser error on its own line (maximum 10), then calls
   * the active provider for a fix. The returned {@link Mono} is cold — subscribe to start the
   * repair.
   *
   * @param sessionId session identifier (for logging)
   * @param brokenHtml original HTML that produced runtime errors
   * @param errors runtime errors reported by the browser iframe (at most 10 are used)
   * @param baseRequest the request whose userId and files are reused for provider context
   * @return a Mono that emits a single {@link BrowserRepairOutcome}, or errors with {@link
   *     BrowserRepairUnsupportedException} at subscription time if neither path is active — callers
   *     should check {@link #isBrowserRepairSupported()} first
   */
  public Mono<BrowserRepairOutcome> repairWithBrowserErrors(
      String sessionId, String brokenHtml, List<BrowserJsError> errors, AgentRequest baseRequest) {

    // Guard deferred (not evaluated until subscription) to preserve the class's cold-Mono
    // contract: callers who never subscribe never trigger any side effect, including this throw.
    // Callers should prefer checking isBrowserRepairSupported() before calling at all.
    return Mono.defer(
        () -> {
          if (provider.isPresent()) {
            return repairViaDashboardProvider(sessionId, brokenHtml, errors, baseRequest);
          }
          if (analysisBrowserRepairClient.isPresent()) {
            return analysisBrowserRepairClient
                .get()
                .repair(sessionId, baseRequest.userId(), brokenHtml, errors);
          }
          throw new BrowserRepairUnsupportedException(
              "Browser-error repair is not supported by the active provider (session "
                  + sessionId
                  + ")");
        });
  }

  private Mono<BrowserRepairOutcome> repairViaDashboardProvider(
      String sessionId, String brokenHtml, List<BrowserJsError> errors, AgentRequest baseRequest) {
    DashboardAgentProvider activeProvider = provider.get();

    String repairPrompt = buildBrowserRepairPrompt(errors);
    AgentRequest repairRequest =
        new AgentRequest(
            baseRequest.userId(),
            sessionId,
            repairPrompt,
            List.of(),
            baseRequest.files(),
            brokenHtml);

    ProviderResult providerResult = activeProvider.generate(repairRequest);
    return providerResult
        .events()
        .then(
            Mono.fromCallable(
                () -> {
                  AgentOutcome outcome = providerResult.outcome().get();
                  String repairedHtml = outcome.html();

                  if (!StringUtils.hasText(repairedHtml)) {
                    log.warn(
                        "repairWithBrowserErrors session={}: provider returned no HTML, keeping"
                            + " original",
                        sessionId);
                    return new BrowserRepairOutcome(brokenHtml, false);
                  }

                  // Non-blank HTML counts as success — no GraalJS re-validation (intentional).
                  log.info(
                      "repairWithBrowserErrors session={} passed=true browserErrors={}",
                      sessionId,
                      errors.size());
                  return new BrowserRepairOutcome(repairedHtml, true);
                }));
  }

  private String buildBrowserRepairPrompt(List<BrowserJsError> errors) {
    List<BrowserJsError> cappedErrors = errors.size() > 10 ? errors.subList(0, 10) : errors;
    VelocityContext ctx = new VelocityContext();
    ctx.put("errors", cappedErrors);
    StringWriter writer = new StringWriter();
    velocityEngine.mergeTemplate(BROWSER_REPAIR_PROMPT_TEMPLATE, "UTF-8", ctx, writer);
    return writer.toString();
  }
}
