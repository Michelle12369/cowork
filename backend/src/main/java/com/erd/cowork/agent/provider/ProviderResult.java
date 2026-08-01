package com.erd.cowork.agent.provider;

import com.erd.cowork.agent.event.AgentEvent;
import com.erd.cowork.agent.model.AgentOutcome;
import java.util.function.Supplier;
import reactor.core.publisher.Flux;

public record ProviderResult(Flux<AgentEvent> events, Supplier<AgentOutcome> outcome) {}
