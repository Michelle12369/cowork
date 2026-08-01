package com.erd.cowork.agent;

import java.util.LinkedHashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.util.StringUtils;

/**
 * Static utility for extracting {@code [[table:id]]} marker ids from AI answer text — the
 * server-side mirror of the frontend {@code tableMarkers.ts} regex, so the persisted {@code
 * referencedTablesJson} captures exactly the tables the answer actually renders inline.
 */
public final class TableMarkerUtils {

  private static final Pattern TABLE_MARKER_PATTERN = Pattern.compile("\\[\\[table:([^\\]]+)]]");

  private TableMarkerUtils() {
    throw new UnsupportedOperationException("Utility class");
  }

  /**
   * Returns every table id referenced by a {@code [[table:id]]} marker in {@code answerText}, in
   * first-appearance order. Duplicate markers for the same id contribute one entry. Returns an
   * empty set when {@code answerText} is blank or carries no marker.
   */
  public static Set<String> extractReferencedTableIds(String answerText) {
    Set<String> ids = new LinkedHashSet<>();
    if (!StringUtils.hasText(answerText)) {
      return ids;
    }
    Matcher matcher = TABLE_MARKER_PATTERN.matcher(answerText);
    while (matcher.find()) {
      ids.add(matcher.group(1));
    }
    return ids;
  }
}
