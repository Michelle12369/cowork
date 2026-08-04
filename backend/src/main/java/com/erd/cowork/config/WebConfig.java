package com.erd.cowork.config;

import com.erd.cowork.context.CurrentUserInterceptor;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
@RequiredArgsConstructor
@Slf4j
public class WebConfig implements WebMvcConfigurer {

  private final ObjectProvider<CurrentUserInterceptor> currentUserInterceptorProvider;

  @Override
  public void addInterceptors(InterceptorRegistry registry) {
    CurrentUserInterceptor interceptor = currentUserInterceptorProvider.getIfAvailable();
    if (interceptor == null) {
      // tsso.enabled=true:身分改由公司 TSSO 的 WebMvcConfigurer 提供。公司若尚未提供,
      // CurrentUser 會全空而症狀延後到第一次查詢才爆——故在啟動時就講明白。
      log.warn(
          "CurrentUserInterceptor not registered (tsso.enabled=true); identity MUST come from"
              + " the TSSO configurer");
      return;
    }
    log.info("CurrentUserInterceptor registered (tsso.enabled=false)");
    registry.addInterceptor(interceptor).excludePathPatterns("/actuator/**");
  }
}
