package com.erd.cowork.artifact;

import com.erd.cowork.config.ArtifactRewriteProperties;
import jakarta.annotation.PostConstruct;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.util.CollectionUtils;
import org.springframework.util.StringUtils;

/**
 * Pre-compiles CDN-rewrite regex patterns from {@link ArtifactRewriteProperties} at startup so
 * per-line regex compilation cost is paid once rather than on every serve request.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class ArtifactCdnRewriter {

  /**
   * Fallback profile for artifacts that pre-date per-profile tracking (null/blank {@code
   * assetProfile}), so legacy artifacts keep rewriting the same as before.
   */
  static final String LEGACY_DEFAULT_PROFILE = "tw3-ec5";

  /**
   * A pre-compiled rewrite rule pairing a compiled {@link Pattern} with its verbatim replacement
   * string.
   */
  public record CompiledRule(Pattern pattern, String replacement) {}

  private final ArtifactRewriteProperties properties;

  /** Immutable map of profile name → compiled rules, populated at {@link #init()} time. */
  private Map<String, List<CompiledRule>> compiledProfiles;

  /**
   * Compiles all profile patterns at startup. A malformed regex will cause an unchecked exception
   * that aborts application boot — intentional fail-fast behaviour.
   */
  @PostConstruct
  public void init() {
    Map<String, List<CompiledRule>> compiled = new HashMap<>();
    if (!CollectionUtils.isEmpty(properties.profiles())) {
      properties
          .profiles()
          .forEach(
              (profileName, rules) -> {
                List<CompiledRule> compiledRules =
                    rules.stream()
                        .map(
                            rule ->
                                new CompiledRule(
                                    Pattern.compile(rule.pattern()), rule.replacement()))
                        .toList();
                compiled.put(profileName, compiledRules);
              });
    }
    compiledProfiles = Map.copyOf(compiled);
    log.info("ArtifactCdnRewriter: compiled profiles={}", compiledProfiles.keySet());
  }

  /**
   * Resolves the effective rewrite rules for an artifact's recorded asset profile. Never returns
   * {@code null}.
   *
   * <p>Resolution order:
   *
   * <ol>
   *   <li>Use {@code assetProfile} when non-blank.
   *   <li>Fall back to {@link #LEGACY_DEFAULT_PROFILE} when {@code assetProfile} is null or blank
   *       (artifacts created before per-profile tracking was introduced).
   *   <li>If the resolved profile name is not found in the configured profiles, log a single
   *       warning and fall back to the current-profile rules (never throws).
   * </ol>
   *
   * @param assetProfile the artifact's recorded asset profile; may be null or blank
   * @param artifactId artifact UUID, used only for warn-log context
   * @return the effective compiled rule list, never {@code null}
   */
  public List<CompiledRule> resolveRules(String assetProfile, String artifactId) {
    String resolvedProfile =
        StringUtils.hasText(assetProfile) ? assetProfile : LEGACY_DEFAULT_PROFILE;

    List<CompiledRule> rewriteRules = rulesForProfile(resolvedProfile);
    if (rewriteRules == null) {
      log.warn(
          "Unknown asset profile '{}' for artifact {}; falling back to current-profile rules",
          resolvedProfile,
          artifactId);
      rewriteRules = currentProfileRules();
    }
    return rewriteRules;
  }

  /**
   * Returns the pre-compiled rule list for the given profile name, or {@code null} if the profile
   * is not configured.
   */
  private List<CompiledRule> rulesForProfile(String profileName) {
    return compiledProfiles.get(profileName);
  }

  /**
   * Returns the pre-compiled rule list for the current (default) profile as declared in {@link
   * ArtifactRewriteProperties#currentProfile()}.
   */
  private List<CompiledRule> currentProfileRules() {
    return compiledProfiles.get(properties.currentProfile());
  }

  /**
   * Applies a list of compiled rewrite rules sequentially to a single line of HTML text. Each
   * rule's replacement is applied to the output of the preceding rule.
   *
   * @param line a single line of HTML
   * @param rules pre-compiled rules to apply in order
   * @return the rewritten line
   */
  public String rewriteLine(String line, List<CompiledRule> rules) {
    String result = line;
    for (CompiledRule compiledRule : rules) {
      result = compiledRule.pattern().matcher(result).replaceAll(compiledRule.replacement());
    }
    return result;
  }
}
