/** FSRS statistics page. */
/* global Chart, api, esc, setChrome, view */

const STATS_FONT_FAMILY = '"Noto Sans SC", system-ui, -apple-system, sans-serif';
const statsCharts = {};
let statsCache = null;

function formatDurationSec(sec) {
  const total = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(total / 60)}m${total % 60}s`;
}

function destroyStatsCharts() {
  Object.values(statsCharts).forEach(chart => { try { chart.destroy(); } catch {} });
  Object.keys(statsCharts).forEach(key => delete statsCharts[key]);
}

function clearStatsCache() { statsCache = null; }

function chartOptions(yTitle) {
  if (typeof Chart !== 'undefined') Chart.defaults.font.family = STATS_FONT_FAMILY;
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false } },
      y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: yTitle } },
    },
  };
}

function distributionCard(id, title, items, color) {
  return `<div class="card stats-card">
    <div class="section-title"><h2>${esc(title)}</h2></div>
    <div class="stats-chart-wrap tall"><canvas id="${id}" aria-label="${esc(title)}"></canvas></div>
    <div class="stats-retention-legend">${items.map(item => `<span class="legend-item"><i style="background:${color}"></i>${esc(item.label)}：${item.count}</span>`).join('')}</div>
  </div>`;
}

async function renderStats() {
  destroyStatsCharts();
  view.innerHTML = '<section class="page stats-page"><p class="status-note">加载中…</p></section>';
  setChrome();
  try {
    statsCache ||= await api('/api/stats/summary');
    const data = statsCache;
    const today = data.today || {};
    const ratings = today.ratings || {};
    const forecast = data.forecast || {};
    const retention = data.retentionPct == null ? '暂无' : `${data.retentionPct}%`;
    view.innerHTML = `<section class="page stats-page">
      <div class="page-head"><div><h1>FSRS 统计</h1><p>基于官方 FSRS Card 状态与最终复习记录。</p></div></div>
      <div class="card stats-card">
        <div class="stats-today-grid" aria-label="今日汇总">
          <div><strong>${today.learned || 0}</strong><span>今日学习</span></div>
          <div><strong>${today.reviewed || 0}</strong><span>今日复习</span></div>
          <div><strong>${ratings.again || 0}</strong><span>忘记</span></div>
          <div><strong>${ratings.hard || 0}</strong><span>模糊</span></div>
          <div><strong>${ratings.good || 0}</strong><span>认识</span></div>
          <div><strong>${ratings.easy || 0}</strong><span>轻松掌握</span></div>
          <div><strong>${data.dueNow || 0}</strong><span>当前待复习</span></div>
          <div><strong>${retention}</strong><span>预计保持率</span></div>
          <div><strong>${formatDurationSec(today.durationSec || 0)}</strong><span>今日时长</span></div>
        </div>
      </div>
      <div class="card stats-card">
        <div class="section-title"><h2>预计复习</h2></div>
        <div class="stats-today-grid">
          <div><strong>${forecast.days7 || 0}</strong><span>未来 7 天</span></div>
          <div><strong>${forecast.days30 || 0}</strong><span>未来 30 天</span></div>
          <div><strong>${forecast.days90 || 0}</strong><span>未来 90 天</span></div>
        </div>
        <p class="stats-footnote">未来窗口为累计数量，不含已经到期的句子。</p>
      </div>
      ${distributionCard('chart-stability', 'Stability 分布', data.stabilityDistribution || [], '#1cb0a0')}
      ${distributionCard('chart-difficulty', 'Difficulty 分布', data.difficultyDistribution || [], '#e8a23a')}
      <p class="stats-footnote">当前使用 FSRS ${esc(data.fsrs.version)}，目标保持率 ${Math.round(data.fsrs.desiredRetention * 100)}%。预计保持率由官方 FSRS 对已学习卡片计算。</p>
    </section>`;

    if (typeof Chart === 'undefined') return;
    const stability = data.stabilityDistribution || [];
    const difficulty = data.difficultyDistribution || [];
    const stabilityCtx = document.getElementById('chart-stability');
    const difficultyCtx = document.getElementById('chart-difficulty');
    if (stabilityCtx) statsCharts.stability = new Chart(stabilityCtx, {
      type: 'bar', data: { labels: stability.map(x => x.label), datasets: [{ data: stability.map(x => x.count), backgroundColor: '#1cb0a0' }] }, options: chartOptions('句子数'),
    });
    if (difficultyCtx) statsCharts.difficulty = new Chart(difficultyCtx, {
      type: 'bar', data: { labels: difficulty.map(x => x.label), datasets: [{ data: difficulty.map(x => x.count), backgroundColor: '#e8a23a' }] }, options: chartOptions('句子数'),
    });
  } catch (error) {
    view.innerHTML = `<section class="page"><p class="error-text">${esc(error.message)}</p></section>`;
  }
}

function handleStatsAction() { return null; }

window.renderStats = renderStats;
window.handleStatsAction = handleStatsAction;
window.destroyStatsCharts = destroyStatsCharts;
window.clearStatsCache = clearStatsCache;
