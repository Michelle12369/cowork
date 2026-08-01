# Bare-HTML Fallback + Prompt Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two minimax-m3 failure modes: (1) bare HTML output without ```html fence produces no artifact and floods chat text; (2) weak-model prompts produce HTML with zero-height (invisible) charts and fence violations.

**Architecture:** Add a static bare-HTML-document extractor to `HtmlExtractingTransformer`; use it as fallback in `AgentOrchestrator.finalize` when fence extraction yielded no HTML — extracted document goes through the normal artifact path and is stripped from the persisted AI message. Harden `PromptBuilder` system prompt with chart-rendering rules and add a trailing fence reminder to the user prompt when files exist.

**Tech Stack:** Java 17 / Spring Boot 3, Reactor, JUnit 5 + Mockito, google-java-format (auto via hook).

## Global Constraints

- Branch: `feat/m4-artifact` (repo `/Users/michellehsu/Desktop/work related/erd-cowork`, backend under `backend/`)
- CLAUDE.md rules apply (constructor injection, no `@Autowired` fields, test naming `methodName_condition_expectedBehavior`, no `any`, etc.)
- Do NOT hand-format; the Claude hook runs google-java-format
- Existing system-prompt anchor sentences MUST NOT change (26 existing PromptBuilderTest assertions must keep passing)
- All 129 existing tests + new tests must pass via `cd backend && ./mvnw test`

---

### Task 1: `extractBareHtmlDocument` static helper

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/extraction/HtmlExtractingTransformer.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/extraction/HtmlExtractingTransformerTest.java` (append new nested/plain test methods)

**Interfaces:**
- Produces: `public static String extractBareHtmlDocument(String text)` — returns the complete HTML document substring, or `null` when no complete document present. Task 2 calls this from `AgentOrchestrator.finalize`.

- [ ] **Step 1: Write the failing tests** (append to `HtmlExtractingTransformerTest`)

```java
// ── extractBareHtmlDocument ────────────────────────────────────────────────

@Test
void extractBareHtmlDocument_doctypeDocument_returnsFullDocument() {
  String text =
      "Here is your dashboard:\n<!DOCTYPE html>\n<html><head></head><body>hi</body></html>\nDone.";
  String result = HtmlExtractingTransformer.extractBareHtmlDocument(text);
  assertThat(result)
      .isEqualTo("<!DOCTYPE html>\n<html><head></head><body>hi</body></html>");
}

@Test
void extractBareHtmlDocument_htmlTagOnlyNoDoctype_returnsFromHtmlTag() {
  String text = "prefix <html lang=\"en\"><body>x</body></html> suffix";
  String result = HtmlExtractingTransformer.extractBareHtmlDocument(text);
  assertThat(result).isEqualTo("<html lang=\"en\"><body>x</body></html>");
}

@Test
void extractBareHtmlDocument_mixedCaseMarkers_returnsDocument() {
  String text = "<!doctype HTML><HTML><BODY>y</BODY></HTML>";
  String result = HtmlExtractingTransformer.extractBareHtmlDocument(text);
  assertThat(result).isEqualTo("<!doctype HTML><HTML><BODY>y</BODY></HTML>");
}

@Test
void extractBareHtmlDocument_missingClosingHtml_returnsNull() {
  String text = "<!DOCTYPE html><html><body>truncated";
  assertThat(HtmlExtractingTransformer.extractBareHtmlDocument(text)).isNull();
}

@Test
void extractBareHtmlDocument_plainTextWithoutHtml_returnsNull() {
  assertThat(HtmlExtractingTransformer.extractBareHtmlDocument("just a chat answer")).isNull();
}

@Test
void extractBareHtmlDocument_nullOrBlank_returnsNull() {
  assertThat(HtmlExtractingTransformer.extractBareHtmlDocument(null)).isNull();
  assertThat(HtmlExtractingTransformer.extractBareHtmlDocument("   ")).isNull();
}

