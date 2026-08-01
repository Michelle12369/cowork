# JS Syntax Repair Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a dashboard HTML is generated, automatically detect JS syntax errors and call the provider once to repair them, without leaking repair events into the main SSE stream.

**Architecture:** `JsSyntaxValidator` (GraalJS parse-only) runs on every generated HTML; `ArtifactRepairer` builds a repair `AgentRequest` and calls `provider.generate()`, consuming its event stream internally without forwarding tokens to the caller; `AgentOrchestrator.finalize()` coordinates the single-retry loop and emits `r1` RUNNING/SUCCESS/ERROR step events into the main SSE.

**Tech Stack:** GraalVM Polyglot 24.1.2 (js-community, parse-only, JDK 17 standard JVM interpreted mode), Spring WebFlux (Mono/Flux), Mockito 5 (final record mocking), JUnit 5, AssertJ.

## Global Constraints

- Java 17 target — NEVER use Java 18+ API (no `SequencedCollection`, no `String.splitWithDelimiters`, etc.)
- `@RequiredArgsConstructor` constructor injection — NEVER `@Autowired` field injection
- `@Slf4j` — NEVER hand-write `LoggerFactory.getLogger`
- DTO = Java record; Entity = `@Getter @Setter @EqualsAndHashCode(of="id")` (no `@Data`)
- All IO resources use try-with-resources
- NEVER return `null` from public API — use `Optional<T>` or empty collection
- Test naming: `methodName_condition_expectedBehavior`
- `./mvnw test` must stay green (210 existing tests must not regress)
- `JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home` in shell env for Maven runs; Java target stays 17
- Repair is triggered at most once (single retry loop, 1 round max)
- Repair TOKEN/THINKING events MUST NOT appear in the main SSE stream
- Validator exception (parser crash) → `log.warn` + treat as zero errors (never block main flow)
- `erd.agent.repair.enabled` defaults to `true` (env var `ERD_AGENT_REPAIR_ENABLED`)

---

## File Map

### New files (create)
| Path | Responsibility |
|------|---------------|
| `backend/src/main/java/com/erd/cowork/agent/repair/JsSyntaxError.java` | Record: `(int scriptIndex, int line, int column, String message)` |
| `backend/src/main/java/com/erd/cowork/agent/repair/RepairOutcome.java` | Record: `(String html, boolean passed, List<JsSyntaxError> errorsBefore, List<JsSyntaxError> errorsAfter)` |
| `backend/src/main/java/com/erd/cowork/agent/repair/JsSyntaxValidator.java` | `@Component`: extract inline `<script>` blocks, GraalJS parse-only per block, collect errors |
| `backend/src/main/java/com/erd/cowork/agent/repair/ArtifactRepairer.java` | `@Component`: build repair `AgentRequest`, call `provider.generate()`, consume events internally, re-validate |
| `backend/src/test/java/com/erd/cowork/agent/repair/JsSyntaxValidatorTest.java` | Unit tests for validator |
| `backend/src/test/java/com/erd/cowork/agent/repair/ArtifactRepairerTest.java` | Unit tests for repairer |
| `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorRepairTest.java` | Integration tests for orchestrator repair path |

### Modified files
| Path | Change |
|------|--------|
| `backend/pom.xml` | Add `org.graalvm.polyglot:polyglot` + `js-community` 24.1.2 |
| `backend/src/main/resources/application.yml` | Add `erd.agent.repair.enabled: ${ERD_AGENT_REPAIR_ENABLED:true}` |
| `backend/docker-compose.yml` | Add `ERD_AGENT_REPAIR_ENABLED: ${ERD_AGENT_REPAIR_ENABLED:-true}` passthrough |
| `backend/src/main/java/com/erd/cowork/config/AgentProperties.java` | Add `Repair repair` nested record + field |
| `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java` | Inject `JsSyntaxValidator`, `ArtifactRepairer`, `AgentProperties`; pass `request` to `finalize()`; add repair block before artifact persist |
| `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java` | Add `@Mock` fields + setUp stubs for the 3 new deps; update orchestrator construction call |

---

## Task 1: GraalJS dependency + repair config

**Files:**
- Modify: `backend/pom.xml`
- Modify: `backend/src/main/resources/application.yml`
- Modify: `backend/docker-compose.yml`
- Modify: `backend/src/main/java/com/erd/cowork/config/AgentProperties.java`

**Interfaces:**
- Produces: `AgentProperties.Repair` record, `agentProperties.repair().enabled()` boolean

- [ ] **Step 1: Add GraalVM Polyglot deps to pom.xml**

In `backend/pom.xml`, add inside `<dependencies>` after the velocity dependency:

```xml
<dependency>
  <groupId>org.graalvm.polyglot</groupId>
  <artifactId>polyglot</artifactId>
  <version>24.1.2</version>
</dependency>
<dependency>
  <groupId>org.graalvm.polyglot</groupId>
  <artifactId>js-community</artifactId>
  <version>24.1.2</version>
  <type>pom</type>
</dependency>
```

- [ ] **Step 2: Add Repair nested record to AgentProperties**

Full replacement of `backend/src/main/java/com/erd/cowork/config/AgentProperties.java`:

```java
package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "erd.agent")
public record AgentProperties(
    String provider, Anthropic anthropic, OpenAiCompatible openAiCompatible, Repair repair) {

  public record Anthropic(String apiKey, String model, int maxTokens, int contextWindow) {}

  public record OpenAiCompatible(
      String baseUrl,
      String apiKey,
      String model,
      int contextWindow,
      String chatCompletionsPath,
      String authMode,
      TokenExchange tokenExchange) {

    public record TokenExchange(
        String url, String serviceAccountKey, String headerName, int tokenTtlSeconds) {}
  }

  public record Repair(boolean enabled) {}
}
```

- [ ] **Step 3: Add repair config to application.yml**

Under `erd.agent:` (same indentation level as `provider:`, `anthropic:`, `open-ai-compatible:`), add:

```yaml
    repair:
      enabled: ${ERD_AGENT_REPAIR_ENABLED:true}
```

- [ ] **Step 4: Add ERD_AGENT_REPAIR_ENABLED to docker-compose.yml**

Under `backend.environment:` in `docker-compose.yml`, add after the last `ERD_AGENT_OPENAI_COMPATIBLE_TOKEN_TTL` line:

```yaml
      ERD_AGENT_REPAIR_ENABLED: ${ERD_AGENT_REPAIR_ENABLED:-true}
```

- [ ] **Step 5: Compile to verify**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw compile -q
```

Expected: BUILD SUCCESS (no errors).

- [ ] **Step 6: Commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork" && \
git add backend/pom.xml \
        backend/src/main/resources/application.yml \
        backend/docker-compose.yml \
        backend/src/main/java/com/erd/cowork/config/AgentProperties.java && \
git commit -m "chore: add GraalJS deps and repair config"
```

