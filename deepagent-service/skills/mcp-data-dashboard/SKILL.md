---
name: mcp-data-dashboard
description: Use when producing or modifying an HTML dashboard in a connector (MCP) session,
  where the page fetches its data at view time through the host-provided `mcp()` function
  instead of reading `window.__ERD_RESULTS__`, including interactive pages where a dropdown or
  multi-select triggers connector calls (and chained calls whose args come from a previous
  response). Single-file contract covering the `mcp()` call contract, in-browser transforms,
  per-card loading/error states, interactive controls, layout, chart selection, ECharts rules,
  and runnable examples; MUST be read before writing dashboard.html.
---

# MCP data dashboard skill

Complete contract for building `dashboard.html` whose data comes **live from API connector
tools**. The page calls `mcp(connectorName, toolName, toolArgs, handler)`; the host forwards
the call to the connector and hands the response to `handler`. Nothing is pre-injected -- there
is no `window.__ERD_RESULTS__` in this mode, and every transformation happens in browser JS.

## Workflow

1. Finish the analysis first with the connector tools (`<connector id>_<tool>`, landing results
   with `land_as` and querying them with `run_sql`). This is where you learn, for each dataset
   the dashboard needs: the **connector id**, the **tool name**, the **exact args**, and the
   **response shape** (top-level array or object, key names, value types). Copy these; never
   reconstruct them from memory.
2. Decide the dashboard's datasets: **one `mcp()` call per dataset, not per chart.** A KPI row,
   a chart and a table that read the same rows share one call. Keep it to a handful of calls
   (≤6) per interaction -- every call is a full round trip through the platform to the
   connector.
3. Decide, per tool arg, **fixed or viewer-chosen** (see "Arg policy"). Two people touch this
   page: the **editor** (the user you are talking to now) and the **viewer** (whoever opens the
   finished page later). Default is viewer-chosen; hardcode only what the editor said is fixed.
4. Plan the layout (see "Default layout").
5. Write the whole page with a **single `write_file` call**, path fixed to `dashboard.html`
   (no other name, no subdirectory). NEVER write a skeleton first and fill it in over several
   small writes -- each write is a full generation pass; a few dozen later you risk the
   recursion limit. MUST persist changes with write_file or edit_file after modifying dashboard!
6. Run `check_dashboard` after every `write_file`/`edit_file` of dashboard.html. It
   syntax-checks every inline script and checks the `mcp()` contract (literal connector/tool,
   arg keys matching a call you made, forbidden APIs, CDN whitelist, `'erd'` theme). Fix every
   finding and re-run until it reports `OK` before you answer the user -- a finding you ship
   becomes a blank page for the viewer.
7. Modifying an existing dashboard.html (user tweak, or a repair round):
   - **Small, targeted change -> `edit_file`**; large change or full restructure -> `write_file`
     (a single complete rewrite). For an edit_file, read the file first, then match a unique
     `old_string` (anchor on a `<!-- section: name -->` comment) and replace just that block.
   - Read the current file in one call first: `read_file(file_path="dashboard.html",
     limit=1000)`. NEVER page-scan with the default limit=100, and NEVER rewrite from memory
     without reading. For a small edit, `grep` the `<!-- section: name -->` anchor to locate
     the block.
   - Preserve everything the user didn't ask to change -- carry unchanged sections over
     verbatim. Silently dropping/altering an unrelated chart is a defect.
   - Self-check before writing: every variable and element id you reference must be
     declared/present in the same version. `getElementById` returns `null` for a removed id and
     the immediate property access throws, blanking every chart -- the guard reproduces this.

## Runtime environment (what you can rely on)

The page runs inside a sandboxed frame owned by the host. Observable consequences:

- `mcp` is a **global function provided by the host** and the **only** way to reach data.
  NEVER define, stub, polyfill or feature-test it (`if (typeof mcp === 'function')`), and NEVER
  wrap it in your own generic helper that takes the connector/tool/args as variables.
