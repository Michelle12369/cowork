package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.context.CurrentUser;
import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.service.ArtifactService;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * Slice test for {@link ArtifactController}. Uses {@code @WebMvcTest} to avoid booting the full
 * Spring context. {@link CurrentUser} is imported explicitly because it's a plain
 * {@code @Component} that {@code @WebMvcTest} does not auto-detect; {@link CurrentUserFilter} is a
 * {@code Filter}, which {@code @WebMvcTest} does auto-detect, but its constructor needs {@link
 * CurrentUser}, so both are imported together (it's also imported explicitly here for clarity
 * rather than relying on that auto-detection). {@link
 * com.erd.cowork.exception.GlobalExceptionHandler} is picked up automatically as a
 * {@code @RestControllerAdvice} by the web slice scan.
 *
 * <p>GET /{id} returns {@code ResponseEntity<StreamingResponseBody>}, so 200 responses require the
 * two-step MockMvc async-dispatch pattern: first perform the request and assert async started, then
 * perform {@link MockMvcRequestBuilders#asyncDispatch} to collect the streamed body. Synchronous
 * error responses (404) do not require async dispatch.
 */
@WebMvcTest(ArtifactController.class)
@Import({CurrentUser.class, CurrentUserFilter.class})
class ArtifactControllerTest {

  @Autowired MockMvc mockMvc;
  @Autowired CurrentUser currentUser;

  @MockitoBean ArtifactService artifactService;
  @MockitoBean com.erd.cowork.service.ArtifactRepairService artifactRepairService;

  // ── GET /{id} — streaming response ────────────────────────────────────────

  @Test
  void getArtifact_existingId_returns200WithHtmlBody() throws Exception {
    String html = "<html><body>Dashboard</body></html>";
    when(artifactService.getHtmlStream("test-id"))
        .thenReturn(out -> out.write(html.getBytes(StandardCharsets.UTF_8)));

    MvcResult asyncResult =
        mockMvc
            .perform(get("/api/artifacts/test-id").header("X-User-Id", "test-user"))
            .andExpect(request().asyncStarted())
            .andReturn();

    mockMvc
        .perform(asyncDispatch(asyncResult))
        .andExpect(status().isOk())
        .andExpect(content().contentType("text/html;charset=UTF-8"))
        .andExpect(content().string(html));
  }

  @Test
  void getArtifact_noUserIdHeader_returns200() throws Exception {
    String html = "<html><body>Capability</body></html>";
    when(artifactService.getHtmlStream("cap-id"))
        .thenReturn(out -> out.write(html.getBytes(StandardCharsets.UTF_8)));

    // No X-User-Id header — confirms endpoint is accessible without it (capability URL semantics).
    MvcResult asyncResult =
        mockMvc
            .perform(get("/api/artifacts/cap-id"))
            .andExpect(request().asyncStarted())
            .andReturn();

    mockMvc
        .perform(asyncDispatch(asyncResult))
        .andExpect(status().isOk())
        .andExpect(content().string(html));
  }

  @Test
  void getArtifact_unknownId_returns404WithJsonBody() throws Exception {
    // NotFoundException is thrown synchronously in the service (before StreamingResponseBody is
    // constructed), so GlobalExceptionHandler handles it without async dispatch.
    when(artifactService.getHtmlStream(anyString()))
        .thenThrow(
            new NotFoundException("Artifact not found: 00000000-0000-0000-0000-000000000000"));

    mockMvc
        .perform(get("/api/artifacts/00000000-0000-0000-0000-000000000000"))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }

  // ── GET /{id}/raw — unchanged synchronous endpoint ────────────────────────

  @Test
  void getRawHtml_existingId_returns200WithTextPlainBody() throws Exception {
    String rawHtml = "<!DOCTYPE html><html><body>Raw</body></html>";
    when(artifactService.getRawHtml("raw-id")).thenReturn(rawHtml);

    mockMvc
        .perform(get("/api/artifacts/raw-id/raw").header("X-User-Id", "test-user"))
        .andExpect(status().isOk())
        .andExpect(content().contentType("text/plain;charset=UTF-8"))
        .andExpect(content().string(rawHtml));
  }

  @Test
  void getRawHtml_unknownId_returns404() throws Exception {
    when(artifactService.getRawHtml(anyString()))
        .thenThrow(new NotFoundException("Artifact not found: missing-id"));

    mockMvc
        .perform(get("/api/artifacts/missing-id/raw"))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }

  @Test
  void getRawHtml_nullRawHtml_returns404() throws Exception {
    when(artifactService.getRawHtml("null-raw-id"))
        .thenThrow(new NotFoundException("Artifact not found: null-raw-id"));

    mockMvc
        .perform(get("/api/artifacts/null-raw-id/raw").header("X-User-Id", "test-user"))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }

  // ── proves CurrentUserFilter actually runs inside this MockMvc slice ──────

  @Test
  void getRawHtml_userIdHeaderPresent_currentUserPopulatedBeforeServiceCall() throws Exception {
    // If CurrentUserFilter were absent from the MockMvc filter chain (e.g. dropped from the
    // @Import, or excluded by @WebMvcTest's Filter auto-detection not applying here),
    // currentUser.getUserId() would still be null/unset at this point in the request — this
    // stub runs on the request thread, inside the filter chain, before the mock "returns".
    when(artifactService.getRawHtml("filter-proof-id"))
        .thenAnswer(
            invocation -> {
              assertThat(currentUser.getUserId()).isEqualTo("filter-proof-user");
              return "<html>filter proof</html>";
            });

    mockMvc
        .perform(get("/api/artifacts/filter-proof-id/raw").header("X-User-Id", "filter-proof-user"))
        .andExpect(status().isOk());
  }
}
