/**
 * Stats page: forgetting curve, learning situation, memory retention.
 * Depends on Chart.js (window.Chart) and app.js globals (api, esc, route, toast, setChrome, state).
 */
/* global Chart, api, esc, route, toast, setChrome, state, view */

const STATS_COLORS = {
  primary: '#1cb0a0',
  primaryDeep: '#146b5a',
  orange: '#e8a23a',
  red: '#d35a4a',
  lightGreen: '#7bc47f',
  midGreen: '#3aa87a',
  deepGreen: '#1f6b4a',
  known: '#53a88f',
  fuzzy: '#e0b35a',
  forgotten: '#b94848',
  mastered: '#287a54',
  muted: '#68827d',
  grid: 'rgba(23,59,53,.08)',
};

/** One-screen bucket caps; mid-gap empty buckets count. */
const VISIBLE_BUCKETS = { day: 14, week: 12, month: 12 };
const PX_PER_BUCKET = 48;

const statsState = {
  tab: 'curve',
  learningMode: 'cognitive', // cognitive | newreview
  learningGranularity: 'day',
  retentionGranularity: 'week',
  charts: {},
  resizeObservers: {},
};

function isNarrowStatsViewport() {
  return typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(max-width: 480px)').matches;
}

function destroyStatsCharts() {
  Object.keys(statsState.resizeObservers).forEach(key => {
    try { statsState.resizeObservers[key].disconnect(); } catch (_) { /* ignore */ }
    delete statsState.resizeObservers[key];
  });
  Object.keys(statsState.charts).forEach(key => {
    try { statsState.charts[key].destroy(); } catch (_) { /* ignore */ }
    delete statsState.charts[key];
  });
}

function statsChartOptions(yMax, yTitle) {
  const narrow = isNarrowStatsViewport();
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        position: 'bottom',
        labels: { boxWidth: 12, padding: 14, color: STATS_COLORS.muted, font: { size: 12 } },
      },
      tooltip: { callbacks: {} },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: {
          color: STATS_COLORS.muted,
          maxRotation: narrow ? 45 : 0,
          minRotation: narrow ? 30 : 0,
          autoSkip: true,
          font: { size: narrow ? 10 : 11 },
        },
      },
      y: {
        beginAtZero: true,
        max: yMax,
        title: yTitle ? { display: true, text: yTitle, color: STATS_COLORS.muted } : undefined,
        grid: { color: STATS_COLORS.grid },
        ticks: { color: STATS_COLORS.muted, font: { size: 11 } },
      },
    },
  };
}

/**
 * Apply horizontal scroll when bucket count exceeds the visible threshold.
 * Keeps per-bucket pixel width fixed (no scale compression). Scrolls to the right (today).
 */
function applyChartScrollLayout(scrollEl, wrapEl, bucketCount, granularity) {
  if (!scrollEl || !wrapEl) return;
  const threshold = VISIBLE_BUCKETS[granularity] || VISIBLE_BUCKETS.day;
  const n = Math.max(0, bucketCount | 0);
  if (n > threshold) {
    const widthPx = Math.max(n * PX_PER_BUCKET, threshold * PX_PER_BUCKET);
    wrapEl.style.width = `${widthPx}px`;
    wrapEl.style.minWidth = `${widthPx}px`;
  } else {
    wrapEl.style.width = '100%';
    wrapEl.style.minWidth = '100%';
  }
  // Defer so layout has applied after Chart draws.
  requestAnimationFrame(() => {
    scrollEl.scrollLeft = scrollEl.scrollWidth;
  });
}

function bindChartResize(key, wrapEl, chart) {
  if (!wrapEl || !chart || typeof ResizeObserver === 'undefined') return;
  const ro = new ResizeObserver(() => {
    try { chart.resize(); } catch (_) { /* ignore */ }
  });
  ro.observe(wrapEl);
  statsState.resizeObservers[key] = ro;
}

