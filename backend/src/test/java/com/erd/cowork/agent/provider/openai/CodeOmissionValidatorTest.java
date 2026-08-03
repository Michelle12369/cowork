package com.erd.cowork.agent.provider.openai;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CodeOmissionValidatorTest {

  private CodeOmissionValidator validator;

  @BeforeEach
  void setUp() {
    validator = new CodeOmissionValidator(new JsSyntaxValidator());
  }

  /**
   * Builds a previous-artifact string three times longer than {@code html}, so the shrinkage gate
   * ({@code html.length() < previous.length() * 0.7}) is open and the pattern scan runs.
   */
  private static String previousThreeTimesLongerThan(String html) {
    return "x".repeat(html.length() * 3);
  }

  // ── OV1: real-world placeholder — ZH pattern in JS line comment ───────────

  @Test
  void validate_realWorldPlaceholderComment_detected() {
    // Actual pattern seen in gpt-oss output: a line comment with omission markers.
    String html =
        "<html><body><script>\n"
            + "// (其他 KPI 及圖表程式略，保留原本結構)\n"
            + "const x = {};\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isNotEmpty();
    // "圖表程式略" and "保留原本" are both ZH_PATTERNS present in this comment.
    assertThat(findings.get(0).commentText()).contains("圖表程式略").contains("保留原本");
  }

  // ── OV1b: regression — legitimate "existing code" comment must NOT fire ────

  @Test
  void validate_legitimateExistingCodeComment_similarLength_notDetected() {
    // Real regression: a legitimate alias comment mentioning "existing code" in a normal
    // iteration (output length comparable to previous artifact) must never trigger.
    String html =
        "<html><body><script>\n"
            + "// Alias to satisfy existing code that expects getCol\n"
            + "const getCol = getColumn;\n"
            + "const x = {};\n"
            + "</script></body></html>";
    // Previous artifact of comparable length — the shrinkage gate stays closed.
    String previousOfSimilarLength = "y".repeat(html.length());

    List<CodeOmissionFinding> findings = validator.validate(html, previousOfSimilarLength);

    assertThat(findings).isEmpty();
  }

  @Test
  void validate_legitimateExistingCodeComment_evenWithLongPrevious_notDetected() {
    // Bare "existing code" was removed from EN_PATTERNS_LOWER — even when the gate is open,
    // this legitimate comment must not match any pattern.
    String html =
        "<html><body><script>\n"
            + "// Alias to satisfy existing code that expects getCol\n"
            + "const getCol = getColumn;\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isEmpty();
  }

  // ── OV1c: shrinkage gate ───────────────────────────────────────────────────

  @Test
  void validate_omissionCommentButNoPreviousArtifact_gateClosed_notDetected() {
    // Fresh generation (no previous artifact) — scan skipped even with a placeholder comment.
    String html =
        "<html><body><script>\n"
            + "// (其他 KPI 及圖表程式略，保留原本結構)\n"
            + "const x = {};\n"
            + "</script></body></html>";

    assertThat(validator.validate(html, null)).isEmpty();
    assertThat(validator.validate(html, "   ")).isEmpty();
  }

  @Test
  void validate_omissionCommentWithThreeTimesLongerPrevious_gateOpen_detected() {
    // Same html as above — with a previous artifact 3x longer, the gate opens and the scan fires.
    String html =
        "<html><body><script>\n"
            + "// (其他 KPI 及圖表程式略，保留原本結構)\n"
            + "const x = {};\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isNotEmpty();
  }

  @Test
  void validate_omissionCommentButOutputNotShrunk_gateClosed_notDetected() {
    // Output length >= previous * 0.7 — normal iteration, scan skipped.
    String html =
        "<html><body><script>\n"
            + "// (其他 KPI 及圖表程式略，保留原本結構)\n"
            + "const x = {};\n"
            + "</script></body></html>";
    String previousOfEqualLength = "y".repeat(html.length());

    assertThat(validator.validate(html, previousOfEqualLength)).isEmpty();
  }

  // ── OV2: string literal with ZH strategy word — must NOT fire ─────────────

  @Test
  void validate_stringLiteralWithStrategyWord_notDetected() {
    // '策略分析' is not in the pattern list; also it is inside a string literal.
    String html = "<html><body><script>\n" + "const label = '策略分析';\n" + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isEmpty();
  }

  // ── OV3: EN pattern inside string literal — must NOT fire ─────────────────

  @Test
  void validate_omittedInsideStringLiteral_notDetected() {
    String html =
        "<html><body><script>\n"
            + "const msg = 'This value is omitted for brevity';\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isEmpty();
  }

  // ── OV4: HTML comment with EN pattern — must fire ─────────────────────────

  @Test
  void validate_htmlCommentRestOfCodeUnchanged_detected() {
    String html =
        "<html><body>\n"
            + "<!-- rest of code unchanged -->\n"
            + "<div>content</div>\n"
            + "</body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isNotEmpty();
    assertThat(findings.get(0).commentText()).contains("unchanged");
  }

  // ── OV4b: C3 — regex literal containing a quote must not hide a later omission ─────

  @Test
  void validate_regexLiteralContainingQuote_doesNotHideLaterOmissionMarker() {
    // The branch's own motivating C3 input: `name.replace(/'/g, '')`. Before delegating script
    // boundary detection to JsSyntaxValidator's regex-aware scanner, this validator's own
    // findInlineScriptEnd had no regex state at all -- its `/` branch fell straight through to
    // `pos++` -- so the `'` inside the regex opened a fake single-quote state that never
    // closed, and the script span was computed as running to html.length(). Everything after
    // the real </script>, including the HTML comment placeholder marker below, was then
    // wrongly considered "inside a script span" and skipped by the HTML-comment scanner.
    String html =
        "<html><body>\n"
            + "<script>\n"
            + "const clean = name.replace(/'/g, '');\n"
            + "</script>\n"
            + "<!-- 其餘不變 -->\n"
            + "</body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isNotEmpty();
    assertThat(findings.get(0).commentText()).contains("其餘不變");
  }

  // ── OV4c: marker comment after a quote-containing regex, same script block ────

  @Test
  void validate_markerCommentAfterRegexLiteralInSameBlock_detected() {
    // D2 (delta review): OV4b proved the *span boundary* is regex-aware after delegating to
    // JsSyntaxValidator.findScriptEnd, but scanJsComments carried its own separate,
    // regex-blind state machine to walk that span looking for placeholder comments. Here the
    // marker comment is inside the same block as the regex, not past the </script> tag: at
    // `/'/g` the `'` opens STATE_SINGLE_QUOTE, the closing `'` of `''` re-closes it, and the
    // second `'` of `''` reopens it -- so without regex-literal awareness in the comment
    // scanner itself, that fake string state never closes before the block ends, and the
    // trailing `//` marker is never recognized as a comment at all.
    String html =
        "<html><body><script>\n"
            + "const clean = name.replace(/'/g, '');\n"
            + "// 其餘不變\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isNotEmpty();
    assertThat(findings.get(0).commentText()).contains("其餘不變");
  }

  // ── OV5: clean full HTML — zero findings ──────────────────────────────────

  @Test
  void validate_cleanFullHtml_zeroFindings() {
    // A complete, well-formed dashboard HTML with no placeholder comments.
    String html =
        "<!DOCTYPE html>\n"
            + "<html lang=\"zh-TW\"><head><meta charset=\"UTF-8\">\n"
            + "<title>Dashboard</title>\n"
            + "<script src=\"https://cdn.tailwindcss.com\"></script>\n"
            + "<script src=\"https://cdn.jsdelivr.net/npm/echarts@5\"></script>\n"
            + "</head><body>\n"
            + "<!-- Banner -->\n"
            + "<header class=\"bg-slate-800 text-white px-8 py-5\">\n"
            + "  <h1>Vt 製程監控儀表板</h1>\n"
            + "</header>\n"
            + "<script>\n"
            + "const data = window.__ERD_DATA__?.sales ?? [];\n"
            + "const chart = echarts.init(document.getElementById('chart'));\n"
            + "// Draw the chart\n"
            + "const opts = { series: [{ type: 'bar', data: data.map(r => r[1]) }] };\n"
            + "chart.setOption(opts);\n"
            + "</script>\n"
            + "</body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isEmpty();
  }

  // ── OV6: ZH block comment ─────────────────────────────────────────────────

  @Test
  void validate_zhPatternInJsBlockComment_detected() {
    String html =
        "<html><body><script>\n"
            + "/* 其餘不變，保留原本程式 */\n"
            + "const x = 1;\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isNotEmpty();
    assertThat(findings.get(0).commentText()).contains("其餘不變");
  }

  // ── OV7: EN pattern in JS line comment — case-insensitive ─────────────────

  @Test
  void validate_enPatternInJsLineComment_caseInsensitive_detected() {
    String html =
        "<html><body><script>\n"
            + "// Rest Of The Code Remains The Same\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isNotEmpty();
  }

  // ── OV8: external script skipped — no false positive inside src block ─────

  @Test
  void validate_externalScriptBlock_notScanned() {
    // A placeholder-style comment appears inside an external script element's content.
    // The content between the tags must be ignored since it's an external script.
    String html =
        "<html><body>"
            + "<script src=\"https://cdn.example.com/lib.js\">// code omitted</script>"
            + "<script>const x = 1;</script>"
            + "</body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isEmpty();
  }

  // ── OV9: double-quoted string with EN pattern — not detected ──────────────

  @Test
  void validate_enPatternInDoubleQuotedString_notDetected() {
    String html =
        "<html><body><script>\n"
            + "const note = \"code omitted from this string is fine\";\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isEmpty();
  }

  // ── OV10: multiple findings in same html ──────────────────────────────────

  @Test
  void validate_multiplePlaceholderComments_allDetected() {
    String html =
        "<html><body>\n"
            + "<!-- rest of the code -->\n"
            + "<script>\n"
            + "// 其餘相同\n"
            + "/* 同前一版 */\n"
            + "</script>\n"
            + "</body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).hasSizeGreaterThanOrEqualTo(3);
  }

  // ── OV11: null or blank html — no crash ───────────────────────────────────

  @Test
  void validate_nullHtml_returnsEmpty() {
    assertThat(validator.validate(null, "some previous html")).isEmpty();
  }

  @Test
  void validate_blankHtml_returnsEmpty() {
    assertThat(validator.validate("   ", "some previous html")).isEmpty();
  }

  // ── OV12: template literal containing pattern — not detected ──────────────

  @Test
  void validate_patternInsideTemplateLiteral_notDetected() {
    String html =
        "<html><body><script>\n"
            + "const msg = `This is omitted for brevity`;\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isEmpty();
  }

  // ── OV13: removed bare patterns must not fire even with gate open ─────────

  @Test
  void validate_bareRemovedPatternsInComments_notDetected() {
    // Bare "省略" / "unchanged" / "omitted" / "placeholder" were removed from the pattern lists —
    // these comments are legitimate and must not match even when the shrinkage gate is open.
    String html =
        "<html><body><script>\n"
            + "// 此欄位可省略\n"
            + "// this flag is unchanged by user input\n"
            + "// tooltip omitted when value is null\n"
            + "// render a placeholder while loading\n"
            + "const x = 1;\n"
            + "</script></body></html>";

    List<CodeOmissionFinding> findings =
        validator.validate(html, previousThreeTimesLongerThan(html));

    assertThat(findings).isEmpty();
  }
}
