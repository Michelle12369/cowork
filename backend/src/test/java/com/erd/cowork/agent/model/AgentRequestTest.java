package com.erd.cowork.agent.model;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * {@link AgentRequest#ssoToken()}/{@link AgentRequest#ssoUrl()} are forwarded to deepagent as HTTP
 * headers, never the JSON body — NEVER let the token ride a log line. These tests pin the {@link
 * AgentRequest#toString()} masking that any incidental log statement (e.g. a future
 * {@code @LogAnnotation(args = true)}) would rely on — mirroring the {@code
 * CoworkContext#toString()} precedent.
 */
class AgentRequestTest {

  private static final String SECRET_TOKEN = "super-secret-sso-token";
  private static final String SSO_URL = "https://sso.internal.example/auth";

  private static final ConnectorSpec SALESFORCE_SPEC =
      new ConnectorSpec("salesforce", "Salesforce CRM", "https://mcp.example/salesforce");

  @Test
  void toString_ssoTokenPresent_maskedNotLeaked() {
    AgentRequest request =
        new AgentRequest(
            "u1",
            "s1",
            "question",
            List.of(),
            List.of(),
            null,
            List.of(SALESFORCE_SPEC),
            SECRET_TOKEN,
            SSO_URL);

    String stringified = request.toString();

    assertThat(stringified).doesNotContain(SECRET_TOKEN);
    assertThat(stringified).contains("ssoToken=***");
  }

  @Test
  void toString_ssoTokenAbsent_rendersNullLiteral() {
    AgentRequest request =
        new AgentRequest("u1", "s1", "question", List.of(), List.of(), null, List.of(), null, null);

    assertThat(request.toString()).contains("ssoToken=null");
  }

  @Test
  void toString_ssoUrlPresent_maskedNotLeaked() {
    AgentRequest request =
        new AgentRequest(
            "u1",
            "s1",
            "question",
            List.of(),
            List.of(),
            null,
            List.of(SALESFORCE_SPEC),
            SECRET_TOKEN,
            SSO_URL);

    String stringified = request.toString();

    assertThat(stringified).doesNotContain(SSO_URL);
    assertThat(stringified).contains("ssoUrl=***");
  }

  @Test
  void toString_ssoUrlAbsent_rendersNullLiteral() {
    AgentRequest request =
        new AgentRequest("u1", "s1", "question", List.of(), List.of(), null, List.of(), null, null);

    assertThat(request.toString()).contains("ssoUrl=null");
  }

  @Test
  void backCompatConstructor_defaultsConnectorsEmptyAndSsoFieldsNull() {
    AgentRequest request = new AgentRequest("u1", "s1", "question", List.of(), List.of(), null);

    assertThat(request.connectorSpecs()).isEmpty();
    assertThat(request.ssoToken()).isNull();
    assertThat(request.ssoUrl()).isNull();
  }
}
