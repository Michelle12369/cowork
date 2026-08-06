package com.erd.cowork.storage;

import com.erd.cowork.config.StorageProperties;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.Delete;
import software.amazon.awssdk.services.s3.model.DeleteObjectsRequest;
import software.amazon.awssdk.services.s3.model.ListObjectsV2Request;
import software.amazon.awssdk.services.s3.model.ObjectIdentifier;
import software.amazon.awssdk.services.s3.model.S3Object;

/**
 * S3-backed implementation of {@link WorkspacePurger}. Active when {@code erd.storage.type=s3};
 * deletes every object under the session's key prefix instead of a directory tree.
 *
 * <p>All AWS SDK {@link SdkException}s are wrapped as {@link IOException} to honour the interface
 * contract; callers that handle {@code IOException} will transparently receive S3 errors.
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "erd.storage", name = "type", havingValue = "s3")
public class S3WorkspacePurger implements WorkspacePurger {

  /** S3 {@code DeleteObjects} accepts at most 1000 keys per request. */
  private static final int DELETE_BATCH_SIZE = 1000;

  // 與 deepagent S3WorkspaceStore 的寫死值必須一致，故兩側都用常數不做設定。
  private static final String WORKSPACE_PREFIX = "workspace";

  private final S3Client s3Client;
  private final StorageProperties storageProperties;

  @Override
  public boolean sessionExists(String userId, String sessionId) {
    String prefix = sessionPrefix(userId, sessionId);
    try {
      var response =
          s3Client.listObjectsV2(
              ListObjectsV2Request.builder().bucket(bucket()).prefix(prefix).maxKeys(1).build());
      // KeyCount is documented as always present for AWS S3, but some S3-compatible backends
      // (e.g. MinIO-style object storage) may omit it -- Optional avoids unboxing a null Integer.
      return Optional.ofNullable(response.keyCount()).orElse(0) > 0;
    } catch (SdkException exception) {
      log.warn(
          "Failed to check workspace objects under prefix={}: {}",
          prefix,
          exception.getMessage(),
          exception);
      return false;
    }
  }

  @Override
  public boolean purgeSession(String userId, String sessionId) throws IOException {
    String prefix = sessionPrefix(userId, sessionId);
    try {
      List<ObjectIdentifier> keys = new ArrayList<>();
      int deletedCount = 0;
      var pages =
          s3Client.listObjectsV2Paginator(
              ListObjectsV2Request.builder().bucket(bucket()).prefix(prefix).build());
      for (var page : pages) {
        for (S3Object object : page.contents()) {
          keys.add(ObjectIdentifier.builder().key(object.key()).build());
          if (keys.size() == DELETE_BATCH_SIZE) {
            deletedCount += deleteBatch(keys);
            keys.clear();
          }
        }
      }
      if (!keys.isEmpty()) {
        deletedCount += deleteBatch(keys);
      }
      return deletedCount > 0;
    } catch (SdkException exception) {
      throw new IOException("S3 workspace purge failed for prefix: " + prefix, exception);
    }
  }

  private int deleteBatch(List<ObjectIdentifier> keys) {
    s3Client.deleteObjects(
        DeleteObjectsRequest.builder()
            .bucket(bucket())
            .delete(Delete.builder().objects(keys).build())
            .build());
    return keys.size();
  }

  private String sessionPrefix(String userId, String sessionId) {
    return applyPrefix(WORKSPACE_PREFIX + "/" + userId + "/sessions/" + sessionId + "/");
  }

  private String bucket() {
    return storageProperties.s3().bucket();
  }

  /**
   * Prefixes a logical key path with the configured bucket sub-path (internal shared-bucket seam);
   * mirrors {@code S3FileStorage.applyPrefix} — kept per-class per project convention (no shared
   * base class for these two conditional-bean storage adapters).
   */
  private String applyPrefix(String key) {
    String prefix = storageProperties.s3().keyPrefix();
    if (!StringUtils.hasText(prefix)) {
      return key;
    }
    return prefix.replaceAll("^/+|/+$", "") + "/" + key;
  }
}
