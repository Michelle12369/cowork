package com.erd.cowork.context;

/**
 * 呼叫者身分與 SSO 憑證的不可變值物件,取代原本 {@code @RequestScope} 的 CurrentUser。放在 {@link CoworkContextHolder} 的
 * ThreadLocal 裡,故可在 async 邊界以 capture/restore 跨執行緒攜帶。
 *
 * <p>{@code ssoUrl}/{@code ssoToken} 只有 internal 側的身分 filter 會填;external(X-User-Id)線 留 null。{@code
 * deptId} 同理只有 internal 填。
 *
 * <p>{@code ssoToken} 是機密:{@link #toString()} 一律遮罩,NEVER 讓它進 log。
 */
public record CoworkContext(String userId, String deptId, String ssoUrl, String ssoToken) {

  /** external(X-User-Id)線:只有 userId,其餘留 null。 */
  public static CoworkContext external(String userId) {
    return new CoworkContext(userId, null, null, null);
  }

  /** 排程/系統作業:沒有真實 user,以 {@code system:<job>} 標示。 */
  public static CoworkContext system(String job) {
    return new CoworkContext("system:" + job, null, null, null);
  }

  @Override
  public String toString() {
    return "CoworkContext[userId="
        + userId
        + ", deptId="
        + deptId
        + ", ssoUrl="
        + ssoUrl
        + ", ssoToken="
        + (ssoToken == null ? "null" : "***")
        + "]";
  }
}
