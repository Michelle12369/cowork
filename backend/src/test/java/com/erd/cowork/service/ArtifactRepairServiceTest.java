package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.agent.repair.ArtifactRepairer;
import com.erd.cowork.agent.repair.BrowserJsError;
import com.erd.cowork.agent.repair.BrowserRepairOutcome;
import com.erd.cowork.artifact.ArtifactAssembler;
import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.Sender;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.exception.BrowserRepairUnsupportedException;
import com.erd.cowork.exception.ConflictException;
import com.erd.cowork.exception.FilesExpiredException;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.time.Duration;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import reactor.core.publisher.Mono;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class ArtifactRepairServiceTest {

  @Mock ArtifactRepository artifacts;
  @Mock SessionGuard sessionGuard;
  @Mock ArtifactRepairer artifactRepairer;
  @Mock ArtifactAssembler artifactAssembler;
  @Mock UploadedFileRepository uploadedFiles;
  @Mock ChatMessageRepository chatMessages;
  @Mock FileStorage fileStorage;
  @Mock StorageProperties storageProperties;
  @Mock ArtifactService artifactService;

  ObjectMapper objectMapper = new ObjectMapper();
  ArtifactRepairService service;

  @BeforeEach
  void setUp() {
    when(storageProperties.retention())
        .thenReturn(
            new StorageProperties.Retention(
                Duration.ofDays(30), Duration.ofDays(30), Duration.ofDays(730)));
    when(uploadedFiles.findBySessionId(any())).thenReturn(List.of());
    // Default: the active provider supports browser repair — the one test that exercises the
    // unsupported path (below) overrides this.
    when(artifactRepairer.isBrowserRepairSupported()).thenReturn(true);
    service =
        new ArtifactRepairService(
            artifacts,
            sessionGuard,
            artifactRepairer,
            artifactAssembler,
            uploadedFiles,
            objectMapper,
            chatMessages,
            fileStorage,
            storageProperties,
            artifactService);
  }

  // ── error paths ────────────────────────────────────────────────────────────

  @Test
  void repairFromBrowserErrors_providerUnsupported_throwsBrowserRepairUnsupported() {
    when(artifactRepairer.isBrowserRepairSupported()).thenReturn(false);

    assertThatThrownBy(
            () ->
                service.repairFromBrowserErrors("art-1", List.of(new BrowserJsError("err", 1, 0))))
        .isInstanceOf(BrowserRepairUnsupportedException.class);

    verify(artifacts, never()).findById(any());
    verify(chatMessages, never()).save(any());
  }

  @Test
  void repairFromBrowserErrors_artifactNotFound_throwsNotFound() {
    when(artifacts.findById("missing")).thenReturn(Optional.empty());

    assertThatThrownBy(
            () ->
                service.repairFromBrowserErrors(
                    "missing", List.of(new BrowserJsError("err", 1, 0))))
        .isInstanceOf(NotFoundException.class);

    verify(chatMessages, never()).save(any());
  }

  @Test
  void repairFromBrowserErrors_noStoredHtmlAtAll_throwsConflict() {
    Artifact artifact = new Artifact();
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    when(artifactService.loadRawHtml(artifact)).thenReturn(Optional.empty());
    ChatSession session = new ChatSession();
    session.setUserId("user-1");
    when(sessionGuard.loadOwned(any())).thenReturn(session);

    assertThatThrownBy(
            () ->
                service.repairFromBrowserErrors("art-1", List.of(new BrowserJsError("err", 1, 0))))
        .isInstanceOf(ConflictException.class);

    verify(chatMessages, never()).save(any());
  }

  @Test
  void repairFromBrowserErrors_hasExpiredFiles_throwsFilesExpiredException() {
    Artifact artifact = brokenArtifact("art-exp", null);
    when(artifacts.findById("art-exp")).thenReturn(Optional.of(artifact));
    stubOwnedSession();

    UploadedFile expiredFile = new UploadedFile();
    expiredFile.setExpired(true);
    when(uploadedFiles.findBySessionId(any())).thenReturn(List.of(expiredFile));

    assertThatThrownBy(
            () ->
                service.repairFromBrowserErrors(
                    "art-exp", List.of(new BrowserJsError("err", 1, 0))))
        .isInstanceOf(FilesExpiredException.class);

    verify(chatMessages, never()).save(any());
    verify(artifactRepairer, never()).repairWithBrowserErrors(any(), any(), any(), any());
  }

  // ── success path: storage interaction ─────────────────────────────────────

  @Test
  void repairFromBrowserErrors_passed_storesNewHtmlAndUpdatesKey() throws IOException {
    Artifact artifact = brokenArtifact("art-1", null);
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    stubPassedOutcome("<html>fixed</html>");
    when(artifactAssembler.assemble(any(), eq("<html>fixed</html>")))
        .thenReturn("<html>fixed+data</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), eq("art-1.html"), any()))
        .thenReturn("new-key");

    boolean result =
        service.repairFromBrowserErrors("art-1", List.of(new BrowserJsError("e", 1, 0)));

    assertThat(result).isTrue();
    assertThat(artifact.getHtmlStorageKey()).isEqualTo("new-key");
    verify(fileStorage).store(eq(StorageCategory.ARTIFACT), any(), eq("art-1.html"), any());
  }

  @Test
  void repairFromBrowserErrors_passed_deletesOldStorageKey() throws IOException {
    Artifact artifact = brokenArtifact("art-del", "old-key");
    when(artifacts.findById("art-del")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    stubPassedOutcome("<html>fixed</html>");
    when(artifactAssembler.assemble(any(), any())).thenReturn("<html>fixed+data</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), any(), any()))
        .thenReturn("new-key");

    service.repairFromBrowserErrors("art-del", List.of(new BrowserJsError("e", 1, 0)));

    verify(fileStorage).delete("old-key");
  }

  @Test
  void repairFromBrowserErrors_passed_noOldKey_deleteNotCalled() throws IOException {
    Artifact artifact = brokenArtifact("art-new", null); // new artifact: no previous storageKey
    when(artifacts.findById("art-new")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    stubPassedOutcome("<html>fixed</html>");
    when(artifactAssembler.assemble(any(), any())).thenReturn("<html>fixed+data</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), any(), any()))
        .thenReturn("new-key");

    service.repairFromBrowserErrors("art-new", List.of(new BrowserJsError("e", 1, 0)));

    verify(fileStorage, never()).delete(any());
  }

  @Test
  void repairFromBrowserErrors_deleteOldKeyThrowsIOException_doesNotBlockRepair()
      throws IOException {
    Artifact artifact = brokenArtifact("art-fail", "stale-key");
    when(artifacts.findById("art-fail")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    stubPassedOutcome("<html>fixed</html>");
    when(artifactAssembler.assemble(any(), any())).thenReturn("<html>fixed+data</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), any(), any()))
        .thenReturn("new-key");
    doThrow(new IOException("storage unavailable")).when(fileStorage).delete("stale-key");

    // Delete failure must not propagate — repair still succeeds.
    boolean result =
        service.repairFromBrowserErrors("art-fail", List.of(new BrowserJsError("e", 1, 0)));

    assertThat(result).isTrue();
    verify(artifacts).save(artifact);
  }

  // ── success path: DB fields ────────────────────────────────────────────────

  @Test
  void repairFromBrowserErrors_passed_updatesHtmlStorageKey() throws IOException {
    Artifact artifact = brokenArtifact("art-2", null);
    when(artifacts.findById("art-2")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    stubPassedOutcome("<html>fixed</html>");
    when(artifactAssembler.assemble(any(), eq("<html>fixed</html>")))
        .thenReturn("<html>fixed+data</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), any(), any()))
        .thenReturn("stored-key");

    service.repairFromBrowserErrors("art-2", List.of(new BrowserJsError("e", 1, 0)));

    assertThat(artifact.getHtmlStorageKey()).isEqualTo("stored-key");
    verify(artifacts).save(artifact);
  }

  @Test
  void repairFromBrowserErrors_success_storesNewRawFileAndDeletesOldKeys() throws IOException {
    Artifact artifact = new Artifact();
    artifact.setHtmlStorageKey("old-html-key");
    artifact.setRawHtmlStorageKey("old-raw-key");
    setArtifactId(artifact, "art-1");
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    when(artifactService.loadRawHtml(artifact)).thenReturn(Optional.of("<html>broken</html>"));
    stubPassedOutcome("<html>fixed</html>");
    when(artifactAssembler.assemble(any(), eq("<html>fixed</html>")))
        .thenReturn("<html>fixed-assembled</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), eq("art-1.html"), any()))
        .thenReturn("new-html-key");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), eq("art-1.raw.html"), any()))
        .thenReturn("new-raw-key");

    boolean repaired =
        service.repairFromBrowserErrors("art-1", List.of(new BrowserJsError("e", 1, 0)));

    assertThat(repaired).isTrue();
    assertThat(artifact.getHtmlStorageKey()).isEqualTo("new-html-key");
    assertThat(artifact.getRawHtmlStorageKey()).isEqualTo("new-raw-key");
    verify(fileStorage).delete("old-html-key");
    verify(fileStorage).delete("old-raw-key");
  }

  @Test
  void repairFromBrowserErrors_passed_savesSuccessRecord() throws IOException {
    Artifact artifact = brokenArtifact("art-3", null);
    when(artifacts.findById("art-3")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    stubPassedOutcome("<html>fixed</html>");
    when(artifactAssembler.assemble(any(), any())).thenReturn("<html>fixed+data</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), any(), any())).thenReturn("key-3");

    service.repairFromBrowserErrors(
        "art-3", List.of(new BrowserJsError("uncaught error msg", 10, 0)));

    verify(chatMessages)
        .save(
            argThat(
                (ChatMessage msg) ->
                    msg.getSender() == Sender.AI
                        && msg.getText() != null
                        && msg.getText().startsWith("已修復儀表板執行錯誤")));
  }

  @Test
  void repairFromBrowserErrors_enrichesErrorsWithSourceLineFromAssembledHtml() throws IOException {
    Artifact artifact = brokenArtifact("art-6", null);
    when(artifacts.findById("art-6")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    // 組裝版=瀏覽器行號的座標系:第 4 行是肇事行;行號 0 與超界不得附 sourceLine。
    when(artifactService.loadAssembledHtml(artifact))
        .thenReturn(
            Optional.of(
                "<html>\n<script>capture()</script>\n<script>\n"
                    + "const rows = stats.rows.rows.map(r => r);\n</script>\n</html>"));
    stubPassedOutcome("<html>fixed</html>");
    when(artifactAssembler.assemble(any(), any())).thenReturn("<html>fixed+data</html>");
    when(fileStorage.store(eq(StorageCategory.ARTIFACT), any(), any(), any())).thenReturn("k6");

    service.repairFromBrowserErrors(
        "art-6",
        List.of(
            new BrowserJsError("TypeError: undefined map", 4, 30),
            new BrowserJsError("unknown location", 0, 0),
            new BrowserJsError("beyond end", 99, 0)));

    verify(artifactRepairer)
        .repairWithBrowserErrors(
            any(),
            any(),
            argThat(
                (List<BrowserJsError> forwarded) ->
                    forwarded.get(0).sourceLine().equals("const rows = stats.rows.rows.map(r => r);")
                        && forwarded.get(1).sourceLine().isEmpty()
                        && forwarded.get(2).sourceLine().isEmpty()),
            any());
  }

  // ── failure path ───────────────────────────────────────────────────────────

  @Test
  void repairFromBrowserErrors_notPassed_returnsFalseWithoutSavingArtifact() {
    Artifact artifact = brokenArtifact("art-4", null);
    when(artifacts.findById("art-4")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    BrowserRepairOutcome outcome = new BrowserRepairOutcome("<html>broken</html>", false);
    when(artifactRepairer.repairWithBrowserErrors(any(), any(), any(), any()))
        .thenReturn(Mono.just(outcome));

    boolean result =
        service.repairFromBrowserErrors("art-4", List.of(new BrowserJsError("e", 1, 0)));

    assertThat(result).isFalse();
    verify(artifacts, never()).save(any());
  }

  @Test
  void repairFromBrowserErrors_notPassed_savesFailureRecord() {
    Artifact artifact = brokenArtifact("art-5", null);
    when(artifacts.findById("art-5")).thenReturn(Optional.of(artifact));
    stubOwnedSession();
    when(uploadedFiles.findBySessionIdAndExpiredFalse(any())).thenReturn(List.of());
    BrowserRepairOutcome outcome = new BrowserRepairOutcome("<html>broken</html>", false);
    when(artifactRepairer.repairWithBrowserErrors(any(), any(), any(), any()))
        .thenReturn(Mono.just(outcome));

    service.repairFromBrowserErrors(
        "art-5", List.of(new BrowserJsError("type error at line 5", 5, 0)));

    verify(chatMessages)
        .save(
            argThat(
                (ChatMessage msg) ->
                    msg.getSender() == Sender.AI
                        && msg.getText() != null
                        && msg.getText().startsWith("儀表板執行錯誤自動修復未成功")));
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  private Artifact brokenArtifact(String artifactId, String existingStorageKey) {
    Artifact artifact = new Artifact();
    artifact.setHtmlStorageKey(existingStorageKey);
    setArtifactId(artifact, artifactId);
    when(artifactService.loadRawHtml(artifact)).thenReturn(Optional.of("<html>broken</html>"));
    return artifact;
  }

  private void setArtifactId(Artifact artifact, String artifactId) {
    try {
      java.lang.reflect.Field idField = Artifact.class.getDeclaredField("id");
      idField.setAccessible(true);
      idField.set(artifact, artifactId);
    } catch (ReflectiveOperationException ex) {
      throw new RuntimeException(ex);
    }
  }

  private void stubOwnedSession() {
    ChatSession session = new ChatSession();
    session.setUserId("user-1");
    when(sessionGuard.loadOwned(any())).thenReturn(session);
  }

  private void stubPassedOutcome(String fixedHtml) {
    BrowserRepairOutcome outcome = new BrowserRepairOutcome(fixedHtml, true);
    when(artifactRepairer.repairWithBrowserErrors(any(), any(), any(), any()))
        .thenReturn(Mono.just(outcome));
  }
}
