package com.erd.cowork.agent.extraction;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.CodeEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.ClarifyingQuestion;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import reactor.core.publisher.Flux;

/**
 * Transforms a raw token {@link Flux}{@code <String>} into a {@link Flux}{@code <AgentEvent>},
 * extracting the full structured model response: the first <code>```html</code> fenced block, the
 * first <code>```questions</code> fenced block, any <code>[[step: Title]]</code> line markers, and
 * TOKEN/CODE/STEP events from the stream.
 *
 * <p>Fence markers (both open and close) may span multiple token boundaries. A sliding-window
 * buffer ({@code pending}) is maintained so that partial markers at the tail of each token are
 * never emitted prematurely.
 *
 * <p>{@value #TEXT_KEEP} = 11: the scanning window is sized to the longest marker opener minus 1
 * char, so a partial marker at a token boundary is never emitted prematurely — {@code ```questions}
 * (12 chars) is the binding constraint. {@code FENCE_CLOSE_KEEP} = 2 covers a partial close fence
 * ({@code ```}, 3 chars) the same way, for both IN_HTML and IN_QUESTIONS states.
 *
 * <p><strong>Contract (single-subscription, non-thread-safe):</strong>
 *
 * <ul>
 *   <li>Create one instance per stream; do not reuse across multiple subscriptions.
 *   <li>Do not call from multiple threads concurrently.
 *   <li>{@link #result()} is only valid <em>after</em> the flux returned by {@link #apply} has
 *       terminated (completed or errored).
 * </ul>
 *
 * <p>non-bean: instantiate per stream.
 */
@Slf4j
public class ResponseExtractionHelper {

  private enum State {
    TEXT,
    IN_HTML,
    DONE_HTML,
    IN_QUESTIONS
  }

  /**
   * Chars to retain at end of {@code pending} in TEXT state. {@code ```questions} is 12 chars, so
   * we keep 11 to detect cross-token occurrences (binding constraint).
   */
  private static final int TEXT_KEEP = 11;

  /**
   * Chars to retain at end of {@code pending} in IN_HTML / IN_QUESTIONS states. {@code ```} is 3
   * chars, so we keep 2 to detect cross-token close-fence occurrences.
   */
  private static final int FENCE_CLOSE_KEEP = 2;

  private static final String STEP_OPEN = "[[step:";
  private static final String STEP_CLOSE = "]]";
  private static final String HTML_FENCE = "```html";
  private static final String QUESTIONS_FENCE = "```questions";
  private static final String FENCE_CLOSE = "```";
  private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
  private static final TypeReference<List<ClarifyingQuestion>> QUESTION_LIST_TYPE =
      new TypeReference<>() {};

  private State state = State.TEXT;
  private State prevStateForQuestions = State.TEXT;
  private final StringBuilder pending = new StringBuilder();
  private final StringBuilder answerBuilder = new StringBuilder();
  private final StringBuilder htmlBuilder = new StringBuilder();
  private final StringBuilder questionsBuilder = new StringBuilder();
  private int stepCounter = 0;
  private String lastDynamicStepKey = null;
  private String lastDynamicStepTitle = null;
  private boolean questionsFound = false;
  private List<ClarifyingQuestion> questions = null;

  /**
   * Set to {@code true} when a step-marker line ends exactly at the buffer boundary (so the
   * trailing {@code \n} will arrive in the next token). Cleared and consumed at the top of the next
   * TEXT-state iteration.
   */
  private boolean pendingNewlineConsume = false;

  /** Constructs a new, fresh helper. */
  public ResponseExtractionHelper() {}

  /**
   * Extracts a complete bare HTML document (no ```html fence) from free text. Delegates to {@link
   * BareHtmlUtils#extract(String)} for backward compatibility.
   */
  public static String extractBareHtmlDocument(String text) {
    return BareHtmlUtils.extract(text);
  }

