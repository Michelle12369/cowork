package com.erd.cowork.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "erd.agent")
public record AgentProperties(String provider, OpenAiCompatible openAiCompatible, Repair repair) {

  public record OpenAiCompatible(
      String baseUrl,
      String apiKey,
      String model,
      int contextWindow,
      String chatCompletionsPath,
      String authMode,
      TokenExchange tokenExchange) {

    public record TokenExchange(
        String url,
        String serviceAccountKey,
        String serviceAccountKeyFile,
        String headerName,
        int tokenTtlSeconds) {}
  }

  public record Repair(boolean enabled) {}
}
