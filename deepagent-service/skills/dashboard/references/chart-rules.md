# Chart rules

Color comes last. First decide "what job is this data doing," then pick the chart type, and
only then think about color -- most ugly or misleading charts got that way because someone did
it in the reverse order.

## Chart-selection table -- start by asking what job the data is doing

| What the data is doing | Use | Don't use |
|---|---|---|
| A single current number (± trend) | KPI card (value + delta) | A one-bar bar chart |
| A handful of key numbers | KPI card row | Grouped bar chart |
| A ratio vs. a limit | Meter/gauge | A two-slice pie |
| More than ~7 meaningful categories | Table (or table + chart) | Cramming in more colors |
| Value comparison (high/low) | Bar/column; heatmap for a grid | -- |
| Trend (time series) | Line; area only for a single series | -- |
| Distinguishing between series | Grouped/stacked bar, multi-line | -- |
| One series is the point, the rest is context | Emphasis (highlight one, gray out the rest) | A fully-colored grouped chart |
| Above/below a baseline, delta-to-target | Diverging bar | -- |
| Part-to-whole | Stacked bar (horizontal if many/long category names) | -- |
| Item-by-item "before -> after" | Dumbbell | -- |
| Detail data | Table | -- |

**Sometimes the right answer isn't a chart, it's a number card.** A single current value
doesn't need a lonely bar, and it doesn't need a two-slice pie either -- a KPI card is both more
honest and easier to read.

## Encoding's four roles (color's meaning, not decoration)

- **Sequential** (single hue, light -> dark): the default choice for magnitude, used by
  heatmaps/visualMap. **NEVER** use a rainbow gradient, and **NEVER** apply a numeric gradient
  to unordered categories (products, teams, machines) -- that's double-encoding, since the
  category itself has no inherent size ordering.
- **Categorical** (one color per series): only use this when "the identity of the series
  itself" is the point. Color follows the entity, and **NEVER** reassign colors to the remaining
  series after filtering -- the user has already memorized "A is this color," and having it
  change color after a filter is misleading.
- **Diverging** (two hues + a neutral gray midpoint): above/below a baseline, positive/negative
  offsets. The midpoint must read as "nothing," and the two extremes must read as "opposites"
  (one warm, one cool).
- **Emphasis** (often overlooked, but often correct): when the story is "this one is special,"
  keep only that one in the primary color and gray out everything else with `#94a3b8`. Far more
  honest than a fully-colored grouped chart.

### The series-count ladder

- <=3 series: just draw them, color alone is clear enough.
- 4-6 series: you **must** add direct labels -- color alone is no longer enough to tell them
  apart.
- \>6 series: fold the long tail into "other," or split into small multiples. **NEVER** invent
  extra colors or cycle back through the theme's palette -- a 9th color will always be
  indistinguishable from one of the earlier ones.

## Hard NOs (each one is a real failure mode that actually happened)

- **A chart gets at most two Y axes** -- three or more is absolutely forbidden. Whenever you use
  a second axis, you MUST: (1) label both axes with their unit
  (`axisLabel.formatter`), (2) make the series-to-axis mapping obvious at a glance (write the
  `yAxisIndex`-paired series name into the legend), (3) have the tooltip show both axes' values
  and units at once. Series sharing the same unit should share one axis -- don't open a second
  axis just for symmetry.
- **NEVER use a pie/donut to compare close values** -- part-to-whole charts are only good for an
  at-a-glance sense of proportion, and only with <=6 slices; when values are close together,
  slice sizes become indistinguishable.
- **Bars always start at 0** -- **NEVER** truncate a bar chart's axis origin, that exaggerates
  differences.
- **Gridlines/axes are always a solid, thin line, slightly darker than the background** --
  **NEVER** use a dashed gridline; a dashed line reads as "a forecast" or "a threshold" to
  viewers.
- **NEVER** label every single data point with its number -- label selectively (endpoints,
  extremes, the one series that actually matters) and let the axis and tooltip carry the rest.
  A tooltip should only ever be a "bonus" -- **NEVER** let it be the only way to read a value.

## Number formatting

