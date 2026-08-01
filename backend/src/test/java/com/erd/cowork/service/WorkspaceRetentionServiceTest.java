package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import jakarta.persistence.EntityManager;
import java.io.IOException;
import java.io.UncheckedIOException;
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

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.workspace-dir=${java.io.tmpdir}/erd-cowork-workspace-test",
      "erd.storage.cleanup.cron=-"
    })
class WorkspaceRetentionServiceTest {

  @Autowired WorkspaceRetentionService workspaceRetentionService;
  @Autowired ChatSessionRepository sessionRepo;
  @Autowired ChatMessageRepository messageRepo;
  @Autowired UploadedFileRepository fileRepo;
  @Autowired ArtifactRepository artifactRepo;
  @Autowired EntityManager entityManager;
  @Autowired PlatformTransactionManager transactionManager;

  private static final Path WORKSPACE_ROOT =
      Path.of(System.getProperty("java.io.tmpdir")).resolve("erd-cowork-workspace-test");

  @BeforeEach
  void resetDb() {
    // Child rows first to respect FK constraints before deleting parent sessions.
    fileRepo.deleteAll();
    messageRepo.deleteAll();
    artifactRepo.deleteAll();
    sessionRepo.deleteAll();
  }

  @AfterAll
  static void cleanupWorkspaceDir() throws IOException {
    if (!Files.exists(WORKSPACE_ROOT)) {
      return;
    }
    String absolutePath = WORKSPACE_ROOT.toAbsolutePath().toString();
    if (!absolutePath.contains("erd-cowork")) {
      throw new IllegalStateException("refusing to delete non-test path: " + absolutePath);
    }
    try (Stream<Path> walk = Files.walk(WORKSPACE_ROOT)) {
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

  @Test
  void purgeStaleSessions_sessionIdleBeyondCutoff_removesSessionDirectory() throws IOException {
    ChatSession session = persistSession(Instant.now().minus(Duration.ofDays(400)));
    Path sessionDir = workspaceSessionDir(session);
    Files.createDirectories(sessionDir.resolve("results"));
    Files.writeString(sessionDir.resolve("dashboard.html"), "<html></html>");

    int purged =
        workspaceRetentionService.purgeStaleSessions(Instant.now().minus(Duration.ofDays(180)));

    assertThat(purged).isEqualTo(1);
    assertThat(Files.exists(sessionDir)).isFalse();
  }

  @Test
  void purgeStaleSessions_activeSession_keepsSessionDirectory() throws IOException {
    ChatSession session = persistSession(Instant.now());
    Path sessionDir = workspaceSessionDir(session);
    Files.createDirectories(sessionDir);
    Files.writeString(sessionDir.resolve("dashboard.html"), "<html></html>");

    int purged =
        workspaceRetentionService.purgeStaleSessions(Instant.now().minus(Duration.ofDays(180)));

    assertThat(purged).isZero();
    assertThat(Files.exists(sessionDir)).isTrue();
  }

  @Test
  void purgeStaleSessions_directoryAlreadyAbsent_doesNotCount() {
    persistSession(Instant.now().minus(Duration.ofDays(400)));

    int purged =
        workspaceRetentionService.purgeStaleSessions(Instant.now().minus(Duration.ofDays(180)));

    assertThat(purged).isZero();
  }

  /**
   * Blast-radius guard for the path-traversal case a plain {@code startsWith(workspaceRoot)} check
   * misses: a malformed row with {@code sessionId=".."} resolves (after {@code resolve("sessions")
   * .resolve("..")} cancels out) to the {@code {userId}} directory itself -- still "under
   * workspaceRoot", so a guard that only checks containment (and not that the path is a genuine
   * {@code sessions/{sessionId}} leaf) would recursively delete every session belonging to that
   * user, including the unrelated active one. Asserts the active sibling and the shared {@code
   * sessions} parent directory both survive alongside the correctly-purged, legitimately stale
   * session.
   */
  @Test
  void purgeStaleSessions_malformedSessionIdTraversal_doesNotDeleteSiblingSessions()
      throws IOException {
    ChatSession activeSession = persistSession(Instant.now());
    ChatSession staleSession = persistSession(Instant.now().minus(Duration.ofDays(400)));
    persistSessionWithId("..", Instant.now().minus(Duration.ofDays(400)));
    Path activeDir = workspaceSessionDir(activeSession);
    Path staleDir = workspaceSessionDir(staleSession);
    Path sessionsParentDir = activeDir.getParent();
    Files.createDirectories(activeDir);
    Files.createDirectories(staleDir);
    Files.writeString(activeDir.resolve("dashboard.html"), "<html></html>");

    int purged =
        workspaceRetentionService.purgeStaleSessions(Instant.now().minus(Duration.ofDays(180)));

    assertThat(purged).isEqualTo(1);
    assertThat(Files.exists(staleDir)).isFalse();
    assertThat(Files.exists(activeDir)).isTrue();
    assertThat(Files.exists(sessionsParentDir)).isTrue();
  }

  /**
   * ChatSession.updatedAt is auditing-managed ({@code @LastModifiedDate}) and gets overwritten to
   * "now" on every save, so backdating it requires a native update run in its own transaction --
   * this test instance is not a Spring-proxied bean, so a self-invoked {@code @Transactional}
   * method would never see an active transaction.
   */
  ChatSession persistSession(Instant updatedAt) {
    return persistSessionWithId(UUID.randomUUID().toString(), updatedAt);
  }

  /**
   * Same as {@link #persistSession}, but lets the caller pick a specific id -- used to plant the
   * malformed {@code ".."} row for the path-traversal test, which a real client can never submit
   * (see {@code SessionGuard}'s UUID-format validation) but which the service must still defend
   * against for any future write path.
   */
  ChatSession persistSessionWithId(String id, Instant updatedAt) {
    ChatSession session = new ChatSession();
    session.setId(id);
    session.setUserId("workspace-user");
    session.setTitle("workspace session");
    session.setUpdatedAt(updatedAt);
    sessionRepo.saveAndFlush(session);
    new TransactionTemplate(transactionManager)
        .executeWithoutResult(
            status ->
                entityManager
                    .createNativeQuery("UPDATE chat_session SET updated_at = ?1 WHERE id = ?2")
                    .setParameter(1, Timestamp.from(updatedAt))
                    .setParameter(2, id)
                    .executeUpdate());
    return session;
  }

  Path workspaceSessionDir(ChatSession session) {
    return WORKSPACE_ROOT.resolve(session.getUserId()).resolve("sessions").resolve(session.getId());
  }
}
