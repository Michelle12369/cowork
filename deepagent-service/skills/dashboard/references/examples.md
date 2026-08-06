# Examples

The two examples below are both complete, directly-renderable dashboard.html files. **Copy the
structure, replace the content with your own analysis** -- the title, column candidate strings,
KPI copy, and insight card text all need to be swapped for your own analysis results, and the
tableIds (`q1`/`q2`/...) need to be swapped for the ids you actually got back from `run_sql`.

Note: the code blocks below are reused verbatim as templates, so the on-page copy inside them
(titles, KPI labels, insight text) stays in Traditional Chinese -- see SKILL.md/html-contract.md:
all dashboard copy MUST be Traditional Chinese, and these examples exist to demonstrate that
correctly.

Both examples follow: the CDN whitelist, `echarts.init(el, 'erd')`, no embedded data values, and
a resize handler on every chart. If this is your first time building a dashboard, read through
this whole file before you start writing.

## Example (a) -- the basics: KPI row + a single bar chart + a detail table

Good for: one analysis question, one grouped-aggregation result (`q1`), one detail dataset
(`q2`).

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>各產線良率總覽</title>
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
        <h1 class="text-2xl font-semibold tracking-tight">各產線良率總覽</h1>
      </div>
      <p class="text-slate-300 text-sm mt-1">依產線分組的良率統計</p>
    </div>
    <span class="text-xs bg-slate-700 text-slate-200 px-3 py-1.5 rounded-full">資料截至 2026-07-29</span>
  </div>
</header>

<main class="max-w-7xl mx-auto px-8 py-6">

  <!-- section: insight -->
  <div class="bg-amber-50 border border-amber-200 border-l-4 border-l-amber-400 rounded-lg p-4 text-sm text-amber-900 mb-6">
    <span class="inline-flex items-center gap-1.5 font-semibold">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 1 4 10.5c-.6.5-1 1.2-1 2v.5H9v-.5c0-.8-.4-1.5-1-2A6 6 0 0 1 12 3z"/></svg>
      自動洞察：</span>
    <span id="insight-text"></span>
  </div>

  <!-- section: kpi -->
  <section class="grid grid-cols-3 gap-4 mb-6">
    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-blue-500">
      <p class="text-xs text-slate-500 font-medium">平均良率</p>
      <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-avg-yield"></p>
    </div>
    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-emerald-500">
      <p class="text-xs text-slate-500 font-medium">最高良率產線</p>
      <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-best-line"></p>
      <span class="inline-block mt-2 text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700">表現最佳</span>
    </div>
    <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 border-l-4 border-l-amber-500">
      <p class="text-xs text-slate-500 font-medium">最低良率產線</p>
      <p class="text-2xl font-semibold text-slate-800 mt-1" id="kpi-worst-line"></p>
      <span class="inline-block mt-2 text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700">需留意</span>
    </div>
  </section>

  <!-- section: charts -->
  <section class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
    <h3 class="text-sm font-semibold text-slate-700 mb-3">各產線良率(%)</h3>
    <div id="chart-yield-by-line" class="h-72"></div>
  </section>

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

</main>

<script>
// 數字格式:唯一的 toFixed 出現點——cell 可能是字串/null,先 Number() 強轉,轉不動原樣顯示
// (見 chart-rules「Number formatting」);極小值走有效位數,免得 0.0007 顯示成 0.00。
const fmt = v => {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (Number.isInteger(n)) return n.toLocaleString();
  const abs = Math.abs(n);
  return (abs !== 0 && abs < 0.01) ? n.toPrecision(3) : n.toFixed(2);
};

function getCol(columns, ...candidates) {
  for (const c of candidates) { const i = columns.indexOf(c); if (i >= 0) return i; }
  console.warn('[ERD] column not found:', candidates); return -1;
}

