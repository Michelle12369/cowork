---
name: dashboard
description: Use when producing or modifying an HTML dashboard to support analysis
  conclusions. Single-file contract covering the dashboard file contract, default layout,
  chart selection, ECharts rules, and a runnable example; MUST be read before writing
  dashboard.html.
---

# Dashboard skill

Complete contract for building `dashboard.html` -- file/data contract, layout, chart
selection, ECharts rules, and a runnable example. No separate reference files.

## Workflow

1. Finish the analysis first with `run_sql`. Each successful query's `tableId` (`q1`, `q2`, …)
   is its key in `window.__ERD_RESULTS__` -- no separate numbering.
   - **These ids are code-only wiring**: they may appear ONLY inside `<script>` as
     `window.__ERD_RESULTS__` keys. NEVER put them in any visible text (badges, footers,
     titles, labels) -- describe the data in plain language (e.g. 「近 30 日明細」).
2. Plan the layout (see "Default layout").
3. Write the whole page with a **single `write_file` call**, path fixed to `dashboard.html`
   (no other name, no subdirectory). NEVER write a skeleton first and fill it in over several
   small writes -- each write is a full generation pass; a few dozen later you risk the
   recursion limit. MUST persist changes with write_file or edit_file after modifying dashboard!
4. Modifying an existing dashboard.html (user tweak, or a repair round):
   - **Small, targeted change -> `edit_file`**; large change or full restructure -> `write_file`
     (a single complete rewrite). For an edit_file, read the file first, then match a unique
     `old_string` (anchor on a `<!-- section: name -->` comment) and replace just that block.
     (dashboard.html and notes.md are overwritable; `queries/*.sql`, `results/*.json`,
     `SOURCES.md` are create-only.)
   - Read the current file in one call first: `read_file(file_path="dashboard.html",
     limit=1000)`. NEVER page-scan with the default limit=100, and NEVER rewrite from memory
     without reading. For a small edit, `grep` the `<!-- section: name -->` anchor to locate
     the block.
   - Preserve everything the user didn't ask to change -- carry unchanged sections over
     verbatim. Silently dropping/altering an unrelated chart is a defect.
   - Self-check before writing: every variable and element id you reference must be
     declared/present in the same version. `getElementById` returns `null` for a removed id and
     the immediate property access throws, blanking every chart -- the guard reproduces this.

## Data contract

