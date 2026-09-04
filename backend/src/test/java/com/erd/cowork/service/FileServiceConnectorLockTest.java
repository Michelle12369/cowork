package com.erd.cowork.service;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.exception.ConflictException;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.UploadNormalizer;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.web.dto.SessionMapper;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Mutual exclusion: a session with a locked connector selection rejects csv/xlsx uploads outright,
 * before any storage/DB side effect. Kept in its own minimal fixture (rather than {@link
 * FileServiceUploadTest}) because the rejection short-circuits before that fixture's default
 * happy-path stubs (normalizer/storage/parsing) would ever be touched, which would trip Mockito's
 * strict-stubbing check.
 */
@ExtendWith(MockitoExtension.class)
class FileServiceConnectorLockTest {

  @Mock SessionGuard sessionGuard;
  @Mock UploadedFileRepository files;
  @Mock FileStorage storage;
  @Mock FileParsingService parsing;
  @Mock UploadProperties limits;
  @Mock SessionMapper mapper;
  @Mock ChatSessionRepository sessionRepository;
  @Mock UploadNormalizer normalizer;
  @Mock TransactionTemplate transactionTemplate;

  FileService service;

  @BeforeEach
  void setUp() {
    service =
        new FileService(
            sessionGuard,
            files,
            storage,
            parsing,
            limits,
            mapper,
            sessionRepository,
            normalizer,
            transactionTemplate);
  }

  @Test
  void upload_lockedConnectorSession_returns409() {
    ChatSession session = new ChatSession();
    session.setId("session-1");
    session.setUserId("user-1");
    session.setSelectedConnectors(List.of("salesforce"));
    when(sessionGuard.loadOrCreateOwned("session-1")).thenReturn(session);

    MockMultipartFile upload =
        new MockMultipartFile(
            "file", "data.csv", "text/csv", "col\n1\n".getBytes(StandardCharsets.UTF_8));

    assertThatThrownBy(() -> service.upload("session-1", List.of(upload)))
        .isInstanceOf(ConflictException.class);

    verifyNoInteractions(storage);
    verify(files, never()).save(any(UploadedFile.class));
    verify(sessionRepository, never()).save(any(ChatSession.class));
  }
}