- NEVER use `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `<img src>` beacons, or any
  script `src` outside the CDN whitelist. Network from the page is brokered; direct calls fail.
- NEVER touch `window.parent`, `window.top`, `postMessage`, `localStorage`, `sessionStorage`,
  `document.cookie`. The page has no state outside itself.
- A call answers **seconds** later, not milliseconds. Every card MUST render a visible loading
  state until its handler runs (see "Card states").

## Data contract -- `mcp()`

```js
mcp('<connector id>', '<tool name>', { /* literal args */ }, r => { /* handler */ });
```

- `connectorName`: the connector id exactly as listed in the system prompt (the prefix before
  `_` in the tool you called, e.g. `sales` for `sales_list_orders`).
- `toolName`: the tool's own name without the connector prefix (`list_orders`).
- `toolArgs`: a plain JSON object literal with the **same keys you passed during analysis**;
  values are literals only for args the editor fixed, otherwise they come from a viewer
  control (see "Arg policy"). `{}` when the tool takes none; never `null`/`undefined`.
- `handler`: called **exactly once** with one response object `r`:
  - **failure** → `r.error` is an object; `r.error.message` is a human-readable string
    (connector unreachable, tool rejected the args, timeout, budget exceeded, …). `r.data` is
    absent.
  - **success** → `r.error` is `null`/`undefined` and `r.data` is the tool's JSON payload,
    **byte-for-byte what you saw when you called the same tool with the same args** during
    analysis: a top-level array of flat row objects, or an object whose keys you already know.
- `mcp()` returns nothing useful; do not `await` it, do not chain on it.

### Three ironclad rules for `mcp()` calls

1. **Connector id and tool name are string literals, args is an object literal at the call
   site.** No variables or template literals for the connector/tool, no spread, no
   `JSON.parse`, no args object passed in from elsewhere. Arg **keys** are exactly the keys you
   used during analysis. Arg **values** are literals, or -- for interactive pages -- values
   taken from a control's current selection or from a previous response, coerced to the type
   the tool expects (`Number(...)`, `String(...)`, an array of strings). A computed tool name
   can't be checked and a guessed one fails at view time with nothing to repair.
2. **Every call MUST mirror an actual tool call you made this session** (same connector, same
   tool, same arg keys, values of the same type). Never a remembered call from a previous turn,
   never a tool you only saw in a skill file but didn't run. If the dashboard needs a dataset
   you haven't fetched, fetch it with the tool first, look at the shape, then write the call.
3. **One handler per call; a response is consumed where it lands.** Never put `r.data` on
   `window`, never keep a raw response around "for later." Everything that needs a dataset
   (KPI, chart, table, insight sentence) is rendered from inside that dataset's handler. Exactly
   two sanctioned ways for data to cross handlers, both via a **top-level `let`/`const`** (not
   `window`) declared next to the chart instances:
   - **Chained call** -- the second dataset's args come from the first response: issue the
     second `mcp()` *inside* the first handler and render in the inner one.
   - **Fan-out join** -- one interaction issues two independent calls whose results must be
     combined (per-head = payroll ÷ headcount): each handler stores its *transformed* output in
     a shared `joinState` tagged with the `requestId`, then calls one `tryRender*Joined()` that
     renders only when both halves for the current `requestId` are present.
   Reference/lookup data (an id → display-name `Map` built from the options call) is also a
   top-level `const` filled by its own handler and read by later ones.
   Both patterns are spelled out in "Interactive controls".

### Arg policy -- viewer-chosen by default, fixed only on the editor's word

Every arg value you supplied during analysis (`days: 30`, `regions: ["North"]`,
`dept_ids: [...]`) is a choice, and the person who opens the finished page is usually not the
person who made it. So:

| Situation | What the page does |
|---|---|
| The editor said the value is fixed (「固定看近 30 天」, "always these two plants") | Literal in the `mcp()` call. Say so in visible copy (subtitle 「近 30 日」) so the viewer knows it isn't adjustable. |
| The editor said nothing about it | **Viewer control** -- a `<select>`/`<select multiple>`/`<input type=date>`/`<input type=number>` bound to that arg key, initialised to the analysis value so the page loads with data on open. |
| You are unsure which the editor wants | One `ask_user` listing each arg, its analysis value, and the proposed control, e.g. 「`days`（分析時 30）：做成下拉讓檢視者選 7/30/90 天？還是固定 30 天？」. Never guess "fixed" because it is less code. |
| The tool takes no args | `{}` -- nothing to decide. |

Building a viewer control:

- **Option lists come from a call, not from memory.** If the connector has a lookup tool for
  that arg (`list_regions`, `list_departments`), populate the options from it at load time
  (card-state pattern on the control). If it doesn't, use an input of the right type (number,
  date) with the analysis value as its initial value -- never a `<select>` whose options are
  data values you remember from a sample. The only hardcoded options allowed are ones that are
  not data: a fixed set of window sizes (7/30/90 days), sort orders, top-N sizes.
- **Initial load uses the analysis values**: `DOMContentLoaded` calls the same `load*()` the
  `change` listener calls, so the page is never empty on open and the two paths can't drift.
- One control may feed several calls (fan-out), and one call may read several controls; the
  args object at the call site still lists every key as a literal key with a coerced value.
- The control's label is plain language for the viewer (「期間」, 「區域」), never the arg key.

### Reading the response

- Normalize to `rows` at the top of the handler using the shape you observed:
  `const rows = r.data;` for a top-level array, or `const rows = r.data.items;` for an object
  -- copy the key exactly. NEVER guess a key and NEVER "search" for the array
  (`Object.values(r.data).find(Array.isArray)`) -- a guess reads `undefined` and every card on
  that dataset dies silently.
- Plain JSON objects don't throw on a wrong column name -- they return `undefined`, which turns
  into `NaN`/`"undefined"` on screen with no error. Read every column through the `col()`
  helper below (throws on a missing key, so a typo enters the repair flow instead of shipping):

```js
function col(row, name) {
  if (!(name in row)) throw new Error('[ERD] no column "' + name + '" in row');
  return row[name];
}
```

- Values arrive as whatever JSON the tool sent -- numbers may be strings, dates are strings.
  Coerce inside transforms (`Number(col(row, 'qty'))`, `String(col(row, 'order_date'))`).
- A column that drives a JS branch (significant/not, pass/fail) MUST be a comparable number or
  boolean, NEVER a label string JS re-parses. Trap: `'不顯著'` contains `'顯著'` as a substring,
  so `.includes('顯著')` matches both and mislabels every row with no error. Branch on
  `is_significant`/`p_value`; label strings are fine only for a table cell/tooltip.
- NEVER embed data values in the HTML (including sample rows from the analysis) -- every number
  on the page is computed from `r.data` at view time. Next time the page opens the connector may
  return different rows; a hardcoded number is then a lie.

### Transforms live in the browser -- keep them pure and separate

There is no SQL step between the connector and the page, so grouping, filtering, sorting and
simple statistics (count, sum, mean, min/max, share, top-N) are done in JS. Rules:

- Every transform is a **pure function `rows → arrays/objects`** declared at top level of the
  `<script>`, named `transform*`/`groupBy*`/`summarize*`. Render functions (`render*`) receive
  transform output and touch the DOM/ECharts; they never re-read `r.data`.
- Keep the math to what a reader can verify by eye. Regression, p-values, control limits and
  the like belong in the analysis (`run_sql`), and their **conclusion** is stated in copy; the
  page does not re-derive them.
- Empty `rows` (`[]`) is a legitimate state, not an error -- show the card's empty state
  (「（無資料）」), never a blank chart and never `|| 0` defaults.

## Card states -- loading / error / empty / content

Every data-bound card (KPI row, chart card, table card, insight card) carries three slots and
starts in `loading`. Use this markup shape and helper verbatim:

```html
<div id="card-yield" class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
  <h3 class="text-sm font-semibold text-slate-700 mb-3">各區域良率(%)</h3>
  <p data-slot="loading" class="text-sm text-slate-400">載入中…</p>
  <p data-slot="error" class="hidden text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2"></p>
  <div data-slot="content" class="hidden">
    <div id="chart-yield" class="h-72"></div>
  </div>
