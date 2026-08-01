package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import jakarta.persistence.EntityManager;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.Comparator;
import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Pins {@code scheduledCleanup()} to the retention window it must hand each data class. The three
 * windows are deliberately far apart and the fixtures sit in the gaps between them, so swapping any
 * two {@code Duration}s at the call site flips at least two assertions -- the sibling tests all
 * pass their own cutoff in and would stay green through such a mix-up.
 */
@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.local-dir=${java.io.tmpdir}/erd-cowork-wiring-test",
      "erd.storage.workspace-dir=${java.io.tmpdir}/erd-cowork-wiring-workspace-test",
      "erd.storage.cleanup.cron=-",
      "erd.storage.cleanup.dry-run=false",
      "erd.storage.retention.uploads=10d",
      "erd.storage.retention.workspace=20d",
      "erd.storage.retention.artifact=30d",
    })
class RetentionScheduledCleanupWiringTest {

  @Autowired RetentionCleanupService cleanupService;
  @Autowired ChatSessionRepository sessionRepo;
  @Autowired UploadedFileRepository fileRepo;
  @Autowired ChatMessageRepository messageRepo;
  @Autowired ArtifactRepository artifactRepo;
  @Autowired FileStorage storage;
  @Autowired EntityManager entityManager;
  @Autowired PlatformTransactionManager transactionManager;

  private static final Path STORAGE_ROOT =
      Path.of(System.getProperty("java.io.tmpdir")).resolve("erd-cowork-wiring-test");
  private static final Path WORKSPACE_ROOT =
      Path.of(System.getProperty("java.io.tmpdir")).resolve("erd-cowork-wiring-workspace-test");

  @BeforeEach
  void resetDb() {
    // Child rows first to respect FK constraints before deleting parent sessions.
    fileRepo.deleteAll();
    messageRepo.deleteAll();
    artifactRepo.deleteAll();
    sessionRepo.deleteAll();
  }

  @AfterAll
  static void cleanupDirs() throws IOException {
    deleteTree(STORAGE_ROOT);
    deleteTree(WORKSPACE_ROOT);
  }

  @Test
  void scheduledCleanup_fixturesBetweenWindows_appliesEachRetentionToItsOwnDataClass()
      throws IOException {
    // 15 days: past the 10d uploads window, inside the 20d workspace and 30d artifact windows.
    ChatSession recentSession = persistSession(Instant.now().minus(Duration.ofDays(15)));
    String uploadKey =
        storage.store(
            StorageCategory.UPLOAD,
            recentSession.getId(),
            "sales.csv",
            new ByteArrayInputStream("col\n1\n".getBytes(StandardCharsets.UTF_8)));
    UploadedFile upload = persistUpload(recentSession.getId(), uploadKey);
    Path recentWorkspaceDir = createWorkspaceDir(recentSession);
    Artifact recentArtifact =
        persistArtifact(recentSession.getId(), Instant.now().minus(Duration.ofDays(15)));

    // 25 days: past the 20d workspace window, still inside the 30d artifact window.
    ChatSession olderSession = persistSession(Instant.now().minus(Duration.ofDays(25)));
    Path olderWorkspaceDir = createWorkspaceDir(olderSession);
    Artifact olderArtifact =
        persistArtifact(olderSession.getId(), Instant.now().minus(Duration.ofDays(25)));

    cleanupService.scheduledCleanup();

    assertThat(fileRepo.findById(upload.getId()).orElseThrow().isExpired()).isTrue();
    assertThat(Files.exists(recentWorkspaceDir)).isTrue();
    assertThat(Files.exists(olderWorkspaceDir)).isFalse();
    assertThat(artifactRepo.findById(recentArtifact.getId()).orElseThrow().getHtmlStorageKey())
        .isNotNull();
    assertThat(artifactRepo.findById(olderArtifact.getId()).orElseThrow().getHtmlStorageKey())
        .isNotNull();
  }

  private ChatSession persistSession(Instant updatedAt) {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setUserId("wiring-user");
    session.setTitle("wiring session");
    session.setUpdatedAt(updatedAt);
    sessionRepo.saveAndFlush(session);
    // updatedAt is auditing-managed, so the persisted value must be forced by a native update.
    runInOwnTransaction(
        "UPDATE chat_session SET updated_at = ?1 WHERE id = ?2", updatedAt, session.getId());
    return session;
  }

  private UploadedFile persistUpload(String sessionId, String storageKey) {
    UploadedFile upload = new UploadedFile();
    upload.setSessionId(sessionId);
    upload.setName("sales.csv");
    upload.setAlias("file1");
    upload.setStorageKey(storageKey);
    upload.setSizeBytes(6L);
    upload.setType("csv");
    return fileRepo.saveAndFlush(upload);
  }

  private Artifact persistArtifact(String sessionId, Instant createdAt) throws IOException {
    String storageKey =
        storage.store(
            StorageCategory.ARTIFACT,
            sessionId,
            "dashboard.html",
            new ByteArrayInputStream("<html></html>".getBytes(StandardCharsets.UTF_8)));
    Artifact artifact = new Artifact();
    artifact.setSessionId(sessionId);
    artifact.setTitle("wiring dashboard");
    artifact.setHtmlStorageKey(storageKey);
    artifact = artifactRepo.saveAndFlush(artifact);
    // createdAt is auditing-managed and not updatable, so it too needs a native update.
    runInOwnTransaction(
        "UPDATE artifact SET created_at = ?1 WHERE id = ?2", createdAt, artifact.getId());
    return artifact;
  }

  private Path createWorkspaceDir(ChatSession session) throws IOException {
    Path sessionDir =
        WORKSPACE_ROOT.resolve(session.getUserId()).resolve("sessions").resolve(session.getId());
    Files.createDirectories(sessionDir.resolve("results"));
    Files.writeString(sessionDir.resolve("dashboard.html"), "<html></html>");
    return sessionDir;
  }

  /**
   * Native updates must run in their own transaction: this test instance is not a Spring-proxied
   * bean, so a self-invoked {@code @Transactional} method would never see an active transaction.
   */
  private void runInOwnTransaction(String sql, Instant timestamp, String id) {
    new TransactionTemplate(transactionManager)
        .executeWithoutResult(
            status ->
                entityManager
                    .createNativeQuery(sql)
                    .setParameter(1, Timestamp.from(timestamp))
                    .setParameter(2, id)
                    .executeUpdate());
  }

  private static void deleteTree(Path root) throws IOException {
    if (!Files.exists(root)) {
      return;
    }
    String absolutePath = root.toAbsolutePath().toString();
    if (!absolutePath.contains("erd-cowork")) {
      throw new IllegalStateException("refusing to delete non-test path: " + absolutePath);
    }
    try (Stream<Path> walk = Files.walk(root)) {
      walk.sorted(Comparator.reverseOrder())
          .forEach(
              path -> {
                try {
                  Files.deleteIfExists(path);
                } catch (IOException exception) {
                  throw new UncheckedIOException(exception);
                }
              });
    }
  }
}
