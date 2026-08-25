package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.when;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.NormalizedUpload;
import com.erd.cowork.parsing.UploadNormalizer;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import com.erd.cowork.web.dto.SessionMapper;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AccessDeniedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Pins the fix for a temp-file leak: {@code UploadNormalizer.normalize()} hands back a temp file
 * containing normalized user data, and {@link FileService#upload} MUST delete it unconditionally —
 * not only via {@code DELETE_ON_CLOSE}, which only fires if {@code content.close()} ever runs.
 *
 * <p>Only {@link #upload_normalizedTempFileCannotBeOpened_failsWithoutMaskingCause} actually pins
 * the bug: {@code Files.newInputStream} throwing during resource <em>acquisition</em> (before
 * {@code content} is ever bound) is the one path where {@code DELETE_ON_CLOSE} never gets a chance
 * to fire. Per JLS 14.20.3, try-with-resources closes an already-bound resource on the way out of
 * the try block, before any catch/finally runs — so a failure <em>after</em> acquisition (e.g.
 * {@link #upload_storageStoreFails_deletesNormalizerTempFile}) already had {@code DELETE_ON_CLOSE}
 * fire before this class's {@code finally}-based fix ever runs; that test verifies cleanup still
 * holds on that path, not that the {@code finally} is what causes it to hold there. Verified by
 * running these three tests against the pre-fix revision (commit {@code 187b169}): the
 * acquisition-failure test fails there (the temp file survives), the other two pass unchanged — so
 * only the first test is a genuine regression guard for this bug. Two reproduction approaches were
 * tried and rejected before landing on POSIX permissions: pre-deleting the temp file makes the
 * "still exists after" assertion trivially true regardless of the fix (nothing to clean up to begin
 * with), and an unreadable directory does not reproduce the bug on macOS — {@code
 * Files.newInputStream} on a directory opens without error there and only fails lazily on the first
 * {@code read()}, by which point {@code content} is already bound and gets closed normally.
 */
@ExtendWith(MockitoExtension.class)
class FileServiceNormalizerTempFileCleanupTest {

  @Mock SessionGuard sessionGuard;
  @Mock UploadedFileRepository files;
  @Mock FileStorage storage;
  @Mock FileParsingService parsing;
  @Mock UploadProperties limits;
  @Mock SessionMapper mapper;
  @Mock ChatSessionRepository sessionRepository;
  @Mock UploadNormalizer normalizer;
  @Mock TransactionTemplate transactionTemplate;

  FileService service;

  @BeforeEach
  void setUp() {
    service =
        new FileService(
            sessionGuard,
            files,
            storage,
            parsing,
            limits,
            mapper,
            sessionRepository,
            normalizer,
            transactionTemplate);

    // Stub the transaction boundary to just run the callback inline — these are unit tests
    // against mocked repositories, not real Mongo transactions (that's covered by
    // TransactionSmokeTest against the replica-set harness). lenient(): most tests in this class
    // fail during the IO phase, before the batch-save transaction is ever entered.
    lenient()
        .when(transactionTemplate.execute(any()))
        .thenAnswer(
            invocation -> {
              TransactionCallback<?> callback = invocation.getArgument(0);
              return callback.doInTransaction(new SimpleTransactionStatus());
            });

    when(limits.maxFiles()).thenReturn(5);
    when(limits.maxSessionBytes()).thenReturn(5_000_000_000L);
    when(limits.maxCsvBytes()).thenReturn(2_000_000_000L);

    when(files.findBySessionIdAndExpiredFalse(anyString())).thenReturn(List.of());
    when(files.findBySessionId(anyString())).thenReturn(List.of());
  }

  @Test
  void upload_success_deletesNormalizerTempFile() throws Exception {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    Path normalizedTempFile = Files.createTempFile("test-normalized-", ".csv");
    Files.writeString(normalizedTempFile, "col\n1\n");
    when(normalizer.normalize(any(), anyString()))
        .thenReturn(new NormalizedUpload(normalizedTempFile, "csv"));

    when(storage.store(eq(StorageCategory.UPLOAD), anyString(), anyString(), any()))
        .thenReturn("storage-key");
    when(storage.read(anyString()))
        .thenReturn(new ByteArrayInputStream("col\n1\n".getBytes(StandardCharsets.UTF_8)));
    when(parsing.profile(anyString(), any()))
        .thenReturn(new FileProfile(1, 1, List.of("col"), List.of(), List.of()));
    when(files.save(any(UploadedFile.class))).thenAnswer(invocation -> invocation.getArgument(0));

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "col\n1\n".getBytes(StandardCharsets.UTF_8));

    service.upload("session-1", List.of(upload));

    assertThat(Files.exists(normalizedTempFile)).isFalse();
  }

  /**
   * Exercises the post-acquisition failure path: {@code Files.newInputStream(...DELETE_ON_CLOSE)}
   * succeeds in opening the temp file, but {@code storage.store()} then throws. Per JLS 14.20.3,
   * the try-with-resources already closes {@code content} (firing {@code DELETE_ON_CLOSE}) on the
   * way out of the try block, before the {@code finally} added by this fix ever runs — so this test
   * does NOT pin the bug the fix addresses; it only confirms cleanup still holds on this path,
   * which was already true before the fix. See the class javadoc.
   */
  @Test
  void upload_storageStoreFails_deletesNormalizerTempFile() throws Exception {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    Path normalizedTempFile = Files.createTempFile("test-normalized-", ".csv");
    Files.writeString(normalizedTempFile, "col\n1\n");
    when(normalizer.normalize(any(), anyString()))
        .thenReturn(new NormalizedUpload(normalizedTempFile, "csv"));

    when(storage.store(eq(StorageCategory.UPLOAD), anyString(), anyString(), any()))
        .thenThrow(new IOException("storage unavailable"));

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "col\n1\n".getBytes(StandardCharsets.UTF_8));

    assertThatThrownBy(() -> service.upload("session-1", List.of(upload)))
        .hasMessageContaining("data.csv");

    assertThat(Files.exists(normalizedTempFile)).isFalse();
  }

  /**
   * Pins the actual bug: {@code Files.newInputStream} throws {@link AccessDeniedException} while
   * acquiring the resource, before {@code content} is ever bound to anything — the one path where
   * {@code DELETE_ON_CLOSE} never gets a chance to fire, since {@code close()} is never called on a
   * stream that was never opened. Stripping all POSIX permissions makes the open itself fail
   * synchronously (verified: unlike a directory, which on macOS opens successfully and only fails
   * on the first {@code read()} — too late to reproduce this bug), while leaving the file present
   * on disk beforehand, so the "still exists after" assertion actually exercises cleanup instead of
   * being trivially true.
   *
   * <p>Deletion of the file afterward relies on write permission on its <em>parent</em> directory
   * (standard POSIX unlink semantics), not on the file's own now-empty permission bits, so {@code
   * Files.deleteIfExists} in the fix can still remove it. Skipped when running as root, which
   * bypasses DAC permission checks entirely and would make the open succeed instead of failing —
   * defeating the reproduction.
   */
  @Test
  void upload_normalizedTempFileCannotBeOpened_failsWithoutMaskingCause() throws Exception {
    org.junit.jupiter.api.Assumptions.assumeTrue(
        !"root".equals(System.getProperty("user.name")),
        "POSIX permission checks are bypassed when running as root");

    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    Path unreadableTempFile = Files.createTempFile("test-normalized-", ".csv");
    Files.writeString(unreadableTempFile, "col\n1\n");
    Files.setPosixFilePermissions(unreadableTempFile, Set.of());
    when(normalizer.normalize(any(), anyString()))
        .thenReturn(new NormalizedUpload(unreadableTempFile, "csv"));

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "col\n1\n".getBytes(StandardCharsets.UTF_8));

    try {
      assertThatThrownBy(() -> service.upload("session-1", List.of(upload)))
          .isInstanceOf(UncheckedIOException.class)
          .hasMessageContaining("data.csv")
          .hasCauseInstanceOf(AccessDeniedException.class);

      assertThat(Files.exists(unreadableTempFile)).isFalse();
    } finally {
      // Best-effort: deletion needs write permission on the parent directory, not on the file
      // itself, so this succeeds regardless of whether the assertions above already removed it.
      Files.deleteIfExists(unreadableTempFile);
    }
  }
}
