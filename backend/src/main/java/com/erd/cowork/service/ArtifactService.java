package com.erd.cowork.service;

import com.erd.cowork.artifact.ArtifactCdnRewriter;
import com.erd.cowork.artifact.ArtifactCdnRewriter.CompiledRule;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.storage.FileStorage;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

/** Thin service layer for artifact retrieval. */
@Slf4j
@Service
@RequiredArgsConstructor
@LogAnnotation
public class ArtifactService {

  private final ArtifactRepository artifacts;
  private final FileStorage fileStorage;
  private final ArtifactCdnRewriter cdnRewriter;
  private final ChatSessionRepository chatSessions;

  /**
   * Returns a {@link StreamingResponseBody} that streams the assembled HTML for the given artifact
   * with CDN URLs rewritten line-by-line to self-hosted vendor paths, using the rewrite rules of
   * the artifact's recorded asset profile.
   *
   * <p>Profile resolution (null/blank fallback to the legacy default profile, unknown-profile
   * fallback to current-profile rules) is fully delegated to {@link
   * ArtifactCdnRewriter#resolveRules(String, String)}, which never returns null.
   *
   * <p>The returned lambda executes later in a Spring async thread; only the captured primitive
   * values (storage key, rewrite rules) are read from it — no repository access happens there.
   *
   * @param artifactId artifact UUID
   * @return streaming body with CDN-rewritten HTML
   * @throws NotFoundException if no artifact with the given ID exists, or its htmlStorageKey is
   *     null
   */
  public StreamingResponseBody getHtmlStream(String artifactId) {
    Artifact artifact =
        artifacts
            .findById(artifactId)
            .orElseThrow(() -> new NotFoundException("Artifact not found: " + artifactId));
    assertOwnedByCaller(artifact);

    String storageKey = artifact.getHtmlStorageKey();

    if (storageKey == null) {
      throw new NotFoundException("Artifact has no stored HTML: " + artifactId);
    }

    List<CompiledRule> rewriteRules =
        cdnRewriter.resolveRules(artifact.getAssetProfile(), artifactId);
    return outputStream -> streamWithCdnRewrite(storageKey, rewriteRules, outputStream);
  }

  /**
   * Returns the raw (pre-assembly) HTML for the given artifact ID. CDN URLs are intentionally NOT
   * rewritten here so iterative prompts continue to reference original LLM-generated URLs.
   *
   * @param artifactId artifact UUID
   * @return raw HTML string (unmodified)
   * @throws NotFoundException if no artifact with the given ID exists or it has no stored HTML
   */
  public String getRawHtml(String artifactId) {
    Artifact artifact =
        artifacts
            .findById(artifactId)
            .orElseThrow(() -> new NotFoundException("Artifact not found: " + artifactId));
    assertOwnedByCaller(artifact);
    return loadRawHtml(artifact)
        .orElseThrow(() -> new NotFoundException("Artifact not found: " + artifactId));
  }

  /**
   * Ownership guard: an artifact belongs to its session's owner. A caller other than that owner is
   * treated as if the artifact does not exist (404, never 403) so artifact-id existence is never
   * leaked to non-owners. A missing session is likewise treated as not owned.
   *
   * @param artifact the artifact to check (never null)
   * @throws NotFoundException if the current caller does not own the artifact's session
   */
  private void assertOwnedByCaller(Artifact artifact) {
    String callerUserId = CoworkContextHolder.userId();
    boolean owned =
        chatSessions
            .findById(artifact.getSessionId())
            .map(session -> session.getUserId().equals(callerUserId))
            .orElse(false);
    if (!owned) {
      throw new NotFoundException("Artifact not found: " + artifact.getId());
    }
  }

  /**
   * Loads the raw model HTML for an artifact from {@link FileStorage}. Falls back to the assembled
   * file when no dedicated raw file exists — the deepagent line stores none because assemble
   * injects no data there (the assembled file differs only by serve-time head boilerplate).
   *
   * @param artifact the artifact entity (never null)
   * @return the raw HTML, or empty when the artifact has no stored HTML at all
   */
  public Optional<String> loadRawHtml(Artifact artifact) {
    String storageKey =
        artifact.getRawHtmlStorageKey() != null
            ? artifact.getRawHtmlStorageKey()
            : artifact.getHtmlStorageKey();
    if (storageKey == null) {
      return Optional.empty();
    }
    try (InputStream storageStream = fileStorage.read(storageKey)) {
      return Optional.of(new String(storageStream.readAllBytes(), StandardCharsets.UTF_8));
    } catch (IOException ioException) {
      throw new RuntimeException(
          "Failed to read raw HTML key=" + storageKey + " for artifact " + artifact.getId(),
          ioException);
    }
  }

  /**
   * Streams HTML from {@link FileStorage} to the output, rewriting CDN URLs line-by-line using the
   * supplied compiled rule set. CDN URLs never span lines, so per-line regex application is
   * correct.
   *
   * <p>The {@code InputStream} (and its {@link BufferedReader} wrapper) is closed via
   * try-with-resources. The {@link Writer} wrapping the Spring-managed {@code outputStream} is
   * intentionally NOT closed — only flushed — because closing it would close the response
   * OutputStream prematurely before Spring MVC finalises the response.
   */
  private void streamWithCdnRewrite(
      String storageKey, List<CompiledRule> rewriteRules, OutputStream outputStream)
      throws IOException {
    try (BufferedReader reader =
        new BufferedReader(
            new InputStreamReader(fileStorage.read(storageKey), StandardCharsets.UTF_8))) {
      Writer writer = new OutputStreamWriter(outputStream, StandardCharsets.UTF_8);
      boolean cdnRewritten = false;
      String line;
      while ((line = reader.readLine()) != null) {
        String rewrittenLine = cdnRewriter.rewriteLine(line, rewriteRules);
        if (!cdnRewritten && !rewrittenLine.equals(line)) {
          cdnRewritten = true;
        }
        writer.write(rewrittenLine);
        writer.write('\n');
      }
      writer.flush();
      if (cdnRewritten) {
        log.debug("Rewrote CDN URLs to vendor paths in artifact HTML");
      }
    }
  }
}
