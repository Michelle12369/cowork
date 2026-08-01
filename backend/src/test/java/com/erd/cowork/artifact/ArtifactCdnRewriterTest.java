package com.erd.cowork.artifact;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.artifact.ArtifactCdnRewriter.CompiledRule;
import com.erd.cowork.config.ArtifactRewriteProperties;
import com.erd.cowork.config.ArtifactRewriteProperties.RewriteRule;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class ArtifactCdnRewriterTest {

  private ArtifactCdnRewriter rewriter;

  @BeforeEach
  void setUp() {
    rewriter = buildRewriter(twoProfileProperties());
  }

  // ── resolveRules: known profile ────────────────────────────────────────────

  @Test
  void resolveRules_knownProfile_returnsThatProfilesRules() {
    List<CompiledRule> rules = rewriter.resolveRules("tw4-ec5", "art-1");

    assertThat(rules).isNotNull();
    assertThat(rewriter.rewriteLine("<script src=\"https://example.com/cdn/tw4/x.js\">", rules))
        .contains("/vendor/tailwind-v4.js");
    // tw3 rule must NOT be present in the tw4 profile's rules.
    assertThat(rewriter.rewriteLine("<script src=\"https://cdn.tailwindcss.com\">", rules))
        .contains("cdn.tailwindcss.com");
  }

  // ── resolveRules: null/blank profile falls back to legacy default ─────────

  @Test
  void resolveRules_nullProfile_fallsBackToLegacyDefaultProfileRules() {
    List<CompiledRule> rules = rewriter.resolveRules(null, "art-null");

    assertThat(rules).isNotNull();
    assertThat(rewriter.rewriteLine("<script src=\"https://cdn.tailwindcss.com\">", rules))
        .contains("/vendor/tailwind-play-v3.js");
  }

  @Test
  void resolveRules_blankProfile_fallsBackToLegacyDefaultProfileRules() {
    List<CompiledRule> rules = rewriter.resolveRules("  ", "art-blank");

    assertThat(rules).isNotNull();
    assertThat(rewriter.rewriteLine("<script src=\"https://cdn.tailwindcss.com\">", rules))
        .contains("/vendor/tailwind-play-v3.js");
  }

  // ── resolveRules: unknown profile falls back to current-profile rules ──────

  @Test
  void resolveRules_unknownProfile_fallsBackToCurrentProfileRulesNeverNull() {
    List<CompiledRule> rules = rewriter.resolveRules("future-profile-v99", "art-unknown");

    // Never null; falls back to current-profile (tw3-ec5) rules.
    assertThat(rules).isNotNull();
    assertThat(rewriter.rewriteLine("<script src=\"https://cdn.tailwindcss.com\">", rules))
        .contains("/vendor/tailwind-play-v3.js");
  }

  // ── rewriteLine ────────────────────────────────────────────────────────────

  @Test
  void rewriteLine_noMatchingCdnUrl_lineUnchanged() {
    List<CompiledRule> rules = rewriter.resolveRules("tw3-ec5", "art-plain");

    String line = "<p>plain content</p>";

    assertThat(rewriter.rewriteLine(line, rules)).isEqualTo(line);
  }

  // ── helpers ───────────────────────────────────────────────────────────────

  private static ArtifactRewriteProperties twoProfileProperties() {
    return new ArtifactRewriteProperties(
        "tw3-ec5",
        Map.of(
            "tw3-ec5",
            List.of(
                new RewriteRule(
                    "https://cdn\\.tailwindcss\\.com[^\"']*", "/vendor/tailwind-play-v3.js"),
                new RewriteRule(
                    "https://cdn\\.jsdelivr\\.net/npm/echarts@5[^\"']*",
                    "/vendor/echarts-v5.min.js")),
            "tw4-ec5",
            List.of(
                new RewriteRule(
                    "https://example\\.com/cdn/tw4[^\"']*", "/vendor/tailwind-v4.js"))));
  }

  private static ArtifactCdnRewriter buildRewriter(ArtifactRewriteProperties properties) {
    ArtifactCdnRewriter rewriter = new ArtifactCdnRewriter(properties);
    rewriter.init();
    return rewriter;
  }
}
