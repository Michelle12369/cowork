package com.erd.cowork.web;

import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.exception.ConflictException;
import com.erd.cowork.exception.FilesExpiredException;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.service.ArtifactRepairService;
import com.erd.cowork.service.ArtifactService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(ArtifactController.class)
@Import(CurrentUserFilter.class)
class ArtifactRepairControllerTest {

  @Autowired MockMvc mockMvc;

  @MockitoBean ArtifactService artifactService;
  @MockitoBean ArtifactRepairService artifactRepairService;

  private static final String VALID_REQUEST =
      "{\"errors\":[{\"message\":\"ReferenceError: showTab is not"
          + " defined\",\"line\":42,\"col\":5}]}";

  @Test
  void repair_repaired_returns200WithRepairedTrue() throws Exception {
    when(artifactRepairService.repairFromBrowserErrors(anyString(), anyList())).thenReturn(true);

    mockMvc
        .perform(
            post("/api/artifacts/art-1/repair")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.repaired").value(true));
  }

  @Test
  void repair_notRepaired_returns200WithRepairedFalse() throws Exception {
    when(artifactRepairService.repairFromBrowserErrors(anyString(), anyList())).thenReturn(false);

    mockMvc
        .perform(
            post("/api/artifacts/art-1/repair")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.repaired").value(false));
  }

  @Test
  void repair_artifactNotFound_returns404() throws Exception {
    when(artifactRepairService.repairFromBrowserErrors(anyString(), anyList()))
        .thenThrow(new NotFoundException("Artifact not found: missing"));

    mockMvc
        .perform(
            post("/api/artifacts/missing/repair")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOT_FOUND"));
  }

  @Test
  void repair_noRawHtml_returns409() throws Exception {
    when(artifactRepairService.repairFromBrowserErrors(anyString(), anyList()))
        .thenThrow(new ConflictException("Artifact has no raw HTML to repair from: art-old"));

    mockMvc
        .perform(
            post("/api/artifacts/art-old/repair")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("CONFLICT"));
  }

  @Test
  void repair_filesExpired_returns409WithFilesExpiredCode() throws Exception {
    when(artifactRepairService.repairFromBrowserErrors(anyString(), anyList()))
        .thenThrow(new FilesExpiredException(30));

    mockMvc
        .perform(
            post("/api/artifacts/art-1/repair")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_REQUEST))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("FILES_EXPIRED"));
  }

  @Test
  void repair_emptyErrors_returns400() throws Exception {
    mockMvc
        .perform(
            post("/api/artifacts/art-1/repair")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"errors\":[]}"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void repair_missingMessageField_returns400() throws Exception {
    mockMvc
        .perform(
            post("/api/artifacts/art-1/repair")
                .header("X-User-Id", "user-1")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"errors\":[{\"message\":\"\",\"line\":1,\"col\":0}]}"))
        .andExpect(status().isBadRequest());
  }
}
