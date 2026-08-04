package com.erd.cowork.config;

import java.util.List;
import java.util.Map;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Binds {@code erd.artifact.rewrite.*} configuration that governs per-profile CDN-to-vendor URL
 * rewriting applied when serving artifacts.
 *
 * <p>Example {@code application.properties}:
 *
 * <pre>{@code
 * erd.artifact.rewrite.current-profile=tw3-ec5
 * erd.artifact.rewrite.profiles.tw3-ec5[0].pattern=https://cdn\\.tailwindcss\\.com[^"']*
 * erd.artifact.rewrite.profiles.tw3-ec5[0].replacement=/vendor/tailwind-play-v3.js
 * }</pre>
 */
@ConfigurationProperties(prefix = "erd.artifact.rewrite")
public record ArtifactRewriteProperties(
    String currentProfile, Map<String, List<RewriteRule>> profiles) {

  /**
   * A single rewrite rule: a raw regex {@code pattern} and its verbatim {@code replacement} string.
   */
  public record RewriteRule(String pattern, String replacement) {}
}
