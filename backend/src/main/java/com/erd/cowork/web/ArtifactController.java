package com.erd.cowork.web;

import com.erd.cowork.agent.provider.analysis.AnalysisReplayClient;
import com.erd.cowork.agent.provider.analysis.AnalysisReplayOutcome;
import com.erd.cowork.agent.repair.BrowserJsError;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.exception.AnalysisReplayRejectedException;
import com.erd.cowork.exception.ConflictException;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.service.ArtifactRepairService;
import com.erd.cowork.service.ArtifactService;
import com.erd.cowork.web.dto.RefreshResponseDto;
import com.erd.cowork.web.dto.RepairRequestDto;
import com.erd.cowork.web.dto.RepairResponseDto;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.util.List;
import java.util.Optional;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StringUtils;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

/**
 * Serves assembled HTML dashboard artifacts.
 *
 * <p>Access is via an unguessable UUID path (capability URL). No {@code X-User-Id} check is
 * performed here — auth hardening deferred to a future milestone.
 */
@RestController
@RequestMapping("/api/artifacts")
@RequiredArgsConstructor
@Validated
@Slf4j
@Tag(name = "Artifacts", description = "Assembled HTML dashboard artifact delivery")
@LogAnnotation
public class ArtifactController {

  private static final String REFRESH_UNSUPPORTED_MESSAGE = "此版本不支援重新整理（無 API 資料源配方）";

  private final ArtifactService artifactService;
  private final ArtifactRepairService artifactRepairService;

  /**
   * Present only under the {@code langgraph-analysis} provider (spec §7) — {@link #refresh(String)}
   * treats an absent client the same as a missing recipe: the deployment simply does not support
   * recipe replay.
   */
  private final Optional<AnalysisReplayClient> analysisReplayClient;

  /**
   * Returns the full self-contained HTML dashboard for the given artifact as a stream.
   *
   * <p>HTML is streamed directly from {@link com.erd.cowork.storage.FileStorage} with CDN URLs
   * rewritten line-by-line to self-hosted vendor paths. The response body is never fully
   * materialised in heap. Unguessable UUID capability URL; auth hardening deferred.
   *
   * @param id artifact UUID
   * @return streaming self-contained HTML response
   */
  @GetMapping(value = "/{id}", produces = MediaType.TEXT_HTML_VALUE + ";charset=UTF-8")
  @Operation(
      summary = "Get artifact HTML dashboard",
      description =
          "Streams the assembled self-contained HTML dashboard for the given artifact ID."
              + " HTML is served directly from file storage with CDN URLs rewritten to"
              + " self-hosted vendor paths. Unguessable UUID capability URL; auth hardening"
              + " deferred.")
  @ApiResponse(responseCode = "200", description = "HTML dashboard streamed successfully")
  @ApiResponse(responseCode = "404", description = "Artifact not found or has no HTML content")
  @LogAnnotation(args = true)
  public ResponseEntity<StreamingResponseBody> getArtifact(@PathVariable String id) {
    StreamingResponseBody stream = artifactService.getHtmlStream(id);
    return ResponseEntity.ok()
        .contentType(MediaType.parseMediaType(MediaType.TEXT_HTML_VALUE + ";charset=UTF-8"))
        .body(stream);
  }

  /**
   * Returns the raw (pre-assembly) HTML for the given artifact as plain text.
   *
   * <p>Used by the in-chat HTML source viewer. Returns 404 when the artifact does not exist or has
   * no raw HTML content in {@link com.erd.cowork.storage.FileStorage}.
   *
   * @param id artifact UUID
   * @return raw HTML string
   */
  @GetMapping(value = "/{id}/raw", produces = "text/plain;charset=UTF-8")
  @Operation(
      summary = "Get raw artifact HTML",
      description =
          "Returns the raw (pre-assembly) HTML source for the given artifact ID as plain text."
              + " Returns 404 when the artifact does not exist or has no raw HTML.")
  @ApiResponse(responseCode = "200", description = "Raw HTML returned as text/plain")
  @ApiResponse(responseCode = "404", description = "Artifact not found or has no raw HTML")
  @LogAnnotation(args = true)
  public String getRawHtml(@PathVariable String id) {
    return artifactService.getRawHtml(id);
  }