  /**
   * Applies the extraction to a raw token stream.
   *
   * <p>Text outside fenced blocks is emitted as {@link TokenEvent}s. Content inside the first
   * {@code ```html} block is captured into the HTML buffer and simultaneously emitted as {@link
   * CodeEvent} deltas (never as TOKEN). Content inside the first {@code ```questions ... ```} block
   * is parsed as JSON and exposed via {@link #result()}. Each {@code [[step: Title]]} marker at a
   * line start emits a {@link StepEvent} with status RUNNING, and the previous dynamic step (if
   * any) is finalized with SUCCESS.
   *
   * @param tokens upstream token flux (one raw LLM token per element)
   * @return transformed event flux
   */
  public Flux<AgentEvent> apply(Flux<String> tokens) {
    return tokens
        .concatMap(
            token -> {
              pending.append(token);
              return Flux.fromIterable(processBuffer());
            })
        .concatWith(
            Flux.defer(
                () -> {
                  List<AgentEvent> remainingEvents = new ArrayList<>(flushBuffer());
                  remainingEvents.addAll(finalizeLastDynamicStep());
                  return Flux.fromIterable(remainingEvents);
                }));
  }

  /**
   * Returns the extraction result.
   *
   * <p>Must be called only after the flux returned by {@link #apply} has terminated.
   *
   * @return {@link AgentOutcome} with trimmed answer text, HTML (or {@code null}), and parsed
   *     questions (or {@code null})
   */
  public AgentOutcome result() {
    String html = htmlBuilder.length() > 0 ? htmlBuilder.toString().trim() : null;
    return new AgentOutcome(answerBuilder.toString().trim(), html, questions);
  }

  /**
   * Processes the current {@code pending} buffer in a tight loop until no further progress is
   * possible. Returns all {@link AgentEvent}s generated.
   */
  private List<AgentEvent> processBuffer() {
    List<AgentEvent> events = new ArrayList<>();
    boolean progress;
    do {
      progress = false;
      switch (state) {
        case TEXT -> {
          if (processTextState(events)) progress = true;
        }
        case IN_HTML -> {
          if (processInHtmlState(events)) progress = true;
        }
        case DONE_HTML -> {
          if (processTextState(events)) progress = true;
        }
        case IN_QUESTIONS -> {
          if (processInQuestionsState(events)) progress = true;
        }
      }
    } while (progress);
    return events;
  }

  private boolean processTextState(List<AgentEvent> events) {
    // Consume leading newline that is the tail of a step-marker line arriving in the next token.
    if (pendingNewlineConsume) {
      if (pending.length() == 0) {
        // The \n hasn't arrived yet — keep the flag and wait for the next token.
        return false;
      }
      pendingNewlineConsume = false;
      if (pending.charAt(0) == '\n') {
        pending.delete(0, 1);
        return true; // re-enter loop to process the rest
      }
    }
    String buf = pending.toString();
    // In DONE_HTML state the first html block is already captured; subsequent ```html fences must
    // flow out as plain text (existing spec). Skip html fence detection.
    int htmlIdx = (state == State.DONE_HTML) ? -1 : buf.indexOf(HTML_FENCE);
    int questionsIdx = questionsFound ? -1 : buf.indexOf(QUESTIONS_FENCE);
    int stepIdx = findLineStartStepMarker(buf);
    int earliest = earliestPositive(htmlIdx, questionsIdx, stepIdx);

    if (earliest < 0) {
      int safeLen = pending.length() - TEXT_KEEP;
      if (safeLen > 0) {
        String safe = pending.substring(0, safeLen);
        answerBuilder.append(safe);
        events.add(new TokenEvent(safe));
        pending.delete(0, safeLen);
        return true;
      }
      return false;
    }

    if (earliest == stepIdx) {
      return processStepMarker(buf, stepIdx, events);
    } else if (earliest == htmlIdx) {
      return processHtmlFenceOpen(buf, htmlIdx, events);
    } else {
      return processQuestionsFenceOpen(buf, questionsIdx, events);
    }
  }

