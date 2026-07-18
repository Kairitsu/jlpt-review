/** Learning overview charts. */
/* global Chart, api, esc, setChrome, view */

const STATS_FONT_FAMILY = '"Noto Sans SC", system-ui, -apple-system, sans-serif';
const STATS_COLORS = {
  newCount: '#1b8f7b',
  reviewCount: '#78b7a5',
  dueCount: '#c77b32',
  forgotten: '#c65f59',
  uncertain: '#d39b3b',
  recognized: '#4f9b78',
  mastered: '#176b5a',
  duration: '#267d74',
  veryStrong: '#176b5a',
  strong: '#4f9b78',
  atRisk: '#d39b3b',
  priority: '#c65f59',
  untracked: '#9aaca8',
};
const CALENDAR_VIEWS = {
  quantity: '学习数量',
  performance: '学习表现',
  duration: '学习时长',
};
const statsCharts = {};
const statsState = {
  data: null,
  calendarView: 'quantity',
  hiddenCalendarSeries: {
    quantity: new Set(),
    performance: new Set(),
    duration: new Set(),
  },
  hiddenMasteryGroups: new Set(),
};

function destroyChart(name) {
  const chart = statsCharts[name];
  if (!chart) return;
  try { chart.destroy(); } catch { /* Already detached. */ }
  delete statsCharts[name];
}

function destroyStatsCharts() {
  Object.keys(statsCharts).forEach(destroyChart);
}

function clearStatsCache() { statsState.data = null; }

function formatPercent(value) {
  if (value == null || !Number.isFinite(Number(value))) return '暂无比例';
  const number = Number(value);
  return `${Number.isInteger(number) ? number : number.toFixed(1)}%`;
}

function formatDurationMs(value) {
  const ms = Math.max(0, Number(value) || 0);
  if (!ms) return '0 分钟';
  if (ms < 60_000) {
    const seconds = Math.max(1, Math.round(ms / 1000));
    return `不足 1 分钟（${seconds} 秒）`;
  }
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  if (!seconds || seconds === 60) return `${minutes + (seconds === 60 ? 1 : 0)} 分钟`;
  return `${minutes} 分钟 ${seconds} 秒`;
}

function dateLabel(day) {
  return [day.monthDay, day.weekday, day.relativeLabel];
}

function ratingGroup(day, key) {
  return day.actual?.ratings?.groups?.find(group => group.key === key) || null;
}

function masteryGroup(key) {
  return statsState.data?.memoryMastery?.groups?.find(group => group.key === key) || null;
}

function tooltipOptions(callbacks) {
  return {
    backgroundColor: 'rgba(22, 63, 55, .96)',
    titleColor: '#fff',
    bodyColor: '#f3fbf8',
    footerColor: '#d9f0e9',
    borderColor: 'rgba(217, 240, 233, .42)',
    borderWidth: 1,
    cornerRadius: 12,
    padding: 12,
    displayColors: true,
    boxPadding: 5,
    callbacks,
  };
}

function baseChartOptions() {
  if (typeof Chart !== 'undefined') Chart.defaults.font.family = STATS_FONT_FAMILY;
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : {duration: 240},
    interaction: {mode: 'index', intersect: false},
    plugins: {legend: {display: false}},
  };
}

const todayBandPlugin = {
  id: 'statsTodayBand',
  beforeDatasetsDraw(chart) {
    const x = chart.scales.x;
    if (!x || !chart.chartArea) return;
    const center = x.getPixelForValue(2);
    const previous = x.getPixelForValue(1);
    const next = x.getPixelForValue(3);
    const half = Math.min(Math.abs(center - previous), Math.abs(next - center)) / 2;
    const {ctx, chartArea} = chart;
    ctx.save();
    ctx.fillStyle = 'rgba(27, 143, 123, .075)';
    ctx.fillRect(center - half, chartArea.top, half * 2, chartArea.bottom - chartArea.top);
    ctx.restore();
  },
};

function axisTitle(text) {
  return {display: true, text, color: '#68827d', font: {size: 12, weight: '600'}};
}

function calendarSeries(viewName) {
  if (viewName === 'quantity') {
    return [
      {key: 'newCount', label: '新学句数'},
      {key: 'reviewCount', label: '复习句数'},
      {key: 'dueCount', label: '到期句数'},
    ];
  }
  if (viewName === 'performance') {
    return [
      {key: 'forgotten', label: '忘记'},
      {key: 'uncertain', label: '模糊'},
      {key: 'recognized', label: '认识'},
      {key: 'mastered', label: '轻松掌握'},
    ];
  }
  return [{key: 'duration', label: '学习时长'}];
}

