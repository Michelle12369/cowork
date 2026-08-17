package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.Test;

class ReplicaSetMongoTestInitializerTest {

  @Test
  void resolveConnectionString_envOverridePresent_returnsOverride() {
    String resolved =
        ReplicaSetMongoTestInitializer.resolveConnectionString(
            Map.of(
                "ERD_TEST_MONGO_URI",
                "mongodb://ci-mongo:27017/cowork-test?directConnection=true"));
    assertThat(resolved).isEqualTo("mongodb://ci-mongo:27017/cowork-test?directConnection=true");
  }

  @Test
  void resolveConnectionString_envAbsent_returnsLocalDefault() {
    String resolved = ReplicaSetMongoTestInitializer.resolveConnectionString(Map.of());
    assertThat(resolved).isEqualTo(ReplicaSetMongoTestInitializer.DEFAULT_URI);
  }

  @Test
  void resolveConnectionString_envBlank_returnsLocalDefault() {
    String resolved =
        ReplicaSetMongoTestInitializer.resolveConnectionString(Map.of("ERD_TEST_MONGO_URI", "  "));
    assertThat(resolved).isEqualTo(ReplicaSetMongoTestInitializer.DEFAULT_URI);
  }
}
