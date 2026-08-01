---
name: dashboard
description: Use when producing or modifying an HTML dashboard to support analysis
  conclusions. Teaches the dashboard file contract, default layout, chart selection, and
  ECharts rules; MUST be read before writing dashboard.html.
---

# Dashboard skill

## Workflow

1. Finish the analysis first with `run_sql` (or other analysis tools). The `tableId` returned
   by each successful query (e.g. `q1`, `q2`) is that result's key in `window.__ERD_RESULTS__`
   -- there is no separate numbering scheme.
2. Plan the layout (see "Default layout" below).
3. Write the dashboard with `write_file`. **The path is fixed to `dashboard.html`** -- no other
   filename, and never into a subdirectory.
   - **Write the initial build in one pass**: once you've planned it, write out the complete
     dashboard.html with a **single** `write_file` call (the output limit is large enough to
     hold a full page). **NEVER** write a skeleton first and then fill in charts with a series
     of `edit_file` calls -- each small step is a full generation pass, so a few dozen steps
     later you're slow, expensive, and risk hitting the recursion limit.
   - **No need to verify after writing**: once `write_file` reports success, it's written --
     **NEVER** `read_file` your own just-written content just to "confirm" it.
4. Modifying an existing dashboard.html (the user asks to adjust an already-produced
   chart/layout):
   - **Small edits** (adjust one chart, tweak a paragraph, change a color) -> use `edit_file`
     for the smallest possible localized change -- when the user only wants one thing changed,
     a full rewrite risks unintentionally touching other parts.
   - **Large refactors** (layout overhaul, charts rearranged entirely) -> you may `write_file`
     over `dashboard.html` directly (the system allows this specific file to be overwritten).
     **Only `dashboard.html` may be overwritten this way** -- every other file (`queries/*.sql`,
     `results/*.json`, `SOURCES.md`, etc.) can never be overwritten; once it exists, it can only
     be reached via `edit_file`.
   - **Locate before you edit -- don't read the whole page**: use `grep` to find the target
     block (heading text, an element `id`, a `<!-- section: ... -->` anchor), then `edit_file`
     directly.
   - **Tool-failure recovery -- change strategy, never replay the same call**:
     - `grep` returns "No matches found" → **NEVER** rerun the identical pattern (the same
       query returns the same result). Broaden stepwise: full phrase → one single keyword →
       an element id or section anchor. Two misses → stop grepping and read the file once
       (`limit=1000`) instead.
     - `edit_file` returns "String not found" → your memory of the file differs from its
       actual content. Immediately `grep` a short distinctive fragment of the target, then
       rewrite `old_string` from what is actually there. **NEVER** retry the same
       `old_string`; **NEVER** rerun SQL (query results have nothing to do with file
       contents). Prefer several short `old_string` edits over one long one.
   - **When you do need to see the content, read it all at once**: dashboard.html is your own
     working file, not an unfamiliar large file -- `read_file(file_path="dashboard.html",
     limit=1000)` to **read the whole thing in one call**. **NEVER** use the default limit=100
     to page-scan it (a single file would take 4-7 calls, each one a full generation pass --
     slow, expensive, and risks hitting the recursion limit). The tool's built-in advice of
     "scan structure with limit=100 first, then page through" only applies to unfamiliar large
     files; this workspace has no such file.
   - **No need to verify after editing**: once `edit_file` reports "Successfully replaced," the
     change has taken effect -- **NEVER** `read_file` it back just to confirm. What you should
     verify is dangling references (with `grep`, see below), not whether the edit itself landed.
   - **Self-check after changes**: after rewriting or deleting a block, you MUST `grep` to
     confirm no remaining references exist anywhere in the file to variables that were
     removed/renamed; for every variable newly-added code references, you MUST confirm its
     declaration still exists (especially `const xxx = window.__ERD_RESULTS__[...]` and
     `getElementById` element variables) -- a dangling reference will be rejected by the
     guard's execution check, and a self-check up front saves a repair round.
   - **The same self-check applies to element ids, not just variables**: after removing or
     rewriting a markup block (a KPI card, a chart container, a table), you MUST `grep` the
     `id` of every element you just deleted and confirm no `getElementById(...)`/
     `querySelector('#...')` call anywhere in the file still references it. A real incident:
     a repair round reshuffled the KPI cards, deleted one card's `<div id="kpi-...">`, but left
     the matching `document.getElementById('kpi-...').textContent = ...` in place -- in a real
     browser `getElementById` returns `null` for a ghost id, and the immediate `null.textContent`
     assignment throws, killing the whole `DOMContentLoaded` handler and blanking every chart on
     the page. The guard's execution check now reproduces this exact failure (it seeds its
     sandbox with the real element ids that exist in your HTML and returns `null` for anything
     else, matching real-browser semantics) -- but a self-check up front still saves a repair
     round.

