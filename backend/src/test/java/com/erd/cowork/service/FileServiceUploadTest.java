package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
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
import com.erd.cowork.storage.StorageCategory;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SessionMapper;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;

@ExtendWith(MockitoExtension.class)
class FileServiceUploadTest {

  @Mock SessionGuard sessionGuard;
  @Mock UploadedFileRepository files;
  @Mock FileStorage storage;
  @Mock FileParsingService parsing;
  @Mock UploadProperties limits;
  @Mock SessionMapper mapper;
  @Mock TransactionTemplate transactionTemplate;
  @Mock ChatSessionRepository sessionRepository;
  @Mock UploadNormalizer normalizer;

  /** Captures what FileService actually handed to storage, so tests can assert on the bytes. */
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
            transactionTemplate,
            sessionRepository,
            normalizer);

    // Make TransactionTemplate execute the callback synchronously (no real transaction manager).
    when(transactionTemplate.execute(any()))
        .thenAnswer(
            invocation -> {
              TransactionCallback<?> callback = invocation.getArgument(0);
              return callback.doInTransaction(null);
            });

    when(limits.maxFiles()).thenReturn(5);
    when(limits.maxSessionBytes()).thenReturn(5_000_000_000L);
    // validate() reads exactly one of these two per upload, branching on the uploaded extension
    // (csv vs xlsx) — no single test exercises both, so strict per-test stub-usage checking would
    // flag whichever one that test's file type didn't touch. lenient() opts these two out of that
    // check while keeping every other stub in this fixture strict.
    lenient().when(limits.maxCsvBytes()).thenReturn(2_000_000_000L);
    lenient().when(limits.maxXlsxBytes()).thenReturn(209_715_200L);

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
        .thenReturn(new FileDto("file-1", "data.csv", "file1", 6L, "csv", 1L, false));
  }

  /**
   * Regression test for the same bug class fixed in AgentOrchestrator: a session that only receives
   * an upload (no question asked yet) must still be recorded as active, so retention's "last
   * activity" cutoff does not treat it as stale. This asserts the field actually advances (not just
   * that save() was invoked), matching the AgentOrchestrator regression test's approach.
   */
  @Test
  void upload_success_advancesSessionUpdatedAt() {
    Instant staleTimestamp = Instant.now().minus(Duration.ofDays(10));
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    session.setUpdatedAt(staleTimestamp);
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "col\n1\n".getBytes(StandardCharsets.UTF_8));

    service.upload("session-1", List.of(upload));

    assertThat(session.getUpdatedAt()).isAfter(staleTimestamp);
    verify(sessionRepository).save(session);
  }

  @Test
  void upload_xlsxUpload_recordsCsvTypeNotTheUploadedExtension() {
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

    service.upload("session-1", List.of(upload));

    ArgumentCaptor<UploadedFile> savedEntity = ArgumentCaptor.forClass(UploadedFile.class);
    verify(files).save(savedEntity.capture());
    // type is the on-disk format; the original extension survives only in name.
    assertThat(savedEntity.getValue().getType()).isEqualTo("csv");
    assertThat(savedEntity.getValue().getName()).isEqualTo("sales.xlsx");
  }

}
