package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param baseUrl agent-service 位址(如 http://agent-service:8000)
 * @param sourceRoot agent-service 視角的上傳檔根目錄;storageKey 接在其後組成完整路徑
 * @param requestTimeoutSeconds 整輪請求的總時長上限,語意為 netty {@code HttpClient#responseTimeout(Duration)}
 *     (從請求送出到收完整份回應,不隨個別 SSE 事件重置);逾時由 {@code LangGraphAnalysisProvider} 轉為 {@code
 *     ErrorEvent},避免例外直接傳播
 * @param maxInMemorySizeMb WebClient 單一 SSE data line 緩衝上限(MB)。{@code DASHBOARD_HTML} 事件把完整
 *     dashboard HTML 與 spec JSON 塞進同一行,遠超 Spring 預設的 256KB,故需調大
 */
@ConfigurationProperties(prefix = "erd.agent.analysis")
public record AnalysisAgentProperties(
    String baseUrl, String sourceRoot, int requestTimeoutSeconds, int maxInMemorySizeMb) {}
