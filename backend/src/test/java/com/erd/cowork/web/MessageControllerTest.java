package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.AnswerEvent;
import com.erd.cowork.agent.extraction.ResponseExtractionHelper;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.service.ArtifactService;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import reactor.core.publisher.Flux;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@TestPropertySource(properties = "tsso.enabled=false")
class MessageControllerTest {

  @Autowired TestRestTemplate rest;
  @Autowired ArtifactRepository artifactRepository;
  @Autowired ArtifactService artifactService;
  @Autowired ChatMessageRepository chatMessageRepository;
  @Autowired ChatSessionRepository chatSessionRepository;
  @Autowired CapturingFakeProvider fakeProvider;

  // ── FakeProvider ─────────────────────────────────────────────────────────

  /**
   * A configurable fake provider that captures each AgentRequest it receives so tests can assert on
   * previousArtifactHtml and other request fields. The token list is swappable per-test.
   */
  static class CapturingFakeProvider implements DashboardAgentProvider {

    // Dashboard-mode HTML references window.__ERD_DATA__ itself (real LLM dashboard code reads
    // from it), which is also the marker ArtifactAssembler uses to decide whether to inject data.
    private static final List<String> DEFAULT_TOKENS =
        List.of("Answer intro ", "```html\n<p>dash</p><!--__ERD_DATA__-->\n```", " tail");

    private final AtomicReference<AgentRequest> lastRequest = new AtomicReference<>();
    private volatile List<String> tokens = DEFAULT_TOKENS;

    AgentRequest getLastRequest() {
      return lastRequest.get();
    }

    /** Replaces the token stream emitted by the next {@code generate()} call. */
    void setTokens(List<String> newTokens) {
      this.tokens = newTokens;
    }

    /** Resets the token stream to the default (HTML-producing) stream. */
    void reset() {
      tokens = DEFAULT_TOKENS;
    }

    @Override
    public ProviderResult generate(AgentRequest request) {
      lastRequest.set(request);
      List<String> current = tokens;
      ResponseExtractionHelper transformer = new ResponseExtractionHelper();
      Flux<AgentEvent> eventFlux =
          Flux.fromIterable(current)
              .transform(transformer::apply)
              .concatWith(
                  Flux.defer(() -> Flux.just(new AnswerEvent(transformer.result().answerText()))));
      return new ProviderResult(eventFlux, transformer::result);
    }
  }

  @TestConfiguration
  static class FakeProviderConfig {

    @Bean
    @Primary
    CapturingFakeProvider fakeProvider() {
      return new CapturingFakeProvider();
    }
  }

