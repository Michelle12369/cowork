package com.erd.cowork.web;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.service.SampleDatasetService;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SampleDatasetDto;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Slice test for {@link SampleDatasetController}. {@link CurrentUserFilter} is imported explicitly
 * — see {@link ArtifactControllerTest} for why. {@link AnalysisAgentProperties} is bound via
 * {@code @EnableConfigurationProperties} for the same reason documented on {@link
 * AppConfigControllerTest}.
 */
@WebMvcTest(SampleDatasetController.class)
@Import(CurrentUserFilter.class)
@EnableConfigurationProperties(AnalysisAgentProperties.class)
class SampleDatasetControllerTest {

  @Autowired MockMvc mockMvc;

  @MockitoBean SampleDatasetService sampleDatasetService;

  @Test
  void listSamples_returns200WithCatalog() throws Exception {
    when(sampleDatasetService.listDatasets())
        .thenReturn(
            List.of(
                new SampleDatasetDto(
                    "product-usage-feedback",
                    "產品使用行為與回饋",
                    "使用行為紀錄與使用者回饋。",
                    List.of("usage_log", "feedback"))));

    mockMvc
        .perform(get("/api/samples"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$[0].name").value("product-usage-feedback"))
        .andExpect(jsonPath("$[0].fileAliases[0]").value("usage_log"))
        .andExpect(jsonPath("$[0].fileAliases[1]").value("feedback"));
  }

  @Test
  void loadSample_knownSample_returns201WithFileDtos() throws Exception {
    when(sampleDatasetService.load("session-1", "product-usage-feedback"))
        .thenReturn(
            List.of(
                new FileDto("id1", "usage_log.csv", "usage_log", 100L, "csv", 2L, false),
                new FileDto("id2", "feedback.csv", "feedback", 50L, "csv", 1L, false)));

    mockMvc
        .perform(post("/api/sessions/session-1/files/samples/product-usage-feedback"))
        .andExpect(status().isCreated())
        .andExpect(jsonPath("$[0].alias").value("usage_log"))
        .andExpect(jsonPath("$[1].alias").value("feedback"));
  }

  @Test
  void loadSample_unknownSampleName_returns404() throws Exception {
    when(sampleDatasetService.load(anyString(), anyString()))
        .thenThrow(new NotFoundException("sample dataset not found: bogus"));

    mockMvc
        .perform(post("/api/sessions/session-1/files/samples/bogus"))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }

  @Test
  void loadSample_othersSession_returns404() throws Exception {
    // FileService.upload() (invoked inside SampleDatasetService.load()) enforces session
    // ownership; the mocked service surfaces the same NotFoundException the real chain would.
    when(sampleDatasetService.load("foreign-session", "product-usage-feedback"))
        .thenThrow(new NotFoundException("session not found: foreign-session"));

    mockMvc
        .perform(
            post("/api/sessions/foreign-session/files/samples/product-usage-feedback")
                .header("X-User-Id", "someone-else"))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }
}
