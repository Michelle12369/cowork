package com.erd.cowork.context;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Reads {@code X-User-Id} and populates the request-scoped {@link CurrentUser}; missing or blank
 * falls back to {@code "local-dev"}. Registered only when {@code tsso.enabled} is false — the
 * internal environment supplies identity via its own filter at the same layer instead.
 *
 * <p>Runs last so {@code RequestContextFilter} has bound {@code RequestContextHolder} — writing to
 * a request-scoped bean before that throws {@code IllegalStateException} on the first request, with
 * no startup-time signal.
 */
@Component
@ConditionalOnProperty(name = "tsso.enabled", havingValue = "false", matchIfMissing = true)
@RequiredArgsConstructor
@Slf4j
@Order(Ordered.LOWEST_PRECEDENCE)
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
    // userId 非使用者資料內容，可記；internal 環境的身分問題本地重現不了，這是唯一線索。
    log.debug("resolved identity userId={} fallback={}", currentUser.getUserId(), usedFallback);
    filterChain.doFilter(request, response);
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return request.getRequestURI().startsWith(ACTUATOR_PATH_PREFIX);
  }
}
