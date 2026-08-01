package com.erd.cowork.storage;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class StorageKeyUtilsTest {

  // ── sanitize ──────────────────────────────────────────────────────────────

  @Test
  void sanitize_plainFilename_returnedUnchanged() {
    assertThat(StorageKeyUtils.sanitize("data.csv")).isEqualTo("data.csv");
  }

  @Test
  void sanitize_pathTraversal_returnsBasenameOnly() {
    // The traversal components must be stripped; only the final segment remains.
    assertThat(StorageKeyUtils.sanitize("../../etc/passwd")).isEqualTo("passwd");
  }

  @Test
  void sanitize_windowsPathSeparator_returnsBasenameOnly() {
    // Backslash is treated as a path separator regardless of platform.
    assertThat(StorageKeyUtils.sanitize("folder\\file.csv")).isEqualTo("file.csv");
  }

  @Test
  void sanitize_windowsPathTraversal_returnsBasenameOnly() {
    assertThat(StorageKeyUtils.sanitize("..\\..\\etc\\passwd")).isEqualTo("passwd");
  }

  @Test
  void sanitize_controlCharacter_replacedWithUnderscore() {
    // NUL would make Paths.get throw InvalidPathException; string sanitizing must not.
    assertThat(StorageKeyUtils.sanitize("bad\u0000name.csv")).isEqualTo("bad_name.csv");
  }

  @Test
  void sanitize_specialChars_replacedWithUnderscore() {
    assertThat(StorageKeyUtils.sanitize("my:file*name?.csv")).isEqualTo("my_file_name_.csv");
  }

  @Test
  void sanitize_nullFilename_returnsUnnamed() {
    assertThat(StorageKeyUtils.sanitize(null)).isEqualTo("unnamed");
  }

  @Test
  void sanitize_emptyFilename_returnsUnnamed() {
    assertThat(StorageKeyUtils.sanitize("")).isEqualTo("unnamed");
  }

  @Test
  void sanitize_separatorOnlyFilename_returnsUnnamed() {
    assertThat(StorageKeyUtils.sanitize("dir/")).isEqualTo("unnamed");
  }

  // ── buildKey ──────────────────────────────────────────────────────────────

  @Test
  void buildKey_returnsCorrectFormat() {
    String key = StorageKeyUtils.buildKey(StorageCategory.UPLOAD, "sess-abc", "report.xlsx");
    assertThat(key).startsWith("uploads/sess-abc/");
    assertThat(key).endsWith("_report.xlsx");
    // Middle segment between "/" and "_report.xlsx" must be a valid UUID
    String uuidPart = key.substring("uploads/sess-abc/".length(), key.lastIndexOf("_report.xlsx"));
    assertThat(uuidPart).matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
  }

  @Test
  void buildKey_pathTraversalFilename_producedKeySafeForStorage() {
    String key = StorageKeyUtils.buildKey(StorageCategory.UPLOAD, "sess-1", "../../etc/passwd");
    assertThat(key).doesNotContain("..");
    assertThat(key).startsWith("uploads/sess-1/");
    assertThat(key).endsWith("_passwd");
  }

  @Test
  void buildKey_twoCallsWithSameArgs_returnDistinctKeys() {
    String firstKey = StorageKeyUtils.buildKey(StorageCategory.UPLOAD, "sess-1", "data.csv");
    String secondKey = StorageKeyUtils.buildKey(StorageCategory.UPLOAD, "sess-1", "data.csv");
    assertThat(firstKey).isNotEqualTo(secondKey);
  }

  @Test
  void buildKey_uploadCategory_prefixesWithUploads() {
    String key = StorageKeyUtils.buildKey(StorageCategory.UPLOAD, "session-1", "sales.csv");

    assertThat(key).startsWith("uploads/session-1/");
    assertThat(key).endsWith("_sales.csv");
  }

  @Test
  void buildKey_artifactCategory_prefixesWithArtifacts() {
    String key = StorageKeyUtils.buildKey(StorageCategory.ARTIFACT, "session-1", "abc.html");

    assertThat(key).startsWith("artifacts/session-1/");
  }

  @Test
  void buildKey_traversalFilename_stillReducedToBasenameUnderPrefix() {
    String key = StorageKeyUtils.buildKey(StorageCategory.UPLOAD, "session-1", "../../etc/passwd");

    assertThat(key).startsWith("uploads/session-1/");
    assertThat(key).endsWith("_passwd");
  }
}
