package com.erd.cowork.agent.provider.openai;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.HardenedOutput;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.provider.RepairResult;
import com.erd.cowork.config.AgentProperties;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.Mockito;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class GenerationRepairGuardTest {

  /** HTML with genuinely broken JS (unclosed brace) — triggers real GraalJS error. */
  private static final String BROKEN_HTML =
      "<!DOCTYPE html><html><head></head><body><script>const x = {</script></body></html>";

  /** Syntactically valid repaired HTML. */
  private static final String REPAIRED_HTML =
      "<!DOCTYPE html><html><head></head><body><script>const x = {};</script></body></html>";

  /**
   * HTML with a placeholder-comment omission (syntactically valid). The JS line comment matches the
   * ZH patterns "圖表程式略" and "保留原本".
   */
  private static final String OMISSION_HTML =
      "<!DOCTYPE html><html><head></head><body>"
          + "<script>\n"
          + "// (其他 KPI 及圖表程式略，保留原本結構)\n"
          + "const x = {};\n"
          + "</script></body></html>";

  /** Clean HTML with no omission comments and no syntax errors. */
  private static final String CLEAN_HTML =
      "<!DOCTYPE html><html><head></head><body>"
          + "<script>const x = {};</script>"
          + "</body></html>";

  /** A previous artifact long enough for the shrinkage gate to open on OMISSION_HTML. */
  private static final String LONG_PREVIOUS_HTML = "x".repeat(OMISSION_HTML.length() * 3);

  @Mock private JsSyntaxValidator jsSyntaxValidator;
  @Mock private CodeOmissionValidator codeOmissionValidator;
  @Mock private GenerationRepairer generationRepairer;

  /** Stub generator — returned by provider mock; identity doesn't matter for guard tests. */
  @Mock private Function<AgentRequest, ProviderResult> generator;

  private AgentRequest baseRequest;
  private AgentRequest requestWithPreviousArtifact;

  @BeforeEach
  void setUp() {
    baseRequest =
        new AgentRequest("user-1", "session-1", "build dashboard", List.of(), List.of(), null);
    requestWithPreviousArtifact =
        new AgentRequest(
            "user-1", "session-1", "build dashboard", List.of(), List.of(), LONG_PREVIOUS_HTML);
  }

  private GenerationRepairGuard guardWithRepairEnabled() {
    AgentProperties.Repair repairConfig = new AgentProperties.Repair(true);
    AgentProperties props = new AgentProperties(null, null, repairConfig);
    return new GenerationRepairGuard(
        jsSyntaxValidator, codeOmissionValidator, generationRepairer, props);
  }

  private GenerationRepairGuard guardWithRepairDisabled() {
    AgentProperties.Repair repairConfig = new AgentProperties.Repair(false);
    AgentProperties props = new AgentProperties(null, null, repairConfig);
    return new GenerationRepairGuard(
        jsSyntaxValidator, codeOmissionValidator, generationRepairer, props);
  }

  // ── GD1: repair disabled → passthrough, validators never called ───────────

  @Test
  void harden_repairDisabled_returnsPassthrough_validatorsNeverCalled() {
    GenerationRepairGuard guard = guardWithRepairDisabled();
    AgentOutcome extraction = new AgentOutcome("answer", BROKEN_HTML, null);

    RepairResult result = guard.harden("session-1", baseRequest, extraction, generator);
    HardenedOutput output = result.output().join();

    assertThat(result.events().collectList().block()).isEmpty();
    assertThat(output.html()).isEqualTo(BROKEN_HTML);
    assertThat(output.answerText()).isEqualTo("answer");

    Mockito.verify(jsSyntaxValidator, never()).validate(anyString());
    Mockito.verify(codeOmissionValidator, never()).validate(anyString(), any());
  }

  // ── GD2: valid JS → passthrough, no r1 steps ──────────────────────────────

  @Test
  void harden_validJs_noOmissions_returnsPassthrough() {
    when(jsSyntaxValidator.validate(anyString())).thenReturn(List.of());
    when(codeOmissionValidator.validate(anyString(), any())).thenReturn(List.of());

    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("answer", CLEAN_HTML, null);

    RepairResult result = guard.harden("session-1", baseRequest, extraction, generator);
    List<AgentEvent> events = result.events().collectList().block();
    HardenedOutput output = result.output().join();

    assertThat(events).isEmpty();
    assertThat(output.html()).isEqualTo(CLEAN_HTML);
    assertThat(output.answerText()).isEqualTo("answer");
  }

  // ── GD3: JS syntax errors → repair triggered → r1 RUNNING/SUCCESS ─────────

  @Test
  void harden_jsSyntaxErrors_repairSucceeds_emitsR1RunningAndSuccess() {
    JsSyntaxError error = new JsSyntaxError(0, 1, 1, "Unexpected end of input");
    when(jsSyntaxValidator.validate(BROKEN_HTML)).thenReturn(List.of(error));
    when(codeOmissionValidator.validate(anyString(), any())).thenReturn(List.of());
    when(generationRepairer.repair(any(), anyString(), anyString(), any(), any()))
        .thenReturn(Mono.just(new RepairOutcome(REPAIRED_HTML, true, List.of(error), List.of())));

    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("", BROKEN_HTML, null);

    RepairResult result = guard.harden("session-1", baseRequest, extraction, generator);
    List<AgentEvent> events = result.events().collectList().block();
    HardenedOutput output = result.output().join();

    // r1 RUNNING emitted first
    assertThat(events)
        .filteredOn(event -> event instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            event -> assertThat(((StepEvent) event).status()).isEqualTo(StepStatus.RUNNING));

    // r1 SUCCESS emitted after repair
    assertThat(events)
        .filteredOn(event -> event instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            event -> assertThat(((StepEvent) event).status()).isEqualTo(StepStatus.SUCCESS));

    assertThat(output.html()).isEqualTo(REPAIRED_HTML);
  }

  // ── GD4: JS repair fails → r1 ERROR, original HTML returned ──────────────

  @Test
  void harden_jsSyntaxErrors_repairFails_emitsR1Error_originalHtmlReturned() {
    JsSyntaxError error = new JsSyntaxError(0, 1, 1, "Unexpected end of input");
    when(jsSyntaxValidator.validate(BROKEN_HTML)).thenReturn(List.of(error));
    when(codeOmissionValidator.validate(anyString(), any())).thenReturn(List.of());
    // Repair still returns broken HTML (repair did not pass)
    when(generationRepairer.repair(any(), anyString(), anyString(), any(), any()))
        .thenReturn(
            Mono.just(new RepairOutcome(BROKEN_HTML, false, List.of(error), List.of(error))));

    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("", BROKEN_HTML, null);

    RepairResult result = guard.harden("session-1", baseRequest, extraction, generator);
    List<AgentEvent> events = result.events().collectList().block();
    HardenedOutput output = result.output().join();

    assertThat(events)
        .filteredOn(event -> event instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(event -> assertThat(((StepEvent) event).status()).isEqualTo(StepStatus.ERROR));

    assertThat(output.html()).isEqualTo(BROKEN_HTML);
  }

  // ── GD5: omission detected → omission retry triggers, takes priority over JS ─

  @Test
  void harden_omissionDetected_returnsOmissionR1Labels_retriedHtmlReturned() {
    // No JS errors; one omission finding — should trigger omission path (not JS path).
    when(jsSyntaxValidator.validate(anyString())).thenReturn(List.of());
    CodeOmissionFinding finding = new CodeOmissionFinding("其他 KPI 及圖表程式略");
    when(codeOmissionValidator.validate(anyString(), anyString())).thenReturn(List.of(finding));
    when(generationRepairer.retryForOmission(any(), anyString(), any(), any(), anyString()))
        .thenReturn(Mono.just(new RepairOutcome(CLEAN_HTML, true, List.of(), List.of())));

    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("", OMISSION_HTML, null);

    RepairResult result =
        guard.harden("session-1", requestWithPreviousArtifact, extraction, generator);
    List<AgentEvent> events = result.events().collectList().block();
    HardenedOutput output = result.output().join();

    // r1 RUNNING with omission title
    assertThat(events)
        .filteredOn(event -> event instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            event -> {
              StepEvent se = (StepEvent) event;
              assertThat(se.status()).isEqualTo(StepStatus.RUNNING);
              assertThat(se.title()).isEqualTo("偵測到程式碼省略，重新生成中");
            });

    // r1 SUCCESS with omission-fixed title
    assertThat(events)
        .filteredOn(event -> event instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            event -> {
              StepEvent se = (StepEvent) event;
              assertThat(se.status()).isEqualTo(StepStatus.SUCCESS);
              assertThat(se.title()).isEqualTo("程式碼省略已修復");
            });

    assertThat(output.html()).isEqualTo(CLEAN_HTML);

    // retryForOmission was called; generationRepairer.repair was NOT called
    Mockito.verify(generationRepairer)
        .retryForOmission(any(), anyString(), any(), any(), anyString());
    Mockito.verify(generationRepairer, never())
        .repair(any(), anyString(), anyString(), any(), any());
  }

  // ── GD6: omission retry fails → r1 ERROR, original HTML returned ──────────

  @Test
  void harden_omissionRetryFails_emitsR1OmissionError_originalHtmlReturned() {
    when(jsSyntaxValidator.validate(anyString())).thenReturn(List.of());
    CodeOmissionFinding finding = new CodeOmissionFinding("其他 KPI 及圖表程式略");
    when(codeOmissionValidator.validate(anyString(), anyString())).thenReturn(List.of(finding));
    // Retry still returns omission HTML (not fixed)
    when(generationRepairer.retryForOmission(any(), anyString(), any(), any(), anyString()))
        .thenReturn(Mono.just(new RepairOutcome(OMISSION_HTML, false, List.of(), List.of())));

    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("", OMISSION_HTML, null);

    RepairResult result =
        guard.harden("session-1", requestWithPreviousArtifact, extraction, generator);
    List<AgentEvent> events = result.events().collectList().block();
    HardenedOutput output = result.output().join();

    assertThat(events)
        .filteredOn(event -> event instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            event -> {
              StepEvent se = (StepEvent) event;
              assertThat(se.status()).isEqualTo(StepStatus.ERROR);
              assertThat(se.title()).isEqualTo("程式碼省略修復失敗");
            });

    assertThat(output.html()).isEqualTo(OMISSION_HTML);
  }

  // ── GD7: validator throws → passthrough (repair skipped) ──────────────────

  @Test
  void harden_validatorThrows_repairSkipped_originalHtmlReturned() {
    when(jsSyntaxValidator.validate(anyString()))
        .thenThrow(new RuntimeException("GraalVM unavailable"));

    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("", BROKEN_HTML, null);

    RepairResult result = guard.harden("session-1", baseRequest, extraction, generator);
    List<AgentEvent> events = result.events().collectList().block();
    HardenedOutput output = result.output().join();

    // No r1 steps — repair was skipped due to validator exception
    assertThat(events)
        .noneMatch(event -> event instanceof StepEvent se && "r1".equals(se.stepKey()));

    // Original HTML returned unchanged
    assertThat(output.html()).isEqualTo(BROKEN_HTML);

    // GenerationRepairer must NOT be called
    Mockito.verify(generationRepairer, never())
        .repair(any(), anyString(), anyString(), any(), any());
  }

  // ── GD8: bare-HTML promotion in answer text ────────────────────────────────

  @Test
  void harden_bareHtmlInAnswerText_promotedToHardenedOutputHtml() {
    // Repair disabled so we only test the bare-HTML promotion path, not repair logic.
    GenerationRepairGuard guard = guardWithRepairDisabled();

    String bareHtml = "<!DOCTYPE html><html><head></head><body>hello</body></html>";
    String answerText = "Here is the result:\n" + bareHtml + "\nEnjoy!";
    AgentOutcome extraction = new AgentOutcome(answerText, null, null);

    RepairResult result = guard.harden("session-1", baseRequest, extraction, generator);
    HardenedOutput output = result.output().join();

    // HTML promoted from answerText
    assertThat(output.html()).isEqualTo(bareHtml);
    // Replacement text substituted in answerText
    assertThat(output.answerText()).contains("（儀表板已生成 → 右側面板）");
    assertThat(output.answerText()).doesNotContain("<html");
  }

  // ── GD9: no HTML at all → passthrough with null html ─────────────────────

  @Test
  void harden_noHtml_noBarHtml_returnsPassthroughWithNullHtml() {
    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("plain text answer", null, null);

    RepairResult result = guard.harden("session-1", baseRequest, extraction, generator);
    HardenedOutput output = result.output().join();

    assertThat(result.events().collectList().block()).isEmpty();
    assertThat(output.html()).isNull();
    assertThat(output.answerText()).isEqualTo("plain text answer");

    // Validators must NOT be called — no HTML to validate
    Mockito.verify(jsSyntaxValidator, never()).validate(anyString());
  }

  // ── SPI default: unoverriding provider uses passthrough ───────────────────

  @Test
  void harden_defaultImplementation_returnsPassthroughUnchanged() {
    // An anonymous provider that does NOT override harden() — uses the interface default.
    DashboardAgentProvider defaultProvider =
        request -> new ProviderResult(Flux.empty(), () -> new AgentOutcome("", null, null));

    AgentOutcome extraction = new AgentOutcome("answer text", "<html>content</html>", null);
    AgentRequest request =
        new AgentRequest("user-1", "session-1", "question", List.of(), List.of(), null);

    RepairResult result = defaultProvider.harden("session-1", request, extraction);
    List<AgentEvent> events = result.events().collectList().block();
    HardenedOutput output = result.output().join();

    assertThat(events).isEmpty();
    assertThat(output.html()).isEqualTo("<html>content</html>");
    assertThat(output.answerText()).isEqualTo("answer text");
  }

  // ── GD10: Error subclass bypasses catch(Exception) → backstop completes future ──
  // Verifies Fix I1: the try-finally backstop in buildJsRepairResult ensures
  // output().get() never hangs when a java.lang.Error escapes catch(Exception).

  @Test
  void harden_repairerThrowsError_backstopCompletesOutputExceptionally() throws Exception {
    JsSyntaxError error = new JsSyntaxError(0, 1, 1, "err");
    when(jsSyntaxValidator.validate(anyString())).thenReturn(List.of(error));
    when(codeOmissionValidator.validate(anyString(), any())).thenReturn(List.of());
    // Mono.error with an Error subclass — .block() propagates it directly, bypassing
    // catch(Exception) in the deferred lambda body.
    when(generationRepairer.repair(any(), anyString(), anyString(), any(), any()))
        .thenReturn(Mono.error(new OutOfMemoryError("backstop test OOM")));

    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("", BROKEN_HTML, null);
    RepairResult result = guard.harden("session-1", baseRequest, extraction, generator);

    // Subscribing triggers the deferred lambda; the OOM propagates as a flux error signal.
    assertThatThrownBy(() -> result.events().collectList().block())
        .isInstanceOf(OutOfMemoryError.class);

    // The finally backstop must have completed the future exceptionally — not left it hanging.
    assertThatThrownBy(() -> result.output().get(1, TimeUnit.SECONDS))
        .isInstanceOf(ExecutionException.class)
        .cause()
        .isInstanceOf(IllegalStateException.class)
        .hasMessageContaining("aborted");
  }

  // ── GD11: both JS errors and omissions → omission retry takes precedence ──
  // Verifies Fix M2: when both validators return non-empty findings, the omission
  // path runs and JS repair is never called (omission > JS priority).

  @Test
  void harden_bothJsErrorsAndOmissions_omissionRetryTakesPrecedence() {
    JsSyntaxError jsError = new JsSyntaxError(0, 1, 1, "Unexpected end of input");
    when(jsSyntaxValidator.validate(anyString())).thenReturn(List.of(jsError));
    CodeOmissionFinding finding = new CodeOmissionFinding("其他 KPI 及圖表程式略");
    when(codeOmissionValidator.validate(anyString(), anyString())).thenReturn(List.of(finding));
    when(generationRepairer.retryForOmission(any(), anyString(), any(), any(), anyString()))
        .thenReturn(Mono.just(new RepairOutcome(CLEAN_HTML, true, List.of(), List.of())));

    GenerationRepairGuard guard = guardWithRepairEnabled();
    AgentOutcome extraction = new AgentOutcome("", OMISSION_HTML, null);

    RepairResult result =
        guard.harden("session-1", requestWithPreviousArtifact, extraction, generator);
    List<AgentEvent> events = result.events().collectList().block();
    HardenedOutput output = result.output().join();

    // r1 RUNNING title must be the omission title, not the JS title
    assertThat(events)
        .filteredOn(event -> event instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            event -> {
              StepEvent se = (StepEvent) event;
              assertThat(se.status()).isEqualTo(StepStatus.RUNNING);
              assertThat(se.title()).isEqualTo("偵測到程式碼省略，重新生成中");
            });

    assertThat(output.html()).isEqualTo(CLEAN_HTML);

    // retryForOmission was called; JS repair must NOT be called
    Mockito.verify(generationRepairer)
        .retryForOmission(any(), anyString(), any(), any(), anyString());
    Mockito.verify(generationRepairer, never())
        .repair(any(), anyString(), anyString(), any(), any());
  }
}