function renderStatsShell() {
  const tabs = [
    { id: 'curve', label: '遗忘曲线' },
    { id: 'learning', label: '学习情况' },
    { id: 'retention', label: '记忆持久度' },
  ];
  return `<section class="page stats-page">
    <div class="stats-top">
      <div class="stats-tabs" role="tablist" aria-label="统计子页">
        ${tabs.map(t => `<button type="button" role="tab" class="stats-tab ${statsState.tab === t.id ? 'active' : ''}" data-action="stats-tab" data-tab="${t.id}" aria-selected="${statsState.tab === t.id}">${t.label}</button>`).join('')}
      </div>
    </div>
    <div id="stats-panel" class="stats-panel"></div>
  </section>`;
}

async function renderStats() {
  destroyStatsCharts();
  view.innerHTML = renderStatsShell();
  setChrome();
  const panel = document.getElementById('stats-panel');
  if (!panel) return;
  panel.innerHTML = '<p class="status-note">加载中…</p>';
  try {
    if (statsState.tab === 'curve') await renderForgettingCurve(panel);
    else if (statsState.tab === 'learning') await renderLearningStats(panel);
    else await renderRetentionStats(panel);
  } catch (error) {
    panel.innerHTML = `<p class="error-text">${esc(error.message)}</p>`;
  }
}

async function renderForgettingCurve(panel) {
  const data = await api('/api/stats/forgetting-curve');
  const labels = data.points.map(p => p.label);
  const theory = data.points.map(p => p.theory);
  const user = data.points.map(p => (p.user == null ? p.theory : p.user));
  const note = data.dataReady
    ? '坚持复习的时间越久，你的遗忘曲线统计将越精准。'
    : '数据积累中，正逐步校准';

  panel.innerHTML = `
    <div class="card stats-card">
      <div class="stats-chart-scroll" id="curve-scroll">
        <div class="stats-chart-wrap" id="curve-wrap"><canvas id="chart-curve" aria-label="遗忘曲线图"></canvas></div>
      </div>
      <div class="stats-legend-row">
        <span class="legend-item"><i style="background:${STATS_COLORS.orange}"></i>你的学习遗忘曲线</span>
        <span class="legend-item"><i style="background:${STATS_COLORS.primary}"></i>艾宾浩斯遗忘曲线</span>
      </div>
      <p class="stats-footnote">${esc(note)}</p>
    </div>`;

  const ctx = document.getElementById('chart-curve');
  const wrap = document.getElementById('curve-wrap');
  if (!ctx || typeof Chart === 'undefined') return;
  const opts = statsChartOptions(100, '记忆保持率 %');
  opts.plugins.legend.display = false;
  opts.scales.y.ticks.callback = v => `${v}%`;
  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: '你的学习遗忘曲线',
          data: user,
          borderColor: STATS_COLORS.orange,
          backgroundColor: 'rgba(232,162,58,.12)',
          spanGaps: true,
          tension: 0.3,
          pointRadius: (c) => (c.dataIndex === 0 ? 6 : 3),
          pointBackgroundColor: STATS_COLORS.orange,
          pointStyle: (c) => (c.dataIndex === 0 ? 'rect' : 'circle'),
        },
        {
          label: '艾宾浩斯遗忘曲线',
          data: theory,
          borderColor: STATS_COLORS.primary,
          backgroundColor: 'transparent',
          tension: 0.3,
          pointRadius: (c) => (c.dataIndex === 0 ? 6 : 3),
          pointBackgroundColor: STATS_COLORS.primary,
          pointStyle: (c) => (c.dataIndex === 0 ? 'rect' : 'circle'),
        },
      ],
    },
    options: opts,
  });
  statsState.charts.curve = chart;
  bindChartResize('curve', wrap, chart);
}

