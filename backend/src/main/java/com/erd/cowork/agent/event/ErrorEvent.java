package com.erd.cowork.agent.event;

public record ErrorEvent(String code, String message) implements AgentEvent {}
