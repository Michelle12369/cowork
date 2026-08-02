package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.parsing.CsvParsingService;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.UploadNormalizer;
import com.erd.cowork.parsing.XlsxParsingService;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.PassthroughUploadDecryptor;
import com.erd.cowork.storage.StorageCategory;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SessionMapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Drives a genuine xlsx upload through {@link FileService#upload} with the real parsing stack wired
 * in — real {@link FileParsingService}, real {@link CsvParsingService}, real {@link
 * XlsxParsingService}, real {@link UploadNormalizer} — rather than the mocked {@code
 * FileParsingService} every other {@code FileService*Test} class uses.
 *
 * <p>This is the gap that let a real bug ship: {@code FileService} was routing {@code
 * parsing.profile(...)} off the <em>uploaded</em> filename (e.g. {@code "sales.xlsx"}) while
 * feeding it the <em>normalized</em> bytes (CSV, since xlsx is always converted to CSV before
 * storage) — so {@code FileParsingService} dispatched to {@code XlsxParsingService}, which then
 * tried to open CSV bytes as an OOXML zip and threw. Every unit test elsewhere mocks {@code
 * parsing}, so none of them could catch a dispatch-routing bug inside the real implementation; the
 * one test that used a real {@code .xlsx} fixture fed garbage bytes that failed even earlier, at
 * normalize. Only a full-stack, real-parsing round trip like this one exercises the actual {@code
 * FileParsingService.profile(fileType, ...)} call with fileType/bytes that are consistent with each
 * other exactly the way production data is.
 *
 * <p>Kept as its own class (not folded into {@link FileServiceUploadTest}) because it deliberately
 * does NOT mock {@code FileParsingService}, {@code UploadNormalizer}, or their transitive {@code
 * CsvParsingService}/{@code XlsxParsingService} collaborators — mixing that with the other class's
 * fully-mocked fixture would be confusing and is unrelated to why each exists.
 */
@ExtendWith(MockitoExtension.class)
class FileServiceXlsxRealParsingTest {

  @Mock SessionGuard sessionGuard;
  @Mock UploadedFileRepository files;
  @Mock FileStorage storage;
  @Mock SessionMapper mapper;
  @Mock TransactionTemplate transactionTemplate;
  @Mock ChatSessionRepository sessionRepository;

  private final UploadProperties limits =
      new UploadProperties(5, 5_368_709_120L, 2_147_483_648L, 209_715_200L, 20);
  private final FileParsingService parsing =
      new FileParsingService(
          new CsvParsingService(limits), new XlsxParsingService(limits), new ObjectMapper());
  private final UploadNormalizer normalizer = new UploadNormalizer(limits);

  /**
   * Plays the role of a real object store: {@code storage.store()} actually reads and retains the
   * bytes FileService hands it (the normalized CSV, not the original xlsx), and {@code
   * storage.read()} plays them back — so the later {@code parsing.profile(storedType, ...)} call
   * genuinely parses what was written, the same as production.
   */
  private final Map<String, byte[]> storedObjects = new HashMap<>();

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
            new PassthroughUploadDecryptor(),
            normalizer);

    when(transactionTemplate.execute(any()))
        .thenAnswer(
            invocation -> {
              TransactionCallback<?> callback = invocation.getArgument(0);
              return callback.doInTransaction(null);
            });

    when(files.findBySessionIdAndExpiredFalse(anyString())).thenReturn(List.of());
    when(files.findBySessionId(anyString())).thenReturn(List.of());
    when(files.save(any(UploadedFile.class))).thenAnswer(invocation -> invocation.getArgument(0));

    when(storage.store(eq(StorageCategory.UPLOAD), anyString(), anyString(), any()))
        .thenAnswer(
            invocation -> {
              InputStream suppliedStream = invocation.getArgument(3);
              String key = "storage-key-" + storedObjects.size();
              storedObjects.put(key, suppliedStream.readAllBytes());
              return key;
            });
    when(storage.read(anyString()))
        .thenAnswer(
            invocation ->
                new ByteArrayInputStream(storedObjects.get((String) invocation.getArgument(0))));

    when(mapper.toFileDto(any(UploadedFile.class)))
        .thenReturn(new FileDto("file-1", "sales.xlsx", "file1", 0L, "csv", 0L, false));
  }

  /** Builds an in-memory xlsx; each inner list is one row, first row is the header. */
  private static byte[] xlsxBytes(List<List<String>> rows) throws Exception {
    try (XSSFWorkbook workbook = new XSSFWorkbook();
        ByteArrayOutputStream output = new ByteArrayOutputStream()) {
      var sheet = workbook.createSheet("Sheet1");
      for (int rowIndex = 0; rowIndex < rows.size(); rowIndex++) {
        var row = sheet.createRow(rowIndex);
        List<String> cells = rows.get(rowIndex);
        for (int columnIndex = 0; columnIndex < cells.size(); columnIndex++) {
          row.createCell(columnIndex).setCellValue(cells.get(columnIndex));
        }
      }
      workbook.write(output);
      return output.toByteArray();
    }
  }

  @Test
  void upload_realXlsxFile_normalizesAndParsesWithoutMisroutingToXlsxParser() throws Exception {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    byte[] xlsx =
        xlsxBytes(List.of(List.of("lot", "vt"), List.of("95", "0.419"), List.of("96", "0.423")));
    MockMultipartFile upload =
        new MockMultipartFile(
            "file",
            "sales.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            xlsx);

    List<FileDto> result = service.upload("session-1", List.of(upload));

    assertThat(result).hasSize(1);

    ArgumentCaptor<UploadedFile> savedEntity = ArgumentCaptor.forClass(UploadedFile.class);
    verify(files).save(savedEntity.capture());
    UploadedFile entity = savedEntity.getValue();

    // The on-disk format, not the uploaded extension: this is what regresses if FileService ever
    // again routes parsing.profile()/readAll() off filename instead of storedType.
    assertThat(entity.getType()).isEqualTo("csv");
    assertThat(entity.getName()).isEqualTo("sales.xlsx");
    assertThat(entity.getRowCount()).isEqualTo(2L);

    FileProfile profile = new ObjectMapper().readValue(entity.getMetadataJson(), FileProfile.class);
    assertThat(profile.headers()).containsExactly("lot", "vt");
    assertThat(profile.rowCount()).isEqualTo(2L);
  }
}
