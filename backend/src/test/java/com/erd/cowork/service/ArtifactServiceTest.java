package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.when;

import com.erd.cowork.artifact.ArtifactCdnRewriter;
import com.erd.cowork.config.ArtifactRewriteProperties;
import com.erd.cowork.config.ArtifactRewriteProperties.RewriteRule;
import com.erd.cowork.context.CoworkContext;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.storage.FileStorage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

@ExtendWith(MockitoExtension.class)
class ArtifactServiceTest {

  private static final String DEFAULT_OWNER_USER_ID = "owner-user";

  @Mock ArtifactRepository artifacts;
  @Mock FileStorage fileStorage;
  @Mock ChatSessionRepository chatSessions;

  ArtifactCdnRewriter cdnRewriter;
  ArtifactService service;

  @BeforeEach
  void setUp() {
    cdnRewriter = buildRewriter(standardTw3Ec5Properties());
    service = new ArtifactService(artifacts, fileStorage, cdnRewriter, chatSessions);
    // Default: every artifact's session (regardless of sessionId, including null/unset) is
    // owned by DEFAULT_OWNER_USER_ID, and the caller is that owner. This keeps every
    // pre-existing test — which predates ownership checks and never sets a sessionId — green
    // without touching each test's Artifact setup. Tests exercising the ownership guard itself
    // override the caller context to a different user.
    lenient()
        .when(chatSessions.findById(any()))
        .thenReturn(Optional.of(chatSessionOwnedBy(DEFAULT_OWNER_USER_ID)));
    CoworkContextHolder.set(CoworkContext.external(DEFAULT_OWNER_USER_ID));
  }

  @AfterEach
  void tearDown() {
    CoworkContextHolder.clear();
  }

  // ── getHtmlStream: profile=tw3-ec5 (storage path) ─────────────────────────

  @Test
  void getHtmlStream_storageKey_tailwindCdnRewrittenToVendorPath() throws IOException {
    String line = "<script src=\"https://cdn.tailwindcss.com\"></script>";
    Artifact artifact = artifactWithStorageKey("key-1");
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("key-1"))
        .thenReturn(new ByteArrayInputStream(line.getBytes(StandardCharsets.UTF_8)));

    String result = collectStream(service.getHtmlStream("art-1"));

