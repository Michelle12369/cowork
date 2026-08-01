package com.erd.cowork.agent.model;

import com.erd.cowork.parsing.model.FileProfile;

public record AgentFileContext(
    String alias, String name, String type, String storageKey, FileProfile profile) {}
