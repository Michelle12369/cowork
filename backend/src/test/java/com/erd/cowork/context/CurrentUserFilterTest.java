package com.erd.cowork.context;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class CurrentUserFilterTest {

  private final CurrentUserFilter currentUserFilter = new CurrentUserFilter();

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
}
