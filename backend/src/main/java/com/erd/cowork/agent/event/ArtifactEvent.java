package com.erd.cowork.agent.event;

public record ArtifactEvent(String artifactId, String title) implements AgentEvent {}
