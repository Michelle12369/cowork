package com.erd.cowork.agent.provider.openai;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.model.AgentFileContext;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.ProviderResult;
import java.util.List;
import java.util.function.Function;
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

@ExtendWith(MockitoExtension.class)
class GenerationRepairerTest {

  /** Stub generator backed by the Mockito mock — call {@link #when} on the mock, not the stub. */
  @Mock private Function<AgentRequest, ProviderResult> generator;

  private JsSyntaxValidator realValidator;
  private CodeOmissionValidator realOmissionValidator;
  private GenerationRepairer repairer;

  private static final String BROKEN_HTML =
      "<html><script>const x = {</script></html>"; // unclosed brace — real GraalJS error
  private static final String REPAIRED_HTML = "<html><script>const x = {};</script></html>";
  private static final List<JsSyntaxError> FAKE_ERRORS =
      List.of(new JsSyntaxError(0, 1, 14, "Unexpected end of input"));

  /** HTML containing a placeholder-comment omission (syntactically valid). */
  private static final String OMISSION_HTML =
      "<html><body><script>\n"
          + "// (其他 KPI 及圖表程式略，保留原本結構)\n"
          + "const x = {};\n"
          + "</script></body></html>";

  /** Fully-written HTML with no omission comments and no syntax errors. */
  private static final String CLEAN_HTML =
      "<html><body><script>\n" + "const x = {};\n" + "</script></body></html>";

  @BeforeEach
  void setUp() {
    realValidator = new JsSyntaxValidator();
    realOmissionValidator = new CodeOmissionValidator(realValidator);
    VelocityEngine velocityEngine = new VelocityEngine();
    velocityEngine.setProperty(RuntimeConstants.RESOURCE_LOADERS, "classpath");
    velocityEngine.setProperty(
        "resource.loader.classpath.class", ClasspathResourceLoader.class.getName());
    velocityEngine.setProperty(RuntimeConstants.INPUT_ENCODING, "UTF-8");
    velocityEngine.init();
    repairer = new GenerationRepairer(realValidator, realOmissionValidator, velocityEngine);
  }

  private AgentRequest dummyRequest() {
    return new AgentRequest("user-1", "session-1", "build dashboard", List.of(), List.of(), null);
  }

  /**
   * Request whose previousArtifactHtml is three times longer than {@code OMISSION_HTML}, so the
   * omission validator's shrinkage gate is open during post-retry validation.
   */
  private AgentRequest requestWithLongPreviousArtifact() {
    return new AgentRequest(
        "user-1",
        "session-1",
        "build dashboard",
        List.of(),
        List.of(),
        "x".repeat(OMISSION_HTML.length() * 3));
  }

  // ── R1: repair succeeds — generator returns valid HTML ──────────────────────

  @Test
  void repair_generatorReturnsFixedHtml_outcomePassed() {
    when(generator.apply(any()))
        .thenReturn(
            new ProviderResult(
                Flux.just(new TokenEvent("repair-token")), // token must not leak to caller
                () -> new AgentOutcome("", REPAIRED_HTML, null)));

    RepairOutcome outcome =
        repairer.repair(generator, "session-1", BROKEN_HTML, FAKE_ERRORS, dummyRequest()).block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isTrue();
    assertThat(outcome.html()).isEqualTo(REPAIRED_HTML);
    assertThat(outcome.errorsBefore()).hasSize(1);
    assertThat(outcome.errorsAfter()).isEmpty();
  }

  // ── R2: repair fails — generator returns HTML that still has errors ─────────

