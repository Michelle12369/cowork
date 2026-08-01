package com.erd.cowork.service;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.repo.ChatSessionRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

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
      Path sessionDir =
          workspaceRoot
              .resolve(session.getUserId())
              .resolve("sessions")
              .resolve(session.getId())
              .normalize();
      // userId and sessionId come from the database, but the join is still verified so a
      // malformed row can never reach outside the workspace root.
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

  private void deleteRecursively(Path directory) throws IOException {
    try (Stream<Path> paths = Files.walk(directory)) {
      List<Path> ordered = paths.sorted(Comparator.reverseOrder()).toList();
      for (Path path : ordered) {
        Files.deleteIfExists(path);
      }
    }
  }
}
