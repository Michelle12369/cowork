package com.erd.cowork.logging;

/** {@link LogAnnotation#args()} 開啟後,參數字串超過 {@code maxArgsLength} 時的處理方式。 */
public enum ArgsOverflow {
  /** 截斷到上限長度並補「…」。 */
  TRUNCATE,
  /** 整段參數不印,只保留長度提示(避免大量/敏感內容進 log)。 */
  OMIT
}
