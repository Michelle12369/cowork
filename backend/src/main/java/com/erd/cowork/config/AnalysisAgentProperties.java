package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param baseUrl agent-service 位址(如 http://agent-service:8000)
 * @param sourceRoot agent-service 視角的上傳檔根目錄;storageKey 接在其後組成完整路徑
 * @param requestTimeoutSeconds 整輪請求的真·總時長上限(從 {@code LangGraphAnalysisProvider} 訂閱事件流那刻起算,不因任何
 *     inbound 訊號重置)。**不是** netty {@code HttpClient#responseTimeout(Duration)}——該 API 底層是 {@code
 *     ReadTimeoutHandler},由每一個 inbound byte(含 15s keepalive ping)重置,無法充當總時長上限;真正的上限實作在 Reactor
 *     層(共用、只發一次訊號的 deadline {@code Mono},見該類別 {@code generate()})。逾時由 {@code
 *     LangGraphAnalysisProvider} 轉為 {@code ErrorEvent},避免例外直接傳播
 * @param maxInMemorySizeMb WebClient 單一 SSE data line 緩衝上限(MB)。{@code DASHBOARD_HTML} 事件把完整
 *     dashboard HTML 與 spec JSON 塞進同一行,遠超 Spring 預設的 256KB,故需調大
 */
@ConfigurationProperties(prefix = "erd.agent.analysis")
public record AnalysisAgentProperties(
    String baseUrl, String sourceRoot, int requestTimeoutSeconds, int maxInMemorySizeMb) {}