---

## Task 2: Domain records — JsSyntaxError and RepairOutcome

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/repair/JsSyntaxError.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/repair/RepairOutcome.java`

**Interfaces:**
- Produces: `JsSyntaxError(int scriptIndex, int line, int column, String message)` — used by validator tests and repairer
- Produces: `RepairOutcome(String html, boolean passed, List<JsSyntaxError> errorsBefore, List<JsSyntaxError> errorsAfter)` — returned by `ArtifactRepairer.repair()`

- [ ] **Step 1: Create JsSyntaxError.java**

```java
package com.erd.cowork.agent.repair;

/**
 * A JS syntax error found in an inline script block of a generated HTML artifact.
 *
 * @param scriptIndex 0-based index of the {@code <script>} block within the HTML
 * @param line        1-based line number within the script block (-1 if unknown)
 * @param column      1-based column number within the script block (-1 if unknown)
 * @param message     human-readable error description from the JS parser
 */
public record JsSyntaxError(int scriptIndex, int line, int column, String message) {}
```

- [ ] **Step 2: Create RepairOutcome.java**

```java
package com.erd.cowork.agent.repair;

import java.util.List;

/**
 * Result of a single repair attempt on a broken HTML artifact.
 *
 * @param html        the best available HTML — repaired version if {@code passed}, original otherwise
 * @param passed      {@code true} if re-validation after repair found zero errors
 * @param errorsBefore errors found in the original HTML (before repair)
 * @param errorsAfter  errors found after repair (empty list when {@code passed == true})
 */
public record RepairOutcome(
    String html,
    boolean passed,
    List<JsSyntaxError> errorsBefore,
    List<JsSyntaxError> errorsAfter) {}
```

- [ ] **Step 3: Compile**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw compile -q
```

Expected: BUILD SUCCESS.