</div>
```

```js
// 卡片三態:loading → (error | content)。error 訊息來自 connector,一律 textContent,
// NEVER innerHTML。content 顯示後才 init chart(隱藏容器高度為 0)。
function setCardState(card, state, message) {
  card.querySelector('[data-slot=loading]').classList.toggle('hidden', state !== 'loading');
  const errorSlot = card.querySelector('[data-slot=error]');
  errorSlot.classList.toggle('hidden', state !== 'error');
  errorSlot.textContent = state === 'error' ? String(message) : '';
  card.querySelector('[data-slot=content]').classList.toggle('hidden', state !== 'content');
}
```

- `setCardState(byId('card-yield'), 'error', r.error.message)` -- the card reference is a
  complete literal id via `byId()`; slots are found relative to the card, so no composed ids.
- **Switch to `content` before `echarts.init`**, never after -- a chart initialized inside a
  `hidden` container measures 0×0 and draws nothing.
- Empty state: `setCardState(card, 'error', '（無資料）')` is acceptable; the tone is informative,
  not a failure.

### Handler skeleton (every `mcp()` handler follows this shape)

```js
mcp('sales', 'list_orders', { days: 30 }, r => {
  const card = byId('card-yield');
  if (r.error) {                       // connector-side condition: show it, do NOT rethrow
    console.warn('[ERD] sales/list_orders failed:', r.error.message);
    setCardState(card, 'error', r.error.message);
    return;
  }
  try {                                // code-side bug: sacrifice this card, surface to repair
    const rows = r.data;
    if (rows.length === 0) { setCardState(card, 'error', '（無資料）'); return; }
    const byRegion = summarizeYieldByRegion(rows);
    setCardState(card, 'content');
    renderYieldChart(byRegion);
  } catch (error) {
    console.error('[ERD] card yield failed:', error);
    setCardState(card, 'error', '圖表載入失敗');
    setTimeout(() => { throw error; }, 0);
  }
});
```

Two different failures, two different treatments -- this distinction is the core of the mode:

| Failure | Where | Treatment |
|---|---|---|
| `r.error` set (connector unreachable, bad args, timeout, budget) | data side | `console.warn` + show `r.error.message` in the card; **no rethrow** -- a repair round can't fix a connector |
| exception inside the handler (typo'd column, ECharts option bug) | code side | `console.error` + card error text + **async rethrow** (`setTimeout(() => { throw error; }, 0)`) so `window.onerror` drives the repair flow without aborting sibling handlers |

NEVER swallow a code-side error with a bare `console.error`; NEVER rethrow a connector error.

## Interactive controls -- a dropdown driving a two-step call chain

The recurring interactive shape: the user picks one or more values in a `<select multiple>`;
the selection is sent to tool A; A's response is transformed into the args of tool B (possibly
on another connector); B's response feeds the transforms and the chart. Rules that keep this
from misbehaving:

- **Fire on `change`, never on every keystroke or render.** Each interaction costs two full
  round trips; no polling, no auto-refresh timers.
- **Disable the control while calls are in flight** and re-enable it in every exit path
  (error, empty, success, exception). When one interaction fires more than one call, count
  them: `let pending = N; const finish = () => { if (--pending === 0) select.disabled = false; };`
  and call `finish()` exactly once per handler exit.
- **KPI text on failure**: when the dataset a KPI reads from fails or is empty, set the value
  element to `'—'` (never leave the `…` placeholder, never `0`), and set the insight text to a
  short failure sentence.
- **Stale-response guard.** The user can change the selection again before the first chain
  answers. Keep a per-card `requestId` counter; capture it when the chain starts and bail out of
  any handler whose captured id is no longer current. Without this, an older response that
  arrives last overwrites the newer one.
- **Args from a control** are read at click time, coerced, and validated for emptiness: an
  empty selection renders the empty state and does not call anything.
- **Args from a response** go through a named `transform*ToArgs` function so the mapping from
  A's columns to B's arg keys is explicit and reviewable; the arg keys still match the call
  you made during analysis.
- **Populate the dropdown's options from a call too** (never hardcode option values from the
  sample you saw), using the same card-state pattern for the control's own loading/error.
- The chart object for an interactive card is created **once** and reused with `setOption` on
  later interactions -- calling `echarts.init` on an already-initialized container logs a
  warning and leaks the old instance. Keep the instance in a top-level `let`.

Complete card (connector `sales`, tool `list_regions` `{}` → options; on change,
`sales/list_orders` `{ regions: [...] }` → its rows' `order_id`s become the args of
`quality/inspection_results` `{ order_ids: [...] }` → chart):

```html
<!-- section: inspection-by-region -->
<section id="card-inspection" class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
  <div class="flex items-center justify-between mb-3">
    <h3 class="text-sm font-semibold text-slate-700">所選區域的檢驗結果分布</h3>
    <select id="select-regions" multiple size="4" disabled
      class="text-sm border border-slate-300 rounded-lg px-2 py-1 min-w-48">
      <option disabled>載入區域中…</option>
    </select>
  </div>
  <p data-slot="loading" class="text-sm text-slate-400">請選擇區域</p>
  <p data-slot="error" class="hidden text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2"></p>
  <div data-slot="content" class="hidden"><div id="chart-inspection" class="h-72"></div></div>
</section>
```

```js
// ---- 純函式:A 的回應 → B 的 args;B 的回應 → 圖表資料 ----
function transformOrdersToInspectionArgs(orderRows) {
  return { order_ids: orderRows.map(row => String(col(row, 'order_id'))) };
}
function summarizeInspectionByResult(inspectionRows) {
  const acc = new Map();
  for (const row of inspectionRows) {
    const result = String(col(row, 'result'));
    acc.set(result, (acc.get(result) || 0) + 1);
  }
  return [...acc.entries()].map(([result, count]) => ({ result, count }));
}

