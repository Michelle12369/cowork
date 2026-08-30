package com.erd.cowork.service;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.exception.ConflictException;
import com.erd.cowork.exception.ErrorCode;
import com.erd.cowork.exception.NotFoundException;
import com.erd.cowork.exception.UploadLimitException;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.parsing.FileParsingService;
import com.erd.cowork.parsing.NormalizedUpload;
import com.erd.cowork.parsing.UploadNormalizer;
import com.erd.cowork.parsing.model.FileProfile;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.storage.FileStorage;
import com.erd.cowork.storage.StorageCategory;
import com.erd.cowork.web.dto.FileDto;
import com.erd.cowork.web.dto.SessionMapper;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.io.input.CountingInputStream;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.util.CollectionUtils;
import org.springframework.web.multipart.MultipartFile;

@Slf4j
@Service
@RequiredArgsConstructor
@LogAnnotation
public class FileService {

  private static final Set<String> CSV_TYPES = Set.of("csv");

  /**
   * 這些型別原樣直存(不解密、不轉檔、不解析)，由 deepagent 下載時解密＋轉檔。
   *
   * <p>與 deepagent {@code source_cache} 的 {@code .xlsx} 副檔名推斷互為鏡像——此清單增加任何型別（尤其 csv）時該推斷失效，MUST 改
   * per-file metadata（見 spec）。internal 環境此類檔案是密文；本服務對其 bytes 不可有任何解讀。
   */
  private static final Set<String> RAW_STORED_TYPES = Set.of("xlsx");

  private final SessionGuard sessionGuard;
  private final UploadedFileRepository files;
  private final FileStorage storage;
  private final FileParsingService parsing;
  private final UploadProperties limits;
  private final SessionMapper mapper;
  private final ChatSessionRepository sessionRepository;
  private final UploadNormalizer normalizer;
  private final TransactionTemplate transactionTemplate;