- **`.toFixed()` / `.toLocaleString()` may appear ONLY inside the `fmt`/`fmtP` definitions --
  NEVER call them directly anywhere else.** Two real crashes this prevents:
  1. `__ERD_RESULTS__` cells are not all numbers (dates are ISO strings, VARCHAR numerics are
     strings, NULL is `null`) -- `cell.toFixed(2)` throws
     `TypeError: v.toFixed is not a function` and kills the whole card. `fmt()` coerces with
     `Number(v)` first and falls back to the raw string, so it never throws.
  2. ECharts `label.formatter` / `tooltip.formatter` callbacks receive a **params object**,
     not a number -- `formatter: v => v.toFixed(2)` throws the same TypeError. Write
     `formatter: params => fmt(params.value)`, or use `valueFormatter` (which does receive
     the value) with `v => fmt(v)`.
- Use thousands separators (`1,234`), not `1234`.
- **Pick precision from the value's magnitude, not a blanket rule.** A flat `toFixed(2)` breaks
  for anything smaller than a typical KPI: a regression slope like `0.000745858` rounds to
  `0.00` and reads as "no effect." Integers still get no decimal places. One shared helper,
  switching to significant figures below what 2 decimal places can represent:
  ```js
  const fmt = v => {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    if (Number.isInteger(n)) return n.toLocaleString();
    const abs = Math.abs(n);
    return (abs !== 0 && abs < 0.01) ? n.toPrecision(3) : n.toFixed(2);
  };
  ```
- **p-values never go through `fmt()`** -- `toFixed(2)` collapses a genuinely small p-value
  (`0.0003`) to `0.00`. Threshold below a cutoff, otherwise use scientific notation:
  ```js
  const fmtP = p => p < 0.001 ? 'p < 0.001' : 'p = ' + Number(p).toExponential(2);
  ```
- **If there's a unit, show the unit -- on both the value axis ticks and the tooltip**:
  - Value axis: `axisLabel: { formatter: '{value} ms' }` (the unit follows the tick); the axis
    title/card heading can still say `回應時間 (ms)`.
  - Tooltip: attach the unit with `valueFormatter`:
    `tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: v => fmt(v) + ' ms' }`.
  - Infer the unit from the column name or the data: `*_ms` -> ms, `*_min` -> min,
    `*_pct`/`*rate`/yield rate -> %, an SPC dataset's `unit` column should be used directly; if
    you can't infer a unit, don't force one on.
- **Every numeric cell in the detail table must also go through `fmt()` (or `fmtP()` for a
  p-value column)** -- the most common way this breaks is dumping raw rows straight into
  `innerHTML`, letting a raw AVG value with a long decimal tail land on the screen.
- The above is the **default**: when the user explicitly specifies a decimal precision ("show 4
  places," "round to an integer"), change the precision **inside `fmt`** (keep the `Number(v)`
  coercion and the `isFinite` fallback, swap `toFixed(2)` for `toFixed(N)`) and stay consistent
  with it for that round -- still NEVER a bare `toFixed(N)` at call sites.

---

## ECharts implementation gotchas (things the guard will reject, or that visibly break)

- **Adding a chart container MUST come with a matching `echarts.init` + `setOption`; adding a
  tab MUST come with the matching switch logic wired up (`showTab`/`onclick`/the init call
  inside `DOMContentLoaded`)** -- adding only a `<div id="chart-xxx">` or a
  `<button onclick="showTab(N)">` skeleton without the corresponding JS logic ships an empty
  shell, and that counts as **unfinished**, not "I'll fill it in later." This is something only
  the user will catch on review -- the guard doesn't block it (the HTML structure itself is
  valid, with no syntax or execution error).
- **Every chart's `option` must set `tooltip`** -- the guard rejects the case where the HTML has
  an `echarts.init` call but `tooltip` doesn't appear anywhere in the whole file. Use
  `tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }` for bar/line/scatter
  (line/scatter can also use `type: 'line'`, but `'shadow'` reads clearest for bars); use
  `tooltip: { trigger: 'item' }` for pie.
- Every Cartesian chart (bar/line/scatter/heatmap) needs
  `grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true }` --
  `containLabel: true` makes sure axis labels don't get clipped.
