package com.erd.cowork.parsing;

import com.erd.cowork.exception.ParseException;
import com.erd.cowork.parsing.model.ColumnProfile;
import com.erd.cowork.parsing.model.FileProfile;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Pattern;

/**
 * Incremental single-pass profiler shared by CSV and xlsx parsers.
 *
 * <p>non-bean: instantiate per file parse.
 */
public class FileProfileHelper {

  /** 上傳時擷取的樣本列上限（erd.upload.sample-rows）——prompt 樣本的天花板。 */
  private final int sampleLimit;

  /** 測試與便利建構子用的預設值；production 一律由 erd.upload.sample-rows 傳入。 */
  public static final int DEFAULT_SAMPLE_LIMIT = 20;

  public FileProfileHelper(int sampleLimit) {
    this.sampleLimit = sampleLimit;
  }

  private static final int DISTINCT_CAP = 1000;
  private static final int TOP_VALUES = 5;
  private static final Pattern DATE_PATTERN = Pattern.compile("^\\d{4}-\\d{2}-\\d{2}.*");

  private List<String> headers;
  private int colCount;
  private final List<List<String>> sampleRows = new ArrayList<>();
  private long rowCount;

  // per-column accumulators (initialised in onHeader)
  private boolean[] allNumeric;
  private boolean[] allDate;
  private long[] nullCounts;
  private long[] valueCounts;
  private double[] min;
  private double[] max;
  private double[] mean;
  private double[] varianceAccumulator;
  private List<Map<String, Long>> distinct = new ArrayList<>();

  /** No-arg constructor; headers are provided via {@link #onHeader(List)}. */
  public FileProfileHelper() {
    this(DEFAULT_SAMPLE_LIMIT);
  }

  /**
   * Convenience constructor for direct use (backwards-compatible). Delegates to {@link
   * #onHeader(List)}.
   */
  public FileProfileHelper(List<String> headers) {
    this(DEFAULT_SAMPLE_LIMIT);
    onHeader(headers);
  }

  /** Initialises all per-column accumulators for the given header names. */
  public void onHeader(List<String> headers) {
    if (headers.isEmpty()) {
      throw new ParseException("file has no header row");
    }
    this.headers = List.copyOf(headers);
    this.colCount = headers.size();
    this.allNumeric = new boolean[colCount];
    this.allDate = new boolean[colCount];
    this.nullCounts = new long[colCount];
    this.valueCounts = new long[colCount];
    this.min = new double[colCount];
    this.max = new double[colCount];
    this.mean = new double[colCount];
    this.varianceAccumulator = new double[colCount];
    this.distinct = new ArrayList<>();
    for (int columnIndex = 0; columnIndex < colCount; columnIndex++) {
      allNumeric[columnIndex] = true;
      allDate[columnIndex] = true;
      distinct.add(new HashMap<>());
    }
  }

  /** Accumulates statistics for a single data row. */
  public void accept(List<String> row) {
    rowCount++;
    if (sampleRows.size() < sampleLimit) {
      sampleRows.add(List.copyOf(row));
    }
    for (int columnIndex = 0; columnIndex < colCount; columnIndex++) {
      String raw = columnIndex < row.size() ? row.get(columnIndex) : null;
      String value = raw == null ? null : raw.trim();
      if (value == null || value.isEmpty()) {
        nullCounts[columnIndex]++;
        continue;
      }
      Double num = tryParseDouble(value);
      if (num != null) {
        allDate[columnIndex] = false;
        updateStats(columnIndex, num);
      } else {
        allNumeric[columnIndex] = false;
        if (!DATE_PATTERN.matcher(value).matches()) {
          allDate[columnIndex] = false;
        }
      }
      Map<String, Long> counts = distinct.get(columnIndex);
      if (counts.size() < DISTINCT_CAP || counts.containsKey(value)) {
        counts.merge(value, 1L, Long::sum);
      }
    }
  }

  /** Finalises and returns the accumulated {@link FileProfile}. */
  public FileProfile finish() {
    Objects.requireNonNull(headers, "onHeader() must be called before finish()");
    List<ColumnProfile> columns = new ArrayList<>(colCount);
    for (int columnIndex = 0; columnIndex < colCount; columnIndex++) {
      boolean numeric = allNumeric[columnIndex] && valueCounts[columnIndex] > 0;
      boolean date = !numeric && allDate[columnIndex] && nullCounts[columnIndex] < rowCount;
      String type = numeric ? "number" : (date ? "date" : "string");
      List<String> top =
          numeric
              ? List.of()
              : distinct.get(columnIndex).entrySet().stream()
                  .sorted(
                      Map.Entry.<String, Long>comparingByValue(Comparator.reverseOrder())
                          .thenComparing(Map.Entry::getKey))
                  .limit(TOP_VALUES)
                  .map(Map.Entry::getKey)
                  .toList();
      double std =
          valueCounts[columnIndex] > 1
              ? Math.sqrt(varianceAccumulator[columnIndex] / (valueCounts[columnIndex] - 1))
              : 0d;
      columns.add(
          new ColumnProfile(
              headers.get(columnIndex),
              type,
              numeric ? min[columnIndex] : null,
              numeric ? max[columnIndex] : null,
              numeric ? mean[columnIndex] : null,
              numeric ? std : null,
              nullCounts[columnIndex],
              top));
    }
    return new FileProfile(rowCount, colCount, headers, columns, List.copyOf(sampleRows));
  }

  private void updateStats(int columnIndex, double value) {
    if (valueCounts[columnIndex] == 0) {
      min[columnIndex] = value;
      max[columnIndex] = value;
    } else {
      min[columnIndex] = Math.min(min[columnIndex], value);
      max[columnIndex] = Math.max(max[columnIndex], value);
    }
    valueCounts[columnIndex]++;
    double delta = value - mean[columnIndex];
    mean[columnIndex] += delta / valueCounts[columnIndex];
    varianceAccumulator[columnIndex] += delta * (value - mean[columnIndex]);
  }

  private Double tryParseDouble(String numberText) {
    try {
      return Double.valueOf(numberText);
    } catch (NumberFormatException exception) {
      return null;
    }
  }
}
