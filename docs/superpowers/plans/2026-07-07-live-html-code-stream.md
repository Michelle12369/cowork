# Live HTML Code Stream + Gray Stop Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While the agent generates a dashboard, stream the HTML being written as `CODE` SSE events and render it in a default-collapsed, auto-scrolling panel in the step area; also change the stop-generation button from red to gray.

**Architecture:** Backend adds a `CodeEvent(delta)` to the sealed `AgentEvent` family; `HtmlExtractingTransformer` emits a `CodeEvent` for every chunk it appends to `htmlBuilder` (TOKEN/answerText/extraction semantics unchanged). The SSE controller serializes any `AgentEvent`, and providers apply the transformer with no event-type filtering, so CODE flows end-to-end with no further backend wiring. `ArtifactRepairer` already consumes ALL provider events internally (`pr.events().then(...)`) so repair-loop CODE never leaks. The bare-html fallback path (no ```html fence) produces no CODE events — accepted, noted in code comment. `TracingProviderDecorator` does not exist in this codebase — nothing to do there. Frontend accumulates CODE deltas into `codeText` in `useAgentStream`, and a new shared `HtmlCodePanel` component renders both the live code stream (steps area) and the existing lazy-fetch history viewer (bubble tail) — one component, two data sources, never both at once in the same bubble.

**Tech Stack:** Java 17 / Spring Boot WebFlux (Reactor Flux), Jackson polymorphic JSON; React 18 + TypeScript + Tailwind + antd 6 + Vitest.

## Global Constraints

- google-java-format is applied by a Claude hook — do not hand-format.
- Frontend: `React.FC<Props>`, explicit return types, no `any`, `import type`, handlers in `useCallback`, Tailwind utilities, `useEffect` cleanup.
- Existing tests MUST NOT be weakened: TOKEN-layer assertions stay; where a test asserted "all events are TokenEvent" inside a fence scenario, it is *strengthened* to "all events are TokenEvent or CodeEvent" **plus** a new exact assertion that concatenated CODE deltas equal the fence content.
- Test naming (backend): `methodName_condition_expectedBehavior`.
- Verify: backend `./mvnw test` all green (243+); frontend `npm test` and `npm run build` all green (183+).
- JAVA_HOME for mvn: `export JAVA_HOME=~/Library/Java/JavaVirtualMachines/jdk-21.0.5+11/Contents/Home`

---

### Task 1: Backend — `CodeEvent` + transformer emission

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/agent/event/CodeEvent.java`
- Modify: `backend/src/main/java/com/erd/cowork/agent/event/AgentEvent.java`
- Modify: `backend/src/main/java/com/erd/cowork/agent/extraction/HtmlExtractingTransformer.java` (`processInHtmlState`, `flushBuffer` IN_HTML case, class javadoc)
- Modify: `backend/src/main/java/com/erd/cowork/agent/repair/ArtifactRepairer.java` (javadoc only)
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentOrchestrator.java` (comment only, bare-html fallback)
- Test: `backend/src/test/java/com/erd/cowork/agent/event/AgentEventJsonTest.java`
- Test: `backend/src/test/java/com/erd/cowork/agent/extraction/HtmlExtractingTransformerTest.java`

**Interfaces:**
- Produces: `public record CodeEvent(String delta) implements AgentEvent {}` serialized as `{"type":"CODE","delta":"..."}`. Every chunk appended to `htmlBuilder` is emitted synchronously as one `CodeEvent` carrying the identical chunk (pre-trim; `result().html()` stays trimmed).

- [ ] **Step 1: Write failing JSON tests** — add to `AgentEventJsonTest.java`:

```java
@Test
void codeEvent_serialize_containsTypeAndDelta() throws Exception {
  CodeEvent event = new CodeEvent("<div>x</div>");
  String json = mapper.writeValueAsString(event);
  assertThat(json).contains("\"type\":\"CODE\"");
  assertThat(json).contains("\"delta\":\"<div>x</div>\"");
}

@Test
void codeEvent_roundTrip_preservesValues() throws Exception {
  CodeEvent original = new CodeEvent("<html>");
  String json = mapper.writeValueAsString(original);
  AgentEvent deserialized = mapper.readValue(json, AgentEvent.class);
  assertThat(deserialized).isInstanceOf(CodeEvent.class);
  assertThat(((CodeEvent) deserialized).delta()).isEqualTo("<html>");
}
```

- [ ] **Step 2: Run — expect compile FAIL** (`CodeEvent` not defined):
`cd backend && ./mvnw test -Dtest=AgentEventJsonTest`

- [ ] **Step 3: Create `CodeEvent` and register subtype**

`CodeEvent.java`:
```java
package com.erd.cowork.agent.event;

/**
 * Streaming delta event for the HTML code being generated inside the first {@code ```html} fence.
 *
 * <p>Mirrors the exact chunks appended to the extraction HTML buffer so the frontend can render a
 * live "code being written" view. Never mixed into TOKEN/answer text.
 *
 * @param delta one HTML code chunk
 */
public record CodeEvent(String delta) implements AgentEvent {}
```

`AgentEvent.java` — add `@JsonSubTypes.Type(value = CodeEvent.class, name = "CODE")` to the list and `CodeEvent` to the `permits` clause.

- [ ] **Step 4: Run `./mvnw test -Dtest=AgentEventJsonTest` — expect PASS**

- [ ] **Step 5: Write failing transformer tests** — add to `HtmlExtractingTransformerTest.java` a helper and new tests:

```java
private static String codeDeltas(List<AgentEvent> events) {
  return events.stream()
      .filter(event -> event instanceof CodeEvent)
      .map(event -> ((CodeEvent) event).delta())
      .collect(joining());
}

@Test
void apply_completeFence_codeEventsCarryFenceContent() {
  var transformer = new HtmlExtractingTransformer();
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
  var transformer = new HtmlExtractingTransformer();
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
  var transformer = new HtmlExtractingTransformer();
  List<AgentEvent> events =
      transformer.apply(Flux.just("x\n```html\n<div>partial")).collectList().block();
  assertThat(events).isNotNull();
  assertThat(codeDeltas(events)).isEqualTo("<div>partial");
}

@Test
void apply_noFence_noCodeEvents() {
  var transformer = new HtmlExtractingTransformer();
  List<AgentEvent> events =
      transformer.apply(Flux.just("hello", " world")).collectList().block();
  assertThat(events).isNotNull();
  assertThat(events).noneMatch(event -> event instanceof CodeEvent);
}

@Test
void apply_secondHtmlFence_noCodeEventsForSecondBlock() {
  var transformer = new HtmlExtractingTransformer();
  String input = "t1\n```html\n<div>1</div>\n```\nt2\n```html\n<div>2</div>\n```\nt3";
  List<AgentEvent> events = transformer.apply(Flux.just(input)).collectList().block();
  assertThat(events).isNotNull();
  assertThat(codeDeltas(events)).isEqualTo("<div>1</div>\n");
}
```

Also import `com.erd.cowork.agent.event.CodeEvent`.

- [ ] **Step 6: Update existing all-TokenEvent assertions (strengthen, not weaken)** — in fence-scenario tests that assert `events).allSatisfy(event -> assertThat(event).isInstanceOf(TokenEvent.class))` (e.g. `singleToken_completeFence_htmlExtractedAndTextEmitted`), replace with:

```java
assertThat(events)
    .allSatisfy(
        event ->
            assertThat(event)
                .satisfiesAnyOf(
                    it -> assertThat(it).isInstanceOf(TokenEvent.class),
                    it -> assertThat(it).isInstanceOf(CodeEvent.class)));
