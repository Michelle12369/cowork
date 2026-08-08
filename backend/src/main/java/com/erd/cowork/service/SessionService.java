package com.erd.cowork.service;

import com.erd.cowork.context.CoworkContextHolder;
import com.erd.cowork.domain.Artifact;
import com.erd.cowork.domain.ChatSession;
import com.erd.cowork.logging.LogAnnotation;
import com.erd.cowork.repo.ArtifactRepository;
import com.erd.cowork.repo.ChatMessageRepository;
import com.erd.cowork.repo.ChatSessionRepository;
import com.erd.cowork.repo.UploadedFileRepository;
import com.erd.cowork.web.dto.MessageDto;
import com.erd.cowork.web.dto.SessionDetailDto;
import com.erd.cowork.web.dto.SessionMapper;
import com.erd.cowork.web.dto.SessionSummaryDto;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.stream.Collectors;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
@Transactional
@LogAnnotation
public class SessionService {

  private final ChatSessionRepository sessions;
  private final ChatMessageRepository messages;
  private final UploadedFileRepository files;
  private final ArtifactRepository artifacts;
  private final SessionMapper mapper;
  private final SessionGuard sessionGuard;

  @Transactional(readOnly = true)
  public List<SessionSummaryDto> list() {
    return sessions.findByUserIdOrderByUpdatedAtDesc(CoworkContextHolder.userId()).stream()
        .map(mapper::toSummary)
        .toList();
  }

  @Transactional(readOnly = true)
  public SessionDetailDto get(String id) {
    ChatSession session = sessionGuard.loadOwned(id);
    List<MessageDto> mapped =
        messages.findBySessionIdOrderByCreatedAtAsc(id).stream().map(mapper::toMessageDto).toList();
    List<MessageDto> msgs = withArtifactTitles(mapped);
    var fileDtos = files.findBySessionId(id).stream().map(mapper::toFileDto).toList();
    return new SessionDetailDto(
        session.getId(), session.getTitle(), session.getCreatedAt(), msgs, fileDtos);
  }

  /**
   * Populates {@code artifactTitle} on messages that reference an artifact. Uses a single batch
   * {@code findAllById} lookup to avoid N+1 queries, then rebuilds each affected record.
   */
  private List<MessageDto> withArtifactTitles(List<MessageDto> msgs) {
    List<String> artifactIds =
        msgs.stream().map(MessageDto::artifactId).filter(Objects::nonNull).toList();
    if (artifactIds.isEmpty()) {
      return msgs;
    }
    // titleById.get() may return null if an artifact was deleted after the message was persisted;
    // the artifactTitle field will be null for such orphaned references (acceptable).
    Map<String, String> titleById =
        artifacts.findAllById(artifactIds).stream()
            .collect(Collectors.toMap(Artifact::getId, Artifact::getTitle));
    return msgs.stream()
        .map(
            messageDto ->
                messageDto.artifactId() == null
                    ? messageDto
                    : new MessageDto(
                        messageDto.id(),
                        messageDto.sender(),
                        messageDto.text(),
                        messageDto.stepsJson(),
                        messageDto.artifactId(),
                        messageDto.createdAt(),
                        titleById.get(messageDto.artifactId()),
                        messageDto.questionsJson()))
        .toList();
  }
}