document.addEventListener('DOMContentLoaded', () => {
  const summary = window.__ERD_RESULTS__['q1'];
  const detail = window.__ERD_RESULTS__['q2'];

  const lineIdx = getCol(summary.columns, 'production_line', 'line', '產線');
  const yieldIdx = getCol(summary.columns, 'yield_rate', 'yield', '良率');

  // -1 代表綁定寫錯了，回頭修候選欄名或改讀對的 query result；這個分支只是不讓頁面炸掉。
  if (lineIdx === -1 || yieldIdx === -1) {
    console.error('[ERD] column binding is wrong for chart yield-by-line');
  } else {
    const lines = summary.rows.map(r => String(r[lineIdx]));
    const yields = summary.rows.map(r => Number(r[yieldIdx]));

    const avg = yields.reduce((a, b) => a + b, 0) / yields.length;
    const bestIndex = yields.indexOf(Math.max(...yields));
    const worstIndex = yields.indexOf(Math.min(...yields));
    document.getElementById('kpi-avg-yield').textContent = fmt(avg) + '%';
    document.getElementById('kpi-best-line').textContent = lines[bestIndex] + '(' + fmt(yields[bestIndex]) + '%)';
    document.getElementById('kpi-worst-line').textContent = lines[worstIndex] + '(' + fmt(yields[worstIndex]) + '%)';
    document.getElementById('insight-text').textContent =
      lines[worstIndex] + ' 良率為 ' + fmt(yields[worstIndex]) + '%,低於平均 ' + fmt(avg) + '%,建議優先排查。';

    try {
      const yieldChart = echarts.init(document.getElementById('chart-yield-by-line'), 'erd');
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
      setTimeout(() => { throw error; }, 0);  // re-throw async: surfaces to window.onerror for the repair flow without killing subsequent charts
    }
  }

  const detailHeadRow = document.getElementById('detail-table-head');
  const detailBody = document.getElementById('detail-table-body');
  detailHeadRow.innerHTML = '<tr>' + detail.columns.map(c => '<th class="py-2 pr-4">' + c + '</th>').join('') + '</tr>';
  detailBody.innerHTML = detail.rows.map(row =>
    '<tr>' + row.map(cell => '<td class="py-2 pr-4">' + cell + '</td>').join('') + '</tr>'
  ).join('');
});
</script>
</body>
</html>
```

## Example (b) -- advanced: tabs + a half-width chart pair + an insight card

Good for: multiple analysis angles presented as separate tabs, two comparable charts side by
side half-width (`q1`/`q2`), plus one supporting dataset (`q3`).

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>製程監控儀表板</title>
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
        <h1 class="text-2xl font-semibold tracking-tight">製程監控儀表板</h1>
      </div>
      <p class="text-slate-300 text-sm mt-1">量測趨勢與分布</p>
    </div>
    <span class="text-xs bg-slate-700 text-slate-200 px-3 py-1.5 rounded-full">資料截至 2026-07-29</span>
  </div>
</header>

<nav class="w-full bg-white border-b border-slate-200 shadow-sm" role="tablist">
  <div class="max-w-7xl mx-auto px-8 flex gap-1">
    <button onclick="showTab(0)" id="tab-0" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-blue-600 text-slate-900 transition-all">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg>
      趨勢分析</button>
    <button onclick="showTab(1)" id="tab-1" role="tab"
      class="inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-800 transition-all">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16"/><path d="M10 4v16"/></svg>
      明細資料</button>
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
        <h3 class="text-sm font-semibold text-slate-700 mb-3">量測值趨勢</h3>
        <div id="chart-trend" class="h-72"></div>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
        <h3 class="text-sm font-semibold text-slate-700 mb-3">分布直方圖</h3>
        <div id="chart-histogram" class="h-72"></div>
      </div>
    </section>
  </div>

  <div id="panel-1" class="hidden">
    <!-- section: detail-table -->
    <section class="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-6">
      <h3 class="text-sm font-semibold text-slate-700 mb-3">原始明細</h3>
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
// 數字格式:唯一的 toFixed 出現點——cell 可能是字串/null,先 Number() 強轉,轉不動原樣顯示
// (見 chart-rules「Number formatting」);極小值走有效位數,免得 0.0007 顯示成 0.00。
const fmt = v => {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (Number.isInteger(n)) return n.toLocaleString();
  const abs = Math.abs(n);
  return (abs !== 0 && abs < 0.01) ? n.toPrecision(3) : n.toFixed(2);
};

function getCol(columns, ...candidates) {
  for (const c of candidates) { const i = columns.indexOf(c); if (i >= 0) return i; }
  console.warn('[ERD] column not found:', candidates); return -1;
}

function showTab(idx) {
  document.querySelectorAll('[role=tab]').forEach((tabButton, index) => {
    tabButton.className = 'inline-flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-all ' +
      (index === idx ? 'border-blue-600 text-slate-900' : 'border-transparent text-slate-500 hover:text-slate-800');
    document.getElementById('panel-' + index).classList.toggle('hidden', index !== idx);
  });
  window.dispatchEvent(new Event('resize'));
}

document.addEventListener('DOMContentLoaded', () => {
  const trend = window.__ERD_RESULTS__['q1'];
  const histogram = window.__ERD_RESULTS__['q2'];
  const detail = window.__ERD_RESULTS__['q3'];

  const timeIdx = getCol(trend.columns, 'timestamp', 'time', '時間');
  const valueIdx = getCol(trend.columns, 'measurement', 'value', '量測值');

  // -1 代表綁定寫錯了，回頭修候選欄名或改讀對的 query result；這個分支只是不讓頁面炸掉。
  if (timeIdx === -1 || valueIdx === -1) {
    console.error('[ERD] column binding is wrong for chart trend');
  } else {
    const times = trend.rows.map(r => String(r[timeIdx]));
    const values = trend.rows.map(r => Number(r[valueIdx]));
    const dataMin = Math.min(...values);
    const dataMax = Math.max(...values);
    const margin = (dataMax - dataMin) * 0.1 || 1;

    try {
      const trendChart = echarts.init(document.getElementById('chart-trend'), 'erd');
      trendChart.setOption({
        tooltip: { trigger: 'axis', valueFormatter: v => fmt(v) },
        grid: { left: '3%', right: '4%', bottom: 60, containLabel: true },
        xAxis: { type: 'category', data: times, axisLabel: { formatter: v => String(v).substring(0, 10), rotate: 30 } },
        yAxis: { type: 'value', min: Math.floor(dataMin - margin), max: Math.ceil(dataMax + margin) },
        dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 5 }],
        series: [{ type: 'line', data: values, showSymbol: false }]
      });
      window.addEventListener('resize', () => trendChart.resize());
    } catch (error) {
      console.error('[ERD] chart trend failed:', error);
      setTimeout(() => { throw error; }, 0);  // re-throw async: surfaces to window.onerror for the repair flow without killing subsequent charts
    }

    const worstDeviation = values.reduce((worst, v, i) =>
      Math.abs(v - dataMin) > Math.abs(worst.deviation) ? { index: i, deviation: v - dataMin } : worst,
      { index: 0, deviation: 0 });
    document.getElementById('insight-text').textContent =
      '量測值範圍為 ' + fmt(dataMin) + ' 至 ' + fmt(dataMax) + ',於 ' + times[worstDeviation.index] + ' 出現最大偏移。';
  }

  const binIdx = getCol(histogram.columns, 'bucket', 'bin', '區間');
  const countIdx = getCol(histogram.columns, 'count', '次數');

  // -1 代表綁定寫錯了，回頭修候選欄名或改讀對的 query result；這個分支只是不讓頁面炸掉。
  if (binIdx === -1 || countIdx === -1) {
    console.error('[ERD] column binding is wrong for chart histogram');
  } else {
    const bins = histogram.rows.map(r => String(r[binIdx]));
    const counts = histogram.rows.map(r => Number(r[countIdx]));

    try {
      const histogramChart = echarts.init(document.getElementById('chart-histogram'), 'erd');
      histogramChart.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: v => fmt(v) + ' 筆' },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { type: 'category', data: bins },
        yAxis: { type: 'value' },
        series: [{ type: 'bar', data: counts, barCategoryGap: '10%' }]
      });
      window.addEventListener('resize', () => histogramChart.resize());
    } catch (error) {
      console.error('[ERD] chart histogram failed:', error);
      setTimeout(() => { throw error; }, 0);  // re-throw async: surfaces to window.onerror for the repair flow without killing subsequent charts
    }
  }

  const detailHeadRow = document.getElementById('detail-table-head');
  const detailBody = document.getElementById('detail-table-body');
  detailHeadRow.innerHTML = '<tr>' + detail.columns.map(c => '<th class="py-2 pr-4">' + c + '</th>').join('') + '</tr>';
  detailBody.innerHTML = detail.rows.map(row =>
    '<tr>' + row.map(cell => '<td class="py-2 pr-4">' + cell + '</td>').join('') + '</tr>'
  ).join('');

  showTab(0);
});
</script>
</body>
</html>
```