assertThat(codeDeltas(events)).isEqualTo("<div>x</div>\n");
```

Only touch tests whose input contains a ```html fence; non-fence tests keep their pure-TokenEvent assertions (and per `apply_noFence_noCodeEvents` there must be no CODE there). Keep every `deltas(...)`, `result().html()`, `result().answerText()` assertion byte-identical.

- [ ] **Step 7: Run — expect new tests FAIL** (no CODE emitted yet):
`./mvnw test -Dtest=HtmlExtractingTransformerTest`

- [ ] **Step 8: Emit `CodeEvent` in the transformer** — in `HtmlExtractingTransformer.java`:

`processInHtmlState`:
```java
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
```

`flushBuffer` IN_HTML case:
```java
case IN_HTML -> {
  String chunk = pending.toString();
  htmlBuilder.append(chunk);
  events.add(new CodeEvent(chunk));
  pending.setLength(0);
}
```

Add import `com.erd.cowork.agent.event.CodeEvent`. Update the `apply(...)` javadoc: "Content inside the first ```html block is captured into the HTML buffer and simultaneously emitted as {@link CodeEvent} deltas (never as TOKEN)."

- [ ] **Step 9: Run full backend suite — expect ALL PASS (243+)**
`./mvnw test`
(Repair loop: `ArtifactRepairer` consumes the full event flux internally via `pr.events().then(...)` — CODE from the repair call is discarded like TOKEN/STEP; no code change needed. Verify `ArtifactRepairerTest` stays green.)

