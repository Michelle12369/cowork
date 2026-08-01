package com.erd.cowork.service;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.exception.ErrorCode;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.exception.UploadLimitException;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SessionMapper;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

@Slf4j
@Service
@RequiredArgsConstructor
public class FileService {

  private static final Set<String> CSV_TYPES = Set.of("csv");

  private final SessionGuard sessionGuard;
  private final UploadedFileRepository files;
  private final FileStorage storage;
  private final FileParsingService parsing;
  private final UploadProperties limits;
  private final SessionMapper mapper;
  private final TransactionTemplate transactionTemplate;

  public List<FileDto> upload(String sessionId, List<MultipartFile> uploads) {
    sessionGuard.loadOrCreateOwned(sessionId);
    // Quota is measured against active files only — expired (retention-cleaned) files no longer
    // occupy storage and MUST NOT count towards the session limit.
    List<UploadedFile> active = files.findBySessionIdAndExpiredFalse(sessionId);
    validate(active, uploads);
    log.info(
        "uploading session={} files={} totalBytes={}",
        sessionId,
        uploads.size(),
        uploads.stream().mapToLong(MultipartFile::getSize).sum());

    // Alias generation scans the full history (incl. expired) so that slugs never collide with
    // a previously used alias. Each alias assigned within the current batch is also tracked so
    // intra-batch collisions are avoided before any entity reaches the database.
    Set<String> occupiedAliases = new HashSet<>();
    for (UploadedFile historical : files.findBySessionId(sessionId)) {
      occupiedAliases.add(historical.getAlias());
    }

    List<String> storedKeys = new ArrayList<>();
    try {
      // IO phase — storage + parsing run outside any transaction.
      List<UploadedFile> entities = new ArrayList<>();
      for (MultipartFile upload : uploads) {
        String filename =
            upload.getOriginalFilename() == null ? "file" : upload.getOriginalFilename();
        String ext = FileParsingService.extension(filename);
        String storageKey;
        FileProfile profile;
        try (InputStream in = upload.getInputStream()) {
          storageKey = storage.store(sessionId, filename, in);
        } catch (IOException exception) {
          throw new UncheckedIOException("failed to store upload: " + filename, exception);
        }
        storedKeys.add(storageKey);
        try (InputStream stored = storage.read(storageKey)) {
          profile = parsing.profile(filename, stored);
        } catch (IOException exception) {
          throw new UncheckedIOException("failed to read stored file: " + storageKey, exception);
        }

        FileAliasUtils.AliasResolution resolution =
            FileAliasUtils.generateAlias(filename, occupiedAliases);
        // Track immediately so the next file in this batch does not claim the same alias.
        occupiedAliases.add(resolution.alias());

        UploadedFile entity = new UploadedFile();
        entity.setSessionId(sessionId);
        entity.setName(FileAliasUtils.buildDisplayName(filename, resolution.suffixNumber()));
        entity.setAlias(resolution.alias());
        entity.setStorageKey(storageKey);
        entity.setSizeBytes(upload.getSize());
        entity.setType(ext);
        entity.setRowCount(profile.rowCount());
        entity.setMetadataJson(parsing.toJson(profile));
        entities.add(entity);
      }

      // Programmatic transaction on purpose — the boundary must NOT cover the IO phase above.
      // @Transactional on upload() would pin a pool connection for the whole multi-second
      // store/parse (pool exhaustion under concurrent uploads) and can't be deferred mid-method
      // via Spring's AOP proxy; the storage cleanup in the catch block below must also stay
      // outside the JDBC transaction since it is not rollback-covered.
      return transactionTemplate.execute(
          status -> {
            List<FileDto> result = new ArrayList<>();
            for (UploadedFile entity : entities) {
              result.add(mapper.toFileDto(files.save(entity)));
            }
            return result;
          });
    } catch (RuntimeException exception) {
      // Any failure (parse error, or a transaction rollback during save) reverts the storage side
      // effects so no orphaned objects are left behind.
      for (String key : storedKeys) {
        try {
          storage.delete(key);
        } catch (Exception cleanupException) {
          log.warn("cleanup failed for {}", key, cleanupException);
        }
      }
      throw exception;
    }
  }

  @Transactional
  public void delete(String sessionId, String fileId) {
    sessionGuard.loadOwned(sessionId);
    UploadedFile file =
        files
            .findById(fileId)
            .filter(uploadedFile -> uploadedFile.getSessionId().equals(sessionId))
            .orElseThrow(() -> new NotFoundException("file not found: " + fileId));
    try {
      storage.delete(file.getStorageKey());
    } catch (IOException exception) {
      log.warn("failed to delete storage object {}", file.getStorageKey(), exception);
    }
    files.delete(file);
  }

  private void validate(List<UploadedFile> existing, List<MultipartFile> uploads) {
    if (existing.size() + uploads.size() > limits.maxFiles()) {
      throw new UploadLimitException(
          ErrorCode.UPLOAD_LIMIT, "You can attach up to " + limits.maxFiles() + " files per chat.");
    }
    long total = existing.stream().mapToLong(UploadedFile::getSizeBytes).sum();
    for (MultipartFile upload : uploads) {
      String ext =
          FileParsingService.extension(
              upload.getOriginalFilename() == null ? "" : upload.getOriginalFilename());
      long size = upload.getSize();
      if (CSV_TYPES.contains(ext)) {
        if (size > limits.maxCsvBytes()) {
          throw new UploadLimitException(ErrorCode.UPLOAD_LIMIT, "CSV file exceeds size limit.");
        }
      } else if ("xlsx".equals(ext)) {
        if (size > limits.maxXlsxBytes()) {
          throw new UploadLimitException(ErrorCode.UPLOAD_LIMIT, "xlsx file exceeds size limit.");
        }
      } else {
        throw new UploadLimitException(ErrorCode.UNSUPPORTED_TYPE, "Unsupported file type: " + ext);
      }
      total += size;
    }
    if (total > limits.maxSessionBytes()) {
      throw new UploadLimitException(
          ErrorCode.UPLOAD_LIMIT, "Total attachments would exceed the session limit.");
    }
  }
}
