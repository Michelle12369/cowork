package com.erd.cowork.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.config.StorageProperties.S3;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import software.amazon.awssdk.core.exception.SdkClientException;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;
import software.amazon.awssdk.services.s3.model.DeleteObjectResponse;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectResponse;

@ExtendWith(MockitoExtension.class)
class S3FileStorageTest {

  private static final String BUCKET = "test-bucket";
  private static final String SESSION_ID = "sess-test";

  @Mock private S3Client s3Client;

  private S3FileStorage storage;

  @BeforeEach
  void setUp() {
    S3 s3Config = new S3("", BUCKET, "test-access-key", "test-secret-key", "");
    StorageProperties properties = new StorageProperties("s3", null, null, null, null, s3Config);
    storage = new S3FileStorage(s3Client, properties);
  }

  private S3FileStorage storageWithKeyPrefix(String keyPrefix) {
    S3 s3Config = new S3("", BUCKET, "test-access-key", "test-secret-key", keyPrefix);
    StorageProperties properties = new StorageProperties("s3", null, null, null, null, s3Config);
    return new S3FileStorage(s3Client, properties);
  }

  // ── store ─────────────────────────────────────────────────────────────────

  @Test
  void store_putsObjectWithCorrectBucketAndKey() throws IOException {
    byte[] content = "col1,col2\n1,2".getBytes(StandardCharsets.UTF_8);
    ArgumentCaptor<PutObjectRequest> requestCaptor =
        ArgumentCaptor.forClass(PutObjectRequest.class);
    when(s3Client.putObject(any(PutObjectRequest.class), any(RequestBody.class)))
        .thenReturn(PutObjectResponse.builder().build());

    String key =
        storage.store(
            StorageCategory.UPLOAD, SESSION_ID, "data.csv", new ByteArrayInputStream(content));

    verify(s3Client).putObject(requestCaptor.capture(), any(RequestBody.class));
    assertThat(requestCaptor.getValue().bucket()).isEqualTo(BUCKET);
    assertThat(requestCaptor.getValue().key()).isEqualTo(key);
    assertThat(key).startsWith("uploads/" + SESSION_ID + "/").endsWith("_data.csv");
  }

  @Test
  void store_contentLengthMatchesInputBytes() throws IOException {
    byte[] content = "hello,world\n".getBytes(StandardCharsets.UTF_8);
    ArgumentCaptor<RequestBody> bodyCaptor = ArgumentCaptor.forClass(RequestBody.class);
    when(s3Client.putObject(any(PutObjectRequest.class), bodyCaptor.capture()))
        .thenReturn(PutObjectResponse.builder().build());

    storage.store(
        StorageCategory.UPLOAD, SESSION_ID, "data.csv", new ByteArrayInputStream(content));

    // RequestBody.fromFile() sets a known content length equal to the spooled file size.
    assertThat(bodyCaptor.getValue().optionalContentLength()).hasValue((long) content.length);
  }

  @Test
  void store_tempFile_isDeletedAfterSuccessfulUpload() throws IOException {
    when(s3Client.putObject(any(PutObjectRequest.class), any(RequestBody.class)))
        .thenReturn(PutObjectResponse.builder().build());
    Path systemTempDir = Paths.get(System.getProperty("java.io.tmpdir"));

    Set<Path> beforeUpload =
        Files.list(systemTempDir)
            .filter(p -> p.getFileName().toString().startsWith("erd-upload-"))
            .collect(Collectors.toSet());

    storage.store(
        StorageCategory.UPLOAD, SESSION_ID, "data.csv", new ByteArrayInputStream("x".getBytes()));

    Set<Path> afterUpload =
        Files.list(systemTempDir)
            .filter(p -> p.getFileName().toString().startsWith("erd-upload-"))
            .collect(Collectors.toSet());

    afterUpload.removeAll(beforeUpload);
    assertThat(afterUpload).as("no erd-upload-* temp files should remain after store").isEmpty();
  }

  @Test
  void store_sdkException_wrappedAsIOException() {
    when(s3Client.putObject(any(PutObjectRequest.class), any(RequestBody.class)))
        .thenThrow(SdkClientException.builder().message("connection refused").build());

    assertThatThrownBy(
            () ->
                storage.store(
                    StorageCategory.UPLOAD,
                    SESSION_ID,
                    "data.csv",
                    new ByteArrayInputStream("x".getBytes())))
        .isInstanceOf(IOException.class)
        .hasMessageContaining("S3 store failed");
  }

  // ── read ──────────────────────────────────────────────────────────────────

  @Test
  void read_invokesGetObjectWithCorrectBucketAndKey() throws IOException {
    ArgumentCaptor<GetObjectRequest> captor = ArgumentCaptor.forClass(GetObjectRequest.class);
    when(s3Client.getObject(captor.capture())).thenReturn(null);

    // null is acceptable here — we only verify the request parameters.
    storage.read("sess-1/some-uuid_data.csv");

    assertThat(captor.getValue().bucket()).isEqualTo(BUCKET);
    assertThat(captor.getValue().key()).isEqualTo("sess-1/some-uuid_data.csv");
  }