- Chart data comes only from `window.__ERD_RESULTS__["<tableId>"]`, shape
  `{ columns: string[], rows: Record<string, unknown>[], truncated: boolean,
  total_row_count: number | null }`. `total_row_count` is the real row count when `truncated`
  is `true`, `null` when unknown or not truncated. `rows` is an
  array of objects keyed by column name -- access `row.column_name` (or `row["column name"]`
  when the name isn't a valid identifier). Accessing a column that doesn't exist -- including
  any numeric index -- **throws immediately** (deliberate: a binding typo explodes and enters
  the repair flow instead of shipping a silent NaN). NEVER guess or invent column names --
  copy them exactly from `get_schema` / the wiring manifest.
- NEVER embed data values in the HTML (including sample rows from the user's message), and
  NEVER compute statistics/aggregation/sorting/filtering in browser JS. Need a new
  aggregation/filter/sort? Issue another `run_sql` for an already-computed result.
- Declare a variable before reading `__ERD_RESULTS__`
  (`const summary = window.__ERD_RESULTS__["q1"];` then use `summary`). Accessing a property on
  an undeclared variable throws a `ReferenceError` that aborts the whole `<script>`, killing
  every chart after it -- not just that one.
- Declare variables shared across blocks at the **top level** of the `DOMContentLoaded`
  callback, before any if-block. Declaring inside one if-block and using it in another is legal
  syntax but throws `ReferenceError` at runtime (out of scope).
- A column that drives a JS branch (significant/not, pass/fail) MUST be a comparable number or
  boolean, NEVER a label string JS re-parses. Trap: `'不顯著'` contains `'顯著'` as a substring,
  so `.includes('顯著')` matches both and mislabels every row with no error. Return
  `p_value`/`is_significant BOOLEAN` and branch on the number. Label strings are fine only for
  a table cell/tooltip.

### Three ironclad rules for `__ERD_RESULTS__` access (the guard rejects all three)

1. **Literal access only.** Every access MUST be exactly `window.__ERD_RESULTS__["qN"]` or
   `window.__ERD_RESULTS__['qN']` -- no variable index, no template literal, no whitespace
   between `__ERD_RESULTS__` and `[`. Injection is a literal-scan whitelist: only ids written
   this way in the HTML source get put into `window.__ERD_RESULTS__` at runtime. Any other form
   (`__ERD_RESULTS__[tblId]`, `` __ERD_RESULTS__[`${id}`] ``) compiles fine but reads `undefined`
   in the browser and crashes every chart that depends on it.
2. **NEVER define, assign, or stub `__ERD_RESULTS__` yourself** -- no
   `window.__ERD_RESULTS__ = {...}`, no `const results = window.__ERD_RESULTS__;` aliasing, no
   `Object.keys(window.__ERD_RESULTS__)`. The object is injected by the system after your HTML
   ships; writing to it or capturing a reference to the whole object doesn't just fail to help,
   it silently overwrites or shadows the real injected data.
3. **Every referenced `qN` MUST come from an actual `run_sql` call's `tableId`** in this session
   -- never a guessed or remembered number from a previous turn. A reference to an id that was
   never produced is caught the same way as a malformed access: the guard rejects the write.

### Truncated results

- Charts and numeric insights MUST read SQL-aggregated results -- `GROUP BY` to the chart's
  granularity, one row per group -- NEVER aggregate raw detail rows in JS.
- Raw detail rows belong only in detail tables. When a result's `truncated` is `true`, the
  table MUST render a visible notice built from `total_row_count`, e.g. `僅顯示前 20,000 筆，
  共 ${total_row_count} 筆`. When computing anything from a truncated result is unavoidable,
  say so in the card.

## HTML contract

dashboard.html is a **single self-contained file** -- all CSS/JS inline or from the whitelisted
CDNs; it references no other local file.

### CDN whitelist (verbatim, NEVER change a character)

```html
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5"></script>
```

- NEVER `tailwindcss@2`, NEVER a `<link rel="stylesheet">` Tailwind, NEVER any other Tailwind URL.
- ECharts prefix may be `echarts@5`, `echarts@5.4.3`, … but **must start with**
  `https://cdn.jsdelivr.net/npm/echarts@` -- a wrong char/host/path gets rejected by the guard.
- Every other `<script>` must be inline (no `src`).

### ECharts theme -- 'erd' is injected by the system

```js
const chart = echarts.init(byId('chart-xxx'), 'erd');
```

- Every `echarts.init(...)` MUST pass second argument `'erd'`.
- NEVER call `echarts.registerTheme(...)` -- the `erd` theme is injected before your script runs.
- NEVER set `color` yourself in any option (series-level or top-level) -- the theme owns color.
  You only decide series count and which one is emphasized.

### resize handler (every chart needs one)

```js
window.addEventListener('resize', () => chart.resize());
```

Multiple charts → multiple `.resize()` calls. On tab switch also `window.dispatchEvent(new
Event('resize'))` (see Tabs).

### Chart containers need a fixed height

Every chart `<div>` needs a fixed height -- ECharts draws nothing in a zero-height container.
Use a Tailwind class (`h-72`, `h-80`) or inline `style="height:320px"`; **percentage heights
don't work** (parent height is usually undefined too → 0).

```html
<div id="chart-main" class="h-72"></div>
```

### Banner -- solid color, NEVER a gradient

```html
<header class="bg-slate-800 text-white px-8 py-5">
  <div class="max-w-7xl mx-auto flex items-end justify-between">
    <div>
      <div class="flex items-center gap-3">
        <span class="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-blue-600">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg>
        </span>
        <h1 class="text-2xl font-semibold tracking-tight">Dashboard 標題</h1>
      </div>
      <p class="text-slate-300 text-sm mt-1">副標題/資料說明</p>
    </div>
  </div>
</header>
```

### Icons/headings -- NEVER emoji

Headings, tab labels, card titles use an inline SVG line icon (`stroke="currentColor"
stroke-width="2"`) -- NEVER emoji.

### DOM ids -- complete literals only, looked up via `byId()`

Include this helper verbatim next to `fmt` and use it for every element lookup (raw
`document.getElementById` appears only inside the `showTab` boilerplate below):

```js
function byId(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error('[ERD] no element with id "' + id + '"');
  return el;
}
```

- Every id passed to `byId()` MUST be a complete string literal, character-identical to an
  `id="..."` in the markup. NEVER compose ids at runtime (`tablePrefix + '-head'`,
  `` `detail-${panel}` ``) -- a composed id that drifts from the markup dies as an unreadable
  null crash, and a composed id can defeat every static check.
- Shared render functions for similar panels take the **complete ids as parameters**, and the
  call sites list every id as a literal, side by side:

```js
function renderDetailTable({ headId, bodyId, result }) {
  const head = byId(headId);
  const body = byId(bodyId);
  // ...same rendering for both panels...
}
renderDetailTable({ headId: 'detail-table-head-p0', bodyId: 'detail-table-body-p0',
                    result: stageStatsAll });     // e.g. __ERD_RESULTS__["q5"]
renderDetailTable({ headId: 'detail-table-head-p1', bodyId: 'detail-table-body-p1',
                    result: stageStatsProd });    // e.g. __ERD_RESULTS__["q9"] (WHERE run_type='PROD')
```

  The paired call lines make a swapped p0/p1 id (or result) visible at a glance -- that
  adjacency is the only defense against cross-wiring two look-alike panels, which no runtime
  check can catch.

### Tabs (multiple panels)

Panels get `id="panel-0"`, `id="panel-1"`, …; every panel except the first also gets
`class="hidden"`. Use this markup and `showTab` verbatim (a styling deviation or missing resize
dispatch gets rejected):

```html
<nav class="w-full bg-white border-b border-slate-200 shadow-sm" role="tablist">
  <div class="max-w-7xl mx-auto px-8 flex gap-1">
    <button onclick="showTab(0)" id="tab-0" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-blue-600 text-slate-900 transition-all">
      Tab 1</button>
    <button onclick="showTab(1)" id="tab-1" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-800 transition-all">
      Tab 2</button>
  </div>
</nav>
```

```js
function showTab(idx) {
  document.querySelectorAll('[role=tab]').forEach((tabButton, index) => {
    tabButton.className = 'inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-all ' +
      (index === idx ? 'border-blue-600 text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-800');
    document.getElementById('panel-' + index).classList.toggle('hidden', index !== idx);
  });
  window.dispatchEvent(new Event('resize'));
}
```

`showTab` MUST be declared at **top level (global scope)** of the `<script>`, not inside
`DOMContentLoaded` -- an inline `onclick` only resolves a global name. Call `showTab(0)` once
inside `DOMContentLoaded` to activate the first tab. The resize dispatch is required or charts
mis-measure on tab switch.

### KPI cards -- white bg, semantic-color left edge, delta badge

Left-edge codes: `border-l-blue-500` (primary), `border-l-emerald-500` (good),
`border-l-amber-500` (warning), `border-l-rose-500` (bad).

```html
<div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-blue-500">
  <p class="text-xs text-slate-500 font-medium">指標名稱</p>
  <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-xxx"></p>
  <span class="inline-block mt-2 text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700">delta 文字</span>
</div>
```

### Insight card -- amber tone + lightbulb icon (at least one per dashboard)

```html
<div class="bg-amber-50 border border-amber-200 border-l-4 border-l-amber-400 rounded-lg p-4 text-sm text-amber-900">
  <span class="inline-flex items-center gap-1.5 font-semibold">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 1 4 10.5c-.6.5-1 1.2-1 2v.5H9v-.5c0-.8-.4-1.5-1-2A6 6 0 0 1 12 3z"/></svg>
    自動洞察：</span>
  [洞察文字——本文用 slate 色階,語意色只用在強調/badge/狀態,不要整段都塗色]
</div>
```

### Spacing -- spacing travels with the section

Every major block (insight card, KPI row, each chart card, detail table) owns its own `mb-6` on
its outermost element. Containers (`<main>`, a tab panel) NEVER use `space-y-*` for section
spacing -- `space-y` only affects direct children, so it silently breaks once a section is
wrapped in a tab panel. Grid spacing inside a section still uses `gap-4`. NEVER mix both (`mb-6`
+ outer `space-y` = double spacing).

### Section anchors

Precede every major block with an HTML comment anchor: `<!-- section: kpi -->`,
`<!-- section: insight -->`, `<!-- section: charts -->`, `<!-- section: detail-table -->`, …
Later edits `grep` this anchor to locate the block instead of reading the whole file.

### Other rules

- `<body>` carries only the background color (`class="bg-slate-50"`) -- NEVER put padding on it
  (`p-6`, `px-*`, `py-*`, …). The banner is full-width; page padding lives on `<main>`
  (`px-8 py-6`). Padding on the body insets the banner and looks wrong.
- All copy MUST be Traditional Chinese; technical terms (KPI, SPC, Cpk) may stay in English.
- NEVER `@apply` inside `<style>` -- the Tailwind CDN doesn't compile it. Use inline utilities.

## Default layout

When the user hasn't specified one, follow this order (if they stated their own, follow theirs):

1. **Insight card** at the top (below banner, above KPI row) -- at least one amber insight card;
   the user should see the conclusion the instant they open the page.
2. **KPI card row** -- each card MUST show the real value (`92.3%`, `1,204`), computed in JS
   from `__ERD_RESULTS__`, never a bare label, never hardcoded.
3. **Primary + secondary chart** -- charts meant to be compared pair half-width side by side;
   time series, SPC charts, heatmaps, and detail tables stay full-width (half-width is illegible).
4. **Detail table** (bottom) -- the complete source data.

### Four ironclad rules for insight cards (each caught a real violation)

1. Every number in a sentence MUST come from a live JS lookup on `__ERD_RESULTS__`, NEVER a
   hardcoded literal -- next round's data change makes a hardcoded number a lie.
2. Legitimately missing data (the column exists, the cell is `null`) → display "（資料缺失）",
   NEVER `|| 0` or a default (misleads the user that 0 is a real measurement). A wrong column
   name throwing is not this case -- fix the binding, don't catch-and-default it away.
3. Verify the field you read matches the sentence's meaning -- a real case inserted a failure
   rate from the Search table into a sentence about the Dashboard table (read succeeded, semantics
   wrong -- more dangerous than a missing value because it looks normal).
4. Dynamic text (insight sentences, tooltip formatters, labels) MUST be assembled with template
   literals (backticks + `${...}`), NEVER with quote-string `+` concatenation chains. Concatenation
   with CJK text is where whole-page kills happen: a raw newline inside a quoted chain, or a
   fullwidth `（` "closed" by an ASCII `)`, is a SyntaxError that discards the entire script block.
   Backtick strings tolerate newlines and eliminate the quote/paren bookkeeping.

## Chart selection

Color comes last: decide "what job is this data doing" → chart type → only then color.

| What the data is doing | Use | Don't use |
|---|---|---|
| A single current number (± trend) | KPI card (value + delta) | A one-bar bar chart |
| A handful of key numbers | KPI card row | Grouped bar chart |
| A ratio vs. a limit | Meter/gauge | A two-slice pie |
| More than ~7 meaningful categories | Table (or table + chart) | Cramming in more colors |
| Value comparison (high/low) | Bar/column; heatmap for a grid | -- |
| Trend (time series) | Line; area only for a single series | -- |
| Distinguishing between series | Grouped/stacked bar, multi-line | -- |
| One series is the point, rest is context | Emphasis (highlight one, gray the rest) | A fully-colored grouped chart |
| Above/below a baseline, delta-to-target | Diverging bar | -- |
| Part-to-whole | Stacked bar (horizontal if many/long names) | -- |
| Item-by-item "before -> after" | Dumbbell | -- |
| Detail data | Table | -- |

A single current value is a KPI card, not a lonely bar or a two-slice pie.

### Encoding's four roles (color = meaning, not decoration)

- **Sequential** (single hue, light→dark): magnitude, heatmaps/visualMap. NEVER a rainbow
  gradient, and NEVER a numeric gradient on unordered categories (products/teams/machines).
- **Categorical** (one color per series): only when the series identity is the point. NEVER
  reassign colors after filtering -- the user memorized "A is this color."
- **Diverging** (two hues + neutral gray midpoint): above/below a baseline. Midpoint reads as
  "nothing," extremes as opposites (one warm, one cool).
- **Emphasis**: when "this one is special," keep only it in the primary color and gray the rest
  with `#94a3b8`.

### Series-count ladder

- ≤3 series: just draw them. 4-6: **must** add direct labels. >6: fold the tail into "other" or
  use small multiples -- NEVER invent extra colors or cycle the palette (a 9th color is
  indistinguishable from an earlier one).

### Hard NOs (each a real failure)

- **At most two Y axes.** With a second axis you MUST: (1) label both axes' units
  (`axisLabel.formatter`), (2) put the `yAxisIndex`-paired series name in the legend, (3) show
  both axes' values+units in the tooltip. Series sharing a unit share one axis.
- **NEVER a pie/donut to compare close values** -- only for at-a-glance proportion, ≤6 slices.
- **Gridlines/axes are solid thin lines** slightly darker than the background -- NEVER dashed
  (reads as forecast/threshold).
- **NEVER label every data point** -- label selectively (endpoints, extremes, the key series);
  a tooltip is a bonus, NEVER the only way to read a value.

## Number formatting

- `.toFixed()` / `.toLocaleString()` may appear ONLY inside the `fmt`/`fmtP` definitions, NEVER
  at a call site. Two crashes this prevents: (1) `__ERD_RESULTS__` cells aren't all numbers
  (ISO date strings, VARCHAR numerics, `null`) -- `cell.toFixed(2)` throws; `fmt()` coerces with
  `Number(v)` and falls back to the raw string. (2) ECharts `label.formatter`/`tooltip.formatter`
  receive a **params object**, not a number -- write `formatter: params => fmt(params.value)`,
  or `valueFormatter: v => fmt(v)` (which does receive the value).
- Use thousands separators (`1,234`).
- Pick precision from magnitude, not a blanket `toFixed(2)` (a slope `0.000745858` → `0.00`
  reads as "no effect"). Integers get no decimals. One shared helper:
  ```js
  const fmt = v => {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    if (Number.isInteger(n)) return n.toLocaleString();
    const abs = Math.abs(n);
    return (abs !== 0 && abs < 0.01) ? n.toPrecision(3) : n.toFixed(2);
  };
  ```
- p-values go through `fmtP`, never `fmt` (`toFixed(2)` collapses `0.0003` → `0.00`):
  ```js
  const fmtP = p => p < 0.001 ? 'p < 0.001' : 'p = ' + Number(p).toExponential(2);
  ```
- If there's a unit, show it on both axis ticks and tooltip: axis
  `axisLabel: { formatter: '{value} ms' }`; tooltip `valueFormatter: v => fmt(v) + ' ms'`. Infer
  the unit from the column name (`*_ms`→ms, `*_min`→min, `*_pct`/`*rate`→%, SPC `unit` column);
  if you can't infer one, don't force it.
- Every numeric cell in the detail table also goes through `fmt()` (or `fmtP()`) -- the common
  break is dumping raw rows into `innerHTML` with a long decimal tail.
- User-specified precision → change it **inside `fmt`** (keep the `Number(v)` coercion and
  `isFinite` fallback, swap `toFixed(2)` for `toFixed(N)`); still never a bare `toFixed(N)` at
  a call site.

## ECharts implementation gotchas (guard-rejected or visibly broken)

- A new chart container MUST come with its `echarts.init` + `setOption`; a new tab MUST come
  with the switch logic (`showTab`/`onclick`/init inside `DOMContentLoaded`). A bare `<div>`/
  `<button>` skeleton ships an empty shell -- unfinished, not "fill in later."
- Every chart's option MUST set `tooltip` (the guard rejects an `echarts.init` with no `tooltip`
  anywhere). Bar/line/scatter: `tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }`;
  pie: `tooltip: { trigger: 'item' }`.
- Every Cartesian chart needs `grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true }`
  (`containLabel` keeps axis labels from clipping).
- A chart with a legend needs `legend: { top: 5 }` (a number, NEVER `'X%'`) and `grid.top` ≥ 48px
  (e.g. `'50px'`) -- set both together, or the legend covers the chart.
- A time series/control chart with >30 points must add a slider dataZoom and set `grid.bottom`
  to `60` (a number):
  ```js
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 5 }],
  grid: { left: '3%', right: '4%', bottom: 60, containLabel: true }
  ```
- A date/time axis MUST never show raw ISO timestamps like `2026-06-01T00:00:00` -- they
  overflow and overlap. Mandatory truncation:
  - `type: 'category'` (date strings): `axisLabel: { formatter: v => String(v).substring(0, 10) }`
    (add `rotate: 30` if crowded).
  - `type: 'time'` (formatter receives a ms number): NEVER `.substring()` on it (throws, blanks
    the chart) -- use `formatter: value => echarts.format.formatTime('MM-dd hh:mm', value)`.
- `xAxis.type: 'time'` needs `series.data` as `[timeValue, y]` pairs, not a bare y-array (a bare
  array silently squashes the line): `data: rows.map(r => [String(r.dateColumn), Number(r.valueColumn)])`.
- A `label.formatter`'s raw number is `params.value`, not `params.data` (object-form points make
  `params.data` the whole object → `fmt` prints "NaN"). Always `formatter: params => fmt(params.value)`.
