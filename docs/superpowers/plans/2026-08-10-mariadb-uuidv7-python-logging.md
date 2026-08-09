# MariaDB 切換 + UUIDv7 PK + deepagent-service Log 強化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DB 由 Oracle 全面切換 MariaDB（不留相容、不遷移資料）、Entity PK 改時間有序 UUIDv7、deepagent-service log 依 stdlib best practice 強化。

**Architecture:** 三個獨立子系統各自成 task 串：backend（pom/Flyway V1/H2 mode → TEXT 護欄 → JUG UUIDv7 generator）、frontend（session id 改 `uuid` 套件 v7）、deepagent-service（dictConfig 集中設定＋contextvar sessionId → EventBridge/main.py 補 log）。最後 compose＋文件 sweep＋端到端驗證。

**Tech Stack:** Spring Boot 3.4.1／Hibernate 6.6／Flyway、`org.mariadb.jdbc:mariadb-java-client`、`com.fasterxml.uuid:java-uuid-generator` 5.x、npm `uuid` v10+、Python stdlib `logging.config.dictConfig`＋`contextvars`。

**Spec:** `docs/superpowers/specs/2026-08-10-mariadb-uuidv7-python-logging-design.md`

## Global Constraints

- Java 17（NEVER 用 18+ API）；google-java-format 由 Claude hook 自動跑，勿手動改格式
- 變數 NEVER 1–2 字元名稱；constructor injection；DTO 一律 record；Secrets NEVER 進 properties
- `.properties` 值一律 ASCII（ISO-8859-1 解碼），中文只能放註解
- 測試命名 `methodName_condition_expectedBehavior`；controller slice 用 `@WebMvcTest`
- React 18／TypeScript 嚴格模式 NEVER `any`；前端測試 Vitest+RTL 斷言行為
- Python：ruff line-length 100；FastAPI 參數一律 `Annotated`；`engine/` 禁 import LLM 框架（ruff TID251）
- 用語：codebase NEVER 寫「公司」「外部（環境義）」——一律 internal／upstream
- 每個 task 完成即 commit；PR 前 `./mvnw test`＋前端測試＋pytest 全綠
- 歷史 plan/spec 文件（`docs/superpowers/` 下既有檔案）不回改
- Branch：`feat/mariadb-uuidv7-logging`（已存在，spec 已 commit）

---

### Task 1: MariaDB 依賴、H2 mode 與 V1__init.sql 重寫

**Files:**
- Modify: `backend/pom.xml`
- Modify: `backend/src/main/resources/db/migration/V1__init.sql`（整檔重寫）
- Modify: `backend/src/main/resources/application.properties:4`
- Modify: `backend/src/main/resources/application-local.properties:8`（gitignored 本機檔，存在才改、不 commit）

**Interfaces:**
- Produces: MariaDB 語法的 V1 schema（`TEXT`／`DATETIME(6)`／`VARCHAR`），H2 以 `MODE=MariaDB` 跑同一份 DDL。後續所有 task 的測試都建立在此之上。

- [ ] **Step 1: pom.xml 換依賴**

移除（`backend/pom.xml:53-57` 與 `91-94`）：

```xml
    <dependency>
      <groupId>com.oracle.database.jdbc</groupId>
      <artifactId>ojdbc11</artifactId>
      <scope>runtime</scope>
    </dependency>
```

```xml
    <dependency>
      <groupId>org.flywaydb</groupId>
      <artifactId>flyway-database-oracle</artifactId>
    </dependency>
```

原位置分別換成（版本由 Spring Boot BOM 管理，不寫 version）：

```xml
    <dependency>
      <groupId>org.mariadb.jdbc</groupId>
      <artifactId>mariadb-java-client</artifactId>
      <scope>runtime</scope>
    </dependency>
```

```xml
    <dependency>
      <groupId>org.flywaydb</groupId>
      <artifactId>flyway-mysql</artifactId>
    </dependency>
```

- [ ] **Step 2: V1__init.sql 整檔重寫**

型別對映：`VARCHAR2(n)→VARCHAR(n)`、`CLOB→TEXT`（internal DBA 規範；64KB 上限的護欄在 Task 2）、`NUMBER(19)→BIGINT`、`NUMBER(1)→TINYINT`、`TIMESTAMP→DATETIME(6)`。**刻意不加** `ENGINE=`/`CHARSET=` table options——InnoDB 是 MariaDB 預設、charset 由 Task 7 的 compose 設 server 級 utf8mb4，DDL 保持 H2（MariaDB mode）與真 MariaDB 的共通子集。索引／FK／unique 全部不變。

