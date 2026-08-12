package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
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
import com.erd.cowork.storage.PassthroughUploadDecryptor;
import com.erd.cowork.storage.StorageCategory;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SessionMapper;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
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
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Kept separate from {@link FileServiceUploadTest} on purpose: that class installs a strip-prefix
 * fake {@code UploadDecryptor} for every test, so the production default's aliasing (decrypt
 * returns the same stream instance it was given, which then gets closed multiple times by the
 * caller's try-with-resources) is never exercised at unit level. This class wires the real {@link
 * PassthroughUploadDecryptor} bean through {@link FileService#upload} instead.
 */
@ExtendWith(MockitoExtension.class)
class FileServicePassthroughDecryptorTest {

  @Mock SessionGuard sessionGuard;
  @Mock UploadedFileRepository files;
  @Mock FileStorage storage;
  @Mock FileParsingService parsing;
  @Mock UploadProperties limits;
  @Mock SessionMapper mapper;
  @Mock ChatSessionRepository sessionRepository;
  @Mock UploadNormalizer normalizer;
  @Mock TransactionTemplate transactionTemplate;

  /** Captures what FileService actually handed to storage, so the test can assert on the bytes. */
  String storedContent;

  FileService service;

  @BeforeEach
  void setUp() throws Exception {
    service =
        new FileService(
            sessionGuard,
            files,
            storage,
            parsing,
            limits,
            mapper,
            sessionRepository,
            new PassthroughUploadDecryptor(),
            normalizer,
            transactionTemplate);

    // Stub the transaction boundary to just run the callback inline — this is a unit test
    // against mocked repositories, not a real Mongo transaction (that's covered by
    // TransactionSmokeTest against the replica-set harness).
    when(transactionTemplate.execute(any()))
        .thenAnswer(
            invocation -> {
              TransactionCallback<?> callback = invocation.getArgument(0);
              return callback.doInTransaction(new SimpleTransactionStatus());
            });

    when(limits.maxFiles()).thenReturn(5);
    when(limits.maxSessionBytes()).thenReturn(5_000_000_000L);
    when(limits.maxXlsxBytes()).thenReturn(209_715_200L);

    when(normalizer.normalize(any(), anyString()))
        .thenAnswer(
            invocation -> {
              InputStream suppliedStream = invocation.getArgument(0);
              Path temporaryFile = Files.createTempFile("test-normalized-", ".csv");
              Files.copy(suppliedStream, temporaryFile, StandardCopyOption.REPLACE_EXISTING);
              return new NormalizedUpload(temporaryFile, "csv");
            });

    when(files.findBySessionIdAndExpiredFalse(anyString())).thenReturn(List.of());
    when(files.findBySessionId(anyString())).thenReturn(List.of());
    when(files.save(any(UploadedFile.class))).thenAnswer(invocation -> invocation.getArgument(0));

    when(storage.store(eq(StorageCategory.UPLOAD), anyString(), anyString(), any()))
        .thenAnswer(
            invocation -> {
              InputStream suppliedStream = invocation.getArgument(3);
              storedContent = new String(suppliedStream.readAllBytes(), StandardCharsets.UTF_8);
              return "storage-key";
            });
    when(storage.read(anyString()))
        .thenReturn(new ByteArrayInputStream("col\n1\n".getBytes(StandardCharsets.UTF_8)));

    when(parsing.profile(anyString(), any()))
        .thenReturn(new FileProfile(1, 1, List.of("col"), List.of(), List.of()));
    when(parsing.toJson(any())).thenReturn("{}");

    when(mapper.toFileDto(any(UploadedFile.class)))
        .thenReturn(new FileDto("file-1", "sales.xlsx", "file1", 6L, "csv", 1L, false));
  }

  /**
   * xlsx, not csv: {@link FileService} now only routes ENCRYPTED_UPLOAD_TYPES (xlsx) through the
   * decryptor at all, so an xlsx fixture is required for this class's real {@link
   * PassthroughUploadDecryptor} to actually be invoked — a csv fixture would bypass it entirely and
   * this test would no longer exercise what it is named for.
   */
  @Test
  void upload_realPassthroughDecryptor_storesContentUnchanged() {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    MockMultipartFile upload =
        new MockMultipartFile(
            "file",
            "sales.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "col\n1\n".getBytes(StandardCharsets.UTF_8));

    List<FileDto> result = service.upload("session-1", List.of(upload));

    assertThat(result).hasSize(1);
    assertThat(storedContent).isEqualTo("col\n1\n");
  }
}