  @Test
  void read_noSuchKey_throwsIOExceptionWithKeyInMessage() {
    when(s3Client.getObject(any(GetObjectRequest.class)))
        .thenThrow(NoSuchKeyException.builder().message("not found").build());

    assertThatThrownBy(() -> storage.read("missing/key"))
        .isInstanceOf(IOException.class)
        .hasMessageContaining("missing/key");
  }

  @Test
  void read_sdkException_wrappedAsIOException() {
    when(s3Client.getObject(any(GetObjectRequest.class)))
        .thenThrow(SdkClientException.builder().message("network error").build());

    assertThatThrownBy(() -> storage.read("some/key"))
        .isInstanceOf(IOException.class)
        .hasMessageContaining("S3 read failed");
  }

  // ── delete ────────────────────────────────────────────────────────────────

  @Test
  void delete_invokesDeleteObjectWithCorrectBucketAndKey() throws IOException {
    ArgumentCaptor<DeleteObjectRequest> captor = ArgumentCaptor.forClass(DeleteObjectRequest.class);
    when(s3Client.deleteObject(captor.capture()))
        .thenReturn(DeleteObjectResponse.builder().build());

    storage.delete("sess-1/some-uuid_data.csv");

    assertThat(captor.getValue().bucket()).isEqualTo(BUCKET);
    assertThat(captor.getValue().key()).isEqualTo("sess-1/some-uuid_data.csv");
  }

  @Test
  void delete_sdkException_wrappedAsIOException() {
    when(s3Client.deleteObject(any(DeleteObjectRequest.class)))
        .thenThrow(SdkClientException.builder().message("timeout").build());

    assertThatThrownBy(() -> storage.delete("some/key"))
        .isInstanceOf(IOException.class)
        .hasMessageContaining("S3 delete failed");
  }

  // ── key prefix ───────────────────────────────────────────────────────────

  @Test
  void store_keyPrefixSet_putsUnderPrefixButReturnsLogicalKey() throws IOException {
    S3FileStorage prefixedStorage = storageWithKeyPrefix("erd-cowork");
    ArgumentCaptor<PutObjectRequest> requestCaptor =
        ArgumentCaptor.forClass(PutObjectRequest.class);
    when(s3Client.putObject(requestCaptor.capture(), any(RequestBody.class)))
        .thenReturn(PutObjectResponse.builder().build());

    String key =
        prefixedStorage.store(
            StorageCategory.UPLOAD,
            SESSION_ID,
            "data.csv",
            new ByteArrayInputStream("x".getBytes(StandardCharsets.UTF_8)));

    assertThat(requestCaptor.getValue().key()).startsWith("erd-cowork/uploads/" + SESSION_ID + "/");
    assertThat(key).startsWith("uploads/" + SESSION_ID + "/").doesNotContain("erd-cowork/");
  }

  @Test
  void read_keyPrefixSet_getsFromPrefixedKey() throws IOException {
    S3FileStorage prefixedStorage = storageWithKeyPrefix("erd-cowork");
    ArgumentCaptor<GetObjectRequest> captor = ArgumentCaptor.forClass(GetObjectRequest.class);
    when(s3Client.getObject(captor.capture())).thenReturn(null);

    prefixedStorage.read("uploads/sess-1/uuid_a.csv");

    assertThat(captor.getValue().key()).isEqualTo("erd-cowork/uploads/sess-1/uuid_a.csv");
  }

  @Test
  void delete_keyPrefixSet_deletesPrefixedKey() throws IOException {
    S3FileStorage prefixedStorage = storageWithKeyPrefix("erd-cowork");
    ArgumentCaptor<DeleteObjectRequest> captor = ArgumentCaptor.forClass(DeleteObjectRequest.class);
    when(s3Client.deleteObject(captor.capture()))
        .thenReturn(DeleteObjectResponse.builder().build());

    prefixedStorage.delete("uploads/sess-1/uuid_a.csv");

    assertThat(captor.getValue().key()).isEqualTo("erd-cowork/uploads/sess-1/uuid_a.csv");
  }

  @Test
  void store_keyPrefixEmpty_usesKeyVerbatim() throws IOException {
    ArgumentCaptor<PutObjectRequest> requestCaptor =
        ArgumentCaptor.forClass(PutObjectRequest.class);
    when(s3Client.putObject(requestCaptor.capture(), any(RequestBody.class)))
        .thenReturn(PutObjectResponse.builder().build());

    String key =
        storage.store(
            StorageCategory.UPLOAD,
            SESSION_ID,
            "data.csv",
            new ByteArrayInputStream("x".getBytes(StandardCharsets.UTF_8)));

    assertThat(requestCaptor.getValue().key()).isEqualTo(key);
  }
}
