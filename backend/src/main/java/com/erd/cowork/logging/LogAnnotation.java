package com.erd.cowork.logging;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 標在 service 類別或方法上,由 {@link LogAnnotationAspect} 在方法進入/離開時各印一行 log(附 userId)。標在類別上=該類所有 public
 * 方法都印;標在方法上只印該方法,且方法上的設定優先於 類別上的設定。
 *
 * <p>參數預設不印(避免誤將大量或敏感資料寫進 log);需要時開 {@link #args()},並可用 {@link #maxArgsLength()} 與 {@link
 * #onOverflow()} 控制超長時截斷或整段略去。
 */
@Target({ElementType.TYPE, ElementType.METHOD})
@Retention(RetentionPolicy.RUNTIME)
public @interface LogAnnotation {

  /** 是否印出方法參數。預設 {@code false}。 */
  boolean args() default false;

  /** 參數字串總長度上限;超過依 {@link #onOverflow()} 處理。僅在 {@link #args()} 為 true 時生效。 */
  int maxArgsLength() default 500;

  /** 參數字串超過 {@link #maxArgsLength()} 時:截斷加「…」或整段略去。 */
  ArgsOverflow onOverflow() default ArgsOverflow.TRUNCATE;
}