function seriesControlsHtml(viewName) {
  const hidden = statsState.hiddenCalendarSeries[viewName];
  const buttons = calendarSeries(viewName).map(item => {
    const visible = !hidden.has(item.key);
    return `<button class="stats-filter ${visible ? 'active' : ''}" type="button" data-action="stats-series" data-series="${item.key}" aria-pressed="${visible}" aria-label="${visible ? '隐藏' : '显示'}${esc(item.label)}"><i style="--series-color:${STATS_COLORS[item.key]}" aria-hidden="true"></i>${esc(item.label)}</button>`;
  }).join('');
  return `${buttons}<button class="stats-restore" type="button" data-action="stats-restore-series">全部显示</button>`;
}

function viewControlsHtml() {
  return Object.entries(CALENDAR_VIEWS).map(([key, label]) => `<button class="stats-view-button ${statsState.calendarView === key ? 'active' : ''}" type="button" data-action="stats-view" data-view="${key}" aria-pressed="${statsState.calendarView === key}">${label}</button>`).join('');
}

function quantitySummary(day) {
  const due = day.due;
  if (!day.actual) {
    return `${day.relativeLabel}，实际学习尚未发生；预计到期 ${due?.count ?? 0} 句`;
  }
  const actual = day.actual;
  const parts = [
    `完成 ${actual.completedCount} 句`,
    `新学 ${actual.newCount} 句`,
    `复习 ${actual.reviewCount} 句`,
  ];
  if (due?.kind === 'current') parts.push(`当前待复习 ${due.count} 句`);
  return `${day.relativeLabel}，${parts.join('；')}`;
}

function performanceSummary(day) {
  if (!day.actual) return `${day.relativeLabel}，日期尚未发生，没有实际学习表现数据`;
  const ratings = day.actual.ratings;
  if (!ratings.validCount) return `${day.relativeLabel}，没有正式生成的学习结果`;
  const groups = ratings.groups.map(group => `${group.label} ${group.count} 句（${formatPercent(group.percentage)}）`);
  return `${day.relativeLabel}，${groups.join('；')}；有效评分共 ${ratings.validCount} 条`;
}

function durationSummary(day) {
  if (!day.actual) return `${day.relativeLabel}，日期尚未发生，没有实际学习时长`;
  return `${day.relativeLabel}，学习时长 ${formatDurationMs(day.actual.durationMs)}`;
}

function calendarSummaryText(day) {
  if (statsState.calendarView === 'quantity') return quantitySummary(day);
  if (statsState.calendarView === 'performance') return performanceSummary(day);
  return durationSummary(day);
}

function calendarSummaryHtml() {
  return statsState.data.timeline.map((day, index) => `<div class="stats-day-summary stats-data-point ${day.isToday ? 'today' : ''}" tabindex="0" data-chart="calendar" data-index="${index}" aria-label="${esc(calendarSummaryText(day))}"><strong>${esc(day.relativeLabel)}</strong><span>${esc(day.monthDay)} · ${esc(day.weekday)}</span><small>${esc(calendarSummaryText(day).replace(`${day.relativeLabel}，`, ''))}</small></div>`).join('');
}

function calendarHasData() {
  const actualDays = statsState.data.timeline.filter(day => day.actual);
  if (statsState.calendarView === 'performance') {
    return actualDays.some(day => day.actual.ratings.validCount > 0);
  }
  if (statsState.calendarView === 'duration') {
    return actualDays.some(day => day.actual.durationMs > 0);
  }
  return actualDays.some(day => day.actual.completedCount > 0)
    || statsState.data.timeline.some(day => day.due?.count > 0);
}

