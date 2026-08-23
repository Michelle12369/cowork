package com.erd.cowork.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.agent.provider.analysis.AnalysisReplayClient;
import com.erd.cowork.agent.provider.analysis.AnalysisReplayOutcome;
import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.domain.Artifact;
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
import reactor.core.publisher.Mono;

/**
 * Slice test for {@link ArtifactController}. {@link CurrentUserFilter} is imported because
 * {@code @WebMvcTest} does not auto-detect it.
 *
 * <p>GET /{id} returns {@code ResponseEntity<StreamingResponseBody>}, so 200 responses require the
 * two-step MockMvc async-dispatch pattern: perform the request, assert async started, then perform
 * {@link MockMvcRequestBuilders#asyncDispatch} to collect the streamed body. Synchronous error
 * responses (404) skip async dispatch.
 *
 * <p>{@code analysisReplayClient} is registered as a {@code @MockitoBean} so the controller's
 * {@code Optional<AnalysisReplayClient>} constructor param resolves to {@code Optional.of(mock)} —
 * mirroring how the {@code langgraph-analysis} provider mode makes the real bean present. Spring
 * resolves an unregistered {@code Optional<T>} bean to {@code Optional.empty()} automatically, so
 * no special setup is needed for tests that must exercise the "unsupported" path.
 */
@WebMvcTest(ArtifactController.class)
@Import(CurrentUserFilter.class)
@TestPropertySource(properties = "tsso.enabled=false")
class ArtifactControllerTest {

  @Autowired MockMvc mockMvc;

  @MockitoBean ArtifactService artifactService;
  @MockitoBean com.erd.cowork.service.ArtifactRepairService artifactRepairService;
  @MockitoBean AnalysisReplayClient analysisReplayClient;

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

  // ── POST /{id}/refresh — recipe replay ────────────────────────────────────

  private static Artifact artifactWithRecipe(String id, boolean hasUploadSources) {
    Artifact artifact = new Artifact();
    artifact.setId(id);
    artifact.setRecipeJson("{\"schemaVersion\":1,\"sources\":[],\"queries\":{}}");
    artifact.setHasUploadSources(hasUploadSources);
    return artifact;
  }

  @Test
  void refresh_unknownId_returns404() throws Exception {
    when(artifactService.getArtifact("missing-id"))
        .thenThrow(new NotFoundException("Artifact not found: missing-id"));

    mockMvc
        .perform(post("/api/artifacts/missing-id/refresh"))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }

  @Test
  void refresh_noRecipeJson_returns409() throws Exception {
    Artifact artifact = new Artifact();
    artifact.setId("no-recipe-id");
    artifact.setRecipeJson(null);
    when(artifactService.getArtifact("no-recipe-id")).thenReturn(artifact);

    mockMvc
        .perform(post("/api/artifacts/no-recipe-id/refresh"))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("CONFLICT"));
  }

  @Test
  void refresh_hasUploadSources_returns409() throws Exception {
    Artifact artifact = artifactWithRecipe("upload-sourced-id", true);
    when(artifactService.getArtifact("upload-sourced-id")).thenReturn(artifact);

    mockMvc
        .perform(post("/api/artifacts/upload-sourced-id/refresh"))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("CONFLICT"));
  }

  @Test
  void refresh_recipePresent_returns200WithFreshHtml() throws Exception {
    Artifact artifact = artifactWithRecipe("happy-id", false);
    when(artifactService.getArtifact("happy-id")).thenReturn(artifact);
    when(artifactService.loadRawHtml(artifact))
        .thenReturn(java.util.Optional.of("<html>base</html>"));
    when(analysisReplayClient.replay(
            eq("happy-id"), eq(artifact.getRecipeJson()), eq("<html>base</html>")))
        .thenReturn(Mono.just(AnalysisReplayOutcome.success("<html>fresh</html>")));

    mockMvc
        .perform(post("/api/artifacts/happy-id/refresh"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.html").value("<html>fresh</html>"));
  }

  @Test
  void refresh_deepagentRejects_returns502WithPassthroughCode() throws Exception {
    Artifact artifact = artifactWithRecipe("rejected-id", false);
    when(artifactService.getArtifact("rejected-id")).thenReturn(artifact);
    when(artifactService.loadRawHtml(artifact))
        .thenReturn(java.util.Optional.of("<html>base</html>"));
    when(analysisReplayClient.replay(
            eq("rejected-id"), eq(artifact.getRecipeJson()), eq("<html>base</html>")))
        .thenReturn(Mono.just(AnalysisReplayOutcome.failure("SOURCE_GONE", "資料源已停用")));

    mockMvc
        .perform(post("/api/artifacts/rejected-id/refresh"))
        .andExpect(status().isBadGateway())
        .andExpect(jsonPath("$.code").value("SOURCE_GONE"))
        .andExpect(jsonPath("$.message").value("資料源已停用"));
  }
}