- [ ] **Step 10: Doc-only notes**
  - `ArtifactRepairer` class javadoc: extend "TOKEN and all other events" sentence to explicitly name CODE: "The repair provider call's TOKEN/CODE and all other events are consumed internally and never forwarded".
  - `AgentOrchestrator.finalize` bare-html fallback comment: append "(this path has no ```html fence, so no CODE events were streamed for it — accepted)".

- [ ] **Step 11: Commit**
```bash
git add backend
git commit -m "feat(backend): live html CODE event stream from fence extraction"
```

---

### Task 2: Frontend — CODE accumulation + collapsible live panel + gray stop button

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/hooks/useAgentStream.ts`
- Create: `frontend/src/components/chat/HtmlCodePanel.tsx`
- Modify: `frontend/src/components/chat/MessageBubble.tsx`
- Modify: `frontend/src/components/chat/MessageList.tsx`
- Modify: `frontend/src/components/chat/PromptSender.tsx`
- Test: `frontend/src/hooks/useAgentStream.test.tsx`, `frontend/src/components/chat/HtmlCodePanel.test.tsx` (new), `frontend/src/components/chat/MessageBubble.test.tsx`, `frontend/src/components/chat/PromptSender.test.tsx`

**Interfaces:**
- Consumes: SSE event `{ type: 'CODE', delta: string }` from Task 1.
- Produces: `AgentStreamState.codeText: string`; component `HtmlCodePanel: React.FC<{ label: string; code?: string | null; artifactId?: string | null; autoScroll?: boolean }>`.

**Viewer integration (the "one component, two data sources" rule):** `HtmlCodePanel` is the single collapsible `</>` row. Live bubble with non-empty `codeText` renders it in the step area (below "Working on it…") with `code={codeText}` + `autoScroll={streaming}`; label is `</> 產生中的 HTML` while streaming and `</> HTML` after. History bubbles (no `codeText`) render it at the bubble tail with `artifactId` — the panel lazy-fetches raw HTML on first expand exactly like today (label `</> 查看 HTML`). A bubble never renders both: tail fetch-viewer is gated by `!codeText`.

- [ ] **Step 1: types.ts** — add to the `AgentEvent` union and state:
```ts
  | { type: 'CODE'; delta: string }
```
and in `AgentStreamState`: `codeText: string;`

- [ ] **Step 2: Write failing hook tests** — add to `useAgentStream.test.tsx` (follow the file's existing sse-mock helpers):
```ts
it('accumulates CODE deltas into codeText', async () => { /* feed CODE events "<div>", "x</div>" → expect state.codeText === '<div>x</div>'; expect liveText unchanged */ });
it('reset() clears codeText back to empty', async () => { /* stream CODE, then act(() => reset()); expect codeText === '' */ });
it('a new send() (START) clears previous codeText', async () => { /* stream CODE, complete, send again with no CODE; expect codeText === '' */ });
```
Use the same mock-fetch SSE patterns as the neighboring THINKING tests (lines ~454-473) — copy their structure, substituting `{"type":"CODE","delta":"..."}`.

- [ ] **Step 3: Run `npm test -- useAgentStream` — expect FAIL**

- [ ] **Step 4: useAgentStream reducer** — `initialState` gets `codeText: ''`; add case:
```ts
case 'CODE':
  return { ...state, codeText: state.codeText + agentEvent.delta };