- A chart with a legend needs `legend: { top: 5 }` (a number, **NEVER** `top: 'X%'` -- a
  percentage string computes to the wrong position), and `grid.top` must be >= 48px, e.g.
  `grid.top: '50px'`. Set both together -- setting only one lets the legend cover the chart.
- A time series/control chart with more than 30 data points must add a slider dataZoom, and set
  `grid.bottom` to `60` (a number, not a percentage string) to keep the slider from covering the
  x-axis labels:
  ```js
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 5 }],
  grid: { left: '3%', right: '4%', bottom: 60, containLabel: true }
  ```
- `axisLabel.formatter` differs depending on `xAxis.type`:
  - `type: 'category'` (data is date strings): can be truncated directly with
    `formatter: v => v.substring(0, 10)`.
  - `type: 'time'` (the formatter receives a ms number): **NEVER** call `.substring()` on it --
    a number doesn't have that method, and it will throw a TypeError that leaves the whole chart
    blank. Use the built-in formatter instead:
    `formatter: value => echarts.format.formatTime('MM-dd hh:mm', value)`.
- **`xAxis.type: 'time'` requires `series.data` as `[timeValue, y]` pairs, not a bare array of
  y-values** -- a bare array silently squashes the line against a time axis (no timestamp to
  place each point at), with no thrown error. Pair every point with its timestamp:
  `data: rows.map(r => [String(r[timeIdx]), Number(r[valueIdx])])`.
- **A `label.formatter`'s raw number is `params.value`, not `params.data`** -- when a
  bar/scatter point uses object form `{ value, itemStyle }` (to color individual points),
  `params.data` is the whole object; `fmt(params.data)` silently prints "NaN". Always
  `formatter: params => fmt(params.value)`.
- **NEVER seed a running max/min compared via `Math.abs()` with `Infinity`/`-Infinity`** --
  `Math.abs(-Infinity)` is still `Infinity`, so the comparison never wins and the tracker ships
  a literal `-Infinity` with no thrown error. Seed from the first real row instead:
  `let maxSlope = slopes[0], maxSlopeEquip = equipNames[0]; for (let i = 1; i < ...)`.
- **NEVER** use ECharts' `title` option -- its default position overlaps the legend. Chart
  titles always go in an HTML card heading, outside the chart container:
  `<h3 class="text-sm font-semibold text-slate-700 mb-3">圖表標題</h3>`.
- When the value axis's data range sits far from 0 (e.g. everything falls between 420-450), set
  `yAxis.min`/`max` to hug the actual data range (e.g. `min: Math.floor(dataMin - margin)`) --
  otherwise the whole line gets squashed into a thin band. This rule is mandatory, not optional,
  for SPC control charts.
- `markLine` labels (UCL/CL/LCL) need `label: { position: 'insideEndTop' }`, otherwise they get
  clipped by the chart's right edge.
- Every element of `markArea.data` MUST be "a two-object [start, end] pair":
  `data: [[{ yAxis: 0 }, { yAxis: 80 }]]`. **NEVER** write it as a single object with two
  identically-named keys (`[[{ yAxis: 0, yAxis: 80 }]]`) -- duplicate keys are legal JS, but the
  second one silently overwrites the first, and ECharts, receiving what it sees as a
  single-element array, throws `undefined (reading 'coord')` at render time, **synchronously
  crashing the whole `setOption` call and taking down every subsequent chart on the same page
  with it** (a real case).
- **Every chart's init+setOption MUST be wrapped in its own try/catch**, with
  `console.error('[ERD] chart <name> failed:', error)` in the catch -- an error in one chart's
  option should only sacrifice that one chart; **NEVER** let the exception abort rendering of
  the remaining charts and tables. The catch MUST also re-throw asynchronously:
  `setTimeout(() => { throw error; }, 0)`. This is not optional decoration -- a plain
  `console.error` with no rethrow silently swallows the error, which means it never reaches
  `window.onerror`, which means the repair-flow's error-reporting chain never fires and the
  broken chart ships as-is. The async rethrow (via `setTimeout`) still surfaces the error to
  `window.onerror` for that chain, while not aborting the synchronous script -- by the time the
  deferred throw fires, every other chart in this block has already finished initializing.
