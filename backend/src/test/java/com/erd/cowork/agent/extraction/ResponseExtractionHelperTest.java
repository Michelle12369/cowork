package com.erd.cowork.agent.extraction;

import static java.util.stream.Collectors.joining;
import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.event.TokenEvent;
import java.util.List;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.Flux;

class ResponseExtractionHelperTest {

  // ── helpers ──────────────────────────────────────────────────────────────────

  private static String deltas(List<AgentEvent> events) {
    return events.stream()
        .filter(event -> event instanceof TokenEvent)
        .map(event -> ((TokenEvent) event).delta())
        .collect(joining());
  }

  private static List<StepEvent> steps(List<AgentEvent> events) {
    return events.stream()
        .filter(event -> event instanceof StepEvent)
        .map(event -> (StepEvent) event)
        .toList();
  }

  private static String codeDeltas(List<AgentEvent> events) {
    return events.stream()
        .filter(event -> event instanceof com.erd.cowork.agent.event.CodeEvent)
        .map(event -> ((com.erd.cowork.agent.event.CodeEvent) event).delta())
        .collect(joining());
  }

  // ── spec 1 + 2 + 3: single-token complete fence ──────────────────────────────

  @Test
  void singleToken_completeFence_htmlExtractedAndTextEmitted() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer
            .apply(Flux.just("before\n```html\n<div>x</div>\n```\nafter"))
            .collectList()
            .block();