@Test
void extractBareHtmlDocument_explanationBeforeAndAfter_extractsOnlyDocument() {
  String text =
      "以下是儀表板\n<!DOCTYPE html>\n<html>\n<head><title>t</title></head>\n"
          + "<body><div id=\"c\"></div></body>\n</html>\n希望這有幫助！";
  String result = HtmlExtractingTransformer.extractBareHtmlDocument(text);
  assertThat(result).startsWith("<!DOCTYPE html>");
  assertThat(result).endsWith("</html>");
  assertThat(result).doesNotContain("希望這有幫助");
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && ./mvnw test -Dtest=HtmlExtractingTransformerTest`
Expected: COMPILATION ERROR — `extractBareHtmlDocument` not defined.

- [ ] **Step 3: Implement the helper** (add to `HtmlExtractingTransformer`, plus `import java.util.Locale;`)

```java
/**
 * Extracts a complete bare HTML document (no ```html fence) from free text. The document starts
 * at the first case-insensitive {@code <!doctype html} (or, failing that, {@code <html}) and ends
 * at the first subsequent {@code </html>} (inclusive). Returns {@code null} when no complete
 * document is present.
 */
public static String extractBareHtmlDocument(String text) {
  if (text == null || text.isBlank()) {
    return null;
  }
  String lower = text.toLowerCase(Locale.ROOT);
  int start = lower.indexOf("<!doctype html");
  if (start < 0) {
    start = lower.indexOf("<html");
  }
  if (start < 0) {
    return null;
  }
  int end = lower.indexOf("</html>", start);
  if (end < 0) {
    return null;
  }
  return text.substring(start, end + "</html>".length());
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && ./mvnw test -Dtest=HtmlExtractingTransformerTest`
Expected: PASS (13 existing + 7 new).

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/extraction/HtmlExtractingTransformer.java backend/src/test/java/com/erd/cowork/agent/extraction/HtmlExtractingTransformerTest.java
git commit -m "feat(backend): add bare HTML document extractor helper"
```

---

### Task 2: Orchestrator bare-HTML fallback

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java:159-256` (finalize)
- Test: Create `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java`

**Interfaces:**
- Consumes: `HtmlExtractingTransformer.extractBareHtmlDocument(String)` from Task 1.
- Produces: no new public API; behavior change only.

- [ ] **Step 1: Write the failing integration test**

Create `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java`:

```java
package com.erd.cowork.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.artifact.ArtifactAssembler;
import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.ArtifactEvent;
import com.erd.cowork.agent.extraction.ExtractionResult;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.service.SessionGuard;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.Mockito;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import reactor.core.publisher.Flux;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentOrchestratorTest {

  private static final String BARE_HTML =
      "<!DOCTYPE html>\n<html><head><title>SPC</title></head>"
          + "<body><div id=\"chart\" style=\"height:320px\"></div></body></html>";

  @Mock private SessionGuard sessionGuard;
  @Mock private ChatMessageRepository messages;
  @Mock private UploadedFileRepository uploadedFiles;
  @Mock private ArtifactRepository artifacts;
  @Mock private DashboardAgentProvider provider;
  @Mock private ChatSessionRepository sessionRepository;
  @Mock private ArtifactAssembler artifactAssembler;

  private AgentOrchestrator orchestrator;

  @BeforeEach
  void setUp() {
    orchestrator =
        new AgentOrchestrator(
            sessionGuard,
            messages,
            uploadedFiles,
            artifacts,
            provider,
            new ObjectMapper(),
            sessionRepository,
            artifactAssembler);

    ChatSession session = new ChatSession();
    when(sessionGuard.loadOwnedAs(anyString(), anyString())).thenReturn(session);
    when(messages.findBySessionIdOrderByCreatedAtAsc(anyString())).thenReturn(List.of());
    when(uploadedFiles.findBySessionIdAndExpiredFalse(anyString())).thenReturn(List.of());
    when(messages.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));
    when(sessionRepository.save(any(ChatSession.class))).thenAnswer(inv -> inv.getArgument(0));
    when(artifactAssembler.assemble(anyString(), anyString()))
        .thenAnswer(inv -> inv.getArgument(1));
    when(artifacts.findFirstBySessionIdOrderByCreatedAtDesc(anyString()))
        .thenReturn(Optional.empty());
    when(artifacts.save(any(Artifact.class)))
        .thenAnswer(
            inv -> {
              Artifact a = inv.getArgument(0);
              a.setId("artifact-1");
              return a;
            });
  }

  private void stubProvider(String answerText, String html) {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new ExtractionResult(answerText, html)));
  }

  @Test
  void stream_bareHtmlWithoutFence_createsArtifactAndStripsHtmlFromMessage() {
    stubProvider("Here is the dashboard.\n" + BARE_HTML + "\nEnjoy!", null);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard").collectList().block();

    assertThat(events).anyMatch(e -> e instanceof ArtifactEvent);

    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getHtml()).contains("<title>SPC</title>");

    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);
    assertThat(aiMsg.getText()).doesNotContain("<html");
    assertThat(aiMsg.getText()).contains("（儀表板已生成 → 右側面板）");
    assertThat(aiMsg.getArtifactId()).isEqualTo("artifact-1");
  }

  @Test
  void stream_fencedHtml_stillUsesFencePath() {
    stubProvider("explanation", BARE_HTML);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard").collectList().block();

    assertThat(events).anyMatch(e -> e instanceof ArtifactEvent);
  }

  @Test
  void stream_plainAnswerWithoutHtml_producesNoArtifact() {
    stubProvider("這是純聊天回答，沒有任何儀表板。", null);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "hello").collectList().block();

    assertThat(events).noneMatch(e -> e instanceof ArtifactEvent);
    Mockito.verify(artifacts, Mockito.never()).save(any());
  }
}
```

Note: if `ChatSession`/`Artifact`/`ChatMessage` setters differ (e.g. no public `setId`), adjust with reflection or builder as the domain classes dictate — check `com.erd.cowork.domain` first. `Artifact.setId` may not exist because of `@UuidGenerator`; if so, use `Mockito.spy` or stub `a.getId()` via a spy, or simply return a pre-built artifact: `Artifact saved = new Artifact(); ... ` and set id via `org.springframework.test.util.ReflectionTestUtils.setField(saved, "id", "artifact-1")`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && ./mvnw test -Dtest=AgentOrchestratorTest`
Expected: `stream_bareHtmlWithoutFence_createsArtifactAndStripsHtmlFromMessage` FAILS (no ArtifactEvent — falls into "no HTML" branch). The other two should pass already.

