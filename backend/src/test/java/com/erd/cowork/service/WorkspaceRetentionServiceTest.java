package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import jakarta.persistence.EntityManager;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.Comparator;
import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.NestedTestConfiguration;
import org.springframework.test.context.NestedTestConfiguration.EnclosingConfiguration;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@SpringBootTest
@TestPropertySource(
    properties = {
      "erd.storage.workspace-dir=${java.io.tmpdir}/erd-cowork-workspace-test",
      "erd.storage.cleanup.cron=-",
      // 斷言真刪除行為——顯式關 dry-run（全域預設已改 true 偏安全側），不依賴預設值
      "erd.storage.cleanup.dry-run=false"
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

  /** Sibling of, and deliberately outside, {@link #WORKSPACE_ROOT} -- symlink-escape target. */
  private static final Path ESCAPE_ROOT =
      Path.of(System.getProperty("java.io.tmpdir")).resolve("erd-cowork-workspace-escape-test");

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
   * One session whose tree cannot be listed (a subdirectory written by deepagent-service under a
   * different UID on the shared RWX volume) must not stop the rest of the nightly pass: a stream
   * walk surfaces that as an {@code UncheckedIOException}, which slips past a {@code catch
   * (IOException)} and aborts every remaining session.
   */
  @Test
  void purgeStaleSessions_unlistableSessionDirectory_stillPurgesOtherSessions() throws IOException {
    assumeTrue(FileSystems.getDefault().supportedFileAttributeViews().contains("posix"));
    ChatSession blockedSession = persistSession(Instant.now().minus(Duration.ofDays(400)));
    ChatSession purgeableSession = persistSession(Instant.now().minus(Duration.ofDays(400)));
    Path unlistableDir = workspaceSessionDir(blockedSession).resolve("results");
    Files.createDirectories(unlistableDir);
    Files.writeString(unlistableDir.resolve("q1.json"), "{}");
    Path purgeableDir = workspaceSessionDir(purgeableSession);
    Files.createDirectories(purgeableDir);
    Files.writeString(purgeableDir.resolve("dashboard.html"), "<html></html>");
    Files.setPosixFilePermissions(unlistableDir, PosixFilePermissions.fromString("---------"));
    try {
      // A test run as root can still list the directory, which would defeat the setup.
      assumeTrue(!Files.isReadable(unlistableDir));

      int purged =
          workspaceRetentionService.purgeStaleSessions(Instant.now().minus(Duration.ofDays(180)));

      assertThat(purged).isEqualTo(1);
      assertThat(Files.exists(purgeableDir)).isFalse();
    } finally {
      Files.setPosixFilePermissions(unlistableDir, PosixFilePermissions.fromString("rwx------"));
    }
  }

  /**
   * A symlinked {@code {userId}} directory escapes a lexical {@code startsWith} guard: the joined
   * path still reads as being under the workspace root, but a walk resolves the link and enumerates
   * the target, so the recursive delete lands outside the volume entirely.
   */
  @Test
  void purgeStaleSessions_userDirectorySymlinkedOutsideRoot_leavesLinkTargetIntact()
      throws IOException {
    ChatSession session =
        persistSessionAs(
            "escape-user", UUID.randomUUID().toString(), Instant.now().minus(Duration.ofDays(400)));
    Path outsideSessionDir = ESCAPE_ROOT.resolve("sessions").resolve(session.getId());
    Files.createDirectories(outsideSessionDir);
    Path outsideFile = outsideSessionDir.resolve("dashboard.html");
    Files.writeString(outsideFile, "<html></html>");
    Files.createDirectories(WORKSPACE_ROOT);
    Path userDirLink = WORKSPACE_ROOT.resolve("escape-user");
    Files.deleteIfExists(userDirLink);
    try {
      Files.createSymbolicLink(userDirLink, ESCAPE_ROOT);
    } catch (UnsupportedOperationException | IOException exception) {
      assumeTrue(false, "filesystem does not support symlinks: " + exception.getMessage());
    }
    try {
      int purged =
          workspaceRetentionService.purgeStaleSessions(Instant.now().minus(Duration.ofDays(180)));

      assertThat(purged).isZero();
      assertThat(Files.exists(outsideFile)).isTrue();
    } finally {
      Files.deleteIfExists(userDirLink);
      deleteTree(ESCAPE_ROOT);
    }
  }

  private static void deleteTree(Path root) throws IOException {
    if (!Files.exists(root)) {
      return;
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
    return persistSessionAs("workspace-user", id, updatedAt);
  }

  /** Same as {@link #persistSessionWithId}, but also lets the caller pick the owning userId. */
  ChatSession persistSessionAs(String userId, String id, Instant updatedAt) {
    ChatSession session = new ChatSession();
    session.setId(id);
    session.setUserId(userId);
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

  /**
   * Separate Spring context with {@code dry-run=true} -- dry-run is a config-time flag and cannot
   * be toggled within the context above (fixed to {@code dry-run=false} to assert real deletion).
   * {@code @NestedTestConfiguration(OVERRIDE)} is required here: Spring's default nested-test
   * inheritance mode is INHERIT, which would otherwise let the enclosing class's
   * {@code @TestPropertySource} (dry-run=false, a different workspace-dir) win over this class's
   * own {@code @SpringBootTest} properties instead of being replaced by them.
   *
   * <p>Regression test for the Task 4 {@code WorkspacePurger} extraction: the original inline
   * implementation ran the {@code toRealPath} symlink check before the dry-run branch, so a
   * symlink-escaping session was never counted as "would purge". After the extraction, {@code
   * LocalWorkspacePurger.sessionExists()} must run the same symlink check, or dry-run would
   * over-count a session it can never actually purge.
   */
  @Nested
  @NestedTestConfiguration(EnclosingConfiguration.OVERRIDE)
  @SpringBootTest(
      properties = {
        "erd.storage.workspace-dir=${java.io.tmpdir}/erd-cowork-workspace-dryrun-symlink-test",
        "erd.storage.cleanup.cron=-",
        "erd.storage.cleanup.dry-run=true"
      })
  class DryRunSymlinkEscape {

    @Autowired WorkspaceRetentionService dryRunWorkspaceRetentionService;
    @Autowired ChatSessionRepository dryRunSessionRepo;
    @Autowired ChatMessageRepository dryRunMessageRepo;
    @Autowired UploadedFileRepository dryRunFileRepo;
    @Autowired ArtifactRepository dryRunArtifactRepo;
    @Autowired EntityManager dryRunEntityManager;
    @Autowired PlatformTransactionManager dryRunTransactionManager;

    private static final Path DRY_RUN_WORKSPACE_ROOT =
        Path.of(System.getProperty("java.io.tmpdir"))
            .resolve("erd-cowork-workspace-dryrun-symlink-test");

    /** Sibling of, and deliberately outside, {@link #DRY_RUN_WORKSPACE_ROOT} -- escape target. */
    private static final Path DRY_RUN_ESCAPE_ROOT =
        Path.of(System.getProperty("java.io.tmpdir"))
            .resolve("erd-cowork-workspace-dryrun-symlink-escape-test");

    @BeforeEach
    void resetDb() {
      // Child rows first to respect FK constraints before deleting parent sessions.
      dryRunFileRepo.deleteAll();
      dryRunMessageRepo.deleteAll();
      dryRunArtifactRepo.deleteAll();
      dryRunSessionRepo.deleteAll();
    }

    @AfterAll
    static void cleanupWorkspaceDir() throws IOException {
      deleteTree(DRY_RUN_WORKSPACE_ROOT);
      deleteTree(DRY_RUN_ESCAPE_ROOT);
    }

    @Test
    void purgeStaleSessions_dryRunWithSymlinkEscape_doesNotCountAndLeavesLinkTargetIntact()
        throws IOException {
      ChatSession session = new ChatSession();
      session.setId(UUID.randomUUID().toString());
      session.setUserId("dryrun-escape-user");
      session.setTitle("dry run symlink escape session");
      dryRunSessionRepo.saveAndFlush(session);
      new TransactionTemplate(dryRunTransactionManager)
          .executeWithoutResult(
              status ->
                  dryRunEntityManager
                      .createNativeQuery("UPDATE chat_session SET updated_at = ?1 WHERE id = ?2")
                      .setParameter(1, Timestamp.from(Instant.now().minus(Duration.ofDays(400))))
                      .setParameter(2, session.getId())
                      .executeUpdate());

      Path outsideSessionDir = DRY_RUN_ESCAPE_ROOT.resolve("sessions").resolve(session.getId());
      Files.createDirectories(outsideSessionDir);
      Path outsideFile = outsideSessionDir.resolve("dashboard.html");
      Files.writeString(outsideFile, "<html></html>");
      Files.createDirectories(DRY_RUN_WORKSPACE_ROOT);
      Path userDirLink = DRY_RUN_WORKSPACE_ROOT.resolve(session.getUserId());
      Files.deleteIfExists(userDirLink);
      try {
        Files.createSymbolicLink(userDirLink, DRY_RUN_ESCAPE_ROOT);
      } catch (UnsupportedOperationException | IOException exception) {
        assumeTrue(false, "filesystem does not support symlinks: " + exception.getMessage());
      }
      try {
        int purged =
            dryRunWorkspaceRetentionService.purgeStaleSessions(
                Instant.now().minus(Duration.ofDays(180)));

        assertThat(purged).isZero();
        assertThat(Files.exists(outsideFile)).isTrue();
      } finally {
        Files.deleteIfExists(userDirLink);
        deleteTree(DRY_RUN_ESCAPE_ROOT);
      }
    }
  }
}
