package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ArtifactRepository.ArtifactStorageKeyView;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import java.io.IOException;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

/**
 * Storage-failure behaviour of the artifact pass, which needs a storage that throws on demand and
 * therefore cannot reuse the Spring-wired sibling tests. The key invariant: the storage key is the
 * only pointer to the file on the PVC, so it may never be cleared unless the file is actually gone.
 */
@ExtendWith(MockitoExtension.class)
class RetentionCleanupDeleteFailureTest {

  @Mock ChatSessionRepository sessionRepo;
  @Mock UploadedFileRepository fileRepo;
  @Mock ArtifactRepository artifactRepo;
  @Mock FileStorage storage;
  @Mock WorkspaceRetentionService workspaceRetentionService;
  @Mock ArtifactStorageKeyView staleArtifact;

  private static final String ARTIFACT_ID = "artifact-1";
  private static final String ARTIFACT_KEY = "artifacts/session-1/uuid_dashboard.html";

  private RetentionCleanupService newService() {
    StorageProperties properties =
        new StorageProperties(
            "./data/files",
            "./data/workspace",
            new StorageProperties.Cleanup("-", false),
            new StorageProperties.Retention(
                Duration.ofDays(180), Duration.ofDays(180), Duration.ofDays(730)));
    return new RetentionCleanupService(
        sessionRepo, fileRepo, artifactRepo, storage, properties, workspaceRetentionService);
  }

  @Test
  void cleanupArtifacts_storageDeleteFails_keepsStorageKeyAndDoesNotCount() throws IOException {
    when(staleArtifact.getHtmlStorageKey()).thenReturn(ARTIFACT_KEY);
    when(artifactRepo.findStaleArtifactStorageKeys(any(Instant.class)))
        .thenReturn(List.of(staleArtifact));
    doThrow(new IOException("volume temporarily unavailable")).when(storage).delete(ARTIFACT_KEY);

    int purged = newService().cleanupArtifacts(Instant.now());

    // Clearing the key on a failed delete would orphan the file forever: nothing else on the
    // volume records where it lives, so no later pass -- automated or manual -- could find it.
    verify(artifactRepo, never()).clearHtmlStorageKey(anyString());
    assertThat(purged).isZero();
  }

  @Test
  void cleanupArtifacts_storageDeleteSucceeds_clearsStorageKeyAndCounts() throws IOException {
    when(staleArtifact.getId()).thenReturn(ARTIFACT_ID);
    when(staleArtifact.getHtmlStorageKey()).thenReturn(ARTIFACT_KEY);
    when(artifactRepo.findStaleArtifactStorageKeys(any(Instant.class)))
        .thenReturn(List.of(staleArtifact));

    int purged = newService().cleanupArtifacts(Instant.now());

    verify(storage).delete(ARTIFACT_KEY);
    verify(artifactRepo).clearHtmlStorageKey(ARTIFACT_ID);
    assertThat(purged).isEqualTo(1);
  }
}
