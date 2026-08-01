package com.erd.cowork.service;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import java.io.IOException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

/**
 * Removes deepagent workspace directories for sessions that have been idle past the retention
 * window. Runs on the backend because the cutoff is driven by {@code chat_session.updated_at},
 * which lives in the backend database; the shared RWX volume makes the files reachable from here.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class WorkspaceRetentionService {

  private final ChatSessionRepository sessionRepo;
  private final StorageProperties properties;

  public int purgeStaleSessions(Instant cutoff) {
    Path workspaceRoot = Paths.get(properties.workspaceDir()).toAbsolutePath().normalize();
    Path realWorkspaceRoot;
    try {
      realWorkspaceRoot = workspaceRoot.toRealPath();
    } catch (IOException exception) {
      log.info(
          "Workspace root {} cannot be resolved ({}), nothing to purge",
          workspaceRoot,
          exception.getMessage());
      return 0;
    }
    List<ChatSession> staleSessions = sessionRepo.findByUpdatedAtBefore(cutoff);
    int count = 0;
    for (ChatSession session : staleSessions) {
      // userId/sessionId are DB-sourced but MUST each be a single, non-empty, non-dot path
      // segment before they are ever joined into a filesystem path -- rejecting "..", "", and
      // multi-segment values here (rather than only checking the joined result) is what stops a
      // malformed row from resolving to a parent directory, "sessions", or the workspace root
      // itself, all of which would otherwise satisfy a plain startsWith(workspaceRoot) check.
      if (!isSinglePathSegment(session.getUserId()) || !isSinglePathSegment(session.getId())) {
        log.warn(
            "Skipping workspace path for malformed session userId={} sessionId={}",
            session.getUserId(),
            session.getId());
        continue;
      }
      Path sessionDir =
          workspaceRoot
              .resolve(session.getUserId())
              .resolve("sessions")
              .resolve(session.getId())
              .normalize();
      // Belt-and-suspenders: confirms the resolved path still lands inside the workspace root.
      if (!sessionDir.startsWith(workspaceRoot)) {
        log.warn("Skipping workspace path outside root: {}", sessionDir);
        continue;
      }
      if (!Files.isDirectory(sessionDir)) {
        continue;
      }
      // startsWith is a lexical check and cannot see symlinks: a symlinked {userId} component
      // still reads as being under the root while resolving elsewhere, which would put the
      // recursive delete outside the volume. toRealPath resolves every component before deciding.
      try {
        if (!sessionDir.toRealPath().startsWith(realWorkspaceRoot)) {
          log.warn("Skipping workspace path resolving outside root: {}", sessionDir);
          continue;
        }
      } catch (IOException exception) {
        log.warn(
            "Failed to resolve workspace dir={}: {}",
            sessionDir,
            exception.getMessage(),
            exception);
        continue;
      }
      if (properties.cleanup().dryRun()) {
        log.info("[dry-run] would purge workspace dir={}", sessionDir);
        count++;
        continue;
      }
      // One unreadable directory -- a subtree written by deepagent-service under a different UID
      // on the shared volume -- must never abort the rest of the pass, so runtime failures are
      // caught alongside IOException and the loop moves on to the next session.
      try {
        deleteRecursively(sessionDir);
        count++;
      } catch (IOException | RuntimeException exception) {
        log.warn(
            "Failed to delete workspace dir={}: {}", sessionDir, exception.getMessage(), exception);
      }
    }
    return count;
  }

  /**
   * True only if {@code value} is exactly one path component with no {@code .}/{@code ..} traversal
   * -- e.g. rejects {@code ""}, {@code ".."}, {@code "../.."}, and anything containing a path
   * separator. Validating before {@link Path#resolve} (rather than only after) also means a value
   * with an embedded NUL byte is rejected here as an {@link InvalidPathException} instead of
   * surfacing later from {@code resolve}.
   */
  private boolean isSinglePathSegment(String value) {
    if (!StringUtils.hasText(value)) {
      return false;
    }
    if (".".equals(value) || "..".equals(value)) {
      return false;
    }
    try {
      Path segment = Path.of(value);
      return !segment.isAbsolute()
          && segment.getNameCount() == 1
          && segment.toString().equals(value);
    } catch (InvalidPathException exception) {
      return false;
    }
  }

  /**
   * Deletes the tree children-before-parents without following symbolic links: a symlinked entry is
   * unlinked, never followed into its target. Walking (rather than streaming) keeps failures as
   * checked {@link IOException}s, so an unreadable subtree stays a per-session failure instead of
   * an {@code UncheckedIOException} that unwinds the whole pass.
   */
  private void deleteRecursively(Path directory) throws IOException {
    Files.walkFileTree(
        directory,
        Set.of(),
        Integer.MAX_VALUE,
        new SimpleFileVisitor<>() {
          @Override
          public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
              throws IOException {
            Files.deleteIfExists(file);
            return FileVisitResult.CONTINUE;
          }

          @Override
          public FileVisitResult postVisitDirectory(Path visitedDirectory, IOException failure)
              throws IOException {
            if (failure != null) {
              throw failure;
            }
            Files.deleteIfExists(visitedDirectory);
            return FileVisitResult.CONTINUE;
          }
        });
  }
}
