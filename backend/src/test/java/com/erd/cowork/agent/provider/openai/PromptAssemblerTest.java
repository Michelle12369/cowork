package com.erd.cowork.agent.provider.openai;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.model.AgentFileContext;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.model.HistoryMessage;
import com.erd.cowork.parsing.model.ColumnProfile;
import com.erd.cowork.parsing.model.FileProfile;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.apache.velocity.app.VelocityEngine;
import org.apache.velocity.runtime.RuntimeConstants;
import org.apache.velocity.runtime.resource.loader.ClasspathResourceLoader;
import org.junit.jupiter.api.Test;

class PromptAssemblerTest {

  private static VelocityEngine buildVelocityEngine() {
    VelocityEngine engine = new VelocityEngine();
    engine.setProperty(RuntimeConstants.RESOURCE_LOADERS, "classpath");
    engine.setProperty("resource.loader.classpath.class", ClasspathResourceLoader.class.getName());
    engine.setProperty(RuntimeConstants.INPUT_ENCODING, "UTF-8");
    engine.init();
    return engine;
  }

  private final PromptAssembler builder = new PromptAssembler(buildVelocityEngine());

  // ── systemPrompt ──────────────────────────────────────────────────────────

  @Test
  void systemPrompt_matchesSnapshot_byteForByte() throws Exception {
    // Anchoring snapshot: locks the Velocity template output to the exact bytes of the
    // pre-migration Java SYSTEM_PROMPT text block. Any drift in the .vm (e.g. Velocity
    // '##' comment semantics, escaping, trailing whitespace) fails this test.
    String expected;
    try (var in = getClass().getResourceAsStream("/snapshots/openai/system-prompt.snapshot.txt")) {
      expected = new String(in.readAllBytes(), StandardCharsets.UTF_8);
    }
    assertThat(builder.systemPrompt()).isEqualTo(expected);
  }

  @Test
  void systemPrompt_returnsFixedText_containsErdDataContract() {
    String system = builder.systemPrompt();
    assertThat(system).contains("window.__ERD_DATA__");
  }

  @Test
  void systemPrompt_returnsFixedText_containsDataShape() {
    String system = builder.systemPrompt();
    assertThat(system).contains("{ columns: string[], rows: unknown[][] }");
  }

  @Test
  void systemPrompt_returnsFixedText_containsHtmlFenceMarker() {
    String system = builder.systemPrompt();
    assertThat(system).contains("```html");
  }

  @Test
  void systemPrompt_returnsFixedText_mentionsTailwind() {
    String system = builder.systemPrompt();
    assertThat(system).contains("Tailwind");
  }

  @Test
  void systemPrompt_returnsFixedText_mentionsEcharts() {
    String system = builder.systemPrompt();
    assertThat(system).contains("echarts");
  }

  @Test
  void systemPrompt_returnsFixedText_enforcesExactlyOneFencedBlock() {
    String system = builder.systemPrompt();
    assertThat(system).contains("exactly one");
  }

  @Test
  void systemPrompt_prohibitsHardcodingData() {
    String system = builder.systemPrompt();
    assertThat(system).contains("do NOT hardcode");
    assertThat(system).contains("sample rows");
  }

  @Test
  void systemPrompt_requiresBlockExternalExplanation() {
    String system = builder.systemPrompt();
    assertThat(system).contains("2-4 sentences");
  }

  @Test
  void systemPrompt_repliesInTraditionalChinese() {
    String system = builder.systemPrompt();
    assertThat(system).contains("Traditional Chinese");
  }

  @Test
  void systemPrompt_declinesUnrelatedRequests() {
    String system = builder.systemPrompt();
    assertThat(system).contains("politely decline");
  }

  @Test
  void systemPrompt_clarifiesVagueRequestsWithChartOptions() {
    String system = builder.systemPrompt();
    assertThat(system).contains("propose suitable chart options");
    assertThat(system).contains("control chart");
  }

  @Test
  void systemPrompt_asksForUploadWhenNoFiles() {
    String system = builder.systemPrompt();
    assertThat(system).contains("(no files uploaded)");
  }

  @Test
  void systemPrompt_htmlOnlyWhenRequested() {
    String system = builder.systemPrompt();
    assertThat(system).contains("ONLY when");
  }

  // ── C3: tuning rules added to system prompt ────────────────────────────────

  @Test
  void systemPrompt_minimalInterpretation_instructionPresent() {
    String system = builder.systemPrompt();
    // Minimal-interpretation rule for dashboard modification
    assertThat(system).contains("Minimal interpretation");
    assertThat(system).contains("smallest change");
  }

