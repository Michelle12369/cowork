package com.erd.cowork.agent.event;

import com.erd.cowork.agent.model.ClarifyingQuestion;
import java.util.List;

/**
 * Event emitted when the LLM requests clarification before generating a dashboard.
 *
 * <p>This event is emitted by the orchestrator after the provider stream completes, when {@link
 * com.erd.cowork.agent.model.AgentOutcome#questions()} contains at least one question.
 *
 * @param questions the clarification questions parsed from the {@code ```questions} fenced block
 */
public record QuestionEvent(List<ClarifyingQuestion> questions) implements AgentEvent {}
