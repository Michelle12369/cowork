package com.erd.cowork.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * Verifies that {@link AnalysisAgentProperties} binds correctly from the default {@code
 * application.properties} configuration.
 */
@SpringBootTest
class AnalysisAgentPropertiesTest {

  @Autowired AnalysisAgentProperties properties;

  @Test
  void defaultConfig_maxInMemorySizeMb_is64() {
    // Live bug fix: DASHBOARD_HTML SSE events can exceed Spring WebClient's default 256KB
    // maxInMemorySize; this default gives headroom for 5 tables x 5000 rows of wide data.
    assertThat(properties.maxInMemorySizeMb()).isEqualTo(64);
  }

  @Test
  void fourArgConstructor_defaultsToolCallTimeoutTo60() {
    AnalysisAgentProperties fourArgProperties =
        new AnalysisAgentProperties("http://localhost:8000", "/data/uploads", 180, 64);

    assertThat(fourArgProperties.toolCallTimeoutSeconds()).isEqualTo(60);
  }

  @Test
  void sevenArgConstructor_defaultsToolCallTimeoutTo60() {
    AnalysisAgentProperties sevenArgProperties =
        new AnalysisAgentProperties(
            "http://localhost:8000", "/data/uploads", 180, 64, "token", "X-A", "X-B");

    assertThat(sevenArgProperties.toolCallTimeoutSeconds()).isEqualTo(60);
    assertThat(sevenArgProperties.ssoTokenHeader()).isEqualTo("X-A");
  }

  @Test
  void canonicalConstructor_keepsExplicitToolCallTimeout() {
    AnalysisAgentProperties canonicalProperties =
        new AnalysisAgentProperties(
            "http://localhost:8000", "/data/uploads", 180, 64, "token", "X-A", "X-B", 15);

    assertThat(canonicalProperties.toolCallTimeoutSeconds()).isEqualTo(15);
  }
}