```sql
CREATE TABLE chat_session (
    id         VARCHAR(36)  PRIMARY KEY,
    user_id    VARCHAR(100) NOT NULL,
    title      VARCHAR(200) NOT NULL,
    created_at DATETIME(6)  NOT NULL,
    updated_at DATETIME(6)  NOT NULL
);
CREATE INDEX idx_chat_session_user ON chat_session (user_id, updated_at);

CREATE TABLE chat_message (
    id             VARCHAR(36) PRIMARY KEY,
    session_id     VARCHAR(36) NOT NULL,
    sender         VARCHAR(10) NOT NULL,
    text           TEXT,
    steps_json     TEXT,
    questions_json TEXT,
    artifact_id    VARCHAR(36),
    created_at     DATETIME(6) NOT NULL,
    CONSTRAINT fk_message_session FOREIGN KEY (session_id) REFERENCES chat_session (id)
);
CREATE INDEX idx_chat_message_session ON chat_message (session_id, created_at);

CREATE TABLE uploaded_file (
    id            VARCHAR(36)  PRIMARY KEY,
    session_id    VARCHAR(36)  NOT NULL,
    name          VARCHAR(500) NOT NULL,
    alias         VARCHAR(100) NOT NULL,
    storage_key   VARCHAR(500) NOT NULL,
    size_bytes    BIGINT       NOT NULL,
    type          VARCHAR(20)  NOT NULL,
    metadata_json TEXT,
    row_count     BIGINT,
    expired       TINYINT      DEFAULT 0 NOT NULL,
    created_at    DATETIME(6)  NOT NULL,
    CONSTRAINT fk_file_session FOREIGN KEY (session_id) REFERENCES chat_session (id),
    CONSTRAINT uq_uploaded_file_alias UNIQUE (session_id, alias)
);
CREATE INDEX idx_uploaded_file_session ON uploaded_file (session_id);

CREATE TABLE artifact (
    id                   VARCHAR(36)  PRIMARY KEY,
    session_id           VARCHAR(36)  NOT NULL,
    title                VARCHAR(300) NOT NULL,
    raw_html_storage_key VARCHAR(500),
    html_storage_key     VARCHAR(500),
    asset_profile        VARCHAR(40),
    created_at           DATETIME(6)  NOT NULL,
    CONSTRAINT fk_artifact_session FOREIGN KEY (session_id) REFERENCES chat_session (id)
);
CREATE INDEX idx_artifact_session ON artifact (session_id);
```

- [ ] **Step 3: H2 mode 切換**

`application.properties:4`：

```properties
spring.datasource.url=${SPRING_DATASOURCE_URL:jdbc:h2:mem:local;MODE=MariaDB;DB_CLOSE_DELAY=-1}
```

`application-local.properties:8`（檔案存在才改；gitignored 不 commit）同樣把 `MODE=Oracle` 換 `MODE=MariaDB`。

- [ ] **Step 4: 跑全測（即為 schema 相容性驗證）**

Run: `cd backend && ./mvnw test`
Expected: 全綠。若 Flyway 對 H2 報 DDL 語法錯誤（`TEXT`/`DATETIME(6)`/`TINYINT` 其一不被 MariaDB mode 接受），對策順序：(1) 改用兩者共通替代語法（`DATETIME(6)`→`TIMESTAMP(6)` 仍與 MariaDB 相容——MariaDB 的 `TIMESTAMP` 有 2038 限制，改回報並記錄取捨）；(2) 仍不行才退 Flyway vendor 目錄（`spring.flyway.locations=classpath:db/migration/{vendor}`），H2／MariaDB 各一份 V1——這是 spec 明定退路。

- [ ] **Step 5: Commit**

```bash
git add backend/pom.xml backend/src/main/resources/db/migration/V1__init.sql backend/src/main/resources/application.properties
git commit -m "feat(backend): DB 切換 MariaDB——driver/flyway 依賴、V1 重寫、H2 改 MariaDB mode"
```

---

### Task 2: TEXT 64KB 寫入護欄

**Files:**
- Create: `backend/src/main/java/com/erd/cowork/parsing/TextColumnUtils.java`
- Create: `backend/src/test/java/com/erd/cowork/parsing/TextColumnUtilsTest.java`
- Modify: `backend/src/main/java/com/erd/cowork/parsing/FileParsingService.java`（加 `toJsonWithinByteLimit`）
- Create/Modify: `backend/src/test/java/com/erd/cowork/parsing/FileParsingServiceTest.java`（已存在則追加）
- Modify: `backend/src/main/java/com/erd/cowork/service/FileService.java:177`
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentConversationWriter.java`（3 個 persist 方法的 text/stepsJson/questionsJson）

**Interfaces:**
- Produces: `TextColumnUtils.truncateToUtf8Bytes(String value, int maxBytes) -> String`（null-safe、不切斷 code point）；常數 `TextColumnUtils.TEXT_COLUMN_MAX_BYTES = 65_000`；`FileParsingService.toJsonWithinByteLimit(FileProfile profile) -> String`。

- [ ] **Step 1: 寫 TextColumnUtils 失敗測試**

`TextColumnUtilsTest.java`：

```java
package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class TextColumnUtilsTest {

  @Test
  void truncateToUtf8Bytes_underLimit_returnsSameInstance() {
    String value = "hello 中文";
    assertThat(TextColumnUtils.truncateToUtf8Bytes(value, 100)).isSameAs(value);
  }

  @Test
  void truncateToUtf8Bytes_nullValue_returnsNull() {
    assertThat(TextColumnUtils.truncateToUtf8Bytes(null, 100)).isNull();
  }

  @Test
  void truncateToUtf8Bytes_overLimit_truncatesOnCharBoundary() {
    // 每個中文字 3 bytes；上限 10 bytes 只裝得下 3 個字（9 bytes），不得切出半個字
    String value = "測試切斷邊界";
    String truncated = TextColumnUtils.truncateToUtf8Bytes(value, 10);
    assertThat(truncated).isEqualTo("測試切");
    assertThat(truncated.getBytes(StandardCharsets.UTF_8).length).isLessThanOrEqualTo(10);
  }

  @Test
  void truncateToUtf8Bytes_surrogatePairAtBoundary_dropsWholePair() {
    // 😀 是 4-byte surrogate pair；上限 5 bytes 裝不下第二個 emoji，不得留下半個 pair
    String value = "a😀😀";
    String truncated = TextColumnUtils.truncateToUtf8Bytes(value, 5);
    assertThat(truncated).isEqualTo("a😀");
  }
}
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `cd backend && ./mvnw test -Dtest=TextColumnUtilsTest`
Expected: COMPILE ERROR（TextColumnUtils 不存在）

- [ ] **Step 3: 實作 TextColumnUtils**

`CharsetEncoder` 在 buffer 滿時停在完整 code point 邊界（surrogate pair 視為一個編碼單位），不需手動位元組回退：

