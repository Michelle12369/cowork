package com.erd.cowork.artifact;

import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.TextSearchUtils;
import com.erd.cowork.parsing.model.ParsedRows;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.io.StringWriter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.velocity.VelocityContext;
import org.apache.velocity.app.VelocityEngine;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
@Slf4j
@LogAnnotation
public class ArtifactAssembler {

  private static final String HEAD_INJECT_TEMPLATE = "templates/artifact/head-inject.vm";

  /**
   * Marker string checked (case-insensitively) to detect whether the artifact uses ECharts, which
   * determines whether the erd theme script block is injected.
   */
  private static final String ECHARTS_MARKER = "echarts";

  /**
   * Marker string that dashboard-mode LLM HTML references ({@code window.__ERD_DATA__}). Its
   * absence means the HTML is self-contained (analysis-mode renderer output), so the data script is
   * skipped — injecting it would be pure waste (50-300KB) and conceptually wrong.
   */
  private static final String DATA_MARKER = "__ERD_DATA__";

  private final FileStorage fileStorage;
  private final FileParsingService fileParsingService;
  private final UploadedFileRepository uploadedFileRepository;
  private final ObjectMapper objectMapper;
  private final VelocityEngine velocityEngine;

  /**
   * True when {@link #assemble} will inject the data script — i.e. the HTML references the marker.
   */
  public boolean injectsData(String rawHtml) {
    return rawHtml != null && rawHtml.contains(DATA_MARKER);
  }

  /**
   * Reads session's non-expired files (full scan), and injects a {@code <script>window.__ERD_DATA__
   * = {...};</script>} block into the HTML.
   *
   * <p>If a single file fails to read/parse, it is skipped with a warning; the others still appear.
   */
  public String assemble(String sessionId, String rawHtml) {
    if (rawHtml == null) {
      return null;
    }
    // Self-contained analysis-mode HTML never references this marker; skip the DB scan and
    // data script entirely for it (avoids 50-300KB of wasted, conceptually-wrong injection).
    boolean includeData = rawHtml.contains(DATA_MARKER);
    Map<String, Object> dataMap = new LinkedHashMap<>();

    if (includeData) {
      List<UploadedFile> files = uploadedFileRepository.findBySessionIdAndExpiredFalse(sessionId);
      for (UploadedFile file : files) {
        try {
          dataMap.put(file.getAlias(), buildEntry(file));
        } catch (Exception exception) {
          log.warn(
              "ArtifactAssembler: skipping file alias={} name={}: {}",
              file.getAlias(),
              file.getName(),
              exception.getMessage());
        }
      }
    }

    try {
      return inject(rawHtml, dataMap, includeData);
    } catch (Exception exception) {
      log.error(
          "ArtifactAssembler: failed to serialize/inject data for session {}; returning raw html",
          sessionId,
          exception);
      return rawHtml;
    }
  }

  // --- private helpers ----------------------------------------------------------

  private Map<String, Object> buildEntry(UploadedFile file) throws IOException {
    try (InputStream in = fileStorage.read(file.getStorageKey())) {
      // file.getType(), not file.getName(): parsing must dispatch on the on-disk format (always
      // "csv" post-normalization). file.getName() deliberately keeps the original extension (e.g.
      // "sales.xlsx"), which would misroute this to XlsxParsingService against CSV bytes.
      ParsedRows parsed = fileParsingService.readAll(file.getType(), in);
      Map<String, Object> entry = new LinkedHashMap<>();
      entry.put("columns", parsed.columns());
      entry.put("rows", parsed.rows());
      entry.put("totalRows", parsed.rows().size());
      return entry;
    }
  }

  private String inject(String rawHtml, Map<String, Object> dataMap, boolean includeData) {
    String safeJson = null;
    if (includeData) {
      String json;
      try {
        json = objectMapper.writeValueAsString(dataMap);
      } catch (JsonProcessingException exception) {
        throw new RuntimeException("ArtifactAssembler: failed to serialize data map", exception);
      }
      // Escape </script> to prevent premature script-block termination
      safeJson = json.replace("</", "<\\/");
    }

    // Inject the 'erd' ECharts theme only when the artifact actually uses ECharts
    boolean hasEcharts = TextSearchUtils.indexOfIgnoreCase(rawHtml, ECHARTS_MARKER) >= 0;

    VelocityContext velocityContext = new VelocityContext();
    velocityContext.put("hasEcharts", hasEcharts);
    velocityContext.put("includeData", includeData);
    velocityContext.put("dataJson", safeJson);
    StringWriter stringWriter = new StringWriter();
    velocityEngine.mergeTemplate(HEAD_INJECT_TEMPLATE, "UTF-8", velocityContext, stringWriter);
    String injectBlock = stringWriter.toString();

    // Case-insensitive search for first <head> tag (not <header>)
    int headIdx = findHeadTag(rawHtml);
    if (headIdx >= 0) {
      // Find the end of the opening <head...> tag
      int closeAngle = rawHtml.indexOf('>', headIdx);
      if (closeAngle < 0) {
        // <head token is truncated — closing '>' not found; treat as no head and prepend
        return injectBlock + rawHtml;
      }
      return rawHtml.substring(0, closeAngle + 1)
          + "\n"
          + injectBlock
          + rawHtml.substring(closeAngle + 1);
    }
    // No <head> → prepend
    return injectBlock + rawHtml;
  }

  /**
   * Returns the index of the first {@code <head>} / {@code <head ...>} tag in {@code html},
   * ignoring {@code <header>} and any other tag whose name merely starts with "head".
   *
   * <p>A match is only accepted when the character immediately after {@code <head} is {@code >},
   * ASCII whitespace, or {@code /} (self-closing).
   *
   * @return index of the {@code <} character, or {@code -1} if not found
   */
  private static int findHeadTag(String html) {
    int from = 0;
    while (true) {
      int idx = TextSearchUtils.indexOfIgnoreCase(html, "<head", from);
      if (idx < 0) {
        return -1;
      }
      int after = idx + 5;
      if (after >= html.length()) {
        return -1;
      }
      char ch = html.charAt(after);
      if (ch == '>' || ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r' || ch == '/') {
        return idx;
      }
      from = idx + 1;
    }
  }
}
