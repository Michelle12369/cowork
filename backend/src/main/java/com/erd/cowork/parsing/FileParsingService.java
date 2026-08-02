package com.erd.cowork.parsing;

import com.erd.cowork.exception.ParseException;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.parsing.model.ParsedRows;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import java.util.Locale;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class FileParsingService {

  private final CsvParsingService csvParsingService;
  private final XlsxParsingService xlsxParsingService;
  private final ObjectMapper objectMapper;

  /**
   * Profiles the file in a single streaming pass, computing column statistics and a sample of rows.
   *
   * @param fileType the on-disk format (e.g. {@code UploadedFile.type}) — NOT the uploaded
   *     filename's extension, which may differ now that uploads are normalized to CSV before
   *     storage. Callers that only have an uploaded filename MUST resolve the stored type first;
   *     see {@link #extension(String)} for uploaded-extension use cases (validation, normalization)
   *     that are unrelated to what is actually on disk.
   */
  public FileProfile profile(String fileType, InputStream in) {
    return switch (fileType) {
      case "csv" -> csvParsingService.profile(in);
      case "xlsx" -> xlsxParsingService.profile(in);
      default -> throw new ParseException("unsupported file type: " + fileType);
    };
  }

  /**
   * Reads every data row from the file and returns the full column list alongside all rows.
   *
   * @param fileType the on-disk format — see {@link #profile(String, InputStream)} for why this is
   *     not the uploaded filename's extension.
   */
  public ParsedRows readAll(String fileType, InputStream in) {
    return switch (fileType) {
      case "csv" -> csvParsingService.readAll(in);
      case "xlsx" -> xlsxParsingService.readAll(in);
      default -> throw new ParseException("unsupported file type: " + fileType);
    };
  }

  public String toJson(FileProfile profile) {
    try {
      return objectMapper.writeValueAsString(profile);
    } catch (JsonProcessingException exception) {
      throw new ParseException("failed to serialize profile", exception);
    }
  }

  public static String extension(String filename) {
    int dot = filename.lastIndexOf('.');
    return dot < 0 ? "" : filename.substring(dot + 1).toLowerCase(Locale.ROOT);
  }
}
