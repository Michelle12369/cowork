package com.erd.cowork.web.dto;

import com.erd.cowork.domain.ChatMessage;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.domain.UploadedFile;
import org.mapstruct.Mapper;
import org.mapstruct.Mapping;
import org.mapstruct.ReportingPolicy;

@Mapper(componentModel = "spring", unmappedTargetPolicy = ReportingPolicy.ERROR)
public interface SessionMapper {

  /** Maps ChatSession → SessionSummaryDto (id, title, updatedAt — all name-matched). */
  SessionSummaryDto toSummary(ChatSession chatSession);

  /**
   * Maps ChatMessage → MessageDto. {@code sender} is an enum; MapStruct calls {@code Sender.name()}
   * automatically when the target is {@code String}. {@code artifactTitle} is not on the entity —
   * it is resolved and populated by {@link com.erd.cowork.service.SessionService} after mapping.
   */
  @Mapping(target = "artifactTitle", ignore = true)
  MessageDto toMessageDto(ChatMessage chatMessage);

  /** Maps UploadedFile → FileDto (id, name, alias, sizeBytes, type — all name-matched). */
  FileDto toFileDto(UploadedFile uploadedFile);
}