- NEVER seed a running max/min compared via `Math.abs()` with `Infinity` (`Math.abs(-Infinity)`
  is still `Infinity`, so it never wins and ships `-Infinity`). Seed from the first real row.
- NEVER use ECharts' `title` option -- it overlaps the legend and duplicates the card heading (a
  real case shipped the title twice, the second on top of the legend). Put the title in an HTML
  heading outside the container: `<h3 class="text-sm font-semibold text-slate-700 mb-3">圖表標題</h3>`.
- When the value range sits far from 0 (e.g. 420-450), set `yAxis.min`/`max` to hug the data
  (`min: Math.floor(dataMin - margin)`) or the line squashes into a band. Mandatory for SPC charts.
- `markLine` labels (UCL/CL/LCL) need `label: { position: 'insideEndTop' }` or they clip.
- Each `markArea.data` element MUST be a two-object `[start, end]` pair
  (`data: [[{ yAxis: 0 }, { yAxis: 80 }]]`). NEVER a single object with duplicate keys
  (`[[{ yAxis: 0, yAxis: 80 }]]`) -- the second key silently overwrites the first and ECharts
  throws `undefined (reading 'coord')`, crashing the whole `setOption` and every later chart.
- Every chart's init+setOption MUST be in its own try/catch with
  `console.error('[ERD] chart <name> failed:', error)`, and the catch MUST re-throw
  asynchronously (`setTimeout(() => { throw error; }, 0)`). One chart's error should sacrifice
  only that chart; the async rethrow still surfaces it to `window.onerror` (which drives the
  repair flow) without aborting the synchronous script. A plain `console.error` with no rethrow
  swallows the error and the broken chart ships as-is.