let inspectionChart = null;                 // 同一容器只 init 一次,之後 setOption 更新
let inspectionRequestId = 0;                // 過期回應守門:只有最新一次互動能畫圖

function renderInspection(byResult) {
  if (!inspectionChart) {
    inspectionChart = echarts.init(byId('chart-inspection'), 'erd');
    window.addEventListener('resize', () => inspectionChart.resize());
  }
  inspectionChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: v => fmt(v) },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: byResult.map(entry => entry.result) },
    yAxis: { type: 'value', axisLabel: { formatter: value => fmt(value) } },
    series: [{ type: 'bar', data: byResult.map(entry => entry.count) }]
  }, true);                                 // notMerge:換選取時舊 series 不殘留
}

function loadInspectionForSelection() {
  const card = byId('card-inspection');
  const select = byId('select-regions');
  const regions = [...select.selectedOptions].map(option => String(option.value));
  if (regions.length === 0) { setCardState(card, 'loading'); return; }   // 「請選擇區域」

  const requestId = ++inspectionRequestId;
  const finish = () => { select.disabled = false; };
  select.disabled = true;
  setCardState(card, 'loading');
  card.querySelector('[data-slot=loading]').textContent = '載入中…';

  // 第一步:選取值 → tool A。args 的 key(regions)與分析時的呼叫相同,值來自控制項。
  mcp('sales', 'list_orders', { regions: regions }, r => {
    if (requestId !== inspectionRequestId) return;            // 已被更新的選取取代
    if (r.error) {
      console.warn('[ERD] sales/list_orders failed:', r.error.message);
      setCardState(card, 'error', r.error.message); finish(); return;
    }
    let inspectionArgs;
    try {
      const orderRows = r.data;
      if (orderRows.length === 0) { setCardState(card, 'error', '（所選區域無訂單）'); finish(); return; }
      inspectionArgs = transformOrdersToInspectionArgs(orderRows);
    } catch (error) {
      console.error('[ERD] inspection args failed:', error);
      setCardState(card, 'error', '圖表載入失敗'); finish();
      setTimeout(() => { throw error; }, 0);
      return;
    }

    // 第二步:A 的回應 → tool B(另一個 connector)。args 值來自上一個回應,key 不變。
    mcp('quality', 'inspection_results', { order_ids: inspectionArgs.order_ids }, r2 => {
      if (requestId !== inspectionRequestId) return;
      if (r2.error) {
        console.warn('[ERD] quality/inspection_results failed:', r2.error.message);
        setCardState(card, 'error', r2.error.message); finish(); return;
      }
      try {
        const inspectionRows = r2.data;
        if (inspectionRows.length === 0) { setCardState(card, 'error', '（無檢驗資料）'); finish(); return; }
        setCardState(card, 'content');                          // 先顯示再畫,容器才有高度
        renderInspection(summarizeInspectionByResult(inspectionRows));
        finish();
      } catch (error) {
        console.error('[ERD] card inspection failed:', error);
        setCardState(card, 'error', '圖表載入失敗'); finish();
        setTimeout(() => { throw error; }, 0);
      }
    });
  });
}

// 在 DOMContentLoaded 內:先用一次呼叫填 options,再掛 change 監聽。
mcp('sales', 'list_regions', {}, r => {
  const select = byId('select-regions');
  if (r.error) {
    console.warn('[ERD] sales/list_regions failed:', r.error.message);
    select.innerHTML = '<option disabled>區域載入失敗</option>';
    return;
  }
  try {
    const regionRows = r.data;
    select.innerHTML = regionRows.map(row =>
      `<option value="${escapeHtml(col(row, 'region'))}">${escapeHtml(col(row, 'region'))}</option>`).join('');
    select.disabled = false;
    select.addEventListener('change', loadInspectionForSelection);
  } catch (error) {
    console.error('[ERD] regions select failed:', error);
    select.innerHTML = '<option disabled>區域載入失敗</option>';
    setTimeout(() => { throw error; }, 0);
  }
});
```

What makes this pattern safe to copy: the two tool names and the arg keys are literal and match
the analysis calls; only the arg *values* move (selection → A, A's `order_id`s → B); every exit
path re-enables the control; a stale chain can never paint over a newer one.

### Fan-out join -- one selection, two independent calls, one combined card

When the selection feeds two tools that don't depend on each other but the card needs both
(e.g. `hr/headcount_by_month` and `finance/payroll_totals` → payroll per head), fire both calls
from the change handler, store each **transformed** half in a top-level `joinState` tagged with
the `requestId`, and let a single `tryRenderPerHead()` render once both halves are current.
Lookup data from the options call (`deptNames`) lives in a top-level `const Map`.

```js
const deptNames = new Map();                // dept_id → dept_name,由 list_departments 的 handler 填
let perHeadRequestId = 0;
let joinState = { requestId: 0, headcount: null, payroll: null };

function transformHeadcountByMonth(rows) {  // → Map(month → headcount)
  const acc = new Map();
  for (const row of rows) {
    const month = String(col(row, 'month'));
    acc.set(month, (acc.get(month) || 0) + Number(col(row, 'headcount')));
  }
  return acc;
}
function transformPayrollByMonth(rows) {    // → Map(month → payroll_twd)
  const acc = new Map();
  for (const row of rows) {
    const month = String(col(row, 'month'));
    acc.set(month, (acc.get(month) || 0) + Number(col(row, 'payroll_twd')));
  }
  return acc;
}
function joinPerHead(headcountByMonth, payrollByMonth) {   // → [[month, twd per head]]
  return [...headcountByMonth.keys()].sort().map(month => {
    const heads = headcountByMonth.get(month);
    const payroll = payrollByMonth.get(month);
    return [month, heads && payroll != null ? payroll / heads : null];
  });
}

function tryRenderPerHead(card) {
  if (joinState.requestId !== perHeadRequestId) return;
  if (!joinState.headcount || !joinState.payroll) return;   // 另一半還沒到
  setCardState(card, 'content');
  renderPerHead(joinPerHead(joinState.headcount, joinState.payroll));
}

