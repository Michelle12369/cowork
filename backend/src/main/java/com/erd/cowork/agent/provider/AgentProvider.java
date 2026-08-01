package com.erd.cowork.agent.provider;

import com.erd.cowork.agent.model.AgentRequest;

/**
 * Root SPI implemented by every LLM-backed agent mode (dashboard generation, LangGraph analysis,
 * …). The seam drawn here is "does this mode need generation-time HTML repair", not "does it
 * produce an artifact". Modes whose LLM writes HTML directly implement {@link
 * DashboardAgentProvider} instead, which adds the repair hook.
 */
public interface AgentProvider {

  ProviderResult generate(AgentRequest request);
}