```
(START/RESET already rebuild from `initialState`, so both clear it.)

- [ ] **Step 5: Run `npm test -- useAgentStream` — expect PASS**

- [ ] **Step 6: Write failing HtmlCodePanel tests** — `HtmlCodePanel.test.tsx`:
```tsx
// - renders label; content NOT in the document by default (collapsed)
// - click expands: with code prop → shows code text in <pre>, no fetch called
// - autoScroll: when expanded and code grows, scrollTop set to scrollHeight (assert via ref jsdom: set scrollHeight defineProperty, rerender with longer code, expect scrollTop === scrollHeight)
// - with artifactId (no code): expand triggers fetchArtifactRawHtml(artifactId), shows fetched text; error → 無法載入 (mirror the four existing "HTML viewer:" tests in MessageBubble.test.tsx)
```

- [ ] **Step 7: Implement `HtmlCodePanel.tsx`** — extract from MessageBubble's current viewer:
```tsx
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { CodeOutlined, DownOutlined, UpOutlined } from '@ant-design/icons';
import { fetchArtifactRawHtml } from '@/api/artifactApi';

export interface Props {
  /** Row label, e.g. '</> 產生中的 HTML' | '</> HTML' | '</> 查看 HTML'. */
  label: string;
  /** Live data source: when non-empty, rendered directly (no fetch). */
  code?: string | null;
  /** Fetch data source: lazy-loaded on first expand when `code` is absent. */
  artifactId?: string | null;
  /** Auto-scroll the panel to the bottom as `code` grows (live typing). */
  autoScroll?: boolean;
}

