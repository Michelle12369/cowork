package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatSessionRepository;
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
}