function loadPerHeadForSelection() {
  const card = byId('card-per-head');
  const select = byId('select-depts');
  const deptIds = [...select.selectedOptions].map(option => String(option.value));
  if (deptIds.length === 0) { setCardState(card, 'loading'); return; }

  const requestId = ++perHeadRequestId;
  joinState = { requestId, headcount: null, payroll: null };   // 新互動:整組重置
  let pending = 2;
  const finish = () => { if (--pending === 0) select.disabled = false; };
  select.disabled = true;
  setCardState(card, 'loading');

  mcp('hr', 'headcount_by_month', { dept_ids: deptIds, months: 12 }, r => {
    if (requestId !== perHeadRequestId) return;
    if (r.error) {
      console.warn('[ERD] hr/headcount_by_month failed:', r.error.message);
      setCardState(card, 'error', r.error.message); finish(); return;
    }
    try {
      const rows = r.data.series;                              // 分析時看到的是 { series: [...] }
      if (rows.length === 0) { setCardState(card, 'error', '（無人數資料）'); finish(); return; }
      joinState.headcount = transformHeadcountByMonth(rows);
      tryRenderPerHead(card); finish();
    } catch (error) {
      console.error('[ERD] headcount half failed:', error);
      setCardState(card, 'error', '圖表載入失敗'); finish();
      setTimeout(() => { throw error; }, 0);
    }
  });

  mcp('finance', 'payroll_totals', { dept_ids: deptIds, months: 12 }, r => {
    if (requestId !== perHeadRequestId) return;
    if (r.error) {
      console.warn('[ERD] finance/payroll_totals failed:', r.error.message);
      setCardState(card, 'error', r.error.message); finish(); return;
    }
    try {
      const rows = r.data;                                     // 分析時看到的是頂層陣列
      if (rows.length === 0) { setCardState(card, 'error', '（無薪資資料）'); finish(); return; }
      joinState.payroll = transformPayrollByMonth(rows);
      tryRenderPerHead(card); finish();
    } catch (error) {
      console.error('[ERD] payroll half failed:', error);
      setCardState(card, 'error', '圖表載入失敗'); finish();
      setTimeout(() => { throw error; }, 0);
    }
  });
}
```

Both handlers are symmetric: guard → error → transform into `joinState` → `tryRenderPerHead` →
`finish`. Whichever half arrives second triggers the render; a half from an older `requestId`
is discarded by the guard before it can touch `joinState`.

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
  null crash, and a composed id can defeat every static check. Inside a card, address slots
  with `card.querySelector('[data-slot=…]')` instead of a second id.
- Shared render functions for similar panels take the **complete ids as parameters**, and the
  call sites list every id as a literal, side by side:

```js
function renderDetailTable({ headId, bodyId, columns, rows }) {
  const head = byId(headId);
  const body = byId(bodyId);
  // ...same rendering for both panels...
}
renderDetailTable({ headId: 'detail-table-head-p0', bodyId: 'detail-table-body-p0',
                    columns: orderColumns, rows: allOrders });
renderDetailTable({ headId: 'detail-table-head-p1', bodyId: 'detail-table-body-p1',
                    columns: orderColumns, rows: prodOrders });
```

  The paired call lines make a swapped p0/p1 id (or dataset) visible at a glance -- that
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
inside `DOMContentLoaded` **before** issuing the `mcp()` calls. The resize dispatch is required
or charts mis-measure on tab switch; data that arrives while a tab is hidden still renders
correctly because the handler switches the card to `content` (visible inside its panel) before
`echarts.init`, and the panel's own `resize` dispatch re-measures on open.

### KPI cards -- white bg, semantic-color left edge, delta badge

Left-edge codes: `border-l-blue-500` (primary), `border-l-emerald-500` (good),
`border-l-amber-500` (warning), `border-l-rose-500` (bad). The value element starts as `…`
and is filled by the handler.

```html
<div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-blue-500">
  <p class="text-xs text-slate-500 font-medium">指標名稱</p>
  <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-xxx">…</p>
  <span class="inline-block mt-2 text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700" id="kpi-xxx-delta"></span>
</div>
```

### Insight card -- amber tone + lightbulb icon (at least one per dashboard)

```html
<div class="bg-amber-50 border border-amber-200 border-l-4 border-l-amber-400 rounded-lg p-4 text-sm text-amber-900">
  <span class="inline-flex items-center gap-1.5 font-semibold">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 1 4 10.5c-.6.5-1 1.2-1 2v.5H9v-.5c0-.8-.4-1.5-1-2A6 6 0 0 1 12 3z"/></svg>
    自動洞察：</span>
  <span id="insight-text">載入中…</span>