function calendarDatasets() {
  const timeline = statsState.data.timeline;
  if (statsState.calendarView === 'quantity') {
    return [
      {
        key: 'newCount', label: '新学句数', type: 'bar', stack: 'completed',
        data: timeline.map(day => day.actual?.newCount ?? null),
        backgroundColor: STATS_COLORS.newCount, borderRadius: 7, borderSkipped: false,
        maxBarThickness: 42,
      },
      {
        key: 'reviewCount', label: '复习句数', type: 'bar', stack: 'completed',
        data: timeline.map(day => day.actual?.reviewCount ?? null),
        backgroundColor: STATS_COLORS.reviewCount, borderRadius: 7, borderSkipped: false,
        maxBarThickness: 42,
      },
      {
        key: 'dueCount', label: '到期句数', type: 'line',
        data: timeline.map(day => day.due?.count ?? null),
        borderColor: STATS_COLORS.dueCount, backgroundColor: '#fff8e8',
        pointBackgroundColor: STATS_COLORS.dueCount, pointBorderColor: '#fff',
        pointBorderWidth: 2, pointRadius: 5, pointHoverRadius: 7,
        borderWidth: 2.5, tension: 0.28, spanGaps: false,
      },
    ];
  }
  if (statsState.calendarView === 'performance') {
    return calendarSeries('performance').map(item => ({
      key: item.key,
      label: item.label,
      type: 'bar',
      stack: 'ratings',
      data: timeline.map(day => {
        const group = ratingGroup(day, item.key);
        return group?.percentage ?? null;
      }),
      backgroundColor: STATS_COLORS[item.key],
      borderSkipped: false,
      borderRadius: 5,
      maxBarThickness: 54,
    }));
  }
  return [{
    key: 'duration', label: '学习时长', type: 'line',
    data: timeline.map(day => day.actual == null ? null : day.actual.durationMs / 60_000),
    borderColor: STATS_COLORS.duration, backgroundColor: 'rgba(38, 125, 116, .13)',
    fill: true, pointBackgroundColor: STATS_COLORS.duration, pointBorderColor: '#fff',
    pointBorderWidth: 2, pointRadius: 5, pointHoverRadius: 7,
    borderWidth: 2.5, tension: 0.28, spanGaps: false,
  }];
}

function calendarTooltipCallbacks() {
  if (statsState.calendarView === 'quantity') {
    return {
      label(context) {
        const day = statsState.data.timeline[context.dataIndex];
        if (context.dataset.key === 'dueCount') {
          const label = day.due?.kind === 'current' ? '当前待复习' : '预计到期';
          return `${label}：${day.due?.count ?? 0} 句`;
        }
        return `${context.dataset.label}：${context.raw} 句`;
      },
      footer(items) {
        const actual = statsState.data.timeline[items[0]?.dataIndex]?.actual;
        return actual ? `当天完成共 ${actual.completedCount} 句` : '';
      },
    };
  }
  if (statsState.calendarView === 'performance') {
    return {
      label(context) {
        const day = statsState.data.timeline[context.dataIndex];
        const group = ratingGroup(day, context.dataset.key);
        return group ? `${group.label}：${group.count} 句（${formatPercent(group.percentage)}）` : '';
      },
      footer(items) {
        const ratings = statsState.data.timeline[items[0]?.dataIndex]?.actual?.ratings;
        return ratings ? `当天有效评分共 ${ratings.validCount} 条` : '';
      },
    };
  }
  return {
    label(context) {
      const ms = statsState.data.timeline[context.dataIndex]?.actual?.durationMs;
      return `学习时长：${formatDurationMs(ms)}`;
    },
  };
}

function renderCalendarChart() {
  destroyChart('calendar');
  const canvas = document.getElementById('chart-learning-calendar');
  if (!canvas || typeof Chart === 'undefined') return;
  const viewName = statsState.calendarView;
  const hidden = statsState.hiddenCalendarSeries[viewName];
  const options = baseChartOptions();
  options.plugins.tooltip = tooltipOptions(calendarTooltipCallbacks());
  options.scales = {
    x: {
      stacked: viewName !== 'duration',
      grid: {display: false},
      border: {display: false},
      ticks: {
        color: context => context.index === 2 ? '#146b5a' : '#68827d',
        font: context => ({size: window.innerWidth <= 480 ? 10 : 12, weight: context.index === 2 ? '700' : '500'}),
        autoSkip: false,
        maxRotation: 0,
      },
    },
    y: {
      stacked: viewName !== 'duration', beginAtZero: true,
      suggestedMax: viewName === 'performance' ? 100 : undefined,
      max: viewName === 'performance' ? 100 : undefined,
      grid: {color: 'rgba(104, 130, 125, .13)'},
      border: {display: false},
      ticks: {
        color: '#68827d',
        precision: viewName === 'duration' ? undefined : 0,
        callback: value => viewName === 'performance' ? `${value}%` : value,
      },
      title: axisTitle(viewName === 'performance' ? '占有效评分比例' : (viewName === 'duration' ? '分钟' : '句子数')),
    },
  };
  statsCharts.calendar = new Chart(canvas, {
    data: {
      labels: statsState.data.timeline.map(dateLabel),
      datasets: calendarDatasets(),
    },
    options,
    plugins: [todayBandPlugin],
  });
  statsCharts.calendar.data.datasets.forEach((dataset, index) => {
    statsCharts.calendar.setDatasetVisibility(index, !hidden.has(dataset.key));
  });
  statsCharts.calendar.update('none');
}

