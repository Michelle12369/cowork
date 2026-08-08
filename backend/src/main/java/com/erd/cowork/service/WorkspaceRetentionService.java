package com.erd.cowork.service;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.storage.WorkspacePurger;
import java.io.IOException;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

/**
 * Removes deepagent workspace directories for sessions that have been idle past the retention
 * window. Runs on the backend because the cutoff is driven by {@code chat_session.updated_at},
 * which lives in the backend database; the actual deletion is delegated to a {@link
 * WorkspacePurger} (local RWX volume or S3, selected via {@code erd.storage.type}).
 */
@Service
@RequiredArgsConstructor
@Slf4j
@LogAnnotation
public class WorkspaceRetentionService {

  private final ChatSessionRepository sessionRepo;
  private final StorageProperties properties;
  private final WorkspacePurger workspacePurger;

  public int purgeStaleSessions(Instant cutoff) {
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
            "Skipping workspace purge for malformed session userId={} sessionId={}",
            session.getUserId(),
            session.getId());
        continue;
      }
      try {
        if (!workspacePurger.sessionExists(session.getUserId(), session.getId())) {
          continue;
        }
        if (properties.cleanup().dryRun()) {
          log.info(
              "[dry-run] would purge workspace session userId={} sessionId={}",
              session.getUserId(),
              session.getId());
          count++;
          continue;
        }
        // One unreadable directory -- a subtree written by deepagent-service under a different
        // UID on the shared volume -- must never abort the rest of the pass, so runtime failures
        // are caught alongside IOException and the loop moves on to the next session.
        if (workspacePurger.purgeSession(session.getUserId(), session.getId())) {
          count++;
        }
      } catch (IOException | RuntimeException exception) {
        log.warn(
            "Failed to purge workspace session userId={} sessionId={}: {}",
            session.getUserId(),
            session.getId(),
            exception.getMessage(),
            exception);
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
}
