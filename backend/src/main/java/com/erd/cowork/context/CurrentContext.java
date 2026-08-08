package com.erd.cowork.context;

import java.util.function.Function;
import org.springframework.stereotype.Component;

/**
 * 注入用門面:讀 {@link CoworkContextHolder} 的當前 {@link CoworkContext},提供 null-safe 取值。 取代原本的
 * {@code @RequestScope CurrentUser}——service 一樣 constructor 注入這個,method 簽名 NEVER 傳
 * userId。非請求執行緒(排程/未進 filter)時各取值回傳 {@code null}。
 */
@Component
public class CurrentContext {

  public String userId() {
    return value(CoworkContext::userId);
  }

  public String deptId() {
    return value(CoworkContext::deptId);
  }

  /** SSO 端點;只有 internal 線會填,external 線為 null。 */
  public String ssoUrl() {
    return value(CoworkContext::ssoUrl);
  }

  /** SSO token(機密,NEVER log);只有 internal 線會填,external 線為 null。 */
  public String ssoToken() {
    return value(CoworkContext::ssoToken);
  }

  private String value(Function<CoworkContext, String> getter) {
    CoworkContext context = CoworkContextHolder.get();
    return context == null ? null : getter.apply(context);
  }
}
