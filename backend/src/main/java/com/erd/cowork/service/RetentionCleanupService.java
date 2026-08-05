package com.erd.cowork.service;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ArtifactRepository.ArtifactStorageKeyView;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import jakarta.annotation.PostConstruct;
import java.io.IOException;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.function.Consumer;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
@Slf4j
public class RetentionCleanupService {

  private final ChatSessionRepository sessionRepo;
  private final UploadedFileRepository fileRepo;
  private final ArtifactRepository artifactRepo;
  private final FileStorage storage;
  private final StorageProperties properties;
  private final WorkspaceRetentionService workspaceRetentionService;

  /**
   * Prints the retention policy actually in force so an operator can confirm it from the logs
   * instead of reconstructing it from env vars, and flags the one combination that silently breaks
   * follow-up questions: a workspace window shorter than the uploads window wipes the agent's
   * working copy while the source files are still live, so {@code FilesExpiredException} never
   * fires and the next turn starts from an empty workspace. Warn rather than fail fast -- the
   * effect is degraded continuation of idle sessions, not data loss or an unsafe state, and
   * refusing to boot over a tuning knob would turn that into an outage.
   */
  @PostConstruct
  void logRetentionPolicy() {
    log.info(
        "Retention policy: uploads={} workspace={} artifact={} cron='{}' dryRun={}",
        inDays(properties.retention().uploads()),
        inDays(properties.retention().workspace()),
        inDays(properties.retention().artifact()),
        properties.cleanup().cron(),
        properties.cleanup().dryRun());
    if (properties.retention().workspace().compareTo(properties.retention().uploads()) < 0) {
      log.warn(
          "Retention misconfigured: workspace={} is shorter than uploads={}. Idle sessions will"
              + " lose their agent workspace while their source files are still readable, so a"
              + " follow-up question restarts from an empty workspace instead of being blocked.",
          inDays(properties.retention().workspace()),
          inDays(properties.retention().uploads()));
    }
  }

  /** Renders a window the way it is configured ({@code 180d}) rather than as ISO-8601 hours. */
  private String inDays(Duration window) {
    return window.toDays() + "d";
  }

  /**
   * Cleanup is an incremental operation; each fileRepo.save carries its own transaction, which is
   * the correct semantic. @Transactional is intentionally absent: the scheduled self-invocation via
   * scheduledCleanup() never goes through the Spring proxy, so wrapping here would be a no-op and
   * misleading.
   */
  public int cleanup(Instant cutoff) {
    List<ChatSession> staleSessions = sessionRepo.findByUpdatedAtBefore(cutoff);
    int count = 0;
    for (ChatSession session : staleSessions) {
      List<UploadedFile> files = fileRepo.findBySessionIdAndExpiredFalse(session.getId());
      for (UploadedFile file : files) {
        if (properties.cleanup().dryRun()) {
          log.info("[dry-run] would purge upload key={}", file.getStorageKey());
          count++;
          continue;
        }
        try {
          storage.delete(file.getStorageKey());
        } catch (IOException exception) {
          log.warn(
              "Failed to delete storage key={}: {}",
              file.getStorageKey(),
              exception.getMessage(),
              exception);
        }
        file.setExpired(true);
        fileRepo.save(file);
        count++;
      }
    }
    return count;
  }

  /**
   * Deletes artifact HTML files (assembled and raw) older than {@code cutoff} and clears their
   * storage keys. The artifact row itself is kept -- chat messages reference artifacts by id, and
   * ArtifactService returns 404 for a null storage key, matching pre-V6 rows. Uses a narrow id/key
   * projection (never the full entity) and targeted column updates, so the unbounded {@code
   * rawHtml} CLOB is never loaded or rewritten by this pass.
   *
   * <p>The two keys are independent: each is cleared only after its own file is confirmed gone,
   * since it is the sole pointer to that file on the volume and clearing it on a failed delete
   * would leave an orphan nothing can find. A failed key is left untouched and simply retried by
   * the next run, regardless of whether the other key on the same row succeeded. A dry run logs
   * both keys without deleting or clearing either.
   */
  public int cleanupArtifacts(Instant cutoff) {
    List<ArtifactStorageKeyView> staleArtifacts = artifactRepo.findStaleArtifactStorageKeys(cutoff);
    int count = 0;
    for (ArtifactStorageKeyView artifact : staleArtifacts) {
      if (properties.cleanup().dryRun()) {
        log.info(
            "[dry-run] would purge artifact id={} htmlKey={} rawKey={}",
            artifact.getId(),
            artifact.getHtmlStorageKey(),
            artifact.getRawHtmlStorageKey());
        count++;
        continue;
      }
      boolean purgedAny = false;
      if (artifact.getHtmlStorageKey() != null
          && deleteAndClear(
              artifact.getHtmlStorageKey(), artifact.getId(), artifactRepo::clearHtmlStorageKey)) {
        purgedAny = true;
      }
      if (artifact.getRawHtmlStorageKey() != null
          && deleteAndClear(
              artifact.getRawHtmlStorageKey(),
              artifact.getId(),
              artifactRepo::clearRawHtmlStorageKey)) {
        purgedAny = true;
      }
      if (purgedAny) {
        count++;
      }
    }
    return count;
  }

  /**
   * Deletes one storage file and clears its column only after the delete succeeded -- the key is
   * the sole pointer to the file, so clearing on failure would orphan it. Returns true on success.
   */
  private boolean deleteAndClear(
      String storageKey, String artifactId, Consumer<String> clearColumn) {
    try {
      storage.delete(storageKey);
    } catch (IOException exception) {
      log.warn(
          "Failed to delete artifact storage key={}, keeping key for retry: {}",
          storageKey,
          exception.getMessage(),
          exception);
      return false;
    }
    clearColumn.accept(artifactId);
    return true;
  }

  @Scheduled(cron = "${erd.storage.cleanup.cron}")
  public void scheduledCleanup() {
    Instant now = Instant.now();
    int uploadsPurged = cleanup(now.minus(properties.retention().uploads()));
    int artifactsPurged = cleanupArtifacts(now.minus(properties.retention().artifact()));
    int workspacesPurged =
        workspaceRetentionService.purgeStaleSessions(now.minus(properties.retention().workspace()));
    log.info(
        "Retention cleanup complete: uploads={} artifacts={} workspaces={} dryRun={}",
        uploadsPurged,
        artifactsPurged,
        workspacesPurged,
        properties.cleanup().dryRun());
  }
}
