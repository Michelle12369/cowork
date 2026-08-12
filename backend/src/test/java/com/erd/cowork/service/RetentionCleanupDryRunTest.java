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
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.mongodb.core.MongoTemplate;
import org.springframework.data.mongodb.core.query.Criteria;
import org.springframework.data.mongodb.core.query.Query;
import org.springframework.data.mongodb.core.query.Update;
import org.springframework.test.context.TestPropertySource;

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.local-dir=${java.io.tmpdir}/erd-cowork-dryrun-test",
      "erd.storage.workspace-dir=${java.io.tmpdir}/erd-cowork-dryrun-workspace-test",
      "erd.storage.cleanup.cron=-",
      "erd.storage.cleanup.dry-run=true",
      "erd.storage.retention.uploads=30d",
      "erd.storage.retention.workspace=30d",
      "erd.storage.retention.artifact=730d"
    })
class RetentionCleanupDryRunTest {

  @Autowired RetentionCleanupService cleanupService;
  @Autowired WorkspaceRetentionService workspaceRetentionService;
  @Autowired ChatSessionRepository sessionRepo;
  @Autowired UploadedFileRepository fileRepo;
  @Autowired ArtifactRepository artifactRepo;
  @Autowired FileStorage fileStorage;
  @Autowired MongoTemplate mongoTemplate;

  private static final Path WORKSPACE_TEST_ROOT =
      Path.of(System.getProperty("java.io.tmpdir")).resolve("erd-cowork-dryrun-workspace-test");

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
    artifact = artifactRepo.save(artifact);
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
   * untested and could regress silently. Backdates the session's {@code updatedAt} to a specific
   * value (rather than using a future cutoff that would match every session in the shared test
   * database) so the assertion is exact regardless of what other test classes leave behind.
   */
  @Test
  void cleanup_dryRunEnabled_leavesFileAndExpiredFlagIntact() throws IOException {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setUserId("dry-run-user");
    session.setTitle("dry run upload session");
    session = sessionRepo.save(session);
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
    file = fileRepo.save(file);

    int purged = cleanupService.cleanup(Instant.now().minus(Duration.ofDays(30)));

    assertThat(purged).isEqualTo(1);
    UploadedFile stillActive = fileRepo.findById(file.getId()).orElseThrow();
    assertThat(stillActive.isExpired()).isFalse();
    try (InputStream stored = fileStorage.read(storageKey)) {
      assertThat(new String(stored.readAllBytes(), StandardCharsets.UTF_8)).isEqualTo("col\n1\n");
    }
  }

  /**
   * Covers the dry-run branch in {@code purgeStaleSessions} (workspace) -- the destructive, `rm
   * -rf`-equivalent path in this whole feature -- which had zero test coverage even though the
   * dry-run branches for the sibling uploads/artifacts cleanups above are both covered. Backdates
   * the session's {@code updatedAt} to a specific value (same technique/reason as {@link
   * #cleanup_dryRunEnabled_leavesFileAndExpiredFlagIntact}) so the assertion is exact regardless of
   * what other test classes leave behind in the shared database.
   */
  @Test
  void purgeStaleSessions_dryRunEnabled_countsButLeavesDirectoryIntact() throws IOException {
    ChatSession session = new ChatSession();
    session.setId(UUID.randomUUID().toString());
    session.setUserId("dry-run-workspace-user");
    session.setTitle("dry run workspace session");
    session = sessionRepo.save(session);
    backdateSessionUpdatedAt(session.getId(), Instant.now().minus(Duration.ofDays(60)));

    Path sessionDir =
        WORKSPACE_TEST_ROOT
            .resolve(session.getUserId())
            .resolve("sessions")
            .resolve(session.getId());
    Files.createDirectories(sessionDir);
    Files.writeString(sessionDir.resolve("dashboard.html"), "<html></html>");

    int purged =
        workspaceRetentionService.purgeStaleSessions(Instant.now().minus(Duration.ofDays(30)));

    assertThat(purged).isEqualTo(1);
    assertThat(Files.exists(sessionDir)).isTrue();
    assertThat(Files.exists(sessionDir.resolve("dashboard.html"))).isTrue();
  }

  /**
   * Artifact.createdAt is auditing-managed and only stamped by {@code AuditingHandler} on save, so
   * backdating it for a test requires a direct collection update that bypasses the repository/
   * auditing layer entirely -- Mongo writes are synchronous and single-document, so no surrounding
   * transaction is needed (unlike the JPA native-update version this replaced).
   */
  void backdateArtifact(String artifactId, Instant createdAt) {
    mongoTemplate.updateFirst(
        Query.query(Criteria.where("id").is(artifactId)),
        Update.update("createdAt", createdAt),
        Artifact.class);
  }

  /**
   * ChatSession.updatedAt is auditing-managed ({@code @LastModifiedDate}) and gets overwritten to
   * "now" on every save, so a stale value can only be forced via a direct collection update -- same
   * technique and same reason as {@link #backdateArtifact}.
   */
  void backdateSessionUpdatedAt(String sessionId, Instant updatedAt) {
    mongoTemplate.updateFirst(
        Query.query(Criteria.where("id").is(sessionId)),
        Update.update("updatedAt", updatedAt),
        ChatSession.class);
  }
}
