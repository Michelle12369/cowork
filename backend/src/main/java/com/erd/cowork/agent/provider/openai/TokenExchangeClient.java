package com.erd.cowork.agent.provider.openai;

import com.erd.cowork.config.AgentProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicReference;
import lombok.ToString;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.Assert;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

/**
 * Client for j1→j2 token exchange authentication.
 *
 * <p>Caches the j2 token until TTL expiry; on cache miss or invalidation, exchanges for a fresh
 * one. Concurrent callers may both exchange (last write wins) — simpler than locking, at the cost
 * of at most one extra exchange under contention.
 *
 * <p>The j1 service account key is resolved fresh on every exchange (preferring {@code
 * serviceAccountKeyFile} over the inline key), so a rotated Kubernetes secret-mounted file takes
 * effect without a restart.
 */
@Slf4j
@Component
@ConditionalOnProperty(
    prefix = "erd.agent.open-ai-compatible",
    name = "auth-mode",
    havingValue = "token-exchange")
@ToString(onlyExplicitlyIncluded = true)
public class TokenExchangeClient {

  private final WebClient webClient;
  private final AgentProperties.OpenAiCompatible.TokenExchange config;
  private final ObjectMapper objectMapper;
  private final AtomicReference<CachedToken> cachedToken = new AtomicReference<>();

  private record CachedToken(String token, Instant expiresAt) {
    boolean isValid() {
      return Instant.now().isBefore(expiresAt);
    }
  }

  public TokenExchangeClient(
      AgentProperties props, WebClient.Builder webClientBuilder, ObjectMapper objectMapper) {
    this.config =
        Objects.requireNonNull(
            props.openAiCompatible().tokenExchange(),
            "erd.agent.open-ai-compatible.token-exchange config is required when"
                + " auth-mode=token-exchange");
    Assert.isTrue(
        StringUtils.hasText(config.headerName()),
        "erd.agent.open-ai-compatible.token-exchange requires header-name to be set");
    Assert.isTrue(
        StringUtils.hasText(config.serviceAccountKeyFile())
            || StringUtils.hasText(config.serviceAccountKey()),
        "erd.agent.open-ai-compatible.token-exchange requires either service-account-key or"
            + " service-account-key-file to be set");
    this.webClient = webClientBuilder.build(); // no base URL — use full URL per request
    this.objectMapper = objectMapper;
  }

  /** Returns a cached token if still valid, otherwise exchanges for a new one. */
  public Mono<String> getToken() {
    CachedToken cached = cachedToken.get();
    if (cached != null && cached.isValid()) {
      return Mono.just(cached.token());
    }
    return exchangeToken();
  }

  /** Clears the cached token, forcing the next {@link #getToken()} call to exchange. */
  public void invalidate() {
    cachedToken.set(null);
    log.debug("token cache invalidated");
  }

  private Mono<String> exchangeToken() {
    return Mono.fromCallable(this::resolveServiceAccountKey)
        .subscribeOn(Schedulers.boundedElastic())
        .flatMap(
            serviceAccountKey ->
                webClient
                    .post()
                    .uri(config.url())
                    .bodyValue(Map.of("key", serviceAccountKey))
                    .retrieve()
                    .bodyToMono(JsonNode.class))
        .map(
            jsonNode -> {
              String token = jsonNode.path("token").asText();
              // Concurrent callers may both exchange; last write wins (see class javadoc).
              cachedToken.set(
                  new CachedToken(token, Instant.now().plusSeconds(config.tokenTtlSeconds())));
              log.info("token exchange completed");
              return token;
            });
  }

  /**
   * Resolves the j1 service account key for this exchange; file path wins when configured. Never
   * logs the key or file contents.
   */
  private String resolveServiceAccountKey() {
    if (StringUtils.hasText(config.serviceAccountKeyFile())) {
      Path keyFilePath = Path.of(config.serviceAccountKeyFile());
      log.debug("reading service account key from file: {}", keyFilePath);
      try {
        return Files.readString(keyFilePath, StandardCharsets.UTF_8).strip();
      } catch (IOException e) {
        throw new IllegalStateException(
            "Failed to read token-exchange service account key from file: " + keyFilePath, e);
      }
    }
    return config.serviceAccountKey();
  }
}
