package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

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
import java.nio.file.Paths;
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

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.local-dir=${java.io.tmpdir}/erd-cowork-cleanup-test",
      "erd.storage.retention.uploads=30d",
      "erd.storage.retention.workspace=30d",
      "erd.storage.retention.artifact=730d",
      "erd.storage.cleanup.cron=-",
      // 斷言真刪除行為——顯式關 dry-run（全域預設已改 true 偏安全側），不依賴預設值
      "erd.storage.cleanup.dry-run=false",
    })
class RetentionCleanupServiceTest {

  @Autowired RetentionCleanupService cleanupService;
  @Autowired ChatSessionRepository sessionRepo;
  @Autowired UploadedFileRepository fileRepo;
  @Autowired ChatMessageRepository messageRepo;
  @Autowired ArtifactRepository artifactRepo;
  @Autowired FileStorage storage;
  @Autowired EntityManager entityManager;
  @Autowired PlatformTransactionManager transactionManager;

  private static final Path TEST_STORAGE_DIR =
      Paths.get(System.getProperty("java.io.tmpdir"), "erd-cowork-cleanup-test");

  @AfterAll
  static void cleanupStorage() throws IOException {
    if (!Files.exists(TEST_STORAGE_DIR)) {
      return;
    }
    String abs = TEST_STORAGE_DIR.toAbsolutePath().toString();
    if (!abs.contains("erd-cowork")) {
      throw new IllegalStateException("refusing to delete non-test path: " + abs);
    }
    try (Stream<Path> walk = Files.walk(TEST_STORAGE_DIR)) {
      walk.sorted(Comparator.reverseOrder())
          .forEach(
              filePath -> {
                try {
                  Files.deleteIfExists(filePath);
                } catch (IOException exception) {
                  throw new UncheckedIOException(exception);
                }
              });
    }
  }

  @BeforeEach
  void resetDb() {
    // Delete child rows first to respect FK constraints before deleting parent sessions.
    fileRepo.deleteAll();
    messageRepo.deleteAll();
    artifactRepo.deleteAll();
    sessionRepo.deleteAll();
  }

  private ChatSession createSession() {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setTitle("test session");
    session.setUserId("test-user");
    return sessionRepo.saveAndFlush(session);
  }

  private UploadedFile createFile(String sessionId, String storageKey) {
    UploadedFile uploadedFile = new UploadedFile();
    uploadedFile.setSessionId(sessionId);
    uploadedFile.setName("test.csv");
    uploadedFile.setAlias("file1");
    uploadedFile.setStorageKey(storageKey);
    uploadedFile.setSizeBytes(10L);
    uploadedFile.setType("csv");
    return fileRepo.saveAndFlush(uploadedFile);
  }

  /**
   * Artifact.createdAt is auditing-managed ({@code updatable = false}), so backdating it for tests
   * requires a native update that bypasses JPA. Run in its own transaction via TransactionTemplate:
   * a plain {@code @Transactional} method has no effect here because this test instance is not a
   * Spring-proxied bean, so a self-invoked call never goes through AOP.
   */
  void setArtifactCreatedAt(String artifactId, Instant createdAt) {
    new TransactionTemplate(transactionManager)
        .executeWithoutResult(
            status ->
                entityManager
                    .createNativeQuery("UPDATE artifact SET created_at = ?1 WHERE id = ?2")
                    .setParameter(1, Timestamp.from(createdAt))
                    .setParameter(2, artifactId)
                    .executeUpdate());
  }

  @Test
  void cleanup_staleSession_expiresFilesAndPurgesStorage() throws IOException {
    ChatSession session = createSession();

    String key =
        storage.store(
            StorageCategory.UPLOAD,
            session.getId(),
            "test.csv",
            new ByteArrayInputStream("col\n1\n".getBytes(StandardCharsets.UTF_8)));

    UploadedFile file = createFile(session.getId(), key);

    // cutoff in future → session updatedAt < cutoff → session is stale → file gets cleaned up
    int count = cleanupService.cleanup(Instant.now().plusSeconds(60));

    assertThat(count).isEqualTo(1);

    UploadedFile updated = fileRepo.findById(file.getId()).orElseThrow();
    assertThat(updated.isExpired()).isTrue();

    // Row still exists in DB (not deleted)
    assertThat(fileRepo.existsById(file.getId())).isTrue();

    // Storage file is gone — read must throw IOException
    assertThatThrownBy(() -> storage.read(key).close()).isInstanceOf(IOException.class);
  }

  @Test
  void cleanup_recentSession_untouched() throws IOException {
    ChatSession session = createSession();

    String key =
        storage.store(
            StorageCategory.UPLOAD,
            session.getId(),
            "recent.csv",
            new ByteArrayInputStream("col\n2\n".getBytes(StandardCharsets.UTF_8)));

    UploadedFile file = createFile(session.getId(), key);

    // cutoff 365 days ago → session is NOT stale → no files touched
    int count = cleanupService.cleanup(Instant.now().minus(Duration.ofDays(365)));

    assertThat(count).isEqualTo(0);

    UploadedFile updated = fileRepo.findById(file.getId()).orElseThrow();
    assertThat(updated.isExpired()).isFalse();
  }

  @Test
  void cleanupArtifacts_olderThanCutoff_deletesFileAndNullsStorageKey() throws IOException {
    ChatSession session = createSession();
    String storageKey =
        storage.store(
            StorageCategory.ARTIFACT,
            session.getId(),
            "old.html",
            new ByteArrayInputStream("<html></html>".getBytes(StandardCharsets.UTF_8)));

    Artifact artifact = new Artifact();
    artifact.setSessionId(session.getId());
    artifact.setTitle("old dashboard");
    artifact.setHtmlStorageKey(storageKey);
    artifact = artifactRepo.save(artifact);
    // createdAt is auditing-managed; force it past the cutoff via a direct update
    artifactRepo.flush();
    setArtifactCreatedAt(artifact.getId(), Instant.now().minus(Duration.ofDays(800)));

    int purged = cleanupService.cleanupArtifacts(Instant.now().minus(Duration.ofDays(730)));

    assertThat(purged).isEqualTo(1);
    assertThat(artifactRepo.findById(artifact.getId())).isPresent();
    assertThat(artifactRepo.findById(artifact.getId()).orElseThrow().getHtmlStorageKey()).isNull();
    assertThatThrownBy(() -> storage.read(storageKey)).isInstanceOf(IOException.class);
  }

  @Test
  void cleanupArtifacts_withinCutoff_keepsFileAndKey() throws IOException {
    ChatSession session = createSession();
    String storageKey =
        storage.store(
            StorageCategory.ARTIFACT,
            session.getId(),
            "recent.html",
            new ByteArrayInputStream("<html></html>".getBytes(StandardCharsets.UTF_8)));

    Artifact artifact = new Artifact();
    artifact.setSessionId(session.getId());
    artifact.setTitle("recent dashboard");
    artifact.setHtmlStorageKey(storageKey);
    artifact = artifactRepo.save(artifact);

    int purged = cleanupService.cleanupArtifacts(Instant.now().minus(Duration.ofDays(730)));

    assertThat(purged).isZero();
    assertThat(artifactRepo.findById(artifact.getId()).orElseThrow().getHtmlStorageKey())
        .isEqualTo(storageKey);
  }
}
