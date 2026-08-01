# HTML contract

dashboard.html is a **single self-contained file** -- all CSS/JS lives inline in the file or
comes from the whitelisted CDNs; it must not reference any other local file.

## CDN whitelist (verbatim, NEVER change a character)

Only the following two `<script src>` tags are allowed, and they must be written exactly like
this:

```html
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5"></script>
```

- **NEVER** use an old-style CSS build like `tailwindcss@2`, **NEVER** load Tailwind via a
  `<link rel="stylesheet">` tag, **NEVER** use any other Tailwind CDN URL.
- The ECharts version prefix can be `echarts@5`, `echarts@5.4.3`, etc., but it **must start
  with** `https://cdn.jsdelivr.net/npm/echarts@` -- these two prefixes are kept in sync with
  `app/engine/html_guard.py`'s `ALLOWED_SCRIPT_SRC_PREFIXES`; a wrong character, a different
  host, or a different path will get the dashboard rejected by the guard and sent back for a
  rewrite.
- Other than these two `<script src>` tags, every other `<script>` must be inline (no `src`).

## ECharts theme -- 'erd' is injected by the system, you only need to specify it

```js
const chart = echarts.init(document.getElementById('chart-xxx'), 'erd');
```

- Every `echarts.init(...)` call **must pass the second argument `'erd'`**.
- **NEVER** call `echarts.registerTheme(...)` -- the `erd` theme (color palette, tooltip
  styling, axis colors) is injected by the system before your script runs; registering your own
  will just be ignored or conflict with it.
- **NEVER** specify `color` yourself in any chart's `option` (whether at the series level or as
  a top-level `color: [...]` in the option) -- color is always decided by the `erd` theme. You
  only decide "how many series, which one should be emphasized" -- not the actual color values.

## resize handler (every chart needs one)

```js
window.addEventListener('resize', () => chart.resize());
```

Multiple charts means multiple `.resize()` calls (or store them in an array and call them all
together). When tabs are switched, you additionally need
`window.dispatchEvent(new Event('resize'))` (see "Tabs" below).

## Chart containers need a fixed height

