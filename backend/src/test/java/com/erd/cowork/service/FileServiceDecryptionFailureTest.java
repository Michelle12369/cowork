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
import com.erd.cowork.parsing.NormalizedUpload;
import com.erd.cowork.parsing.UploadNormalizer;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.web.dto.SessionMapper;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.transaction.support.TransactionTemplate;

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
  @Mock TransactionTemplate transactionTemplate;

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
            normalizer,
            transactionTemplate);

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

  /**
   * NOT a transaction-rollback test: decryption for {@code fail.xlsx} throws inside the IO loop,
   * before {@code upload()} ever calls {@code transactionTemplate.execute(...)} — this class mocks
   * {@code transactionTemplate} and never stubs {@code execute()}, so a real DB save is never even
   * reachable here. What this test actually exercises is the pre-existing {@code catch
   * (RuntimeException) → storage.delete()} compensation for IO-phase failures (see the class
   * Javadoc note above {@code storedKeys} in {@code FileService.upload}). For a test that forces a
   * real mid-transaction DB failure and proves rollback, see {@code
   * UploadedFileTransactionRollbackTest}.
   */
  @Test
  void upload_secondFileDecryptionFailsBeforeAnyDbSave_compensatesFirstFilesStoredObject()
      throws Exception {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    // Only stubbed here (not in the shared fixture) so the single-file test above keeps its
    // minimal, strict-stub-clean surface — this path is the only one that ever reaches storage.
    // The first file's decrypted bytes do reach normalize() (only the second file's decryption
    // fails), so it must be stubbed too, or NormalizedUpload.type() below would NPE.
    when(normalizer.normalize(any(), anyString()))
        .thenAnswer(
            invocation -> {
              InputStream suppliedStream = invocation.getArgument(0);
              Path temporaryFile = Files.createTempFile("test-normalized-", ".csv");
              Files.copy(suppliedStream, temporaryFile, StandardCopyOption.REPLACE_EXISTING);
              return new NormalizedUpload(temporaryFile, "csv");
            });
    when(storage.store(any(), anyString(), anyString(), any())).thenReturn("storage-key-1");
    when(storage.read(anyString()))
        .thenReturn(new ByteArrayInputStream("col\n1\n".getBytes(StandardCharsets.UTF_8)));
    when(parsing.profile(anyString(), any()))
        .thenReturn(new FileProfile(1, 1, List.of("col"), List.of(), List.of()));

    FileService partiallyFailingService =
        new FileService(
            sessionGuard,
            files,
            storage,
            parsing,
            limits,
            mapper,
            sessionRepository,
            (ciphertext, originalFilename) -> {
              if ("fail.xlsx".equals(originalFilename)) {
                throw new IOException("decryption API unavailable");
              }
              return ciphertext;
            },
            normalizer,
            transactionTemplate);

    // xlsx, not csv: only ENCRYPTED_UPLOAD_TYPES (xlsx) reaches the decryptor now, so both
    // fixtures must be xlsx for the second file's decryption to fail at all.
    MockMultipartFile firstUpload =
        new MockMultipartFile(
            "file",
            "success.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "col\n1\n".getBytes(StandardCharsets.UTF_8));
    MockMultipartFile secondUpload =
        new MockMultipartFile(
            "file",
            "fail.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "col\n1\n".getBytes(StandardCharsets.UTF_8));

    assertThatThrownBy(
            () -> partiallyFailingService.upload("session-1", List.of(firstUpload, secondUpload)))
        .isInstanceOf(UncheckedIOException.class)
        .hasMessageContaining("fail.xlsx");

    // 第一個檔已成功 store，第二個檔解密失敗中止整批上傳——外層清理邏輯必須刪除第一個檔已落地的物件。
    verify(storage).delete("storage-key-1");
  }
}
