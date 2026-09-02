package com.erd.cowork.web;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.service.ConnectorCatalogService;
import com.erd.cowork.web.dto.ConnectorInfoDto;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

// No explicit @Import(CurrentUserFilter.class) here — @WebMvcTest auto-detects it as a Filter
// bean regardless, which is why it still needs AnalysisAgentProperties bound (see
// AppConfigControllerTest javadoc for the full explanation).
@WebMvcTest(ConnectorController.class)
@EnableConfigurationProperties(AnalysisAgentProperties.class)
class ConnectorControllerTest {

  @Autowired MockMvc mockMvc;

  @MockitoBean ConnectorCatalogService connectorCatalogService;

  @Test
  void list_catalogHasEntries_returnsThemAsJsonArray() throws Exception {
    when(connectorCatalogService.listCatalog())
        .thenReturn(
            List.of(
                new ConnectorInfoDto("salesforce", "Salesforce CRM"),
                new ConnectorInfoDto("hubspot", "HubSpot")));

    mockMvc
        .perform(get("/api/connectors"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$[0].id").value("salesforce"))
        .andExpect(jsonPath("$[0].name").value("Salesforce CRM"))
        .andExpect(jsonPath("$[1].id").value("hubspot"));
  }

  @Test
  void list_catalogUnavailable_returnsEmptyArray() throws Exception {
    // ConnectorCatalogService itself absorbs deepagent failures into an empty list
    // (graceful-empty) — the controller is a pure proxy over that contract.
    when(connectorCatalogService.listCatalog()).thenReturn(List.of());

    mockMvc
        .perform(get("/api/connectors"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$").isArray())
        .andExpect(jsonPath("$").isEmpty());
  }
}
