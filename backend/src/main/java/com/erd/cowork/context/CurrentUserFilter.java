package com.erd.cowork.context;

import com.erd.cowork.config.AnalysisAgentProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Reads {@code X-User-Id} and populates {@link CoworkContextHolder}; missing or blank falls back to
 * {@code "local-dev"}. Also reads the two SSO headers named by {@link
 * AnalysisAgentProperties#ssoTokenHeader()}/{@link AnalysisAgentProperties#ssoUrlHeader()} — the
 * same header names used outbound to deepagent — so a local reverse proxy or manual test request
 * can simulate the internal identity filter; either header missing/blank falls back to a fixed dev
 * placeholder ({@link #DEFAULT_SSO_TOKEN}/{@link #DEFAULT_SSO_URL}), mirroring the {@code
 * X-User-Id} → {@link #DEFAULT_USER_ID} pattern above.
 *
 * <p>Unlike {@code userId}, the SSO fields are never left {@code null} here: deepagent's connector
 * mode treats both SSO values as required and fails fast with {@code CHAT_INIT_FAILED} when either
 * is absent, and the dev frontend never sends these headers. Since this filter is the only identity
 * source on the dev line, a {@code null} passthrough would make the full frontend → Java →
 * deepagent → MCP chain untestable locally; the placeholder keeps it walkable end to end.
 * Registered only when {@code tsso.enabled} is false — internal environments supply identity via
 * their own filter at the same layer instead and are unaffected by this fallback.
 *
 * <p>Clears the holder in a {@code finally} after the filter chain returns, so pooled request
 * threads never leak or bleed identity into the next request.
 */
@Component
@ConditionalOnProperty(name = "tsso.enabled", havingValue = "false", matchIfMissing = true)
@RequiredArgsConstructor
@Slf4j
public class CurrentUserFilter extends OncePerRequestFilter {

  static final String DEFAULT_USER_ID = "local-dev";
  static final String HEADER = "X-User-Id";
  static final String DEFAULT_SSO_TOKEN = "local-dev-sso-token";
  static final String DEFAULT_SSO_URL = "http://local-dev-sso";

  private static final String ACTUATOR_PATH_PREFIX = "/actuator/";

  private final AnalysisAgentProperties analysisAgentProperties;

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String header = request.getHeader(HEADER);
    boolean usedFallback = !StringUtils.hasText(header);
    String userId = usedFallback ? DEFAULT_USER_ID : header;
    String ssoUrlHeaderValue = request.getHeader(analysisAgentProperties.ssoUrlHeader());
    String ssoTokenHeaderValue = request.getHeader(analysisAgentProperties.ssoTokenHeader());
    boolean ssoUrlUsedFallback = !StringUtils.hasText(ssoUrlHeaderValue);
    boolean ssoTokenUsedFallback = !StringUtils.hasText(ssoTokenHeaderValue);
    String ssoUrl = ssoUrlUsedFallback ? DEFAULT_SSO_URL : ssoUrlHeaderValue;
    String ssoToken = ssoTokenUsedFallback ? DEFAULT_SSO_TOKEN : ssoTokenHeaderValue;
    boolean ssoFallbackUsed = ssoUrlUsedFallback || ssoTokenUsedFallback;
    CoworkContextHolder.set(CoworkContext.external(userId, ssoUrl, ssoToken));
    try {
      // 進來的 URL（method + path + query）——排查路由/迴圈/來源很有用。ssoFallback 只記布林，token/url 值 NEVER 進 log。
      String query = request.getQueryString();
      log.info(
          "[REQ] {} {}{} userId={} ssoFallback={}",
          request.getMethod(),
          request.getRequestURI(),
          query == null ? "" : "?" + query,
          userId,
          ssoFallbackUsed);
      // userId 非使用者資料內容，可記；internal 環境的身分問題本地重現不了，這是唯一線索。
      log.debug(
          "resolved identity userId={} fallback={} ssoFallback={}",
          userId,
          usedFallback,
          ssoFallbackUsed);
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
