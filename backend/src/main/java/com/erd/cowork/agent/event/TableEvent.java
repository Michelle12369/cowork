package com.erd.cowork.agent.event;

import java.util.List;

/**
 * Event carrying one query-result table emitted by agent-service after a successful {@code
 * run_sql}/{@code trend_3sigma}/{@code flag_outliers} tool call.
 *
 * <p>{@code intent} is the model's one-sentence restatement of what the query does, letting a human
 * catch semantic errors at a glance. This event is live-only — it flows through the SSE stream but
 * is never persisted, so a reloaded history bubble cannot render referenced tables inline.
 *
 * @param tableId query-result id (e.g. {@code q1}), matching the {@code __ERD_RESULTS__["qN"]} key
 *     and the {@code [[table:qN]]} answer marker
 * @param intent one-sentence restatement of the query's purpose
 * @param columns column headers, in display order
 * @param rows row values, each inner list aligned with {@code columns} by index
 * @param truncated true when the underlying result set was cut off before being sent
 */
public record TableEvent(
    String tableId, String intent, List<String> columns, List<List<Object>> rows, boolean truncated)
    implements AgentEvent {}