async function renderLearningStats(panel) {
  const data = await api(`/api/stats/learning?granularity=${encodeURIComponent(statsState.learningGranularity)}`);
  const t = data.today || {};
  const mode = statsState.learningMode;
  const gran = statsState.learningGranularity;
  const series = data.series || [];

  panel.innerHTML = `
    <div class="card stats-card">
      <div class="stats-toolbar">
        <div class="stats-toggle" role="group" aria-label="展示模式">
          <button type="button" class="count-option ${mode === 'cognitive' ? 'active' : ''}" data-action="stats-learning-mode" data-mode="cognitive">认知情况</button>
          <button type="button" class="count-option ${mode === 'newreview' ? 'active' : ''}" data-action="stats-learning-mode" data-mode="newreview">复习新学</button>
        </div>
        <div class="stats-toggle" role="group" aria-label="时间粒度">
          ${['day','week','month'].map(g => `<button type="button" class="count-option ${gran === g ? 'active' : ''}" data-action="stats-learning-granularity" data-granularity="${g}">${{day:'日',week:'周',month:'月'}[g]}</button>`).join('')}
        </div>
      </div>
      <div class="stats-chart-scroll" id="learning-scroll">
        <div class="stats-chart-wrap tall" id="learning-wrap"><canvas id="chart-learning" aria-label="学习情况图"></canvas></div>
      </div>
      <div class="stats-today-grid" aria-label="今日汇总">
        <div><strong>${t.mastered || 0}</strong><span>今日熟知</span></div>
        <div><strong>${t.known || 0}</strong><span>今日认识</span></div>
        <div><strong>${t.fuzzy || 0}</strong><span>今日模糊</span></div>
        <div><strong>${t.forgotten || 0}</strong><span>今日忘记</span></div>
        <div><strong>${t.dueTotal || 0}</strong><span>今日待学</span></div>
        <div><strong>${t.durationSec || 0}s</strong><span>今日时长</span></div>
      </div>
      ${data.pressureHint ? `<button type="button" class="stats-pressure" data-action="stats-go-review">${esc(data.pressureMessage || '待复习句子较多，可分散复习减轻压力')}</button>` : ''}
    </div>`;

  const labels = series.map(s => s.label);
  let datasets;
  if (mode === 'newreview') {
    datasets = [
      { label: '新学', data: series.map(s => s.new), backgroundColor: STATS_COLORS.primary, stack: 'a' },
      { label: '复习', data: series.map(s => s.review), backgroundColor: STATS_COLORS.orange, stack: 'a' },
    ];
  } else {
    datasets = [
      { label: '熟知', data: series.map(s => s.mastered), backgroundColor: STATS_COLORS.mastered, stack: 'a' },
      { label: '认识', data: series.map(s => s.known), backgroundColor: STATS_COLORS.known, stack: 'a' },
      { label: '模糊', data: series.map(s => s.fuzzy), backgroundColor: STATS_COLORS.fuzzy, stack: 'a' },
      { label: '忘记', data: series.map(s => s.forgotten), backgroundColor: STATS_COLORS.forgotten, stack: 'a' },
    ];
  }

  const scrollEl = document.getElementById('learning-scroll');
  const wrap = document.getElementById('learning-wrap');
  applyChartScrollLayout(scrollEl, wrap, series.length, gran);

  const ctx = document.getElementById('chart-learning');
  if (!ctx || typeof Chart === 'undefined') return;
  const opts = statsChartOptions(undefined, '句子数');
  const threshold = VISIBLE_BUCKETS[gran] || 14;
  // When scrolling, show all labels more freely; otherwise auto-skip.
  if (series.length > threshold) {
    opts.scales.x.ticks.autoSkip = false;
    opts.scales.x.ticks.maxRotation = isNarrowStatsViewport() ? 60 : 45;
  } else {
    opts.scales.x.ticks.autoSkip = true;
    opts.scales.x.ticks.maxTicksLimit = gran === 'day' ? 12 : 16;
  }
  const chart = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
    options: opts,
  });
  statsState.charts.learning = chart;
  bindChartResize('learning', wrap, chart);
  applyChartScrollLayout(scrollEl, wrap, series.length, gran);
}

