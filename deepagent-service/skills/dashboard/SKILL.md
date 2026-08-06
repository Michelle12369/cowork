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
   - **These ids are code-only wiring**: they may appear ONLY inside `<script>` as
     `window.__ERD_RESULTS__` keys. NEVER render them in visible text -- no 「依 q1 計算」
     badges, no data-source footers listing `q1`/`q2`, no titles or labels containing them.
     When a card needs a source hint, describe the data in plain language
     (e.g. 「近 30 日明細」).
2. Plan the layout (see "Default layout" below).
3. Write the dashboard with `write_file`. **The path is fixed to `dashboard.html`** -- no other
   filename, and never into a subdirectory.
   - **Write the initial build in one pass**: once you've planned it, write out the complete
     dashboard.html with a **single** `write_file` call (the output limit is large enough to
     hold a full page). **NEVER** write a skeleton first and then fill it in with a series of
     small, separate write steps -- each small step is a full generation pass, so a few dozen
     steps later you're slow, expensive, and risk hitting the recursion limit.
4. Modifying an existing dashboard.html (the user asks to adjust an already-produced
   chart/layout, or a repair round reports quality-check errors):
   - **Small, localized changes** (retitle, recolor, fix one chart option) may use
     `edit_file`. **Rewrite the whole file with a single `write_file` call instead** when
     any of these holds: the turn needs more than 3 separate edits to dashboard.html, the
     change touches more than about one third of the file, or the layout is restructured
     (sections/charts added/removed/reordered). If an `edit_file` fails to find its old
     string, do NOT retry another edit -- read the file again and do one full `write_file`
     rewrite.
     Overwriting dashboard.html with `write_file` is allowed (dashboard.html and notes.md
     are the only overwritable files; `queries/*.sql`, `results/*.json`, `SOURCES.md` etc.
     remain create-only).
   - **Read the current version in one call first**: `read_file(file_path="dashboard.html",
     limit=1000)` to load the whole file at once. **NEVER** page-scan it with the default
     limit=100 (4-7 calls, each a full generation pass), and **NEVER** rewrite from memory
     without reading -- your memory of the file may differ from its actual content.
   - **Preserve everything the user didn't ask to change**: the rewrite must carry over all
     unchanged sections verbatim -- markup, chart configs, data references, styling. A rewrite
     that silently drops or alters unrelated charts is a defect.
   - **Self-check before writing**: in the version you are about to write, every variable and
     element id that is referenced must also be declared/present in that same version
     (especially `const xxx = window.__ERD_RESULTS__[...]` bindings and
     `getElementById('...')` targets). In a real browser `getElementById` returns `null` for
     a removed id and the immediate property access throws, killing the whole
     `DOMContentLoaded` handler and blanking every chart -- the guard's execution check
     reproduces exactly this, so a self-check up front saves a repair round.

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

`getCol` returning `-1` is a defensive fallback, not a state to design around -- it means the
column binding itself is wrong. If it happens, go back and fix the candidate names you're
passing (or which query result you're reading), don't render a `<p>Column not found</p>`
placeholder to paper over it.

- **A column that drives a JS branch (significant vs not, pass vs fail) MUST be a comparable
  number or boolean -- NEVER a label string JS re-parses.** A `significance` column returning
  `'p < 0.05 (顯著)'` / `'p >= 0.05 (不顯著)'` is a trap: `不顯著` (not significant) contains
  `顯著` (significant) as a substring, so `.includes('顯著')` matches both and silently
  mislabels every row, with no thrown error to catch it. Return `p_value`/`t_statistic`/
  `is_significant BOOLEAN` and branch on the number directly. Label columns are fine for a table
  cell or tooltip -- never for something an `if`/`.filter()`/`.includes()` branches on.

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
2. When the corresponding data is legitimately missing (e.g. an empty result set), that
   fragment must display "(data missing)" ("（資料缺失）") -- **NEVER** paper over it with
   `|| 0` or a default value, which would mislead the user into thinking 0 is a real measured
   value. A `getCol` returning `-1` is not this case -- it means the column binding is wrong;
   fix the binding, don't render a placeholder for it.
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
