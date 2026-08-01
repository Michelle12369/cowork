package com.erd.cowork.service;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import java.io.IOException;
import java.time.Instant;
import java.util.List;
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
   * Deletes artifact HTML files older than {@code cutoff} and clears their storage key. The
   * artifact row itself is kept -- chat messages reference artifacts by id, and ArtifactService
   * returns 404 for a null storage key, matching pre-V6 rows.
   */
  public int cleanupArtifacts(Instant cutoff) {
    List<Artifact> staleArtifacts =
        artifactRepo.findByCreatedAtBeforeAndHtmlStorageKeyIsNotNull(cutoff);
    int count = 0;
    for (Artifact artifact : staleArtifacts) {
      String storageKey = artifact.getHtmlStorageKey();
      if (properties.cleanup().dryRun()) {
        log.info("[dry-run] would purge artifact id={} key={}", artifact.getId(), storageKey);
        count++;
        continue;
      }
      try {
        storage.delete(storageKey);
      } catch (IOException exception) {
        log.warn(
            "Failed to delete artifact storage key={}: {}",
            storageKey,
            exception.getMessage(),
            exception);
      }
      artifact.setHtmlStorageKey(null);
      artifactRepo.save(artifact);
      count++;
    }
    return count;
  }

  @Scheduled(cron = "${erd.storage.cleanup.cron}")
  public void scheduledCleanup() {
    Instant now = Instant.now();
    int uploadsPurged = cleanup(now.minus(properties.retention().uploads()));
    int artifactsPurged = cleanupArtifacts(now.minus(properties.retention().artifact()));
    log.info(
        "Retention cleanup complete: uploads={} artifacts={} dryRun={}",
        uploadsPurged,
        artifactsPurged,
        properties.cleanup().dryRun());
  }
}