</div>
```

The sentence is assembled inside the handler of the dataset it talks about; until then the
placeholder text stays.

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
  Connector ids and tool names are **code-only wiring**: they appear ONLY inside `mcp()` calls,
  never in visible text (badges, footers, titles). Describe the data in plain language
  (「近 30 日訂單」), not `sales/list_orders`.
- NEVER `@apply` inside `<style>` -- the Tailwind CDN doesn't compile it. Use inline utilities.

## Default layout

When the user hasn't specified one, follow this order (if they stated their own, follow theirs):

1. **Insight card** at the top (below banner, above KPI row) -- at least one amber insight card;
   the user should see the conclusion the instant the data lands.
2. **KPI card row** -- each card MUST show the real value (`92.3%`, `1,204`), computed in JS
   from the response, never a bare label, never hardcoded.
3. **Primary + secondary chart** -- charts meant to be compared pair half-width side by side;
   time series, SPC charts, heatmaps, and detail tables stay full-width (half-width is illegible).
4. **Detail table** (bottom) -- the rows as returned by the connector.

### Four ironclad rules for insight cards (each caught a real violation)

1. Every number in a sentence MUST come from a live computation on the response, NEVER a
   hardcoded literal -- the connector's next answer makes a hardcoded number a lie.
2. Legitimately missing data (the column exists, the cell is `null`) → display "（資料缺失）",
   NEVER `|| 0` or a default (misleads the user that 0 is a real measurement). A wrong column
   name throwing via `col()` is not this case -- fix the binding, don't catch-and-default it away.
3. Verify the field you read matches the sentence's meaning -- a real case inserted a failure
   rate from one dataset into a sentence about another (read succeeded, semantics wrong -- more
   dangerous than a missing value because it looks normal).
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
  at a call site. Two crashes this prevents: (1) connector cells aren't all numbers (ISO date
  strings, numeric strings, `null`) -- `cell.toFixed(2)` throws; `fmt()` coerces with `Number(v)`
  and falls back to the raw string. (2) ECharts `label.formatter`/`tooltip.formatter` receive a
  **params object**, not a number -- write `formatter: params => fmt(params.value)`, or
  `valueFormatter: v => fmt(v)` (which does receive the value).
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
  the unit from the column name (`*_ms`→ms, `*_min`→min, `*_pct`/`*rate`→%); if you can't infer
  one, don't force it.
- Every numeric cell in the detail table also goes through `fmt()` (or `fmtP()`) -- the common
  break is dumping raw rows into `innerHTML` with a long decimal tail. Cell text from the
  connector goes through `escapeHtml()` (below) before `innerHTML`, or use `textContent`.
- User-specified precision → change it **inside `fmt`** (keep the `Number(v)` coercion and
  `isFinite` fallback, swap `toFixed(2)` for `toFixed(N)`); still never a bare `toFixed(N)` at
  a call site.

## ECharts implementation gotchas (guard-rejected or visibly broken)

- A new chart container MUST come with its `mcp()` handler + `echarts.init` + `setOption`; a new
  tab MUST come with the switch logic (`showTab`/`onclick`). A bare `<div>`/`<button>` skeleton
  ships an empty shell -- unfinished, not "fill in later."
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
  array silently squashes the line): `data: rows.map(r => [String(col(r, 'date')), Number(col(r, 'value'))])`.
- A `label.formatter`'s raw number is `params.value`, not `params.data` (object-form points make
  `params.data` the whole object → `fmt` prints "NaN"). Always `formatter: params => fmt(params.value)`.
- NEVER seed a running max/min compared via `Math.abs()` with `Infinity` (`Math.abs(-Infinity)`
  is still `Infinity`, so it never wins and ships `-Infinity`). Seed from the first real row.
- NEVER use ECharts' `title` option -- it overlaps the legend and duplicates the card heading (a
  real case shipped the title twice, the second on top of the legend). Put the title in an HTML
  heading outside the container: `<h3 class="text-sm font-semibold text-slate-700 mb-3">圖表標題</h3>`.
- When the value range sits far from 0 (e.g. 420-450), set `yAxis.min`/`max` to hug the data
  (`min: Math.floor(dataMin - margin)`) or the line squashes into a band.
- `markLine` labels (UCL/CL/LCL) need `label: { position: 'insideEndTop' }` or they clip.
- Each `markArea.data` element MUST be a two-object `[start, end]` pair
  (`data: [[{ yAxis: 0 }, { yAxis: 80 }]]`). NEVER a single object with duplicate keys
  (`[[{ yAxis: 0, yAxis: 80 }]]`) -- the second key silently overwrites the first and ECharts
  throws `undefined (reading 'coord')`, crashing the whole `setOption` and every later chart.
- Chart init + setOption lives inside the handler's `try` block (see "Handler skeleton"); the
  `catch` re-throws asynchronously. One chart's error sacrifices only that card.

### complete example

A complete, directly-renderable dashboard.html for a connector `sales` with two tools already
called during analysis: `list_orders` with args `{ days: 30 }` (returns a top-level array of
rows `order_date, region, qty, defect_qty`) and `defect_summary` with args `{}` (returns
`{ items: [{ defect_type, cnt }] }`). The editor said nothing about the period, so `days` is a
viewer control (7/30/90, initialised to the analysis value 30). It covers: two tabs, a period
control, an insight card + KPI row + region bar chart + daily trend all fed by **one**
`list_orders` call per interaction, a donut fed by `defect_summary`, a detail table, per-card
loading/error/empty states, a stale-response guard, chart instances reused across
interactions, and pure `transform*` functions. **Copy the structure, swap in your own
connector/tools/args/columns** -- on-page copy stays Traditional Chinese.

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>近 30 日訂單品質分析</title>
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
        <h1 class="text-2xl font-semibold tracking-tight">近 30 日訂單品質分析</h1>
      </div>
      <p class="text-slate-300 text-sm mt-1">依區域與日期的出貨與不良統計（開啟頁面時即時取得，期間可調整）</p>
    </div>
  </div>
</header>

<nav class="w-full bg-white border-b border-slate-200 shadow-sm" role="tablist">
  <div class="max-w-7xl mx-auto px-8 flex gap-1">
    <button onclick="showTab(0)" id="tab-0" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-blue-600 text-slate-900 transition-all">總覽</button>
    <button onclick="showTab(1)" id="tab-1" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-800 transition-all">明細資料</button>
  </div>
</nav>

<main class="max-w-7xl mx-auto px-8 py-6">

  <div id="panel-0">
    <!-- section: controls -->
    <div class="flex items-center gap-3 mb-6">
      <label for="select-days" class="text-sm font-medium text-slate-600">期間</label>
      <select id="select-days" class="text-sm border border-slate-300 rounded-lg px-2 py-1">
        <option value="7">近 7 日</option>
        <option value="30" selected>近 30 日</option>
        <option value="90">近 90 日</option>
      </select>
    </div>
    <!-- section: insight -->
    <div class="bg-amber-50 border border-amber-200 border-l-4 border-l-amber-400 rounded-lg p-4 text-sm text-amber-900 mb-6">
      <span class="inline-flex items-center gap-1.5 font-semibold">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 1 4 10.5c-.6.5-1 1.2-1 2v.5H9v-.5c0-.8-.4-1.5-1-2A6 6 0 0 1 12 3z"/></svg>
        自動洞察：</span>
      <span id="insight-text">載入中…</span>
    </div>

    <!-- section: kpi -->
    <section class="grid grid-cols-3 gap-4 mb-6">
      <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-blue-500">
        <p class="text-xs text-slate-500 font-medium">總出貨量</p>
        <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-total-qty">…</p>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-rose-500">
        <p class="text-xs text-slate-500 font-medium">總不良數</p>
        <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-defect-qty">…</p>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-emerald-500">
        <p class="text-xs text-slate-500 font-medium">整體良率</p>
        <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-yield">…</p>
      </div>
    </section>

    <!-- section: charts -->
    <section class="grid grid-cols-2 gap-4 mb-6">
      <div id="card-yield-by-region" class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
        <h3 class="text-sm font-semibold text-slate-700 mb-3">各區域良率(%)</h3>
        <p data-slot="loading" class="text-sm text-slate-400">載入中…</p>
        <p data-slot="error" class="hidden text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2"></p>
        <div data-slot="content" class="hidden"><div id="chart-yield-by-region" class="h-72"></div></div>
      </div>
      <div id="card-defect-share" class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
        <h3 class="text-sm font-semibold text-slate-700 mb-3">不良類型佔比</h3>
        <p data-slot="loading" class="text-sm text-slate-400">載入中…</p>
        <p data-slot="error" class="hidden text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2"></p>
        <div data-slot="content" class="hidden"><div id="chart-defect-share" class="h-72"></div></div>
      </div>
    </section>

    <!-- section: daily-trend -->
    <section id="card-daily-trend" class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
      <h3 class="text-sm font-semibold text-slate-700 mb-3">每日良率趨勢(%)</h3>
      <p data-slot="loading" class="text-sm text-slate-400">載入中…</p>
      <p data-slot="error" class="hidden text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2"></p>
      <div data-slot="content" class="hidden"><div id="chart-daily-trend" class="h-72"></div></div>
    </section>
  </div>

  <div id="panel-1" class="hidden">
    <!-- section: detail-table -->
    <section id="card-detail" class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
      <h3 class="text-sm font-semibold text-slate-700 mb-3">訂單明細</h3>
      <p data-slot="loading" class="text-sm text-slate-400">載入中…</p>
      <p data-slot="error" class="hidden text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2"></p>
      <div data-slot="content" class="hidden overflow-x-auto">
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

// connector 回來的字串進 innerHTML 前一律跳脫。
const escapeHtml = s => String(s).replace(/[&<>"']/g, ch =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

function byId(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error('[ERD] no element with id "' + id + '"');
  return el;
}

// JSON 物件對不存在的欄名回 undefined 不會丟錯——用 col() 讓打錯字立刻炸、進修復流程。
function col(row, name) {
  if (!(name in row)) throw new Error('[ERD] no column "' + name + '" in row');
  return row[name];
}

// 卡片三態:loading → (error | content)。error 訊息一律 textContent。
function setCardState(card, state, message) {
  card.querySelector('[data-slot=loading]').classList.toggle('hidden', state !== 'loading');
  const errorSlot = card.querySelector('[data-slot=error]');
  errorSlot.classList.toggle('hidden', state !== 'error');
  errorSlot.textContent = state === 'error' ? String(message) : '';
  card.querySelector('[data-slot=content]').classList.toggle('hidden', state !== 'content');
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

// ---- 純函式 transform:rows → 圖表/卡片要的陣列,不碰 DOM ----
function summarizeTotals(rows) {
  let qty = 0, defect = 0;
  for (const row of rows) { qty += Number(col(row, 'qty')); defect += Number(col(row, 'defect_qty')); }
  return { qty, defect, yieldPct: qty === 0 ? null : (1 - defect / qty) * 100 };
}

function summarizeYieldByRegion(rows) {
  const acc = new Map();
  for (const row of rows) {
    const region = String(col(row, 'region'));
    const entry = acc.get(region) || { qty: 0, defect: 0 };
    entry.qty += Number(col(row, 'qty')); entry.defect += Number(col(row, 'defect_qty'));
    acc.set(region, entry);
  }
  return [...acc.entries()]
    .map(([region, entry]) => ({ region, yieldPct: entry.qty === 0 ? null : (1 - entry.defect / entry.qty) * 100 }))
    .sort((left, right) => (left.yieldPct ?? 0) - (right.yieldPct ?? 0));
}

function summarizeDailyYield(rows) {
  const acc = new Map();
  for (const row of rows) {
    const day = String(col(row, 'order_date')).substring(0, 10);
    const entry = acc.get(day) || { qty: 0, defect: 0 };
    entry.qty += Number(col(row, 'qty')); entry.defect += Number(col(row, 'defect_qty'));
    acc.set(day, entry);
  }
  return [...acc.entries()].sort().map(([day, entry]) => [day, entry.qty === 0 ? null : (1 - entry.defect / entry.qty) * 100]);
}

// ---- render:只收 transform 的輸出;互動會重畫,同一容器只 init 一次 ----
let yieldByRegionChart = null;
let dailyTrendChart = null;

function renderYieldByRegion(byRegion) {
  if (!yieldByRegionChart) {
    yieldByRegionChart = echarts.init(byId('chart-yield-by-region'), 'erd');
    window.addEventListener('resize', () => yieldByRegionChart.resize());
  }
  yieldByRegionChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: v => fmt(v) + '%' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: byRegion.map(entry => entry.region) },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
    series: [{ type: 'bar', data: byRegion.map(entry => entry.yieldPct) }]
  }, true);                                 // notMerge:換期間時舊 series 不殘留
}

function renderDailyTrend(points) {
  const values = points.map(point => point[1]).filter(value => value !== null);
  const dataMin = Math.min(...values), dataMax = Math.max(...values);
  const margin = (dataMax - dataMin) * 0.1 || 1;
  if (!dailyTrendChart) {
    dailyTrendChart = echarts.init(byId('chart-daily-trend'), 'erd');
    window.addEventListener('resize', () => dailyTrendChart.resize());
  }
  dailyTrendChart.setOption({
    tooltip: { trigger: 'axis', valueFormatter: v => fmt(v) + '%' },
    grid: { left: '3%', right: '4%', bottom: 60, containLabel: true },
    xAxis: { type: 'time', axisLabel: { formatter: value => echarts.format.formatTime('MM-dd', value) } },
    yAxis: { type: 'value', min: Math.floor(dataMin - margin), max: Math.ceil(dataMax + margin), axisLabel: { formatter: '{value}%' } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 5 }],
    series: [{ type: 'line', data: points, showSymbol: false }]
  }, true);
}

function renderDefectShare(items) {
  const chart = echarts.init(byId('chart-defect-share'), 'erd');
  chart.setOption({
    tooltip: { trigger: 'item' },
    legend: { top: 5 },
    series: [{
      type: 'pie', radius: ['45%', '70%'], center: ['50%', '56%'],
      label: { formatter: '{b}: {c} ({d}%)' },
      data: items.map(item => ({ name: String(col(item, 'defect_type')), value: Number(col(item, 'cnt')) }))
    }]
  });
  window.addEventListener('resize', () => chart.resize());
}

function renderDetailTable({ headId, bodyId, columns, rows }) {
  byId(headId).innerHTML = `<tr>${columns.map(name => `<th class="py-2 pr-4">${escapeHtml(name)}</th>`).join('')}</tr>`;
  // null 顯示空白,不顯示假的 0;字串一律跳脫。
  byId(bodyId).innerHTML = rows.map(row =>
    `<tr>${columns.map(name => `<td class="py-2 pr-4">${col(row, name) == null ? '' : escapeHtml(fmt(col(row, name)))}</td>`).join('')}</tr>`
  ).join('');
}

// 資料集 1:訂單——洞察、KPI、區域圖、趨勢圖、明細表全部共用這一次呼叫。
// `days` 由檢視者選(編輯者未說固定),初始值 30 = 分析時的值;開頁與 change 走同一條路。
let ordersRequestId = 0;

function loadOrders() {
  const select = byId('select-days');
  const days = Number(select.value);
  const cards = [byId('card-yield-by-region'), byId('card-daily-trend'), byId('card-detail')];
  const requestId = ++ordersRequestId;
  const finish = () => { select.disabled = false; };
  select.disabled = true;
  cards.forEach(card => setCardState(card, 'loading'));
  byId('insight-text').textContent = '載入中…';

  // connector id / tool / arg key 逐字複製自分析階段實際呼叫過的 sales_list_orders。
  mcp('sales', 'list_orders', { days: days }, r => {
    if (requestId !== ordersRequestId) return;               // 已被更新的選取取代
    if (r.error) {
      console.warn('[ERD] sales/list_orders failed:', r.error.message);
      cards.forEach(card => setCardState(card, 'error', r.error.message));
      byId('kpi-total-qty').textContent = '—';
      byId('kpi-defect-qty').textContent = '—';
      byId('kpi-yield').textContent = '—';
      byId('insight-text').textContent = '資料載入失敗';
      finish(); return;
    }
    try {
      const rows = r.data;                                   // 分析時看到的是頂層陣列
      if (rows.length === 0) {
        cards.forEach(card => setCardState(card, 'error', '（無資料）'));
        byId('kpi-total-qty').textContent = '—';
        byId('kpi-defect-qty').textContent = '—';
        byId('kpi-yield').textContent = '—';
        byId('insight-text').textContent = `近 ${days} 日沒有訂單資料。`;
        finish(); return;
      }
      const totals = summarizeTotals(rows);
      const byRegion = summarizeYieldByRegion(rows);
      const worst = byRegion[0];

      byId('kpi-total-qty').textContent = fmt(totals.qty);
      byId('kpi-defect-qty').textContent = fmt(totals.defect);
      byId('kpi-yield').textContent = totals.yieldPct === null ? '（資料缺失）' : `${fmt(totals.yieldPct)}%`;
      byId('insight-text').textContent = worst.yieldPct === null
        ? '各區域出貨量為 0,無法計算良率。'
        : `近 ${days} 日 ${worst.region} 良率 ${fmt(worst.yieldPct)}%,為各區域最低(整體 ${fmt(totals.yieldPct)}%),建議優先排查。`;

      setCardState(byId('card-yield-by-region'), 'content');   // 先顯示再 init,容器才有高度
      renderYieldByRegion(byRegion);
      setCardState(byId('card-daily-trend'), 'content');
      renderDailyTrend(summarizeDailyYield(rows));
      setCardState(byId('card-detail'), 'content');
      renderDetailTable({ headId: 'detail-table-head', bodyId: 'detail-table-body',
                          columns: ['order_date', 'region', 'qty', 'defect_qty'], rows });
      finish();
    } catch (error) {
      console.error('[ERD] list_orders cards failed:', error);
      cards.forEach(card => setCardState(card, 'error', '圖表載入失敗'));
      finish();
      setTimeout(() => { throw error; }, 0);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  showTab(0);
  byId('select-days').addEventListener('change', loadOrders);
  loadOrders();                                              // 開頁即載入分析時的預設期間

  // 資料集 2:不良類型彙總——獨立呼叫、獨立卡片;它失敗不影響上面的卡。
  mcp('sales', 'defect_summary', {}, r => {
    const card = byId('card-defect-share');
    if (r.error) {
      console.warn('[ERD] sales/defect_summary failed:', r.error.message);
      setCardState(card, 'error', r.error.message);
      return;
    }
    try {
      const items = r.data.items;                            // 分析時看到的是 { items: [...] }
      if (items.length === 0) { setCardState(card, 'error', '（無資料）'); return; }
      setCardState(card, 'content');
      renderDefectShare(items);
    } catch (error) {
      console.error('[ERD] card defect-share failed:', error);
      setCardState(card, 'error', '圖表載入失敗');
      setTimeout(() => { throw error; }, 0);
    }
  });
});
</script>
</body>
</html>
```