  private boolean processStepMarker(String buf, int stepIdx, List<AgentEvent> events) {
    int lineStart = findLineStart(buf, stepIdx);
    int closeIdx = buf.indexOf(STEP_CLOSE, stepIdx + STEP_OPEN.length());

    if (closeIdx >= 0) {
      // Emit text before the line containing the marker
      if (lineStart > 0) {
        String before = buf.substring(0, lineStart);
        answerBuilder.append(before);
        events.add(new TokenEvent(before));
      }
      String title = buf.substring(stepIdx + STEP_OPEN.length(), closeIdx).trim();
      int afterClose = closeIdx + STEP_CLOSE.length();
      boolean newlineConsumed = afterClose < buf.length() && buf.charAt(afterClose) == '\n';
      if (newlineConsumed) {
        afterClose++;
      } else {
        // The \n that ends the marker line will arrive in the next token; flag it for consumption.
        pendingNewlineConsume = true;
      }
      pending.delete(0, afterClose);

      // Finalize previous dynamic step
      if (lastDynamicStepKey != null) {
        events.add(
            new StepEvent(lastDynamicStepKey, lastDynamicStepTitle, null, StepStatus.SUCCESS));
      }
      stepCounter++;
      lastDynamicStepKey = "d" + stepCounter;
      lastDynamicStepTitle = title;
      events.add(new StepEvent(lastDynamicStepKey, title, null, StepStatus.RUNNING));
      return true;
    } else {
      // Close not yet seen — emit text before the marker line, hold the rest
      if (lineStart > 0) {
        String before = buf.substring(0, lineStart);
        answerBuilder.append(before);
        events.add(new TokenEvent(before));
      }
      pending.delete(0, lineStart);
      return false;
    }
  }

  private boolean processHtmlFenceOpen(String buf, int htmlIdx, List<AgentEvent> events) {
    int afterMarker = htmlIdx + HTML_FENCE.length();
    int newlineIdx = buf.indexOf('\n', afterMarker);
    if (newlineIdx >= 0) {
      if (htmlIdx > 0) {
        String before = buf.substring(0, htmlIdx);
        answerBuilder.append(before);
        events.add(new TokenEvent(before));
      }
      pending.delete(0, newlineIdx + 1);
      state = State.IN_HTML;
      return true;
    } else {
      if (htmlIdx > 0) {
        String before = buf.substring(0, htmlIdx);
        answerBuilder.append(before);
        events.add(new TokenEvent(before));
      }
      pending.delete(0, htmlIdx);
      return false;
    }
  }

  private boolean processQuestionsFenceOpen(String buf, int questionsIdx, List<AgentEvent> events) {
    int afterMarker = questionsIdx + QUESTIONS_FENCE.length();
    int newlineIdx = buf.indexOf('\n', afterMarker);
    if (newlineIdx >= 0) {
      if (questionsIdx > 0) {
        String before = buf.substring(0, questionsIdx);
        answerBuilder.append(before);
        events.add(new TokenEvent(before));
      }
      pending.delete(0, newlineIdx + 1);
      prevStateForQuestions = state; // remember TEXT or DONE_HTML so we return correctly
      questionsFound = true;
      state = State.IN_QUESTIONS;
      return true;
    } else {
      if (questionsIdx > 0) {
        String before = buf.substring(0, questionsIdx);
        answerBuilder.append(before);
        events.add(new TokenEvent(before));
      }
      pending.delete(0, questionsIdx);
      return false;
    }
  }

  private boolean processInHtmlState(List<AgentEvent> events) {
    String buf = pending.toString();
    int closeIdx = buf.indexOf(FENCE_CLOSE);
    if (closeIdx >= 0) {
      if (closeIdx > 0) {
        String chunk = buf.substring(0, closeIdx);
        htmlBuilder.append(chunk);
        events.add(new CodeEvent(chunk));
      }
      pending.delete(0, closeIdx + FENCE_CLOSE.length());
      state = State.DONE_HTML;
      return true;
    } else {
      int safeLen = pending.length() - FENCE_CLOSE_KEEP;
      if (safeLen > 0) {
        String chunk = pending.substring(0, safeLen);
        htmlBuilder.append(chunk);
        events.add(new CodeEvent(chunk));
        pending.delete(0, safeLen);
        return true;
      }
      return false;
    }
  }

