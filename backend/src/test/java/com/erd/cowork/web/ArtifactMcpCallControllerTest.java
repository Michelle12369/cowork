package com.erd.cowork.web;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.service.ArtifactMcpCallService;
import com.erd.cowork.service.ArtifactRepairService;
import com.erd.cowork.service.ArtifactService;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(ArtifactController.class)
@Import(CurrentUserFilter.class)
@EnableConfigurationProperties(AnalysisAgentProperties.class)
class ArtifactMcpCallControllerTest {

  private static final String VALID_REQUEST =
      "{\"connector\":\"sales\",\"tool\":\"list_orders\",\"args\":{\"days\":30}}";

  @Autowired MockMvc mockMvc;

  @MockitoBean ArtifactService artifactService;
  @MockitoBean ArtifactRepairService artifactRepairService;
  @MockitoBean ArtifactMcpCallService artifactMcpCallService;

  @Test
  void mcpCall_serviceReturnsBody_returns200JsonUnchanged() throws Exception {
    String body = "{\"data\":{\"result\":[{\"qty\":1.10}]}}";
    when(artifactMcpCallService.call(
            eq("art-1"), eq("sales"), eq("list_orders"), eq(Map.of("days", 30))))
        .thenReturn(body);

    mockMvc
        .perform(
            post("/api/artifacts/art-1/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isOk())
        .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
        .andExpect(content().string(body));
  }

  @Test
  void mcpCall_artifactNotFound_returns404() throws Exception {
    when(artifactMcpCallService.call(anyString(), anyString(), anyString(), any()))
        .thenThrow(new NotFoundException("Artifact not found: missing"));

    mockMvc
        .perform(
            post("/api/artifacts/missing/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }

  @Test
  void mcpCall_blankTool_returns400() throws Exception {
    mockMvc
        .perform(
            post("/api/artifacts/art-1/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"connector\":\"sales\",\"tool\":\"\",\"args\":{}}"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void mcpCall_argsIsArray_returns400() throws Exception {
    mockMvc
        .perform(
            post("/api/artifacts/art-1/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"connector\":\"sales\",\"tool\":\"list_orders\",\"args\":[1,2]}"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void mcpCall_missingArgs_returns400() throws Exception {
    mockMvc
        .perform(
            post("/api/artifacts/art-1/mcp-call")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"connector\":\"sales\",\"tool\":\"list_orders\"}"))
        .andExpect(status().isBadRequest());
  }
}