  @Test
  void repair_generatorReturnsBrokenHtml_outcomeNotPassed_originalHtmlReturned() {
    // Generator returns the same broken HTML — re-validation still finds errors
    when(generator.apply(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", BROKEN_HTML, null)));

    RepairOutcome outcome =
        repairer.repair(generator, "session-1", BROKEN_HTML, FAKE_ERRORS, dummyRequest()).block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isFalse();
    assertThat(outcome.html()).isEqualTo(BROKEN_HTML); // fall back to original
    assertThat(outcome.errorsAfter()).isNotEmpty();
  }

  // ── R3: generator returns no HTML — outcome not passed, original html returned

  @Test
  void repair_generatorReturnsNoHtml_outcomeNotPassed_originalHtmlReturned() {
    when(generator.apply(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("no html here", null, null)));

    RepairOutcome outcome =
        repairer.repair(generator, "session-1", BROKEN_HTML, FAKE_ERRORS, dummyRequest()).block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isFalse();
    assertThat(outcome.html()).isEqualTo(BROKEN_HTML);
    assertThat(outcome.errorsAfter()).isEqualTo(FAKE_ERRORS); // same as before since no html
  }

  // ── R4: repair request carries files from original request ─────────────────

  @Test
  void repair_repairRequestCarriesFilesFromOriginalRequest() {
    AgentFileContext fakeFile =
        new AgentFileContext("alias1", "data.csv", "text/csv", "storage/key/test.csv", null);
    AgentRequest originalWithFiles =
        new AgentRequest("u1", "s1", "build", List.of(), List.of(fakeFile), null);

    ArgumentCaptor<AgentRequest> requestCaptor = ArgumentCaptor.forClass(AgentRequest.class);
    when(generator.apply(requestCaptor.capture()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", REPAIRED_HTML, null)));

    repairer.repair(generator, "s1", BROKEN_HTML, FAKE_ERRORS, originalWithFiles).block();

    AgentRequest repairRequest = requestCaptor.getValue();
    assertThat(repairRequest.files()).containsExactly(fakeFile);
    assertThat(repairRequest.history()).isEmpty();
    assertThat(repairRequest.previousArtifactHtml()).isEqualTo(BROKEN_HTML);
    assertThat(repairRequest.question()).contains("script#1 第 1 行：Unexpected end of input");
    assertThat(repairRequest.question()).contains("只修列出問題，其餘逐字保留，輸出完整 HTML");
  }

  // ── R5: content-pinning — exact syntax-repair prompt for 2-error case ──────

  @Test
  void repair_exactPromptContent_2errors() {
    List<JsSyntaxError> errors =
        List.of(
            new JsSyntaxError(0, 1, 14, "Unexpected end of input"),
            new JsSyntaxError(2, 5, 3, "Missing semicolon"));

    String expected =
        "以下 HTML 中有 JavaScript 語法錯誤，請修復：\n"
            + "\n"
            + "script#1 第 1 行：Unexpected end of input\n"
            + "script#3 第 5 行：Missing semicolon\n"
            + "\n"
            + "只修列出問題，其餘逐字保留，輸出完整 HTML。";

    ArgumentCaptor<AgentRequest> captor = ArgumentCaptor.forClass(AgentRequest.class);
    when(generator.apply(captor.capture()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", REPAIRED_HTML, null)));

    repairer.repair(generator, "s1", BROKEN_HTML, errors, dummyRequest()).block();

    assertThat(captor.getValue().question()).isEqualTo(expected);
  }

  // ── RO1: retryForOmission succeeds — generator returns clean HTML ───────────

  @Test
  void retryForOmission_generatorReturnsCleanHtml_outcomePassed() {
    List<CodeOmissionFinding> findings = List.of(new CodeOmissionFinding("(其他 KPI 及圖表程式略，保留原本結構)"));

    when(generator.apply(any()))
        .thenReturn(
            new ProviderResult(
                Flux.just(new TokenEvent("retry-token")),
                () -> new AgentOutcome("", CLEAN_HTML, null)));

    RepairOutcome outcome =
        repairer
            .retryForOmission(
                generator, "session-1", findings, requestWithLongPreviousArtifact(), OMISSION_HTML)
            .block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isTrue();
    assertThat(outcome.html()).isEqualTo(CLEAN_HTML);
  }

  // ── RO2: retryForOmission fails — generator still returns omission HTML ─────

  @Test
  void retryForOmission_generatorStillOmits_outcomeNotPassed_originalHtmlReturned() {
    List<CodeOmissionFinding> findings = List.of(new CodeOmissionFinding("(其他 KPI 及圖表程式略，保留原本結構)"));

    when(generator.apply(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", OMISSION_HTML, null)));

    RepairOutcome outcome =
        repairer
            .retryForOmission(
                generator, "session-1", findings, requestWithLongPreviousArtifact(), OMISSION_HTML)
            .block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isFalse();
    assertThat(outcome.html()).isEqualTo(OMISSION_HTML); // failure → original html preserved
  }

  // ── RO3: retryForOmission request preserves original question, history, files

  @Test
  void retryForOmission_requestPreservesOriginalFieldsAndAppendsWarning() {
    AgentFileContext fakeFile =
        new AgentFileContext("alias1", "data.csv", "text/csv", "storage/key/test.csv", null);
    AgentRequest original =
        new AgentRequest(
            "u1", "s1", "build a KPI dashboard", List.of(), List.of(fakeFile), "<html>prev</html>");

    List<CodeOmissionFinding> findings = List.of(new CodeOmissionFinding("(其他 KPI 及圖表程式略，保留原本結構)"));

    ArgumentCaptor<AgentRequest> requestCaptor = ArgumentCaptor.forClass(AgentRequest.class);
    when(generator.apply(requestCaptor.capture()))
        .thenReturn(new ProviderResult(Flux.empty(), () -> new AgentOutcome("", CLEAN_HTML, null)));

    repairer.retryForOmission(generator, "s1", findings, original, OMISSION_HTML).block();

    AgentRequest sentRequest = requestCaptor.getValue();
    // Original question is preserved as the base, with the warning appended
    assertThat(sentRequest.question()).startsWith("build a KPI dashboard");
    assertThat(sentRequest.question()).contains("【重要】");
    assertThat(sentRequest.question()).contains("省略");
    assertThat(sentRequest.question()).contains("(其他 KPI 及圖表程式略，保留原本結構)");
    // History, files, and previousArtifactHtml are all carried over unchanged
    assertThat(sentRequest.files()).containsExactly(fakeFile);
    assertThat(sentRequest.previousArtifactHtml()).isEqualTo("<html>prev</html>");
  }

  // ── RO4: retryForOmission — generator returns no HTML, outcome not passed ───

  @Test
  void retryForOmission_generatorReturnsNoHtml_outcomeNotPassed_originalHtmlReturned() {
    List<CodeOmissionFinding> findings =
        List.of(new CodeOmissionFinding("existing code omitted here"));

    when(generator.apply(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("answer only", null, null)));

    RepairOutcome outcome =
        repairer
            .retryForOmission(generator, "session-1", findings, dummyRequest(), OMISSION_HTML)
            .block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isFalse();
    assertThat(outcome.html()).isEqualTo(OMISSION_HTML); // failure → original html preserved
  }

  // ── RO5: retryForOmission fails when retried HTML still has JS syntax errors

  @Test
  void retryForOmission_retriedHtmlHasSyntaxErrors_outcomeNotPassed_originalHtmlReturned() {
    List<CodeOmissionFinding> findings = List.of(new CodeOmissionFinding("(程式略)"));

    // Generator returns HTML that has no omission but has a syntax error
    when(generator.apply(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", BROKEN_HTML, null)));

    RepairOutcome outcome =
        repairer
            .retryForOmission(generator, "session-1", findings, dummyRequest(), OMISSION_HTML)
            .block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isFalse();
    assertThat(outcome.html()).isEqualTo(OMISSION_HTML); // failure → original html preserved
  }

  // ── RO6: content-pinning — exact omission-retry warning for 2-finding case ──

  @Test
  void retryForOmission_exactWarningContent_2findings() {
    List<CodeOmissionFinding> findings =
        List.of(
            new CodeOmissionFinding("(其他 KPI 及圖表程式略，保留原本結構)"), new CodeOmissionFinding("// 此處程式略"));

    String expectedWarning =
        "\n\n【重要】你上一次的輸出用註解省略了部分程式碼（偵測到：「(其他 KPI 及圖表程式略，保留原本結構)」等 2 處）。"
            + "請重新輸出完整 HTML：每一行程式碼都必須完整寫出，NEVER 以任何註解或省略記號代替程式碼。";

    AgentRequest original =
        new AgentRequest("u1", "s1", "build dashboard", List.of(), List.of(), null);

    ArgumentCaptor<AgentRequest> captor = ArgumentCaptor.forClass(AgentRequest.class);
    when(generator.apply(captor.capture()))
        .thenReturn(new ProviderResult(Flux.empty(), () -> new AgentOutcome("", CLEAN_HTML, null)));

    repairer.retryForOmission(generator, "s1", findings, original, OMISSION_HTML).block();

    String sentQuestion = captor.getValue().question();
    // The question is "build dashboard" + warning
    assertThat(sentQuestion).isEqualTo("build dashboard" + expectedWarning);
  }
}
