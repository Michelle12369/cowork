package com.erd.cowork.agent.event;

import java.util.List;

/**
 * Event carrying one query-result table emitted by agent-service after a successful {@code
 * run_sql}/{@code trend_3sigma}/{@code flag_outliers} tool call.
 *
 * <p>{@code intent} is the model's one-sentence restatement of what the query does, letting a human
 * catch semantic errors at a glance. Live-only: the orchestrator never persists this event, so
 * history reload shows only the text answer.
 *
 * @param tableId identifier for the table, scoped to one analysis run (e.g. {@code tbl_<runid>})
 * @param intent one-sentence restatement of the query's purpose
 * @param columns column headers, in display order
 * @param rows row values, each inner list aligned with {@code columns} by index
 * @param truncated true when the underlying result set was cut off before being sent
 */
public record TableEvent(
    String tableId, String intent, List<String> columns, List<List<Object>> rows, boolean truncated)
    implements AgentEvent {}