async function renderRetentionStats(panel) {
  const gran = statsState.retentionGranularity;
  const data = await api(`/api/stats/retention?granularity=${encodeURIComponent(gran)}`);
  const series = data.series || [];
  const last = series[series.length - 1] || {};

  panel.innerHTML = `
    <div class="card stats-card">
      <div class="stats-toolbar">
        <div class="stats-toggle" role="group" aria-label="时间粒度">
          ${['day','week','month'].map(g => `<button type="button" class="count-option ${gran === g ? 'active' : ''}" data-action="stats-retention-granularity" data-granularity="${g}">${{day:'日',week:'周',month:'月'}[g]}</button>`).join('')}
        </div>
      </div>
      <div class="stats-chart-scroll" id="retention-scroll">
        <div class="stats-chart-wrap tall" id="retention-wrap"><canvas id="chart-retention" aria-label="记忆持久度图"></canvas></div>
      </div>
      <div class="stats-retention-legend">
        <span class="legend-item"><i style="background:${STATS_COLORS.red}"></i>已加入记忆规划 ${last.all || 0}</span>
        <span class="legend-item"><i style="background:${STATS_COLORS.orange}"></i>≥10 天 ${last.d10 || 0}（${last.d10Pct || 0}%）</span>
        <span class="legend-item"><i style="background:${STATS_COLORS.lightGreen}"></i>≥30 天 ${last.d30 || 0}（${last.d30Pct || 0}%）</span>
        <span class="legend-item"><i style="background:${STATS_COLORS.primary}"></i>≥60 天 ${last.d60 || 0}（${last.d60Pct || 0}%）</span>
        <span class="legend-item"><i style="background:${STATS_COLORS.deepGreen}"></i>≥90 天 ${last.d90 || 0}（${last.d90Pct || 0}%）</span>
      </div>
      <p class="stats-footnote">记忆持久度：按当前稳定度预测，保持率仍 ≥90% 的可保持天数。</p>
    </div>`;

  const labels = series.map(s => s.label);
  const scrollEl = document.getElementById('retention-scroll');
  const wrap = document.getElementById('retention-wrap');
  applyChartScrollLayout(scrollEl, wrap, series.length, gran);

  const ctx = document.getElementById('chart-retention');
  if (!ctx || typeof Chart === 'undefined') return;
  const opts = statsChartOptions(undefined, '句子数');
  opts.plugins.legend.display = false;
  const threshold = VISIBLE_BUCKETS[gran] || 12;
  if (series.length > threshold) {
    opts.scales.x.ticks.autoSkip = false;
    opts.scales.x.ticks.maxRotation = isNarrowStatsViewport() ? 60 : 45;
  } else {
    opts.scales.x.ticks.autoSkip = true;
    opts.scales.x.ticks.maxTicksLimit = 12;
  }
  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: '全部句子', data: series.map(s => s.all), borderColor: STATS_COLORS.red, tension: 0.25, pointRadius: 2 },
        { label: '≥10 天', data: series.map(s => s.d10), borderColor: STATS_COLORS.orange, tension: 0.25, pointRadius: 2 },
        { label: '≥30 天', data: series.map(s => s.d30), borderColor: STATS_COLORS.lightGreen, tension: 0.25, pointRadius: 2 },
        { label: '≥60 天', data: series.map(s => s.d60), borderColor: STATS_COLORS.primary, tension: 0.25, pointRadius: 2 },
        { label: '≥90 天', data: series.map(s => s.d90), borderColor: STATS_COLORS.deepGreen, tension: 0.25, pointRadius: 2 },
      ],
    },
    options: opts,
  });
  statsState.charts.retention = chart;
  bindChartResize('retention', wrap, chart);
  applyChartScrollLayout(scrollEl, wrap, series.length, gran);
}

function handleStatsAction(action, button) {
  if (action === 'stats-tab') {
    statsState.tab = button.dataset.tab || 'curve';
    return renderStats();
  }
  if (action === 'stats-learning-mode') {
    statsState.learningMode = button.dataset.mode || 'cognitive';
    return renderStats();
  }
  if (action === 'stats-learning-granularity') {
    statsState.learningGranularity = button.dataset.granularity || 'day';
    return renderStats();
  }
  if (action === 'stats-retention-granularity') {
    statsState.retentionGranularity = button.dataset.granularity || 'week';
    return renderStats();
  }
  if (action === 'stats-go-review') {
    state.homeDuePicker = true;
    return route('home');
  }
  return null;
}

// Expose for app.js
window.renderStats = renderStats;
window.handleStatsAction = handleStatsAction;
window.destroyStatsCharts = destroyStatsCharts;
// Test/debug helpers (also used by docs)
window.__STATS_VISIBLE_BUCKETS = VISIBLE_BUCKETS;
window.__STATS_PX_PER_BUCKET = PX_PER_BUCKET;
