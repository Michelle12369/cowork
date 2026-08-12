package com.erd.cowork.support;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.repo.UploadedFileRepository;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The two "atomicity" tests on {@code FileControllerTest}/{@code FileServiceDecryptionFailureTest}
 * only cover IO-phase failures (before any DB write is attempted), so they pass with or without
 * {@code MongoTransactionManager} — no transaction regression protection. This test forces a
 * failure INSIDE the transactional DB-write phase itself, using the real {@code (sessionId, alias)}
 * unique index that {@code MongoIndexInitializer} creates on {@code uploaded_file} (fired via
 * {@code ApplicationReadyEvent}, hence {@code @SpringBootTest} rather than a slice test that would
 * skip it). If {@code MongoTransactionManager} were removed, or the replica-set harness swapped
 * back for standalone (no transaction semantics), the first save below would NOT be rolled back and
 * this test would fail.
 */
@SpringBootTest
class UploadedFileTransactionRollbackTest {

  @Autowired UploadedFileRepository files;
  @Autowired TransactionTemplate transactionTemplate;

  @Test
  void save_secondSaveViolatesUniqueIndex_firstSaveRolledBack() {
    String sessionId = UUID.randomUUID().toString();

    UploadedFile existing = new UploadedFile();
    existing.setSessionId(sessionId);
    existing.setName("dup.csv");
    existing.setAlias("dup");
    existing.setStorageKey("key-existing");
    existing.setSizeBytes(1);
    existing.setType("csv");
    files.save(existing);

    assertThatThrownBy(
            () ->
                transactionTemplate.executeWithoutResult(
                    status -> {
                      UploadedFile firstInTransaction = new UploadedFile();
                      firstInTransaction.setSessionId(sessionId);
                      firstInTransaction.setName("a.csv");
                      firstInTransaction.setAlias("a");
                      firstInTransaction.setStorageKey("key-a");
                      firstInTransaction.setSizeBytes(1);
                      firstInTransaction.setType("csv");
                      files.save(firstInTransaction);

                      // Collides with `existing` on the (sessionId, alias) unique index —
                      // aborts the transaction mid-write.
                      UploadedFile conflicting = new UploadedFile();
                      conflicting.setSessionId(sessionId);
                      conflicting.setName("dup2.csv");
                      conflicting.setAlias("dup");
                      conflicting.setStorageKey("key-dup2");
                      conflicting.setSizeBytes(1);
                      conflicting.setType("csv");
                      files.save(conflicting);
                    }))
        .isInstanceOf(DuplicateKeyException.class);

    // The transaction aborted, so `firstInTransaction` ("a") must be rolled back too — only the
    // pre-existing "dup" document survives. Without a real multi-document transaction this would
    // be 2 (the pre-existing "dup" plus the never-rolled-back "a").
    assertThat(files.findBySessionId(sessionId))
        .extracting(UploadedFile::getAlias)
        .containsExactly("dup");
  }
}
