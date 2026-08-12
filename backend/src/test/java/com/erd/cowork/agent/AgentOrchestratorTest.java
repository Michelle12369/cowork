package com.erd.cowork.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.atLeast;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.ArtifactEvent;
import com.erd.cowork.agent.event.ErrorEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.model.AgentOutcome;
import com.erd.cowork.agent.model.AgentRequest;
import com.erd.cowork.agent.provider.DashboardAgentProvider;
import com.erd.cowork.agent.provider.HardenedOutput;
import com.erd.cowork.agent.provider.ProviderResult;
import com.erd.cowork.agent.provider.RepairResult;
import com.erd.cowork.artifact.ArtifactAssembler;
import com.erd.cowork.config.ArtifactRewriteProperties;
import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.service.ArtifactService;
import com.erd.cowork.service.SessionGuard;
import com.erd.cowork.storage.FileStorage;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.Mockito;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;
import reactor.core.publisher.Flux;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentOrchestratorTest {

  private static final String BARE_HTML =
      "<!DOCTYPE html>\n<html><head><title>SPC</title></head>"
          + "<body><div id=\"chart\" style=\"height:320px\"></div></body></html>";

  private static final String SESSION_ID = "11111111-2222-3333-4444-555555555555";
  private static final String USER_ID = "user-1";

  @Mock private SessionGuard sessionGuard;
  @Mock private ChatMessageRepository messages;
  @Mock private UploadedFileRepository uploadedFiles;
  @Mock private ArtifactRepository artifacts;
  @Mock private DashboardAgentProvider provider;
  @Mock private ChatSessionRepository sessionRepository;
  @Mock private ArtifactAssembler artifactAssembler;
  @Mock private FileStorage fileStorage;
  @Mock private StorageProperties storageProperties;
  @Mock private ArtifactService artifactService;
  @Mock private TransactionTemplate transactionTemplate;

  private AgentConversationWriter conversationWriter;
  private AgentOrchestrator orchestrator;

  @BeforeEach
  void setUp() throws Exception {
    ArtifactRewriteProperties rewriteProperties =
        new ArtifactRewriteProperties("tw3-ec5", java.util.Map.of());
    // Stub the transaction boundary to just run the callback inline — this is a unit test
    // against mocked repositories, not a real Mongo transaction (that's covered by
    // TransactionSmokeTest against the replica-set harness).
    when(transactionTemplate.execute(any()))
        .thenAnswer(
            invocation -> {
              TransactionCallback<?> callback = invocation.getArgument(0);
              return callback.doInTransaction(new SimpleTransactionStatus());
            });
    Mockito.doAnswer(
            invocation -> {
              java.util.function.Consumer<org.springframework.transaction.TransactionStatus>
                  callback = invocation.getArgument(0);
              callback.accept(new SimpleTransactionStatus());
              return null;
            })
        .when(transactionTemplate)
        .executeWithoutResult(any());
    conversationWriter =
        new AgentConversationWriter(
            messages,
            artifacts,
            artifactAssembler,
            fileStorage,
            rewriteProperties,
            transactionTemplate);

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
            storageProperties,
            artifactService);

    // Default: harden() is a passthrough so existing tests are unaffected.
    when(provider.harden(anyString(), any(), any()))
        .thenAnswer(inv -> RepairResult.passthrough(inv.getArgument(2)));

    // Mock-only session — id intentionally absent; sessionRepository.save is also mocked, so the
    // Persistable NOT-NULL id contract of ChatSession never applies here.
    ChatSession session = new ChatSession();
    when(sessionGuard.loadOrCreateOwnedAs(anyString(), anyString())).thenReturn(session);
    when(messages.findBySessionIdOrderByCreatedAtAsc(anyString())).thenReturn(List.of());
    // Default: no expired files — all non-expired-guard tests use this path.
    when(uploadedFiles.findBySessionId(anyString())).thenReturn(List.of());
    when(uploadedFiles.findBySessionIdAndExpiredFalse(anyString())).thenReturn(List.of());
    when(messages.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));
    when(sessionRepository.save(any(ChatSession.class))).thenAnswer(inv -> inv.getArgument(0));
    when(artifactAssembler.assemble(anyString(), anyString()))
        .thenAnswer(inv -> inv.getArgument(1));
    when(artifacts.findFirstBySessionIdOrderByCreatedAtDesc(anyString()))
        .thenReturn(Optional.empty());
    when(artifacts.countBySessionId(anyString())).thenReturn(0L);
    when(artifacts.save(any(Artifact.class)))
        .thenAnswer(
            inv -> {
              Artifact artifact = inv.getArgument(0);
              artifact.setId("artifact-1");
              return artifact;
            });
    // persistHtmlResult stores assembled HTML in FileStorage (not CLOB); stub to avoid NPE.
    // doReturn avoids compile-time checked-exception handling for store()'s throws IOException.
    Mockito.doReturn("storage-key").when(fileStorage).store(any(), any(), any(), any());
  }

  private void stubProvider(String answerText, String html) {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome(answerText, html, null)));
  }

  /**
   * Returns the HTML bytes passed to {@code fileStorage.store(...)} for the assembled (plain {@code
   * .html}, not {@code .raw.html}) file — this test file's {@code artifactAssembler} stub is a
   * passthrough, so assemble never changes the HTML; the assembled file therefore always carries
   * the same content the writer received as raw input.
   */
  private String capturedAssembledHtml() {
    try {
      ArgumentCaptor<String> filenameCaptor = ArgumentCaptor.forClass(String.class);
      ArgumentCaptor<java.io.InputStream> streamCaptor =
          ArgumentCaptor.forClass(java.io.InputStream.class);
      Mockito.verify(fileStorage, atLeast(1))
          .store(
              eq(com.erd.cowork.storage.StorageCategory.ARTIFACT),
              anyString(),
              filenameCaptor.capture(),
              streamCaptor.capture());
      List<String> filenames = filenameCaptor.getAllValues();
      List<java.io.InputStream> streams = streamCaptor.getAllValues();
      for (int index = 0; index < filenames.size(); index++) {
        String filename = filenames.get(index);
        if (filename.endsWith(".html") && !filename.endsWith(".raw.html")) {
          return new String(
              streams.get(index).readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
        }
      }
      throw new AssertionError("No assembled .html fileStorage.store call was captured");
    } catch (java.io.IOException ioException) {
      throw new RuntimeException("Failed to read captured HTML stream in test", ioException);
    }
  }

  @Test
  void stream_bareHtmlWithoutFence_createsArtifactAndStripsHtmlFromMessage() {
    stubProvider("Here is the dashboard.\n" + BARE_HTML + "\nEnjoy!", null);

    // Override harden() to simulate bare-HTML promotion performed by GenerationRepairGuard.
    // doReturn style: re-stubbing with when(provider.harden(...)) would trigger the
    // setUp passthrough answer with null arguments during stubbing.
    Mockito.doReturn(
            new RepairResult(
                Flux.empty(),
                CompletableFuture.completedFuture(
                    new HardenedOutput(BARE_HTML, "（儀表板已生成 → 右側面板）"))))
        .when(provider)
        .harden(anyString(), any(), any());

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    assertThat(events).anyMatch(event -> event instanceof ArtifactEvent);

    // persistHtmlResult calls artifacts.save twice (once for UUID, once to write storageKey).
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts, Mockito.atLeast(1)).save(artifactCaptor.capture());
    // Raw is always stored regardless of the __ERD_DATA__ marker; the assembled file carries
    // the promoted bare HTML.
    assertThat(artifactCaptor.getValue().getRawHtmlStorageKey()).isNotNull();
    assertThat(capturedAssembledHtml()).contains("<title>SPC</title>");

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
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    assertThat(events).anyMatch(event -> event instanceof ArtifactEvent);
  }

  @Test
  void stream_plainAnswerWithoutHtml_producesNoArtifact() {
    stubProvider("這是純聊天回答，沒有任何儀表板。", null);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "hello", null).collectList().block();

    assertThat(events).noneMatch(event -> event instanceof ArtifactEvent);
    Mockito.verify(artifacts, Mockito.never()).save(any());
  }

  // ── E1: expired-files guard — FILES_EXPIRED ErrorEvent emitted, no USER message saved ──

  @Test
  void stream_hasExpiredFiles_emitsFilesExpiredEventWithoutSavingUserMessage() {
    // Seed one expired file for the session.
    UploadedFile expiredFile = new UploadedFile();
    expiredFile.setExpired(true);
    expiredFile.setSessionId("session-1");
    when(uploadedFiles.findBySessionId("session-1")).thenReturn(List.of(expiredFile));
    when(storageProperties.retention())
        .thenReturn(
            new StorageProperties.Retention(
                Duration.ofDays(30), Duration.ofDays(30), Duration.ofDays(730)));

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // Must emit exactly one ErrorEvent with code FILES_EXPIRED.
    assertThat(events)
        .filteredOn(event -> event instanceof ErrorEvent)
        .singleElement()
        .satisfies(event -> assertThat(((ErrorEvent) event).code()).isEqualTo("FILES_EXPIRED"));

    // USER message must NOT be persisted — no orphaned rows.
    Mockito.verify(messages, Mockito.never()).save(any(ChatMessage.class));

    // Provider must NOT be invoked — request is rejected before the LLM call.
    Mockito.verify(provider, Mockito.never()).generate(any());
  }

  // ── N1: null-message guard — ErrorEvent.message() is always non-null ────────

  @Test
  void stream_agentExceptionWithNullMessage_errorEventMessageNonNull() {
    // Some JDK exceptions (e.g. NullPointerException) may have a null message.
    // The orchestrator must default to the class simple name instead.
    when(provider.generate(any())).thenThrow(new NullPointerException((String) null));

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    assertThat(events)
        .filteredOn(event -> event instanceof ErrorEvent)
        .isNotEmpty()
        .allMatch(event -> ((ErrorEvent) event).message() != null);
  }

  // ── C1: AGENT_ERROR path persists an AI reply so USER message is not left orphaned ─

  @Test
  void stream_agentError_persistsAiErrorMessage() {
    // prepare() succeeds (USER message saved) but provider.generate() throws,
    // reaching the outer onErrorResume(AGENT_ERROR) path.
    when(provider.generate(any())).thenThrow(new RuntimeException("provider exploded"));

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // ErrorEvent must be emitted
    assertThat(events)
        .filteredOn(event -> event instanceof ErrorEvent)
        .isNotEmpty()
        .allMatch(event -> ((ErrorEvent) event).message() != null);

    // messages.save() must have been called at least twice: USER message + AI error message
    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    boolean hasAiErrorMsg =
        msgCaptor.getAllValues().stream()
            .anyMatch(
                chatMessage ->
                    com.erd.cowork.domain.Sender.AI.equals(chatMessage.getSender())
                        && chatMessage.getText() != null
                        && chatMessage.getText().contains("provider exploded"));
    assertThat(hasAiErrorMsg).isTrue();
  }

  // ── Part B: stepsJson persists every step event the provider emits ─────────

  @Test
  void stream_providerEmitsStepThenError_stepsJsonContainsEmittedStep() {
    // Provider first emits a step (d1), then signals an error via ErrorEvent.
    AgentEvent d1 = new StepEvent("d1", "讀取", null, StepStatus.RUNNING);
    AgentEvent err = new ErrorEvent("PROVIDER_ERROR", "upstream failure");

    when(provider.generate(any()))
        .thenReturn(new ProviderResult(Flux.just(d1, err), () -> new AgentOutcome("", null, null)));

    orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // The AI ChatMessage saved to DB must have stepsJson containing the step the provider emitted.
    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);

    String stepsJson = aiMsg.getStepsJson();
    assertThat(stepsJson).isNotNull();

    // Every step the provider emitted (just d1 here) must be present, and nothing else.
    com.fasterxml.jackson.databind.ObjectMapper stepsMapper =
        new com.fasterxml.jackson.databind.ObjectMapper();
    com.fasterxml.jackson.databind.JsonNode stepsArray;
    try {
      stepsArray = stepsMapper.readTree(stepsJson);
    } catch (com.fasterxml.jackson.core.JsonProcessingException jsonException) {
      throw new RuntimeException(jsonException);
    }
    assertThat(stepsArray.isArray()).isTrue();
    assertThat(stepsArray.size()).isEqualTo(1);
    assertThat(stepsArray.get(0).get("stepKey").asText()).isEqualTo("d1");
  }

  @Test
  void stream_providerEmitsStepWithoutDPrefix_stepIsPersisted() {
    // The stepKey prefix convention is deleted (spec §16.4-1): a step key carries no
    // "should this render" meaning any more. A provider-emitted key with no "d" prefix
    // (e.g. the analysis provider's literal "analysis") must still be persisted.
    AgentEvent analysisStep = new StepEvent("analysis", "分析資料中", null, StepStatus.RUNNING);

    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.just(analysisStep), () -> new AgentOutcome("分析完成", null, null)));

    orchestrator.stream("user-1", "session-1", "analyze data", null).collectList().block();

    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);

    String stepsJson = aiMsg.getStepsJson();
    assertThat(stepsJson).isNotNull();
    assertThat(stepsJson).contains("\"stepKey\":\"analysis\"");
  }

  @Test
  void stream_providerEmitsOneStep_persistedStepsJsonContainsExactlyThatStep()
      throws com.fasterxml.jackson.core.JsonProcessingException {
    // Provider emits one step (d1); success path (no HTML).
    AgentEvent d1 = new StepEvent("d1", "分析圖表", null, StepStatus.SUCCESS);

    when(provider.generate(any()))
        .thenReturn(new ProviderResult(Flux.just(d1), () -> new AgentOutcome("分析完成", null, null)));

    orchestrator.stream("user-1", "session-1", "analyze data", null).collectList().block();

    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);

    String stepsJson = aiMsg.getStepsJson();
    assertThat(stepsJson).isNotNull();

    // All emitted steps are persisted — the array must have exactly one entry (d1).
    com.fasterxml.jackson.databind.ObjectMapper om =
        new com.fasterxml.jackson.databind.ObjectMapper();
    com.fasterxml.jackson.databind.JsonNode arr = om.readTree(stepsJson);
    assertThat(arr.isArray()).isTrue();
    assertThat(arr.size()).isEqualTo(1);
    assertThat(arr.get(0).get("stepKey").asText()).isEqualTo("d1");
  }

  @Test
  void stream_providerEmitsNoSteps_persistedStepsJsonIsEmptyArray()
      throws com.fasterxml.jackson.core.JsonProcessingException {
    // Provider emits no step events; plain answer path.
    stubProvider("純文字回答", null);

    orchestrator.stream("user-1", "session-1", "hello", null).collectList().block();

    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);

    com.fasterxml.jackson.databind.ObjectMapper om =
        new com.fasterxml.jackson.databind.ObjectMapper();
    com.fasterxml.jackson.databind.JsonNode arr = om.readTree(aiMsg.getStepsJson());
    assertThat(arr.isArray()).isTrue();
    assertThat(arr.size()).isEqualTo(0);
  }

  // ── D1: client disconnect mid-stream — interrupted AI message is persisted ───

  @Test
  void stream_clientCancelsMidStream_persistsInterruptedAiMessage() {
    // Provider emits one TOKEN event then never completes (simulates a live SSE stream).
    // take(1) consumes the token then cancels the upstream, triggering doOnCancel.
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.concat(Flux.just(new TokenEvent("chunk")), Flux.never()),
                () -> new AgentOutcome("", null, null)));

    orchestrator.stream("user-1", "session-1", "build dashboard", null)
        .take(1)
        .collectList()
        .block(Duration.ofSeconds(5));

    // Persistence is async on boundedElastic — await up to 2 s for the save to complete.
    await()
        .atMost(Duration.ofSeconds(2))
        .untilAsserted(
            () -> {
              ArgumentCaptor<ChatMessage> captor = ArgumentCaptor.forClass(ChatMessage.class);
              Mockito.verify(messages, Mockito.atLeast(2)).save(captor.capture());
              boolean hasInterruptedMsg =
                  captor.getAllValues().stream()
                      .anyMatch(
                          chatMessage ->
                              Sender.AI.equals(chatMessage.getSender())
                                  && "回應已中斷，請重新送出以繼續".equals(chatMessage.getText())
                                  && "[]".equals(chatMessage.getStepsJson()));
              assertThat(hasInterruptedMsg).isTrue();
            });
  }

  // ── D2: normal completion — no extra "interrupted" row is written ────────────

  @Test
  void stream_normalCompletion_doesNotPersistInterruptedMessage() {
    // Normal plain-answer run — stream completes naturally; doOnCancel must NOT fire.
    stubProvider("純文字回答", null);

    orchestrator.stream("user-1", "session-1", "hello", null).collectList().block();

    ArgumentCaptor<ChatMessage> captor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(captor.capture());

    boolean hasInterruptedMsg =
        captor.getAllValues().stream().anyMatch(m -> "回應已中斷，請重新送出以繼續".equals(m.getText()));
    assertThat(hasInterruptedMsg).isFalse();
  }

  // ── F1: blank-answer fallback — html present, answerText empty → default sentence ────

  @Test
  void stream_htmlPresentAndAnswerBlank_persistsDefaultFallbackSentence() {
    // gpt-oss sometimes returns HTML with no surrounding explanation text.
    // The orchestrator must store the constant fallback sentence so the DB text field
    // is never empty when an artifact is present.
    stubProvider("", BARE_HTML);

    orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);

    assertThat(aiMsg.getText()).isEqualTo("✅ 儀表板已產生並顯示於右側面板。");
    assertThat(aiMsg.getArtifactId()).isEqualTo("artifact-1");
  }

  @Test
  void stream_htmlPresentAndAnswerNull_persistsDefaultFallbackSentence() {
    stubProvider(null, BARE_HTML);

    orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);

    assertThat(aiMsg.getText()).isEqualTo("✅ 儀表板已產生並顯示於右側面板。");
  }

  @Test
  void stream_htmlPresentAndAnswerNonBlank_preservesOriginalAnswerText() {
    // When the provider already supplies explanation text, it must NOT be replaced by the fallback.
    stubProvider("自訂說明文字。", BARE_HTML);

    orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, Mockito.atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);

    assertThat(aiMsg.getText()).isEqualTo("自訂說明文字。");
  }

  // ── T1: artifact titles are sequential "Version N" within a session ─────────

  @Test
  void stream_noExistingArtifacts_titleIsVersion1() {
    when(artifacts.countBySessionId("session-1")).thenReturn(0L);
    stubProvider("explanation", BARE_HTML);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    assertThat(events)
        .filteredOn(event -> event instanceof ArtifactEvent)
        .singleElement()
        .satisfies(event -> assertThat(((ArtifactEvent) event).title()).isEqualTo("Version 1"));
  }

  @Test
  void stream_twoExistingArtifacts_titleIsVersion3() {
    when(artifacts.countBySessionId("session-1")).thenReturn(2L);
    stubProvider("explanation", BARE_HTML);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    assertThat(events)
        .filteredOn(event -> event instanceof ArtifactEvent)
        .singleElement()
        .satisfies(event -> assertThat(((ArtifactEvent) event).title()).isEqualTo("Version 3"));
  }

  @Test
  void stream_regenerateQuestion_titleIsNextVersionNumber() {
    when(artifacts.countBySessionId("session-1")).thenReturn(1L);
    stubProvider("explanation", BARE_HTML);

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "Regenerate the dashboard.", null)
            .collectList()
            .block();

    assertThat(events)
        .filteredOn(event -> event instanceof ArtifactEvent)
        .singleElement()
        .satisfies(event -> assertThat(((ArtifactEvent) event).title()).isEqualTo("Version 2"));
  }

  // ── A1: history truncation — options summary survives 600-char AI message ────

  @Test
  void stream_aiHistoryWith600CharsAndQuestions_optionsSummaryNotTruncated() {
    // An AI ChatMessage whose text is 600 chars and which carried questionsJson.
    // After the A1 fix: base text is truncated to 500 *before* the options are appended,
    // so the options summary always appears in the HistoryMessage passed to provider.generate().
    String longText = "x".repeat(600);
    String questionsJson =
        "[{\"text\":\"Which chart?\","
            + "\"options\":[\"Control Chart\",\"Histogram\"],\"multiSelect\":false}]";

    ChatMessage priorAiMsg = new ChatMessage();
    priorAiMsg.setSessionId("session-1");
    priorAiMsg.setSender(Sender.AI);
    priorAiMsg.setText(longText);
    priorAiMsg.setQuestionsJson(questionsJson);

    // Override the default empty-list stub for this test.
    when(messages.findBySessionIdOrderByCreatedAtAsc("session-1")).thenReturn(List.of(priorAiMsg));

    // Capture the AgentRequest to inspect the history entries built by buildHistoryMessage().
    ArgumentCaptor<AgentRequest> requestCaptor = ArgumentCaptor.forClass(AgentRequest.class);
    when(provider.generate(requestCaptor.capture()))
        .thenReturn(new ProviderResult(Flux.empty(), () -> new AgentOutcome("answer", null, null)));

    orchestrator.stream("user-1", "session-1", "follow-up question", null).collectList().block();

    AgentRequest captured = requestCaptor.getValue();
    assertThat(captured.history()).hasSize(1);
    String histText = captured.history().get(0).text();

    // Options summary must be present — not truncated.
    assertThat(histText).contains("[你提供過的選項:");
    assertThat(histText).contains("Control Chart");

    // Base text must be capped at 500 chars (not 600); options appear after that boundary.
    int optionsIdx = histText.indexOf("[你提供過的選項:");
    assertThat(optionsIdx).isLessThanOrEqualTo(502); // 500 chars + "\n" before the tag
    assertThat(histText.substring(0, optionsIdx)).doesNotContain("x".repeat(501));
  }

  // ── baseArtifactId resolution: AgentRequest.previousArtifactHtml, both provider modes ────
  // resolveArtifactHtml() is called unconditionally in prepare() regardless of provider — the
  // mocked `provider` here stands in for either mode since the resolution logic itself does not
  // branch on provider type (see AgentOrchestrator#resolveArtifactHtml).

  @Test
  void stream_baseArtifactIdSpecified_agentRequestCarriesThatArtifactsRawHtml() {
    Artifact specifiedArtifact = new Artifact();
    specifiedArtifact.setId("artifact-old");
    specifiedArtifact.setSessionId("session-1");
    specifiedArtifact.setRawHtmlStorageKey("artifacts/session-1/uuid_a.raw.html");
    when(artifacts.findById("artifact-old")).thenReturn(Optional.of(specifiedArtifact));
    when(artifactService.loadRawHtml(specifiedArtifact))
        .thenReturn(Optional.of("<p>version one</p>"));

    ArgumentCaptor<AgentRequest> requestCaptor = ArgumentCaptor.forClass(AgentRequest.class);
    when(provider.generate(requestCaptor.capture()))
        .thenReturn(new ProviderResult(Flux.empty(), () -> new AgentOutcome("answer", null, null)));

    orchestrator.stream("user-1", "session-1", "iterate on v1", "artifact-old")
        .collectList()
        .block();

    assertThat(requestCaptor.getValue().previousArtifactHtml()).isEqualTo("<p>version one</p>");
  }

  @Test
  void stream_baseArtifactIdNotSpecified_agentRequestFallsBackToMostRecentArtifactRawHtml() {
    Artifact latestArtifact = new Artifact();
    latestArtifact.setId("artifact-latest");
    latestArtifact.setSessionId("session-1");
    latestArtifact.setRawHtmlStorageKey("artifacts/session-1/uuid_b.raw.html");
    when(artifacts.findFirstBySessionIdOrderByCreatedAtDesc("session-1"))
        .thenReturn(Optional.of(latestArtifact));
    when(artifactService.loadRawHtml(latestArtifact))
        .thenReturn(Optional.of("<p>latest version</p>"));

    ArgumentCaptor<AgentRequest> requestCaptor = ArgumentCaptor.forClass(AgentRequest.class);
    when(provider.generate(requestCaptor.capture()))
        .thenReturn(new ProviderResult(Flux.empty(), () -> new AgentOutcome("answer", null, null)));

    orchestrator.stream("user-1", "session-1", "iterate", null).collectList().block();

    assertThat(requestCaptor.getValue().previousArtifactHtml()).isEqualTo("<p>latest version</p>");
  }

  @Test
  void stream_baseArtifactIdNotOwnedBySession_agentRequestFallsBackToMostRecentArtifactRawHtml() {
    // Specified artifact belongs to a different session — sessionId ownership check in
    // resolveArtifactHtml() must reject it and fall back to the most-recent artifact instead.
    // The filter rejects foreignArtifact before loadRawHtml is ever invoked on it, so no
    // artifactService stub is needed for it.
    Artifact foreignArtifact = new Artifact();
    foreignArtifact.setId("artifact-foreign");
    foreignArtifact.setSessionId("other-session");
    foreignArtifact.setRawHtmlStorageKey("artifacts/other-session/uuid_c.raw.html");
    when(artifacts.findById("artifact-foreign")).thenReturn(Optional.of(foreignArtifact));

    Artifact latestArtifact = new Artifact();
    latestArtifact.setId("artifact-latest");
    latestArtifact.setSessionId("session-1");
    latestArtifact.setRawHtmlStorageKey("artifacts/session-1/uuid_d.raw.html");
    when(artifacts.findFirstBySessionIdOrderByCreatedAtDesc("session-1"))
        .thenReturn(Optional.of(latestArtifact));
    when(artifactService.loadRawHtml(latestArtifact))
        .thenReturn(Optional.of("<p>latest version</p>"));

    ArgumentCaptor<AgentRequest> requestCaptor = ArgumentCaptor.forClass(AgentRequest.class);
    when(provider.generate(requestCaptor.capture()))
        .thenReturn(new ProviderResult(Flux.empty(), () -> new AgentOutcome("answer", null, null)));

    orchestrator.stream("user-1", "session-1", "iterate", "artifact-foreign").collectList().block();

    assertThat(requestCaptor.getValue().previousArtifactHtml()).isEqualTo("<p>latest version</p>");
  }

  // ── updatedAt touch: session activity must advance updatedAt every turn ──────

  @Test
  void prepare_secondTurn_advancesSessionUpdatedAt() {
    Instant staleTimestamp = Instant.now().minus(Duration.ofDays(10));
    ChatSession session = new ChatSession();
    session.setId(SESSION_ID);
    session.setUserId(USER_ID);
    session.setTitle("existing title");
    session.setUpdatedAt(staleTimestamp);

    ChatMessage existingUserMessage = new ChatMessage();
    existingUserMessage.setSender(Sender.USER);

    when(sessionGuard.loadOrCreateOwnedAs(USER_ID, SESSION_ID)).thenReturn(session);
    when(messages.findBySessionIdOrderByCreatedAtAsc(SESSION_ID))
        .thenReturn(List.of(existingUserMessage));
    when(uploadedFiles.findBySessionId(SESSION_ID)).thenReturn(List.of());

    orchestrator.prepareForTest(USER_ID, SESSION_ID, "second question", null);

    assertThat(session.getUpdatedAt()).isAfter(staleTimestamp);
    Mockito.verify(sessionRepository).save(session);
  }
}