```java
package com.erd.cowork.parsing;

import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharsetEncoder;
import java.nio.charset.StandardCharsets;

/** DB TEXT 欄位（64KB bytes 上限）的 UTF-8 截斷工具。 */
public final class TextColumnUtils {

  /** MariaDB TEXT 實際上限 65_535 bytes；留 headroom 取 65_000。 */
  public static final int TEXT_COLUMN_MAX_BYTES = 65_000;

  private TextColumnUtils() {
    throw new UnsupportedOperationException();
  }

  /** 依 UTF-8 byte 長度截斷；不足上限時原樣返回（同一實例），不切斷 code point。 */
  public static String truncateToUtf8Bytes(String value, int maxBytes) {
    if (value == null || value.getBytes(StandardCharsets.UTF_8).length <= maxBytes) {
      return value;
    }
    CharsetEncoder encoder = StandardCharsets.UTF_8.newEncoder();
    ByteBuffer encoded = ByteBuffer.allocate(maxBytes);
    encoder.encode(CharBuffer.wrap(value), encoded, true);
    return new String(encoded.array(), 0, encoded.position(), StandardCharsets.UTF_8);
  }
}
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `cd backend && ./mvnw test -Dtest=TextColumnUtilsTest`
Expected: PASS

- [ ] **Step 5: 寫 toJsonWithinByteLimit 失敗測試**

追加到 `FileParsingServiceTest.java`（不存在則新建；`FileParsingService` 的 csv/xlsx 依賴此測試用不到，傳 `null` 即可）：

```java
package com.erd.cowork.parsing;

import static org.assertj.core.api.Assertions.assertThat;

import com.erd.cowork.parsing.model.FileProfile;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.List;
import org.junit.jupiter.api.Test;

class FileParsingServiceTest {

  private final FileParsingService parsingService =
      new FileParsingService(null, null, new ObjectMapper());

  private static FileProfile profileWithSampleRows(int rowCount, int cellChars) {
    List<List<String>> sampleRows =
        Collections.nCopies(rowCount, List.of("x".repeat(cellChars), "y".repeat(cellChars)));
    return new FileProfile(rowCount, 2, List.of("col_a", "col_b"), List.of(), sampleRows);
  }

  @Test
  void toJsonWithinByteLimit_smallProfile_returnsFullJson() {
    FileProfile profile = profileWithSampleRows(3, 10);
    String json = parsingService.toJsonWithinByteLimit(profile);
    assertThat(json).contains("sampleRows").contains("xxxxxxxxxx");
  }

  @Test
  void toJsonWithinByteLimit_oversizedSampleRows_shrinksSamplesToFit() {
    // 20 列 × 兩欄 × 5000 字 ≈ 200KB，遠超 65_000 bytes——縮樣本後必須合上限且仍是合法 JSON
    FileProfile profile = profileWithSampleRows(20, 5000);
    String json = parsingService.toJsonWithinByteLimit(profile);
    assertThat(json.getBytes(StandardCharsets.UTF_8).length)
        .isLessThanOrEqualTo(TextColumnUtils.TEXT_COLUMN_MAX_BYTES);
    assertThat(json).startsWith("{").endsWith("}");
  }
}
```

- [ ] **Step 6: 跑測試確認 FAIL**

Run: `cd backend && ./mvnw test -Dtest=FileParsingServiceTest`
Expected: COMPILE ERROR（`toJsonWithinByteLimit` 不存在）

- [ ] **Step 7: 實作 toJsonWithinByteLimit**

`FileParsingService` 加 `@Slf4j`（class 上，Lombok），加方法：

```java
  /**
   * 序列化 profile 並保證結果放得進 TEXT 欄位（64KB）：超限時逐步砍半 sampleRows（生成端降級），
   * 樣本清空仍超限（極寬表的欄位統計本身過大）才硬截斷——截斷後非合法 JSON，下游
   * （AgentOrchestrator / ArtifactRepairService）本就以 lenient 模式跳過 unparseable metadata，
   * 該檔案僅失去 LLM context，上傳與查詢不受影響。
   */
  public String toJsonWithinByteLimit(FileProfile profile) {
    String json = toJson(profile);
    List<List<String>> sampleRows = profile.sampleRows();
    while (utf8ByteLength(json) > TextColumnUtils.TEXT_COLUMN_MAX_BYTES && !sampleRows.isEmpty()) {
      sampleRows = sampleRows.subList(0, sampleRows.size() / 2);
      profile =
          new FileProfile(
              profile.rowCount(),
              profile.colCount(),
              profile.headers(),
              profile.columns(),
              sampleRows);
      json = toJson(profile);
      log.warn(
          "metadata json over TEXT limit, sample rows reduced to {} rows", sampleRows.size());
    }
    if (utf8ByteLength(json) > TextColumnUtils.TEXT_COLUMN_MAX_BYTES) {
      log.warn(
          "metadata json still over TEXT limit after dropping samples ({} bytes), hard-truncating",
          utf8ByteLength(json));
      json = TextColumnUtils.truncateToUtf8Bytes(json, TextColumnUtils.TEXT_COLUMN_MAX_BYTES);
    }
    return json;
  }

  private static int utf8ByteLength(String value) {
    return value.getBytes(StandardCharsets.UTF_8).length;
  }
```

（import：`java.nio.charset.StandardCharsets`、`java.util.List`、`lombok.extern.slf4j.Slf4j`。）

- [ ] **Step 8: 跑測試確認 PASS**

Run: `cd backend && ./mvnw test -Dtest=FileParsingServiceTest`
Expected: PASS

- [ ] **Step 9: 接上兩個寫入點**

`FileService.java:177`：

```java
        entity.setMetadataJson(parsing.toJsonWithinByteLimit(profile));