const HtmlCodePanel: React.FC<Props> = ({ label, code, artifactId, autoScroll }) => {
  const [expanded, setExpanded] = useState(false); // default collapsed
  const contentRef = useRef<HTMLPreElement>(null);
  const [htmlFetch, setHtmlFetch] = useState<{
    artifactId: string;
    status: 'loading' | 'ok' | 'error';
    content?: string;
  } | null>(null);

  const toggle = useCallback(() => setExpanded((prev) => !prev), []);

  const hasLiveCode = !!code;

  // Lazy-fetch on first expand — only for the fetch data source.
  useEffect(() => {
    if (!expanded || hasLiveCode || !artifactId) return;
    if (htmlFetch?.artifactId === artifactId) return;
    setHtmlFetch({ artifactId, status: 'loading' });
    const controller = new AbortController();
    fetchArtifactRawHtml(artifactId, controller.signal)
      .then((text) => setHtmlFetch({ artifactId, status: 'ok', content: text }))
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === 'AbortError') return;
        setHtmlFetch({ artifactId, status: 'error' });
      });
    return () => controller.abort();
  }, [expanded, hasLiveCode, artifactId, htmlFetch?.artifactId]);

  // Auto-scroll to bottom as live code streams in.
  useEffect(() => {
    if (expanded && autoScroll && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [code, expanded, autoScroll]);

  const body = hasLiveCode ? (
    <pre
      ref={contentRef}
      className="mt-1 max-h-80 overflow-auto rounded bg-gray-200 p-2 text-[11px] text-gray-600"
    >
      <code>{code}</code>
    </pre>
  ) : htmlFetch?.artifactId !== artifactId || htmlFetch?.status === 'loading' ? (
    <div className="text-[11px] text-gray-400">載入中…</div>
  ) : htmlFetch?.status === 'error' ? (
    <div className="text-[11px] text-red-400">無法載入</div>
  ) : (
    <pre ref={contentRef} className="mt-1 max-h-80 overflow-auto rounded bg-gray-200 p-2 text-[11px]">
      <code>{htmlFetch?.content}</code>
    </pre>
  );

  return (
    <div className="mt-2">
      <button
        onClick={toggle}
        className="flex w-full items-center gap-1 text-left text-[11px] text-gray-400 hover:text-gray-600"
      >
        <CodeOutlined style={{ fontSize: 11 }} />
        <span className="flex-1">{label}</span>
        {expanded ? <UpOutlined style={{ fontSize: 10 }} /> : <DownOutlined style={{ fontSize: 10 }} />}
      </button>
      {expanded && <div className="mt-1">{body}</div>}
    </div>
  );
};

export default HtmlCodePanel;
```
Note: the visible label text keeps the `&lt;/&gt;` prefix inside the string passed by callers (`'</> 查看 HTML'` etc.) so existing MessageBubble tests matching `查看 HTML` stay green.

- [ ] **Step 8: Run `npm test -- HtmlCodePanel` — expect PASS**

- [ ] **Step 9: Write failing MessageBubble tests** — add:
```ts
// - streaming bubble with codeText: shows '產生中的 HTML' row in the steps area; content collapsed by default
// - expand shows the codeText content in <pre>
// - non-streaming live bubble with codeText: label is '</> HTML' (not 產生中)
// - bubble with codeText AND artifact: only ONE </> row rendered (no 查看 HTML fetch row)
// - history bubble (artifact, no codeText): 查看 HTML fetch viewer unchanged (existing 4 tests must stay green)
```

- [ ] **Step 10: MessageBubble integration**
  - Props: add `codeText?: string | null;`
  - Delete the inline viewer state/effect (`htmlViewExpanded`, `htmlFetch`, `toggleHtmlView`, the lazy-fetch `useEffect`, and the tail viewer JSX) — replaced by `HtmlCodePanel`.
  - In the steps area, immediately after the `{hasSteps && (...)}` block (i.e. below "Working on it…" / StepChain), render:
    ```tsx
    {codeText && (
      <HtmlCodePanel
        label={streaming ? '</> 產生中的 HTML' : '</> HTML'}
        code={codeText}
        autoScroll={!!streaming}
      />
    )}
    ```
  - At the old tail position: `{artifact && !codeText && <HtmlCodePanel label="</> 查看 HTML" artifactId={artifact.artifactId} />}`

- [ ] **Step 11: MessageList** — pass `codeText={live.codeText || null}` on the live `MessageBubble` (history bubbles get no codeText).

- [ ] **Step 12: Run `npm test -- MessageBubble` — expect PASS (old + new)**

- [ ] **Step 13: Gray stop button** — in `PromptSender.tsx` replace the stop `Button`:
```tsx
<Button
  type="primary"
  shape="default"
  className="!bg-gray-500 hover:!bg-gray-600"
  icon={<StopSquareIcon />}
  onClick={onStop}
  aria-label="Stop generation"
/>
```
(`danger` removed; antd-neutral gray via Tailwind, hover one step darker; white icon preserved by keeping `type="primary"`.) Update the Props JSDoc "red stop button" → "gray stop button". Add test:
```ts
test('stop button is gray, not red (no danger class)', () => {
  render(<PromptSender onSend={vi.fn()} isStreaming={true} onStop={vi.fn()} />);
  const btn = screen.getByLabelText('Stop generation');
  expect(btn.className).not.toContain('ant-btn-dangerous');
  expect(btn.className).toContain('!bg-gray-500');
});
```

- [ ] **Step 14: Full frontend verify** — `npm test` (183+ green) and `npm run build` (green).

- [ ] **Step 15: Commit**
```bash
git add frontend
git commit -m "feat(frontend): live html code panel in step area + gray stop button"
```

---

### Task 3: Integration verify + ship (coordinator-run)

- [ ] Backend `./mvnw test` full green; frontend `npm test` + `npm run build` green.
- [ ] `git push origin feat/company-gptoss`
- [ ] `DOCKER_CONFIG=$(mktemp -d) docker compose up -d --build backend frontend`
- [ ] `curl actuator/health` → UP; `curl -o /dev/null -w '%{http_code}' localhost:3000` → 200
- [ ] Live smoke: create session via API, POST a dashboard question to `/api/sessions/{id}/messages` with `X-User-Id`, capture SSE; assert at least one `"type":"CODE"` event and that no `"type":"TOKEN"` delta contains `<html`/```` ```html ````.

## Self-Review

- Spec coverage: CodeEvent + @JsonSubTypes "CODE" (T1 S1-4); transformer sync CodeEvent emission on both htmlBuilder append sites incl. flush (T1 S8); TOKEN/answer/extraction semantics untouched (T1 S6 keeps assertions byte-identical); repair CODE non-leak (existing `pr.events().then()` discard, javadoc T1 S10); bare-html-no-CODE note (T1 S10); TracingProviderDecorator — does not exist, documented in header; frontend union + codeText + START/RESET clear (T2 S1-5); default-collapsed panel, mono small text, gray bg, max-h-80(=320px), auto-scroll, title switch, live/history integration without duplicate rows (T2 S6-12); gray stop button + test (T2 S13); all verify/commit/smoke steps (T3).
- No placeholders; hook test bodies reference the concrete neighboring THINKING-test pattern by line.
- Types consistent: `codeText: string`, `CodeEvent(String delta)`, `HtmlCodePanel` props identical across tasks.
