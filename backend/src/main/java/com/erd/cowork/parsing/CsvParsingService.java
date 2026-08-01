package com.erd.cowork.parsing;

import com.erd.cowork.exception.ParseException;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.parsing.model.ParsedRows;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.apache.commons.csv.CSVFormat;
import org.apache.commons.csv.CSVParser;
import org.apache.commons.csv.CSVRecord;
import org.springframework.stereotype.Service;

@Service
@lombok.RequiredArgsConstructor
public class CsvParsingService {

  private final com.erd.cowork.config.UploadProperties uploadProperties;

  private static CSVFormat csvFormat() {
    return CSVFormat.DEFAULT
        .builder()
        .setHeader()
        .setSkipHeaderRecord(true)
        .setIgnoreEmptyLines(false)
        .build();
  }

  /** Profiles the CSV: single pass that computes statistics and collects sample rows. */
  public FileProfile profile(InputStream in) {
    FileProfileHelper profiler = new FileProfileHelper(uploadProperties.sampleRows());
    try (CSVParser parser =
        CSVParser.parse(new InputStreamReader(in, StandardCharsets.UTF_8), csvFormat())) {
      profiler.onHeader(parser.getHeaderNames());
      for (CSVRecord record : parser) {
        List<String> row = new ArrayList<>(record.size());
        record.forEach(row::add);
        profiler.accept(row);
      }
    } catch (ParseException exception) {
      throw exception;
    } catch (IOException | IllegalArgumentException exception) {
      throw new ParseException("failed to parse csv: " + exception.getMessage(), exception);
    }
    return profiler.finish();
  }

  /** Reads all rows from the CSV and returns them together with the header column names. */
  public ParsedRows readAll(InputStream in) {
    try (CSVParser parser =
        CSVParser.parse(new InputStreamReader(in, StandardCharsets.UTF_8), csvFormat())) {
      List<String> columns = parser.getHeaderNames();
      List<List<String>> rows = new ArrayList<>();
      for (CSVRecord record : parser) {
        List<String> row = new ArrayList<>(record.size());
        record.forEach(row::add);
        rows.add(List.copyOf(row));
      }
      return new ParsedRows(columns, rows);
    } catch (ParseException exception) {
      throw exception;
    } catch (IOException | IllegalArgumentException exception) {
      throw new ParseException("failed to parse csv: " + exception.getMessage(), exception);
    }
  }
}
