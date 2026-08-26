package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
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
import org.springframework.transaction.support.SimpleTransactionStatus;
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
  @Mock ChatSessionRepository sessionRepository;
  @Mock UploadNormalizer normalizer;
  @Mock TransactionTemplate transactionTemplate;

  /** Captures what FileService actually handed to storage, so tests can assert on the bytes. */
  byte[] storedContent;

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
            normalizer,
            transactionTemplate);

    // Stub the transaction boundary to just run the callback inline — these are unit tests
    // against mocked repositories, not real Mongo transactions (that's covered by
    // TransactionSmokeTest against the replica-set harness).
    when(transactionTemplate.execute(any()))
        .thenAnswer(
            invocation -> {
              TransactionCallback<?> callback = invocation.getArgument(0);
              return callback.doInTransaction(new SimpleTransactionStatus());
            });

    when(limits.maxFiles()).thenReturn(5);
    when(limits.maxSessionBytes()).thenReturn(5_000_000_000L);
    // validate() reads exactly one of these two per upload, branching on the uploaded extension
    // (csv vs xlsx) — no single test exercises both, so strict per-test stub-usage checking would
    // flag whichever one that test's file type didn't touch. lenient() opts these two out of that
    // check while keeping every other stub in this fixture strict.
    lenient().when(limits.maxCsvBytes()).thenReturn(2_000_000_000L);
    lenient().when(limits.maxXlsxBytes()).thenReturn(209_715_200L);

    lenient()
        .when(normalizer.normalize(any(), anyString()))
        .thenAnswer(
            invocation -> {
              InputStream suppliedStream = invocation.getArgument(0);
              Path temporaryFile = Files.createTempFile("test-normalized-", ".csv");
              Files.copy(suppliedStream, temporaryFile, StandardCopyOption.REPLACE_EXISTING);
              return new NormalizedUpload(temporaryFile, "csv");
            });

    when(files.findBySessionIdAndExpiredFalse(anyString())).thenReturn(List.of());
    when(files.findBySessionId(anyString())).thenReturn(List.of());
    lenient()
        .when(files.save(any(UploadedFile.class)))
        .thenAnswer(invocation -> invocation.getArgument(0));

    lenient()
        .when(storage.store(eq(StorageCategory.UPLOAD), anyString(), anyString(), any()))
        .thenAnswer(
            invocation -> {
              String originalFilename = invocation.getArgument(2);
              InputStream suppliedStream = invocation.getArgument(3);
              storedContent = suppliedStream.readAllBytes();
              // Real StorageKeyUtils.buildKey preserves the uploaded filename's extension
              // (case included) into the storage key — the Python source_cache detection
              // contract depends on that suffix surviving, so the stub mirrors it here
              // instead of returning an extension-less literal.
              int dotIndex = originalFilename.lastIndexOf('.');
              String extension = dotIndex < 0 ? "" : originalFilename.substring(dotIndex);
              return "storage-key" + extension;
            });
    lenient()
        .when(storage.read(anyString()))
        .thenReturn(new ByteArrayInputStream("col\n1\n".getBytes(StandardCharsets.UTF_8)));

    lenient()
        .when(parsing.profile(anyString(), any()))
        .thenReturn(new FileProfile(1, 1, List.of("col"), List.of(), List.of()));
    lenient().when(parsing.toJson(any())).thenReturn("{}");

    lenient()
        .when(mapper.toFileDto(any(UploadedFile.class)))
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

  /** Existing csv behavior, unchanged by the xlsx raw-store split: profile still gets computed. */
  @Test
  void upload_csvFile_normalizeAndProfileUnchanged() throws Exception {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "col\n1\n".getBytes(StandardCharsets.UTF_8));

    service.upload("session-1", List.of(upload));

    ArgumentCaptor<UploadedFile> savedEntity = ArgumentCaptor.forClass(UploadedFile.class);
    verify(files).save(savedEntity.capture());
    UploadedFile entity = savedEntity.getValue();
    assertThat(entity.getType()).isEqualTo("csv");
    assertThat(entity.getRowCount()).isEqualTo(1L);
    assertThat(entity.getMetadataJson()).isEqualTo("{}");
    verify(normalizer).normalize(any(), eq("data.csv"));
    verify(parsing).profile(eq("csv"), any());
  }

  /**
   * xlsx now lands byte-identical: no decryption, no xlsx-to-csv conversion happen on the Java side
   * any more — deepagent does both at download time.
   */
  @Test
  void upload_xlsxFile_storedVerbatimWithoutProfile() {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    byte[] fakeXlsxBytes =
        "not really an xlsx, just arbitrary bytes".getBytes(StandardCharsets.UTF_8);
    MockMultipartFile upload =
        new MockMultipartFile(
            "file",
            "sales.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            fakeXlsxBytes);

    service.upload("session-1", List.of(upload));

    assertThat(storedContent).isEqualTo(fakeXlsxBytes);

    ArgumentCaptor<UploadedFile> savedEntity = ArgumentCaptor.forClass(UploadedFile.class);
    verify(files).save(savedEntity.capture());
    UploadedFile entity = savedEntity.getValue();
    assertThat(entity.getType()).isEqualTo("xlsx");
    assertThat(entity.getStorageKey()).isNotNull();
    // Python source_cache 端靠 storageKey 的副檔名判斷是否需要解密→轉檔管線；
    // 這條斷言釘住「副檔名存活進 storageKey」這個跨語言契約不被悄悄改掉。
    assertThat(entity.getStorageKey()).endsWith(".xlsx");
    assertThat(entity.getRowCount()).isNull();
    assertThat(entity.getMetadataJson()).isNull();
  }

  /** xlsx bypasses both collaborators entirely — they exist only for the csv path now. */
  @Test
  void upload_xlsxFile_neverInvokesNormalizerOrParsing() {
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

    verifyNoInteractions(normalizer);
    verify(parsing, never()).profile(anyString(), any());
    verify(parsing, never()).toJson(any());
  }
}
