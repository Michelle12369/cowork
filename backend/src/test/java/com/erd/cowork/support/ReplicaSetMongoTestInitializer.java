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
 * replicaSet 參數的成員發現會拿到解析不了的位址）。首次使用先做 readiness 探測 fail-fast——輪詢 `hello` 等到
 * `isWritablePrimary`（而非單純 ping：mongod 進程活著但 rs.initiate 還沒跑完時 ping 會過、交易卻全被拒， compose
 * 冷啟動窗口尤其會踩到），逾時給出帶指令的錯誤，而非每個 context 各自懸掛 30 秒。錯誤訊息只列 host（不印完整連線字串）——CI 覆寫的 URI 可能帶
 * `user:pass@`，全文印進 log 會漏密碼。
 */
public class ReplicaSetMongoTestInitializer
    implements ApplicationContextInitializer<ConfigurableApplicationContext> {

  static final String URI_ENV = "ERD_TEST_MONGO_URI";

  static final String DEFAULT_URI = "mongodb://localhost:27017/cowork-test?directConnection=true";

  private static final int READINESS_TIMEOUT_SECONDS = 3;

  private static final long READINESS_POLL_INTERVAL_MILLIS = 200;

  /** JVM 存活期間快取——幾十個測試 context 共用，readiness 探測只做一次。 */
  private static String verifiedConnectionString;

  /** env 解析純函式：URI 覆寫優先，否則預設值。抽出來讓解析順序可單元測試。 */
  static String resolveConnectionString(Map<String, String> environment) {
    String overrideUri = environment.get(URI_ENV);
    return StringUtils.hasText(overrideUri) ? overrideUri : DEFAULT_URI;
  }

  /** 錯誤訊息用顯示字串純函式：只列 host，NEVER 印使用者資訊（覆寫 URI 可能帶密碼）。 */
  static String redactToHosts(String connectionString) {
    return String.join(",", new ConnectionString(connectionString).getHosts());
  }

  /** `hello` 回應判讀純函式：抽出來讓 readiness 條件可單元測試，不必真的連線。 */
  static boolean isWritablePrimary(Document helloResponse) {
    return Boolean.TRUE.equals(helloResponse.getBoolean("isWritablePrimary"));
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
                cluster ->
                    cluster.serverSelectionTimeout(READINESS_TIMEOUT_SECONDS, TimeUnit.SECONDS))
            .build();
    try (MongoClient client = MongoClients.create(settings)) {
      awaitWritablePrimary(client);
    } catch (RuntimeException readinessFailure) {
      throw new IllegalStateException(
          "測試用 Mongo 連不上或未選出可寫 PRIMARY ("
              + redactToHosts(connectionString)
              + ")。請先啟動本機 infra Mongo：docker compose -f docker-compose.infra.yml up -d"
              + " mongo mongo-init；若剛啟動，等 mongo-init 完成 rs.initiate；或設 "
              + URI_ENV
              + " 指向單成員以上 replica set（交易測試需要 RS）。",
          readinessFailure);
    }
    verifiedConnectionString = connectionString;
    return verifiedConnectionString;
  }

  /** 在 {@link #READINESS_TIMEOUT_SECONDS} 預算內輪詢 `hello`，等到 PRIMARY 可寫；逾時丟例外。 */
  private static void awaitWritablePrimary(MongoClient client) {
    long deadlineMillis =
        System.currentTimeMillis() + TimeUnit.SECONDS.toMillis(READINESS_TIMEOUT_SECONDS);
    RuntimeException lastFailure = null;
    while (System.currentTimeMillis() < deadlineMillis) {
      try {
        Document helloResponse = client.getDatabase("admin").runCommand(new Document("hello", 1));
        if (isWritablePrimary(helloResponse)) {
          return;
        }
      } catch (RuntimeException helloFailure) {
        lastFailure = helloFailure;
      }
      sleepBeforeNextPoll();
    }
    throw new IllegalStateException("Mongo 在逾時內未選出可寫 PRIMARY", lastFailure);
  }

  private static void sleepBeforeNextPoll() {
    try {
      Thread.sleep(READINESS_POLL_INTERVAL_MILLIS);
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("等待 Mongo PRIMARY 時被中斷", interrupted);
    }
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
