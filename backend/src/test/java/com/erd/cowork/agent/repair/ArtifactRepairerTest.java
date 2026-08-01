package com.erd.cowork.agent.repair;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.model.AgentFileContext;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.provider.analysis.AnalysisBrowserRepairClient;
import com.erd.cowork.exception.BrowserRepairUnsupportedException;
import java.util.List;
import java.util.Optional;
import org.apache.velocity.app.VelocityEngine;
import org.apache.velocity.runtime.RuntimeConstants;
import org.apache.velocity.runtime.resource.loader.ClasspathResourceLoader;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/**
 * Tests for {@link ArtifactRepairer} — browser-error repair path only.
 *
 * <p>Generation-repair tests (repair / retryForOmission) are in {@code
 * com.erd.cowork.agent.provider.openai.GenerationRepairerTest}.
 */
@ExtendWith(MockitoExtension.class)
class ArtifactRepairerTest {

  @Mock private DashboardAgentProvider provider;
  @Mock private AnalysisBrowserRepairClient analysisBrowserRepairClient;

  private ArtifactRepairer repairer;
  private VelocityEngine velocityEngine;

  /** Syntactically broken HTML — would fail GraalJS, but browser-repair no longer re-validates. */
  private static final String BROKEN_HTML =
      "<html><script>function broken( {</script></html>"; // unclosed paren — real GraalJS error

  private static final String REPAIRED_HTML = "<html><script>function fixed() {}</script></html>";

  @BeforeEach
  void setUp() {
    velocityEngine = new VelocityEngine();
    velocityEngine.setProperty(RuntimeConstants.RESOURCE_LOADERS, "classpath");
    velocityEngine.setProperty(
        "resource.loader.classpath.class", ClasspathResourceLoader.class.getName());
    velocityEngine.setProperty(RuntimeConstants.INPUT_ENCODING, "UTF-8");
    velocityEngine.init();
    repairer = new ArtifactRepairer(Optional.of(provider), Optional.empty(), velocityEngine);
  }

  private AgentRequest dummyRequest() {
    return new AgentRequest("user-1", "session-1", "", List.of(), List.of(), null);
  }

  // ── availability: Optional<DashboardAgentProvider> absent (spec §16.2.1 — no repair-capable
  // provider active, e.g. langgraph-analysis mode) ───────────────────────────────────────────

  @Test
  void isBrowserRepairSupported_providerPresent_returnsTrue() {
    assertThat(repairer.isBrowserRepairSupported()).isTrue();
  }

  @Test
  void isBrowserRepairSupported_bothAbsent_returnsFalse() {
    ArtifactRepairer unsupportedRepairer =
        new ArtifactRepairer(Optional.empty(), Optional.empty(), velocityEngine);
    assertThat(unsupportedRepairer.isBrowserRepairSupported()).isFalse();
  }

  @Test
  void isBrowserRepairSupported_onlyAnalysisClientPresent_returnsTrue() {
    ArtifactRepairer analysisRepairer =
        new ArtifactRepairer(
            Optional.empty(), Optional.of(analysisBrowserRepairClient), velocityEngine);
    assertThat(analysisRepairer.isBrowserRepairSupported()).isTrue();
  }

  @Test
  void repairWithBrowserErrors_bothAbsent_throwsBrowserRepairUnsupported() {
    ArtifactRepairer unsupportedRepairer =
        new ArtifactRepairer(Optional.empty(), Optional.empty(), velocityEngine);

    assertThatThrownBy(
            () ->
                unsupportedRepairer
                    .repairWithBrowserErrors(
                        "session-1",
                        BROKEN_HTML,
                        List.of(new BrowserJsError("err", 1, 0)),
                        dummyRequest())
                    .block())
        .isInstanceOf(BrowserRepairUnsupportedException.class);
  }

  // ── analysis-mode path: DashboardAgentProvider absent, AnalysisBrowserRepairClient present ──

  @Test
  void repairWithBrowserErrors_dashboardAbsentAnalysisClientPresent_delegatesToAnalysisClient() {
    ArtifactRepairer analysisRepairer =
        new ArtifactRepairer(
            Optional.empty(), Optional.of(analysisBrowserRepairClient), velocityEngine);
    BrowserRepairOutcome analysisOutcome = new BrowserRepairOutcome(REPAIRED_HTML, true);
    when(analysisBrowserRepairClient.repair(eq("session-1"), eq("u1"), eq(BROKEN_HTML), any()))
        .thenReturn(Mono.just(analysisOutcome));

    AgentRequest baseRequest = new AgentRequest("u1", "session-1", "", List.of(), List.of(), null);
    BrowserRepairOutcome outcome =
        analysisRepairer
            .repairWithBrowserErrors(
                "session-1", BROKEN_HTML, List.of(new BrowserJsError("err", 1, 0)), baseRequest)
            .block();

    assertThat(outcome).isEqualTo(analysisOutcome);
    verify(analysisBrowserRepairClient).repair(eq("session-1"), eq("u1"), eq(BROKEN_HTML), any());
    // Dashboard provider must never be touched on this path.
    verify(provider, never()).generate(any());
  }

