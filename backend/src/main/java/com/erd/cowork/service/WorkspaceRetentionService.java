package com.erd.cowork.service;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;
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
      if (properties.cleanup().dryRun()) {
        log.info("[dry-run] would purge workspace dir={}", sessionDir);
        count++;
        continue;
      }
      try {
        deleteRecursively(sessionDir);
        count++;
      } catch (IOException exception) {
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

  private void deleteRecursively(Path directory) throws IOException {
    try (Stream<Path> paths = Files.walk(directory)) {
      List<Path> ordered = paths.sorted(Comparator.reverseOrder()).toList();
      for (Path path : ordered) {
        Files.deleteIfExists(path);
      }
    }
  }
}
