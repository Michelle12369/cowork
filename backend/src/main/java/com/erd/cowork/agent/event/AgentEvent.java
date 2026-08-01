package com.erd.cowork.agent.event;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, include = JsonTypeInfo.As.PROPERTY, property = "type")
@JsonSubTypes({
  @JsonSubTypes.Type(value = StepEvent.class, name = "STEP"),
  @JsonSubTypes.Type(value = TokenEvent.class, name = "TOKEN"),
  @JsonSubTypes.Type(value = AnswerEvent.class, name = "ANSWER"),
  @JsonSubTypes.Type(value = ArtifactEvent.class, name = "ARTIFACT"),
  @JsonSubTypes.Type(value = ErrorEvent.class, name = "ERROR"),
  @JsonSubTypes.Type(value = QuestionEvent.class, name = "QUESTION"),
  @JsonSubTypes.Type(value = ThinkingEvent.class, name = "THINKING"),
  @JsonSubTypes.Type(value = CodeEvent.class, name = "CODE"),
  @JsonSubTypes.Type(value = TableEvent.class, name = "TABLE")
})
public sealed interface AgentEvent
    permits StepEvent,
        TokenEvent,
        AnswerEvent,
        ArtifactEvent,
        ErrorEvent,
        QuestionEvent,
        ThinkingEvent,
        CodeEvent,
        TableEvent {}
