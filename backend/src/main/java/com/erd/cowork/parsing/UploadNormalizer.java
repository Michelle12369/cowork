package com.erd.cowork.parsing;

import com.erd.cowork.exception.ParseException;
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
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVPrinter;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.springframework.stereotype.Component;

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
public class UploadNormalizer {

  private static final String CSV_TYPE = "csv";

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
      Files.deleteIfExists(temporaryFile);
      throw exception;
    }
  }

  private void convertFirstSheetToCsv(InputStream source, Path target, String originalFilename)
      throws IOException {
    DataFormatter formatter = new DataFormatter();
    try (Workbook workbook =
            StreamingReader.builder().rowCacheSize(100).bufferSize(8192).open(source);
        Writer writer =
            new OutputStreamWriter(Files.newOutputStream(target), StandardCharsets.UTF_8);
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
          headerWidth = cells.size();
        } else if (cells.size() < headerWidth) {
          // A row whose trailing (or all) columns are empty reads back narrower than the
          // header unless padded here -- CsvParsingService's setIgnoreEmptyLines(false) format
          // trusts row width, so a short row silently misaligns every column after it.
          cells = padToWidth(cells, headerWidth);
        }
        printer.printRecord(cells);
        wroteAnyRow = true;
      }
      if (!wroteAnyRow) {
        throw new ParseException("xlsx has no rows");
      }
    } catch (ParseException exception) {
      throw exception;
    } catch (IOException exception) {
      throw exception;
    } catch (Exception exception) {
      throw new ParseException("failed to convert xlsx: " + exception.getMessage(), exception);
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