### complete example
A complete, directly-renderable dashboard.html covering the recurring shapes: tabs, a bar
chart, a donut share chart (legend on top, labels with name/count/percent), a smooth area
time-series trend (visible point symbols, named yAxis, dataZoom), a control chart (target/UCL/
LCL as dashed constant series named in a top legend, limits from SQL-computed columns, hugging
yAxis), and a detail table. **Copy the structure, swap in your own analysis** -- title, column
candidate strings, copy, and the tableIds (`q1`/`q2`/…) all become yours; on-page copy stays
Traditional Chinese. Tabs: the `<nav>` sits between the banner and `<main>`, each angle wrapped
in `<div id="panel-N">` (every panel except the first gets `class="hidden"`), `showTab` at top
level, and a `showTab(0)` call at the end of `DOMContentLoaded`. A chart inside a hidden panel
initializes at size 0 -- the `resize` dispatch inside `showTab` is what re-measures it when its
tab opens.

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>產線良率多角度分析</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5"></script>
</head>
<body class="bg-slate-50">

<header class="bg-slate-800 text-white px-8 py-5">
  <div class="max-w-7xl mx-auto flex items-end justify-between">
    <div>
      <div class="flex items-center gap-3">
        <span class="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-blue-600">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg>
        </span>
        <h1 class="text-2xl font-semibold tracking-tight">產線良率多角度分析</h1>
      </div>
      <p class="text-slate-300 text-sm mt-1">依產線與日期的良率統計</p>
    </div>
  </div>
