package com.erd.cowork.parsing;

import com.erd.cowork.exception.ParseException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.parsing.model.ParsedRows;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
@Slf4j
@LogAnnotation
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

  /**
   * 序列化 profile 並保證結果放得進 TEXT 欄位（64KB）：超限時逐步砍半 sampleRows（生成端降級）， 樣本清空仍超限（極寬表的欄位統計本身過大）才硬截斷——截斷後非合法
   * JSON，下游 （AgentOrchestrator / ArtifactRepairService）本就以 lenient 模式跳過 unparseable metadata，
   * 該檔案僅失去 LLM context，上傳與查詢不受影響。
   */
  public String toJsonWithinByteLimit(FileProfile profile) {
    String json = toJson(profile);
    List<List<String>> sampleRows = profile.sampleRows();
    while (utf8ByteLength(json) > TextColumnUtils.TEXT_COLUMN_MAX_BYTES && !sampleRows.isEmpty()) {
      sampleRows = sampleRows.subList(0, sampleRows.size() / 2);
      profile =
          new FileProfile(
              profile.rowCount(),
              profile.colCount(),
              profile.headers(),
              profile.columns(),
              sampleRows);
      json = toJson(profile);
      log.warn("metadata json over TEXT limit, sample rows reduced to {} rows", sampleRows.size());
    }
    if (utf8ByteLength(json) > TextColumnUtils.TEXT_COLUMN_MAX_BYTES) {
      log.warn(
          "metadata json still over TEXT limit after dropping samples ({} bytes), hard-truncating",
          utf8ByteLength(json));
      json = TextColumnUtils.truncateToUtf8Bytes(json, TextColumnUtils.TEXT_COLUMN_MAX_BYTES);
    }
    return json;
  }

  private static int utf8ByteLength(String value) {
    return value.getBytes(StandardCharsets.UTF_8).length;
  }
}
