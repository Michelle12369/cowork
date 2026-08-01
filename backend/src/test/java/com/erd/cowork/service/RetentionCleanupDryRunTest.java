package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import jakarta.persistence.EntityManager;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.local-dir=${java.io.tmpdir}/erd-cowork-dryrun-test",
      "erd.storage.cleanup.cron=-",
      "erd.storage.cleanup.dry-run=true",
      "erd.storage.retention.uploads=30d",
      "erd.storage.retention.workspace=30d",
      "erd.storage.retention.artifact=730d"
    })
class RetentionCleanupDryRunTest {

  @Autowired RetentionCleanupService cleanupService;
  @Autowired ChatSessionRepository sessionRepo;
  @Autowired UploadedFileRepository fileRepo;
  @Autowired ArtifactRepository artifactRepo;
  @Autowired FileStorage fileStorage;
  @Autowired EntityManager entityManager;
  @Autowired PlatformTransactionManager transactionManager;

  @Test
  void cleanupArtifacts_dryRunEnabled_countsButLeavesFileAndKeyIntact() throws IOException {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setUserId("dry-run-user");
    session.setTitle("dry run session");
    session.setUpdatedAt(Instant.now());
    sessionRepo.save(session);

    String storageKey =
        fileStorage.store(
            StorageCategory.ARTIFACT,
            session.getId(),
            "old.html",
            new ByteArrayInputStream("<html></html>".getBytes(StandardCharsets.UTF_8)));

    Artifact artifact = new Artifact();
    artifact.setSessionId(session.getId());
    artifact.setTitle("old dashboard");
    artifact.setHtmlStorageKey(storageKey);
    artifact = artifactRepo.saveAndFlush(artifact);
    backdateArtifact(artifact.getId(), Instant.now().minus(Duration.ofDays(800)));

    int purged = cleanupService.cleanupArtifacts(Instant.now().minus(Duration.ofDays(730)));

    assertThat(purged).isEqualTo(1);
    assertThat(artifactRepo.findById(artifact.getId()).orElseThrow().getHtmlStorageKey())
        .isEqualTo(storageKey);
    try (InputStream stored = fileStorage.read(storageKey)) {
      assertThat(new String(stored.readAllBytes(), StandardCharsets.UTF_8))
          .isEqualTo("<html></html>");
    }
  }

  /**
   * Covers the sibling dry-run branch in {@code cleanup(Instant)} (uploads), which was previously
   * untested and could regress silently. Backdates the session's {@code updated_at} to a specific
   * value (rather than using a future cutoff that would match every session in the shared test
   * database) so the assertion is exact regardless of what other test classes leave behind.
   */
  @Test
  void cleanup_dryRunEnabled_leavesFileAndExpiredFlagIntact() throws IOException {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setUserId("dry-run-user");
    session.setTitle("dry run upload session");
    session = sessionRepo.saveAndFlush(session);
    backdateSessionUpdatedAt(session.getId(), Instant.now().minus(Duration.ofDays(60)));

    String storageKey =
        fileStorage.store(
            StorageCategory.UPLOAD,
            session.getId(),
            "old.csv",
            new ByteArrayInputStream("col\n1\n".getBytes(StandardCharsets.UTF_8)));

    UploadedFile file = new UploadedFile();
    file.setSessionId(session.getId());
    file.setName("old.csv");
    file.setAlias("file1");
    file.setStorageKey(storageKey);
    file.setSizeBytes(6L);
    file.setType("csv");
    file = fileRepo.saveAndFlush(file);

    int purged = cleanupService.cleanup(Instant.now().minus(Duration.ofDays(30)));

    assertThat(purged).isEqualTo(1);
    UploadedFile stillActive = fileRepo.findById(file.getId()).orElseThrow();
    assertThat(stillActive.isExpired()).isFalse();
    try (InputStream stored = fileStorage.read(storageKey)) {
      assertThat(new String(stored.readAllBytes(), StandardCharsets.UTF_8)).isEqualTo("col\n1\n");
    }
  }

  /**
   * Artifact.createdAt is auditing-managed ({@code updatable = false}); backdate it via a native
   * update run in its own transaction, since a plain method on this non-proxied test instance would
   * not otherwise see an active transaction.
   */
  void backdateArtifact(String artifactId, Instant createdAt) {
    new TransactionTemplate(transactionManager)
        .executeWithoutResult(
            status ->
                entityManager
                    .createNativeQuery("UPDATE artifact SET created_at = ?1 WHERE id = ?2")
                    .setParameter(1, Timestamp.from(createdAt))
                    .setParameter(2, artifactId)
                    .executeUpdate());
  }

  /**
   * ChatSession.updatedAt is auditing-managed ({@code @LastModifiedDate}) and gets overwritten to
   * "now" on every save, so a stale value can only be forced via a native update -- same technique
   * and same reason as {@link #backdateArtifact}.
   */
  void backdateSessionUpdatedAt(String sessionId, Instant updatedAt) {
    new TransactionTemplate(transactionManager)
        .executeWithoutResult(
            status ->
                entityManager
                    .createNativeQuery("UPDATE chat_session SET updated_at = ?1 WHERE id = ?2")
                    .setParameter(1, Timestamp.from(updatedAt))
                    .setParameter(2, sessionId)
                    .executeUpdate());
  }
}
