package com.erd.cowork.support;

import com.mongodb.ConnectionString;
import com.mongodb.MongoClientSettings;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.bson.Document;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.env.MapPropertySource;
import org.springframework.util.StringUtils;

/**
 * 全域測試 context 初始化器：把測試用 Mongo 連線字串注入每個 Spring 測試 context（經 META-INF/spring.factories
 * 對所有 @SpringBootTest/@DataMongoTest 生效）。連線來源： `ERD_TEST_MONGO_URI` env（internal CI 指向 sidecar
 * RS），未設則預設本機 infra compose 的 mongo（單成員 replica set；rs.initiate 用容器內主機名故 MUST directConnection 直連，帶
 * replicaSet 參數的成員發現會拿到解析不了的位址）。首次使用先做短逾時 ping fail-fast—— Mongo 沒起時幾秒內給出帶指令的錯誤，而非每個 context 各自懸掛
 * 30 秒。
 */
public class ReplicaSetMongoTestInitializer
    implements ApplicationContextInitializer<ConfigurableApplicationContext> {

  static final String URI_ENV = "ERD_TEST_MONGO_URI";

  static final String DEFAULT_URI = "mongodb://localhost:27017/cowork-test?directConnection=true";

  private static final int PING_TIMEOUT_SECONDS = 3;

  /** JVM 存活期間快取——幾十個測試 context 共用，ping 只做一次。 */
  private static String verifiedConnectionString;

  /** env 解析純函式：URI 覆寫優先，否則預設值。抽出來讓解析順序可單元測試。 */
  static String resolveConnectionString(Map<String, String> environment) {
    String overrideUri = environment.get(URI_ENV);
    return StringUtils.hasText(overrideUri) ? overrideUri : DEFAULT_URI;
  }

  private static synchronized String verifiedConnectionString() {
    if (verifiedConnectionString != null) {
      return verifiedConnectionString;
    }
    String connectionString = resolveConnectionString(System.getenv());
    MongoClientSettings settings =
        MongoClientSettings.builder()
            .applyConnectionString(new ConnectionString(connectionString))
            .applyToClusterSettings(
                cluster -> cluster.serverSelectionTimeout(PING_TIMEOUT_SECONDS, TimeUnit.SECONDS))
            .build();
    try (MongoClient client = MongoClients.create(settings)) {
      client.getDatabase("admin").runCommand(new Document("ping", 1));
    } catch (RuntimeException pingFailure) {
      throw new IllegalStateException(
          "測試用 Mongo 連不上 ("
              + connectionString
              + ")。請先啟動本機 infra Mongo：docker compose -f docker-compose.infra.yml up -d"
              + " mongo mongo-init；或設 "
              + URI_ENV
              + " 指向單成員以上 replica set（交易測試需要 RS）。",
          pingFailure);
    }
    verifiedConnectionString = connectionString;
    return verifiedConnectionString;
  }

  @Override
  public void initialize(ConfigurableApplicationContext applicationContext) {
    applicationContext
        .getEnvironment()
        .getPropertySources()
        .addFirst(
            new MapPropertySource(
                "testMongoConnection",
                Map.of("spring.data.mongodb.uri", verifiedConnectionString())));
  }
}