```

`AgentConversationWriter.java`——加 private helper 並套用到 3 個方法所有 TEXT 欄位（`text`／`stepsJson`／`questionsJson`；class 已有 `@Slf4j`）：

```java
  /** TEXT 欄位 64KB 護欄：超限截斷並警告,不讓 DB 錯誤打斷 turn。 */
  private static String fitTextColumn(String value, String columnName, String sessionId) {
    String fitted =
        TextColumnUtils.truncateToUtf8Bytes(value, TextColumnUtils.TEXT_COLUMN_MAX_BYTES);
    if (!java.util.Objects.equals(fitted, value)) {
      log.warn(
          "chat_message.{} truncated to fit TEXT column session={} originalChars={}",
          columnName,
          sessionId,
          value.length());
    }
    return fitted;
  }
```

套用（`persistHtmlResult`、`persistAiMessage`、`tryPersistAiMessage` 三處一致）：

```java
          aiMsg.setText(fitTextColumn(answerText, "text", sessionId));
          aiMsg.setStepsJson(fitTextColumn(stepsJson, "steps_json", sessionId));
          aiMsg.setQuestionsJson(fitTextColumn(questionsJson, "questions_json", sessionId));
```

（`tryPersistAiMessage` 只有 `text` 與固定 `"[]"` 的 stepsJson——只包 `text`。import `com.erd.cowork.parsing.TextColumnUtils`；`Objects` 用完整路徑或 import。）

- [ ] **Step 10: 全測 + Commit**

Run: `cd backend && ./mvnw test`
Expected: 全綠

```bash
git add backend/src/main/java backend/src/test/java
git commit -m "feat(backend): TEXT 64KB 寫入護欄——metadata 生成端降級、chat_message 截斷警告"
```

---

### Task 3: 後端 UUIDv7（JUG）＋ Entity 切換 ＋ CLAUDE.md 規則

**Files:**
- Modify: `backend/pom.xml`（加 JUG）
- Create: `backend/src/main/java/com/erd/cowork/domain/id/UuidV7.java`
- Create: `backend/src/main/java/com/erd/cowork/domain/id/UuidV7Generator.java`
- Create: `backend/src/test/java/com/erd/cowork/domain/id/UuidV7GeneratorTest.java`
- Modify: `backend/src/main/java/com/erd/cowork/domain/ChatMessage.java:16,30`、`domain/UploadedFile.java:14,28`、`domain/Artifact.java:13,27`
- Modify: `backend/src/main/java/com/erd/cowork/agent/AgentConversationWriter.java:42-43,72`（Javadoc/註解字樣）
- Modify: `CLAUDE.md`（General 的 Entity ID 規則行）

**Interfaces:**
- Consumes: 無（獨立於 Task 1/2，但排在其後以維持單線 branch 歷史）
- Produces: `@com.erd.cowork.domain.id.UuidV7` field annotation——後續所有新 entity 的 id 標準。

- [ ] **Step 1: pom 加 JUG**

`backend/pom.xml` dependencies 區塊（放 h2 之後）：

```xml
    <dependency>
      <groupId>com.fasterxml.uuid</groupId>
      <artifactId>java-uuid-generator</artifactId>
      <version>5.1.0</version>
    </dependency>
```

- [ ] **Step 2: 寫 generator 失敗測試**

`UuidV7GeneratorTest.java`：

```java
package com.erd.cowork.domain.id;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;
import org.hibernate.generator.EventType;
import org.junit.jupiter.api.Test;

class UuidV7GeneratorTest {

  private final UuidV7Generator generator = new UuidV7Generator();

  private String generate() {
    return (String) generator.generate(null, null, null, EventType.INSERT);
  }

  @Test
  void generate_returnsCanonical36CharUuidVersion7() {
    String uuid = generate();
    assertThat(uuid).hasSize(36);
    assertThat(uuid.charAt(14)).isEqualTo('7'); // version nibble
    assertThat("89ab").contains(String.valueOf(uuid.charAt(19))); // variant nibble
  }

  @Test
  void generate_consecutiveCalls_lexicographicallyIncreasingAndUnique() {
    List<String> generated = new ArrayList<>();
    for (int index = 0; index < 1000; index++) {
      generated.add(generate());
    }
    List<String> sorted = new ArrayList<>(generated);
    sorted.sort(String::compareTo);
    assertThat(generated).isEqualTo(sorted); // JUG timeBasedEpochGenerator 同毫秒單調
    assertThat(generated).doesNotHaveDuplicates();
  }
}
```

- [ ] **Step 3: 跑測試確認 FAIL**

Run: `cd backend && ./mvnw test -Dtest=UuidV7GeneratorTest`
Expected: COMPILE ERROR

- [ ] **Step 4: 實作 annotation + generator**

`UuidV7.java`：

```java
package com.erd.cowork.domain.id;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import org.hibernate.annotations.IdGeneratorType;

/**
 * 時間有序 UUIDv7 字串 id：前 48 bits 為毫秒 timestamp，插入落點集中在 MariaDB clustered index
 * 最右側熱 page。Hibernate 6.6 的 {@code @UuidGenerator} 無 v7 才自訂此接縫；產生邏輯委給 JUG。
 */
@IdGeneratorType(UuidV7Generator.class)
@Retention(RetentionPolicy.RUNTIME)
@Target({ElementType.FIELD, ElementType.METHOD})
public @interface UuidV7 {}
```

`UuidV7Generator.java`（JUG 的 `timeBasedEpochGenerator()` 即 v7 單調變體——同毫秒遞增前值；`timeBasedEpochRandomGenerator()` 才是每次全隨機，勿用錯）：

```java
package com.erd.cowork.domain.id;

