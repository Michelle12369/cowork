package com.erd.cowork.artifact;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.model.ParsedRows;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.List;
import org.apache.velocity.app.VelocityEngine;
import org.apache.velocity.runtime.RuntimeConstants;
import org.apache.velocity.runtime.resource.loader.ClasspathResourceLoader;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class ArtifactAssemblerTest {

  /**
   * Appended to fixtures that must exercise the dashboard-mode (data-injection) path. Real
   * LLM-written dashboard HTML references {@code window.__ERD_DATA__} itself; these minimal
   * fixtures don't, so the marker is added explicitly wherever a test asserts on injected data.
   */
  private static final String DASHBOARD_MARKER = "<!--__ERD_DATA__-->";

  @Mock FileStorage fileStorage;
  @Mock FileParsingService fileParsingService;
  @Mock UploadedFileRepository uploadedFileRepository;

  ObjectMapper objectMapper = new ObjectMapper();

  ArtifactAssembler assembler;

  private static VelocityEngine buildVelocityEngine() {
    VelocityEngine engine = new VelocityEngine();
    engine.setProperty(RuntimeConstants.RESOURCE_LOADERS, "classpath");
    engine.setProperty("resource.loader.classpath.class", ClasspathResourceLoader.class.getName());
    engine.setProperty(RuntimeConstants.INPUT_ENCODING, "UTF-8");
    engine.init();
    return engine;
  }

  @BeforeEach
  void setUp() {
    VelocityEngine velocityEngine = buildVelocityEngine();
    assembler =
        new ArtifactAssembler(
            fileStorage, fileParsingService, uploadedFileRepository, objectMapper, velocityEngine);
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  private UploadedFile makeFile(String alias, String name, String storageKey) {
    UploadedFile uploadedFile = new UploadedFile();
    uploadedFile.setAlias(alias);
    uploadedFile.setName(name);
    uploadedFile.setStorageKey(storageKey);
    return uploadedFile;
  }

  /** Stubs fileStorage + fileParsingService so a single file produces the given rows. */
  private void stubReadAll(String storageKey, String col, String... rowValues) throws IOException {
    when(fileStorage.read(storageKey)).thenReturn(new ByteArrayInputStream(new byte[0]));
    List<List<String>> rows =
        java.util.Arrays.stream(rowValues)
            .map(List::of)
            .collect(java.util.stream.Collectors.toList());
    when(fileParsingService.readAll(anyString(), any()))
        .thenReturn(new ParsedRows(List.of(col), rows));
  }

  // ── injection position ────────────────────────────────────────────────────

  @Test
  void assemble_htmlWithHead_injectsScriptAfterHead() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble(
            "s1", "<html><head><title>T</title></head><body></body></html>" + DASHBOARD_MARKER);
    assertThat(result).contains("<head>\n<script>");
    assertThat(result).contains("<script>window.__ERD_DATA__");
  }

  @Test
  void assemble_lengthChangingUnicodeBeforeHead_injectsAtCorrectPosition() {
    // "İ" (U+0130) becomes two chars when lowercased — an index computed on a lowercased
    // copy would land the injection one char early per İ, corrupting the markup.
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble(
            "s1",
            "<html lang=\"tr\"><!-- İşlem --><head><title>T</title></head><body></body></html>"
                + DASHBOARD_MARKER);
    assertThat(result).contains("<head>\n<script>");
    assertThat(result).contains("<!-- İşlem --><head>");
  }

  @Test
  void assemble_htmlWithoutHead_prependsScript() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result = assembler.assemble("s1", "<p>dash</p>" + DASHBOARD_MARKER);
    assertThat(result).startsWith("<script>");
    assertThat(result).contains("<script>window.__ERD_DATA__");
    assertThat(result).contains("<p>dash</p>");
  }

  @Test
  void assemble_headTagCaseInsensitive_recognizesUpperCase() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble(
            "s1", "<html><HEAD><title>T</title></HEAD><body/></html>" + DASHBOARD_MARKER);
    assertThat(result).contains("<HEAD>\n<script>");
    assertThat(result).contains("<script>window.__ERD_DATA__");
  }

  @Test
  void assemble_htmlWithHeaderButNoHead_prependsScript() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble("s1", "<div><header>Nav</header><p>x</p></div>" + DASHBOARD_MARKER);
    assertThat(result).startsWith("<script>");
    assertThat(result).contains("<script>window.__ERD_DATA__");
    assertThat(result).doesNotContain("<header>\n<script>");
  }

  @Test
  void assemble_truncatedHeadTag_prependsScript() {
    // HTML where <head is present but its closing '>' is missing — truncated input
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result = assembler.assemble("s1", "<html><head" + DASHBOARD_MARKER);
    // Must fall back to prepend, not insert mid-token
    assertThat(result).startsWith("<script>");
    assertThat(result).contains("<script>window.__ERD_DATA__");
    assertThat(result).contains("<html><head");
  }

  // ── </script> escaping ────────────────────────────────────────────────────

  @Test
  void assemble_scriptTagInData_escapesClosingTag() throws IOException {
    UploadedFile f = makeFile("file1", "data.csv", "key1");
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of(f));
    when(fileStorage.read("key1")).thenReturn(new ByteArrayInputStream(new byte[0]));
    when(fileParsingService.readAll(anyString(), any()))
        .thenReturn(new ParsedRows(List.of("code"), List.of(List.of("</script>alert(1)"))));

    String result = assembler.assemble("s1", "<p>x</p>" + DASHBOARD_MARKER);
    // The raw closing tag must be escaped so it cannot terminate the script block early
    assertThat(result).doesNotContain("</script>alert");
    assertThat(result).contains("<\\/script>alert");
  }

  // ── alias key ─────────────────────────────────────────────────────────────

  @Test
  void assemble_fileWithAlias_usesAliasAsKeyInDataMap() throws IOException {
    UploadedFile f = makeFile("file3", "metrics.csv", "key3");
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of(f));
    stubReadAll("key3", "metric", "v1", "v2");

    String result = assembler.assemble("s1", "<p/>" + DASHBOARD_MARKER);
    assertThat(result).contains("\"file3\"");
    assertThat(result).doesNotContain("\"file1\"");
  }

  // ── data entry shape ──────────────────────────────────────────────────────

  @Test
  void assemble_twoRowFile_entryHasColumnsRowsTotalRows() throws IOException {
    UploadedFile f = makeFile("file1", "data.csv", "k1");
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of(f));
    when(fileStorage.read("k1")).thenReturn(new ByteArrayInputStream(new byte[0]));
    when(fileParsingService.readAll(anyString(), any()))
        .thenReturn(
            new ParsedRows(
                List.of("col1", "col2"),
                List.of(List.of("r1c1", "r1c2"), List.of("r2c1", "r2c2"))));

    String result = assembler.assemble("s1", "<p/>" + DASHBOARD_MARKER);
    assertThat(result).contains("\"columns\":[\"col1\",\"col2\"]");
    assertThat(result).contains("[\"r1c1\",\"r1c2\"]");
    assertThat(result).doesNotContain("\"sampled\"");
    assertThat(result).contains("\"totalRows\":2");
  }

  // ── single-pass read ─────────────────────────────────────────────────────

  @Test
  void assemble_file_readAllCalledOnce() throws IOException {
    UploadedFile f = makeFile("file1", "data.csv", "key1");
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of(f));
    stubReadAll("key1", "col", "v1");

    assembler.assemble("s1", "<p/>" + DASHBOARD_MARKER);
    // Full scan: fileStorage.read called exactly once per file (no profile pre-pass)
    verify(fileStorage, times(1)).read("key1");
  }

  @Test
  void assemble_totalRows_derivedFromRowCount() throws IOException {
    UploadedFile f = makeFile("file1", "data.csv", "key1");
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of(f));
    stubReadAll("key1", "col", "v1", "v2", "v3");

    String result = assembler.assemble("s1", "<p/>" + DASHBOARD_MARKER);
    assertThat(result).contains("\"totalRows\":3");
    verify(fileStorage, times(1)).read("key1");
  }

  // ── single file failure is skipped ───────────────────────────────────────

  @Test
  void assemble_singleFileFails_otherFileStillIncluded() throws IOException {
    UploadedFile f1 = makeFile("file1", "bad.csv", "key1");
    UploadedFile f2 = makeFile("file2", "good.csv", "key2");
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of(f1, f2));

    when(fileStorage.read("key1")).thenThrow(new IOException("storage error"));
    when(fileStorage.read("key2")).thenReturn(new ByteArrayInputStream(new byte[0]));
    when(fileParsingService.readAll(anyString(), any()))
        .thenReturn(new ParsedRows(List.of("val"), List.of(List.of("ok"))));

    String result = assembler.assemble("s1", "<p/>" + DASHBOARD_MARKER);
    assertThat(result).contains("\"file2\"");
    assertThat(result).doesNotContain("\"file1\"");
  }

  // ── empty file list ───────────────────────────────────────────────────────

  @Test
  void assemble_noFiles_emitsEmptyDataMap() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result = assembler.assemble("s1", "<p/>" + DASHBOARD_MARKER);
    assertThat(result).contains("window.__ERD_DATA__ = {}");
  }

  // ── font style injection ──────────────────────────────────────────────────

  @Test
  void assemble_htmlWithHead_injectsFontStyleBlock() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble(
            "s1", "<html><head><title>T</title></head><body></body></html>" + DASHBOARD_MARKER);
    assertThat(result).contains("@font-face");
    assertThat(result).contains("Inter Variable");
    assertThat(result).contains("font-family:'Inter Variable'");
  }

  @Test
  void assemble_htmlWithHead_injectsFontFamilyOnBody() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble(
            "s1", "<html><head><title>T</title></head><body></body></html>" + DASHBOARD_MARKER);
    assertThat(result).contains("body{font-family:'Inter Variable'");
    assertThat(result).contains("PingFang TC");
  }

  @Test
  void assemble_htmlWithoutHead_prependsFontStyleBlock() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result = assembler.assemble("s1", "<p>dash</p>" + DASHBOARD_MARKER);
    assertThat(result).startsWith("<script>");
    assertThat(result).contains("@font-face");
  }

  @Test
  void assemble_fontStyleBlock_pointsToCorrectFontPaths() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result = assembler.assemble("s1", "<p/>" + DASHBOARD_MARKER);
    assertThat(result).contains("/fonts/inter-latin-wght-normal.woff2");
    assertThat(result).contains("/fonts/inter-latin-ext-wght-normal.woff2");
  }

  // ── ECharts 'erd' theme injection ────────────────────────────────────────

  @Test
  void assemble_htmlWithEcharts_injectsErdTheme() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    // HTML that loads ECharts CDN triggers theme injection
    String html =
        "<html><head></head><body>"
            + "<script src=\"https://cdn.jsdelivr.net/npm/echarts@5\"></script>"
            + "</body></html>"
            + DASHBOARD_MARKER;
    String result = assembler.assemble("s1", html);
    assertThat(result).contains("echarts.registerTheme('erd'");
    assertThat(result).contains("#2a78d6");
    // DOMContentLoaded guard ensures registration fires after CDN loads
    assertThat(result).contains("DOMContentLoaded");
    assertThat(result).contains("registerErdTheme");
    // Brace pinning: '}' closing registerErdTheme() must precede the top-level guard `if`,
    // otherwise the IIFE never closes and the whole theme script is a SyntaxError.
    assertThat(result).contains("}}});}if(window.echarts)");
  }

  @Test
  void assemble_htmlWithoutEcharts_noThemeInjected() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    // Plain HTML with no ECharts reference must NOT get the theme block
    String result = assembler.assemble("s1", "<p>no charts here</p>" + DASHBOARD_MARKER);
    assertThat(result).doesNotContain("registerTheme");
    assertThat(result).contains("window.__ERD_DATA__");
  }

  @Test
  void assemble_echartsTheme_appearsBeforeDataScript() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    // Theme script must be injected before the __ERD_DATA__ data script
    String result =
        assembler.assemble(
            "s1", "<html><head></head><body>echarts</body></html>" + DASHBOARD_MARKER);
    int themeIdx = result.indexOf("registerTheme");
    int dataIdx = result.indexOf("__ERD_DATA__");
    assertThat(themeIdx).isGreaterThan(0);
    assertThat(themeIdx).isLessThan(dataIdx);
  }

  // ── error capture script ─────────────────────────────────────────────────

  @Test
  void assemble_errorCaptureScript_containsErdArtifactErrorType() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble("s1", "<html><head></head><body></body></html>" + DASHBOARD_MARKER);
    assertThat(result).contains("erd-artifact-error");
  }

  @Test
  void assemble_errorCaptureScript_appearsBeforeErdThemeAndDataScript() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble(
            "s1", "<html><head></head><body>echarts</body></html>" + DASHBOARD_MARKER);
    int errorIdx = result.indexOf("erd-artifact-error");
    int themeIdx = result.indexOf("registerErdTheme");
    int dataIdx = result.indexOf("__ERD_DATA__");
    assertThat(errorIdx).isGreaterThan(0);
    assertThat(errorIdx).isLessThan(themeIdx);
    assertThat(errorIdx).isLessThan(dataIdx);
  }

  // ── raw-html fallback on inject failure ──────────────────────────────────

  @Test
  void assemble_injectThrows_returnsRawHtml() throws Exception {
    // Marker must be present so serialization is actually attempted (and throws) below.
    String rawHtml = "<p>original dashboard</p>" + DASHBOARD_MARKER;
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());

    ObjectMapper spyMapper = spy(new ObjectMapper());
    doThrow(new com.fasterxml.jackson.core.JsonProcessingException("boom") {})
        .when(spyMapper)
        .writeValueAsString(org.mockito.ArgumentMatchers.any());

    ArtifactAssembler brokenAssembler =
        new ArtifactAssembler(
            fileStorage,
            fileParsingService,
            uploadedFileRepository,
            spyMapper,
            buildVelocityEngine());

    String result = brokenAssembler.assemble("s1", rawHtml);
    assertThat(result).isEqualTo(rawHtml);
  }

  // ── template content verification ─────────────────────────────────────────

  @Test
  void assemble_output_containsCssHexColors() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String html =
        "<html><head></head><body>"
            + "<script src=\"https://cdn.jsdelivr.net/npm/echarts@5\"></script>"
            + "</body></html>"
            + DASHBOARD_MARKER;
    String result = assembler.assemble("s1", html);
    assertThat(result).contains("#2a78d6");
    assertThat(result).contains("#CBD5E1");
  }

  @Test
  void assemble_output_containsErrorCaptureMarkers() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble("s1", "<html><head></head><body></body></html>" + DASHBOARD_MARKER);
    assertThat(result).contains("erd-artifact-error");
    assertThat(result).contains("unhandledrejection");
  }

  @Test
  void assemble_output_containsFontFaceAndStack() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble("s1", "<html><head></head><body></body></html>" + DASHBOARD_MARKER);
    assertThat(result).contains("Inter Variable");
    assertThat(result).contains("PingFang TC");
    assertThat(result).contains("/fonts/inter-latin-ext-wght-normal.woff2");
    assertThat(result).contains("/fonts/inter-latin-wght-normal.woff2");
  }

  @Test
  void assemble_output_dataScriptWellFormed() {
    when(uploadedFileRepository.findBySessionIdAndExpiredFalse("s1")).thenReturn(List.of());
    String result =
        assembler.assemble("s1", "<html><head></head><body></body></html>" + DASHBOARD_MARKER);
    assertThat(result).contains("window.__ERD_DATA__ = {");
    // Adjacency pins the template's no-trailing-newline property: a newline sneaking into the
    // end of head-inject.vm would break this exact-substring match.
    assertThat(result).contains(";</script></head>");
  }

  // ── analysis-mode: marker absent → data injection skipped ─────────────────
  //
  // head-inject.vm carries four parts: (1) error-capture script, (2) font-face style block,
  // (3) ECharts 'erd' theme script — gated on the ECHARTS_MARKER, unrelated to data — and
  // (4) the window.__ERD_DATA__ data script. Only part (4) is data; parts (1)-(3) are
  // theme/vendor concerns and stay unconditional regardless of the data marker.

  @Test
  void assemble_htmlWithoutDataMarker_dataScriptNotInjected() {
    String result =
        assembler.assemble("s1", "<html><head></head><body>self-contained</body></html>");
    assertThat(result).doesNotContain("__ERD_DATA__");
  }

  @Test
  void assemble_htmlWithoutDataMarker_skipsFileRepositoryScan() {
    assembler.assemble("s1", "<html><head></head><body>self-contained</body></html>");
    // No marker → no reason to touch the DB or read any file content.
    verify(uploadedFileRepository, never()).findBySessionIdAndExpiredFalse(anyString());
    verifyNoInteractions(fileStorage);
  }

  @Test
  void assemble_htmlWithoutDataMarker_nonDataPartsStillInjected() {
    String result =
        assembler.assemble("s1", "<html><head></head><body>self-contained</body></html>");
    // Error-capture and font-face parts are unconditional — still injected without the marker.
    assertThat(result).contains("erd-artifact-error");
    assertThat(result).contains("@font-face");
    assertThat(result).contains("Inter Variable");
  }

  @Test
  void assemble_htmlWithoutDataMarkerButWithEcharts_themeInjectedDataSkipped() {
    String result =
        assembler.assemble("s1", "<html><head></head><body>echarts chart here</body></html>");
    // Theme gating is independent of the data marker — echarts marker alone still triggers it.
    assertThat(result).contains("registerTheme");
    assertThat(result).doesNotContain("__ERD_DATA__");
  }

  // ── null / blank html edge cases ───────────────────────────────────────────
  //
  // Pinned outward behavior: assemble() never throws to the caller for these inputs. The marker
  // check itself is null-safe, so the DB scan is now additionally skipped for both (a new,
  // intended efficiency side-effect of the feature, not a change to the returned value).

  @Test
  void assemble_nullHtml_returnsNullAndSkipsRepositoryScan() {
    String result = assembler.assemble("s1", null);
    assertThat(result).isNull();
    verify(uploadedFileRepository, never()).findBySessionIdAndExpiredFalse(anyString());
  }

  @Test
  void assemble_blankHtml_prependsNonDataPartsWithoutThrowing() {
    String result = assembler.assemble("s1", "");
    assertThat(result).isNotNull();
    assertThat(result).contains("erd-artifact-error");
    assertThat(result).doesNotContain("__ERD_DATA__");
  }
}