function renderCalendarView() {
  const controls = document.getElementById('stats-calendar-series');
  const summary = document.getElementById('stats-calendar-summary');
  const empty = document.getElementById('stats-calendar-empty');
  const gradeHint = document.getElementById('stats-grade-hint');
  if (controls) controls.innerHTML = seriesControlsHtml(statsState.calendarView);
  if (summary) summary.innerHTML = calendarSummaryHtml();
  if (empty) {
    const copy = statsState.calendarView === 'performance'
      ? '这五天内还没有正式生成的学习结果。'
      : (statsState.calendarView === 'duration' ? '这五天内还没有学习时长记录。' : '目前没有学习记录或待复习安排。');
    empty.textContent = copy;
    empty.classList.toggle('hidden', calendarHasData());
  }
  if (gradeHint) gradeHint.classList.toggle('hidden', statsState.calendarView !== 'performance');
  renderCalendarChart();
}

function masteryControlsHtml() {
  const groups = statsState.data.memoryMastery.groups;
  const buttons = groups.map(group => {
    const visible = !statsState.hiddenMasteryGroups.has(group.key);
    return `<button class="stats-filter ${visible ? 'active' : ''}" type="button" data-action="stats-memory-series" data-series="${group.key}" aria-pressed="${visible}" aria-label="${visible ? '隐藏' : '显示'}${esc(group.label)}"><i style="--series-color:${STATS_COLORS[group.key]}" aria-hidden="true"></i>${esc(group.label)}</button>`;
  }).join('');
  return `${buttons}<button class="stats-restore" type="button" data-action="stats-restore-memory">全部显示</button>`;
}

function masterySummaryText(group) {
  const ratio = group.includedInPercentage
    ? `占有效记录 ${formatPercent(group.percentage)}`
    : '不计入掌握度比例';
  return `${group.label}，${group.count} 句，${ratio}，${group.status}`;
}

function masteryListHtml() {
  return statsState.data.memoryMastery.groups.map((group, index) => `<div class="stats-mastery-item stats-data-point" tabindex="0" data-chart="mastery" data-index="${index}" aria-label="${esc(masterySummaryText(group))}"><i style="--series-color:${STATS_COLORS[group.key]}" aria-hidden="true"></i><div><strong>${esc(group.label)}</strong><span>${group.count} 句 · ${group.includedInPercentage ? formatPercent(group.percentage) : '不计入掌握度比例'}</span><small>${esc(group.status)}</small></div></div>`).join('');
}

function renderMasteryChart() {
  destroyChart('mastery');
  const canvas = document.getElementById('chart-memory-mastery');
  const empty = document.getElementById('stats-memory-empty');
  const mastery = statsState.data.memoryMastery;
  const hasSentences = mastery.totalSentenceCount > 0;
  if (empty) empty.classList.toggle('hidden', hasSentences);
  if (canvas) canvas.classList.toggle('hidden', !hasSentences);
  if (!canvas || !hasSentences || typeof Chart === 'undefined') return;
  const groups = mastery.groups;
  const options = baseChartOptions();
  options.indexAxis = 'y';
  options.interaction = {mode: 'nearest', axis: 'y', intersect: false};
  options.plugins.tooltip = tooltipOptions({
    label(context) {
      const group = groups[context.dataIndex];
      return `${group.count} 句 · ${group.includedInPercentage ? formatPercent(group.percentage) : '不计入掌握度比例'}`;
    },
    afterLabel(context) { return groups[context.dataIndex].status; },
  });
  options.scales = {
    x: {
      beginAtZero: true,
      grid: {color: 'rgba(104, 130, 125, .13)'},
      border: {display: false},
      ticks: {precision: 0, color: '#68827d'},
      title: axisTitle('句子数'),
    },
    y: {
      grid: {display: false}, border: {display: false},
      ticks: {color: '#365b54', font: {size: window.innerWidth <= 480 ? 10 : 12, weight: '600'}},
    },
  };
  statsCharts.mastery = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: groups.map(group => group.label),
      datasets: [{
        label: '句子数',
        data: groups.map(group => statsState.hiddenMasteryGroups.has(group.key) ? null : group.count),
        backgroundColor: groups.map(group => STATS_COLORS[group.key]),
        borderRadius: 8, borderSkipped: false, maxBarThickness: 34,
      }],
    },
    options,
  });
}

