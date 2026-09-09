package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.ConstructorBinding;

/**
 * @param baseUrl agent-service 位址(如 http://agent-service:8000)
 * @param sourceRoot agent-service 視角的上傳檔根目錄;storageKey 接在其後組成完整路徑
 * @param requestTimeoutSeconds SSE 事件間的閒置逾時秒數(非總時長)。語意為 {@code Flux#timeout(Duration)},計時器隨每個事件重置;
 *     逾時由 {@code LangGraphAnalysisProvider} 轉為 {@code ErrorEvent},避免 {@code TimeoutException} 直接傳播
 * @param maxInMemorySizeMb WebClient 單一 SSE data line 緩衝上限(MB)。{@code DASHBOARD_HTML} 事件把完整
 *     dashboard HTML 與 spec JSON 塞進同一行,遠超 Spring 預設的 256KB,故需調大
 * @param bearerToken 打 agent-service 全部請求附帶的固定 Bearer token;空字串=不附帶(dev 預設)。 值一律由環境變數注入,NEVER 寫死於
 *     properties 檔
 * @param ssoTokenHeader 出站 {@code /chat} 請求上,ssoToken 值所附的 HTTP header 名稱;預設 {@code
 *     X-SSO-Token},internal 環境的 gateway 若要求不同名稱可另外配置(需與 deepagent 端 {@code SSO_TOKEN_HEADER} 保持一致)
 * @param ssoUrlHeader 出站 {@code /chat} 請求上,ssoUrl 值所附的 HTTP header 名稱;預設 {@code X-SSO-Url},語意同
 *     {@code ssoTokenHeader}
 */
@ConfigurationProperties(prefix = "erd.agent.analysis")
public record AnalysisAgentProperties(
    String baseUrl,
    String sourceRoot,
    int requestTimeoutSeconds,
    int maxInMemorySizeMb,
    String bearerToken,
    String ssoTokenHeader,
    String ssoUrlHeader) {

  private static final String DEFAULT_SSO_TOKEN_HEADER = "X-SSO-Token";
  private static final String DEFAULT_SSO_URL_HEADER = "X-SSO-Url";

  /** 多建構子下指定 Spring 綁定用 canonical——不標會被當 JavaBean 找無參建構子而炸。 */
  @ConstructorBinding
  public AnalysisAgentProperties {}

  /** 既有 4 參數建構——bearerToken 預設空(不附帶)、header 名稱預設值,既有呼叫端與測試零改動。 */
  public AnalysisAgentProperties(
      String baseUrl, String sourceRoot, int requestTimeoutSeconds, int maxInMemorySizeMb) {
    this(baseUrl, sourceRoot, requestTimeoutSeconds, maxInMemorySizeMb, "");
  }

  /** 既有 5 參數建構(帶 bearerToken)——header 名稱預設值,既有呼叫端與測試零改動。 */
  public AnalysisAgentProperties(
      String baseUrl,
      String sourceRoot,
      int requestTimeoutSeconds,
      int maxInMemorySizeMb,
      String bearerToken) {
    this(
        baseUrl,
        sourceRoot,
        requestTimeoutSeconds,
        maxInMemorySizeMb,
        bearerToken,
        DEFAULT_SSO_TOKEN_HEADER,
        DEFAULT_SSO_URL_HEADER);
  }
}
