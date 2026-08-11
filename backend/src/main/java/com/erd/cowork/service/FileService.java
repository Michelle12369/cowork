package com.erd.cowork.service;

import com.erd.cowork.config.UploadProperties;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
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
import com.erd.cowork.storage.UploadDecryptor;
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
import org.springframework.web.multipart.MultipartFile;

@Slf4j
@Service
@RequiredArgsConstructor
@LogAnnotation
public class FileService {

  private static final Set<String> CSV_TYPES = Set.of("csv");

  /**
   * Upload types that arrive encrypted and must go through {@link UploadDecryptor}. Everything else
   * is stored as-received.
   *
   * <p>⚠️ In the company environment only xlsx is encrypted, so routing csv through the internal
   * decryption API would be a wasted round-trip (csv uploads reach 2GB). If csv ever starts
   * arriving encrypted, this set MUST be updated: a type missing from it is stored WITHOUT
   * decryption, which silently persists ciphertext as if it were data — no exception, no warning,
   * and DuckDB later reads garbage.
   */
  private static final Set<String> ENCRYPTED_UPLOAD_TYPES = Set.of("xlsx");

  private final SessionGuard sessionGuard;
  private final UploadedFileRepository files;
  private final FileStorage storage;
  private final FileParsingService parsing;
  private final UploadProperties limits;
  private final SessionMapper mapper;
  private final ChatSessionRepository sessionRepository;
  private final UploadDecryptor decryptor;
  private final UploadNormalizer normalizer;

  public List<FileDto> upload(String sessionId, List<MultipartFile> uploads) {
    ChatSession session = sessionGuard.loadOrCreateOwned(sessionId);
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
        // resources closes its resources BEFORE any catch runs, so a decrypted stream whose
        // close() throws lands in the catch with the temp file already created. Only a finally
        // that can see `normalized` — hence this declaration — deletes it on that path.
        NormalizedUpload normalized = null;
        String uploadedExtension = FileParsingService.extension(filename);
        try {
          // Decrypt first (only ENCRYPTED_UPLOAD_TYPES actually arrive encrypted — see that
          // constant), then normalize to CSV: deepagent-service points DuckDB at this file
          // directly and DuckDB has no xlsx reader, so only CSV may land.
          try (InputStream in = upload.getInputStream();
              // csv is never encrypted, so plaintext just aliases `in` and decrypt() is skipped —
              // that means `in` gets closed twice (once via `plaintext`, once via itself), which
              // is safe: UploadDecryptor's contract requires close() to be idempotent, and
              // PassthroughUploadDecryptor already relies on this exact aliasing.
              InputStream plaintext =
                  ENCRYPTED_UPLOAD_TYPES.contains(uploadedExtension)
                      ? decryptor.decrypt(in, filename)
                      : in) {
            normalized = normalizer.normalize(plaintext, filename);
          } catch (IOException exception) {
            throw new UncheckedIOException("failed to normalize upload: " + filename, exception);
          }
          storedType = normalized.type();
          // DELETE_ON_CLOSE removes the normalizer's temp file once it has been streamed to
          // storage. That alone is not a guarantee: it only fires if content.close() runs, which
          // never happens when Files.newInputStream itself throws while acquiring the resource.
          // The outer finally below deletes unconditionally so a temp file holding decrypted user
          // data can never survive this method, however the open or store attempt fails.
          try (InputStream content =
                  Files.newInputStream(normalized.content(), StandardOpenOption.DELETE_ON_CLOSE);
              CountingInputStream counting = new CountingInputStream(content)) {
            storageKey = storage.store(StorageCategory.UPLOAD, sessionId, filename, counting);
            // MUST be recorded before leaving this try block: try-with-resources routes a
            // close()-time IOException (counting/content) into the catch below, and if the key
            // were added after the block, that path would skip it — leaving an orphaned stored
            // object that the outer cleanup can never find.
            storedKeys.add(storageKey);
            // Post-normalization byte count, not upload.getSize(): decryption and (for xlsx)
            // spreadsheet-to-CSV conversion both change the length, so the multipart size would
            // desync sizeBytes (and the session quota) from what actually landed on disk.
            storedBytes = counting.getByteCount();
          } catch (IOException exception) {
            throw new UncheckedIOException("failed to store upload: " + filename, exception);
          }
          try (InputStream stored = storage.read(storageKey)) {
            // storedType, not filename: parsing must dispatch on the on-disk format, which may
            // differ from the uploaded extension now that xlsx is normalized to CSV before storage.
            profile = parsing.profile(storedType, stored);
          } catch (IOException exception) {
            throw new UncheckedIOException("failed to read stored file: " + storageKey, exception);
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
        entity.setRowCount(profile.rowCount());
        entity.setMetadataJson(parsing.toJson(profile));
        entities.add(entity);
      }

      // Bare per-file save on purpose — Branch 1（純遷移基座）不引入交易語意，寫入非原子。
      // 若中途 save 失敗，下方 catch 仍會清掉 storage 側已寫入的物件，但已成功 save 的 DB
      // row 不會回滾（多文件原子性策略解耦到 Branch 2/3，本分支不含）。
      List<FileDto> result = new ArrayList<>();
      for (UploadedFile entity : entities) {
        result.add(mapper.toFileDto(files.save(entity)));
      }
      return result;
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
   * thus whether {@code DELETE_ON_CLOSE} ever had a chance to fire). The path is decrypted user
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
