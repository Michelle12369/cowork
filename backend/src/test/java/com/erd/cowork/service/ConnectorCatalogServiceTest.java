package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.config.AnalysisAgentProperties;
import com.erd.cowork.web.dto.ConnectorInfoDto;
import java.util.List;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * Graceful-empty is the load-bearing contract here (spec §5b): the connector directory must never
 * surface an error to the frontend — any failure mode collapses to an empty list.
 */
class ConnectorCatalogServiceTest {

  private MockWebServer mockWebServer;

  @BeforeEach
  void setUp() throws Exception {
    mockWebServer = new MockWebServer();
    mockWebServer.start();
  }

  @AfterEach
  void tearDown() {
    // Tolerant: the unreachable-server test shuts mockWebServer down itself mid-test, so a
    // second shutdown here (this cleanup hook) must not fail the test with a spurious error.
    try {
      mockWebServer.shutdown();
    } catch (Exception alreadyShutDown) {
      // ignored — cleanup-only, see comment above.
    }
  }

  private ConnectorCatalogService newService(String bearerToken) {
    AnalysisAgentProperties analysisProperties =
        new AnalysisAgentProperties(
            "http://localhost:" + mockWebServer.getPort(), "/data/uploads", 30, 64, bearerToken);
    return new ConnectorCatalogService(analysisProperties, WebClient.builder());
  }

  @Test
  void list_happyPath_returnsParsedConnectors() throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("[{\"id\":\"salesforce\",\"name\":\"Salesforce CRM\"}]"));

    List<ConnectorInfoDto> connectors = newService("").list();

    assertThat(connectors).containsExactly(new ConnectorInfoDto("salesforce", "Salesforce CRM"));
  }

  @Test
  void list_bearerTokenConfigured_sentOnRequest() throws Exception {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("[]"));

    newService("secret-bearer").list();

    RecordedRequest request = mockWebServer.takeRequest(2, TimeUnit.SECONDS);
    assertThat(request).isNotNull();
    assertThat(request.getHeader("Authorization")).isEqualTo("Bearer secret-bearer");
  }

  @Test
  void list_deepagentReturns500_returnsEmptyList() {
    mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("boom"));

    assertThat(newService("").list()).isEmpty();
  }

  @Test
  void list_deepagentUnreachable_returnsEmptyList() throws Exception {
    // Build the service (captures the still-live port) before shutting the server down, so the
    // subsequent call fails at connection time (unreachable) rather than never resolving a port.
    ConnectorCatalogService service = newService("");
    mockWebServer.shutdown();

    assertThat(service.list()).isEmpty();
  }

  @Test
  void list_malformedBody_returnsEmptyList() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("not-json"));

    assertThat(newService("").list()).isEmpty();
  }

  @Test
  void list_emptyUpstreamDirectory_returnsEmptyList() {
    mockWebServer.enqueue(
        new MockResponse()
            .setResponseCode(200)
            .addHeader("Content-Type", "application/json")
            .setBody("[]"));

    assertThat(newService("").list()).isEmpty();
  }
}
