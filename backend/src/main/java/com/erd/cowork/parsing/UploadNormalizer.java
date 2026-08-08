package com.erd.cowork.parsing;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.exception.ErrorCode;
import com.erd.cowork.exception.ParseException;
import com.erd.cowork.exception.UploadLimitException;
import com.erd.cowork.logging.LogAnnotation;
import com.github.pjfanning.xlsx.StreamingReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVPrinter;
import org.apache.commons.io.output.CountingOutputStream;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

/**
 * Normalizes an upload to the single format everything downstream reads: CSV.
 *
 * <p>xlsx is converted here rather than at read time because deepagent-service points DuckDB at the
 * stored file directly and DuckDB has no xlsx reader. Converting once at upload keeps a single
 * format at rest instead of teaching every reader about spreadsheets.
 *
 * <p>Only the first sheet is used. That is not a new restriction: {@link XlsxParsingService}
 * already pins {@code getSheetAt(0)}, so multi-sheet workbooks have always been read this way.
 */
@Slf4j
@Component
@RequiredArgsConstructor
@LogAnnotation
public class UploadNormalizer {

  private static final String CSV_TYPE = "csv";

  private final UploadProperties uploadProperties;

  public NormalizedUpload normalize(InputStream source, String originalFilename)
      throws IOException {
    Path temporaryFile = Files.createTempFile("erd-upload-", ".tmp");
    try {
      if ("xlsx".equals(FileParsingService.extension(originalFilename))) {
        convertFirstSheetToCsv(source, temporaryFile, originalFilename);
      } else {
        Files.copy(source, temporaryFile, StandardCopyOption.REPLACE_EXISTING);
      }
      return new NormalizedUpload(temporaryFile, CSV_TYPE);
    } catch (RuntimeException | IOException exception) {
      deleteTemporaryFileQuietly(temporaryFile);
      throw exception;
    }
  }

  /**
   * Deletes a partial temp file, logging (path only, never content) instead of throwing: a failure
   * to delete must never replace the conversion failure that triggered the cleanup.
   */
  private void deleteTemporaryFileQuietly(Path temporaryFile) {
    try {
      Files.deleteIfExists(temporaryFile);
    } catch (IOException deleteException) {
      log.warn("failed to delete partial normalizer temp file {}", temporaryFile, deleteException);
    }
  }

  private void convertFirstSheetToCsv(InputStream source, Path target, String originalFilename)
      throws IOException {
    DataFormatter formatter = new DataFormatter();
    long maxCsvBytes = uploadProperties.maxCsvBytes();
    try (Workbook workbook =
            StreamingReader.builder().rowCacheSize(100).bufferSize(8192).open(source);
        CountingOutputStream counting = new CountingOutputStream(Files.newOutputStream(target));
        Writer writer = new OutputStreamWriter(counting, StandardCharsets.UTF_8);
        CSVPrinter printer = new CSVPrinter(writer, CSVFormat.DEFAULT)) {
      int sheetCount = workbook.getNumberOfSheets();
      if (sheetCount > 1) {
        // Data silently disappearing is the worst failure mode -- leave a trace.
        log.warn(
            "xlsx {} has {} sheets; only the first is converted", originalFilename, sheetCount);
      }
      Sheet sheet = workbook.getSheetAt(0);
      boolean wroteAnyRow = false;
      int headerWidth = -1;
      for (Row row : sheet) {
        List<String> cells = extractCells(row, formatter);
        if (headerWidth < 0) {
          requireNoBlankHeaderCells(cells);
          headerWidth = cells.size();
        } else if (cells.size() < headerWidth) {
          // A row whose trailing (or all) columns are empty reads back narrower than the
          // header unless padded here -- CsvParsingService's setIgnoreEmptyLines(false) format
          // trusts row width, so a short row silently misaligns every column after it.
          cells = padToWidth(cells, headerWidth);
        }
        printer.printRecord(cells);
        wroteAnyRow = true;
        // Upload validation only sees the pre-conversion xlsx size, but xlsx->CSV expands 10-20x
        // for text-heavy sheets, so the write side needs its own cap. The count lags by at most
        // one writer buffer, which does not change the limit's meaning.
        if (counting.getByteCount() > maxCsvBytes) {
          throw new UploadLimitException(
              ErrorCode.UPLOAD_LIMIT, "Converted CSV exceeds the " + maxCsvBytes + " byte limit.");
        }
      }
      if (!wroteAnyRow) {
        throw new ParseException("xlsx has no rows");
      }
    } catch (ParseException | UploadLimitException exception) {
      throw exception;
    } catch (IOException exception) {
      throw exception;
    } catch (Exception exception) {
      throw new ParseException("failed to convert xlsx: " + exception.getMessage(), exception);
    }
  }

  /**
   * Rejects the header row outright if any cell is blank (empty or whitespace-only).
   *
   * <p>The CSV written here is read by two independent consumers: commons-csv on the Java side and
   * DuckDB's {@code read_csv_auto} in deepagent-service. An invented positional name would need
   * both to agree on it to stay consistent, and silently inventing a name also hides a data problem
   * from the user -- a blank header cell almost always means the source file is malformed. A clear,
   * early error is preferable to guessing.
   */
  private static void requireNoBlankHeaderCells(List<String> headerCells) {
    for (int columnIndex = 0; columnIndex < headerCells.size(); columnIndex++) {
      if (!StringUtils.hasText(headerCells.get(columnIndex))) {
        throw new ParseException(
            "xlsx header row has a blank cell at column "
                + (columnIndex + 1)
                + "; every column needs a name in the first row");
      }
    }
  }

  private static List<String> extractCells(Row row, DataFormatter formatter) {
    List<String> cells = new ArrayList<>();
    int lastCellNumber = row.getLastCellNum();
    for (int columnIndex = 0; columnIndex < lastCellNumber; columnIndex++) {
      Cell cell = row.getCell(columnIndex);
      cells.add(cell == null ? "" : formatter.formatCellValue(cell));
    }
    return cells;
  }

  private static List<String> padToWidth(List<String> cells, int width) {
    List<String> padded = new ArrayList<>(cells);
    while (padded.size() < width) {
      padded.add("");
    }
    return padded;
  }
}
