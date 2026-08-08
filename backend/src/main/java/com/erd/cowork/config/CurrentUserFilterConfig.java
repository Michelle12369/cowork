package com.erd.cowork.config;

import com.erd.cowork.context.CurrentUserFilter;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Configuration;

/**
 * Logs, at startup, whether {@link CurrentUserFilter} was registered, so a misconfiguration (e.g.
 * {@code tsso.enabled=true} without the internal identity filter) is visible immediately instead of
 * surfacing later as an empty {@code CoworkContext}.
 */
@Configuration
@RequiredArgsConstructor
@Slf4j
public class CurrentUserFilterConfig {

  private final ObjectProvider<CurrentUserFilter> currentUserFilterProvider;

  @PostConstruct
  void logRegistrationStatus() {
    CurrentUserFilter currentUserFilter = currentUserFilterProvider.getIfAvailable();
    if (currentUserFilter == null) {
      // internal 若尚未提供同層 filter,CoworkContext 會全空到第一次查詢才爆,故啟動時先講明白。
      log.warn(
          "CurrentUserFilter not registered (tsso.enabled=true); identity MUST come from the"
              + " internal identity filter");
      return;
    }
    log.info("CurrentUserFilter registered (tsso.enabled=false)");
  }
}
