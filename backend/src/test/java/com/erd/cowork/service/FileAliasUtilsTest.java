package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;
import org.junit.jupiter.api.Test;

class FileAliasUtilsTest {

  // --- stripExtension ---

  @Test
  void stripExtension_withExtension_removesIt() {
    assertThat(FileAliasUtils.stripExtension("sales.csv")).isEqualTo("sales");
  }

  @Test
  void stripExtension_withoutExtension_returnsOriginal() {
    assertThat(FileAliasUtils.stripExtension("sales")).isEqualTo("sales");
  }

  @Test
  void stripExtension_dotFile_returnsOriginal() {
    // dot at position 0 is treated as part of the name, not an extension separator
    assertThat(FileAliasUtils.stripExtension(".gitignore")).isEqualTo(".gitignore");
  }

  // --- toSlug ---

  @Test
  void toSlug_englishStem_returnsLowercaseUnchanged() {
    assertThat(FileAliasUtils.toSlug("sales_report")).isEqualTo("sales_report");
  }

  @Test
  void toSlug_uppercaseStem_lowercases() {
    assertThat(FileAliasUtils.toSlug("Sales")).isEqualTo("sales");
  }

  @Test
  void toSlug_chineseStem_preservesCharacters() {
    assertThat(FileAliasUtils.toSlug("請假紀錄")).isEqualTo("請假紀錄");
  }

  @Test
  void toSlug_spacesAndParens_collapsedToSingleUnderscore() {
    // "sales (1)" → s,a,l,e,s,' ','(','1',')' → "sales__1_" → collapse → "sales_1_" → trim →
    // "sales_1"
    assertThat(FileAliasUtils.toSlug("sales (1)")).isEqualTo("sales_1");
  }

  @Test
  void toSlug_hyphen_replacedWithUnderscore() {
    assertThat(FileAliasUtils.toSlug("sales-1")).isEqualTo("sales_1");
  }

  @Test
  void toSlug_consecutiveSpecialChars_collapsedToSingleUnderscore() {
    assertThat(FileAliasUtils.toSlug("sales--data")).isEqualTo("sales_data");
  }

  @Test
  void toSlug_leadingAndTrailingUnderscores_trimmed() {
    assertThat(FileAliasUtils.toSlug("_sales_")).isEqualTo("sales");
  }

  @Test
  void toSlug_allSymbols_returnsEmpty() {
    assertThat(FileAliasUtils.toSlug("###")).isEmpty();
  }

