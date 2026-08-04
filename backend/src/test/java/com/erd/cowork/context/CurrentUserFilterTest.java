package com.erd.cowork.context;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.springframework.boot.web.servlet.filter.OrderedRequestContextFilter;
import org.springframework.core.annotation.Order;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class CurrentUserFilterTest {

  private final CurrentUser currentUser = new CurrentUser();
  private final CurrentUserFilter currentUserFilter = new CurrentUserFilter(currentUser);

  @Test
  void doFilterInternal_userIdHeaderPresent_populatesUserId() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    MockHttpServletResponse response = new MockHttpServletResponse();
    FilterChain filterChain = mock(FilterChain.class);

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(currentUser.getUserId()).isEqualTo("user-1");
    verify(filterChain).doFilter(request, response);
  }

  @Test
  void doFilterInternal_userIdHeaderMissing_fallsBackToLocalDev() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    MockHttpServletResponse response = new MockHttpServletResponse();
    FilterChain filterChain = mock(FilterChain.class);

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(currentUser.getUserId()).isEqualTo("local-dev");
  }

  @Test
  void doFilterInternal_userIdHeaderBlank_fallsBackToLocalDev() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "   ");
    MockHttpServletResponse response = new MockHttpServletResponse();
    FilterChain filterChain = mock(FilterChain.class);

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(currentUser.getUserId()).isEqualTo("local-dev");
  }

  @Test
  void doFilterInternal_userIdHeaderPresent_deptIdRemainsNull() throws Exception {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    MockHttpServletResponse response = new MockHttpServletResponse();
    FilterChain filterChain = mock(FilterChain.class);

    currentUserFilter.doFilterInternal(request, response, filterChain);

    assertThat(currentUser.getDeptId()).isNull();
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
  void order_comparedToRequestContextFilter_runsAfterIt() {
    // 排在 RequestContextFilter 之前的話，只會在第一個請求以 IllegalStateException 浮現。
    assertThat(CurrentUserFilter.class.getAnnotation(Order.class).value())
        .isGreaterThan(new OrderedRequestContextFilter().getOrder());
  }
}
