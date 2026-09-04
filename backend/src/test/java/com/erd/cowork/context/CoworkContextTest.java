package com.erd.cowork.context;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

/**
 * {@link CoworkContext#ssoToken()}/{@link CoworkContext#ssoUrl()} are forwarded to deepagent as
 * {@code X-SSO-Token}/{@code X-SSO-Url} HTTP headers — NEVER let either ride a log line. These
 * tests pin the {@link CoworkContext#toString()} masking that any incidental log statement (e.g. a
 * future {@code @LogAnnotation(args = true)}) would rely on.
 */
class CoworkContextTest {

  private static final String SECRET_TOKEN = "super-secret-sso-token";
  private static final String SSO_URL = "https://sso.internal.example/auth";

  @Test
  void toString_ssoTokenPresent_maskedNotLeaked() {
    CoworkContext context = new CoworkContext("u1", "dept1", SSO_URL, SECRET_TOKEN);

    String stringified = context.toString();

    assertThat(stringified).doesNotContain(SECRET_TOKEN);
    assertThat(stringified).contains("ssoToken=***");
  }

  @Test
  void toString_ssoUrlPresent_maskedNotLeaked() {
    CoworkContext context = new CoworkContext("u1", "dept1", SSO_URL, SECRET_TOKEN);

    String stringified = context.toString();

    assertThat(stringified).doesNotContain(SSO_URL);
    assertThat(stringified).contains("ssoUrl=***");
  }

  @Test
  void toString_ssoTokenAndUrlAbsent_rendersNullLiterals() {
    CoworkContext context = CoworkContext.external("u1");

    String stringified = context.toString();

    assertThat(stringified).contains("ssoToken=null");
    assertThat(stringified).contains("ssoUrl=null");
  }
}