- [ ] **Step 3: Implement the fallback in `finalize`**

In `AgentOrchestrator.finalize`, after the `err != null` block, replace the direct use of `ex.html()`/`ex.answerText()` with local variables plus fallback. Add import `com.erd.cowork.agent.extraction.HtmlExtractingTransformer`.

Replace:

```java
      if (ex.html() != null && !ex.html().isBlank()) {
        // HTML produced — inject data, save artifact, emit s3/s4 SUCCESS + ArtifactEvent
        String injectedHtml = artifactAssembler.assemble(sessionId, ex.html());
```

with:

```java
      String html = ex.html();
      String answerText = ex.answerText();

      // Fallback: some models emit a full HTML document without a ```html fence. Detect a
      // complete document inside the answer text, promote it to the artifact path, and strip
      // it from the persisted message so reloads don't replay the raw HTML in chat.
      if (html == null || html.isBlank()) {
        String bare = HtmlExtractingTransformer.extractBareHtmlDocument(answerText);
        if (bare != null) {
          html = bare;
          answerText = answerText.replace(bare, "（儀表板已生成 → 右側面板）").trim();
        }
      }

      if (html != null && !html.isBlank()) {
        // HTML produced — inject data, save artifact, emit s3/s4 SUCCESS + ArtifactEvent
        String injectedHtml = artifactAssembler.assemble(sessionId, html);
```

And in the same branch replace `aiMsg.setText(ex.answerText());` with `aiMsg.setText(answerText);`.
In the final "No HTML" branch replace `aiMsg.setText(ex.answerText());` with `aiMsg.setText(answerText);`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && ./mvnw test -Dtest=AgentOrchestratorTest`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java
git commit -m "fix(backend): fall back to bare-HTML extraction when model omits fence"
```

---

### Task 3: PromptBuilder hardening

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/prompt/PromptBuilder.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/prompt/PromptBuilderTest.java` (append tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature change; `userPrompt` now appends a trailing reminder when `request.files()` non-empty.

- [ ] **Step 1: Write the failing tests** (append to `PromptBuilderTest`; reuse the test class's existing fixture helpers for building an `AgentRequest` with/without files — read the file first and follow its existing helper style)

```java
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
  // build request WITH at least one file (reuse existing fixture helper)
  String prompt = builder.userPrompt(requestWithFiles());
  assertThat(prompt.trim())
      .endsWith(
          "Remember: output the complete HTML document inside a single ```html fenced block.");
}

@Test
void userPrompt_withoutFiles_hasNoFenceReminder() {
  String prompt = builder.userPrompt(requestWithoutFiles());
  assertThat(prompt).doesNotContain("Remember: output the complete HTML document");
}
```

(`requestWithFiles()`/`requestWithoutFiles()` are placeholders for whatever helper pattern the existing test class already uses — match it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && ./mvnw test -Dtest=PromptBuilderTest`
Expected: 5 new tests FAIL; 26 existing PASS.

- [ ] **Step 3: Implement**

(a) In `SYSTEM_PROMPT`, after the `## Visual style` section (end of the text block), append a new section — do not modify any existing line:

```text
## Chart rendering rules
- Every chart container div MUST have an explicit inline height (e.g. style="height:320px").
  ECharts renders nothing inside a zero-height container.
- Access the injected data exactly like this (rows is an array of arrays, NOT objects):
  const { columns, rows } = window.__ERD_DATA__["file1"]; // rows: array of arrays
  const vtIdx = columns.indexOf("vt");
  const vtValues = rows.map(r => Number(r[vtIdx]));
- Initialize charts after DOMContentLoaded using echarts.init(document.getElementById(...)),
  and call chart.resize() on window resize.
```

(b) In `appendFiles`, when files are non-empty, append the trailing reminder after the file sections:

```java
private void appendFiles(StringBuilder sb, List<AgentFileContext> files) {
  if (files == null || files.isEmpty()) {
    sb.append("(no files uploaded)\n");
    return;
  }
  for (AgentFileContext file : files) {
    appendFileSection(sb, file);
  }
  sb.append(
      "Remember: output the complete HTML document inside a single ```html fenced block.\n");
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && ./mvnw test -Dtest=PromptBuilderTest`
Expected: PASS (31 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/main/java/com/erd/cowork/agent/prompt/PromptBuilder.java backend/src/test/java/com/erd/cowork/agent/prompt/PromptBuilderTest.java
git commit -m "fix(backend): harden prompt with chart-height, data-shape and fence rules"
```

---

### Task 4: Full verification + live smoke + push

- [ ] **Step 1: Full test suite**

Run: `cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && ./mvnw test`
Expected: all green (129 existing + ~15 new).

- [ ] **Step 2: Rebuild backend**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork" && DOCKER_CONFIG=$(mktemp -d) docker compose up -d --build backend
curl -s localhost:8080/actuator/health   # expect {"status":"UP"}
```

- [ ] **Step 3: Live verification (minimax-m3, run twice)**

1. `POST /api/sessions` with `X-User-Id: smoke` → sessionId
2. Upload small CSV (`lot,vt`, 30 rows) to the session
3. `POST` agent question "Build an SPC dashboard for vt with control chart and histogram" (SSE)
4. Assert: SSE stream contains ARTIFACT event (fenced or fallback path)
5. `GET` artifact HTML → assert contains `__ERD_DATA__` and `height:`
6. Record per run: fence present? artifact created? height check pass?

- [ ] **Step 4: Squash-or-keep commits, final commit + push**

```bash
git commit -m "fix(backend): bare-html fallback extraction, chart-rendering prompt hardening"  # if any remaining changes
git push origin feat/m4-artifact
```