  @PostMapping(value = "/{id}/repair", consumes = MediaType.APPLICATION_JSON_VALUE)
  @Operation(
      summary = "Repair artifact from browser errors",
      description =
          "Calls the LLM to repair an artifact that produced runtime JavaScript errors"
              + " in the browser. The artifact is repaired in-place. Each artifact may only"
              + " be auto-repaired once per client session (enforced client-side).")
  @ApiResponse(responseCode = "200", description = "Repair attempted; see 'repaired' field")
  @ApiResponse(
      responseCode = "400",
      description = "Validation failed — errors list empty or invalid")
  @ApiResponse(responseCode = "404", description = "Artifact not found or does not belong to user")
  @ApiResponse(
      responseCode = "409",
      description =
          "Artifact has no raw HTML to repair from, or the active agent provider does not"
              + " support browser repair")
  @LogAnnotation(args = true)
  public RepairResponseDto repair(
      @PathVariable String id, @Valid @RequestBody RepairRequestDto request) {
    log.info("POST repair artifact={} errorCount={}", id, request.errors().size());
    List<BrowserJsError> browserErrors =
        request.errors().stream()
            .map(dto -> new BrowserJsError(dto.message(), dto.line(), dto.col()))
            .toList();
    boolean repaired = artifactRepairService.repairFromBrowserErrors(id, browserErrors);
    return new RepairResponseDto(repaired);
  }

  /**
   * Replays the artifact's stored data-fetch recipe against upstream API sources and returns
   * freshly re-injected HTML.
   *
   * <p>This is a view-only operation (spec §7: 重新整理是視圖操作不是創作操作) — the returned HTML is transient
   * and never persisted; no new artifact version is created. Capability-URL access, same as the
   * rest of this controller: possession of the artifact id is the authorization.
   *
   * @param id artifact UUID
   * @return freshly re-injected HTML
   * @throws NotFoundException if the artifact does not exist, or has no stored HTML at all
   * @throws ConflictException if the artifact carries no recipe (upload-sourced, or a pre-replay
   *     version), or the deployment's active provider does not support recipe replay
   * @throws AnalysisReplayRejectedException if deepagent-service rejects the replay (mapped to 502
   *     by {@link com.erd.cowork.exception.GlobalExceptionHandler})
   */
  @PostMapping("/{id}/refresh")
  @Operation(
      summary = "Refresh artifact data from its recipe",
      description =
          "Replays the artifact's stored API data-fetch recipe (spec §7) against upstream"
              + " sources and returns freshly re-injected HTML. View-only — no new artifact"
              + " version is persisted.")
  @ApiResponse(responseCode = "200", description = "Replay succeeded; freshest HTML returned")
  @ApiResponse(responseCode = "404", description = "Artifact not found, or has no stored HTML")
  @ApiResponse(
      responseCode = "409",
      description =
          "Artifact has no data-fetch recipe (upload-sourced or a pre-replay version), or"
              + " recipe replay is not supported by the active provider")
  @ApiResponse(
      responseCode = "502",
      description =
          "deepagent-service rejected the replay (e.g. upstream source removed or schema"
              + " changed); its code/message are passed through")
  @LogAnnotation(args = true)
  public RefreshResponseDto refresh(@PathVariable String id) {
    Artifact artifact = artifactService.getArtifact(id);

    boolean hasRecipe = StringUtils.hasText(artifact.getRecipeJson());
    boolean uploadSourced = Boolean.TRUE.equals(artifact.getHasUploadSources());
    if (!hasRecipe || uploadSourced || analysisReplayClient.isEmpty()) {
      throw new ConflictException(REFRESH_UNSUPPORTED_MESSAGE);
    }

    String baseHtml =
        artifactService
            .loadRawHtml(artifact)
            .orElseThrow(() -> new NotFoundException("Artifact has no stored HTML: " + id));

    AnalysisReplayOutcome outcome =
        analysisReplayClient.get().replay(id, artifact.getRecipeJson(), baseHtml).block();

    if (outcome.isSuccess()) {
      return new RefreshResponseDto(outcome.html());
    }
    log.warn("refresh artifact={} rejected code={}", id, outcome.errorCode());
    throw new AnalysisReplayRejectedException(outcome.errorCode(), outcome.errorMessage());
  }
}
