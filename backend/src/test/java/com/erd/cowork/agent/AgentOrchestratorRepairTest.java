package com.erd.cowork.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.atLeast;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.event.ArtifactEvent;
import com.erd.cowork.agent.event.StepEvent;
import com.erd.cowork.agent.event.StepStatus;
import com.erd.cowork.agent.event.TokenEvent;
import com.erd.cowork.agent.model.AgentOutcome;
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
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.service.SessionGuard;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
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
import org.springframework.transaction.support.TransactionTemplate;
import reactor.core.publisher.Flux;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AgentOrchestratorRepairTest {

  // ── common broken/repaired HTML ────────────────────────────────────────────

  /** HTML with genuinely broken JS (unclosed brace). */
  private static final String BROKEN_HTML =
      "<!DOCTYPE html><html><head></head><body><script>const x = {</script></body></html>";

  /** Syntactically valid repaired HTML. */
  private static final String REPAIRED_HTML =
      "<!DOCTYPE html><html><head></head><body><script>const x = {};</script></body></html>";

  /**
   * HTML with a placeholder-comment omission (syntactically valid). The JS line comment matches the
   * ZH patterns "圖表程式略" and "保留原本".
   */
  private static final String OMISSION_HTML =
      "<!DOCTYPE html><html><head></head><body>"
          + "<script>\n"
          + "// (其他 KPI 及圖表程式略，保留原本結構)\n"
          + "const x = {};\n"
          + "</script></body></html>";

  /** Clean HTML with no omission comments and no syntax errors. */
  private static final String CLEAN_HTML =
      "<!DOCTYPE html><html><head></head><body>"
          + "<script>const x = {};</script>"
          + "</body></html>";

  @Mock private SessionGuard sessionGuard;
  @Mock private ChatMessageRepository messages;
  @Mock private UploadedFileRepository uploadedFiles;
  @Mock private ArtifactRepository artifacts;
  @Mock private DashboardAgentProvider provider;
  @Mock private ChatSessionRepository sessionRepository;
  @Mock private ArtifactAssembler artifactAssembler;
  @Mock private FileStorage fileStorage;
  @Mock private TransactionTemplate transactionTemplate;
  @Mock private StorageProperties storageProperties;

  private AgentConversationWriter conversationWriter;
  private AgentOrchestrator orchestrator;

  @BeforeEach
  void setUp() throws Exception {
    when(transactionTemplate.execute(any()))
        .thenAnswer(
            inv -> {
              org.springframework.transaction.support.TransactionCallback<?> cb =
                  inv.getArgument(0);
              return cb.doInTransaction(null);
            });

    ArtifactRewriteProperties rewriteProperties =
        new ArtifactRewriteProperties("tw3-ec5", java.util.Map.of());
    conversationWriter =
        new AgentConversationWriter(
            messages,
            artifacts,
            artifactAssembler,
            fileStorage,
            transactionTemplate,
            rewriteProperties);

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
            storageProperties);

    // Default: harden() is a passthrough — individual tests override as needed.
    when(provider.harden(anyString(), any(), any()))
        .thenAnswer(inv -> RepairResult.passthrough(inv.getArgument(2)));

    ChatSession session = new ChatSession();
    when(sessionGuard.loadOrCreateOwnedAs(anyString(), anyString())).thenReturn(session);
    when(messages.findBySessionIdOrderByCreatedAtAsc(anyString())).thenReturn(List.of());
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
              artifact.setId("artifact-repair-1");
              return artifact;
            });
    doReturn("storage-key").when(fileStorage).store(any(), any(), any(), any());
  }

  /**
   * Returns the HTML bytes passed to {@code fileStorage.store(...)} for the assembled (plain {@code
   * .html}, not {@code .raw.html}) file — this test file's {@code artifactAssembler} stub is a
   * passthrough, so assemble never changes the HTML and no dedicated raw file is written; the
   * assembled file therefore always carries the same content the writer received as raw input.
   */
  private String capturedAssembledHtml() {
    try {
      ArgumentCaptor<String> filenameCaptor = ArgumentCaptor.forClass(String.class);
      ArgumentCaptor<InputStream> streamCaptor = ArgumentCaptor.forClass(InputStream.class);
      Mockito.verify(fileStorage, atLeast(1))
          .store(
              eq(StorageCategory.ARTIFACT),
              anyString(),
              filenameCaptor.capture(),
              streamCaptor.capture());
      List<String> filenames = filenameCaptor.getAllValues();
      List<InputStream> streams = streamCaptor.getAllValues();
      for (int index = 0; index < filenames.size(); index++) {
        String filename = filenames.get(index);
        if (filename.endsWith(".html") && !filename.endsWith(".raw.html")) {
          return new String(streams.get(index).readAllBytes(), StandardCharsets.UTF_8);
        }
      }
      throw new AssertionError("No assembled .html fileStorage.store call was captured");
    } catch (IOException ioException) {
      throw new RuntimeException("Failed to read captured HTML stream in test", ioException);
    }
  }

  // ── RP1: successful repair — r1 RUNNING → SUCCESS in SSE, repaired html stored ─

  @Test
  void stream_brokenJs_repairEnabled_emitsR1Steps_storesRepairedHtml() {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.just(new TokenEvent("original-token")),
                () -> new AgentOutcome("", BROKEN_HTML, null)));

    // Stub harden() to simulate successful JS repair.
    CompletableFuture<HardenedOutput> outputFuture = new CompletableFuture<>();
    Flux<AgentEvent> hardenEvents =
        Flux.concat(
            Flux.just(
                (AgentEvent) new StepEvent("r1", "偵測到 1 個 JS 問題，自動修復中", null, StepStatus.RUNNING)),
            Flux.defer(
                () -> {
                  outputFuture.complete(new HardenedOutput(REPAIRED_HTML, ""));
                  return Flux.just(
                      (AgentEvent) new StepEvent("r1", "JS 問題修復完成（1 個）", null, StepStatus.SUCCESS));
                }));
    // doReturn style: re-stubbing with when(provider.harden(...)) would trigger the
    // setUp passthrough answer with null arguments during stubbing.
    doReturn(new RepairResult(hardenEvents, outputFuture))
        .when(provider)
        .harden(anyString(), any(), any());

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

    // DB must store the REPAIRED html — assemble is a passthrough stub in this file, so no
    // dedicated raw file is written (rawHtmlStorageKey stays null); the assembled file carries it.
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts, atLeast(1)).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getRawHtmlStorageKey()).isNull();
    assertThat(capturedAssembledHtml()).isEqualTo(REPAIRED_HTML);

    // stepsJson must contain r1 SUCCESS
    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);
    assertThat(aiMsg.getStepsJson()).contains("\"stepKey\":\"r1\"");
    assertThat(aiMsg.getStepsJson()).contains("SUCCESS");

    // provider.generate() called exactly once — repair is now inside harden(), not generate()
    Mockito.verify(provider, Mockito.times(1)).generate(any());
  }

  // ── RP2: repair fails — original html stored, r1 ERROR in SSE ─────────────

  @Test
  void stream_brokenJs_repairFails_originalHtmlStored_r1Error() {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", BROKEN_HTML, null)));

    // Stub harden() to simulate failed JS repair.
    CompletableFuture<HardenedOutput> outputFuture = new CompletableFuture<>();
    Flux<AgentEvent> hardenEvents =
        Flux.concat(
            Flux.just(
                (AgentEvent) new StepEvent("r1", "偵測到 1 個 JS 問題，自動修復中", null, StepStatus.RUNNING)),
            Flux.defer(
                () -> {
                  // Repair did not fix the HTML — return the original
                  outputFuture.complete(new HardenedOutput(BROKEN_HTML, ""));
                  return Flux.just(
                      (AgentEvent) new StepEvent("r1", "JS 問題修復失敗", "1 個問題未修復", StepStatus.ERROR));
                }));
    // doReturn style: re-stubbing with when(provider.harden(...)) would trigger the
    // setUp passthrough answer with null arguments during stubbing.
    doReturn(new RepairResult(hardenEvents, outputFuture))
        .when(provider)
        .harden(anyString(), any(), any());

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // r1 ERROR in SSE
    assertThat(events)
        .filteredOn(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(e -> assertThat(((StepEvent) e).status()).isEqualTo(StepStatus.ERROR));

    // DB must store the ORIGINAL (broken) html — assemble is a passthrough stub in this file, so
    // no dedicated raw file is written (rawHtmlStorageKey stays null).
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts, atLeast(1)).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getRawHtmlStorageKey()).isNull();
    assertThat(capturedAssembledHtml()).isEqualTo(BROKEN_HTML);

    // stepsJson contains r1 with ERROR
    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);
    assertThat(aiMsg.getStepsJson()).contains("\"stepKey\":\"r1\"");
    assertThat(aiMsg.getStepsJson()).contains("ERROR");

    // provider.generate() called exactly once
    Mockito.verify(provider, Mockito.times(1)).generate(any());
  }

  // ── RP3: repair passthrough — no r1 steps, original html stored ───────────

  @Test
  void stream_repairDisabled_noRepairCall_originalHtmlStored() {
    // Default harden() passthrough from setUp() applies — no r1 steps emitted.
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", BROKEN_HTML, null)));

    orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // provider.generate() called exactly once (harden() is passthrough)
    Mockito.verify(provider, Mockito.times(1)).generate(any());

    // DB stores the original (broken) html — assemble is a passthrough stub in this file, so no
    // dedicated raw file is written (rawHtmlStorageKey stays null).
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts, atLeast(1)).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getRawHtmlStorageKey()).isNull();
    assertThat(capturedAssembledHtml()).isEqualTo(BROKEN_HTML);
  }

  // ── RP4: valid JS HTML — harden passthrough, single provider call ─────────

  @Test
  void stream_validJs_noRepairTriggered() {
    String validHtml = "<!DOCTYPE html><html><script>const x = {};</script></html>";

    // Default harden() passthrough from setUp() applies.
    when(provider.generate(any()))
        .thenReturn(new ProviderResult(Flux.empty(), () -> new AgentOutcome("", validHtml, null)));

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // No r1 step in SSE — harden returned passthrough
    assertThat(events).noneMatch(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()));

    // provider called only once
    Mockito.verify(provider, Mockito.times(1)).generate(any());
  }

  // ── RP5: cancel during repair — CAS gate prevents double-persist ───────────
  //
  // Sequence: r1 RUNNING emitted → CountDownLatch fires → main thread disposes.
  // doOnCancel wins the aiPersisted CAS → persists "回應已中斷，請重新送出以繼續".
  // After the sleep, the harden deferred lambda completes, tries the same CAS → loses →
  // logs → returns Flux.empty(). End state: exactly one AI row ("回應已中斷，請重新送出以繼續"),
  // zero artifact rows.

  @Test
  void stream_cancelDuringRepair_repairResultDiscarded_noDoublePersist()
      throws InterruptedException {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", BROKEN_HTML, null)));

    // Stub harden() with a slow deferred repair so cancel has time to win the CAS.
    CompletableFuture<HardenedOutput> outputFuture = new CompletableFuture<>();
    Flux<AgentEvent> hardenEvents =
        Flux.concat(
            Flux.just(
                (AgentEvent) new StepEvent("r1", "偵測到 1 個 JS 問題，自動修復中", null, StepStatus.RUNNING)),
            Flux.defer(
                () -> {
                  // Block for 500ms so doOnCancel fires and wins the CAS first.
                  try {
                    Thread.sleep(500);
                  } catch (InterruptedException ignored) {
                  }
                  outputFuture.complete(new HardenedOutput(REPAIRED_HTML, ""));
                  return Flux.just(
                      (AgentEvent) new StepEvent("r1", "JS 問題修復完成（1 個）", null, StepStatus.SUCCESS));
                }));
    // doReturn style: re-stubbing with when(provider.harden(...)) would trigger the
    // setUp passthrough answer with null arguments during stubbing.
    doReturn(new RepairResult(hardenEvents, outputFuture))
        .when(provider)
        .harden(anyString(), any(), any());

    java.util.concurrent.CountDownLatch r1RunningLatch = new java.util.concurrent.CountDownLatch(1);

    reactor.core.Disposable disposable =
        orchestrator.stream("user-1", "session-1", "build dashboard", null)
            .doOnNext(
                event -> {
                  if (event instanceof StepEvent se
                      && "r1".equals(se.stepKey())
                      && se.status() == StepStatus.RUNNING) {
                    r1RunningLatch.countDown();
                  }
                })
            .subscribe(event -> {}, error -> {}, () -> {});

    // Await r1 RUNNING — by the time countDown fires, the repair defer has started blocking.
    assertThat(r1RunningLatch.await(5, java.util.concurrent.TimeUnit.SECONDS)).isTrue();

    // Dispose immediately — doOnCancel fires before the 500ms sleep unblocks.
    disposable.dispose();

    // Allow doOnCancel persistence + repair CAS-loser path to complete.
    org.awaitility.Awaitility.await()
        .atMost(java.time.Duration.ofSeconds(3))
        .untilAsserted(
            () -> {
              ArgumentCaptor<ChatMessage> captor = ArgumentCaptor.forClass(ChatMessage.class);
              Mockito.verify(messages, Mockito.atLeast(2)).save(captor.capture());
              boolean hasInterruptedMsg =
                  captor.getAllValues().stream()
                      .anyMatch(m -> "回應已中斷，請重新送出以繼續".equals(m.getText()));
              assertThat(hasInterruptedMsg).isTrue();
              // CAS guard must prevent double-persist — zero artifact rows.
              Mockito.verify(artifacts, Mockito.never()).save(any());
            });
  }

  // ── OM1: html with omission comment → r1 RUNNING/SUCCESS, retried html stored

  @Test
  void stream_htmlWithOmissionComment_emitsOmissionR1Labels_storesRetriedHtml() {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(
                Flux.just(new TokenEvent("original-token")),
                () -> new AgentOutcome("", OMISSION_HTML, null)));

    // Stub harden() to simulate successful omission retry.
    CompletableFuture<HardenedOutput> outputFuture = new CompletableFuture<>();
    Flux<AgentEvent> hardenEvents =
        Flux.concat(
            Flux.just((AgentEvent) new StepEvent("r1", "偵測到程式碼省略，重新生成中", null, StepStatus.RUNNING)),
            Flux.defer(
                () -> {
                  outputFuture.complete(new HardenedOutput(CLEAN_HTML, ""));
                  return Flux.just(
                      (AgentEvent) new StepEvent("r1", "程式碼省略已修復", null, StepStatus.SUCCESS));
                }));
    // doReturn style: re-stubbing with when(provider.harden(...)) would trigger the
    // setUp passthrough answer with null arguments during stubbing.
    doReturn(new RepairResult(hardenEvents, outputFuture))
        .when(provider)
        .harden(anyString(), any(), any());

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // r1 RUNNING must carry the omission title
    assertThat(events)
        .filteredOn(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            e -> {
              StepEvent se = (StepEvent) e;
              assertThat(se.status()).isEqualTo(StepStatus.RUNNING);
              assertThat(se.title()).isEqualTo("偵測到程式碼省略，重新生成中");
            });

    // r1 SUCCESS must carry the omission-fixed title
    assertThat(events)
        .filteredOn(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            e -> {
              StepEvent se = (StepEvent) e;
              assertThat(se.status()).isEqualTo(StepStatus.SUCCESS);
              assertThat(se.title()).isEqualTo("程式碼省略已修復");
            });

    // ArtifactEvent must be present
    assertThat(events).anyMatch(e -> e instanceof ArtifactEvent);

    // DB must store the CLEAN retried html, not the original OMISSION html — assemble is a
    // passthrough stub in this file, so no dedicated raw file is written (key stays null).
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts, atLeast(1)).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getRawHtmlStorageKey()).isNull();
    assertThat(capturedAssembledHtml()).isEqualTo(CLEAN_HTML);

    // stepsJson must contain r1 SUCCESS
    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);
    assertThat(aiMsg.getStepsJson()).contains("\"stepKey\":\"r1\"");
    assertThat(aiMsg.getStepsJson()).contains("SUCCESS");

    // provider.generate() called exactly once — retry is inside harden(), not generate()
    Mockito.verify(provider, Mockito.times(1)).generate(any());
  }

  // ── OM2: omission retry fails → original html stored + r1 ERROR ───────────

  @Test
  void stream_omissionRetryFails_originalHtmlStored_r1OmissionError() {
    when(provider.generate(any()))
        .thenReturn(
            new ProviderResult(Flux.empty(), () -> new AgentOutcome("", OMISSION_HTML, null)));

    // Stub harden() to simulate failed omission retry.
    CompletableFuture<HardenedOutput> outputFuture = new CompletableFuture<>();
    Flux<AgentEvent> hardenEvents =
        Flux.concat(
            Flux.just((AgentEvent) new StepEvent("r1", "偵測到程式碼省略，重新生成中", null, StepStatus.RUNNING)),
            Flux.defer(
                () -> {
                  // Retry did not help — return original omission HTML
                  outputFuture.complete(new HardenedOutput(OMISSION_HTML, ""));
                  return Flux.just(
                      (AgentEvent) new StepEvent("r1", "程式碼省略修復失敗", null, StepStatus.ERROR));
                }));
    // doReturn style: re-stubbing with when(provider.harden(...)) would trigger the
    // setUp passthrough answer with null arguments during stubbing.
    doReturn(new RepairResult(hardenEvents, outputFuture))
        .when(provider)
        .harden(anyString(), any(), any());

    List<AgentEvent> events =
        orchestrator.stream("user-1", "session-1", "build dashboard", null).collectList().block();

    // r1 ERROR with omission-specific label
    assertThat(events)
        .filteredOn(e -> e instanceof StepEvent se && "r1".equals(se.stepKey()))
        .anySatisfy(
            e -> {
              StepEvent se = (StepEvent) e;
              assertThat(se.status()).isEqualTo(StepStatus.ERROR);
              assertThat(se.title()).isEqualTo("程式碼省略修復失敗");
            });

    // DB must store the ORIGINAL omission html (not the retry) — assemble is a passthrough stub
    // in this file, so no dedicated raw file is written (rawHtmlStorageKey stays null).
    ArgumentCaptor<Artifact> artifactCaptor = ArgumentCaptor.forClass(Artifact.class);
    Mockito.verify(artifacts, atLeast(1)).save(artifactCaptor.capture());
    assertThat(artifactCaptor.getValue().getRawHtmlStorageKey()).isNull();
    assertThat(capturedAssembledHtml()).isEqualTo(OMISSION_HTML);

    // stepsJson contains r1 with ERROR
    ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
    Mockito.verify(messages, atLeast(2)).save(msgCaptor.capture());
    ChatMessage aiMsg = msgCaptor.getAllValues().get(msgCaptor.getAllValues().size() - 1);
    assertThat(aiMsg.getStepsJson()).contains("ERROR");

    // provider.generate() called exactly once
    Mockito.verify(provider, Mockito.times(1)).generate(any());
  }
}
