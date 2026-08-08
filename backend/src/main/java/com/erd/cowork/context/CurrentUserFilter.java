package com.erd.cowork.context;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Reads {@code X-User-Id} and populates {@link CoworkContextHolder}; missing or blank falls back to
 * {@code "local-dev"}. Registered only when {@code tsso.enabled} is false — the internal
 * environment supplies identity via its own filter at the same layer instead.
 *
 * <p>Clears the holder in a {@code finally} after the filter chain returns, so pooled request
 * threads never leak or bleed identity into the next request.
 */
@Component
@ConditionalOnProperty(name = "tsso.enabled", havingValue = "false", matchIfMissing = true)
@Slf4j
public class CurrentUserFilter extends OncePerRequestFilter {

  static final String DEFAULT_USER_ID = "local-dev";
  static final String HEADER = "X-User-Id";

  private static final String ACTUATOR_PATH_PREFIX = "/actuator/";

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String header = request.getHeader(HEADER);
    boolean usedFallback = !StringUtils.hasText(header);
    String userId = usedFallback ? DEFAULT_USER_ID : header;
    CoworkContextHolder.set(CoworkContext.external(userId));
    try {
      // userId 非使用者資料內容，可記；internal 環境的身分問題本地重現不了，這是唯一線索。
      log.debug("resolved identity userId={} fallback={}", userId, usedFallback);
      filterChain.doFilter(request, response);
    } finally {
      CoworkContextHolder.clear();
    }
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return request.getRequestURI().startsWith(ACTUATOR_PATH_PREFIX);
  }
}
