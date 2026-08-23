package com.erd.cowork.agent.provider.analysis;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.web.dto.ConnectorGroupDto;
import java.util.List;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

class AnalysisConnectorsClientTest {

  private static final int DEFAULT_TEST_TIMEOUT_SECONDS = 30;
  private static final int DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB = 64;

  private MockWebServer mockWebServer;
  private AnalysisConnectorsClient client;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    mockWebServer.start();
    client = newClient(mockWebServer, DEFAULT_TEST_TIMEOUT_SECONDS);
  }

  @AfterEach
  void tearDown() throws Exception {
    mockWebServer.shutdown();
  }

  private static AnalysisConnectorsClient newClient(
      MockWebServer mockWebServer, int requestTimeoutSeconds) {
    AnalysisAgentProperties analysisProperties =
        new AnalysisAgentProperties(
            "http://localhost:" + mockWebServer.getPort(),
            "/data/uploads",
            requestTimeoutSeconds,
            DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB);
    return new AnalysisConnectorsClient(analysisProperties, WebClient.builder());
  }

  @Test
  void fetchGroups_200Response_returnsParsedGroupList() throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody(
                "[{\"name\":\"mes\",\"display\":\"MES 系統\",\"description\":\"產線資料\"},"
                    + "{\"name\":\"erp\",\"display\":\"ERP 系統\",\"description\":\"訂單資料\"}]"));

    List<ConnectorGroupDto> groups = client.fetchGroups().block();

    assertThat(groups)
        .containsExactly(
            new ConnectorGroupDto("mes", "MES 系統", "產線資料"),
            new ConnectorGroupDto("erp", "ERP 系統", "訂單資料"));

    RecordedRequest request = mockWebServer.takeRequest();
    assertThat(request.getPath()).isEqualTo("/connectors");
    assertThat(request.getMethod()).isEqualTo("GET");
  }

  @Test
  void fetchGroups_emptyResponse_returnsEmptyList() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("[]"));

    List<ConnectorGroupDto> groups = client.fetchGroups().block();

    assertThat(groups).isEmpty();
  }

  @Test
  void fetchGroups_500Response_resolvesToEmptyListInsteadOfErroring() {
    mockWebServer.enqueue(new MockResponse().setResponseCode(500));

    List<ConnectorGroupDto> groups = client.fetchGroups().block();

    assertThat(groups).isEmpty();
  }

  @Test
  void fetchGroups_deepagentUnreachable_resolvesToEmptyListInsteadOfErroring() throws Exception {
    // A server started then immediately shut down frees its port with nothing listening —
    // simulates deepagent-service being unreachable without disturbing the shared mockWebServer
    // (which tearDown() shuts down again; a double-shutdown there would itself error).
    MockWebServer closedServer = new MockWebServer();
    closedServer.start();
    int unreachablePort = closedServer.getPort();
    closedServer.shutdown();

    AnalysisAgentProperties analysisProperties =
        new AnalysisAgentProperties(
            "http://localhost:" + unreachablePort,
            "/data/uploads",
            DEFAULT_TEST_TIMEOUT_SECONDS,
            DEFAULT_TEST_MAX_IN_MEMORY_SIZE_MB);
    AnalysisConnectorsClient unreachableClient =
        new AnalysisConnectorsClient(analysisProperties, WebClient.builder());

    List<ConnectorGroupDto> groups = unreachableClient.fetchGroups().block();

    assertThat(groups).isEmpty();
  }

  @Test
  void fetchGroups_streamStalls_resolvesToEmptyListInsteadOfErroring() {
    AnalysisConnectorsClient stallingClient = newClient(mockWebServer, 1);
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBodyDelay(5, TimeUnit.SECONDS)
            .setBody("[]"));

    List<ConnectorGroupDto> groups = stallingClient.fetchGroups().block();

    assertThat(groups).isEmpty();
  }
}
