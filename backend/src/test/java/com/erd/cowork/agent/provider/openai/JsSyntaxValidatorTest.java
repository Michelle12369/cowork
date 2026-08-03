package com.erd.cowork.agent.provider.openai;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class JsSyntaxValidatorTest {

  private JsSyntaxValidator validator;

  @BeforeEach
  void setUp() {
    validator = new JsSyntaxValidator();
  }

  // ── E1: syntax error detected with line number ─────────────────────────────

  @Test
  void validate_unclosedBrace_returnsErrorWithLineNumber() {
    String html =
        "<html><body><script>\n"
            + "function foo() {\n"
            + "  return 1;\n"
            + "// unclosed brace\n"
            + "</script></body></html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    assertThat(errors.get(0).scriptIndex()).isEqualTo(0);
    assertThat(errors.get(0).line()).isGreaterThan(0);
    assertThat(errors.get(0).message()).isNotBlank();
  }

  @Test
  void validate_unclosedStringLiteral_returnsError() {
    String html = "<html><script>const msg = \"hello world;</script></html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    assertThat(errors.get(0).scriptIndex()).isEqualTo(0);
  }

  // ── E2: modern JS syntax — zero false positives ────────────────────────────

  @Test
  void validate_optionalChaining_noFalsePositive() {
    String html =
        "<html><script>\n"
            + "const x = obj?.prop?.nested;\n"
            + "const y = arr?.[0];\n"
            + "const z = fn?.();\n"
            + "</script></html>";

    assertThat(validator.validate(html)).isEmpty();
  }

  @Test
  void validate_nullishCoalescing_noFalsePositive() {
    String html =
        "<html><script>\n"
            + "const a = value ?? 'default';\n"
            + "const b = obj?.x ?? [];\n"
            + "</script></html>";

    assertThat(validator.validate(html)).isEmpty();
  }

  @Test
  void validate_arrowFunctions_noFalsePositive() {
    String html =
        "<html><script>\n"
            + "const add = (a, b) => a + b;\n"
            + "const ids = items.map(item => item.id);\n"
            + "const greet = name => `Hello, ${name}!`;\n"
            + "</script></html>";

    assertThat(validator.validate(html)).isEmpty();
  }

  @Test
  void validate_templateLiterals_noFalsePositive() {
    String html =
        "<html><script>\n"
            + "const greeting = `Hello ${name}, you have ${count} messages.`;\n"
            + "const multi = `line1\n"
            + "line2`;\n"
            + "</script></html>";

    assertThat(validator.validate(html)).isEmpty();
  }

  @Test
  void validate_spreadOperator_noFalsePositive() {
    String html =
        "<html><script>\n"
            + "const merged = { ...obj1, ...obj2 };\n"
            + "const arr = [...a, ...b];\n"
            + "function foo(...args) { return args; }\n"
            + "</script></html>";

    assertThat(validator.validate(html)).isEmpty();
  }

  // ── E3: multiple script blocks — index correctness ─────────────────────────

  @Test
  void validate_twoScriptBlocks_secondBroken_indexIsOne() {
    String html =
        "<html><head>\n"
            + "<script>const valid = true;</script>\n"
            + "</head><body>\n"
            + "<script>const x = {</script>\n"
            + "</body></html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    // All errors must come from script block index 1 (the second block)
    assertThat(errors).allMatch(e -> e.scriptIndex() == 1);
  }

  @Test
  void validate_srcScriptThenBrokenInline_indexIsOne() {
    String html =
        "<html>"
            + "<script src=\"/vendor/echarts-v5.min.js\"></script>"
            + "<script>const x = {</script>"
            + "</html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    assertThat(errors).allMatch(e -> e.scriptIndex() == 1);
  }

  @Test
  void validate_closingScriptTagInsideStringLiteral_scriptParsedAsWholeBlock() {
    // The </script> inside the string literal must NOT terminate the block.
    // The whole script is valid JS, so no errors should be reported.
    String html =
        "<html><script>\n"
            + "const tooltip = () => '<div></scr' + 'ipt>';\n" // safe split — control
            + "const raw = \"</script>\";\n" // the dangerous literal
            + "const after = 1;\n"
            + "</script><script>const second = {</script></html>";

    List<JsSyntaxError> errors = validator.validate(html);

    // Only the second block is broken; index must be 1 (not shifted by a phantom split).
    assertThat(errors).isNotEmpty();
    assertThat(errors).allMatch(e -> e.scriptIndex() == 1);
  }

  // ── E4: src-only scripts are skipped ──────────────────────────────────────

  @Test
  void validate_srcScript_skipped() {
    String html =
        "<html><body>"
            + "<script src=\"/vendor/echarts-v5.min.js\"></script>"
            + "<script>const x = {};</script>"
            + "</body></html>";

    assertThat(validator.validate(html)).isEmpty();
  }

  // ── E5: no script at all ───────────────────────────────────────────────────

  @Test
  void validate_htmlWithNoScript_returnsEmpty() {
    String html = "<html><body><p>Hello world</p></body></html>";
    assertThat(validator.validate(html)).isEmpty();
  }

  // ── E7: regex literal state machine (C3) ────────────────────────────────────

  @Test
  void validate_regexLiteralContainingQuote_doesNotHideLaterScriptBlock() {
    // Before the fix: the `'` inside the regex opened a fake single-quote state that never
    // closed, findScriptEnd returned html.length(), and the second <script> block became
    // invisible to the validator entirely -- so its real syntax error (unclosed brace) never
    // surfaced. Also asserts the first block itself is valid (no false positive).
    String html =
        "<html><script>\n"
            + "const clean = name.replace(/'/g, '');\n"
            + "</script>\n"
            + "<script>const second = {</script>"
            + "</html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    assertThat(errors).allMatch(e -> e.scriptIndex() == 1);
  }

  @Test
  void validate_regexLiteralContainingDoubleQuote_doesNotHideLaterScriptBlock() {
    String html =
        "<html><script>\n"
            + "const clean = name.replace(/\"/g, \"\");\n"
            + "</script>\n"
            + "<script>const second = {</script>"
            + "</html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    assertThat(errors).allMatch(e -> e.scriptIndex() == 1);
  }

  @Test
  void validate_divisionChain_isNotMisreadAsRegex() {
    // The opposite direction: `a / b / c` is two divisions, not a regex whose body is
    // " b " and whose second `/` terminates it -- the whole line must still parse as valid JS.
    String html = "<html><script>const result = a / b / c;</script></html>";

    assertThat(validator.validate(html)).isEmpty();
  }

  @Test
  void validate_regexAfterReturnKeyword_noFalsePositive() {
    // `return` ends in an identifier character but syntactically expects an expression --
    // the regex-context keyword exception must cover it.
    String html = "<html><script>function t(x) { return /'/.test(x); }</script></html>";

    assertThat(validator.validate(html)).isEmpty();
  }

  @Test
  void validate_regexAfterArrowFunction_doesNotHideLaterScriptBlock() {
    // `=>` ends in `>`, which the `<`/`>` exclusion in isRegexContext would otherwise treat as
    // division-like -- but an arrow function body always expects an expression next. Before the
    // fix, the `'` inside the regex opened a fake single-quote state that never closed,
    // findScriptEnd ran to html.length(), and the second <script> block became invisible to the
    // validator (same failure class as the quote-in-regex case above, triggered via `=>` instead
    // of a bare `/`).
    String html =
        "<html><script>\n"
            + "const clean = s => /'/.test(s);\n"
            + "</script>\n"
            + "<script>const second = {</script>"
            + "</html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    assertThat(errors).allMatch(e -> e.scriptIndex() == 1);
  }

  @Test
  void validate_closingHtmlTagSlash_isNotMisreadAsRegexStart() {
    // `<`/`>` are excluded from "expression expected" precisely so that the `/` in a closing
    // tag like `</div>` is never treated as opening a regex literal -- if it were, the regex
    // state would run past the real `</script>` terminator and swallow the rest of the document.
    String html =
        "<html><script>const x = 1; /* </div> */</script><script>const second = {</script></html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    assertThat(errors).allMatch(e -> e.scriptIndex() == 1);
  }

  @Test
  void validate_regexCharacterClassContainingSlash_isNotMisreadAsTerminator() {
    // A `/` inside a regex character class (`[...]`) must not be treated as the regex's closing
    // delimiter -- without character-class tracking, the first `/` inside `[/']` would close the
    // regex early, and the stray `'` right after would open a fake string that never closes,
    // hiding the second script block.
    String html =
        "<html><script>\n"
            + "const pattern = /[/']/.test(x);\n"
            + "</script>\n"
            + "<script>const second = {</script>"
            + "</html>";

    List<JsSyntaxError> errors = validator.validate(html);

    assertThat(errors).isNotEmpty();
    assertThat(errors).allMatch(e -> e.scriptIndex() == 1);
  }

  // ── E6: valid full modern dashboard HTML — zero errors ────────────────────

  @Test
  void validate_validModernDashboardHtml_returnsEmpty() {
    String html =
        "<!DOCTYPE html>\n"
            + "<html><head><title>Dashboard</title></head><body>\n"
            + "<script src=\"https://cdn.echarts.com/echarts.min.js\"></script>\n"
            + "<script>\n"
            + "const data = window.__ERD_DATA__?.sales ?? [];\n"
            + "const chart = echarts.init(document.getElementById('chart'));\n"
            + "const rows = data.map(r => ({ name: r[0], value: r[1] }));\n"
            + "const opts = { series: [{ type: 'bar', data: [...rows] }] };\n"
            + "chart.setOption(opts);\n"
            + "</script>\n"
            + "</body></html>";

    assertThat(validator.validate(html)).isEmpty();
  }
}
