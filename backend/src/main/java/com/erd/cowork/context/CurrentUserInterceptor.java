package com.erd.cowork.context;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * Reads the {@code X-User-Id} header and populates the request-scoped {@link CurrentUser}. A
 * missing or blank header falls back to {@code "local-dev"} so the v1 local environment works
 * without SSO.
 */
@Component
@RequiredArgsConstructor
public class CurrentUserInterceptor implements HandlerInterceptor {

  static final String DEFAULT_USER_ID = "local-dev";
  static final String HEADER = "X-User-Id";

  private final CurrentUser currentUser;

  @Override
  public boolean preHandle(
      HttpServletRequest request, HttpServletResponse response, Object handler) {
    String header = request.getHeader(HEADER);
    currentUser.setUserId(StringUtils.hasText(header) ? header : DEFAULT_USER_ID);
    return true;
  }
}
