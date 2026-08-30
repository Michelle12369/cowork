package com.erd.cowork.agent.model;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * {@link AgentRequest#ssoToken()} is a secret (spec §8: NEVER let it ride a log line). These tests
 * pin the {@link AgentRequest#toString()} masking that any incidental log statement (e.g. a future
 * {@code @LogAnnotation(args = true)}) would rely on — mirroring the {@code
 * CoworkContext#toString()} precedent.
 */
class AgentRequestTest {

  private static final String SECRET_TOKEN = "super-secret-sso-token";

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
            List.of("salesforce"),
            SECRET_TOKEN);

    String stringified = request.toString();

    assertThat(stringified).doesNotContain(SECRET_TOKEN);
    assertThat(stringified).contains("ssoToken=***");
  }

  @Test
  void toString_ssoTokenAbsent_rendersNullLiteral() {
    AgentRequest request =
        new AgentRequest("u1", "s1", "question", List.of(), List.of(), null, List.of(), null);

    assertThat(request.toString()).contains("ssoToken=null");
  }

  @Test
  void backCompatConstructor_defaultsConnectorsEmptyAndTokenNull() {
    AgentRequest request = new AgentRequest("u1", "s1", "question", List.of(), List.of(), null);

    assertThat(request.selectedConnectors()).isEmpty();
    assertThat(request.ssoToken()).isNull();
  }
}