function renderMasteryView() {
  const controls = document.getElementById('stats-memory-series');
  const list = document.getElementById('stats-memory-list');
  if (controls) controls.innerHTML = masteryControlsHtml();
  if (list) list.innerHTML = masteryListHtml();
  renderMasteryChart();
}

function statsPageHtml(data) {
  const mastery = data.memoryMastery;
  const timezoneLabel = data.timezone.name || '服务器本地时区';
  return `<section class="page stats-page">
    <div class="page-head"><div><h1>学习概览</h1><p>查看近期学习情况、未来复习安排和当前记忆状态。</p></div></div>
    <article class="card stats-card stats-calendar-card">
      <div class="stats-card-head"><div><h2>学习与复习日历</h2><p>过去两天、今天，以及未来两天的安排</p></div><span class="stats-timezone" title="统计生成时间：${esc(data.generatedAt)}">${esc(timezoneLabel)}</span></div>
      <div class="stats-view-switch" role="group" aria-label="日历图表视图">${viewControlsHtml()}</div>
      <div id="stats-calendar-series" class="stats-series-controls" aria-label="日历图表显示项目"></div>
      <p id="stats-calendar-empty" class="stats-empty-inline hidden" role="status"></p>
      <div class="stats-chart-wrap stats-calendar-chart"><canvas id="chart-learning-calendar" role="img" aria-label="学习与复习日历图表" aria-describedby="stats-calendar-summary"></canvas></div>
      <div id="stats-calendar-summary" class="stats-day-summaries" aria-label="学习与复习日历数值摘要"></div>
      <div id="stats-grade-hint" class="stats-grade-hint hidden">
        <p><strong>忘记：</strong>第一次核对错误，第二次仍未正确，或没有完成纠正；</p>
        <p><strong>模糊：</strong>第一次核对错误，第二次核对正确；</p>
        <p><strong>认识：</strong>本次第一次核对正确，但尚未形成连续稳定表现；</p>
        <p><strong>轻松掌握：</strong>本次第一次核对正确，并且上一次练习也是第一次核对正确。</p>
      </div>
      <p class="stats-footnote">未来复习数量依据当前学习进度估算，完成新的练习后可能发生变化。</p>
    </article>
    <article class="card stats-card stats-memory-card">
      <div class="stats-card-head"><div><h2>当前记忆掌握度</h2><p>有效记录 ${mastery.effectiveSentenceCount} 句 · 尚无有效记录 ${mastery.untrackedSentenceCount} 句</p></div></div>
      <div id="stats-memory-series" class="stats-series-controls stats-memory-controls" aria-label="记忆掌握度显示范围"></div>
      <p id="stats-memory-empty" class="stats-empty-inline hidden" role="status">还没有已学习的句子，完成学习后这里会显示记忆状态。</p>
      <div class="stats-chart-wrap stats-memory-chart"><canvas id="chart-memory-mastery" role="img" aria-label="当前记忆掌握度图表" aria-describedby="stats-memory-list"></canvas></div>
      <div id="stats-memory-list" class="stats-mastery-list" aria-label="当前记忆掌握度文字摘要"></div>
      <p class="stats-footnote">系统根据每个句子的历史表现和距上次复习的时间，估算你现在仍能正确回忆它的概率。这个数值会随时间变化，并在完成复习后重新计算。</p>
    </article>
  </section>`;
}

async function renderStats() {
  destroyStatsCharts();
  view.innerHTML = '<section class="page stats-page"><p class="status-note">正在整理学习概览…</p></section>';
  setChrome();
  try {
    statsState.data = await api('/api/stats/summary');
    view.innerHTML = statsPageHtml(statsState.data);
    renderCalendarView();
    renderMasteryView();
  } catch (error) {
    statsState.data = null;
    view.innerHTML = `<section class="page"><p class="error-text">${esc(error.message)}</p></section>`;
  }
}

