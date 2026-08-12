package com.erd.cowork.support;

import java.util.Map;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.env.MapPropertySource;

/**
 * 全域測試 context 初始化器：把 {@link EmbeddedReplicaSetMongo} 的連線字串（含 {@code replicaSet=rs0}）注入每個 Spring 測試
 * context 的 environment，取代 spring3x standalone flapdoodle autoconfig（該 autoconfig 已由 {@code
 * src/test/resources/config/application.properties} 的 {@code spring.autoconfigure.exclude} 排除）——所有
 * {@code @SpringBootTest}/{@code @DataMongoTest} 測試才都連得到支援交易的 replica set。
 *
 * <p>註冊於 {@code META-INF/spring.factories} 的 {@code ApplicationContextInitializer} key，對所有測試
 * context 生效，無需逐檔加註解。
 */
public class ReplicaSetMongoTestInitializer
    implements ApplicationContextInitializer<ConfigurableApplicationContext> {

  @Override
  public void initialize(ConfigurableApplicationContext applicationContext) {
    String connectionString = EmbeddedReplicaSetMongo.start();
    applicationContext
        .getEnvironment()
        .getPropertySources()
        .addFirst(
            new MapPropertySource(
                "embeddedReplicaSetMongo", Map.of("spring.data.mongodb.uri", connectionString)));
  }
}