## Data contract (one-page summary)

- Chart data can only come from `window.__ERD_RESULTS__["<tableId>"]`, with a fixed shape of
  `{ columns: string[], rows: unknown[][], truncated: boolean }`.
- `rows` is an "array of arrays," not an array of objects -- access by field index, not
  `row.columnName`.
- **NEVER** embed data values in the HTML (including sample rows you saw in the user's
  message), and **NEVER** compute statistics, aggregation, sorting, or filtering yourself in
  browser JS.
- Want a new aggregation, filter, or sort order? Go back and issue another `run_sql` to get an
  already-computed result -- don't compute it live inside the dashboard's `<script>`.
- Always resolve column indexes dynamically with `getCol`; **NEVER** call
  `columns.indexOf(...)` directly on a domain column (the actual column name may differ from
  what you expect in case/naming):

```js
function getCol(columns, ...candidates) {
  for (const c of candidates) { const i = columns.indexOf(c); if (i >= 0) return i; }
  console.warn('[ERD] column not found:', candidates); return -1;
}
// const valueIdx = getCol(columns, 'value', 'Value', 'measurement');
```

If `getCol` returns `-1`, that block should render `<p>Column not found</p>` -- don't force a
chart to render with empty/NaN data.

## Default layout

When the user hasn't specified a layout, follow this order:

1. **Insight card** (top of the page, below the banner and above the KPI card row) -- every
   dashboard needs at least one amber insight card. Placing it at the top is deliberate: the
   user should see the conclusion the instant they open the dashboard, not scan a row of
   numbers first before finding the point.
2. **KPI card row** -- key metrics, laid out side by side. Each card **MUST** display the
   actual value itself (e.g. `92.3%`, `1,204`), not just a metric name and unit label with no
   real number; values must always be computed in JS from `window.__ERD_RESULTS__` and filled
   in, never hardcoded.
3. **Primary chart + secondary chart** -- charts meant to be compared should be paired
   half-width, side by side; time series, SPC control charts, heatmaps, and detail tables should
   stay full-width (squeezing them to half-width makes them illegible).
4. **Detail table** (bottom) -- the complete source data.

If the user has stated their own layout requirements (e.g. "split into tabs," "put the chart
above the table"), always follow their instructions -- the above is only the default for when
they haven't said anything.

### Three ironclad rules for insight cards (each one caught a real violation, not a
hypothetical)

1. Every number in a sentence **MUST** be inserted via a live JS lookup from
   `window.__ERD_RESULTS__`, **NEVER** hardcoded as a literal string constant -- once the data
   changes in the next round, a hardcoded number becomes a lie.
2. When `getCol` returns `-1` or the corresponding data is missing, that fragment must display
   "(data missing)" ("（資料缺失）") -- **NEVER** paper over it with `|| 0` or a default value,
   which would mislead the user into thinking 0 is a real measured value.
3. Before writing any number into a sentence, you **MUST** verify that the field name it reads
   matches the semantic meaning of the sentence -- a real case caught a failure rate from the
   Search table being mistakenly inserted into a sentence about the Dashboard table (the field
   existed and the read succeeded, but the semantics were mismatched -- more dangerous than a
   plain missing value, because it looks completely normal).

## Where to look for details

- HTML skeleton, CDN whitelist, the `erd` theme, and the Tailwind classes for
  banner/tabs/KPI cards/insight cards -> read `references/html-contract.md`
- Chart type selection, color rules, number formatting, ECharts gotchas (dataZoom, legend,
  grid...) -> read `references/chart-rules.md`
- Complete, runnable examples (**recommended reading before your first dashboard -- copying the
  structure is fastest**) -> read `references/examples.md`