import com.fasterxml.uuid.Generators;
import com.fasterxml.uuid.impl.TimeBasedEpochGenerator;
import java.util.EnumSet;
import org.hibernate.engine.spi.SharedSessionContractImplementor;
import org.hibernate.generator.BeforeExecutionGenerator;
import org.hibernate.generator.EventType;
import org.hibernate.generator.EventTypeSets;

/** {@link UuidV7} 的 Hibernate 接縫：JUG 產生 v7 後轉 36 字元字串。 */
public class UuidV7Generator implements BeforeExecutionGenerator {

  private static final TimeBasedEpochGenerator UUID_V7_GENERATOR =
      Generators.timeBasedEpochGenerator();

  @Override
  public Object generate(
      SharedSessionContractImplementor session,
      Object owner,
      Object currentValue,
      EventType eventType) {
    return UUID_V7_GENERATOR.generate().toString();
  }

  @Override
  public EnumSet<EventType> getEventTypes() {
    return EventTypeSets.INSERT_ONLY;
  }
}
```

- [ ] **Step 5: 跑測試確認 PASS**

Run: `cd backend && ./mvnw test -Dtest=UuidV7GeneratorTest`
Expected: PASS

- [ ] **Step 6: 三個 entity 換 annotation**

`ChatMessage.java`／`UploadedFile.java`／`Artifact.java` 一致修改：

```java
// 移除
import org.hibernate.annotations.UuidGenerator;
// 加入
import com.erd.cowork.domain.id.UuidV7;
```

```java
  @Id
  @UuidV7
  @Column(length = 36)
  private String id;
```

`AgentConversationWriter.java` 的 Javadoc（42-43 行）與註解（72 行）中 `{@code @UuidGenerator}`／`@UuidGenerator-assigned` 字樣改為 `{@code @UuidV7}`／`@UuidV7-assigned`。

- [ ] **Step 7: CLAUDE.md 規則行更新**

General 一節，原行：

```
- Entity ID 用 Hibernate `@UuidGenerator`（String）；時間戳一律 JPA Auditing（`@CreatedDate`/`@LastModifiedDate`）。例外：`ChatSession` 採 client 指定 id（session upsert 設計），無 generator、實作 `Persistable<String>`，建立時 MUST 先 `setId()`——理由見該 entity class Javadoc
```

改為：

```
- Entity ID 用專案自訂 `@UuidV7`（`com.erd.cowork.domain.id`；String，時間有序 UUIDv7——MariaDB clustered index 追加式插入；NEVER 用隨機 v4 的 Hibernate `@UuidGenerator`）；時間戳一律 JPA Auditing（`@CreatedDate`/`@LastModifiedDate`）。例外：`ChatSession` 採 client 指定 id（session upsert 設計，前端以 uuid 套件產 v7），無 generator、實作 `Persistable<String>`，建立時 MUST 先 `setId()`——理由見該 entity class Javadoc
```

- [ ] **Step 8: 全測 + Commit**

Run: `cd backend && ./mvnw test`
Expected: 全綠（既有 repository/service 測試以 H2 實際走過新 generator）

```bash
git add backend/pom.xml backend/src/main/java backend/src/test/java CLAUDE.md
git commit -m "feat(backend): PK 改時間有序 UUIDv7——JUG generator + @UuidV7 接縫,三 entity 換裝"
```

---

### Task 4: 前端 session id 改 UUIDv7

**Files:**
- Modify: `frontend/package.json`（加 `uuid`）
- Modify: `frontend/src/CoworkPage.tsx:91`

**Interfaces:**
- Consumes: 無
- Produces: draft session id 為 v7 格式（36 字元，排序≈建立時間）。`apiClient.ts:8` 的匿名 user id 維持 `crypto.randomUUID()` **不改**（`user_id` 欄位非 PK）。

- [ ] **Step 1: 安裝 uuid**

Run: `cd frontend && npm install uuid`
（v10+ 原生支援 v7、內建同毫秒單調計數器、自帶 TS 型別——不需要 `@types/uuid`。）

- [ ] **Step 2: 換產生器**

`CoworkPage.tsx` import 區加：

```typescript
import { v7 as uuidv7 } from 'uuid';
```

`CoworkPage.tsx:91`：

```typescript
    const draftSessionId = uuidv7();
```

- [ ] **Step 3: 跑前端測試與建置**

Run: `cd frontend && npm test -- --run && npm run build`
Expected: 全綠、build 成功（既有 CoworkPage 行為測試未 mock `crypto.randomUUID`，不需調整；若有測試斷言 id 格式再對應修正）

- [ ] **Step 4: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/CoworkPage.tsx
git commit -m "feat(frontend): draft session id 改 uuid 套件 v7——與後端 PK 時間有序策略一致"
```

---

### Task 5: deepagent-service 集中 logging 設定

**Files:**
- Create: `deepagent-service/app/logging_config.py`
- Create: `deepagent-service/tests/test_logging_config.py`
- Modify: `deepagent-service/app/config.py`（Settings 加 `LOG_LEVEL`）
- Modify: `deepagent-service/app/main.py`（module 載入時 configure；兩個端點 set contextvar）
- Modify: `deepagent-service/one.properties`（範本補 `LOG_LEVEL=` 空值列）

**Interfaces:**
- Produces: `configure_logging(settings: Settings) -> None`、`current_session_id: ContextVar[str | None]`（Task 6 的 log 行自動帶 session）。格式：`時間 等級 [模組] [session=xxx] 訊息`。

- [ ] **Step 1: 寫失敗測試**

`tests/test_logging_config.py`：