  @Test
  void systemPrompt_answeringClarificationOptions_neverRepeatQuestion() {
    String system = builder.systemPrompt();
    // Execute-immediately rule when user answers previous clarification options
    assertThat(system).contains("Executing clarification answers");
    assertThat(system).contains("NEVER ask for clarification again");
  }

  // ── userPrompt – normal files ──────────────────────────────────────────────

  @Test
  void userPrompt_withTwoFiles_containsFileHeaderForFirstFile() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE))
        .contains("## file: file1 (lots.csv)");
  }

  @Test
  void userPrompt_withTwoFiles_containsRowCount() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).contains("rows: 100");
  }

  @Test
  void userPrompt_withTwoFiles_containsNumberColumnRow() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).contains("| vt | number |");
  }

  @Test
  void userPrompt_withTwoFiles_containsStringColumnRow() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).contains("| lot | string |");
  }

  @Test
  void userPrompt_withTwoFiles_containsTopValuesForStringColumn() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE))
        .contains("top values (lot): A, B, C");
  }

  @Test
  void userPrompt_withTwoFiles_containsSampleTableHeader() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE))
        .contains("### sample (first 2 rows)");
  }

  @Test
  void userPrompt_withTwoFiles_containsSampleColumnHeaders() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).contains("| vt | lot |");
  }

  @Test
  void userPrompt_columnTable_hasNullCountHeader() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE))
        .contains("| column | type | min | max | mean | std | nullCount |");
  }

  @Test
  void userPrompt_withTwoFiles_containsSecondFileSection() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE))
        .contains("## file: file2 (empty.csv)");
  }

  @Test
  void userPrompt_withTwoFiles_doesNotContainNoFilesUploaded() {
    AgentRequest request = buildTwoFileRequest();
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE))
        .doesNotContain("(no files uploaded)");
  }

  // ── userPrompt – history ──────────────────────────────────────────────────

  @Test
  void userPrompt_withHistory_containsConversationSoFarHeader() {
    AgentRequest request = buildHistoryRequest("short", "reply");
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).contains("## conversation so far");
  }

  @Test
  void userPrompt_withHistory_containsSenderAndText() {
    AgentRequest request = buildHistoryRequest("short message", "assistant reply");
    String result = builder.userPrompt(request, Integer.MAX_VALUE);
    assertThat(result).contains("user: short message");
    assertThat(result).contains("assistant: assistant reply");
  }

  @Test
  void userPrompt_with501CharText_passedThroughUntruncated() {
    // Truncation responsibility moved upstream to AgentOrchestrator.buildHistoryMessage();
    // PromptAssembler passes history text through as-is — do NOT truncate here.
    String longText = "x".repeat(501);
    AgentRequest request = buildHistoryRequest(longText, "ok");
    String result = builder.userPrompt(request, Integer.MAX_VALUE);
    assertThat(result).contains("user: " + longText); // full 501-char text preserved
  }

  // ── userPrompt – section ordering ─────────────────────────────────────────

  @Test
  void userPrompt_withHistoryAndFiles_sectionOrderIsHistoryThenQuestionThenFile() {
    FileProfile profile =
        new FileProfile(
            1L,
            1,
            List.of("x"),
            List.of(new ColumnProfile("x", "number", 0.0, 1.0, 0.5, 0.1, 0L, List.of())),
            List.of());
    AgentRequest request =
        new AgentRequest(
            "u1",
            "s1",
            "Question text",
            List.of(new HistoryMessage("user", "prior")),
            List.of(new AgentFileContext("f1", "data.csv", "csv", "storage/key/test.csv", profile)),
            null);
    String result = builder.userPrompt(request, Integer.MAX_VALUE);
    int histIdx = result.indexOf("## conversation so far");
    int qIdx = result.indexOf("# Question");
    int fileIdx = result.indexOf("## file:");
    assertThat(histIdx).isLessThan(qIdx);
    assertThat(qIdx).isLessThan(fileIdx);
  }

  // ── userPrompt – empty files ──────────────────────────────────────────────

  @Test
  void userPrompt_emptyFilesList_containsNoFilesUploadedPlaceholder() {
    AgentRequest request =
        new AgentRequest("u1", "s1", "What can you do?", List.of(), List.of(), null);
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).contains("(no files uploaded)");
  }

  @Test
  void userPrompt_emptyFilesList_doesNotContainFileSection() {
    AgentRequest request =
        new AgentRequest("u1", "s1", "What can you do?", List.of(), List.of(), null);
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).doesNotContain("## file:");
  }

  // ── userPrompt – pipe escaping ────────────────────────────────────────────

  @Test
  void userPrompt_sampleHeaderContainingPipe_isPipeEscaped() {
    FileProfile profile =
        new FileProfile(
            1L,
            1,
            List.of("col|name"),
            List.of(new ColumnProfile("col|name", "string", null, null, null, null, 0L, List.of())),
            List.of(List.of("val")));
    AgentRequest request =
        new AgentRequest(
            "u1",
            "s1",
            "q",
            List.of(),
            List.of(new AgentFileContext("f1", "data.csv", "csv", "storage/key/test.csv", profile)),
            null);
    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).contains("col\\|name");
  }

  @Test
  void userPrompt_sampleCellContainingPipe_isPipeEscaped() {
    FileProfile profile =
        new FileProfile(
            1L,
            1,
            List.of("notes"),
            List.of(new ColumnProfile("notes", "string", null, null, null, null, 0L, List.of())),
            List.of(List.of("a|b")));

    AgentRequest request =
        new AgentRequest(
            "u1",
            "s1",
            "Check notes",
            List.of(),
            List.of(new AgentFileContext("f1", "data.csv", "csv", "storage/key/test.csv", profile)),
            null);

    assertThat(builder.userPrompt(request, Integer.MAX_VALUE)).contains("a\\|b");
  }

  // ── systemPrompt – chart rendering rules ─────────────────────────────────

  @Test
  void systemPrompt_containsChartHeightRule() {
    String prompt = builder.systemPrompt();
    assertThat(prompt).contains("height:320px");
    assertThat(prompt).contains("explicit inline height");
  }

  @Test
  void systemPrompt_containsDataAccessExample() {
    String prompt = builder.systemPrompt();
    assertThat(prompt).contains("columns.indexOf(");
    assertThat(prompt).contains("rows.map(r => Number(r[");
  }

  @Test
  void systemPrompt_containsEchartsInitRules() {
    String prompt = builder.systemPrompt();
    assertThat(prompt).contains("DOMContentLoaded");
    assertThat(prompt).contains("chart.resize()");
  }

  @Test
  void userPrompt_withFiles_endsWithFenceReminder() {
    String prompt = builder.userPrompt(requestWithFiles(), Integer.MAX_VALUE);
    assertThat(prompt.trim())
        .endsWith(
            "Remember: output the complete HTML document inside a single ```html fenced block.");
  }

  @Test
  void userPrompt_withoutFiles_hasNoFenceReminder() {
    String prompt = builder.userPrompt(requestWithoutFiles(), Integer.MAX_VALUE);
    assertThat(prompt).doesNotContain("Remember: output the complete HTML document");
  }

  // ── userPrompt – previous html feedback ──────────────────────────────────

  @Test
  void userPrompt_withPreviousHtml_containsPreviousHtmlSection() {
    AgentRequest request =
        new AgentRequest(
            "u1", "s1", "Change the color to blue", List.of(), List.of(), "<p>old dashboard</p>");
    String result = builder.userPrompt(request, Integer.MAX_VALUE);
    assertThat(result).contains("## previous dashboard html (the user is iterating on this)");
    assertThat(result).contains("<p>old dashboard</p>");
  }

  @Test
  void userPrompt_withPreviousHtml_containsMinimumChangeInstruction() {
    AgentRequest request =
        new AgentRequest(
            "u1", "s1", "Change the color to blue", List.of(), List.of(), "<p>old</p>");
    String result = builder.userPrompt(request, Integer.MAX_VALUE);
    assertThat(result).contains("Modify ONLY what the user asked for");
    assertThat(result).contains("byte-identical");
  }

  @Test
  void userPrompt_withPreviousHtml_overBudget_skipsFeedback() {
    // PROMPT_OVERHEAD_CHARS ≈ 12500 chars added to estimated tokens.
    // contextWindow=10 → budget = 10 * 0.5 = 5 tokens; overhead alone (12500/3.5 ≈ 3571) > 5,
    // so the previous-html section is always skipped regardless of html size.
    String largeHtml = "<html>" + "x".repeat(500) + "</html>";
    AgentRequest request = new AgentRequest("u1", "s1", "q", List.of(), List.of(), largeHtml);
    String result = builder.userPrompt(request, 10);
    assertThat(result).doesNotContain("## previous dashboard html");
    assertThat(result).doesNotContain(largeHtml);
  }

  @Test
  void userPrompt_withNullPreviousHtml_noFeedbackSection() {
    AgentRequest request = new AgentRequest("u1", "s1", "New analysis", List.of(), List.of(), null);
    String result = builder.userPrompt(request, Integer.MAX_VALUE);
    assertThat(result).doesNotContain("## previous dashboard html");
    assertThat(result).doesNotContain("Modify ONLY what the user asked for");
  }

  // ── systemPrompt – progress markers ──────────────────────────────────────

  @Test
  void systemPrompt_containsStepMarkerInstruction() {
    String system = builder.systemPrompt();
    // The instruction must reference the exact lowercase [[step: format (case-sensitive)
    assertThat(system).contains("[[step:");
  }

  @Test
  void systemPrompt_stepMarkerHasFormatExample() {
    String system = builder.systemPrompt();
    // A concrete example must be present so the LLM knows the exact format
    assertThat(system).contains("[[step: 資料讀取]]");
  }

  @Test
  void systemPrompt_stepMarkerMentionsCharacterLimit() {
    String system = builder.systemPrompt();
    assertThat(system).contains("20");
  }

  // ── systemPrompt – clarification protocol ────────────────────────────────

  @Test
  void systemPrompt_containsQuestionsProtocolFence() {
    String system = builder.systemPrompt();
    assertThat(system).contains("```questions");
  }

  @Test
  void systemPrompt_questionsProtocolHasFewShotExample() {
    String system = builder.systemPrompt();
    // The example from the spec: multiSelect:false question with options
    assertThat(system).contains("要分析哪個特性？");
    assertThat(system).contains("Bore Diameter");
    assertThat(system).contains("multiSelect");
  }

  @Test
  void systemPrompt_questionsProtocolProhibitsCombiningWithHtml() {
    String system = builder.systemPrompt();
    assertThat(system).contains("Do NOT generate");
  }

  @Test
  void systemPrompt_questionsProtocolHasMultiSelectFewShot() {
    // A multiSelect:true example must be present to improve model trigger rate
    String system = builder.systemPrompt();
    assertThat(system).contains("\"multiSelect\":true");
    assertThat(system).contains("Shaft OD");
  }

  @Test
  void systemPrompt_clarificationMode_onlyWhenGenuinelyAmbiguous() {
    // Fix #3: clarification mode must be exclusive — only trigger when the model
    // genuinely cannot infer intent; if inference is possible it should proceed.
    String system = builder.systemPrompt();
    assertThat(system).contains("genuinely ambiguous");
    assertThat(system).contains("reasonably infer");
    assertThat(system).contains("proceed with generation");
  }

  // ── systemPrompt – prompt hardening wave 1 (grid / @apply / getCol / tabs) ──

  @Test
  void systemPrompt_requiresContainLabel_forCartesianCharts() {
    String system = builder.systemPrompt();
    assertThat(system).contains("containLabel: true");
    assertThat(system).contains("left: '3%'");
  }

  @Test
  void systemPrompt_prohibitsAtApplyInStyleBlocks() {
    String system = builder.systemPrompt();
    assertThat(system).contains("NEVER use @apply inside <style> blocks");
    assertThat(system).contains("Tailwind CDN does not compile @apply");
  }

  @Test
  void systemPrompt_requiresGetColHelper_forDynamicColumnResolution() {
    String system = builder.systemPrompt();
    assertThat(system).contains("function getCol(columns, ...candidates)");
    assertThat(system).contains("console.warn('[ERD] column not found:'");
  }

  @Test
  void systemPrompt_providesGoldenTabSnippet() {
    String system = builder.systemPrompt();
    // Tabler-style tabs: numeric index, role=tab, bottom-border active class
    assertThat(system).contains("function showTab(idx)");
    assertThat(system).contains("role=tab");
    assertThat(system).contains("border-b-2 border-blue-600");
  }

  @Test
  void systemPrompt_requiresKpiColorCodedBorders() {
    String system = builder.systemPrompt();
    // KPI cards now use Tailwind border utility classes, not inline hex colors
    assertThat(system).contains("border-l-4");
    assertThat(system).contains("border-l-emerald-500");
    assertThat(system).contains("color-coded left borders");
  }

  // ── systemPrompt – artifact contract placement + modification summary ────────

  @Test
  void systemPrompt_explanationMustAppearAfterHtml_instructed() {
    String system = builder.systemPrompt();
    // Instruction must state that explanation appears AFTER the closing fence
    assertThat(system).contains("After the closing");
    // The old "Outside the fenced block" placement instruction must be gone
    assertThat(system).doesNotContain("Outside the fenced block");
  }

  @Test
  void systemPrompt_explanationForModification_mentionsModifiedLocations() {
    String system = builder.systemPrompt();
    // Modification requests must ask the model to list which elements were changed
    assertThat(system).contains("modified locations");
  }

  @Test
  void systemPrompt_explanationNeverBeforeHtmlBlock_instructed() {
    String system = builder.systemPrompt();
    assertThat(system).contains("NEVER write the explanation before");
  }

  // ── systemPrompt – tab resize dispatch ───────────────────────────────────────

  @Test
  void systemPrompt_showTab_dispatchesResizeAfterSwitch() {
    String system = builder.systemPrompt();
    // showTab golden pattern must dispatch a resize event so ECharts re-renders after
    // a display:none tab becomes visible
    assertThat(system).contains("dispatchEvent(new Event('resize'))");
  }

  // ── systemPrompt – axis-aware formatter, dataZoom spacing, legend pairing ────

  @Test
  void systemPrompt_timeAxisFormatter_prohibitsSubstringUsesEchartsFormatTime() {
    String system = builder.systemPrompt();
    // time axis formatter value is ms number — substring on a number causes TypeError
    assertThat(system).contains("echarts.format.formatTime");
    assertThat(system).contains("NEVER call .substring()");
  }

  @Test
  void systemPrompt_sliderDataZoom_requiresGridBottomSixty() {
    String system = builder.systemPrompt();
    // slider height ~20px + bottom:5 needs grid.bottom:60 to avoid overlap with x-axis labels
    assertThat(system).contains("grid.bottom: 60");
    assertThat(system).contains("bottom: 60, containLabel");
  }

  @Test
  void systemPrompt_legendTopMustBeNumberNotPercent() {
    String system = builder.systemPrompt();
    // top:'X%' resolved to px on small containers may collide with chart area
    assertThat(system).contains("NEVER top:'X%'");
    assertThat(system).contains("grid.top MUST be");
  }

  // ── systemPrompt – fancy design wave anchor tests (erd theme / banner / icons) ──

  @Test
  void systemPrompt_echartsInitUsesBrandTheme() {
    String system = builder.systemPrompt();
    // Charts MUST be initialized with the injected 'erd' theme, never a custom palette
    assertThat(system).contains("echarts.init(el, 'erd')");
    assertThat(system).contains("NEVER call echarts.registerTheme()");
  }

  @Test
  void systemPrompt_bannerUsesSolidSlateBackground() {
    String system = builder.systemPrompt();
    // Banner must be solid bg-slate-800 — gradients are prohibited
    assertThat(system).contains("bg-slate-800");
    assertThat(system).contains("NEVER use gradient");
  }

  @Test
  void systemPrompt_tabActiveClassHasBottomBorderBlue() {
    String system = builder.systemPrompt();
    // Active tab indicator: bottom border blue-600 with full class string
    assertThat(system).contains("border-b-2 border-blue-600 text-slate-900");
  }

  @Test
  void systemPrompt_prohibitsEmojiInUiElements() {
    String system = builder.systemPrompt();
    // Headings, tab labels, and cards must use SVG icons, not emoji
    assertThat(system).contains("NEVER use emoji");
  }

  @Test
  void systemPrompt_requiresSvgStrokeIcons() {
    String system = builder.systemPrompt();
    // All icons must be inline SVG with stroke="currentColor" and stroke-width="2"
    assertThat(system).contains("stroke=\"currentColor\"");
    assertThat(system).contains("stroke-width=\"2\"");
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  private AgentRequest requestWithFiles() {
    return buildTwoFileRequest();
  }

  private AgentRequest requestWithoutFiles() {
    return new AgentRequest("u1", "s1", "What can you do?", List.of(), List.of(), null);
  }

  private AgentRequest buildTwoFileRequest() {
    FileProfile profile =
        new FileProfile(
            100L,
            2,
            List.of("vt", "lot"),
            List.of(
                new ColumnProfile("vt", "number", 0.417, 0.452, 0.4214, 0.0071, 0L, List.of()),
                new ColumnProfile(
                    "lot", "string", null, null, null, null, 0L, List.of("A", "B", "C"))),
            List.of(List.of("0.419", "A"), List.of("0.423", "B")));

    FileProfile emptyProfile = new FileProfile(0L, 0, List.of(), List.of(), List.of());

    return new AgentRequest(
        "u1",
        "s1",
        "Show me analysis",
        List.of(),
        List.of(
            new AgentFileContext("file1", "lots.csv", "csv", "storage/key/test.csv", profile),
            new AgentFileContext(
                "file2", "empty.csv", "csv", "storage/key/test.csv", emptyProfile)),
        null);
  }

  private AgentRequest buildHistoryRequest(String userText, String assistantText) {
    return new AgentRequest(
        "u1",
        "s1",
        "Follow up",
        List.of(
            new HistoryMessage("user", userText), new HistoryMessage("assistant", assistantText)),
        List.of(),
        null);
  }
}