</header>

<nav class="w-full bg-white border-b border-slate-200 shadow-sm" role="tablist">
  <div class="max-w-7xl mx-auto px-8 flex gap-1">
    <button onclick="showTab(0)" id="tab-0" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-blue-600 text-slate-900 transition-all">產線總覽</button>
    <button onclick="showTab(1)" id="tab-1" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-800 transition-all">趨勢分析</button>
    <button onclick="showTab(2)" id="tab-2" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-800 transition-all">明細資料</button>
  </div>
</nav>

<main class="max-w-7xl mx-auto px-8 py-6">

  <div id="panel-0">
    <!-- section: insight -->
    <div class="bg-amber-50 border border-amber-200 border-l-4 border-l-amber-400 rounded-lg p-4 text-sm text-amber-900 mb-6">
      <span class="inline-flex items-center gap-1.5 font-semibold">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 1 4 10.5c-.6.5-1 1.2-1 2v.5H9v-.5c0-.8-.4-1.5-1-2A6 6 0 0 1 12 3z"/></svg>
        自動洞察：</span>
      <span id="insight-text"></span>
    </div>
    <!-- section: charts -->
    <section class="grid grid-cols-2 gap-4 mb-6">
      <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
        <h3 class="text-sm font-semibold text-slate-700 mb-3">各產線良率(%)</h3>
        <div id="chart-yield-by-line" class="h-72"></div>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
        <h3 class="text-sm font-semibold text-slate-700 mb-3">不良類型佔比</h3>
        <div id="chart-defect-share" class="h-72"></div>
      </div>
    </section>
  </div>

  <div id="panel-1" class="hidden">
    <!-- section: monthly-trend -->
    <section class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
      <h3 class="text-sm font-semibold text-slate-700 mb-3">月度產出趨勢</h3>
      <div id="chart-monthly-output" class="h-72"></div>
    </section>
    <!-- section: control-chart -->
    <section class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
      <h3 class="text-sm font-semibold text-slate-700 mb-3">每日良率趨勢與控制圖(%)</h3>
      <div id="chart-yield-trend" class="h-72"></div>
    </section>
  </div>

  <div id="panel-2" class="hidden">
    <!-- section: detail-table -->
    <section class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
      <h3 class="text-sm font-semibold text-slate-700 mb-3">明細資料</h3>
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm text-left">
          <thead id="detail-table-head" class="text-slate-500 border-b border-slate-200"></thead>
          <tbody id="detail-table-body" class="divide-y divide-slate-100"></tbody>
        </table>
      </div>
    </section>
  </div>

