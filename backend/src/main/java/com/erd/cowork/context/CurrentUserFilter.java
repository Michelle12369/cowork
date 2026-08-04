package com.erd.cowork.context;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.web.servlet.filter.OrderedRequestContextFilter;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Reads the {@code X-User-Id} header and populates the request-scoped {@link CurrentUser}. A
 * missing or blank header falls back to {@code "local-dev"} so the v1 local environment works
 * without SSO.
 *
 * <p>Registered only when {@code tsso.enabled} is false or unset. In the company environment the
 * identity is injected by the company's own filter at the same layer, so this bean is absent.
 *
 * <p>{@code @Order} MUST be greater than {@link OrderedRequestContextFilter#getOrder()} (which
 * defaults to -105). {@link CurrentUser} is a {@code @RequestScope} bean resolved via {@code
 * RequestContextHolder}, and that holder is only bound to the current thread once {@code
 * OrderedRequestContextFilter} has run. Relying on the default filter order (undeclared @Order
 * falls back to lowest precedence, which happens to run after) is fragile and undocumented — if
 * this filter ran first, writes to {@code CurrentUser} would throw {@code IllegalStateException("No
 * thread-bound request found")}, and there is no startup-time signal; it only surfaces on the first
 * real request.
 */
@Component
@ConditionalOnProperty(name = "tsso.enabled", havingValue = "false", matchIfMissing = true)
@RequiredArgsConstructor
@Slf4j
@Order(-100)
public class CurrentUserFilter extends OncePerRequestFilter {

  static final String DEFAULT_USER_ID = "local-dev";
  static final String HEADER = "X-User-Id";

  private static final String ACTUATOR_PATH_PREFIX = "/actuator/";

  private final CurrentUser currentUser;

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String header = request.getHeader(HEADER);
    boolean usedFallback = !StringUtils.hasText(header);
    currentUser.setUserId(usedFallback ? DEFAULT_USER_ID : header);
    // 公司環境的身分問題家裡重現不了，識別碼與是否走 fallback 是唯一線索(非使用者資料內容)。
    log.debug("resolved identity userId={} fallback={}", currentUser.getUserId(), usedFallback);
    filterChain.doFilter(request, response);
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return request.getRequestURI().startsWith(ACTUATOR_PATH_PREFIX);
  }
}
