package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.UploadNormalizer;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.web.dto.SessionMapper;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;

/**
 * Kept separate from {@link FileServiceUploadTest} on purpose: the decryption failure path returns
 * before storage, parsing, or mapping are ever touched, so a fixture stubbing those (as the
 * success-path tests need) trips Mockito's strict-stubbing check for this test. A minimal,
 * decryption-failure-only fixture avoids that without loosening strictness anywhere.
 */
@ExtendWith(MockitoExtension.class)
class FileServiceDecryptionFailureTest {

  @Mock SessionGuard sessionGuard;
  @Mock UploadedFileRepository files;
  @Mock FileStorage storage;
  @Mock FileParsingService parsing;
  @Mock UploadProperties limits;
  @Mock SessionMapper mapper;
  @Mock ChatSessionRepository sessionRepository;
  @Mock UploadNormalizer normalizer;

  @BeforeEach
  void setUp() {
    when(limits.maxFiles()).thenReturn(5);
    when(limits.maxSessionBytes()).thenReturn(5_000_000_000L);
    when(limits.maxXlsxBytes()).thenReturn(209_715_200L);

    when(files.findBySessionIdAndExpiredFalse(anyString())).thenReturn(List.of());
    when(files.findBySessionId(anyString())).thenReturn(List.of());
  }

  @Test
  void upload_decryptionFails_abortsAndLeavesNoStoredObject() throws Exception {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    FileService failingService =
        new FileService(
            sessionGuard,
            files,
            storage,
            parsing,
            limits,
            mapper,
            sessionRepository,
            (ciphertext, originalFilename) -> {
              throw new IOException("decryption API unavailable");
            },
            normalizer);

    // xlsx, not csv: only ENCRYPTED_UPLOAD_TYPES (xlsx) reaches the decryptor now, so a csv
    // fixture would never hit this failing decryptor at all.
    MockMultipartFile upload =
        new MockMultipartFile(
            "file",
            "sales.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ENC:col\n1\n".getBytes(StandardCharsets.UTF_8));

    assertThatThrownBy(() -> failingService.upload("session-1", List.of(upload)))
        .isInstanceOf(UncheckedIOException.class)
        .hasMessageContaining("sales.xlsx");

    // 解密在 store 之前就失敗，因此不該有任何物件被寫入，也就沒有東西需要清理。
    verify(storage, never()).store(any(), anyString(), anyString(), any());
    verify(files, never()).save(any(UploadedFile.class));
  }

  // upload_secondFileDecryptionFails_deletesFirstFilesStoredObject removed: asserted batch-level
  // storage compensation tied to the pre-migration atomicity story. 原子性測試移至 Branch 2/3。
}