  private boolean processInQuestionsState(List<AgentEvent> events) {
    String buf = pending.toString();
    int closeIdx = buf.indexOf(FENCE_CLOSE);
    if (closeIdx >= 0) {
      if (closeIdx > 0) {
        questionsBuilder.append(buf, 0, closeIdx);
      }
      pending.delete(0, closeIdx + FENCE_CLOSE.length());
      // Consume the trailing newline after the close fence so it doesn't bleed into answer text.
      // If the \n hasn't arrived yet (cross-token boundary), flag it for deferred consumption just
      // like the step-marker handling does with pendingNewlineConsume.
      if (pending.length() > 0 && pending.charAt(0) == '\n') {
        pending.delete(0, 1);
      } else {
        pendingNewlineConsume = true;
      }
      parseQuestionsJson(questionsBuilder.toString().trim(), events);
      state = prevStateForQuestions;
      return true;
    } else {
      int safeLen = pending.length() - FENCE_CLOSE_KEEP;
      if (safeLen > 0) {
        questionsBuilder.append(pending, 0, safeLen);
        pending.delete(0, safeLen);
        return true;
      }
      return false;
    }
  }

  private void parseQuestionsJson(String json, List<AgentEvent> events) {
    try {
      questions = OBJECT_MAPPER.readValue(json, QUESTION_LIST_TYPE);
    } catch (Exception exception) {
      log.debug("Failed to parse ```questions block as JSON: {}", exception.getMessage());
      String raw = QUESTIONS_FENCE + "\n" + json + "\n" + FENCE_CLOSE;
      answerBuilder.append(raw);
      events.add(new TokenEvent(raw));
      questions = null;
    }
  }

  /**
   * Flushes any remaining content in {@code pending} when the upstream terminates.
   *
   * <ul>
   *   <li>TEXT / DONE_HTML: remaining bytes are emitted as a final {@link TokenEvent}.
   *   <li>IN_HTML: remaining bytes are appended to the HTML buffer (unclosed-fence tolerance).
   *   <li>IN_QUESTIONS: attempt to parse whatever was collected (unclosed-fence tolerance).
   * </ul>
   */
  private List<AgentEvent> flushBuffer() {
    List<AgentEvent> events = new ArrayList<>();
    if (pending.length() == 0) {
      return events;
    }
    switch (state) {
      case TEXT, DONE_HTML -> {
        String text = pending.toString();
        answerBuilder.append(text);
        events.add(new TokenEvent(text));
        pending.setLength(0);
      }
      case IN_HTML -> {
        String chunk = pending.toString();
        htmlBuilder.append(chunk);
        events.add(new CodeEvent(chunk));
        pending.setLength(0);
      }
      case IN_QUESTIONS -> {
        questionsBuilder.append(pending);
        pending.setLength(0);
        parseQuestionsJson(questionsBuilder.toString().trim(), events);
      }
    }
    return events;
  }

  private List<AgentEvent> finalizeLastDynamicStep() {
    if (lastDynamicStepKey == null) return List.of();
    return List.of(
        new StepEvent(lastDynamicStepKey, lastDynamicStepTitle, null, StepStatus.SUCCESS));
  }

  private int findLineStartStepMarker(String buf) {
    int searchFrom = 0;
    while (searchFrom < buf.length()) {
      int found = buf.indexOf(STEP_OPEN, searchFrom);
      if (found < 0) return -1;
      if (isAtLineStart(buf, found)) return found;
      searchFrom = found + 1;
    }
    return -1;
  }

  private boolean isAtLineStart(String buf, int idx) {
    for (int index = idx - 1; index >= 0; index--) {
      char ch = buf.charAt(index);
      if (ch == '\n') return true;
      if (ch != ' ' && ch != '\t') return false;
    }
    // Everything in buf before idx is line-leading whitespace (or idx == 0). Consult the
    // already-emitted answer tail, skipping spaces/tabs that may have been emitted before the
    // partial marker was retained in the scanning window.
    for (int index = answerBuilder.length() - 1; index >= 0; index--) {
      char ch = answerBuilder.charAt(index);
      if (ch == '\n') return true;
      if (ch != ' ' && ch != '\t') return false;
    }
    return true; // stream start
  }

  private static int findLineStart(String buf, int markerIdx) {
    for (int index = markerIdx - 1; index >= 0; index--) {
      if (buf.charAt(index) == '\n') return index + 1;
    }
    return 0;
  }

  private static int earliestPositive(int... indices) {
    int min = -1;
    for (int index : indices) {
      if (index >= 0 && (min < 0 || index < min)) min = index;
    }
    return min;
  }
}
