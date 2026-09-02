package com.erd.cowork.config;

import io.swagger.v3.oas.models.parameters.HeaderParameter;
import lombok.RequiredArgsConstructor;
import org.springdoc.core.customizers.OpenApiCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@RequiredArgsConstructor
public class OpenApiConfig {

  // Fallback only — AnalysisAgentProperties is always bound (@ConfigurationPropertiesScan) with
  // its own defaults, so this never actually triggers; kept simple rather than making the field
  // itself nullable/optional.
  private static final String FALLBACK_SSO_TOKEN_HEADER = "X-SSO-Token";
  private static final String FALLBACK_SSO_URL_HEADER = "X-SSO-Url";

  private final AnalysisAgentProperties analysisAgentProperties;

  /**
   * Adds the optional {@code X-User-Id} header, plus the two SSO headers named by {@link
   * AnalysisAgentProperties#ssoTokenHeader()}/{@link AnalysisAgentProperties#ssoUrlHeader()}, to
   * every operation so the identity carried by {@link com.erd.cowork.context.CoworkContextHolder}
   * stays documented after it was removed from controller signatures. All three are optional;
   * {@code X-User-Id} missing/blank defaults to {@code local-dev}, the SSO headers missing/blank
   * default to fixed dev placeholders ({@code local-dev-sso-token}/{@code http://local-dev-sso}) —
   * see {@code CurrentUserFilter}'s Javadoc for why the SSO fields fall back instead of staying
   * null.
   */
  @Bean
  public OpenApiCustomizer currentUserHeaderCustomizer() {
    String ssoTokenHeaderName =
        analysisAgentProperties.ssoTokenHeader() == null
            ? FALLBACK_SSO_TOKEN_HEADER
            : analysisAgentProperties.ssoTokenHeader();
    String ssoUrlHeaderName =
        analysisAgentProperties.ssoUrlHeader() == null
            ? FALLBACK_SSO_URL_HEADER
            : analysisAgentProperties.ssoUrlHeader();
    return openApi ->
        openApi
            .getPaths()
            .values()
            .forEach(
                pathItem ->
                    pathItem
                        .readOperations()
                        .forEach(
                            operation -> {
                              operation.addParametersItem(
                                  new HeaderParameter()
                                      .name("X-User-Id")
                                      .required(false)
                                      .description(
                                          "Caller identity. Optional; defaults to 'local-dev' when"
                                              + " absent or blank."));
                              operation.addParametersItem(
                                  new HeaderParameter()
                                      .name(ssoTokenHeaderName)
                                      .required(false)
                                      .description(
                                          "SSO token, same header used outbound to deepagent."
                                              + " Optional; absent/blank defaults to"
                                              + " 'local-dev-sso-token'."));
                              operation.addParametersItem(
                                  new HeaderParameter()
                                      .name(ssoUrlHeaderName)
                                      .required(false)
                                      .description(
                                          "SSO url, same header used outbound to deepagent."
                                              + " Optional; absent/blank defaults to"
                                              + " 'http://local-dev-sso'."));
                            }));
  }
}