    assertThat(events).isNotNull();
    // All emitted events must be TokenEvents or CodeEvents (no fenced content leaks as text)
    assertThat(events)
        .allSatisfy(
            event ->
                assertThat(event)
                    .satisfiesAnyOf(
                        it -> assertThat(it).isInstanceOf(TokenEvent.class),
                        it ->
                            assertThat(it)
                                .isInstanceOf(com.erd.cowork.agent.event.CodeEvent.class)));
    // CODE deltas carry the exact fence content
    assertThat(codeDeltas(events)).isEqualTo("<div>x</div>\n");
    // Delta concatenation equals the non-fenced text
    assertThat(deltas(events)).isEqualTo("before\n\nafter");
    // HTML is the inner content (trimmed)
    assertThat(transformer.result().html()).isEqualTo("<div>x</div>");
    // answerText is the trimmed non-fenced text
    assertThat(transformer.result().answerText()).isEqualTo("before\n\nafter");
  }

  // ── spec 1: cross-token fence markers ────────────────────────────────────────

  @Test
  void crossTokenFence_openAndCloseSpanMultipleTokens_htmlExtracted() {
    var transformer = new ResponseExtractionHelper();
    // Fence markers deliberately split: "pre``" + "`ht" + "ml\n..." + "...\n``" + "`post"
    List<AgentEvent> events =
        transformer
            .apply(Flux.just("pre``", "`ht", "ml\n<p>", "hi</p>\n``", "`post"))
            .collectList()
            .block();

    assertThat(events).isNotNull();
    String concat = deltas(events);
    // Fence-outside text must be present and fence-inside text must not leak
    assertThat(concat).contains("pre");
    assertThat(concat).contains("post");
    assertThat(concat).doesNotContain("```html");
    assertThat(concat).doesNotContain("<p>");
    assertThat(concat).doesNotContain("hi</p>");
    // Exact delta concatenation (implementation-precise)
    assertThat(concat).isEqualTo("prepost");
    assertThat(transformer.result().html()).isEqualTo("<p>hi</p>");
    assertThat(transformer.result().answerText()).isEqualTo("prepost");
  }

  // ── spec 3 / no fence: all tokens become TokenEvents, html is null ────────────

  @Test
  void noFence_plainText_allTokenEventsHtmlNull() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer.apply(Flux.just("hello", " world", "!")).collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).allSatisfy(event -> assertThat(event).isInstanceOf(TokenEvent.class));
    assertThat(deltas(events)).isEqualTo("hello world!");
    assertThat(transformer.result().html()).isNull();
    assertThat(transformer.result().answerText()).isEqualTo("hello world!");
  }

  // ── spec 4: multiple blocks — only the first captured, rest flow out ──────────

  @Test
  void multipleBlocks_firstGoesToHtml_secondFlowsOutAsText() {
    var transformer = new ResponseExtractionHelper();
    String input = "text1\n```html\n<div>1</div>\n```\ntext2\n```html\n<div>2</div>\n```\ntext3";
    List<AgentEvent> events = transformer.apply(Flux.just(input)).collectList().block();

    assertThat(events).isNotNull();
    String concat = deltas(events);
    // Exact delta concatenation (second block flows out verbatim, first does not)
    assertThat(concat).isEqualTo("text1\n\ntext2\n```html\n<div>2</div>\n```\ntext3");
    // First block must NOT appear in text deltas
    assertThat(concat).doesNotContain("<div>1</div>");
    // HTML is from first block only
    assertThat(transformer.result().html()).isEqualTo("<div>1</div>");
    assertThat(transformer.result().answerText())
        .isEqualTo("text1\n\ntext2\n```html\n<div>2</div>\n```\ntext3");
  }

  // ── spec 5: unclosed fence — HTML holds whatever was collected ────────────────

  @Test
  void unclosedFence_htmlContainsCollectedContent() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer.apply(Flux.just("intro\n```html\n<partial>content")).collectList().block();

    assertThat(events).isNotNull();
    // Only "intro\n" flows as text before the fence is opened
    assertThat(deltas(events)).isEqualTo("intro\n");
    // Unclosed fence: HTML holds whatever was collected
    assertThat(transformer.result().html()).isEqualTo("<partial>content");
    assertThat(transformer.result().answerText()).isEqualTo("intro");
  }

  // ── spec 6: empty stream ──────────────────────────────────────────────────────

  @Test
  void emptyStream_answerTextEmpty_htmlNull() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events = transformer.apply(Flux.empty()).collectList().block();

    assertThat(events).isNotNull();
    assertThat(events).isEmpty();
    assertThat(transformer.result().answerText()).isEqualTo("");
    assertThat(transformer.result().html()).isNull();
  }

  // ── false-positive: partial backticks that are not a fence ────────────────────

  @Test
  void falsePositive_doubleAndSingleBacktick_allFlowOutUnchanged() {
    var transformer = new ResponseExtractionHelper();
    // "a``b`c" — has backticks but no "```html" pattern
    List<AgentEvent> events = transformer.apply(Flux.just("a``b`c")).collectList().block();

    assertThat(events).isNotNull();
    assertThat(deltas(events)).isEqualTo("a``b`c");
    assertThat(transformer.result().html()).isNull();
  }

  @Test
  void falsePositive_threeBackticksNonHtmlFence_allFlowOut() {
    var transformer = new ResponseExtractionHelper();
    // ```python block — not ```html, must flow out entirely
    List<AgentEvent> events =
        transformer.apply(Flux.just("```python\ncode\n```")).collectList().block();

    assertThat(events).isNotNull();
    assertThat(deltas(events)).isEqualTo("```python\ncode\n```");
    assertThat(transformer.result().html()).isNull();
  }

  // ── additional: fence header with trailing spaces ignored ─────────────────────

  @Test
  void fenceWithTrailingSpaceInHeader_htmlExtracted() {
    var transformer = new ResponseExtractionHelper();
    // "```html  \n" — trailing spaces before newline are ignored per spec
    List<AgentEvent> events =
        transformer.apply(Flux.just("```html  \n<b>bold</b>\n```")).collectList().block();

    assertThat(events).isNotNull();
    assertThat(deltas(events)).isEmpty();
    assertThat(transformer.result().html()).isEqualTo("<b>bold</b>");
    assertThat(transformer.result().answerText()).isEqualTo("");
  }

  // ── additional: single-token unclosed (no newline in "```html") ───────────────

  @Test
  void incompleteMarker_noNewlineEver_treatedAsPlainText() {
    var transformer = new ResponseExtractionHelper();
    // "```html" appears but stream ends without a newline — treat as plain text
    List<AgentEvent> events = transformer.apply(Flux.just("```html")).collectList().block();

    assertThat(events).isNotNull();
    assertThat(deltas(events)).isEqualTo("```html");
    assertThat(transformer.result().html()).isNull();
  }

  // ── extractBareHtmlDocument ────────────────────────────────────────────────

  @Test
  void extractBareHtmlDocument_doctypeDocument_returnsFullDocument() {
    String text =
        "Here is your dashboard:\n"
            + "<!DOCTYPE html>\n"
            + "<html><head></head><body>hi</body></html>\n"
            + "Done.";
    String result = ResponseExtractionHelper.extractBareHtmlDocument(text);
    assertThat(result).isEqualTo("<!DOCTYPE html>\n<html><head></head><body>hi</body></html>");
  }

  @Test
  void extractBareHtmlDocument_htmlTagOnlyNoDoctype_returnsFromHtmlTag() {
    String text = "prefix <html lang=\"en\"><body>x</body></html> suffix";
    String result = ResponseExtractionHelper.extractBareHtmlDocument(text);
    assertThat(result).isEqualTo("<html lang=\"en\"><body>x</body></html>");
  }

  @Test
  void extractBareHtmlDocument_mixedCaseMarkers_returnsDocument() {
    String text = "<!doctype HTML><HTML><BODY>y</BODY></HTML>";
    String result = ResponseExtractionHelper.extractBareHtmlDocument(text);
    assertThat(result).isEqualTo("<!doctype HTML><HTML><BODY>y</BODY></HTML>");
  }

  @Test
  void extractBareHtmlDocument_missingClosingHtml_returnsNull() {
    String text = "<!DOCTYPE html><html><body>truncated";
    assertThat(ResponseExtractionHelper.extractBareHtmlDocument(text)).isNull();
  }

  @Test
  void extractBareHtmlDocument_plainTextWithoutHtml_returnsNull() {
    assertThat(ResponseExtractionHelper.extractBareHtmlDocument("just a chat answer")).isNull();
  }

  @Test
  void extractBareHtmlDocument_nullOrBlank_returnsNull() {
    assertThat(ResponseExtractionHelper.extractBareHtmlDocument(null)).isNull();
    assertThat(ResponseExtractionHelper.extractBareHtmlDocument("   ")).isNull();
  }

  @Test
  void extractBareHtmlDocument_explanationBeforeAndAfter_extractsOnlyDocument() {
    String text =
        "以下是儀表板\n<!DOCTYPE html>\n<html>\n<head><title>t</title></head>\n"
            + "<body><div id=\"c\"></div></body>\n</html>\n希望這有幫助！";
    String result = ResponseExtractionHelper.extractBareHtmlDocument(text);
    assertThat(result).startsWith("<!DOCTYPE html>");
    assertThat(result).endsWith("</html>");
    assertThat(result).doesNotContain("希望這有幫助");
  }

  // ── [[step:]] markers ───────────────────────────────────────────────────────────

  @Test
  void stepMarker_singleToken_emitsRunningAndSuccessOnComplete() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer.apply(Flux.just("[[step: Planning]]\nhello")).collectList().block();
    List<StepEvent> stepEvents = steps(events);
    assertThat(stepEvents).hasSize(2);
    assertThat(stepEvents.get(0))
        .isEqualTo(new StepEvent("d1", "Planning", null, StepStatus.RUNNING));
    assertThat(stepEvents.get(1))
        .isEqualTo(new StepEvent("d1", "Planning", null, StepStatus.SUCCESS));
    assertThat(deltas(events)).isEqualTo("hello");
    assertThat(transformer.result().answerText()).isEqualTo("hello");
  }

  @Test
  void stepMarker_crossToken_splitThreeWays_detected() {
    var transformer = new ResponseExtractionHelper();
    // "[[st" + "ep: " + "Foo]]" + "\ntext"
    List<AgentEvent> events =
        transformer.apply(Flux.just("[[st", "ep: ", "Foo]]", "\ntext")).collectList().block();
    List<StepEvent> stepEvents = steps(events);
    assertThat(stepEvents).hasSize(2);
    assertThat(stepEvents.get(0).stepKey()).isEqualTo("d1");
    assertThat(stepEvents.get(0).title()).isEqualTo("Foo");
    assertThat(stepEvents.get(0).status()).isEqualTo(StepStatus.RUNNING);
    assertThat(stepEvents.get(1).status()).isEqualTo(StepStatus.SUCCESS);
    assertThat(deltas(events)).isEqualTo("text");
  }

  @Test
  void stepMarker_falsePrefix_notAtLineStart_flowsAsText() {
    var transformer = new ResponseExtractionHelper();
    // "[[step: x]]" in middle of a line — not at line start
    List<AgentEvent> events =
        transformer.apply(Flux.just("foo [[step: x]]\n")).collectList().block();
    assertThat(steps(events)).isEmpty();
    assertThat(deltas(events)).isEqualTo("foo [[step: x]]\n");
  }

  @Test
  void stepMarker_unclosed_streamEnds_flowsAsPlainText() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events = transformer.apply(Flux.just("[[step: x")).collectList().block();
    assertThat(steps(events)).isEmpty();
    assertThat(deltas(events)).isEqualTo("[[step: x");
  }

  @Test
  void stepMarker_multiple_eachFinalizePrevious() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer.apply(Flux.just("[[step: A]]\n[[step: B]]\ntext")).collectList().block();
    List<StepEvent> stepEvents = steps(events);
    // d1 RUNNING, d1 SUCCESS (when d2 appears), d2 RUNNING, d2 SUCCESS (on complete)
    assertThat(stepEvents).hasSize(4);
    assertThat(stepEvents.get(0)).isEqualTo(new StepEvent("d1", "A", null, StepStatus.RUNNING));
    assertThat(stepEvents.get(1)).isEqualTo(new StepEvent("d1", "A", null, StepStatus.SUCCESS));
    assertThat(stepEvents.get(2)).isEqualTo(new StepEvent("d2", "B", null, StepStatus.RUNNING));
    assertThat(stepEvents.get(3)).isEqualTo(new StepEvent("d2", "B", null, StepStatus.SUCCESS));
    assertThat(deltas(events)).isEqualTo("text");
  }

  @Test
  void stepMarker_leadingWhitespace_tolerant() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer.apply(Flux.just("\n   [[step: Trimmed Title]]\n")).collectList().block();
    List<StepEvent> stepEvents = steps(events);
    assertThat(stepEvents).hasSize(2);
    assertThat(stepEvents.get(0).title()).isEqualTo("Trimmed Title");
    // The \n before the step line flows as text; leading spaces are consumed with the marker
    assertThat(deltas(events)).isEqualTo("\n");
  }

  @Test
  void stepMarker_titleWithBracket_tolerated() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer.apply(Flux.just("[[step: foo [bar]]]\nend")).collectList().block();
    List<StepEvent> stepEvents = steps(events);
    // title is "foo [bar" trimmed (first ]] closes the marker)
    assertThat(stepEvents).isNotEmpty();
    assertThat(deltas(events)).contains("end");
  }

  // ── ```questions fence ─────────────────────────────────────────────────────────

  @Test
  void questionsBlock_validJson_extractedNotFlowedOut() {
    var transformer = new ResponseExtractionHelper();
    String input =
        "intro\n"
            + "```questions\n"
            + "[{\"text\":\"Q?\",\"options\":[\"Yes\",\"No\"],\"multiSelect\":false}]\n"
            + "```\n"
            + "end";
    List<AgentEvent> events = transformer.apply(Flux.just(input)).collectList().block();
    // questions content must NOT appear in token deltas
    assertThat(deltas(events)).doesNotContain("Q?").doesNotContain("Yes");
    assertThat(deltas(events)).isEqualTo("intro\nend");
    assertThat(transformer.result().questions()).isNotNull().hasSize(1);
    com.erd.cowork.agent.model.ClarifyingQuestion question =
        transformer.result().questions().get(0);
    assertThat(question.text()).isEqualTo("Q?");
    assertThat(question.options()).containsExactly("Yes", "No");
    assertThat(question.multiSelect()).isFalse();
  }

  @Test
  void questionsBlock_invalidJson_emittedAsPlainText() {
    var transformer = new ResponseExtractionHelper();
    String input = "```questions\nnot valid json\n```";
    List<AgentEvent> events = transformer.apply(Flux.just(input)).collectList().block();
    assertThat(transformer.result().questions()).isNull();
    // The raw block is emitted as text
    String text = deltas(events);
    assertThat(text).contains("not valid json");
  }

  @Test
  void questionsBlock_onlyFirstCaptured_secondFlowsOut() {
    var transformer = new ResponseExtractionHelper();
    String q1 = "[{\"text\":\"Q1?\",\"options\":[],\"multiSelect\":false}]";
    String q2 = "[{\"text\":\"Q2?\",\"options\":[],\"multiSelect\":false}]";
    String input = "```questions\n" + q1 + "\n```\ntext\n```questions\n" + q2 + "\n```";
    List<AgentEvent> events = transformer.apply(Flux.just(input)).collectList().block();
    assertThat(transformer.result().questions()).hasSize(1);
    assertThat(transformer.result().questions().get(0).text()).isEqualTo("Q1?");
    // Second block flows out as text
    assertThat(deltas(events)).contains("Q2?");
  }

  @Test
  void questionsAndHtml_coexist_bothExtracted() {
    var transformer = new ResponseExtractionHelper();
    String input =
        "```questions\n"
            + "[{\"text\":\"Q?\",\"options\":[],\"multiSelect\":false}]\n"
            + "```\n"
            + "```html\n"
            + "<b>hi</b>\n"
            + "```";
    List<AgentEvent> events = transformer.apply(Flux.just(input)).collectList().block();
    assertThat(transformer.result().questions()).hasSize(1);
    assertThat(transformer.result().html()).isEqualTo("<b>hi</b>");
    assertThat(deltas(events)).isEmpty();
  }

  @Test
  void noQuestionsBlock_questionsResultIsNull() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events = transformer.apply(Flux.just("hello")).collectList().block();
    assertThat(transformer.result().questions()).isNull();
  }

  // ── Fix: DONE_HTML state now scans protocol markers ───────────────────────────

  /**
   * Regression for spec violation: [[step:]] markers appearing after the html fence must be
   * recognised even when the transformer is in DONE_HTML state. Counter-example from review:
   * [[step: A]] → ```html block → [[step: Done]].
   */
  @Test
  void stepMarker_afterHtmlFence_recognizedInDoneHtmlState() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer
            .apply(Flux.just("[[step: A]]\n", "```html\n<div/>\n```", "\n[[step: Done]]\n"))
            .collectList()
            .block();

    assertThat(events).isNotNull();
    List<StepEvent> stepEvents = steps(events);
    // d1 RUNNING, d1 SUCCESS (finalised when d2 appears), d2 RUNNING, d2 SUCCESS (on complete)
    assertThat(stepEvents).hasSize(4);
    assertThat(stepEvents.get(0)).isEqualTo(new StepEvent("d1", "A", null, StepStatus.RUNNING));
    assertThat(stepEvents.get(1)).isEqualTo(new StepEvent("d1", "A", null, StepStatus.SUCCESS));
    assertThat(stepEvents.get(2)).isEqualTo(new StepEvent("d2", "Done", null, StepStatus.RUNNING));
    assertThat(stepEvents.get(3)).isEqualTo(new StepEvent("d2", "Done", null, StepStatus.SUCCESS));
    // No [[step: text leaks into the token stream
    assertThat(deltas(events)).doesNotContain("[[step:");
    // answerText is clean
    assertThat(transformer.result().answerText()).doesNotContain("[[step:");
    // HTML extraction must not be affected
    assertThat(transformer.result().html()).isEqualTo("<div/>");
  }

  // ── Fix: questions close fence async newline consume ──────────────────────────

  /**
   * Regression: when the close fence {@code ```} lands at the very end of a token and the trailing
   * {@code \n} arrives in the next token, that newline must NOT bleed into answerText.
   */
  @Test
  void questionsBlock_closeFenceAtTokenBoundary_newlineNotLeakedToAnswerText() {
    var transformer = new ResponseExtractionHelper();
    String q1 = "[{\"text\":\"Q?\",\"options\":[],\"multiSelect\":false}]";
    // "```" is the very last chars of the first token; "\n" leads the second token
    List<AgentEvent> events =
        transformer
            .apply(Flux.just("```questions\n" + q1 + "\n```", "\nafter"))
            .collectList()
            .block();

    assertThat(events).isNotNull();
    // The leading "\n" of the second token is the close-fence trailer — must not appear in deltas
    assertThat(deltas(events)).isEqualTo("after");
    assertThat(transformer.result().answerText()).isEqualTo("after");
    assertThat(transformer.result().questions()).isNotNull().hasSize(1);
    assertThat(transformer.result().questions().get(0).text()).isEqualTo("Q?");
  }

  // ── CODE event emission ───────────────────────────────────────────────────────

  @Test
  void apply_completeFence_codeEventsCarryFenceContent() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer
            .apply(Flux.just("before\n```html\n<div>x</div>\n```\nafter"))
            .collectList()
            .block();
    assertThat(events).isNotNull();
    assertThat(codeDeltas(events)).isEqualTo("<div>x</div>\n");
    // TOKEN channel unchanged
    assertThat(deltas(events)).isEqualTo("before\n\nafter");
    assertThat(transformer.result().html()).isEqualTo("<div>x</div>");
  }

  @Test
  void apply_crossTokenFence_codeEventsConcatenateToHtml() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer
            .apply(Flux.just("pre``", "`ht", "ml\n<p>", "hi</p>\n``", "`post"))
            .collectList()
            .block();
    assertThat(events).isNotNull();
    assertThat(codeDeltas(events)).isEqualTo("<p>hi</p>\n");
    assertThat(deltas(events)).isEqualTo("prepost");
  }

  @Test
  void apply_unclosedFence_flushEmitsCodeEvents() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events =
        transformer.apply(Flux.just("x\n```html\n<div>partial")).collectList().block();
    assertThat(events).isNotNull();
    assertThat(codeDeltas(events)).isEqualTo("<div>partial");
  }

  @Test
  void apply_noFence_noCodeEvents() {
    var transformer = new ResponseExtractionHelper();
    List<AgentEvent> events = transformer.apply(Flux.just("hello", " world")).collectList().block();
    assertThat(events).isNotNull();
    assertThat(events).noneMatch(event -> event instanceof com.erd.cowork.agent.event.CodeEvent);
  }

  @Test
  void apply_secondHtmlFence_noCodeEventsForSecondBlock() {
    var transformer = new ResponseExtractionHelper();
    String input = "t1\n```html\n<div>1</div>\n```\nt2\n```html\n<div>2</div>\n```\nt3";
    List<AgentEvent> events = transformer.apply(Flux.just(input)).collectList().block();
    assertThat(events).isNotNull();
    assertThat(codeDeltas(events)).isEqualTo("<div>1</div>\n");
  }
}
