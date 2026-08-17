package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.bson.Document;
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
    // 對字面內容斷言（非自我引用 DEFAULT_URI）：directConnection=true 是安全關鍵——少了它，
    // 成員發現會拿到 rs.initiate 用的容器內主機名，未來誤改也能讓這裡紅燈。
    assertThat(resolved)
        .startsWith("mongodb://localhost:27017")
        .contains("cowork-test")
        .contains("directConnection=true");
  }

  @Test
  void resolveConnectionString_envBlank_returnsLocalDefault() {
    String resolved =
        ReplicaSetMongoTestInitializer.resolveConnectionString(Map.of("ERD_TEST_MONGO_URI", "  "));
    assertThat(resolved).isEqualTo(ReplicaSetMongoTestInitializer.DEFAULT_URI);
  }

  @Test
  void redactToHosts_uriWithCredentials_omitsUserInfo() {
    String redacted =
        ReplicaSetMongoTestInitializer.redactToHosts(
            "mongodb://ci-user:s3cr3t@ci-mongo:27017/cowork-test?directConnection=true");
    assertThat(redacted).isEqualTo("ci-mongo:27017");
    assertThat(redacted).doesNotContain("s3cr3t").doesNotContain("ci-user");
  }

  @Test
  void redactToHosts_uriWithoutCredentials_returnsHostOnly() {
    String redacted =
        ReplicaSetMongoTestInitializer.redactToHosts(ReplicaSetMongoTestInitializer.DEFAULT_URI);
    assertThat(redacted).isEqualTo("localhost:27017");
  }

  @Test
  void isWritablePrimary_helloReportsTrue_returnsTrue() {
    boolean writablePrimary =
        ReplicaSetMongoTestInitializer.isWritablePrimary(new Document("isWritablePrimary", true));
    assertThat(writablePrimary).isTrue();
  }

  @Test
  void isWritablePrimary_helloReportsFalse_returnsFalse() {
    boolean writablePrimary =
        ReplicaSetMongoTestInitializer.isWritablePrimary(new Document("isWritablePrimary", false));
    assertThat(writablePrimary).isFalse();
  }

  @Test
  void isWritablePrimary_fieldMissing_returnsFalse() {
    boolean writablePrimary = ReplicaSetMongoTestInitializer.isWritablePrimary(new Document());
    assertThat(writablePrimary).isFalse();
  }
}
