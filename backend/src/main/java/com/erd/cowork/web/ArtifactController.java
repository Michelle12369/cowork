package com.erd.cowork.web;

import com.erd.cowork.agent.repair.BrowserJsError;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.service.ArtifactRepairService;
import com.erd.cowork.service.ArtifactService;
import com.erd.cowork.web.dto.RepairRequestDto;
import com.erd.cowork.web.dto.RepairResponseDto;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
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

  private final ArtifactService artifactService;
  private final ArtifactRepairService artifactRepairService;

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
  @LogAnnotation(args = true, maxArgsLength = 200)
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
  @LogAnnotation(args = true, maxArgsLength = 200)
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
  @LogAnnotation(args = true, maxArgsLength = 200)
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
}
