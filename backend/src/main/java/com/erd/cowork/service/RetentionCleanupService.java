package com.erd.cowork.service;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import java.io.IOException;
import java.time.Duration;
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

  @Scheduled(cron = "${erd.storage.cleanup-cron:0 0 3 * * *}")
  public void scheduledCleanup() {
    Instant cutoff = Instant.now().minus(Duration.ofDays(properties.retentionDays()));
    int purged = cleanup(cutoff);
    log.info("Retention cleanup complete: purged {} file(s) with cutoff={}", purged, cutoff);
  }
}