```python
"""logging_config 的單元測試:filter 注入、LOG_LEVEL 生效。"""

import logging

from app.config import get_settings
from app.logging_config import SessionIdFilter, configure_logging, current_session_id


def _make_record() -> logging.LogRecord:
    return logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", None, None)


def test_filter_without_context_sets_dash():
    record = _make_record()
    assert SessionIdFilter().filter(record) is True
    assert record.session_id == "-"


def test_filter_with_context_sets_session_id():
    token = current_session_id.set("session-123")
    try:
        record = _make_record()
        SessionIdFilter().filter(record)
        assert record.session_id == "session-123"
    finally:
        current_session_id.reset(token)


def test_configure_logging_respects_log_level_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging(get_settings())
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_default_level_is_info():
    configure_logging(get_settings())
    assert logging.getLogger().level == logging.INFO
```

- [ ] **Step 2: 跑測試確認 FAIL**

Run: `cd deepagent-service && uv run pytest tests/test_logging_config.py -v`
Expected: FAIL（ImportError: app.logging_config 不存在）

- [ ] **Step 3: 實作 logging_config.py ＋ Settings.LOG_LEVEL**

`app/logging_config.py`：

```python
"""集中 logging 設定:dictConfig 一次定義 formatter/filter/handler,uvicorn 三支 logger 一併
納管(統一格式、關 propagate 杜絕雙重輸出)。只在應用進入點(main.py)呼叫 configure_logging()
一次;其餘模組一律只 logging.getLogger(__name__),不自設 handler/level。sessionId 用 contextvar
注入每一行(async 流程隨 task 自動傳播),端點進入時 set。"""

import logging
import logging.config
from contextvars import ContextVar

from app.config import Settings

current_session_id: ContextVar[str | None] = ContextVar("current_session_id", default=None)

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [session=%(session_id)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class SessionIdFilter(logging.Filter):
    """把 contextvar 的 sessionId 掛上每筆 record;無值(啟動期、健康檢查)顯示 '-'。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = current_session_id.get() or "-"
        return True


def configure_logging(settings: Settings) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"session_id": {"()": SessionIdFilter}},
            "formatters": {
                "standard": {"format": LOG_FORMAT, "datefmt": LOG_DATE_FORMAT},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "standard",
                    "filters": ["session_id"],
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
            },
            "root": {"handlers": ["console"], "level": settings.LOG_LEVEL.upper()},
        }
    )
```

`app/config.py` 的 `Settings` 加欄位（放 `AGENT_AUTH_MODE` 之前，維持字母序無此慣例、放最前即可）：

```python
    LOG_LEVEL: str = "INFO"
```

`one.properties` 範本補一行（照該檔既有格式）：

```properties
LOG_LEVEL=
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `cd deepagent-service && uv run pytest tests/test_logging_config.py -v`
Expected: PASS

- [ ] **Step 5: main.py 接線**

`app/main.py`——import 區加：

```python
from app.logging_config import configure_logging, current_session_id
```

`logger = logging.getLogger(__name__)` 之前加（module 載入即生效，uvicorn worker 啟動就吃到）：

```python
configure_logging(get_settings())
```

`chat()` 端點第一行（`logger.info(...)` 之前）加：

```python
    current_session_id.set(request.sessionId)
```

`repair()` 端點第一行同樣加 `current_session_id.set(request.sessionId)`。

- [ ] **Step 6: 全測 + Commit**

Run: `cd deepagent-service && uv run pytest`
Expected: 全綠（若既有測試以 caplog 斷言訊息內容，格式變更不影響——caplog 看 record 不看 formatter）

```bash
git add deepagent-service/app deepagent-service/tests deepagent-service/one.properties
git commit -m "feat(deepagent): dictConfig 集中 logging——contextvar sessionId 注入、LOG_LEVEL 可調、uvicorn 收編"
```

---

### Task 6: deepagent-service 關鍵路徑 log 涵蓋

**Files:**
- Modify: `deepagent-service/app/agent/events.py`（EventBridge：tool/model 耗時）
- Modify: `deepagent-service/app/agent/chat_turn.py`（首輪重試 warning）
- Modify: `deepagent-service/app/agent/repair_flow.py`（model call 耗時）
- Modify: `deepagent-service/app/main.py`（SSE 事件摘要）
- Modify: `deepagent-service/tests/test_events.py`（追加 caplog 測試）

**Interfaces:**
- Consumes: Task 5 的 logging 設定（sessionId 自動帶上，這裡的 log 行不用手寫 session 欄位——sessionId 已由 contextvar 注入）。
- Produces: 無（純觀測性）。

- [ ] **Step 1: EventBridge 加 tool/model 計時 log**

`app/agent/events.py`——module 頂部加：

```python
import logging
import time
```

```python
logger = logging.getLogger(__name__)
```

`EventBridge.__init__` 追加兩個狀態：

```python
        self._tool_started_at: dict[str, float] = {}
        self._model_started_at: float | None = None
```

`_handle_tool_start` 在 `return [step]` 前加：

```python
        self._tool_started_at[str(agent_event.get("run_id"))] = time.monotonic()
        logger.debug("tool start name=%s", agent_event["name"])
```

`_handle_tool_end` 在建立 `events` list 之後、`if not pop_record` 之前加（工具名/狀態/耗時——內容不落 log，遵守「NEVER log 使用者資料內容」）：

```python
        started_at = self._tool_started_at.pop(str(agent_event.get("run_id")), None)
        duration_seconds = time.monotonic() - started_at if started_at is not None else -1.0
        logger.info(
            "tool done name=%s status=%s duration=%.2fs",
            agent_event["name"],
            status,
            duration_seconds,
        )
```

`handle()` 的 `on_chat_model_start` 分支加計時起點：

```python
        if event_type == "on_chat_model_start":
            self.current_text = ""
            self._model_started_at = time.monotonic()
            return []
