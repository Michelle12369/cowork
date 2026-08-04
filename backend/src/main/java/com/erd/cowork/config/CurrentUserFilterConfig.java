package com.erd.cowork.config;

import com.erd.cowork.context.CurrentUserFilter;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Configuration;

/**
 * Logs, at startup, whether {@link CurrentUserFilter} (the v1 identity source) was registered. The
 * filter itself self-registers as a {@code @Component}; this class only reports the outcome so a
 * misconfiguration (e.g. {@code tsso.enabled=true} without the internal identity filter in place)
 * is visible immediately instead of surfacing later as an empty {@code CurrentUser}.
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
      // tsso.enabled=true:身分改由 internal 側同層的 filter 提供。internal 若尚未提供,
      // CurrentUser 會全空而症狀延後到第一次查詢才爆——故在啟動時就講明白。
      log.warn(
          "CurrentUserFilter not registered (tsso.enabled=true); identity MUST come from the"
              + " internal identity filter");
      return;
    }
    log.info("CurrentUserFilter registered (tsso.enabled=false)");
  }
}
