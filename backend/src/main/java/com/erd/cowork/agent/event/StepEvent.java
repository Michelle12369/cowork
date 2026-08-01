package com.erd.cowork.agent.event;

public record StepEvent(String stepKey, String title, String description, StepStatus status)
    implements AgentEvent {}
