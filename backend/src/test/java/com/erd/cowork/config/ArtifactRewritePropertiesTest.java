package com.erd.cowork.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * Verifies that {@link ArtifactRewriteProperties} binds correctly from the default {@code
 * application.yml} configuration.
 */
@SpringBootTest
class ArtifactRewritePropertiesTest {

  @Autowired ArtifactRewriteProperties properties;

  @Test
  void defaultConfig_currentProfile_isTw3Ec5() {
    assertThat(properties.currentProfile()).isEqualTo("tw3-ec5");
  }

  @Test
  void defaultConfig_tw3Ec5Profile_hasTwoRules() {
    assertThat(properties.profiles()).containsKey("tw3-ec5");
    assertThat(properties.profiles().get("tw3-ec5")).hasSize(2);
  }

  @Test
  void defaultConfig_tw3Ec5Profile_firstRule_matchesTailwindPattern() {
    ArtifactRewriteProperties.RewriteRule tailwindRule =
        properties.profiles().get("tw3-ec5").get(0);
    assertThat(tailwindRule.pattern()).contains("tailwindcss");
    assertThat(tailwindRule.replacement()).isEqualTo("/vendor/tailwind-play-v3.js");
  }

  @Test
  void defaultConfig_tw3Ec5Profile_secondRule_matchesEchartsPattern() {
    ArtifactRewriteProperties.RewriteRule echartsRule = properties.profiles().get("tw3-ec5").get(1);
    assertThat(echartsRule.pattern()).contains("echarts@5");
    assertThat(echartsRule.replacement()).isEqualTo("/vendor/echarts-v5.min.js");
  }
}