  @Test
  void repairWithBrowserErrors_bothPresent_prefersDashboardProvider() {
    // When both a DashboardAgentProvider and an AnalysisBrowserRepairClient are present
    // (shouldn't happen in practice — the two are mutually exclusive @ConditionalOnProperty
    // beans — but the routing order itself should still be deterministic), the dashboard
    // provider path takes precedence and the analysis client is never invoked.
    ArtifactRepairer bothPresentRepairer =
        new ArtifactRepairer(
            Optional.of(provider), Optional.of(analysisBrowserRepairClient), velocityEngine);
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", REPAIRED_HTML, null)));

    BrowserRepairOutcome outcome =
        bothPresentRepairer
            .repairWithBrowserErrors(
                "session-1", BROKEN_HTML, List.of(new BrowserJsError("err", 1, 0)), dummyRequest())
            .block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isTrue();
    verify(analysisBrowserRepairClient, never()).repair(any(), any(), any(), any());
  }

  // ── B1: provider returns non-blank HTML → passed=true (no GraalJS re-validation) ──────────

  @Test
  void repairWithBrowserErrors_nonBlankHtml_passesWithoutValidation() {
    // Provider returns HTML that would FAIL GraalJS (broken syntax), but browser-repair success
    // criterion is "non-blank HTML returned" — GraalJS re-validation was intentionally removed.
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.just(new TokenEvent("repair-token")),
                () -> new AgentOutcome("", BROKEN_HTML, null)));

    BrowserRepairOutcome outcome =
        repairer
            .repairWithBrowserErrors(
                "session-1", BROKEN_HTML, List.of(new BrowserJsError("err", 1, 0)), dummyRequest())
            .block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isTrue(); // non-blank → passed, even though JS is still broken
    assertThat(outcome.html()).isEqualTo(BROKEN_HTML);
  }

  // ── B2: provider returns valid repaired HTML → passed=true ────────────────

  @Test
  void repairWithBrowserErrors_providerReturnsFixedHtml_outcomePassed() {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", REPAIRED_HTML, null)));

    BrowserRepairOutcome outcome =
        repairer
            .repairWithBrowserErrors(
                "session-1", BROKEN_HTML, List.of(new BrowserJsError("err", 1, 0)), dummyRequest())
            .block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isTrue();
    assertThat(outcome.html()).isEqualTo(REPAIRED_HTML);
  }

  // ── B3: provider returns no HTML → passed=false, original html returned ───

  @Test
  void repairWithBrowserErrors_providerReturnsNoHtml_outcomeNotPassed_originalHtmlReturned() {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.empty(), () -> new AgentOutcome("answer text only", null, null)));

    BrowserRepairOutcome outcome =
        repairer
            .repairWithBrowserErrors(
                "session-1", BROKEN_HTML, List.of(new BrowserJsError("err", 1, 0)), dummyRequest())
            .block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isFalse();
    assertThat(outcome.html()).isEqualTo(BROKEN_HTML); // fallback to original on blank
  }

  // ── B4: repair request carries browser errors and original files ───────────

  @Test
  void repairWithBrowserErrors_repairRequestContainsBrowserErrorPromptAndFiles() {
    AgentRequest baseRequest =
        new AgentRequest(
            "u1",
            "s1",
            "",
            List.of(),
            List.of(
                new AgentFileContext(
                    "alias1", "data.csv", "text/csv", "storage/key/test.csv", null)),
            BROKEN_HTML);

    ArgumentCaptor<AgentRequest> requestCaptor = ArgumentCaptor.forClass(AgentRequest.class);
    when(provider.generate(requestCaptor.capture()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", REPAIRED_HTML, null)));

    repairer
        .repairWithBrowserErrors(
            "s1",
            BROKEN_HTML,
            List.of(new BrowserJsError("TypeError: x is undefined", 42, 0)),
            baseRequest)
        .block();

    AgentRequest repairRequest = requestCaptor.getValue();
    // Repair prompt must mention browser errors and include the suffix
    assertThat(repairRequest.question()).contains("TypeError: x is undefined");
    assertThat(repairRequest.question()).contains("只修列出問題，其餘逐字保留，輸出完整 HTML");
    // Files from original request are carried through
    assertThat(repairRequest.files()).hasSize(1);
    assertThat(repairRequest.files().get(0).alias()).isEqualTo("alias1");
    // previousArtifactHtml is set to brokenHtml so the provider has context
    assertThat(repairRequest.previousArtifactHtml()).isEqualTo(BROKEN_HTML);
  }

  // ── B5: content-pinning — exact prompt string for 2-error case ────────────

  @Test
  void repairWithBrowserErrors_exactPromptContent_2errors() {
    List<BrowserJsError> errors =
        List.of(
            new BrowserJsError("TypeError: x is undefined", 42, 0),
            new BrowserJsError("ReferenceError: y is not defined", 10, 5));

    String expected =
        "以下 HTML 在瀏覽器執行時發生 JavaScript 錯誤，請修復：\n"
            + "\n"
            + "第 42 行：TypeError: x is undefined\n"
            + "第 10 行：ReferenceError: y is not defined\n"
            + "\n"
            + "只修列出問題，其餘逐字保留，輸出完整 HTML。";

    ArgumentCaptor<AgentRequest> captor = ArgumentCaptor.forClass(AgentRequest.class);
    when(provider.generate(captor.capture()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", REPAIRED_HTML, null)));

    repairer.repairWithBrowserErrors("s1", BROKEN_HTML, errors, dummyRequest()).block();

    assertThat(captor.getValue().question()).isEqualTo(expected);
  }
}