    assertThat(result).contains("/vendor/tailwind-play-v3.js");
    assertThat(result).doesNotContain("cdn.tailwindcss.com");
  }

  @Test
  void getHtmlStream_storageKey_eChartsCdnRewrittenToVendorPath() throws IOException {
    String line =
        "<script src=\"https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js\"></script>";
    Artifact artifact = artifactWithStorageKey("key-2");
    when(artifacts.findById("art-2")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("key-2"))
        .thenReturn(new ByteArrayInputStream(line.getBytes(StandardCharsets.UTF_8)));

    String result = collectStream(service.getHtmlStream("art-2"));

    assertThat(result).contains("/vendor/echarts-v5.min.js");
    assertThat(result).doesNotContain("cdn.jsdelivr.net");
  }

  @Test
  void getHtmlStream_storageKey_bothCdnUrlsRewrittenAcrossMultipleLines() throws IOException {
    String html =
        "<script src=\"https://cdn.tailwindcss.com?plugins=forms\"></script>\n"
            + "<p>content</p>\n"
            + "<script"
            + " src=\"https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js\"></script>";
    Artifact artifact = artifactWithStorageKey("key-3");
    when(artifacts.findById("art-3")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("key-3"))
        .thenReturn(new ByteArrayInputStream(html.getBytes(StandardCharsets.UTF_8)));

    String result = collectStream(service.getHtmlStream("art-3"));

    assertThat(result).contains("/vendor/tailwind-play-v3.js");
    assertThat(result).contains("<p>content</p>");
    assertThat(result).contains("/vendor/echarts-v5.min.js");
    assertThat(result).doesNotContain("cdn.tailwindcss.com");
    assertThat(result).doesNotContain("cdn.jsdelivr.net");
  }

  @Test
  void getHtmlStream_storageKey_noCdnUrls_contentUnchanged() throws IOException {
    String html = "<html><body><h1>Hello</h1></body></html>";
    Artifact artifact = artifactWithStorageKey("key-4");
    when(artifacts.findById("art-4")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("key-4"))
        .thenReturn(new ByteArrayInputStream(html.getBytes(StandardCharsets.UTF_8)));

    String result = collectStream(service.getHtmlStream("art-4"));

    assertThat(result).contains("<html>");
    assertThat(result).contains("<h1>Hello</h1>");
  }

  // Profile fallback semantics (null/blank → legacy default, unknown → current-profile rules)
  // are covered by ArtifactCdnRewriterTest; service tests only verify that the rules resolved by
  // the rewriter are applied to the stream.

  // ── getHtmlStream: second profile uses its own independent rules ───────────

  @Test
  void getHtmlStream_secondProfile_appliesItsOwnRulesNotTw3Rules() throws IOException {
    // Build a rewriter that knows two profiles: tw3-ec5 and a hypothetical tw4-ec5.
    ArtifactRewriteProperties twoProfileProperties =
        new ArtifactRewriteProperties(
            "tw3-ec5",
            Map.of(
                "tw3-ec5",
                List.of(
                    new RewriteRule(
                        "https://cdn\\.tailwindcss\\.com[^\"']*", "/vendor/tailwind-play-v3.js"),
                    new RewriteRule(
                        "https://cdn\\.jsdelivr\\.net/npm/echarts@5[^\"']*",
                        "/vendor/echarts-v5.min.js")),
                "tw4-ec5",
                List.of(
                    new RewriteRule(
                        "https://example\\.com/cdn/tw4[^\"']*", "/vendor/tailwind-v4.js"))));
    ArtifactService twoProfileService =
        new ArtifactService(
            artifacts, fileStorage, buildRewriter(twoProfileProperties), chatSessions);

    // This line contains a tw4-specific CDN URL that should be rewritten.
    String html =
        "<script src=\"https://example.com/cdn/tw4/tailwind.css\"></script>\n"
            + "<script src=\"https://cdn.tailwindcss.com\"></script>";
    Artifact artifact = new Artifact();
    artifact.setHtmlStorageKey("key-tw4");
    artifact.setAssetProfile("tw4-ec5");
    when(artifacts.findById("art-tw4")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("key-tw4"))
        .thenReturn(new ByteArrayInputStream(html.getBytes(StandardCharsets.UTF_8)));

    String result = collectStream(twoProfileService.getHtmlStream("art-tw4"));

    // tw4-specific URL is rewritten by the tw4 profile rule.
    assertThat(result).contains("/vendor/tailwind-v4.js");
    assertThat(result).doesNotContain("example.com/cdn/tw4");
    // tw3 CDN URL is NOT rewritten because tw4-ec5 profile has no such rule.
    assertThat(result).contains("cdn.tailwindcss.com");
    assertThat(result).doesNotContain("/vendor/tailwind-play-v3.js");
  }

  // ── getHtmlStream: legacy CLOB-only artifacts now return 404 ──────────────

  @Test
  void getHtmlStream_noStorageKey_tailwindLegacy_throwsNotFoundException() {
    // Pre-V9 artifacts that have no htmlStorageKey must surface as 404.
    Artifact artifact = new Artifact(); // htmlStorageKey=null
    when(artifacts.findById("legacy-1")).thenReturn(Optional.of(artifact));

    assertThatThrownBy(() -> service.getHtmlStream("legacy-1"))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining("legacy-1");
  }

  @Test
  void getHtmlStream_noStorageKey_eChartsLegacy_throwsNotFoundException() {
    Artifact artifact = new Artifact(); // htmlStorageKey=null
    when(artifacts.findById("legacy-2")).thenReturn(Optional.of(artifact));

    assertThatThrownBy(() -> service.getHtmlStream("legacy-2"))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining("legacy-2");
  }

  @Test
  void getHtmlStream_noStorageKey_plainLegacy_throwsNotFoundException() {
    Artifact artifact = new Artifact(); // htmlStorageKey=null
    when(artifacts.findById("legacy-3")).thenReturn(Optional.of(artifact));

    assertThatThrownBy(() -> service.getHtmlStream("legacy-3"))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining("legacy-3");
  }

  // ── getHtmlStream: error paths ─────────────────────────────────────────────

  @Test
  void getHtmlStream_unknownId_throwsNotFoundException() {
    when(artifacts.findById("missing")).thenReturn(Optional.empty());

    assertThatThrownBy(() -> service.getHtmlStream("missing"))
        .isInstanceOf(NotFoundException.class);
  }

  @Test
  void getHtmlStream_nullStorageKey_throwsNotFoundException() {
    Artifact artifact = new Artifact(); // htmlStorageKey=null
    when(artifacts.findById("no-html")).thenReturn(Optional.of(artifact));

    assertThatThrownBy(() -> service.getHtmlStream("no-html"))
        .isInstanceOf(NotFoundException.class)
        .hasMessageContaining("no-html");
  }

  // ── getRawHtml: no rewriting ───────────────────────────────────────────────

  @Test
  void getRawHtml_htmlContainsCdnUrls_returnedUnchanged() throws IOException {
    String rawHtml =
        "<script src=\"https://cdn.tailwindcss.com\"></script><script"
            + " src=\"https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js\"></script>";
    Artifact artifact = new Artifact();
    artifact.setRawHtmlStorageKey("artifacts/s1/raw-1.raw.html");
    when(artifacts.findById("raw-1")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("artifacts/s1/raw-1.raw.html"))
        .thenReturn(new ByteArrayInputStream(rawHtml.getBytes(StandardCharsets.UTF_8)));

    String result = service.getRawHtml("raw-1");

    assertThat(result).isEqualTo(rawHtml);
    assertThat(result).contains("cdn.tailwindcss.com");
    assertThat(result).contains("cdn.jsdelivr.net");
  }

  @Test
  void getRawHtml_unknownId_throwsNotFoundException() {
    when(artifacts.findById("missing")).thenReturn(Optional.empty());

    assertThatThrownBy(() -> service.getRawHtml("missing")).isInstanceOf(NotFoundException.class);
  }

  @Test
  void getRawHtml_rawKeyPresent_readsRawFile() throws IOException {
    Artifact artifact = new Artifact();
    artifact.setRawHtmlStorageKey("artifacts/s1/uuid_a.raw.html");
    artifact.setHtmlStorageKey("artifacts/s1/uuid_a.html");
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("artifacts/s1/uuid_a.raw.html"))
        .thenReturn(new ByteArrayInputStream("<html>raw</html>".getBytes(StandardCharsets.UTF_8)));

    assertThat(service.getRawHtml("art-1")).isEqualTo("<html>raw</html>");
  }

  @Test
  void getRawHtml_rawKeyNull_fallsBackToAssembledFile() throws IOException {
    Artifact artifact = new Artifact();
    artifact.setHtmlStorageKey("artifacts/s1/uuid_a.html");
    when(artifacts.findById("art-1")).thenReturn(Optional.of(artifact));
    when(fileStorage.read("artifacts/s1/uuid_a.html"))
        .thenReturn(
            new ByteArrayInputStream("<html>assembled</html>".getBytes(StandardCharsets.UTF_8)));

    assertThat(service.getRawHtml("art-1")).isEqualTo("<html>assembled</html>");
  }

  @Test
  void getRawHtml_bothKeysNull_throwsNotFound() {
    when(artifacts.findById("art-1")).thenReturn(Optional.of(new Artifact()));

    assertThatThrownBy(() -> service.getRawHtml("art-1")).isInstanceOf(NotFoundException.class);
  }

  // ── ownership guard: non-owner access is indistinguishable from not-found ──

  @Test
  void getHtmlStream_artifactOwnedByAnotherUser_throwsNotFound() {
    Artifact artifact = artifactWithStorageKey("key-owned");
    artifact.setSessionId("session-owned-1");
    when(artifacts.findById("art-owned-1")).thenReturn(Optional.of(artifact));
    when(chatSessions.findById("session-owned-1"))
        .thenReturn(Optional.of(chatSessionOwnedBy(DEFAULT_OWNER_USER_ID)));
    CoworkContextHolder.set(CoworkContext.external("other-user"));

    assertThatThrownBy(() -> service.getHtmlStream("art-owned-1"))
        .isInstanceOf(NotFoundException.class);
  }

  @Test
  void getRawHtml_artifactOwnedByAnotherUser_throwsNotFound() {
    Artifact artifact = new Artifact();
    artifact.setHtmlStorageKey("artifacts/s1/art-owned-2.html");
    artifact.setSessionId("session-owned-2");
    when(artifacts.findById("art-owned-2")).thenReturn(Optional.of(artifact));
    when(chatSessions.findById("session-owned-2"))
        .thenReturn(Optional.of(chatSessionOwnedBy(DEFAULT_OWNER_USER_ID)));
    CoworkContextHolder.set(CoworkContext.external("other-user"));

    assertThatThrownBy(() -> service.getRawHtml("art-owned-2"))
        .isInstanceOf(NotFoundException.class);
  }

  @Test
  void getHtmlStream_ownArtifact_returnsStream() throws IOException {
    String html = "<html><body>owned</body></html>";
    Artifact artifact = artifactWithStorageKey("key-owned-3");
    artifact.setSessionId("session-owned-3");
    when(artifacts.findById("art-owned-3")).thenReturn(Optional.of(artifact));
    when(chatSessions.findById("session-owned-3"))
        .thenReturn(Optional.of(chatSessionOwnedBy(DEFAULT_OWNER_USER_ID)));
    when(fileStorage.read("key-owned-3"))
        .thenReturn(new ByteArrayInputStream(html.getBytes(StandardCharsets.UTF_8)));
    CoworkContextHolder.set(CoworkContext.external(DEFAULT_OWNER_USER_ID));

    String result = collectStream(service.getHtmlStream("art-owned-3"));

    assertThat(result).contains("owned");
  }

  @Test
  void getHtmlStream_artifactSessionMissing_throwsNotFound() {
    Artifact artifact = artifactWithStorageKey("key-orphan");
    artifact.setSessionId("session-deleted");
    when(artifacts.findById("art-orphan")).thenReturn(Optional.of(artifact));
    when(chatSessions.findById("session-deleted")).thenReturn(Optional.empty());
    CoworkContextHolder.set(CoworkContext.external(DEFAULT_OWNER_USER_ID));

    assertThatThrownBy(() -> service.getHtmlStream("art-orphan"))
        .isInstanceOf(NotFoundException.class);
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  private static ArtifactRewriteProperties standardTw3Ec5Properties() {
    return new ArtifactRewriteProperties(
        "tw3-ec5",
        Map.of(
            "tw3-ec5",
            List.of(
                new RewriteRule(
                    "https://cdn\\.tailwindcss\\.com[^\"']*", "/vendor/tailwind-play-v3.js"),
                new RewriteRule(
                    "https://cdn\\.jsdelivr\\.net/npm/echarts@5[^\"']*",
                    "/vendor/echarts-v5.min.js"))));
  }

  /** Builds a fully initialised {@link ArtifactCdnRewriter} for use in unit tests. */
  private static ArtifactCdnRewriter buildRewriter(ArtifactRewriteProperties properties) {
    ArtifactCdnRewriter rewriter = new ArtifactCdnRewriter(properties);
    rewriter.init();
    return rewriter;
  }

  private static Artifact artifactWithStorageKey(String key) {
    Artifact artifact = new Artifact();
    artifact.setHtmlStorageKey(key);
    return artifact;
  }

  /** Builds a {@link ChatSession} stub owned by the given user for ownership-guard stubbing. */
  private static ChatSession chatSessionOwnedBy(String userId) {
    ChatSession session = new ChatSession();
    session.setUserId(userId);
    return session;
  }

  /** Collects the full output of a {@link StreamingResponseBody} into a String. */
  private static String collectStream(StreamingResponseBody stream) throws IOException {
    ByteArrayOutputStream baos = new ByteArrayOutputStream();
    stream.writeTo(baos);
    return baos.toString(StandardCharsets.UTF_8);
  }
}