  @AfterEach
  void resetProvider() {
    fakeProvider.reset();
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  record SessionSummary(String id, String title, String updatedAt) {}

  record MessageDetail(String id, String sender, String artifactId, String artifactTitle) {}

  record SessionDetail(String id, String title, List<MessageDetail> messages) {}

  private String createSession(String userId) {
    return UUID.randomUUID().toString();
  }

  /** Posts a message (no baseArtifactId) and returns the full raw SSE response body. */
  private String postSse(String sessionId, String userId, String question) {
    return postSse(sessionId, userId, question, null);
  }

  /** Posts a message with optional baseArtifactId and returns the full raw SSE response body. */
  private String postSse(String sessionId, String userId, String question, String baseArtifactId) {
    return postSse(sessionId, userId, question, baseArtifactId, null);
  }

  /** Posts a message with optional baseArtifactId/selectedGroups; returns the raw SSE body. */
  private String postSse(
      String sessionId,
      String userId,
      String question,
      String baseArtifactId,
      List<String> selectedGroups) {
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-User-Id", userId);
    headers.setContentType(MediaType.APPLICATION_JSON);
    headers.set(HttpHeaders.ACCEPT, MediaType.TEXT_EVENT_STREAM_VALUE);
    Map<String, Object> body = new HashMap<>();
    body.put("question", question);
    if (baseArtifactId != null) {
      body.put("baseArtifactId", baseArtifactId);
    }
    if (selectedGroups != null) {
      body.put("selectedGroups", selectedGroups);
    }
    return rest.exchange(
            "/api/sessions/" + sessionId + "/messages",
            HttpMethod.POST,
            new HttpEntity<>(body, headers),
            String.class)
        .getBody();
  }

  /** Uploads a CSV to {@code POST /api/sessions/{id}/files} (field name {@code files}). */
  private void uploadCsv(String sessionId, String userId, String filename, String csvContent) {
    ByteArrayResource resource =
        new ByteArrayResource(csvContent.getBytes(StandardCharsets.UTF_8)) {
          @Override
          public String getFilename() {
            return filename;
          }
        };
    MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
    body.add("files", resource);
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-User-Id", userId);
    headers.setContentType(MediaType.MULTIPART_FORM_DATA);
    rest.postForEntity(
        "/api/sessions/" + sessionId + "/files", new HttpEntity<>(body, headers), String.class);
  }

  // ── tests ─────────────────────────────────────────────────────────────────

  @Test
  void sse_happyPath_emitsTokensAndArtifact_andPersists() {
    String sid = createSession("u1");
    String body = postSse(sid, "u1", "Run SPC analysis of Vt");

    // SSE stream: token content and artifact event present; fixed s1-s4 steps removed (Part B)
    assertThat(body).contains("Answer intro"); // TOKEN content
    assertThat(body).contains("ARTIFACT"); // ArtifactEvent type
    // Fixed steps s1-s4 are no longer emitted
    assertThat(body).doesNotContain("\"stepKey\":\"s1\"");
    assertThat(body).doesNotContain("\"stepKey\":\"s4\"");

    // DB: two messages persisted (USER + AI)
    List<ChatMessage> msgs = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    assertThat(msgs).hasSize(2);
    assertThat(msgs.get(0).getSender()).isEqualTo(Sender.USER);
    ChatMessage aiMsg = msgs.get(1);
    assertThat(aiMsg.getSender()).isEqualTo(Sender.AI);
    assertThat(aiMsg.getStepsJson()).isNotBlank();
    assertThat(aiMsg.getArtifactId()).isNotNull();

    // DB: session title matches question (≤30 chars, no truncation needed)
    ChatSession session = chatSessionRepository.findById(sid).orElseThrow();
    assertThat(session.getTitle()).isEqualTo("Run SPC analysis of Vt");

    // DB: artifact row exists
    assertThat(artifactRepository.findById(aiMsg.getArtifactId())).isPresent();

    // Artifact HTML is stored in FileStorage (not CLOB); read via the artifact endpoint to verify
    // the assembled HTML contains the injected __ERD_DATA__ script.
    String artifactHtml =
        rest.getForObject("/api/artifacts/" + aiMsg.getArtifactId(), String.class);
    assertThat(artifactHtml).contains("window.__ERD_DATA__");

    // Detail DTO: the AI message carries the artifact title (batch-resolved in SessionService)
    HttpHeaders getHeaders = new HttpHeaders();
    getHeaders.set("X-User-Id", "u1");
    SessionDetail detail =
        rest.exchange(
                "/api/sessions/" + sid,
                HttpMethod.GET,
                new HttpEntity<Void>(getHeaders),
                SessionDetail.class)
            .getBody();
    MessageDetail aiDetail =
        detail.messages().stream()
            .filter(messageDetail -> "AI".equals(messageDetail.sender()))
            .findFirst()
            .orElseThrow();
    assertThat(aiDetail.artifactId()).isNotNull();
    assertThat(aiDetail.artifactTitle()).isEqualTo("Version 1");
  }

  @Test
  void sse_withUploadedFile_artifactContainsInjectedData() {
    String sid = createSession("u1");

    // Upload a small CSV before sending the prompt — slug alias "lots", 2 data rows
    uploadCsv(sid, "u1", "lots.csv", "lot,vt\nA,0.42\nB,0.43\n");

    String body = postSse(sid, "u1", "Analyse Vt distribution");
    assertThat(body).contains("ARTIFACT");

    // Retrieve the persisted AI message
    List<ChatMessage> msgs = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    ChatMessage aiMsg =
        msgs.stream()
            .filter(chatMessage -> chatMessage.getSender() == Sender.AI)
            .findFirst()
            .orElseThrow();

    // Artifact HTML is stored in FileStorage (not CLOB); read via the artifact endpoint to verify
    // the assembled HTML contains the real injected data, not an empty map.
    String artifactHtml =
        rest.getForObject("/api/artifacts/" + aiMsg.getArtifactId(), String.class);
    assertThat(artifactHtml).contains("\"lots\""); // slug alias key present (lots.csv → "lots")
    assertThat(artifactHtml).contains("\"columns\""); // entry shape
    assertThat(artifactHtml).contains("\"rows\"");
    assertThat(artifactHtml).contains("0.42"); // actual data value
  }

  @Test
  void sse_secondMessage_doesNotChangeTitle() {
    String sid = createSession("u1");
    postSse(sid, "u1", "First question here");
    String titleAfterFirst = chatSessionRepository.findById(sid).orElseThrow().getTitle();

    postSse(sid, "u1", "Second question here");
    String titleAfterSecond = chatSessionRepository.findById(sid).orElseThrow().getTitle();

    assertThat(titleAfterSecond).isEqualTo(titleAfterFirst);
  }

  @Test
  void sse_othersSession_returnsNotFoundErrorEvent() {
    String sid = createSession("u1");
    // Establish the session under u1 first (upsert creates it on first upload).
    uploadCsv(sid, "u1", "seed.csv", "x\n1\n");
    // u2 tries to access u1's session — prepare phase throws NotFoundException,
    // which is caught inside the Flux and returned as ErrorEvent (HTTP 200 + SSE body)
    String body = postSse(sid, "u2", "Run SPC");

    assertThat(body).contains("NOT_FOUND");
  }

  @Test
  void sse_longQuestion_titleTruncatedTo30() {
    String sid = createSession("u1");
    String longQuestion = "a".repeat(40); // 40 chars → title = 30 chars + "…" = 31 chars
    postSse(sid, "u1", longQuestion);

    ChatSession session = chatSessionRepository.findById(sid).orElseThrow();
    assertThat(session.getTitle()).hasSize(31); // 30 + "…" (U+2026, single char)
    assertThat(session.getTitle()).endsWith("…");
  }

  @Test
  void sse_regenerate_titleIsNextVersionNumber() {
    String sid = createSession("u1");

    // First message produces "Version 1" (0 existing artifacts in session)
    postSse(sid, "u1", "Analyse Vt distribution");
    List<ChatMessage> msgs1 = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    ChatMessage ai1 =
        msgs1.stream()
            .filter(chatMessage -> chatMessage.getSender() == Sender.AI)
            .findFirst()
            .orElseThrow();
    String firstTitle = artifactRepository.findById(ai1.getArtifactId()).orElseThrow().getTitle();
    assertThat(firstTitle).isEqualTo("Version 1");

    // Regenerate — the new artifact gets the next sequential version number, not the question text
    postSse(sid, "u1", "Regenerate the dashboard.");
    List<ChatMessage> msgs2 = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    ChatMessage ai2 =
        msgs2.stream()
            .filter(chatMessage -> chatMessage.getSender() == Sender.AI)
            .reduce((first, second) -> second) // last AI message
            .orElseThrow();

    String secondTitle = artifactRepository.findById(ai2.getArtifactId()).orElseThrow().getTitle();
    assertThat(secondTitle).isEqualTo("Version 2");
  }

  // ── raw_html feedback loop tests ──────────────────────────────────────────

  @Test
  void sse_firstMessage_artifactRawHtmlNonEmpty() {
    String sid = createSession("u-raw-1");
    postSse(sid, "u-raw-1", "Build a Vt trend chart");

    List<ChatMessage> msgs = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    ChatMessage aiMsg =
        msgs.stream()
            .filter(chatMessage -> chatMessage.getSender() == Sender.AI)
            .findFirst()
            .orElseThrow();

    assertThat(aiMsg.getArtifactId()).isNotNull();
    // A dedicated raw file is always written regardless of the __ERD_DATA__ marker
    // (rawHtmlStorageKey non-null); read the raw content back via the central reader
    // (ArtifactService.getRawHtml), not the retired CLOB.
    Artifact artifact = artifactRepository.findById(aiMsg.getArtifactId()).orElseThrow();
    assertThat(artifact.getRawHtmlStorageKey()).isNotNull();
    String rawHtml = artifactService.getRawHtml(aiMsg.getArtifactId());
    assertThat(rawHtml).isNotBlank();
    // rawHtml is the un-injected HTML returned by the provider (before __ERD_DATA__ injection)
    assertThat(rawHtml).contains("<p>dash</p>");
  }

  @Test
  void sse_secondMessage_providerReceivesPreviousArtifactHtml() {
    String sid = createSession("u-raw-2");

    // First question — default fake-provider tokens include the __ERD_DATA__ marker, so a
    // dedicated raw file is written; AgentOrchestrator.resolveArtifactHtml reads it back via
    // ArtifactService.loadRawHtml (the central reader), no manual setup needed here.
    postSse(sid, "u-raw-2", "Initial dashboard");

    // Second question — prepare should load the first artifact's rawHtml into the request
    postSse(sid, "u-raw-2", "Change the title color to red");

    AgentRequest lastReq = fakeProvider.getLastRequest();
    assertThat(lastReq).isNotNull();
    assertThat(lastReq.previousArtifactHtml()).isNotNull();
    assertThat(lastReq.previousArtifactHtml()).contains("<p>dash</p>");
  }

  // ── dynamic steps + questions tests ──────────────────────────────────────

  @Test
  void sse_withStepMarkerAndQuestions_emitsStepD1AndQuestionEvent_andPersists() {
    // Provider emits [[step: 分析]] + a questions block (no HTML dashboard)
    fakeProvider.setTokens(
        List.of(
            "[[step: 分析]]\n",
            "請先釐清需求",
            "```questions\n"
                + "[{\"text\":\"要分析哪個特性？\",\"options\":[\"Bore Diameter\",\"全部\"],"
                + "\"multiSelect\":false}]\n"
                + "```"));

    String sid = createSession("u-step-q");
    String body = postSse(sid, "u-step-q", "幫我分析");

    // SSE must contain a STEP event for the dynamic key d1
    assertThat(body).contains("\"stepKey\":\"d1\"");
    // SSE must contain a QUESTION event with the question text
    assertThat(body).contains("\"type\":\"QUESTION\"");
    assertThat(body).contains("要分析哪個特性");

    // DB: AI message stepsJson must record the d1 dynamic step
    List<ChatMessage> msgs = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    ChatMessage aiMsg =
        msgs.stream()
            .filter(chatMessage -> chatMessage.getSender() == Sender.AI)
            .findFirst()
            .orElseThrow();
    assertThat(aiMsg.getStepsJson()).contains("\"d1\"");

    // DB: questionsJson must be non-empty and contain the question
    assertThat(aiMsg.getQuestionsJson()).isNotBlank();
    assertThat(aiMsg.getQuestionsJson()).contains("要分析哪個特性");
  }

  @Test
  void sse_pureHtmlFlow_regressionNoStepsOrQuestions() {
    // Default tokens: produces HTML, no [[step:]] markers, no ```questions block
    String sid = createSession("u-regress");
    String body = postSse(sid, "u-regress", "Show me the dashboard");

    // Artifact produced; fixed s1/s4 steps no longer emitted (Part B)
    assertThat(body).contains("\"type\":\"ARTIFACT\"");
    assertThat(body).doesNotContain("\"stepKey\":\"s1\"");
    assertThat(body).doesNotContain("\"stepKey\":\"s4\"");
    // No dynamic steps, no question event
    assertThat(body).doesNotContain("\"stepKey\":\"d1\"");
    assertThat(body).doesNotContain("\"type\":\"QUESTION\"");

    // DB: questionsJson null for pure-html response
    List<ChatMessage> msgs = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    ChatMessage aiMsg =
        msgs.stream()
            .filter(chatMessage -> chatMessage.getSender() == Sender.AI)
            .findFirst()
            .orElseThrow();
    assertThat(aiMsg.getQuestionsJson()).isNull();
  }

  // ── baseArtifactId tests ──────────────────────────────────────────────────

  @Test
  void sse_withBaseArtifactId_providerReceivesSpecifiedOlderArtifactRawHtml() {
    String sid = createSession("u-base-id");

    // First message: produces artifact with rawHtml "<p>first</p>"
    fakeProvider.setTokens(List.of("Intro ", "```html\n<p>first</p>\n```", " end"));
    postSse(sid, "u-base-id", "Dashboard v1");

    List<ChatMessage> msgs1 = chatMessageRepository.findBySessionIdOrderByCreatedAtAsc(sid);
    ChatMessage ai1 =
        msgs1.stream()
            .filter(chatMessage -> chatMessage.getSender() == Sender.AI)
            .findFirst()
            .orElseThrow();
    String firstArtifactId = ai1.getArtifactId();
    assertThat(firstArtifactId).isNotNull();
    // "<p>first</p>" has no __ERD_DATA__ marker, but raw is always stored regardless of the
    // marker (rawHtmlStorageKey is non-null); the central reader prefers the raw file, which
    // contains the original content verbatim.
    Artifact firstArtifact = artifactRepository.findById(firstArtifactId).orElseThrow();
    assertThat(firstArtifact.getRawHtmlStorageKey()).isNotNull();
    assertThat(artifactService.getRawHtml(firstArtifactId)).contains("<p>first</p>");
    // AgentOrchestrator.resolveArtifactHtml reads via the same central reader, so no manual setup
    // is needed here to verify the baseArtifactId feed-back wiring below.

    // Second message: produces artifact with rawHtml "<p>second</p>"
    fakeProvider.setTokens(List.of("Intro ", "```html\n<p>second</p>\n```", " end"));
    postSse(sid, "u-base-id", "Dashboard v2");

    // Third request: specify the FIRST artifact explicitly — provider must receive its rawHtml
    postSse(sid, "u-base-id", "Iterate on v1", firstArtifactId);

    AgentRequest lastReq = fakeProvider.getLastRequest();
    assertThat(lastReq).isNotNull();
    assertThat(lastReq.previousArtifactHtml()).isNotNull();
    // Provider must receive the first artifact's rawHtml, not the second's
    assertThat(lastReq.previousArtifactHtml()).contains("<p>first</p>");
    assertThat(lastReq.previousArtifactHtml()).doesNotContain("<p>second</p>");
  }

  // ── C2: history includes options summary from previous questionsJson ──────

  @Test
  void sse_secondMessage_historyIncludesQuestionOptionsSummary() {
    // First message: provider returns a questions block with options
    fakeProvider.setTokens(
        List.of(
            "請先釐清",
            "```questions\n"
                + "[{\"text\":\"選哪個特性？\",\"options\":[\"選項A\",\"選項B\"],"
                + "\"multiSelect\":false}]\n"
                + "```"));

    String sid = createSession("u-hist-q");
    postSse(sid, "u-hist-q", "幫我分析");

    // Second message: reset to default HTML-producing tokens, send answer
    fakeProvider.reset();
    postSse(sid, "u-hist-q", "選項A");

    // The provider must have received a history entry that contains the options summary
    AgentRequest lastReq = fakeProvider.getLastRequest();
    assertThat(lastReq).isNotNull();
    boolean hasOptionsSummary =
        lastReq.history().stream()
            .anyMatch(
                historyEntry ->
                    historyEntry.text().contains("[你提供過的選項:")
                        && historyEntry.text().contains("選項A")
                        && historyEntry.text().contains("選項B"));
    assertThat(hasOptionsSummary).isTrue();
  }

  // ── selectedGroups mapping (§11 connector selection) ───────────────────────

  @Test
  void sse_withSelectedGroups_providerReceivesSelectedGroups() {
    String sid = createSession("u-groups-1");
    postSse(sid, "u-groups-1", "Which sites had defects?", null, List.of("mes", "erp"));

    AgentRequest lastReq = fakeProvider.getLastRequest();
    assertThat(lastReq).isNotNull();
    assertThat(lastReq.selectedGroups()).containsExactly("mes", "erp");
  }

  @Test
  void sse_withoutSelectedGroups_providerReceivesEmptyList() {
    // No selectedGroups key sent at all — SendMessageRequest.selectedGroups() deserializes to
    // null; MessageController must normalize that to an empty list (= all groups invariant).
    String sid = createSession("u-groups-2");
    postSse(sid, "u-groups-2", "Which sites had defects?");

    AgentRequest lastReq = fakeProvider.getLastRequest();
    assertThat(lastReq).isNotNull();
    assertThat(lastReq.selectedGroups()).isEmpty();
  }
}