</main>

<script>
// 數字格式:唯一的 toFixed 出現點——cell 可能是字串/null,先 Number() 強轉,轉不動原樣顯示;
// 極小值走有效位數,免得 0.0007 顯示成 0.00。
const fmt = v => {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (Number.isInteger(n)) return n.toLocaleString();
  const abs = Math.abs(n);
  return (abs !== 0 && abs < 0.01) ? n.toPrecision(3) : n.toFixed(2);
};

function byId(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error('[ERD] no element with id "' + id + '"');
  return el;
}

// showTab MUST 在 top level(inline onclick 只解析全域名稱);resize dispatch 讓隱藏分頁裡
// 以 0 尺寸初始化的圖表在分頁打開時重新量測。
function showTab(idx) {
  document.querySelectorAll('[role=tab]').forEach((tabButton, index) => {
    tabButton.className = 'inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-all ' +
      (index === idx ? 'border-blue-600 text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-800');
    document.getElementById('panel-' + index).classList.toggle('hidden', index !== idx);
  });
  window.dispatchEvent(new Event('resize'));
}

document.addEventListener('DOMContentLoaded', () => {
  const summary = window.__ERD_RESULTS__['q1'];
  const defectShare = window.__ERD_RESULTS__['q2'];
  const trend = window.__ERD_RESULTS__['q3'];
  const monthly = window.__ERD_RESULTS__['q4'];
  const detail = window.__ERD_RESULTS__['q5'];

  // 欄名綁錯(含打錯字)在 rows 的 Proxy 上直接 throw,交給下面的 try/catch 接住 -- 不再有
  // -1 崗哨分支。
  try {
    const lines = summary.rows.map(r => String(r.production_line));
    const yields = summary.rows.map(r => Number(r.yield_rate));

    const avg = yields.reduce((a, b) => a + b, 0) / yields.length;
    const worstIndex = yields.indexOf(Math.min(...yields));
    byId('insight-text').textContent =
      lines[worstIndex] + ' 良率為 ' + fmt(yields[worstIndex]) + '%,低於平均 ' + fmt(avg) + '%,建議優先排查。';

    const yieldChart = echarts.init(byId('chart-yield-by-line'), 'erd');
    yieldChart.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: v => fmt(v) + '%' },
      grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
      xAxis: { type: 'category', data: lines },
      yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
      series: [{ type: 'bar', data: yields }]
    });
    window.addEventListener('resize', () => yieldChart.resize());
  } catch (error) {
    console.error('[ERD] chart yield-by-line failed:', error);
    setTimeout(() => { throw error; }, 0);
  }

  // 圓餅只用於一眼看佔比(≤6 片):legend 置頂,label 帶名稱+數量+百分比;顏色一律交給
  // erd theme,不自己設。
  try {
    const shareChart = echarts.init(byId('chart-defect-share'), 'erd');
    shareChart.setOption({
      tooltip: { trigger: 'item' },
      legend: { top: 5 },
      series: [{
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['50%', '56%'],
        label: { formatter: '{b}: {c} ({d}%)' },
        data: defectShare.rows.map(r => ({ name: String(r.defect_type), value: Number(r.cnt) }))
      }]
    });
    window.addEventListener('resize', () => shareChart.resize());
  } catch (error) {
    console.error('[ERD] chart defect-share failed:', error);
    setTimeout(() => { throw error; }, 0);
  }

  // 月趨勢(少量點):smooth+areaStyle(面積只給單一 series)+顯示資料點;數量類指標的
  // 面積圖從 0 起算,yAxis 給軸名。
  try {
    const monthlyChart = echarts.init(byId('chart-monthly-output'), 'erd');
    monthlyChart.setOption({
      tooltip: { trigger: 'axis', valueFormatter: v => fmt(v) + ' 件' },
      grid: { left: '3%', right: '4%', bottom: 60, containLabel: true },
      xAxis: { type: 'category', data: monthly.rows.map(r => String(r.stat_month)), axisLabel: { rotate: 30 } },
      yAxis: { type: 'value', name: '產出 (件)', axisLabel: { formatter: value => fmt(value) } },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 5 }],
      series: [{ type: 'line', smooth: true, data: monthly.rows.map(r => Number(r.output_qty)), areaStyle: { opacity: 0.15 } }]
    });
    window.addEventListener('resize', () => monthlyChart.resize());
  } catch (error) {
    console.error('[ERD] chart monthly-output failed:', error);
    setTimeout(() => { throw error; }, 0);
  }

  try {
    // 時間軸三件套:series 用 [timeValue, y] 對、軸標籤截短、>30 點加 dataZoom。
    // 控制限是 SQL 算好的常數欄(每列同值),取第一列即可——統計不在瀏覽器算。
    const points = trend.rows.map(r => [String(r.measure_date), Number(r.daily_yield)]);
    const values = points.map(p => p[1]);
    const target = Number(trend.rows[0].target_value);
    const ucl = Number(trend.rows[0].ucl);
    const lcl = Number(trend.rows[0].lcl);
    // yAxis 貼住資料與控制限(SPC 必做),不從 0 起算,免得線被壓成一條帶。
    const dataMin = Math.min(...values, lcl), dataMax = Math.max(...values, ucl);
    const margin = (dataMax - dataMin) * 0.1 || 1;
    // 控制線各自成獨立常數 series 才會出現在頂端 legend(markLine 不進 legend);
    // 首末兩點就能畫滿整條;虛線=門檻語意,legend 有了就要 grid.top >= 48。
    const firstTime = String(trend.rows[0].measure_date);
    const lastTime = String(trend.rows[trend.rows.length - 1].measure_date);
    const limitLine = (value) => ({
      type: 'line', showSymbol: false, lineStyle: { type: 'dashed' },
      data: [[firstTime, value], [lastTime, value]]
    });

    const trendChart = echarts.init(byId('chart-yield-trend'), 'erd');
    trendChart.setOption({
      tooltip: { trigger: 'axis', valueFormatter: v => fmt(v) + '%' },
      legend: { top: 5 },
      grid: { left: '3%', right: '4%', top: '50px', bottom: 60, containLabel: true },
      xAxis: { type: 'time', axisLabel: { formatter: value => echarts.format.formatTime('MM-dd', value) } },
      yAxis: { type: 'value', min: Math.floor(dataMin - margin), max: Math.ceil(dataMax + margin), axisLabel: { formatter: '{value}%' } },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 5 }],
      series: [
        { name: '良率趨勢', type: 'line', data: points, showSymbol: false },
        { name: '目標線', ...limitLine(target) },
        { name: 'UCL', ...limitLine(ucl) },
        { name: 'LCL', ...limitLine(lcl) }
      ]
    });
    window.addEventListener('resize', () => trendChart.resize());
  } catch (error) {
    console.error('[ERD] chart yield-trend failed:', error);
    setTimeout(() => { throw error; }, 0);
  }

  const detailHeadRow = byId('detail-table-head');
  const detailBody = byId('detail-table-body');
  detailHeadRow.innerHTML = '<tr>' + detail.columns.map(c => '<th class="py-2 pr-4">' + c + '</th>').join('') + '</tr>';
  // null 直接 fmt 會變成 0(Number(null)=0)——缺值顯示空白,不顯示假的 0。row 是物件不是陣列
  // ——逐欄名(而非逐 cell)取值,`.map` 是對 detail.columns 做,不是對 row 做。
  detailBody.innerHTML = detail.rows.map(row =>
    '<tr>' + detail.columns.map(columnName => '<td class="py-2 pr-4">' + (row[columnName] == null ? '' : fmt(row[columnName])) + '</td>').join('') + '</tr>'
  ).join('');

  showTab(0);
});
</script>
</body>
</html>
```