  @Test
  void toSlug_longAsciiStem_truncatedToByteLimit() {
    // ASCII chars are 1 byte each; 70 chars = 70 bytes > MAX_ALIAS_UTF8_BYTES (60)
    String longStem = "a".repeat(70);
    String slug = FileAliasUtils.toSlug(longStem);
    assertThat(slug.getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(FileAliasUtils.MAX_ALIAS_UTF8_BYTES);
    assertThat(slug).isEqualTo("a".repeat(FileAliasUtils.MAX_ALIAS_UTF8_BYTES));
  }

  @Test
  void toSlug_longChineseStem_byteAwareTruncationAtCodePointBoundary() {
    // Each CJK char encodes to 3 UTF-8 bytes; 20 chars × 3 = 60 bytes = MAX_ALIAS_UTF8_BYTES
    String twentyChars = "請".repeat(20); // 60 bytes — at limit, must not truncate
    String twentyOneChars = "請".repeat(21); // 63 bytes — must truncate to 20 chars (60 bytes)

    assertThat(FileAliasUtils.toSlug(twentyChars)).isEqualTo(twentyChars);
    assertThat(FileAliasUtils.toSlug(twentyOneChars)).isEqualTo(twentyChars);
    assertThat(FileAliasUtils.toSlug(twentyOneChars).getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(FileAliasUtils.MAX_ALIAS_UTF8_BYTES);
  }

  // --- generateAlias (full pipeline) ---

  @Test
  void generateAlias_plainEnglishFilename_returnsSlug() {
    assertThat(FileAliasUtils.generateAlias("sales.csv", Set.of()).alias()).isEqualTo("sales");
  }

  @Test
  void generateAlias_plainEnglishFilename_suffixNumberIsZero() {
    assertThat(FileAliasUtils.generateAlias("sales.csv", Set.of()).suffixNumber()).isZero();
  }

  @Test
  void generateAlias_chineseFilename_returnsChineseStem() {
    assertThat(FileAliasUtils.generateAlias("請假紀錄.csv", Set.of()).alias()).isEqualTo("請假紀錄");
  }

  @Test
  void generateAlias_caseVariant_deduplicatesWithSuffix2() {
    // Sales.csv uploaded first → alias "sales"; now sales.csv → same slug → "sales_2"
    assertThat(FileAliasUtils.generateAlias("sales.csv", Set.of("sales")).alias())
        .isEqualTo("sales_2");
  }

  @Test
  void generateAlias_thirdDuplicate_getsSuffix3() {
    assertThat(FileAliasUtils.generateAlias("sales.csv", Set.of("sales", "sales_2")).alias())
        .isEqualTo("sales_3");
  }

  @Test
  void generateAlias_expiredFileAliasOccupied_stillDeduplicates() {
    // Expired files' aliases must occupy names — same behaviour as active ones.
    assertThat(FileAliasUtils.generateAlias("sales.csv", Set.of("sales")).alias())
        .isEqualTo("sales_2");
  }

  @Test
  void generateAlias_allSymbolFilename_fallbackFile1() {
    assertThat(FileAliasUtils.generateAlias("###.csv", Set.of()).alias()).isEqualTo("file1");
  }

  @Test
  void generateAlias_allSymbolFilenameWithExistingFallback_incrementsFallbackNumber() {
    assertThat(FileAliasUtils.generateAlias("###.csv", Set.of("file1")).alias()).isEqualTo("file2");
  }

  @Test
  void generateAlias_longAsciiFilename_aliasBytesWithinLimit() {
    // 70 ASCII chars → slug truncated to 60 bytes
    String longFilename = "a".repeat(70) + ".csv";
    FileAliasUtils.AliasResolution resolution =
        FileAliasUtils.generateAlias(longFilename, Set.of());
    assertThat(resolution.alias().getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(FileAliasUtils.MAX_ALIAS_UTF8_BYTES);
    assertThat(resolution.alias()).isEqualTo("a".repeat(FileAliasUtils.MAX_ALIAS_UTF8_BYTES));
  }

  @Test
  void generateAlias_longChineseFilename_aliasBytesWithinLimit() {
    // 25 CJK chars × 3 bytes = 75 bytes → must be truncated to ≤ 60 bytes (≤ 20 chars)
    String longChineseFilename = "請".repeat(25) + ".csv";
    FileAliasUtils.AliasResolution resolution =
        FileAliasUtils.generateAlias(longChineseFilename, Set.of());
    int aliasByteLength = resolution.alias().getBytes(StandardCharsets.UTF_8).length;
    assertThat(aliasByteLength).isLessThanOrEqualTo(FileAliasUtils.MAX_ALIAS_UTF8_BYTES);
    // No code point should be split — each char is exactly 3 bytes, so bytes must be a multiple of
    // 3
    assertThat(aliasByteLength % 3).isZero();
    assertThat(resolution.suffixNumber()).isZero();
  }

  @Test
  void generateAlias_chineseFilenameWithCollision_suffixedAliasBytesWithinLimit() {
    // Slug at exactly 60 bytes (20 CJK chars); on collision body must shrink to fit "_2"
    String fullSlug = "請".repeat(20); // 60 bytes
    String filename = fullSlug + ".csv";
    Set<String> occupied = Set.of(fullSlug);

    FileAliasUtils.AliasResolution resolution = FileAliasUtils.generateAlias(filename, occupied);

    assertThat(resolution.alias().getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(FileAliasUtils.MAX_ALIAS_UTF8_BYTES);
    assertThat(resolution.alias()).endsWith("_2");
    assertThat(resolution.suffixNumber()).isEqualTo(2);
  }

  @Test
  void generateAlias_differentSpecialCharPatternsSameSlug_secondGetsDeduplicatedAlias() {
    // "sales (1).csv" → "sales_1"; "sales-1.csv" also → "sales_1" → collision → "sales_1_2"
    Set<String> occupied = new HashSet<>();
    FileAliasUtils.AliasResolution first = FileAliasUtils.generateAlias("sales (1).csv", occupied);
    occupied.add(first.alias());
    FileAliasUtils.AliasResolution second = FileAliasUtils.generateAlias("sales-1.csv", occupied);

    assertThat(first.alias()).isEqualTo("sales_1");
    assertThat(second.alias()).isEqualTo("sales_1_2");
  }

  @Test
  void generateAlias_longSlugWithCollision_suffixFitsWithinByteLimit() {
    // Slug is exactly MAX_ALIAS_UTF8_BYTES chars (ASCII); adding "_2" would exceed limit → body
    // truncated first
    String longSlug = "a".repeat(FileAliasUtils.MAX_ALIAS_UTF8_BYTES);
    String longFilename = longSlug + ".csv";
    Set<String> occupied = Set.of(longSlug);

    FileAliasUtils.AliasResolution resolution =
        FileAliasUtils.generateAlias(longFilename, occupied);

    assertThat(resolution.alias().getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(FileAliasUtils.MAX_ALIAS_UTF8_BYTES);
    assertThat(resolution.alias()).endsWith("_2");
  }

  @Test
  void generateAlias_intraBatchCollisionTracking_bothAliasesAreUnique() {
    // Simulate two files with the same name in one batch; caller adds each alias to occupied set.
    Set<String> occupied = new HashSet<>();
    FileAliasUtils.AliasResolution first = FileAliasUtils.generateAlias("report.csv", occupied);
    occupied.add(first.alias());
    FileAliasUtils.AliasResolution second = FileAliasUtils.generateAlias("report.csv", occupied);

    assertThat(first.alias()).isEqualTo("report");
    assertThat(first.suffixNumber()).isZero();
    assertThat(second.alias()).isEqualTo("report_2");
    assertThat(second.suffixNumber()).isEqualTo(2);
  }

  // --- buildDisplayName ---

  @Test
  void buildDisplayName_uppercaseFilename_lowercasesName() {
    assertThat(FileAliasUtils.buildDisplayName("Sales.csv", 0)).isEqualTo("sales.csv");
  }

  @Test
  void buildDisplayName_noSuffix_returnsLowercasedFilenameUnchanged() {
    assertThat(FileAliasUtils.buildDisplayName("report_data.CSV", 0)).isEqualTo("report_data.csv");
  }

  @Test
  void buildDisplayName_withSuffix2_insertsSuffixBeforeExtension() {
    assertThat(FileAliasUtils.buildDisplayName("Sales.csv", 2)).isEqualTo("sales_2.csv");
  }

  @Test
  void buildDisplayName_withSuffix3_insertsSuffixBeforeExtension() {
    assertThat(FileAliasUtils.buildDisplayName("Sales.csv", 3)).isEqualTo("sales_3.csv");
  }

  @Test
  void buildDisplayName_chineseFilename_unaffectedByLowercase() {
    // CJK characters have no case; toLowerCase is a no-op for them
    assertThat(FileAliasUtils.buildDisplayName("請假紀錄.csv", 0)).isEqualTo("請假紀錄.csv");
  }

  @Test
  void buildDisplayName_chineseFilenameWithSuffix_insertsSameSuffixNumber() {
    assertThat(FileAliasUtils.buildDisplayName("請假紀錄.csv", 2)).isEqualTo("請假紀錄_2.csv");
  }

  @Test
  void buildDisplayName_veryLongChineseStem_truncatesStemPreservesExtension() {
    // 134 CJK chars × 3 bytes = 402 bytes; with ".csv" (4 bytes) = 406 > MAX_NAME_UTF8_BYTES (400)
    String longStem = "请".repeat(134);
    String filename = longStem + ".csv";
    String result = FileAliasUtils.buildDisplayName(filename, 0);

    assertThat(result).endsWith(".csv");
    assertThat(result.getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(FileAliasUtils.MAX_NAME_UTF8_BYTES);
  }

  @Test
  void buildDisplayName_veryLongChineseStemWithSuffix_truncatesStemPreservesExtensionAndSuffix() {
    String longStem = "请".repeat(134);
    String filename = longStem + ".csv";
    String result = FileAliasUtils.buildDisplayName(filename, 2);

    assertThat(result).endsWith("_2.csv");
    assertThat(result.getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(FileAliasUtils.MAX_NAME_UTF8_BYTES);
  }

  // --- same-number guarantee: alias suffixNumber == name suffix ---

  @Test
  void generateAlias_duplicateSalesCsv_aliasAndNameSuffixNumbersMatch() {
    // First upload: no collision
    FileAliasUtils.AliasResolution first = FileAliasUtils.generateAlias("Sales.csv", Set.of());
    assertThat(first.alias()).isEqualTo("sales");
    assertThat(first.suffixNumber()).isZero();
    assertThat(FileAliasUtils.buildDisplayName("Sales.csv", first.suffixNumber()))
        .isEqualTo("sales.csv");

    // Second upload with same slug: gets _2
    FileAliasUtils.AliasResolution second =
        FileAliasUtils.generateAlias("Sales.csv", Set.of("sales"));
    assertThat(second.alias()).isEqualTo("sales_2");
    assertThat(second.suffixNumber()).isEqualTo(2);
    assertThat(FileAliasUtils.buildDisplayName("Sales.csv", second.suffixNumber()))
        .isEqualTo("sales_2.csv");
  }
}