```

`_handle_chat_model_end` 結尾加（token 概況取 langchain `usage_metadata`，無資料時記 None）：

```python
        duration_seconds = (
            time.monotonic() - self._model_started_at if self._model_started_at is not None else -1.0
        )
        usage = getattr(message, "usage_metadata", None) or {}
        logger.info(
            "model call done duration=%.2fs tool_calls=%d input_tokens=%s output_tokens=%s",
            duration_seconds,
            len(tool_calls),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
```

- [ ] **Step 2: 首輪重試與 repair 耗時 log**

`app/agent/chat_turn.py` `ChatTurn.stream()` 的 retry while 迴圈內（`retry_runs += 1` 之後）加：

```python
            logger.warning(
                "empty first round (no text, no tool started), retrying (%d/%d)",
                retry_runs,
                FIRST_ROUND_RETRY_MAX_RUNS,
            )
```

`app/agent/repair_flow.py` `_invoke_repair_model`——`import time`（module 頂部），函式內計時：

```python
async def _invoke_repair_model(model: Any, messages: list[BaseMessage], session_id: str) -> str:
    # 與 /chat 同組 Langfuse handler;run_name=repair 供辨識、session metadata 供分組。
    invoke_config = {
        "callbacks": _build_callbacks(),
        "run_name": "repair",
        "metadata": {"langfuse_session_id": session_id},
    }
    started_at = time.monotonic()
    response = await asyncio.wait_for(
        model.ainvoke(messages, config=invoke_config),
        timeout=REPAIR_MODEL_CALL_TIMEOUT_SECONDS,
    )
    logger.info("repair model call done duration=%.2fs", time.monotonic() - started_at)
    content = response.content
    return content if isinstance(content, str) else str(content)
```

- [ ] **Step 3: main.py SSE 事件摘要**

`app/main.py` `chat()` 改為（計數所有 wire 事件、ERROR 記內容、結束一定記摘要——`finally` 涵蓋提前 return 與例外）：

```python
@app.post("/chat", response_class=EventSourceResponse)
async def chat(request: Annotated[ChatRequest, Body()]) -> AsyncIterable[ServerSentEvent]:
    current_session_id.set(request.sessionId)
    logger.info(
        "chat request sessionId=%s message_length=%d source_count=%d",
        request.sessionId,
        len(request.message),
        len(request.sources),
    )
    event_counts: Counter[str] = Counter()
    try:
        async with ChatTurn(request) as turn:
            async for wire_event in turn.stream():
                event_counts[type(wire_event).__name__] += 1
                yield ServerSentEvent(data=wire_event)
                if isinstance(wire_event, ErrorEvent):
                    logger.warning(
                        "chat turn errored code=%s message=%s", wire_event.code, wire_event.message
                    )
                    return
            async for wire_event in turn.finalize():
                event_counts[type(wire_event).__name__] += 1
                yield ServerSentEvent(data=wire_event)
                if isinstance(wire_event, ErrorEvent):
                    logger.warning(
                        "chat turn errored code=%s message=%s", wire_event.code, wire_event.message
                    )
                    return
    finally:
        logger.info("chat turn finished events=%s", dict(event_counts))
```

（import 區加 `from collections import Counter`。）

- [ ] **Step 4: 追加 caplog 測試**

`tests/test_events.py` 追加（沿用該檔既有的 EventBridge 建構方式；若 helper 名稱不同，以現檔為準改寫斷言目標不變）：

```python
def test_handle_tool_lifecycle_logs_duration(caplog):
    import logging

    from app.agent.tools.recording import ToolResultRecorder

    bridge = EventBridge(ToolResultRecorder())
    start_event = {"event": "on_tool_start", "name": "run_sql", "run_id": "r1", "data": {"input": {}}}
    end_event = {"event": "on_tool_end", "name": "run_sql", "run_id": "r1", "data": {}}
    with caplog.at_level(logging.INFO, logger="app.agent.events"):
        bridge.handle(start_event)
        bridge.handle(end_event)
    tool_done_records = [record for record in caplog.records if "tool done" in record.message]
    assert len(tool_done_records) == 1


def test_handle_model_end_logs_duration(caplog):
    import logging
    from types import SimpleNamespace

    from app.agent.tools.recording import ToolResultRecorder

    bridge = EventBridge(ToolResultRecorder())
    message = SimpleNamespace(content="done", tool_calls=[], usage_metadata=None)
    with caplog.at_level(logging.INFO, logger="app.agent.events"):
        bridge.handle({"event": "on_chat_model_start", "data": {}})
        bridge.handle({"event": "on_chat_model_end", "data": {"output": message}})
    model_done_records = [record for record in caplog.records if "model call done" in record.message]
    assert len(model_done_records) == 1
```

- [ ] **Step 5: 全測 + Commit**

Run: `cd deepagent-service && uv run pytest`
Expected: 全綠（test_chat.py 走完整 /chat 流程——若其中有嚴格斷言 log 行數/內容的測試受新 log 影響，調整該斷言為包含式比對）

```bash
git add deepagent-service/app deepagent-service/tests
git commit -m "feat(deepagent): 關鍵路徑 log——tool/model 耗時、首輪重試、repair 耗時、SSE 事件摘要"
```

---

### Task 7: compose 切換 MariaDB ＋ 文件 sweep ＋ 端到端驗證

**Files:**
- Modify: `docker-compose.infra.yml`（`oracle` 服務整組換 `mariadb`、header 註解、cloudbeaver depends_on、volumes）
- Modify: `docker-compose.app.yml`（datasource 預設值、3-4 行與 18-20 行註解）
- Modify: `README.md`（所有 Oracle 敘述）
- Modify: `docs/architecture.md`（mermaid 節點、ER 型別標註、BYTE 語意節、H2 mode 敘述）

**Interfaces:**
- Consumes: Task 1 的 V1 schema 與 datasource 切換。
- Produces: `docker compose` 可起的 MariaDB 環境；文件與現實一致。

- [ ] **Step 1: docker-compose.infra.yml**

`oracle` 服務整組替換為（header 註解的「oracle」字樣同步改「mariadb」；`volumes:` 區塊的 `oracle-data:` 改 `mariadb-data:`；`cloudbeaver.depends_on` 的 `- oracle` 改 `- mariadb`）：

```yaml
  mariadb:
    image: mariadb:11.4
    restart: unless-stopped
    # DDL 不帶 charset table options,一律吃 server 級預設——這裡就是唯一的 charset 決定點
    command: --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
    environment:
      MARIADB_ROOT_PASSWORD: ${MARIADB_ROOT_PASSWORD:-mariadb_dev}
      MARIADB_DATABASE: ${MARIADB_DATABASE:-cowork}
      MARIADB_USER: ${SPRING_DATASOURCE_USERNAME:-cowork}
      MARIADB_PASSWORD: ${SPRING_DATASOURCE_PASSWORD:-cowork_dev}
    ports:
      - "3306:3306"
    volumes:
      - mariadb-data:/var/lib/mysql
    networks:
      - erd-cowork-net
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s
```

- [ ] **Step 2: docker-compose.app.yml**

23 行：

```yaml
      SPRING_DATASOURCE_URL: ${SPRING_DATASOURCE_URL:-jdbc:mariadb://mariadb:3306/cowork}
```

3-4 行註解的「oracle」改「mariadb」；18-20 行註解改為：

```yaml
    # infra stack 的 mariadb 是跨 project 的服務，compose 的 depends_on 不跨 project——
    # 由 restart 讓 backend 在 MariaDB 尚未就緒時自動重試（MariaDB 秒級就緒，首次啟動
    # 也只需數十秒，restart 次數遠少於過去 Oracle 的 2–4 分鐘）。
```

- [ ] **Step 3: README 與 architecture.md sweep**

Run: `grep -ni "oracle\|VARCHAR2\|CLOB" README.md docs/architecture.md` 逐一處理：

README（依行號現況調整）：
- L23、L69：「H2（Oracle 相容模式）」→「H2（MariaDB 相容模式）」
- L127：「H2，無需 Oracle」→「H2，無需 MariaDB」
- L134：「真 Oracle」→「真 MariaDB」
- L141：infra 表格 `oracle` → `mariadb`
- 其餘出現處（含 `ORACLE_PASSWORD` env 說明若有）比照改寫；`.env.docker` 變數改名為 `MARIADB_ROOT_PASSWORD` 並在 README 的環境變數段落註明

docs/architecture.md：
- L16 mermaid 節點 `Oracle[("Oracle DB\nFlyway 單一 baseline")]` → `MariaDB[("MariaDB\nFlyway 單一 baseline")]`（節點 id 全檔同步改，L35 `Spring -->|JPA| Oracle` 一併）
- L87、L152 sequence participant `DB as Oracle DB` → `DB as MariaDB`
- ER 圖（L453-L473 一帶）：`VARCHAR2_36`→`VARCHAR_36`、`VARCHAR2_100`→`VARCHAR_100`（其餘同型式）、`CLOB`→`TEXT`；L453 註記「非 @UuidGenerator」→「非 @UuidV7」
- L376「**Oracle BYTE 語意說明**」整段改寫為：

> **MariaDB 字元語意與 byte 計長**：MariaDB utf8mb4 的 `VARCHAR(N)` 為字元語意（N 個字元，不是 bytes），現行以 UTF-8 byte 計長的驗證（alias ≤ 60 bytes、name ≤ 400 bytes）比字元上限更嚴格，保守安全、維持不動。`TEXT` 欄位上限 65,535 bytes——寫入端有截斷護欄（`TextColumnUtils`／`FileParsingService.toJsonWithinByteLimit`），超限截斷並記 warning，不讓 DB 錯誤打斷 turn。
- L435「H2 Oracle mode」→「H2 MariaDB mode」
- 若 mermaid 圖表改動，肉眼確認語法（節點 id 一致、無殘留 `Oracle` 引用）

- [ ] **Step 4: 端到端驗證（人工步驟，記錄輸出）**

```bash
docker network create erd-cowork-net 2>/dev/null || true
docker compose --env-file .env.docker -f docker-compose.infra.yml up -d mariadb
docker compose -f docker-compose.infra.yml ps   # 等 healthy
cd backend && SPRING_DATASOURCE_URL="jdbc:mariadb://localhost:3306/cowork" \
  SPRING_DATASOURCE_USERNAME=cowork SPRING_DATASOURCE_PASSWORD=cowork_dev \
  ./mvnw spring-boot:run
```

Expected: 啟動 log 出現 Flyway `Successfully applied 1 migration`；另開 shell `curl -s localhost:8080/actuator/health` 回 `{"status":"UP"}`。驗證後 Ctrl-C 停 backend。若 `.env.docker` 缺新變數（`MARIADB_ROOT_PASSWORD` 等），用預設值即可（compose 已帶 fallback）。

- [ ] **Step 5: Commit**

```bash
git add docker-compose.infra.yml docker-compose.app.yml README.md docs/architecture.md
git commit -m "feat(infra): compose oracle 換 mariadb:11.4 + README/architecture 文件同步"
```

---

## 完成後

依 `superpowers:finishing-a-development-branch`：三側測試全綠 → opus 全分支終審 → `gh pr create`（終審結論寫進 PR 描述）→ 使用者觸發 merge。
