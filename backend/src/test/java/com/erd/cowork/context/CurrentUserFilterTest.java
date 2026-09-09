package com.erd.cowork.context;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import com.erd.cowork.config.AnalysisAgentProperties;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class CurrentUserFilterTest {

  private static final String CUSTOM_SSO_TOKEN_HEADER = "X-Custom-Sso-Token";
  private static final String CUSTOM_SSO_URL_HEADER = "X-Custom-Sso-Url";

  private final AnalysisAgentProperties analysisAgentProperties =
      new AnalysisAgentProperties("http://localhost:8000", "/data/uploads", 180, 64);

  private final CurrentUserFilter currentUserFilter =
      new CurrentUserFilter(analysisAgentProperties);

  @AfterEach
  void tearDown() {
    CoworkContextHolder.clear();
  }

  @Test
  void doFilterInternal_userIdHeaderPresent_populatesUserId() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].userId()).isEqualTo("user-1");
  }

  @Test
  void doFilterInternal_userIdHeaderMissing_fallsBackToLocalDev() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].userId()).isEqualTo("local-dev");
  }

  @Test
  void doFilterInternal_userIdHeaderBlank_fallsBackToLocalDev() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "   ");
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].userId()).isEqualTo("local-dev");
  }

  @Test
  void doFilterInternal_userIdHeaderPresent_deptIdRemainsNull() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].deptId()).isNull();
  }

  @Test
  void doFilterInternal_delegatesToFilterChain() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    MockHttpServletResponse response = new MockHttpServletResponse();
    FilterChain filterChain = mock(FilterChain.class);

    currentUserFilter.doFilterInternal(request, response, filterChain);

    verify(filterChain).doFilter(request, response);
  }

  @Test
  void doFilterInternal_afterFilterChain_clearsHolder() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    MockHttpServletResponse response = new MockHttpServletResponse();
    FilterChain filterChain = mock(FilterChain.class);

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(CoworkContextHolder.get()).isNull();
  }

  @Test
  void shouldNotFilter_actuatorHealthPath_returnsTrue() {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.setRequestURI("/actuator/health");

    assertThat(currentUserFilter.shouldNotFilter(request)).isTrue();
  }

  @Test
  void shouldNotFilter_apiPath_returnsFalse() {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.setRequestURI("/api/artifacts/test-id");

    assertThat(currentUserFilter.shouldNotFilter(request)).isFalse();
  }

  @Test
  void doFilterInternal_ssoHeadersPresent_populatesSsoFields() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    request.addHeader("X-SSO-Token", "sso-token-value");
    request.addHeader("X-SSO-Url", "https://sso.example.com");
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].ssoToken()).isEqualTo("sso-token-value");
    assertThat(captured[0].ssoUrl()).isEqualTo("https://sso.example.com");
  }

  @Test
  void doFilterInternal_onlySsoTokenHeaderPresent_ssoUrlFallsBackToDevPlaceholder()
      throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    request.addHeader("X-SSO-Token", "sso-token-value");
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].ssoToken()).isEqualTo("sso-token-value");
    assertThat(captured[0].ssoUrl()).isEqualTo("http://local-dev-sso");
  }

  @Test
  void doFilterInternal_ssoHeadersAbsent_ssoFieldsFallBackToDevPlaceholdersWithoutError()
      throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].ssoToken()).isEqualTo("local-dev-sso-token");
    assertThat(captured[0].ssoUrl()).isEqualTo("http://local-dev-sso");
  }

  @Test
  void doFilterInternal_ssoHeadersBlank_ssoFieldsFallBackToDevPlaceholders() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    request.addHeader("X-SSO-Token", "   ");
    request.addHeader("X-SSO-Url", "   ");
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].ssoToken()).isEqualTo("local-dev-sso-token");
    assertThat(captured[0].ssoUrl()).isEqualTo("http://local-dev-sso");
  }

  @Test
  void doFilterInternal_customHeaderNamesConfigured_readsConfiguredHeaderNames() throws Exception {
    AnalysisAgentProperties customHeaderProperties =
        new AnalysisAgentProperties(
            "http://localhost:8000",
            "/data/uploads",
            180,
            64,
            "",
            CUSTOM_SSO_TOKEN_HEADER,
            CUSTOM_SSO_URL_HEADER);
    CurrentUserFilter customHeaderFilter = new CurrentUserFilter(customHeaderProperties);
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    // 預設名稱刻意也帶上,證明 filter 讀的是 properties 指定的自訂名稱、而非硬編預設值。
    request.addHeader("X-SSO-Token", "default-name-token");
    request.addHeader("X-SSO-Url", "https://default-name.example.com");
    request.addHeader(CUSTOM_SSO_TOKEN_HEADER, "custom-name-token");
    request.addHeader(CUSTOM_SSO_URL_HEADER, "https://custom-name.example.com");
    MockHttpServletResponse response = new MockHttpServletResponse();
    CoworkContext[] captured = new CoworkContext[1];
    FilterChain filterChain =
        (servletRequest, servletResponse) -> captured[0] = CoworkContextHolder.get();

    customHeaderFilter.doFilterInternal(request, response, filterChain);

    assertThat(captured[0].ssoToken()).isEqualTo("custom-name-token");
    assertThat(captured[0].ssoUrl()).isEqualTo("https://custom-name.example.com");
  }
}
