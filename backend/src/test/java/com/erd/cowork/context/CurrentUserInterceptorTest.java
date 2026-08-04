package com.erd.cowork.context;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class CurrentUserInterceptorTest {

  private final CurrentUser currentUser = new CurrentUser();
  private final CurrentUserInterceptor interceptor = new CurrentUserInterceptor(currentUser);

  @Test
  void preHandle_headersPresent_populatesUserIdAndDeptId() {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-1");
    request.addHeader("X-Dept-Id", "dept-9");
    HttpServletResponse response = new MockHttpServletResponse();

    assertThat(interceptor.preHandle(request, response, new Object())).isTrue();
    assertThat(currentUser.getUserId()).isEqualTo("user-1");
    assertThat(currentUser.getDeptId()).isEqualTo("dept-9");
  }

  @Test
  void preHandle_headersMissing_fallsBackToLocalDev() {
    MockHttpServletRequest request = new MockHttpServletRequest();
    HttpServletResponse response = new MockHttpServletResponse();

    interceptor.preHandle(request, response, new Object());

    assertThat(currentUser.getUserId()).isEqualTo("local-dev");
    assertThat(currentUser.getDeptId()).isEqualTo("local-dev");
  }

  @Test
  void preHandle_blankDeptHeader_fallsBackToLocalDev() {
    MockHttpServletRequest request = new MockHttpServletRequest();
    request.addHeader("X-User-Id", "user-2");
    request.addHeader("X-Dept-Id", "   ");
    HttpServletResponse response = new MockHttpServletResponse();

    interceptor.preHandle(request, response, new Object());

    assertThat(currentUser.getUserId()).isEqualTo("user-2");
    assertThat(currentUser.getDeptId()).isEqualTo("local-dev");
  }
}
