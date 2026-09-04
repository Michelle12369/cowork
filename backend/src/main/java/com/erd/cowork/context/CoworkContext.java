package com.erd.cowork.context;

/**
 * 呼叫者身分與 SSO 憑證的不可變值物件,取代原本 {@code @RequestScope} 的 CurrentUser。放在 {@link CoworkContextHolder} 的
 * ThreadLocal 裡,故可在 async 邊界以 capture/restore 跨執行緒攜帶。
 *
 * <p>{@code ssoUrl}/{@code ssoToken} 由 internal 側的身分 filter 填,或 dev 線 {@code CurrentUserFilter}
 * 讀到對應 SSO header 時填;dev 前端不送這兩個 header 是常態,{@code CurrentUserFilter} 缺席/空白時補固定 dev 假值(而非留
 * null)——deepagent connector 模式兩者皆必須,留 null 會讓 dev 線整條 frontend→Java→deepagent→MCP 鏈路無法在本機走通。{@code
 * deptId} 僅 internal 填,external(X-User-Id)線恆 null。
 *
 * <p>{@code ssoToken} 是機密:{@link #toString()} 一律遮罩,NEVER 讓它進 log。{@code ssoUrl} 敏感度較低,但同樣以 {@code
 * ***} 遮罩——比照 {@link com.erd.cowork.agent.model.AgentRequest#toString()} 的一致做法。
 */
public record CoworkContext(String userId, String deptId, String ssoUrl, String ssoToken) {

  /** external(X-User-Id)線:只有 userId,其餘留 null。 */
  public static CoworkContext external(String userId) {
    return new CoworkContext(userId, null, null, null);
  }

  /**
   * external 線 + dev 環境讀到的 SSO header(見 {@code CurrentUserFilter}):{@code ssoUrl}/{@code ssoToken}
   * 由呼叫端決定(真實 header 值或 dev fallback 假值皆可能),此工廠本身不做 null 判斷或補值——{@code deptId} 維持 external 線的既定行為,恆
   * null。
   */
  public static CoworkContext external(String userId, String ssoUrl, String ssoToken) {
    return new CoworkContext(userId, null, ssoUrl, ssoToken);
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
        + (ssoUrl == null ? "null" : "***")
        + ", ssoToken="
        + (ssoToken == null ? "null" : "***")
        + "]";
  }
}