function updateCalendarSeriesVisibility() {
  const chart = statsCharts.calendar;
  if (!chart) return;
  const hidden = statsState.hiddenCalendarSeries[statsState.calendarView];
  chart.data.datasets.forEach((dataset, index) => chart.setDatasetVisibility(index, !hidden.has(dataset.key)));
  chart.update();
}

function handleStatsAction(action, button) {
  if (!statsState.data) return false;
  if (action === 'stats-view') {
    const requested = button.dataset.view;
    if (!CALENDAR_VIEWS[requested] || requested === statsState.calendarView) return true;
    statsState.calendarView = requested;
    const switcher = button.closest('.stats-view-switch');
    switcher?.querySelectorAll('[data-action="stats-view"]').forEach(item => {
      const active = item.dataset.view === requested;
      item.classList.toggle('active', active);
      item.setAttribute('aria-pressed', String(active));
    });
    renderCalendarView();
    return true;
  }
  if (action === 'stats-series') {
    const hidden = statsState.hiddenCalendarSeries[statsState.calendarView];
    const key = button.dataset.series;
    hidden.has(key) ? hidden.delete(key) : hidden.add(key);
    document.getElementById('stats-calendar-series').innerHTML = seriesControlsHtml(statsState.calendarView);
    updateCalendarSeriesVisibility();
    return true;
  }
  if (action === 'stats-restore-series') {
    statsState.hiddenCalendarSeries[statsState.calendarView].clear();
    document.getElementById('stats-calendar-series').innerHTML = seriesControlsHtml(statsState.calendarView);
    updateCalendarSeriesVisibility();
    return true;
  }
  if (action === 'stats-memory-series') {
    const key = button.dataset.series;
    statsState.hiddenMasteryGroups.has(key)
      ? statsState.hiddenMasteryGroups.delete(key)
      : statsState.hiddenMasteryGroups.add(key);
    renderMasteryView();
    return true;
  }
  if (action === 'stats-restore-memory') {
    statsState.hiddenMasteryGroups.clear();
    renderMasteryView();
    return true;
  }
  return false;
}

function setChartPointActive(point, active) {
  const chart = statsCharts[point.dataset.chart];
  const index = Number(point.dataset.index);
  if (!chart || !Number.isInteger(index)) return;
  if (!active) {
    chart.setActiveElements([]);
    chart.tooltip?.setActiveElements([], {x: 0, y: 0});
    chart.update('none');
    return;
  }
  const elements = chart.data.datasets.flatMap((dataset, datasetIndex) => {
    const visible = chart.isDatasetVisible(datasetIndex);
    return visible && dataset.data[index] != null ? [{datasetIndex, index}] : [];
  });
  chart.setActiveElements(elements);
  if (elements.length) {
    const element = chart.getDatasetMeta(elements[0].datasetIndex).data[index];
    chart.tooltip?.setActiveElements(elements, {x: element.x, y: element.y});
  }
  chart.update('none');
}

document.addEventListener('focusin', event => {
  const point = event.target.closest?.('.stats-data-point');
  if (point) setChartPointActive(point, true);
});
document.addEventListener('focusout', event => {
  const point = event.target.closest?.('.stats-data-point');
  if (point) setChartPointActive(point, false);
});
document.addEventListener('mouseover', event => {
  const point = event.target.closest?.('.stats-data-point');
  if (point) setChartPointActive(point, true);
});
document.addEventListener('mouseout', event => {
  const point = event.target.closest?.('.stats-data-point');
  if (point && !point.contains(event.relatedTarget)) setChartPointActive(point, false);
});
document.addEventListener('keydown', event => {
  const point = event.target.closest?.('.stats-data-point');
  if (!point || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
  const points = [...point.parentElement.querySelectorAll('.stats-data-point')];
  const current = points.indexOf(point);
  const backward = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
  points[(current + (backward ? -1 : 1) + points.length) % points.length]?.focus();
  event.preventDefault();
});

window.renderStats = renderStats;
window.handleStatsAction = handleStatsAction;
window.destroyStatsCharts = destroyStatsCharts;
window.clearStatsCache = clearStatsCache;
