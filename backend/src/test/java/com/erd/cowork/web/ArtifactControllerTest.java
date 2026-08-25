package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.service.ArtifactService;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * Slice test for {@link ArtifactController}. {@link CurrentUserFilter} is imported because
 * {@code @WebMvcTest} does not auto-detect it.
 *
 * <p>GET /{id} returns {@code ResponseEntity<StreamingResponseBody>}, so 200 responses require the
 * two-step MockMvc async-dispatch pattern: perform the request, assert async started, then perform
 * {@link MockMvcRequestBuilders#asyncDispatch} to collect the streamed body. Synchronous error
 * responses (404) skip async dispatch.
 */
@WebMvcTest(ArtifactController.class)
@Import(CurrentUserFilter.class)
@TestPropertySource(properties = "tsso.enabled=false")
class ArtifactControllerTest {

  @Autowired MockMvc mockMvc;

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
  void getArtifact_htmlResponse_carriesContentSecurityPolicyHeader() throws Exception {
    when(artifactService.getHtmlStream("test-id"))
        .thenReturn(out -> out.write("<html></html>".getBytes(StandardCharsets.UTF_8)));

    MvcResult asyncResult =
        mockMvc
            .perform(get("/api/artifacts/test-id").header("X-User-Id", "test-user"))
            .andExpect(request().asyncStarted())
            .andReturn();

    mockMvc
        .perform(asyncDispatch(asyncResult))
        .andExpect(status().isOk())
        .andExpect(
            header()
                .string(
                    "Content-Security-Policy",
                    ArtifactController.ARTIFACT_CONTENT_SECURITY_POLICY));
    // 政策內容釘死斷言:script-src 只允許同源+inline(rewrite 已把祝福 CDN 換成 /vendor/*),
    // connect-src 'none' 封外洩、sandbox allow-scripts 封同源特權(直接導覽也吃 opaque origin)——
    // 任一段被放寬都該讓這個測試先亮紅燈。
    assertThat(ArtifactController.ARTIFACT_CONTENT_SECURITY_POLICY)
        .contains("script-src 'self' 'unsafe-inline'")
        .contains("connect-src 'none'")
        .contains("sandbox allow-scripts")
        .doesNotContain("http");
  }

  @Test
  void getArtifact_serviceReturnsStream_returns200HtmlWithCspHeader() throws Exception {
    // Controller-wiring test only: ArtifactService is mocked here, so this does NOT exercise the
    // real ownership guard (see ArtifactServiceTest for that). It just proves the controller wires
    // the service's stream through with the correct content-type and CSP header regardless of
    // which request path (with/without X-User-Id) reaches it in this slice test.
    String html = "<html><body>Dashboard</body></html>";
    when(artifactService.getHtmlStream("cap-id"))
        .thenReturn(out -> out.write(html.getBytes(StandardCharsets.UTF_8)));

    MvcResult asyncResult =
        mockMvc
            .perform(get("/api/artifacts/cap-id"))
            .andExpect(request().asyncStarted())
            .andReturn();

    mockMvc
        .perform(asyncDispatch(asyncResult))
        .andExpect(status().isOk())
        .andExpect(content().contentType("text/html;charset=UTF-8"))
        .andExpect(content().string(html))
        .andExpect(
            header()
                .string(
                    "Content-Security-Policy",
                    ArtifactController.ARTIFACT_CONTENT_SECURITY_POLICY));
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

  @Test
  void getRawHtml_userIdHeaderPresent_currentUserPopulatedBeforeServiceCall() throws Exception {
    // Stub 跑在請求執行緒、filter chain 內，故能證明 CurrentUserFilter 真的填了 CoworkContextHolder。
    when(artifactService.getRawHtml("filter-proof-id"))
        .thenAnswer(
            invocation -> {
              assertThat(CoworkContextHolder.userId()).isEqualTo("filter-proof-user");
              return "<html>filter proof</html>";
            });

    mockMvc
        .perform(get("/api/artifacts/filter-proof-id/raw").header("X-User-Id", "filter-proof-user"))
        .andExpect(status().isOk());
  }
}
