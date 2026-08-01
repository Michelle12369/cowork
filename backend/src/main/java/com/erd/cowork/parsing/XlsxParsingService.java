package com.erd.cowork.parsing;

import com.erd.cowork.exception.ParseException;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.parsing.model.ParsedRows;
import com.github.pjfanning.xlsx.StreamingReader;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.springframework.stereotype.Service;

@Service
@lombok.RequiredArgsConstructor
public class XlsxParsingService {

  private final com.erd.cowork.config.UploadProperties uploadProperties;

  /** Profiles the xlsx sheet: single pass computing statistics and collecting sample rows. */
  public FileProfile profile(InputStream in) {
    FileProfileHelper profiler = new FileProfileHelper(uploadProperties.sampleRows());
    DataFormatter formatter = new DataFormatter();
    try (Workbook workbook =
        StreamingReader.builder().rowCacheSize(100).bufferSize(8192).open(in)) {
      Sheet sheet = workbook.getSheetAt(0);
      boolean headerDone = false;
      for (Row row : sheet) {
        List<String> cells = extractCells(row, formatter);
        if (!headerDone) {
          profiler.onHeader(cells);
          headerDone = true;
        } else {
          profiler.accept(cells);
        }
      }
      if (!headerDone) {
        throw new ParseException("xlsx has no rows");
      }
    } catch (ParseException exception) {
      throw exception;
    } catch (Exception exception) {
      throw new ParseException("failed to parse xlsx: " + exception.getMessage(), exception);
    }
    return profiler.finish();
  }

  /** Reads all rows from the first sheet and returns them together with the header column names. */
  public ParsedRows readAll(InputStream in) {
    DataFormatter formatter = new DataFormatter();
    try (Workbook workbook =
        StreamingReader.builder().rowCacheSize(100).bufferSize(8192).open(in)) {
      Sheet sheet = workbook.getSheetAt(0);
      List<String> columns = null;
      List<List<String>> rows = new ArrayList<>();
      for (Row row : sheet) {
        List<String> cells = extractCells(row, formatter);
        if (columns == null) {
          columns = List.copyOf(cells);
        } else {
          rows.add(List.copyOf(cells));
        }
      }
      if (columns == null) {
        throw new ParseException("xlsx has no rows");
      }
      return new ParsedRows(columns, rows);
    } catch (ParseException exception) {
      throw exception;
    } catch (Exception exception) {
      throw new ParseException("failed to parse xlsx: " + exception.getMessage(), exception);
    }
  }

  private static List<String> extractCells(Row row, DataFormatter formatter) {
    List<String> cells = new ArrayList<>();
    int last = row.getLastCellNum();
    for (int columnIndex = 0; columnIndex < last; columnIndex++) {
      Cell cell = row.getCell(columnIndex);
      cells.add(cell == null ? "" : formatter.formatCellValue(cell));
    }
    return cells;
  }
}
