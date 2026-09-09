package com.erd.cowork.agent.model;

/**
 * One resolved MCP connector wire spec, sent to deepagent's {@code /chat} endpoint so it can
 * connect to the MCP server directly (no static registry on the deepagent side any more). Resolved
 * from the Mongo-backed catalog by {@link
 * com.erd.cowork.service.ConnectorCatalogService#resolveSpecs} early in {@link
 * com.erd.cowork.agent.AgentOrchestrator#stream}, before the reactive webClient call — mirrors the
 * pre-async materialization pattern used for {@link AgentRequest#ssoToken()}.
 *
 * @param bearerTokenKey the key deepagent looks up in its {@code CONNECTOR_BEARER_TOKENS} dict to
 *     find this connector's service token; {@code null} means the connector needs no
 *     authentication. Declared plainly on the catalog entry (never a secret itself) — the actual
 *     token value never passes through Java.
 */
public record ConnectorSpec(String id, String name, String url, String bearerTokenKey) {}