  public List<FileDto> upload(String sessionId, List<MultipartFile> uploads) {
    ChatSession session = sessionGuard.loadOrCreateOwned(sessionId);
    // Mutual exclusion (spec §5): a session with a locked connector selection never accepts
    // csv/xlsx uploads — checked before any other side effect so a rejected upload leaves no
    // trace (no updatedAt touch, no storage/DB writes).
    if (!CollectionUtils.isEmpty(session.getSelectedConnectors())) {
      throw new ConflictException("本對話已鎖定 API 資料源，上傳請開新對話");
    }
    // Touch updatedAt so an upload-only session (no question asked yet) still counts as active
    // for retention purposes — same rationale as AgentOrchestrator#prepare.
    session.setUpdatedAt(Instant.now());
    sessionRepository.save(session);
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
        String storageKey;
        long storedBytes;
        String storedType;
        FileProfile profile;
        // Assigned inside the try below but declared outside it: per JLS 14.20.3 a try-with-
        // resources closes its resources BEFORE any catch runs, so a normalizer temp file whose
        // close() throws lands in the catch with the temp file already created. Only a finally
        // that can see `normalized` — hence this declaration — deletes it on that path.
        NormalizedUpload normalized = null;
        String uploadedExtension = FileParsingService.extension(filename);
        try {
          if (RAW_STORED_TYPES.contains(uploadedExtension)) {
            // 原樣直存:bytes 不解讀、不轉檔、不解析——deepagent 下載時才處理。
            try (InputStream in = upload.getInputStream();
                CountingInputStream counting = new CountingInputStream(in)) {
              storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
              // MUST be recorded before leaving this try block: try-with-resources routes a
              // close()-time IOException (counting/in) into the catch below, and if the key were
              // added after the block, that path would skip it — leaving an orphaned stored
              // object that the outer cleanup can never find.
              storedKeys.add(storageKey);
              storedBytes = counting.getByteCount();
            } catch (IOException exception) {
              throw new UncheckedIOException("failed to store upload: " + filename, exception);
            }
            storedType = uploadedExtension;
            profile = null;
          } else {
            // Normalize to CSV: deepagent-service points DuckDB at this file directly and DuckDB
            // has no xlsx reader, so only CSV may land — RAW_STORED_TYPES bypasses this entirely.
            try (InputStream in = upload.getInputStream()) {
              normalized = normalizer.normalize(in, filename);
            } catch (IOException exception) {
              throw new UncheckedIOException("failed to normalize upload: " + filename, exception);
            }
            storedType = normalized.type();
            // DELETE_ON_CLOSE removes the normalizer's temp file once it has been streamed to
            // storage. That alone is not a guarantee: it only fires if content.close() runs, which
            // never happens when Files.newInputStream itself throws while acquiring the resource.
            // The outer finally below deletes unconditionally so a temp file holding normalized
            // user data can never survive this method, however the open or store attempt fails.
            try (InputStream content =
                    Files.newInputStream(normalized.content(), StandardOpenOption.DELETE_ON_CLOSE);
                CountingInputStream counting = new CountingInputStream(content)) {
              storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
              // MUST be recorded before leaving this try block: try-with-resources routes a
              // close()-time IOException (counting/content) into the catch below, and if the key
              // were added after the block, that path would skip it — leaving an orphaned stored
              // object that the outer cleanup can never find.
              storedKeys.add(storageKey);
              // Post-normalization byte count, not upload.getSize(): spreadsheet-to-CSV
              // conversion changes the length, so the multipart size would desync sizeBytes (and
              // the session quota) from what actually landed on disk.
              storedBytes = counting.getByteCount();
            } catch (IOException exception) {
              throw new UncheckedIOException("failed to store upload: " + filename, exception);
            }
            try (InputStream stored = storage.read(storageKey)) {
              // storedType, not filename: parsing must dispatch on the on-disk format, which may
              // differ from the uploaded extension now that xlsx is normalized to CSV before
              // storage.
              profile = parsing.profile(storedType, stored);
            } catch (IOException exception) {
              throw new UncheckedIOException(
                  "failed to read stored file: " + storageKey, exception);
            }
          }
        } finally {
          if (normalized != null) {
            deleteNormalizedTempFileQuietly(normalized.content());
          }
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
        entity.setSizeBytes(storedBytes);
        entity.setType(storedType);
        // profile is null for RAW_STORED_TYPES (xlsx): no parsing happened, so no profile exists.
        entity.setRowCount(profile == null ? null : profile.rowCount());
        entity.setMetadataJson(profile == null ? null : parsing.toJson(profile));
        entities.add(entity);
      }

      // Batch save runs inside a single transaction — a failure partway through rolls back all
      // DB rows saved so far. The storage side effects (below, in the outer catch) are not
      // transactional resources, so they stay outside this boundary and are cleaned up
      // separately on failure.
      return transactionTemplate.execute(
          status -> {
            List<FileDto> result = new ArrayList<>();
            for (UploadedFile entity : entities) {
              result.add(mapper.toFileDto(files.save(entity)));
            }
            return result;
          });
    } catch (RuntimeException exception) {
      // Any failure (parse error, or a save failure) reverts the storage side effects so upload
      // artifacts are not left orphaned on disk; already-saved DB rows are not rolled back.
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

  /**
   * Deletes a normalizer temp file unconditionally, regardless of whether it was ever opened (and
   * thus whether {@code DELETE_ON_CLOSE} ever had a chance to fire). The path is normalized user
   * data at rest in the JVM temp dir, so leaving it behind on any failure is not acceptable — a
   * delete failure here is logged (path only, never content) rather than thrown, so it can never
   * mask the original upload failure that triggered cleanup.
   */
  private void deleteNormalizedTempFileQuietly(Path temporaryFile) {
    try {
      Files.deleteIfExists(temporaryFile);
    } catch (IOException exception) {
      log.warn("failed to delete normalizer temp file {}", temporaryFile, exception);
    }
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
