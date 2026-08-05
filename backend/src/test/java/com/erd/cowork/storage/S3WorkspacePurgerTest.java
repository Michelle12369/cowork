package com.erd.cowork.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.erd.cowork.config.StorageProperties;
import com.erd.cowork.config.StorageProperties.S3;
import java.io.IOException;
import java.util.List;
import java.util.stream.Collectors;
import java.util.stream.IntStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import software.amazon.awssdk.core.exception.SdkClientException;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectsRequest;
import software.amazon.awssdk.services.s3.model.DeleteObjectsResponse;
import software.amazon.awssdk.services.s3.model.ListObjectsV2Request;
import software.amazon.awssdk.services.s3.model.ListObjectsV2Response;
import software.amazon.awssdk.services.s3.model.S3Object;
import software.amazon.awssdk.services.s3.paginators.ListObjectsV2Iterable;

@ExtendWith(MockitoExtension.class)
class S3WorkspacePurgerTest {

  private static final String BUCKET = "test-bucket";
  private static final String WORKSPACE_PREFIX = "workspace";
  private static final String USER_ID = "user-1";
  private static final String SESSION_ID = "sess-1";
  private static final String EXPECTED_PREFIX = "workspace/user-1/sessions/sess-1/";

  @Mock private S3Client s3Client;

  private S3WorkspacePurger purger;

  @BeforeEach
  void setUp() {
    S3 s3Config = new S3("", "us-east-1", BUCKET, false, WORKSPACE_PREFIX);
    StorageProperties properties = new StorageProperties("s3", null, null, null, null, s3Config);
    purger = new S3WorkspacePurger(s3Client, properties);
  }

  // ── sessionExists ────────────────────────────────────────────────────────

  @Test
  void sessionExists_objectsUnderPrefix_returnsTrue() {
    when(s3Client.listObjectsV2(any(ListObjectsV2Request.class)))
        .thenReturn(ListObjectsV2Response.builder().keyCount(1).build());

    boolean exists = purger.sessionExists(USER_ID, SESSION_ID);

    assertThat(exists).isTrue();
  }

  @Test
  void sessionExists_emptyPrefix_returnsFalse() {
    when(s3Client.listObjectsV2(any(ListObjectsV2Request.class)))
        .thenReturn(ListObjectsV2Response.builder().keyCount(0).build());

    boolean exists = purger.sessionExists(USER_ID, SESSION_ID);

    assertThat(exists).isFalse();
  }

  @Test
  void sessionExists_usesMaxKeysOneAndConfiguredPrefix() {
    ArgumentCaptor<ListObjectsV2Request> captor =
        ArgumentCaptor.forClass(ListObjectsV2Request.class);
    when(s3Client.listObjectsV2(captor.capture()))
        .thenReturn(ListObjectsV2Response.builder().keyCount(0).build());

    purger.sessionExists(USER_ID, SESSION_ID);

    assertThat(captor.getValue().bucket()).isEqualTo(BUCKET);
    assertThat(captor.getValue().prefix()).isEqualTo(EXPECTED_PREFIX);
    assertThat(captor.getValue().maxKeys()).isEqualTo(1);
  }

  // ── purgeSession ─────────────────────────────────────────────────────────

  @Test
  void purgeSession_manyObjects_deletesInBatchesOf1000() throws IOException {
    List<S3Object> objects =
        IntStream.range(0, 1500)
            .mapToObj(index -> S3Object.builder().key(EXPECTED_PREFIX + "file-" + index).build())
            .collect(Collectors.toList());
    ListObjectsV2Iterable page = singlePageIterable(objects);
    when(s3Client.listObjectsV2Paginator(any(ListObjectsV2Request.class))).thenReturn(page);
    ArgumentCaptor<DeleteObjectsRequest> deleteCaptor =
        ArgumentCaptor.forClass(DeleteObjectsRequest.class);
    when(s3Client.deleteObjects(deleteCaptor.capture()))
        .thenReturn(DeleteObjectsResponse.builder().build());

    boolean purged = purger.purgeSession(USER_ID, SESSION_ID);

    assertThat(purged).isTrue();
    List<DeleteObjectsRequest> deleteRequests = deleteCaptor.getAllValues();
    assertThat(deleteRequests).hasSize(2);
    assertThat(deleteRequests.get(0).delete().objects()).hasSize(1000);
    assertThat(deleteRequests.get(1).delete().objects()).hasSize(500);
  }

  @Test
  void purgeSession_sdkException_wrapsAsIOException() {
    when(s3Client.listObjectsV2Paginator(any(ListObjectsV2Request.class)))
        .thenThrow(SdkClientException.builder().message("connection refused").build());

    assertThatThrownBy(() -> purger.purgeSession(USER_ID, SESSION_ID))
        .isInstanceOf(IOException.class)
        .hasMessageContaining("S3 workspace purge failed")
        .hasMessageContaining(EXPECTED_PREFIX);
  }

  @Test
  void purgeSession_prefixUsesConfiguredWorkspacePrefix() throws IOException {
    ArgumentCaptor<ListObjectsV2Request> captor =
        ArgumentCaptor.forClass(ListObjectsV2Request.class);
    ListObjectsV2Iterable emptyPage = singlePageIterable(List.of());
    when(s3Client.listObjectsV2Paginator(captor.capture())).thenReturn(emptyPage);

    purger.purgeSession(USER_ID, SESSION_ID);

    assertThat(captor.getValue().bucket()).isEqualTo(BUCKET);
    assertThat(captor.getValue().prefix()).isEqualTo(EXPECTED_PREFIX);
  }

  private ListObjectsV2Iterable singlePageIterable(List<S3Object> objects) {
    S3Client stubClient = mock(S3Client.class);
    when(stubClient.listObjectsV2(any(ListObjectsV2Request.class)))
        .thenReturn(ListObjectsV2Response.builder().contents(objects).isTruncated(false).build());
    return new ListObjectsV2Iterable(
        stubClient, ListObjectsV2Request.builder().bucket(BUCKET).build());
  }
}
