package com.erd.cowork.support;

import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import de.flapdoodle.embed.mongo.commands.MongodArguments;
import de.flapdoodle.embed.mongo.commands.ServerAddress;
import de.flapdoodle.embed.mongo.config.Storage;
import de.flapdoodle.embed.mongo.distribution.Version;
import de.flapdoodle.embed.mongo.transitions.Mongod;
import de.flapdoodle.embed.mongo.transitions.RunningMongodProcess;
import de.flapdoodle.reverse.TransitionWalker;
import de.flapdoodle.reverse.transitions.Start;
import java.util.List;
import org.bson.Document;

/**
 * 單成員 replica set 嵌入 mongod——交易在測試才成立（standalone 無交易）。啟動一次、JVM
 * 存活期間共用（static，跨測試類共享，避免每個 @DataMongoTest 類別各起一個 mongod 拖垮測試時間）。
 */
public final class EmbeddedReplicaSetMongo {

  private static TransitionWalker.ReachedState<RunningMongodProcess> running;
  private static String connectionString;

  private EmbeddedReplicaSetMongo() {}

  /** 啟動 replica set（若尚未啟動）並回傳連線字串；已啟動時直接回傳快取值。 */
  public static synchronized String start() {
    if (running != null) {
      return connectionString;
    }
    Mongod mongod =
        Mongod.instance()
            .withMongodArguments(
                Start.to(MongodArguments.class)
                    .initializedWith(
                        MongodArguments.defaults().withReplication(Storage.of("rs0", 10))));
    running = mongod.start(Version.Main.V7_0);
    ServerAddress address = running.current().getServerAddress();
    String host = address.getHost() + ":" + address.getPort();
    try (MongoClient client =
        MongoClients.create("mongodb://" + host + "/?directConnection=true")) {
      Document config =
          new Document("_id", "rs0")
              .append("members", List.of(new Document("_id", 0).append("host", host)));
      client.getDatabase("admin").runCommand(new Document("replSetInitiate", config));
      awaitPrimary(client);
    }
    connectionString = "mongodb://" + host + "/cowork?replicaSet=rs0";
    return connectionString;
  }

  private static void awaitPrimary(MongoClient client) {
    try {
      for (int attempt = 0; attempt < 60; attempt++) {
        Document status = client.getDatabase("admin").runCommand(new Document("hello", 1));
        if (Boolean.TRUE.equals(status.getBoolean("isWritablePrimary"))) {
          return;
        }
        Thread.sleep(500);
      }
      throw new IllegalStateException("replica set did not reach PRIMARY within timeout");
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("interrupted waiting for replica set primary", interrupted);
    }
  }
}