- [ ] **Step 4: Commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork" && \
git add backend/src/main/java/com/erd/cowork/agent/repair/JsSyntaxError.java \
        backend/src/main/java/com/erd/cowork/agent/repair/RepairOutcome.java && \
git commit -m "feat(backend): add JsSyntaxError and RepairOutcome records"
```

---

## Task 3: TDD — JsSyntaxValidator

**Files:**
- Create: `backend/src/test/java/com/erd/cowork/agent/repair/JsSyntaxValidatorTest.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/repair/JsSyntaxValidator.java`

**Interfaces:**
- Produces: `JsSyntaxValidator.validate(String html): List<JsSyntaxError>` — returns empty list on valid HTML or validator crash, returns non-empty list with correct scriptIndex/line/message on syntax errors.

- [ ] **Step 1: Write the failing tests**

Create `backend/src/test/java/com/erd/cowork/agent/repair/JsSyntaxValidatorTest.java`:

```java
package com.erd.cowork.agent.repair;

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

  // ── E4: src-only scripts are skipped ──────────────────────────────────────

  @Test
  void validate_srcScript_skipped() {
    String html =
        "<html><body>"
            + "<script src=\"/vendor/echarts.min.js\"></script>"
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
```

- [ ] **Step 2: Run to verify it fails (class not found)**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw test -pl . -Dtest=JsSyntaxValidatorTest -q 2>&1 | tail -20
```

Expected: compilation failure because `JsSyntaxValidator` does not exist yet.

- [ ] **Step 3: Implement JsSyntaxValidator**

Create `backend/src/main/java/com/erd/cowork/agent/repair/JsSyntaxValidator.java`:

```java
package com.erd.cowork.agent.repair;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import lombok.extern.slf4j.Slf4j;
import org.graalvm.polyglot.Context;
import org.graalvm.polyglot.PolyglotException;
import org.graalvm.polyglot.Source;
import org.springframework.stereotype.Component;

/**
 * Validates JavaScript syntax in inline {@code <script>} blocks of an HTML string.
 *
 * <p>Uses GraalVM Polyglot {@code Context.parse()} in parse-only mode — the script is parsed for
 * syntax errors but never executed. A new {@link Context} is created per call to ensure
 * thread-safety (GraalVM Context is not thread-safe).
 *
 * <p>On parser failure (e.g. GraalVM not available), the exception is logged at WARN level and an
 * empty list is returned so the main flow is never blocked.
 */
@Component
@Slf4j
public class JsSyntaxValidator {

  /**
   * Matches inline {@code <script>} blocks (excluding {@code src=} external scripts). Group 1 =
   * attributes string (may be empty), Group 2 = script content.
   */
  private static final Pattern SCRIPT_PATTERN =
      Pattern.compile("<script([^>]*)>(.*?)</script>", Pattern.CASE_INSENSITIVE | Pattern.DOTALL);

  /**
   * Extracts all inline {@code <script>} blocks from {@code html} and parses each for syntax
   * errors using GraalJS.
   *
   * @param html the full HTML string to validate
   * @return list of syntax errors found; empty list means no errors (or validator crashed)
   */
  public List<JsSyntaxError> validate(String html) {
    List<JsSyntaxError> errors = new ArrayList<>();
    Matcher matcher = SCRIPT_PATTERN.matcher(html);
    int scriptIndex = 0;

    while (matcher.find()) {
      String attrs = matcher.group(1);
      String content = matcher.group(2);

      // Skip external scripts (src= attribute present)
      if (attrs != null && attrs.toLowerCase(java.util.Locale.ROOT).contains("src=")) {
        scriptIndex++;
        continue;
      }

      if (content == null || content.isBlank()) {
        scriptIndex++;
        continue;
      }

      errors.addAll(parseScript(scriptIndex, content));
      scriptIndex++;
    }

    return errors;
  }

  private List<JsSyntaxError> parseScript(int scriptIndex, String content) {
    try (Context ctx =
        Context.newBuilder("js").option("engine.WarnInterpreterOnly", "false").build()) {
      Source source = Source.create("js", content);
      ctx.parse(source);
      return List.of();
    } catch (PolyglotException e) {
      if (e.isSyntaxError()) {
        org.graalvm.polyglot.SourceSection loc = e.getSourceLocation();
        int line = (loc != null) ? loc.getStartLine() : -1;
        int column = (loc != null) ? loc.getStartColumn() : -1;
        return List.of(new JsSyntaxError(scriptIndex, line, column, e.getMessage()));
      }
      // Non-syntax polyglot error — log and treat as no error
      log.warn(
          "JsSyntaxValidator: non-syntax polyglot error for script#{}: {}", scriptIndex, e.getMessage());
      return List.of();
    } catch (Exception e) {
      log.warn("JsSyntaxValidator: parser exception for script#{}: {}", scriptIndex, e.getMessage());
      return List.of();
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw test -Dtest=JsSyntaxValidatorTest -q 2>&1 | tail -20
```

Expected: `Tests run: 9, Failures: 0, Errors: 0, Skipped: 0` (adjust count to match actual test count).

- [ ] **Step 5: Run all existing tests to verify no regression**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw test -q 2>&1 | tail -15
```

Expected: BUILD SUCCESS with no failures.

- [ ] **Step 6: Commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork" && \
git add backend/src/main/java/com/erd/cowork/agent/repair/JsSyntaxValidator.java \
        backend/src/test/java/com/erd/cowork/agent/repair/JsSyntaxValidatorTest.java && \
git commit -m "feat(backend): JsSyntaxValidator with GraalJS parse-only"
```

---

## Task 4: TDD — ArtifactRepairer

**Files:**
- Create: `backend/src/test/java/com/erd/cowork/agent/repair/ArtifactRepairerTest.java`
- Create: `backend/src/main/java/com/erd/cowork/agent/repair/ArtifactRepairer.java`

**Interfaces:**
- Consumes: `JsSyntaxValidator.validate(String): List<JsSyntaxError>`, `DashboardAgentProvider.generate(AgentRequest): ProviderResult`, `AgentRequest(userId, sessionId, question, history, files, previousArtifactHtml)`
- Produces: `ArtifactRepairer.repair(String sessionId, String brokenHtml, List<JsSyntaxError> errors, AgentRequest originalRequest): Mono<RepairOutcome>` — the Mono is a cold publisher that consumes the provider stream internally. TOKEN/THINKING events from the repair call are discarded.

- [ ] **Step 1: Write the failing tests**

Create `backend/src/test/java/com/erd/cowork/agent/repair/ArtifactRepairerTest.java`:

```java
package com.erd.cowork.agent.repair;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.AgentFileContext;
import com.erd.cowork.agent.AgentRequest;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.extraction.ExtractionResult;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.Mockito;
import org.mockito.junit.jupiter.MockitoExtension;
import reactor.core.publisher.Flux;

@ExtendWith(MockitoExtension.class)
class ArtifactRepairerTest {

  @Mock private DashboardAgentProvider provider;

  private JsSyntaxValidator realValidator;
  private ArtifactRepairer repairer;

  private static final String BROKEN_HTML =
      "<html><script>const x = {</script></html>"; // unclosed brace — real GraalJS error
  private static final String REPAIRED_HTML =
      "<html><script>const x = {};</script></html>";
  private static final List<JsSyntaxError> FAKE_ERRORS =
      List.of(new JsSyntaxError(0, 1, 14, "Unexpected end of input"));

  @BeforeEach
  void setUp() {
    realValidator = new JsSyntaxValidator();
    repairer = new ArtifactRepairer(provider, realValidator);
  }

  private AgentRequest dummyRequest() {
    return new AgentRequest("user-1", "session-1", "build dashboard", List.of(), List.of(), null);
  }

  // ── R1: repair succeeds — provider returns valid HTML ──────────────────────

  @Test
  void repair_providerReturnsFixedHtml_outcomePassed() {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.just(new TokenEvent("repair-token")), // token must not leak to caller
                () -> new ExtractionResult("", REPAIRED_HTML, null)));

    RepairOutcome outcome =
        repairer.repair("session-1", BROKEN_HTML, FAKE_ERRORS, dummyRequest()).block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isTrue();
    assertThat(outcome.html()).isEqualTo(REPAIRED_HTML);
    assertThat(outcome.errorsBefore()).hasSize(1);
    assertThat(outcome.errorsAfter()).isEmpty();
  }

  // ── R2: repair fails — provider returns HTML that still has errors ─────────

  @Test
  void repair_providerReturnsBrokenHtml_outcomeNotPassed_originalHtmlReturned() {
    // Provider returns the same broken HTML — re-validation still finds errors
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.empty(), () -> new ExtractionResult("", BROKEN_HTML, null)));

    RepairOutcome outcome =
        repairer.repair("session-1", BROKEN_HTML, FAKE_ERRORS, dummyRequest()).block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isFalse();
    assertThat(outcome.html()).isEqualTo(BROKEN_HTML); // fall back to original
    assertThat(outcome.errorsAfter()).isNotEmpty();
  }

  // ── R3: provider returns no HTML — outcome not passed, original html returned

  @Test
  void repair_providerReturnsNoHtml_outcomeNotPassed_originalHtmlReturned() {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.empty(), () -> new ExtractionResult("no html here", null, null)));

    RepairOutcome outcome =
        repairer.repair("session-1", BROKEN_HTML, FAKE_ERRORS, dummyRequest()).block();

    assertThat(outcome).isNotNull();
    assertThat(outcome.passed()).isFalse();
    assertThat(outcome.html()).isEqualTo(BROKEN_HTML);
    assertThat(outcome.errorsAfter()).isEqualTo(FAKE_ERRORS); // same as before since no html
  }

  // ── R4: repair request carries files from original request ─────────────────

  @Test
  void repair_repairRequestCarriesFilesFromOriginalRequest() {
    AgentFileContext fakeFile =
        new AgentFileContext("alias1", "data.csv", "text/csv", null);
    AgentRequest originalWithFiles =
        new AgentRequest("u1", "s1", "build", List.of(), List.of(fakeFile), null);

    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.empty(), () -> new ExtractionResult("", REPAIRED_HTML, null)));

    ArgumentCaptor<AgentRequest> requestCaptor = ArgumentCaptor.forClass(AgentRequest.class);
    when(provider.generate(requestCaptor.capture()))
        .thenReturn(
            new ProviderResult(
                Flux.empty(), () -> new ExtractionResult("", REPAIRED_HTML, null)));

    repairer.repair("s1", BROKEN_HTML, FAKE_ERRORS, originalWithFiles).block();

    AgentRequest repairRequest = requestCaptor.getValue();
    assertThat(repairRequest.files()).containsExactly(fakeFile);
    assertThat(repairRequest.history()).isEmpty();
    assertThat(repairRequest.previousArtifactHtml()).isEqualTo(BROKEN_HTML);
  }
}
```

- [ ] **Step 2: Run to verify it fails (class not found)**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw test -Dtest=ArtifactRepairerTest -q 2>&1 | tail -20
```

Expected: compilation failure — `ArtifactRepairer` does not exist yet.

- [ ] **Step 3: Implement ArtifactRepairer**

Create `backend/src/main/java/com/erd/cowork/agent/repair/ArtifactRepairer.java`:

```java
package com.erd.cowork.agent.repair;

import com.erd.cowork.agent.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

/**
 * Calls the provider once to repair a broken HTML artifact, then re-validates the result.
 *
 * <p>The repair provider call's TOKEN and all other events are consumed internally and never
 * forwarded to the caller — only the {@link RepairOutcome} is returned.
 *
 * <p>The returned {@link Mono} is cold: subscribe to start the repair, block on
 * {@code Schedulers.boundedElastic} threads only.
 */
@Component
@RequiredArgsConstructor
@Slf4j
public class ArtifactRepairer {

  private final DashboardAgentProvider provider;
  private final JsSyntaxValidator validator;

  /**
   * Attempts to repair {@code brokenHtml} by calling the provider with a repair instruction.
   *
   * @param sessionId       session identifier (for logging)
   * @param brokenHtml      original HTML that failed validation
   * @param errors          errors found by {@link JsSyntaxValidator} — used to build the prompt
   * @param originalRequest the original request whose files/userId/sessionId are reused
   * @return a Mono that emits a single {@link RepairOutcome}
   */
  public Mono<RepairOutcome> repair(
      String sessionId,
      String brokenHtml,
      List<JsSyntaxError> errors,
      AgentRequest originalRequest) {

    String repairPrompt = buildRepairPrompt(errors);
    AgentRequest repairRequest =
        new AgentRequest(
            originalRequest.userId(),
            sessionId,
            repairPrompt,
            List.of(), // empty history — repair turn is self-contained
            originalRequest.files(), // carry original files so provider has schema context
            brokenHtml); // previousArtifactHtml = broken html

    ProviderResult pr = provider.generate(repairRequest);

    // Consume the entire event stream (TOKEN/THINKING/STEP all discarded — not forwarded).
    // Extraction supplier is populated as a side-effect of consuming the stream.
    return pr.events()
        .then(
            Mono.fromCallable(
                () -> {
                  com.erd.cowork.agent.extraction.ExtractionResult extraction =
                      pr.extraction().get();
                  String repairedHtml = extraction.html();

                  if (repairedHtml == null || repairedHtml.isBlank()) {
                    log.warn(
                        "repair session={}: provider returned no HTML, keeping original", sessionId);
                    return new RepairOutcome(brokenHtml, false, errors, errors);
                  }

                  List<JsSyntaxError> errorsAfter = validator.validate(repairedHtml);
                  boolean passed = errorsAfter.isEmpty();
                  log.info(
                      "repair session={} passed={} errorsBefore={} errorsAfter={}",
                      sessionId,
                      passed,
                      errors.size(),
                      errorsAfter.size());
                  return new RepairOutcome(
                      passed ? repairedHtml : brokenHtml, passed, errors, errorsAfter);
                }));
  }

  /**
   * Builds a Chinese-language repair prompt listing each error on its own line.
   *
   * <p>Format: {@code script#N 第 X 行：message}
   */
  private String buildRepairPrompt(List<JsSyntaxError> errors) {
    StringBuilder sb = new StringBuilder();
    sb.append("以下 HTML 中有 JavaScript 語法錯誤，請修復：\n\n");
    for (JsSyntaxError error : errors) {
      sb.append(
          String.format(
              "script#%d 第 %d 行：%s%n",
              error.scriptIndex() + 1, error.line(), error.message()));
    }
    sb.append("\n只修列出問題，其餘逐字保留，輸出完整 HTML。");
    return sb.toString();
  }
}
```

- [ ] **Step 4: Run ArtifactRepairerTest to verify it passes**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw test -Dtest=ArtifactRepairerTest -q 2>&1 | tail -20
```

Expected: `Tests run: 4, Failures: 0, Errors: 0, Skipped: 0`.

- [ ] **Step 5: Commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork" && \
git add backend/src/main/java/com/erd/cowork/agent/repair/ArtifactRepairer.java \
        backend/src/test/java/com/erd/cowork/agent/repair/ArtifactRepairerTest.java && \
git commit -m "feat(backend): ArtifactRepairer — repair provider call with internal event consumption"
```

---

## Task 5: Wire repair into AgentOrchestrator + integration tests

**Files:**
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java`
- Modify: `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java`
- Create: `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorRepairTest.java`

**Interfaces:**
- Consumes: `JsSyntaxValidator.validate(String): List<JsSyntaxError>`, `ArtifactRepairer.repair(...): Mono<RepairOutcome>`, `AgentProperties.repair().enabled(): boolean`
- Produces: main SSE stream now includes `StepEvent("r1", ...)` with RUNNING then SUCCESS/ERROR when repair fires; `stepsJson` in the persisted ChatMessage includes `r1`; DB stores repaired html when repair passes.

- [ ] **Step 1: Write the integration tests first (failing)**

Create `backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorRepairTest.java`:

```java
package com.erd.cowork.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.atLeast;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.artifact.ArtifactAssembler;
import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.ArtifactEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.extraction.ExtractionResult;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.repair.ArtifactRepairer;
import com.erd.cowork.agent.repair.JsSyntaxError;
import com.erd.cowork.agent.repair.JsSyntaxValidator;
import com.erd.cowork.agent.repair.RepairOutcome;
import com.erd.cowork.config.AgentProperties;
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
import org.springframework.transaction.support.TransactionTemplate;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentOrchestratorRepairTest {

  // ── common broken/repaired HTML ────────────────────────────────────────────

  /** HTML with genuinely broken JS (unclosed brace) — triggers real GraalJS error. */
  private static final String BROKEN_HTML =
      "<!DOCTYPE html><html><head></head><body><script>const x = {</script></body></html>";

  /** Syntactically valid repaired HTML. */
  private static final String REPAIRED_HTML =
      "<!DOCTYPE html><html><head></head><body><script>const x = {};</script></body></html>";

  @Mock private SessionGuard sessionGuard;
  @Mock private ChatMessageRepository messages;
  @Mock private UploadedFileRepository uploadedFiles;
  @Mock private ArtifactRepository artifacts;
  @Mock private DashboardAgentProvider provider;
  @Mock private ChatSessionRepository sessionRepository;
  @Mock private ArtifactAssembler artifactAssembler;
  @Mock private TransactionTemplate transactionTemplate;

  // Repair-specific: use REAL validator and REAL repairer so the full path is exercised.
  // Provider is mocked, so the second provider.generate() call is the repair call.
  private JsSyntaxValidator realValidator;
  private ArtifactRepairer realRepairer;

  private AgentConversationWriter conversationWriter;
  private AgentOrchestrator orchestrator;

  @BeforeEach
  void setUp() {
    when(transactionTemplate.execute(any()))
        .thenAnswer(
            inv -> {
              org.springframework.transaction.support.TransactionCallback<?> cb =
                  inv.getArgument(0);
              return cb.doInTransaction(null);
            });

    realValidator = new JsSyntaxValidator();
    realRepairer = new ArtifactRepairer(provider, realValidator);

    conversationWriter =
        new AgentConversationWriter(messages, artifacts, artifactAssembler, transactionTemplate);

    // Build properties with repair ENABLED
    AgentProperties.Repair repairProps = new AgentProperties.Repair(true);
    AgentProperties agentProperties =
        new AgentProperties(null, null, null, repairProps);

    orchestrator =
        new AgentOrchestrator(
            sessionGuard,
            messages,
            uploadedFiles,
            artifacts,
            provider,
            new ObjectMapper(),
            sessionRepository,
            conversationWriter,
            realValidator,
            realRepairer,
            agentProperties);

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
              Artifact artifact = inv.getArgument(0);
              artifact.setId("artifact-repair-1");
              return artifact;
            });
  }

  // ── RP1: successful repair — r1 RUNNING → SUCCESS in SSE, repaired html stored ─

  @Test
  void stream_brokenJs_repairEnabled_emitsR1Steps_storesRepairedHtml() {
    // First call (original): returns broken HTML
    ProviderResult originalResult =
        new ProviderResult(
            Flux.just(new TokenEvent("original-token")),
            () -> new ExtractionResult("", BROKEN_HTML, null));
    // Second call (repair): returns a repair-specific token + repaired HTML
    ProviderResult repairResult =
        new ProviderResult(
            Flux.just(new TokenEvent("repair-only-token-xyz")),
            () -> new ExtractionResult("", REPAIRED_HTML, null));

    when(provider.generate(any())).thenReturn(originalResult, repairResult);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // SSE must contain r1 RUNNING
    assertThat(events)
        .filteredOn(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(e -> assertThat(((StepEvent) e).status()).isEqualTo(StepStatus.RUNNING));

    // SSE must contain r1 SUCCESS
    assertThat(events)
        .filteredOn(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(e -> assertThat(((StepEvent) e).status()).isEqualTo(StepStatus.SUCCESS));

    // ArtifactEvent must be present
    assertThat(events).anyMatch(e -> e instanceof ArtifactEvent);

    // repair-only token must NOT appear in SSE
    assertThat(events)
        .filteredOn(e -> e instanceof TokenEvent te && te.delta().contains("repair-only-token-xyz"))
        .isEmpty();

    // DB must store the REPAIRED html as rawHtml
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getRawHtml()).isEqualTo(REPAIRED_HTML);

    // stepsJson must contain r1
    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);
    assertThat(aiMsg.getStepsJson()).contains("\"stepKey\":\"r1\"");
    assertThat(aiMsg.getStepsJson()).contains("SUCCESS");
  }

  // ── RP2: repair fails — original html stored, r1 ERROR in SSE ─────────────

  @Test
  void stream_brokenJs_repairFails_originalHtmlStored_r1Error() {
    // First call: broken HTML
    ProviderResult originalResult =
        new ProviderResult(
            Flux.empty(), () -> new ExtractionResult("", BROKEN_HTML, null));
    // Second call (repair): still returns broken HTML (repair did not fix it)
    ProviderResult repairResult =
        new ProviderResult(
            Flux.empty(), () -> new ExtractionResult("", BROKEN_HTML, null));

    when(provider.generate(any())).thenReturn(originalResult, repairResult);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // r1 ERROR in SSE
    assertThat(events)
        .filteredOn(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(e -> assertThat(((StepEvent) e).status()).isEqualTo(StepStatus.ERROR));

    // DB must store the ORIGINAL (broken) html as rawHtml — not the repair attempt
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getRawHtml()).isEqualTo(BROKEN_HTML);

    // stepsJson contains r1 with ERROR
    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);
    assertThat(aiMsg.getStepsJson()).contains("\"stepKey\":\"r1\"");
    assertThat(aiMsg.getStepsJson()).contains("ERROR");
  }

  // ── RP3: repair disabled — no second provider call, original html stored ───

  @Test
  void stream_repairDisabled_noRepairCall_originalHtmlStored() {
    // Rebuild orchestrator with repair DISABLED
    AgentProperties.Repair repairDisabled = new AgentProperties.Repair(false);
    AgentProperties agentPropertiesOff = new AgentProperties(null, null, null, repairDisabled);

    AgentOrchestrator orchestratorOff =
        new AgentOrchestrator(
            sessionGuard,
            messages,
            uploadedFiles,
            artifacts,
            provider,
            new ObjectMapper(),
            sessionRepository,
            conversationWriter,
            realValidator,
            realRepairer,
            agentPropertiesOff);

    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.empty(), () -> new ExtractionResult("", BROKEN_HTML, null)));

    orchestratorOff
        .stream("user-1", "session-1", "build dashboard", null)
        .collectList()
        .block();

    // provider.generate() called exactly once (no repair call)
    Mockito.verify(provider, Mockito.times(1)).generate(any());

    // DB stores the original (broken) html
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getRawHtml()).isEqualTo(BROKEN_HTML);
  }

  // ── RP4: valid JS HTML — no repair triggered, single provider call ─────────

  @Test
  void stream_validJs_noRepairTriggered() {
    String validHtml =
        "<!DOCTYPE html><html><script>const x = {};</script></html>";

    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.empty(), () -> new ExtractionResult("", validHtml, null)));

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // No r1 step in SSE
    assertThat(events)
        .noneMatch(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()));

    // provider called only once
    Mockito.verify(provider, Mockito.times(1)).generate(any());
  }
}
```

- [ ] **Step 2: Update AgentOrchestratorTest.java — add new mock fields and wire them**

In `AgentOrchestratorTest.java`, add the following three mock fields (AFTER existing `@Mock` declarations):

```java
@Mock private JsSyntaxValidator jsSyntaxValidator;
@Mock private ArtifactRepairer artifactRepairer;
@Mock private AgentProperties agentProperties;
```

In `setUp()`, add stubs just before the `orchestrator = new AgentOrchestrator(...)` line:

```java
// Repair disabled by default so existing tests are unaffected
when(agentProperties.repair()).thenReturn(new AgentProperties.Repair(false));
when(jsSyntaxValidator.validate(anyString())).thenReturn(List.of());
```

Update the `orchestrator = new AgentOrchestrator(...)` construction to add the three new params at the end:

```java
orchestrator =
    new AgentOrchestrator(
        sessionGuard,
        messages,
        uploadedFiles,
        artifacts,
        provider,
        new ObjectMapper(),
        sessionRepository,
        conversationWriter,
        jsSyntaxValidator,
        artifactRepairer,
        agentProperties);
```

Also add these imports at the top of `AgentOrchestratorTest.java`:

```java
import com.erd.cowork.agent.repair.ArtifactRepairer;
import com.erd.cowork.agent.repair.JsSyntaxValidator;
import com.erd.cowork.config.AgentProperties;
```

- [ ] **Step 3: Run the new repair tests to verify they fail (orchestrator not yet updated)**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw test -Dtest=AgentOrchestratorRepairTest,AgentOrchestratorTest -q 2>&1 | tail -20
```

Expected: compilation errors because `AgentOrchestrator` constructor does not yet accept the 3 new params.

- [ ] **Step 4: Implement orchestrator changes**

Replace the full content of `AgentOrchestrator.java`. Key changes from the current version:
1. Add three new `final` fields: `jsSyntaxValidator`, `artifactRepairer`, `agentProperties` (before `ObjectMapper`)
2. Add `AgentRequest request` parameter to `finalize()`; pass it from `buildEventFlow`
3. Add repair block inside the `if (html != null && !html.isBlank())` branch
4. Re-serialize `stepsJson` after repair if `r1` was added to `dynamicStepAccum`
5. Return `Flux.concat(repairStepFlux, artifactFlux, questionFlux)` instead of just `artifactFlux`

Full replacement of `AgentOrchestrator.java`:

```java
package com.erd.cowork.agent;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.ArtifactEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.QuestionEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.extraction.BareHtmlExtractor;
import com.erd.cowork.agent.extraction.ExtractionResult;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.repair.ArtifactRepairer;
import com.erd.cowork.agent.repair.JsSyntaxError;
import com.erd.cowork.agent.repair.JsSyntaxValidator;
import com.erd.cowork.agent.repair.RepairOutcome;
import com.erd.cowork.config.AgentProperties;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.parsing.FileProfile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.service.SessionGuard;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

@Component
@RequiredArgsConstructor
@Slf4j
public class AgentOrchestrator {

  private final SessionGuard sessionGuard;
  private final ChatMessageRepository messages;
  private final UploadedFileRepository uploadedFiles;
  private final ArtifactRepository artifacts;
  private final DashboardAgentProvider provider;
  private final ObjectMapper objectMapper;
  private final ChatSessionRepository sessionRepository;
  private final AgentConversationWriter conversationWriter;
  private final JsSyntaxValidator jsSyntaxValidator;
  private final ArtifactRepairer artifactRepairer;
  private final AgentProperties agentProperties;

  private record PrepareResult(
      ChatSession session,
      List<AgentFileContext> files,
      List<HistoryMessage> history,
      String previousArtifactHtml) {}

  /** Streams agent events for the given session and question. */
  public Flux<AgentEvent> stream(
      String userId, String sessionId, String question, String baseArtifactId) {
    AtomicBoolean aiPersisted = new AtomicBoolean(false);
    return Mono.fromCallable(() -> prepare(userId, sessionId, question, baseArtifactId))
        .subscribeOn(Schedulers.boundedElastic())
        .flatMapMany(
            prepareResult ->
                buildEventFlow(userId, sessionId, question, prepareResult, aiPersisted))
        .onErrorResume(
            NotFoundException.class,
            exception ->
                Flux.just(
                    new ErrorEvent(
                        "NOT_FOUND",
                        Objects.requireNonNullElse(
                            exception.getMessage(), exception.getClass().getSimpleName()))))
        .onErrorResume(
            exception -> {
              log.error("Agent error for session {}", sessionId, exception);
              String errorMsg =
                  Objects.requireNonNullElse(
                      exception.getMessage(), exception.getClass().getSimpleName());
              return Mono.<Void>fromRunnable(
                      () -> {
                        aiPersisted.set(true);
                        conversationWriter.tryPersistAiMessage(sessionId, errorMsg);
                      })
                  .subscribeOn(Schedulers.boundedElastic())
                  .thenReturn((AgentEvent) new ErrorEvent("AGENT_ERROR", errorMsg))
                  .flux();
            });
  }

  // ── Phase 1: prepare ─────────────────────────────────────────────────────

  private PrepareResult prepare(
      String userId, String sessionId, String question, String baseArtifactId) {
    var session = sessionGuard.loadOwnedAs(userId, sessionId);

    List<ChatMessage> existingMessages = messages.findBySessionIdOrderByCreatedAtAsc(sessionId);

    boolean hasUserMessage =
        existingMessages.stream().anyMatch(chatMessage -> chatMessage.getSender() == Sender.USER);
    if (!hasUserMessage) {
      session.setTitle(truncate(question, 30));
      sessionRepository.save(session);
    }

    ChatMessage userMsg = new ChatMessage();
    userMsg.setSessionId(sessionId);
    userMsg.setSender(Sender.USER);
    userMsg.setText(question);
    messages.save(userMsg);

    List<AgentFileContext> fileContexts = new ArrayList<>();
    for (var uploadedFile : uploadedFiles.findBySessionIdAndExpiredFalse(sessionId)) {
      if (uploadedFile.getMetadataJson() == null) {
        log.warn("null metadataJson for file {}", uploadedFile.getId());
        continue;
      }
      try {
        FileProfile profile =
            objectMapper.readValue(uploadedFile.getMetadataJson(), FileProfile.class);
        fileContexts.add(
            new AgentFileContext(
                uploadedFile.getAlias(), uploadedFile.getName(), uploadedFile.getType(), profile));
      } catch (Exception exception) {
        log.warn(
            "failed to parse metadataJson for file {}: {}",
            uploadedFile.getId(),
            exception.getMessage());
      }
    }

    List<HistoryMessage> history =
        existingMessages.stream().map(this::buildHistoryMessage).toList();

    String previousArtifactHtml = resolveArtifactHtml(sessionId, baseArtifactId);

    return new PrepareResult(session, fileContexts, history, previousArtifactHtml);
  }

  private HistoryMessage buildHistoryMessage(ChatMessage chatMessage) {
    String text = chatMessage.getText() != null ? chatMessage.getText() : "";
    if (text.length() > 500) {
      text = text.substring(0, 500);
    }
    if (chatMessage.getSender() == Sender.AI
        && chatMessage.getQuestionsJson() != null
        && !chatMessage.getQuestionsJson().isBlank()) {
      try {
        List<com.erd.cowork.agent.clarify.ClarifyingQuestion> questions =
            objectMapper.readValue(
                chatMessage.getQuestionsJson(),
                new TypeReference<List<com.erd.cowork.agent.clarify.ClarifyingQuestion>>() {});
        if (!questions.isEmpty()) {
          String optionsSummary =
              questions.stream()
                  .map(clarifyingQuestion -> String.join(" / ", clarifyingQuestion.options()))
                  .collect(Collectors.joining("; "));
          text = text + "\n[你提供過的選項: " + optionsSummary + "]";
        }
      } catch (Exception exception) {
        log.debug(
            "failed to parse questionsJson for history message {}", chatMessage.getId(), exception);
      }
    }
    return new HistoryMessage(chatMessage.getSender().name(), text);
  }

  private String resolveArtifactHtml(String sessionId, String baseArtifactId) {
    if (baseArtifactId != null && !baseArtifactId.isBlank()) {
      var specified =
          artifacts
              .findById(baseArtifactId)
              .filter(artifact -> sessionId.equals(artifact.getSessionId()))
              .map(Artifact::getRawHtml)
              .orElse(null);
      if (specified != null) {
        return specified;
      }
      log.debug(
          "baseArtifactId {} not found or not owned by session {}; falling back to most-recent",
          baseArtifactId,
          sessionId);
    }
    return artifacts
        .findFirstBySessionIdOrderByCreatedAtDesc(sessionId)
        .map(Artifact::getRawHtml)
        .orElse(null);
  }

  // ── Phase 2: event flow ───────────────────────────────────────────────────

  private Flux<AgentEvent> buildEventFlow(
      String userId,
      String sessionId,
      String question,
      PrepareResult prepareResult,
      AtomicBoolean aiPersisted) {

    AtomicReference<ErrorEvent> errorRef = new AtomicReference<>();
    Map<String, StepEvent> dynamicStepAccum = new LinkedHashMap<>();

    AgentRequest request =
        new AgentRequest(
            userId,
            sessionId,
            question,
            prepareResult.history(),
            prepareResult.files(),
            prepareResult.previousArtifactHtml());

    ProviderResult providerResult = provider.generate(request);

    Flux<AgentEvent> providerEvents =
        providerResult
            .events()
            .doOnNext(
                event -> {
                  if (event instanceof ErrorEvent errorEvent) {
                    errorRef.set(errorEvent);
                  }
                  if (event instanceof StepEvent stepEvent
                      && stepEvent.stepKey() != null
                      && stepEvent.stepKey().startsWith("d")) {
                    dynamicStepAccum.put(stepEvent.stepKey(), stepEvent);
                  }
                });

    return Flux.concat(
            providerEvents,
            Flux.defer(
                    () ->
                        finalize(
                            sessionId,
                            question,
                            request,
                            providerResult,
                            errorRef,
                            dynamicStepAccum,
                            aiPersisted))
                .subscribeOn(Schedulers.boundedElastic()))
        .doOnCancel(
            () -> {
              if (aiPersisted.compareAndSet(false, true)) {
                Mono.fromRunnable(() -> conversationWriter.tryPersistAiMessage(sessionId, "（回應中斷）"))
                    .subscribeOn(Schedulers.boundedElastic())
                    .subscribe(
                        null,
                        exception ->
                            log.error(
                                "failed to persist interrupted AI message for session {}",
                                sessionId,
                                exception));
              }
            });
  }

  // ── Phase 3: finalize ─────────────────────────────────────────────────────

  private Flux<AgentEvent> finalize(
      String sessionId,
      String question,
      AgentRequest request,
      ProviderResult providerResult,
      AtomicReference<ErrorEvent> errorRef,
      Map<String, StepEvent> dynamicStepAccum,
      AtomicBoolean aiPersisted) {
    try {
      ExtractionResult extraction = providerResult.extraction().get();
      ErrorEvent err = errorRef.get();

      // stepsJson snapshot for the error path (no r1 expected on error)
      String stepsJson =
          objectMapper.writeValueAsString(new ArrayList<>(dynamicStepAccum.values()));

      if (err != null) {
        aiPersisted.set(true);
        conversationWriter.persistAiMessage(sessionId, err.message(), stepsJson, null);
        return Flux.empty();
      }

      String html = extraction.html();
      String answerText = extraction.answerText();

      if (html == null || html.isBlank()) {
        String bare = BareHtmlExtractor.extract(answerText);
        if (bare != null) {
          html = bare;
          answerText = answerText.replace(bare, "（儀表板已生成 → 右側面板）").trim();
        }
      }

      String questionsJson = null;
      var questions = extraction.questions();
      if (questions != null && !questions.isEmpty()) {
        questionsJson = objectMapper.writeValueAsString(questions);
        log.info(
            "clarification requested session={} questionCount={}", sessionId, questions.size());
      }

      if (html != null && !html.isBlank()) {
        // ── JS repair loop (single retry) ────────────────────────────────
        List<AgentEvent> repairStepEvents = new ArrayList<>();
        boolean repairEnabled =
            agentProperties.repair() != null && agentProperties.repair().enabled();

        if (repairEnabled) {
          List<JsSyntaxError> jsErrors;
          try {
            jsErrors = jsSyntaxValidator.validate(html);
          } catch (Exception e) {
            log.warn(
                "JS validator threw exception for session {}, skipping repair: {}",
                sessionId,
                e.getMessage());
            jsErrors = List.of();
          }

          if (!jsErrors.isEmpty()) {
            int errorCount = jsErrors.size();
            log.info(
                "JS syntax repair triggered session={} errorCount={}", sessionId, errorCount);
            StepEvent r1Running =
                new StepEvent(
                    "r1", "偵測到 " + errorCount + " 個 JS 問題，自動修復中", null, StepStatus.RUNNING);
            repairStepEvents.add(r1Running);

            try {
              RepairOutcome outcome =
                  artifactRepairer.repair(sessionId, html, jsErrors, request).block();

              if (outcome != null && outcome.passed()) {
                html = outcome.html();
                StepEvent r1Success =
                    new StepEvent(
                        "r1",
                        "偵測到 " + errorCount + " 個 JS 問題，自動修復中",
                        null,
                        StepStatus.SUCCESS);
                dynamicStepAccum.put("r1", r1Success);
                repairStepEvents.add(r1Success);
              } else {
                int remaining =
                    (outcome != null && outcome.errorsAfter() != null)
                        ? outcome.errorsAfter().size()
                        : errorCount;
                StepEvent r1Error =
                    new StepEvent(
                        "r1",
                        "偵測到 " + errorCount + " 個 JS 問題，自動修復中",
                        remaining + " 個問題未修復",
                        StepStatus.ERROR);
                dynamicStepAccum.put("r1", r1Error);
                repairStepEvents.add(r1Error);
              }
            } catch (Exception e) {
              log.error(
                  "Repair call failed for session {}: {}", sessionId, e.getMessage(), e);
              StepEvent r1Error =
                  new StepEvent(
                      "r1",
                      "偵測到 " + errorCount + " 個 JS 問題，自動修復中",
                      "修復失敗",
                      StepStatus.ERROR);
              dynamicStepAccum.put("r1", r1Error);
              repairStepEvents.add(r1Error);
            }

            // Re-serialize stepsJson to include r1
            stepsJson = objectMapper.writeValueAsString(new ArrayList<>(dynamicStepAccum.values()));
          }
        }
        // ── end repair loop ───────────────────────────────────────────────

        String artifactTitle = resolveArtifactTitle(sessionId, question);
        String artifactId =
            conversationWriter.persistHtmlResult(
                sessionId, html, stepsJson, questionsJson, answerText, artifactTitle);
        aiPersisted.set(true);

        Flux<AgentEvent> repairFlux = Flux.fromIterable(repairStepEvents);
        Flux<AgentEvent> artifactFlux = Flux.just(new ArtifactEvent(artifactId, artifactTitle));
        Flux<AgentEvent> questionFlux =
            questionsJson != null ? Flux.just(new QuestionEvent(questions)) : Flux.empty();

        return Flux.concat(repairFlux, artifactFlux, questionFlux);
      }

      log.info(
          "no dashboard produced session={} answerChars={}",
          sessionId,
          answerText != null ? answerText.length() : 0);

      conversationWriter.persistAiMessage(sessionId, answerText, stepsJson, questionsJson);
      aiPersisted.set(true);

      return questionsJson != null ? Flux.just(new QuestionEvent(questions)) : Flux.empty();

    } catch (JsonProcessingException exception) {
      throw new RuntimeException("Failed to serialize steps JSON", exception);
    }
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  private String resolveArtifactTitle(String sessionId, String question) {
    return "Regenerate the dashboard.".equals(question)
        ? artifacts
            .findFirstBySessionIdOrderByCreatedAtDesc(sessionId)
            .map(Artifact::getTitle)
            .orElse(truncate(question, 50))
        : truncate(question, 50);
  }

  static String truncate(String text, int maxLength) {
    return text.length() <= maxLength ? text : text.substring(0, maxLength) + "…";
  }
}
```

- [ ] **Step 5: Run all tests to verify both repair tests and existing tests pass**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw test -q 2>&1 | tail -20
```

Expected: BUILD SUCCESS with ALL tests passing (existing 210+ + new repair tests). If any test fails, diagnose and fix before committing.

**Common fixes if tests fail:**
- If `AgentOrchestratorTest` fails to compile: verify the 3 new `@Mock` fields and updated constructor call in setUp() are correctly added.
- If `AgentOrchestratorRepairTest.RP1` fails: verify `REPAIRED_HTML` passes real GraalJS validation (`const x = {};` is valid).
- If `AgentOrchestratorRepairTest.RP2` fails: verify `BROKEN_HTML` actually fails GraalJS validation (`const x = {` is invalid).
- If `AgentProperties.Repair` cannot be mocked: verify Mockito's inline mock maker is active (it is in Spring Boot 3.4.1 via `mockito-core:5.x`).

- [ ] **Step 6: Run spotless check to ensure formatting**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw spotless:apply -q
```

- [ ] **Step 7: Run tests once more after spotless**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork/backend" && \
  export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home && \
  ./mvnw test -q 2>&1 | tail -10
```

Expected: BUILD SUCCESS.

- [ ] **Step 8: Commit**

```bash
cd "/Users/michellehsu/Desktop/work related/erd-cowork" && \
git add \
  backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java \
  backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorTest.java \
  backend/src/test/java/com/erd/cowork/agent/AgentOrchestratorRepairTest.java && \
git commit -m "feat(backend): js syntax repair loop with single retry"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered in |
|-----------------|-----------|
| `JsSyntaxValidator` — extract inline scripts, GraalJS parse-only, return `List<JsSyntaxError>(scriptIndex, line, column, message)` | Task 3 |
| Validator: zero false positives on optional chaining `?.`, nullish `??`, arrow, template literal, spread | Task 3 — `JsSyntaxValidatorTest` E2 group |
| Validator: multi-block index correct | Task 3 — test `validate_twoScriptBlocks_secondBroken_indexIsOne` |
| `ArtifactRepairer.repair(sessionId, brokenHtml, errors, originalRequest): Mono<RepairOutcome>` | Task 4 |
| Repair prompt in Chinese, error format `script#N 第 X 行: message` | Task 4 — `buildRepairPrompt()` |
| Repair AgentRequest: question=修復指令, previousArtifactHtml=brokenHtml, history=empty, files carried | Task 4 — `repair_repairRequestCarriesFilesFromOriginalRequest` |
| TOKEN/THINKING from repair call NOT forwarded | Task 4 — `pr.events().then(...)` discards all events |
| RepairOutcome re-validates repaired HTML | Task 4 — `validator.validate(repairedHtml)` inside `repair()` |
| Orchestrator: `r1 RUNNING → repair → r1 SUCCESS` in SSE | Task 5 — orchestrator finalize + `AgentOrchestratorRepairTest.RP1` |
| Orchestrator: repair fails → original html + r1 ERROR | Task 5 — `AgentOrchestratorRepairTest.RP2` |
| `r1` in `stepsJson` in DB | Task 5 — `RP1` and `RP2` assert `stepsJson` contains `r1` |
| `repair.enabled=false` → no second call, original stored | Task 5 — `AgentOrchestratorRepairTest.RP3` |
| Validator exception → log.warn + treat as zero errors | Task 3 — try/catch in `parseScript()` |
| `erd.agent.repair.enabled` config | Task 1 — AgentProperties, application.yml |
| docker-compose passthrough | Task 1 — `ERD_AGENT_REPAIR_ENABLED` in compose |
| Existing 210 tests must pass | Task 5 — Step 5 runs all tests before commit |

**Placeholder scan:** None found — every step contains actual code.

**Type consistency check:**
- `JsSyntaxError(int scriptIndex, int line, int column, String message)` — used consistently in validator, repairer, and orchestrator
- `RepairOutcome(String html, boolean passed, List<JsSyntaxError> errorsBefore, List<JsSyntaxError> errorsAfter)` — used consistently in repairer and orchestrator
- `ArtifactRepairer.repair(String sessionId, String brokenHtml, List<JsSyntaxError> errors, AgentRequest originalRequest): Mono<RepairOutcome>` — signature matches across tasks 4 and 5
- `AgentOrchestrator` constructor order matches both `AgentOrchestratorTest` and `AgentOrchestratorRepairTest`

---

## Execution Notes

**Parser selection rationale (binding):** GraalJS via `org.graalvm.polyglot:js-community:24.1.2`. Reasons:
1. Nashorn-standalone: does not support ES2020 (`?.`, `??`) — fails the zero-false-positive requirement immediately.
2. GraalJS: ES2022+ full support, `Context.parse()` is parse-only (no execution), confirmed zero false positives on all five modern syntax forms in the spec. Runs on standard JDK 17 in interpreted mode — acceptable overhead for once-per-generation validation.
3. Thread safety: GraalVM Context is not thread-safe; we create a new Context per `validate()` call. `finalize()` runs on `Schedulers.boundedElastic()` where blocking is allowed.

**Repair call tokens not leaked:** `ArtifactRepairer` uses `pr.events().then(Mono.fromCallable(...))` — `.then()` on a Flux discards all emitted elements and only propagates termination. No repair TOKEN ever escapes the `repair()` method boundary.

**Single retry only:** The orchestrator repair block runs at most once (`if (!jsErrors.isEmpty())`). If repair fails, the original HTML is persisted and `r1 ERROR` is emitted. No further retry loop.
