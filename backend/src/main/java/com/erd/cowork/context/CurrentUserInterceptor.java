package com.erd.cowork.context;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * Reads the {@code X-User-Id} / {@code X-Dept-Id} headers and populates the request-scoped {@link
 * CurrentUser}. Missing or blank headers fall back to {@code "local-dev"} so the v1 local
 * environment works without SSO.
 *
 * <p>Registered only when {@code tsso.enabled} is false or unset. In the company environment TSSO
 * supplies the identity and provides its own {@code WebMvcConfigurer}, so this bean is absent.
 */
@Component
@ConditionalOnProperty(name = "tsso.enabled", havingValue = "false", matchIfMissing = true)
@RequiredArgsConstructor
@Slf4j
public class CurrentUserInterceptor implements HandlerInterceptor {

  static final String DEFAULT_VALUE = "local-dev";
  static final String USER_HEADER = "X-User-Id";
  static final String DEPT_HEADER = "X-Dept-Id";

  private final CurrentUser currentUser;

  @Override
  public boolean preHandle(
      HttpServletRequest request, HttpServletResponse response, Object handler) {
    String userHeader = request.getHeader(USER_HEADER);
    String deptHeader = request.getHeader(DEPT_HEADER);
    boolean usedFallback = !StringUtils.hasText(userHeader) || !StringUtils.hasText(deptHeader);
    currentUser.setUserId(StringUtils.hasText(userHeader) ? userHeader : DEFAULT_VALUE);
    currentUser.setDeptId(StringUtils.hasText(deptHeader) ? deptHeader : DEFAULT_VALUE);
    // 公司環境的身分問題家裡重現不了，識別碼與是否走 fallback 是唯一線索(皆非使用者資料內容)。
    log.debug(
        "resolved identity userId={} deptId={} fallback={}",
        currentUser.getUserId(),
        currentUser.getDeptId(),
        usedFallback);
    return true;
  }
}
