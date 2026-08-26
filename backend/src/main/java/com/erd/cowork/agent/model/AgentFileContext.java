package com.erd.cowork.agent.model;

import com.erd.cowork.domain.UploadedFile;
import com.erd.cowork.parsing.model.FileProfile;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;

/**
 * File reference passed to an {@link com.erd.cowork.agent.provider.AgentProvider}.
 *
 * @param profile column/row schema profile parsed from the upload's stored {@code metadataJson};
 *     {@code null} when the file was stored without upload-time parsing (e.g. the internal xlsx
 *     ciphertext-zip path) or the stored JSON failed to parse — schema is derived at analysis time
 *     instead, and prompt assembly must degrade gracefully rather than dereference it.
 */
@Slf4j
public record AgentFileContext(
    String alias, String name, String type, String storageKey, FileProfile profile) {

  /**
   * Builds an {@link AgentFileContext} from a persisted {@link UploadedFile}, tolerating a null or
   * unparseable {@code metadataJson}: the file is always included, with {@code profile=null} in
   * that case, so it stays visible to a provider (e.g. the deepagent line only needs
   * alias/storageKey/type) instead of silently disappearing from the request. Shared by {@link
   * com.erd.cowork.agent.AgentOrchestrator} and {@link
   * com.erd.cowork.service.ArtifactRepairService} — the two independent producers of file contexts
   * — so both stay in sync on this semantic.
   */
  public static AgentFileContext fromUploadedFile(
      UploadedFile uploadedFile, ObjectMapper objectMapper) {
    FileProfile profile = null;
    if (uploadedFile.getMetadataJson() == null) {
      log.debug("null metadataJson for file {}; including with profile=null", uploadedFile.getId());
    } else {
      try {
        profile = objectMapper.readValue(uploadedFile.getMetadataJson(), FileProfile.class);
      } catch (Exception exception) {
        log.warn(
            "failed to parse metadataJson for file {}: {}; including with profile=null",
            uploadedFile.getId(),
            exception.getMessage());
      }
    }
    return new AgentFileContext(
        uploadedFile.getAlias(),
        uploadedFile.getName(),
        uploadedFile.getType(),
        uploadedFile.getStorageKey(),
        profile);
  }
}
