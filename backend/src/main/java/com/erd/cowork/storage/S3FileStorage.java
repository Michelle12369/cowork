package com.erd.cowork.storage;

import com.erd.cowork.config.StorageProperties;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;

/**
 * S3-backed implementation of {@link FileStorage}. Active when {@code erd.storage.type=s3}.
 *
 * <p>Because S3 {@code putObject} requires a known content length, the incoming {@link InputStream}
 * is spooled to a temporary file before upload. The temp file is always deleted in a finally block.
 *
 * <p>All AWS SDK {@link SdkException}s are wrapped as {@link IOException} to honour the interface
 * contract; callers that handle {@code IOException} will transparently receive S3 errors.
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "erd.storage", name = "type", havingValue = "s3")
public class S3FileStorage implements FileStorage {

  private final S3Client s3Client;
  private final StorageProperties storageProperties;

  @Override
  public String store(
      StorageCategory category, String sessionId, String originalFilename, InputStream in)
      throws IOException {
    String key = StorageKeyUtils.buildKey(category, sessionId, originalFilename);
    Path tempFile = Files.createTempFile("erd-upload-", null);
    try {
      long bytes = Files.copy(in, tempFile, StandardCopyOption.REPLACE_EXISTING);
      s3Client.putObject(
          PutObjectRequest.builder().bucket(bucket()).key(key).build(),
          RequestBody.fromFile(tempFile));
      log.info("stored file key={} bytes={}", key, bytes);
    } catch (SdkException ex) {
      throw new IOException("S3 store failed for key: " + key, ex);
    } finally {
      Files.deleteIfExists(tempFile);
    }
    return key;
  }

  @Override
  public InputStream read(String storageKey) throws IOException {
    try {
      return s3Client.getObject(
          GetObjectRequest.builder().bucket(bucket()).key(storageKey).build());
    } catch (NoSuchKeyException ex) {
      throw new IOException("S3 object not found for key: " + storageKey, ex);
    } catch (SdkException ex) {
      throw new IOException("S3 read failed for key: " + storageKey, ex);
    }
  }

  @Override
  public void delete(String storageKey) throws IOException {
    try {
      // S3 deleteObject is idempotent — no exception when the key is already absent.
      s3Client.deleteObject(DeleteObjectRequest.builder().bucket(bucket()).key(storageKey).build());
    } catch (SdkException ex) {
      throw new IOException("S3 delete failed for key: " + storageKey, ex);
    }
  }

  private String bucket() {
    return storageProperties.s3().bucket();
  }
}
