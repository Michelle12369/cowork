# Asking questions about the numbers on a connector dashboard — brainstorming notes (undecided)

> Status: **notes, not a spec.** Captured from a 2026-09-10 discussion; has not gone through the brainstorming process, has no plan. Purpose is to preserve the two comparison tables and the minimal design until the D9 transport layer (`docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md` §7) has landed, then revisit. Where anything here conflicts with the main spec, the main spec wins.
>
> Premise: a connector-mode dashboard fetches its data through `mcp()` at view time, so the numbers are computed in the viewer's browser. The model, running on the server, has never seen them. Handing the model the HTML+JS tells it how a number is computed, not what it is.

## 1. The problem

A user looks at the dashboard and asks "why is this 43%". The model has the HTML+JS and the conversation history; the displayed values live in browser variables, ECharts options, and DOM text. Either the current view has to be carried back to the model, or the model has to recompute.

## 2. Two routes

**A. Capture a snapshot in the browser and send it with the question.** The frontend already sends `previousDashboardHtml` with every turn; add a `dashboardSnapshot` next to it, produced by the injected runtime on request (`erd-snapshot-request` → `erd-snapshot`).

**B. Recompute on the server.** The model already has the call record; it can call the connector tool again with the same args, land the table, and `run_sql` for the exact figure (main spec D6: qN results are for conversation answers). Exact, but it can diverge from what the user sees, since the viewer's identity, the current control state, and the point in time all differ. "The screen says 43%, the model says 41%" is worse than no answer.

Recommendation: use both, in this order of trust. Answer "what is on screen right now" from the snapshot, which also tells the model the control state. Only for a number not on screen, re-query the connector, and say so.

## 3. Snapshot capture methods compared

| Method | How | Yields | Limits |
|---|---|---|---|
| `console.log` relay | runtime overrides `console.log` in the iframe and posts lines to the parent | whatever the model chose to print | unstructured, noisy, depends on the model printing the right things, no control-state context. **Not recommended** |
| DOM text | `document.body.innerText`, trimmed | every KPI tile, table cell, title, and label the user can read | chart series are invisible (canvas); numbers are formatted strings ("1.2M") |
| ECharts option export | enumerate `[_echarts_instance_]` elements, call `getOption()`, keep `title`, `xAxis.data`, `series[].name`, `series[].data` | the exact plotted values per chart, no model cooperation | can be large; series that use `dataset` need a fallback |
| Control state | serialise `select` / `input` values | the region, window, and filters the user has applied | needs stable `id`s (the skill already requires them) |
| Explicit view object | skill convention: the dashboard writes derived numbers to `window.__ERD_VIEW__ = {kpis:{...}, series:{...}}`; the runtime includes it | named, structured numbers, matching how the model itself reasons | relies on the model doing it; `check_dashboard` should warn when absent |

Suggested shipping combination: control state + DOM text + ECharts option export as the baseline (works on any dashboard, zero model cooperation), plus the `__ERD_VIEW__` convention so dashboards that follow it get sharper answers. The snapshot enters conversation history, so cap it (suggest 200 KB, truncate series).

## 4. Edit mode vs. view mode

Requirement (2026-09-10): both modes must support asking about the numbers. View mode can only filter and look; it cannot change the dashboard. Edit mode can do both.

**Same in both modes:** the injected runtime and the `mcp()` round trip (filtering in view mode is just controls firing new `mcp()` calls, which the existing mechanism covers); snapshot capture (the runtime neither knows nor needs to know the mode); the model's input is dashboard HTML + snapshot + question.

**Different:**

| | Edit mode (author) | View mode (viewer) |
|---|---|---|
| Who | session owner | anyone the artifact is shared with; the access rule is "can view", not "owns" |
| Entry point | existing `/chat` turn, request carries `dashboardSnapshot` in addition | new endpoint `POST /api/artifacts/{id}/ask`, forwarded to deepagent with `mode: "ask"` |
| Model may | answer, or edit and emit a new dashboard version | answer only. No `write_file` / `edit_file`, no `DASHBOARD_HTML` event, no dashboard skill gate |
| Model may re-query connectors | yes, as today | yes, but as the viewer: the viewer's SSO goes on the request, connectors come from the artifact's session, the call budget applies |
| Which dashboard | latest version in the session | the exact published version the viewer has open (`artifactId` pins it) |
| Conversation state | session checkpoint, as today | none server-side in v1: the frontend sends the last few Q&A turns with each ask (same pattern as `previousDashboardHtml`); nothing is written to the author's session |
| Frontend surface | `ArtifactPanel` on the chat page | `ArtifactFullscreenPage` (or the share route) gains a question box that does not interfere with filtering; also needs the bridge hook and snapshot capture |
| Data in the prompt | the snapshot is the author's view | the snapshot is the viewer's view, which may differ from the author's due to permissions; answers must not flow back into the author's session |

## 5. Minimal design

1. deepagent chat request gains `mode: "edit" | "ask"` and `dashboardSnapshot`. In `ask` mode the agent gets connector tools and `run_sql` only, no write tools, no skill gate, and one extra system sentence: answer from the snapshot first; re-query the connector only for a number not on screen, and say that you did.
2. Java: `POST /api/artifacts/{id}/ask`, body `{question, snapshot, priorTurns[]}`. Access rule identical to `GET /api/artifacts/{id}`. Resolves connectors from the artifact's session, forwards the viewer's SSO, streams the answer. Stateless.
3. Frontend: one snapshot hook shared by both surfaces. Edit mode attaches the snapshot to the existing send; view mode posts to the ask endpoint and keeps `priorTurns` in component state.
4. Skill: add the `__ERD_VIEW__` convention; `check_dashboard` warns when it is absent (does not reject).

## 6. Decisions this forces (open)

- View-mode Q&A is the first feature that runs the model on behalf of a non-owner. Every ask can trigger connector calls, so per-viewer cost and abuse limits are a product decision.
- Snapshots put displayed values into the model prompt. In view mode those are the viewer's own permitted data, and stateless asks persist nothing, which is the safer default. Persisting viewer conversations later is a separate decision.
- Depends on the D9 transport layer landing first (runtime, `ArtifactPanel` bridge, Java `/mcp-call`, deepagent `/tool-call`). The snapshot messages (`erd-snapshot-request` / `erd-snapshot`) should be named and frozen together with D9's message set.
- Whether share-page viewers can open connector dashboards at all is still an open product decision (main spec §11). Without it, view-mode Q&A has no subject.
