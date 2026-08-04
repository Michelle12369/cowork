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
}
