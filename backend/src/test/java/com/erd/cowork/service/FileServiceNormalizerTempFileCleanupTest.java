package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
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
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Pins the fix for a temp-file leak: {@code UploadNormalizer.normalize()} hands back a temp file
 * containing decrypted user data, and {@link FileService#upload} MUST delete it unconditionally —
 * not only via {@code DELETE_ON_CLOSE}, which never fires if the stream is never successfully
 * opened or if a later step in the same per-file block fails.
 *
 * <p>Kept separate from {@link FileServiceUploadTest} on purpose: each test here needs a different,
 * non-default {@code normalizer.normalize()} / {@code storage.store()} stub (a real temp file whose
 * survival is asserted, and a store failure, respectively), and re-stubbing those methods away from
 * a shared {@code @BeforeEach} default would make the default unreachable for that test — tripping
 * Mockito's strict-stubbing check. A minimal per-purpose fixture avoids that.
 */
@ExtendWith(MockitoExtension.class)
class FileServiceNormalizerTempFileCleanupTest {

  @Mock SessionGuard sessionGuard;
  @Mock UploadedFileRepository files;
  @Mock FileStorage storage;
  @Mock FileParsingService parsing;
  @Mock UploadProperties limits;
  @Mock SessionMapper mapper;
  @Mock TransactionTemplate transactionTemplate;
  @Mock ChatSessionRepository sessionRepository;
  @Mock UploadNormalizer normalizer;

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
            transactionTemplate,
            sessionRepository,
            (ciphertext, originalFilename) -> ciphertext,
            normalizer);

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
    when(transactionTemplate.execute(any()))
        .thenAnswer(
            invocation -> {
              TransactionCallback<?> callback = invocation.getArgument(0);
              return callback.doInTransaction(null);
            });

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "col\n1\n".getBytes(StandardCharsets.UTF_8));

    service.upload("session-1", List.of(upload));

    assertThat(Files.exists(normalizedTempFile)).isFalse();
  }

  /**
   * The realistic leak path: {@code Files.newInputStream(...DELETE_ON_CLOSE)} succeeds in opening
   * the temp file, but {@code storage.store()} then throws before the stream closes normally.
   * Without the unconditional {@code finally}-based cleanup, the temp file — holding decrypted user
   * data — would be orphaned in the JVM temp dir.
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
}
