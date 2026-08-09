package com.erd.cowork.service;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.UploadNormalizer;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.web.dto.SessionMapper;
import java.io.IOException;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionTemplate;

@ExtendWith(MockitoExtension.class)
class FileServiceDeleteTest {

  @Mock SessionGuard sessionGuard;
  @Mock UploadedFileRepository files;
  @Mock FileStorage storage;
  @Mock FileParsingService parsing;
  @Mock UploadProperties limits;
  @Mock SessionMapper mapper;
  @Mock TransactionTemplate transactionTemplate;
  @Mock ChatSessionRepository sessionRepository;
  @Mock UploadNormalizer normalizer;

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
            transactionTemplate,
            sessionRepository,
            normalizer);
  }

  /**
   * Verifies that deleting an expired file succeeds even when the physical storage object is
   * already gone (the 30-day retention job removes both the file and its storage entry). The
   * IOException from {@link FileStorage#delete} must only be logged — it must not block the DB
   * record deletion.
   */
  @Test
  void delete_expiredFile_storageIoExceptionToleratedAndDbDeleteProceeds() throws IOException {
    ChatSession session = new ChatSession();
    when(sessionGuard.loadOwned(anyString())).thenReturn(session);

    UploadedFile expiredFile = new UploadedFile();
    expiredFile.setExpired(true);
    expiredFile.setSessionId("session-1");
    expiredFile.setStorageKey("sessions/session-1/old-file.csv");
    when(files.findById("file-1")).thenReturn(Optional.of(expiredFile));
    doThrow(new IOException("no such file"))
        .when(storage)
        .delete("sessions/session-1/old-file.csv");

    // Must not propagate the IOException — warn-only, then proceed with DB delete.
    service.delete("session-1", "file-1");

    verify(files).delete(expiredFile);
  }

  @Test
  void delete_activeFile_storageIoExceptionToleratedAndDbDeleteProceeds() throws IOException {
    ChatSession session = new ChatSession();
    when(sessionGuard.loadOwned(anyString())).thenReturn(session);

    UploadedFile activeFile = new UploadedFile();
    activeFile.setExpired(false);
    activeFile.setSessionId("session-2");
    activeFile.setStorageKey("sessions/session-2/data.csv");
    when(files.findById("file-2")).thenReturn(Optional.of(activeFile));
    doThrow(new IOException("storage unavailable"))
        .when(storage)
        .delete("sessions/session-2/data.csv");

    service.delete("session-2", "file-2");

    verify(files).delete(activeFile);
  }
}