Every chart container `<div>` **must have a fixed height** -- ECharts draws nothing in a
zero-height container. A Tailwind height class (e.g. `h-72`, `h-80`) or an inline
`style="height:320px"` both work, but **percentage heights are not allowed** (the parent's
height usually isn't defined either, so it still computes to 0).

```html
<div id="chart-main" class="h-72"></div>
```

## Banner -- solid color, NEVER a gradient

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
    <span class="text-xs bg-slate-700 text-slate-200 px-3 py-1.5 rounded-full">資料截至 YYYY-MM-DD</span>
  </div>
</header>
```

## Icons/headings -- NEVER emoji

Headings, tab labels, and card titles must always use an inline SVG line icon
(`stroke="currentColor" stroke-width="2"`) -- **NEVER** decorate with emoji characters.

## Tabs (when there are multiple panels)

Panels get `id="panel-0"`, `id="panel-1"`, etc.; every panel except the first also gets
`class="hidden"`.

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

`showTab` must always be declared at the **top level (global scope)** of the `<script>`, not
inside a `DOMContentLoaded` callback -- an inline `onclick="showTab(0)"` can only resolve a
global name; declaring it inside a callback would throw a `ReferenceError`. The resize dispatch
is required, otherwise charts don't measure their correct size when switching back to a tab:

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

Call `showTab(0)` once inside `DOMContentLoaded` to activate the first tab.

The tabs markup and `showTab` MUST use this template verbatim (including the class strings) --
a styling deviation (pill/segmented style, etc.) or a missing resize dispatch will get the
dashboard rejected by the system and sent back for a redo.

## KPI cards -- white background, semantic-color left edge, delta badge

Left-edge color codes: `border-l-blue-500` (primary), `border-l-emerald-500` (good),
`border-l-amber-500` (warning), `border-l-rose-500` (bad).

```html
<div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-blue-500">
  <p class="text-xs text-slate-500 font-medium">指標名稱</p>
  <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-xxx"></p>
  <span class="inline-block mt-2 text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700">delta 文字</span>
</div>
```

## Insight card -- amber tone + lightbulb icon (at least one per dashboard)

```html
<div class="bg-amber-50 border border-amber-200 border-l-4 border-l-amber-400 rounded-lg p-4 text-sm text-amber-900">
  <span class="inline-flex items-center gap-1.5 font-semibold">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 1 4 10.5c-.6.5-1 1.2-1 2v.5H9v-.5c0-.8-.4-1.5-1-2A6 6 0 0 1 12 3z"/></svg>
    自動洞察：</span>
  [洞察文字——本文用 slate 色階,語意色只用在強調/badge/狀態,不要整段都塗色]
</div>
```

## Spacing between sections -- spacing travels with the section

Every major block (insight card, KPI card row, each chart card, detail table) **owns its own
`mb-6` on its outermost element**; containers (`<main>`, a tab's `panel` div) **NEVER** use
`space-y-*` to handle section spacing. Reason: `space-y` only affects direct children, so the
moment a section gets wrapped in an intermediate layer like a tab panel, it silently stops
working (a real case: the insight card and the KPI row ended up stuck together). Binding the
spacing to the section itself means it never gets lost no matter which layer it's placed in.
Grid spacing inside a section still uses `gap-4` as usual, unaffected by this rule. **NEVER**
mix both approaches (`mb-6` plus an outer `space-y` stacks into double spacing).

## Section anchors -- for later grep-based navigation

Every major block (insight card, KPI card row, chart section, detail table, ...) **MUST** be
preceded by an HTML comment anchor line, formatted as `<!-- section: name -->` (e.g.
`<!-- section: kpi -->`, `<!-- section: insight -->`, `<!-- section: charts -->`,
`<!-- section: detail-table -->`). This is a navigation point for later iterative edits --
before making a change, `grep` for the anchor or the heading text to locate the target block;
don't `read_file` the whole document to find your target (see the "Modifying an existing
dashboard.html" section in SKILL.md).

## Always declare a variable before reading `__ERD_RESULTS__`

Every access to `window.__ERD_RESULTS__["qN"]` **MUST** first be declared with
`const variableName = window.__ERD_RESULTS__["qN"];` before use -- **NEVER** take the shortcut
of accessing a property on an undeclared variable (e.g. forgetting to declare `timeout` and
then writing `timeout.columns`). This isn't a style issue: a `ReferenceError` aborts execution
of the entire `<script>` block, killing **every** chart that comes after it in that script, not
just the one that referenced it -- a real case had several consecutive iteration rounds all
break this way. The guard's sandbox execution check will catch it, but it's faster to just
declare it correctly the first time than wait for the guard to reject and rewrite.

```js
// Correct -- declare, then use
const summary = window.__ERD_RESULTS__["q1"];
const lineIdx = getCol(summary.columns, 'production_line', 'line', '產線');

// Wrong -- forgot to declare `summary` first, ReferenceError blows up the entire <script>
const lineIdx = getCol(summary.columns, 'production_line', 'line', '產線');
```

## Other rules

- All copy in the dashboard MUST be Traditional Chinese; technical terms (e.g. KPI, SPC, Cpk)
  may stay in English.
- **NEVER** write `@apply` inside a `<style>` block -- the Tailwind CDN doesn't compile it, so
  the style silently fails to apply. Use inline utility classes instead.
- Variables shared across blocks (column indexes, aggregated results, per-tool maps) must
  always be declared at the **top level** of the `DOMContentLoaded` callback, before any
  if-block. NEVER declare a variable inside one if-block and reference it inside another --
  that's syntactically legal but throws a `ReferenceError` at runtime (the variable is out of
  scope in the other block).
