package com.erd.cowork.web;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.service.ArtifactRepairService;
import com.erd.cowork.service.ArtifactService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Slice test for {@link ArtifactController#refresh}'s provider-unsupported 409 arm specifically —
 * deliberately does NOT register a {@code @MockitoBean AnalysisReplayClient}, so Spring resolves
 * the controller's {@code Optional<AnalysisReplayClient>} constructor param to {@code
 * Optional.empty()} (mirroring a deployment whose active provider is not {@code
 * langgraph-analysis}, e.g. {@code OpenAICompatibleProvider}). {@link ArtifactControllerTest}
 * covers the other two 409 arms (no recipe, upload-sourced) with the client mocked present.
 */
@WebMvcTest(ArtifactController.class)
@Import(CurrentUserFilter.class)
@TestPropertySource(properties = "tsso.enabled=false")
class ArtifactControllerReplayUnsupportedTest {

  @Autowired MockMvc mockMvc;

  @MockitoBean ArtifactService artifactService;
  @MockitoBean ArtifactRepairService artifactRepairService;

  @Test
  void refresh_replayClientAbsent_returns409EvenWithRecipePresentAndNotUploadSourced()
      throws Exception {
    Artifact artifact = new Artifact();
    artifact.setId("no-provider-id");
    artifact.setRecipeJson("{\"schemaVersion\":1,\"sources\":[],\"queries\":{}}");
    artifact.setHasUploadSources(false);
    when(artifactService.getArtifact("no-provider-id")).thenReturn(artifact);

    mockMvc
        .perform(post("/api/artifacts/no-provider-id/refresh"))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("CONFLICT"));
  }
}
