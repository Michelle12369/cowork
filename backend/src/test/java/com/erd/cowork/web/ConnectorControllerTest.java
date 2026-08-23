package com.erd.cowork.web;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.erd.cowork.agent.provider.analysis.AnalysisConnectorsClient;
import com.erd.cowork.context.CurrentUserFilter;
import com.erd.cowork.web.dto.ConnectorGroupDto;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import reactor.core.publisher.Mono;

/**
 * Slice test for {@link ConnectorController}. The "no client bean present" branch (provider not
 * {@code langgraph-analysis}) is covered directly at the POJO level in {@link
 * ConnectorControllerNoClientTest}, since {@code @WebMvcTest} without a registered {@link
 * AnalysisConnectorsClient} bean already resolves the {@code Optional<AnalysisConnectorsClient>}
 * dependency to empty by itself.
 */
@WebMvcTest(ConnectorController.class)
@Import(CurrentUserFilter.class)
class ConnectorControllerTest {

  @Autowired MockMvc mockMvc;

  @MockitoBean AnalysisConnectorsClient connectorsClient;

  @Test
  void listConnectors_clientReturnsGroups_proxiesListAsJson() throws Exception {
    when(connectorsClient.fetchGroups())
        .thenReturn(
            Mono.just(
                List.of(
                    new ConnectorGroupDto("mes", "MES 系統", "產線資料"),
                    new ConnectorGroupDto("erp", "ERP 系統", "訂單資料"))));

    mockMvc
        .perform(get("/api/connectors"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.length()").value(2))
        .andExpect(jsonPath("$[0].name").value("mes"))
        .andExpect(jsonPath("$[0].display").value("MES 系統"))
        .andExpect(jsonPath("$[0].description").value("產線資料"))
        .andExpect(jsonPath("$[1].name").value("erp"));
  }

  @Test
  void listConnectors_clientReturnsEmptyList_returnsEmptyJsonArray() throws Exception {
    // Mirrors deepagent-service's own contract: AGENT_CONNECTORS_FILE unset → GET /connectors
    // returns [] rather than erroring — the client already normalizes any other failure to this
    // same shape (see AnalysisConnectorsClientTest), so the proxy only needs to pass it through.
    when(connectorsClient.fetchGroups()).thenReturn(Mono.just(List.of()));

    mockMvc
        .perform(get("/api/connectors"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.length()").value(0));
  }
}
