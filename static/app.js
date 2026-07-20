const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const view = $('#view');
const state = {
  route: 'home', dashboard: null,
  activeCollection: Number(localStorage.getItem('activeCollection') || 0),
  draft: null, selectedChunks: [], editing: null, practice: null, report: null,
  routeMeta: {}, homeDuePicker: false,
  timezone: '',
};

function esc(value = '') { return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function rubyHtml(segments) {
  if (!Array.isArray(segments) || !segments.length
    || !segments.every(seg => seg && typeof seg.text === 'string' && seg.text.length
      && (seg.ruby === undefined || typeof seg.ruby === 'string'))) return '';
  return segments.map(seg => seg.ruby
    ? `<ruby>${esc(seg.text)}<rt>${esc(seg.ruby)}</rt></ruby>`
    : esc(seg.text)).join('');
}
function chunkRubyHtml(sentence, chunk) {
  const text = typeof chunk?.text === 'string' ? chunk.text : '';
  const fallback = esc(text);
  try {
    const japanese = sentence?.japanese;
    const segments = sentence?.furigana;
    const start = chunk?.start, end = chunk?.end;
    if (typeof japanese !== 'string' || !Array.isArray(segments) || !segments.length
      || !Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start >= end) return fallback;

    const sentenceChars = Array.from(japanese);
    if (end > sentenceChars.length || sentenceChars.slice(start, end).join('') !== text) return fallback;
    if (!segments.every(seg => seg && typeof seg.text === 'string' && seg.text.length
      && (seg.ruby === undefined || typeof seg.ruby === 'string'))) return fallback;
    if (segments.map(seg => seg.text).join('') !== japanese) return fallback;

    const sliced = [];
    let cursor = 0;
    for (const segment of segments) {
      const segmentChars = Array.from(segment.text);
      const segmentStart = cursor, segmentEnd = cursor + segmentChars.length;
      cursor = segmentEnd;
      if (segmentEnd <= start || segmentStart >= end) continue;
      const localStart = Math.max(start, segmentStart) - segmentStart;
      const localEnd = Math.min(end, segmentEnd) - segmentStart;
      const part = segmentChars.slice(localStart, localEnd).join('');
      if (!part) continue;
      const wholeSegment = localStart === 0 && localEnd === segmentChars.length;
      sliced.push(wholeSegment && segment.ruby ? {text:part, ruby:segment.ruby} : {text:part});
    }
    if (sliced.map(seg => seg.text).join('') !== text) return fallback;
    return rubyHtml(sliced) || fallback;
  } catch {
    return fallback;
  }
}
function formatDate(value) {
  if (!value) return '从未';
  const opts = { dateStyle: 'medium', timeStyle: 'short' };
  if (state.timezone) opts.timeZone = state.timezone;
  return new Intl.DateTimeFormat('zh-CN', opts).format(new Date(value));
}

const COMMON_TIMEZONES = ['Asia/Shanghai', 'Asia/Singapore', 'Asia/Tokyo', 'UTC'];
const TZ_REGION_LABELS = {
  Africa: '非洲', America: '美洲', Antarctica: '南极洲', Arctic: '北极',
  Asia: '亚洲', Atlantic: '大西洋', Australia: '大洋洲', Europe: '欧洲',
  Indian: '印度洋', Pacific: '太平洋', Etc: 'UTC 偏移 / 其他',
};

function detectBrowserTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch { return ''; }
}
function tzOffsetLabel(tz) {
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'shortOffset' }).formatToParts(new Date());
    return (parts.find(p => p.type === 'timeZoneName') || {}).value || '';
  } catch { return ''; }
}
function tzDisplayLabel(tz) {
  const city = tz.split('/').pop().replace(/_/g, ' ');
  const offset = tzOffsetLabel(tz);
  return offset ? `${city}（${offset}）` : city;
}
function allTimezones() {
  if (typeof Intl.supportedValuesOf === 'function') {
    try { return Intl.supportedValuesOf('timeZone'); } catch { /* 继续走兜底 */ }
  }
  return COMMON_TIMEZONES; // 极老旧浏览器兜底，至少保留常用项可选
}
function timezoneOptionsHtml(selected) {
  const detected = detectBrowserTimezone();
  const common = [...new Set(detected ? [...COMMON_TIMEZONES, detected] : COMMON_TIMEZONES)];
  let html = '<optgroup label="常用">';
  html += `<option value="" ${!selected ? 'selected' : ''}>跟随服务器时区（默认）</option>`;
  for (const tz of common) {
    const suffix = tz === detected ? '　·　本设备当前时区' : '';
    html += `<option value="${tz}" ${selected === tz ? 'selected' : ''}>${esc(tzDisplayLabel(tz))}${suffix}</option>`;
  }
  html += '</optgroup>';

  const groups = {};
  for (const tz of allTimezones()) {
    const region = tz.includes('/') ? tz.split('/')[0] : 'Etc';
    (groups[region] ||= []).push(tz);
  }
  for (const region of Object.keys(groups).sort()) {
    const label = TZ_REGION_LABELS[region] || region;
    html += `<optgroup label="${esc(label)}">`;
    for (const tz of groups[region].sort()) {
      html += `<option value="${tz}" ${selected === tz ? 'selected' : ''}>${esc(tzDisplayLabel(tz))}</option>`;
    }
    html += '</optgroup>';
  }
  return html;
}
async function loadTimezoneState() {
  try { state.timezone = (await api('/api/settings/timezone')).timezone || ''; }
  catch { /* 未登录或请求失败时静默忽略，访问设置页时会重新加载一次 */ }
}

function toast(message, error = false) { const el = $('#toast'); el.textContent = message; el.className = `toast${error ? ' error' : ''}`; clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.add('hidden'), 3600); }
async function api(url, options = {}) {
  const response = await fetch(url, {headers:{'Content-Type':'application/json', ...(options.headers || {})}, ...options});
  let data = {}; try { data = await response.json(); } catch {}
  if (response.status === 401 && data.authRequired) { showLogin(); const error = new Error('请先登录'); error.data = data; throw error; }
  if (!response.ok) { const error = new Error(data.error || `请求失败 (${response.status})`); error.data = data; error.status = response.status; throw error; }
  return data;
}
function showLogin() { $('#login-modal').classList.remove('hidden'); }
function hideLogin() { $('#login-modal').classList.add('hidden'); }
function openDialog(html, { className = '', label = '对话框' } = {}) { const el = $('#dialog'); el.innerHTML = `<div class="modal ${className}" role="dialog" aria-modal="true" aria-label="${esc(label)}">${html}</div>`; el.classList.remove('hidden'); requestAnimationFrame(() => $('input, select, button', el)?.focus()); }
function closeDialog() { const el = $('#dialog'); el.classList.add('hidden'); el.innerHTML = ''; }
function librarySelectionState(checkedStates) {
  const states = Array.isArray(checkedStates) ? checkedStates.map(Boolean) : [];
  const selected = states.filter(Boolean).length;
  return {
    total: states.length,
    selected,
    allSelected: Boolean(states.length) && selected === states.length,
    selectLabel: states.length && selected === states.length ? '取消全选' : '全选',
    selectDisabled: !states.length,
    selectionActionsDisabled: !selected,
  };
}
function nextLibrarySelection(checkedStates) {
  const selection = librarySelectionState(checkedStates);
  return (checkedStates || []).map(() => !selection.allSelected);
}
function selectedSentenceIds() { return $$('.sentence-check:checked').map(input => Number(input.value)); }
function updateLibrarySelectionButtons() {
  const checks = $$('.sentence-check');
  const selection = librarySelectionState(checks.map(input => input.checked));
  const selectBtn = $('#select-all-btn');
  if (selectBtn) { selectBtn.textContent = selection.selectLabel; selectBtn.disabled = selection.selectDisabled; }
  for (const selector of ['#rechunk-selected-btn', '#move-selected-btn']) {
    const button = $(selector); if (button) button.disabled = selection.selectionActionsDisabled;
  }
}
function toggleVisibleSentenceSelection() {
  const checks = $$('.sentence-check');
  const nextStates = nextLibrarySelection(checks.map(input => input.checked));
  checks.forEach((input, index) => { input.checked = nextStates[index]; });
  updateLibrarySelectionButtons();
}
function openManageCollectionDialog() {
  const current = state.dashboard.collections.find(c => c.id === state.activeCollection);
  if (!current) return;
  const onlyOne = state.dashboard.collections.length <= 1;
  const n = current.total || 0;
  openDialog(`<h1>管理句集</h1><p>${esc(current.name)} · 共 ${n} 句</p><label>重命名<input id="rename-collection-name" value="${esc(current.name)}"></label><div class="form-actions"><button class="btn outline" data-action="close-dialog">取消</button><button class="btn primary" data-action="rename-collection">保存名称</button></div><div style="margin-top:22px;padding-top:18px;border-top:1px solid var(--border)"><p class="status-note" style="text-align:left;margin:0 0 12px">删除前可用「转移选中句子」把有用的句子移到别处。</p>${onlyOne ? '<p class="status-note" style="text-align:left;margin:0 0 12px">至少保留一个句集，无法删除。</p>' : ''}<div class="form-actions"><button class="btn danger" data-action="delete-collection-ask" ${onlyOne ? 'disabled' : ''}>删除句集</button></div></div>`);
}
function openDeleteCollectionConfirm() {
  const current = state.dashboard.collections.find(c => c.id === state.activeCollection);
  if (!current) return;
  const n = current.total || 0;
  openDialog(`<h1>确认删除</h1><p style="text-align:left">删除句集会同时删除其中的全部 ${n} 句句子，以及这些句子的原始作答、FSRS 状态和复习记录，且不可恢复。</p><p class="status-note" style="text-align:left;margin-top:12px">可以先用批量转移把有用的句子移到别处。</p><div class="form-actions"><button class="btn outline" data-action="manage-collection">返回</button><button class="btn danger" data-action="delete-collection-confirm">确定删除</button></div>`);
}
function openDeleteReportConfirm(id) {
  openDialog(`<h1>确认删除</h1><p style="text-align:left">删除这条练习记录吗？删除后不会影响句子的记忆进度，仅移除这条历史记录，且不可恢复。</p><div class="form-actions"><button class="btn outline" data-action="close-dialog">取消</button><button class="btn danger" data-action="delete-report-confirm">确定删除</button></div>`);
  $('#dialog').dataset.reportId = id;
}
function openMoveSentencesDialog(ids) {
  const options = (state.dashboard?.collections || []).filter(c => c.id !== state.activeCollection)
    .map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
  if (!options) { toast('没有可转移的目标句集', true); return; }
  openDialog(`<h1>转移句子</h1><p>将选中的 ${ids.length} 句转移到目标句集。</p><label>目标句集<select id="move-target-collection">${options}</select></label><div class="form-actions"><button class="btn outline" data-action="close-dialog">取消</button><button class="btn primary" data-action="confirm-move-sentences">确认转移</button></div>`);
  $('#dialog').dataset.moveIds = ids.join(',');
}
function openRechunkSentencesDialog(ids) {
  openDialog(`<div class="rechunk-sentences-dialog"><h1>重新分块</h1><p class="rechunk-sentences-copy">将使用当前 GiNZA 分块规则重新生成所选 ${ids.length} 句的词块。已有的人工拆分或合并结果会被覆盖；日语原句、中文翻译、所属句集、FSRS 记忆进度和练习历史不会改变。</p><p id="rechunk-sentences-error" class="form-error rechunk-sentences-error" role="alert"></p><div class="form-actions"><button class="btn outline" data-action="close-dialog">取消</button><button class="btn primary" data-action="confirm-rechunk-sentences">确认重新分块</button></div></div>`, { className: 'rechunk-sentences-modal', label: '确认重新分块' });
  $('#dialog').dataset.rechunkIds = ids.join(',');
}
async function confirmRechunkSentences(button) {
  const ids = ($('#dialog').dataset.rechunkIds || '').split(',').filter(Boolean).map(Number);
  if (!ids.length) throw new Error('请至少勾选一条句子');
  const oldLabel = button.textContent;
  button.disabled = true;
  button.textContent = '正在重新分块…';
  const errorEl = $('#rechunk-sentences-error');
  if (errorEl) errorEl.textContent = '';
  let result;
  try {
    result = await api('/api/sentences/rechunk', {method:'POST', body:JSON.stringify({sentenceIds:ids})});
  } catch (error) {
    button.disabled = false;
    button.textContent = oldLabel;
    if (errorEl) errorEl.textContent = error.message;
    return;
  }
  closeDialog();
  toast(`已重新分块 ${result.updated} 句`);
  try { await reloadLibrary(); }
  catch (error) { toast(`重新加载列表失败：${error.message}`, true); }
}

async function openRetryRoundDialog() {
  const reportId = state.report?.id;
  if (!reportId) return;
  state.report = (await api(`/api/reports/${reportId}`)).report;
  const collection = state.report?.collection;
  const retry = state.report?.retry || {};
  const max = Number(retry.availableCount || 0);
  const unanswered = Number(retry.unansweredCount || 0);
  const collectionLabel = collection
    ? `“${esc(collection.name)}”当前可练习 <strong>${max}</strong> 句，其中包含本轮未回答 <strong>${unanswered}</strong> 句。`
    : `当前可练习 <strong>${max}</strong> 句，其中包含本轮未回答 <strong>${unanswered}</strong> 句。`;
  const picker = renderCountPicker({
    idPrefix: 'report-count',
    max,
    filterQuick: true,
    startAction: 'start-report-round',
    startLabel: '开始练习',
    groupAriaLabel: '下一轮题目数量',
    initialHint: max ? '优先选择本轮未回答题目，其余按到期顺序补充。' : '',
    includeStartButton: false,
    emptyHtml: '<span class="status-note retry-empty">当前没有可再次练习的句子。</span>',
  });
  openDialog(`<div class="retry-round-dialog"><h1>再练一轮</h1><p class="retry-collection-total">${collectionLabel}</p>${picker}<div class="form-actions"><button class="btn outline" data-action="close-dialog">取消</button><button class="btn primary" data-action="start-report-round" ${!max ? 'disabled' : ''}>开始练习</button></div></div>`, { className: 'retry-round-modal', label: '再练一轮' });
  $('#dialog').dataset.retryReportId = reportId;
}

function openExitPracticeDialog() {
  const practice = state.practice;
  if (!practice) return;
  const total = practice.sentences.length;
  const completed = completedPracticeCount(practice);
  const unanswered = Math.max(0, total - completed);
  if (!completed) {
    openDialog(`<div class="exit-practice-dialog"><h1>当前还没有完成任何题目</h1><p class="exit-practice-copy">本轮原计划 ${total} 题，目前还有 ${unanswered} 题未完成。</p><p class="status-note exit-practice-note">直接放弃不会生成练习报告，也不会更新任何 FSRS 数据。</p><div class="form-actions"><button class="btn outline" data-action="close-dialog">继续练习</button><button class="btn danger" data-action="abandon-practice">放弃本轮</button></div></div>`, { className: 'exit-practice-modal', label: '当前还没有完成任何题目' });
    return;
  }
  openDialog(`<div class="exit-practice-dialog"><h1>提前结束并提交？</h1><div class="exit-practice-counts"><div><strong>${completed}</strong><span>已经完成</span></div><div><strong>${unanswered}</strong><span>尚未完成</span></div></div><p class="exit-practice-copy">确认退出后，已完成题目会按现有评分规则保存到 FSRS，并生成正式练习报告。</p><p class="status-note exit-practice-note">未完成题目不会计入 FSRS，也不会被判定为错误或遗忘，原有记忆和到期状态保持不变。</p><p id="exit-practice-error" class="form-error exit-practice-error" role="alert"></p><div class="form-actions"><button class="btn outline" data-action="close-dialog">继续练习</button><button class="btn danger" data-action="confirm-exit-practice">提前结束并生成报告</button></div></div>`, { className: 'exit-practice-modal', label: '提前结束并提交？' });
}

function isExitPracticeDialogOpen() {
  const dialog = $('#dialog');
  return Boolean(dialog && !dialog.classList.contains('hidden') && $('.exit-practice-dialog', dialog));
}

async function confirmExitPractice(button) {
  const practice = state.practice;
  if (!practice || practice.exiting) return;
  practice.exiting = true;
  const dialog = $('#dialog');
  const buttons = $$('button', dialog);
  buttons.forEach(item => { item.disabled = true; });
  const oldLabel = button.textContent;
  button.textContent = '正在退出…';
  const errorEl = $('#exit-practice-error');
  if (errorEl) errorEl.textContent = '';
  try {
    const submission = await api(`/api/practice/sessions/${practice.sessionId}/complete`, {
      method: 'POST',
      body: JSON.stringify({
        ...roundSubmissionPayload(practice, true),
        completionMode: 'early_exit',
      }),
    });
    state.report = (await api(`/api/reports/${submission.reportId}`)).report;
    if (typeof window.clearStatsCache === 'function') window.clearStatsCache();
    practice.exiting = false;
    closeDialog();
    await route('report', { reportId: submission.reportId });
  } catch (error) {
    practice.exiting = false;
    buttons.forEach(item => { item.disabled = false; });
    button.textContent = oldLabel;
    if (errorEl) errorEl.textContent = error.message;
    toast(error.message, true);
  }
}

async function abandonPractice() {
  if (!state.practice || state.practice.exiting) return;
  abortPracticeDrag();
  state.practice = null;
  closeDialog();
  await route('home');
}

const secondaryRoutes = new Set(['library', 'add', 'reports', 'report', 'settings', 'stats', 'due', 'today']);
function setChrome(practice = false) {
  const header = $('#main-header');
  header.classList.toggle('hidden', practice);
  if (!practice) {
    const secondary = secondaryRoutes.has(state.route);
    header.innerHTML = `<button class="brand plain-button" data-action="${secondary ? 'back' : 'home'}" aria-label="${secondary ? '返回上一页' : '返回首页'}">${secondary ? '<span class="back-arrow" aria-hidden="true">←</span>' : '<span class="brand-mark">文</span>'}<strong>句子重组</strong></button><button class="icon-button" data-route="settings" aria-label="设置" title="设置"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></svg></button>`;
  }
  $('#bottom-nav').classList.toggle('hidden', practice);
  $('#fab').classList.toggle('hidden', practice || state.route === 'add' || state.route === 'report' || state.route === 'settings' || state.route === 'stats' || state.route === 'due' || state.route === 'today');
  $$('#bottom-nav button').forEach(button => button.classList.toggle('active', button.dataset.route === state.route));
}
function historyEntry(name, options = {}) {
  const entry = {route:name, collectionId:state.activeCollection, reportId:state.report?.id, editingId:state.editing?.id};
  if (name === 'due' || name === 'today') entry.fromHome = Boolean(options.fromHome);
  return entry;
}
async function route(name, options = {}) {
  const allowed = new Set(['home','library','add','reports','report','settings','practice','stats','due','today']);
  if (!allowed.has(name)) name = 'home';
  if (state.route === 'stats' && name !== 'stats' && typeof destroyStatsCharts === 'function') destroyStatsCharts();
  state.route = name; state.routeMeta = options;
  if (!options.fromPop) history[options.replace ? 'replaceState' : 'pushState'](historyEntry(name, options), '', `#${name}`);
  setChrome(name === 'practice');
  if (!options.preserveScroll) window.scrollTo(0, 0);
  try {
    if (name === 'home') await renderHome();
    else if (name === 'library') await renderLibrary(options.collectionId || state.activeCollection);
    else if (name === 'due' || name === 'today') await renderStudyStatus(name, options.collectionId || state.activeCollection);
    else if (name === 'add') {
      if (options.editingId && (!state.editing || state.editing.id !== Number(options.editingId))) state.editing = (await api(`/api/sentences/${options.editingId}`)).sentence;
      await renderAdd();
    } else if (name === 'reports') await renderReports();
    else if (name === 'settings') await renderSettings();
    else if (name === 'stats') {
      if (typeof renderStats === 'function') await renderStats();
      else view.innerHTML = '<section class="page"><p class="error-text">统计模块未加载</p></section>';
    }
    else if (name === 'practice') renderPractice();
    else if (name === 'report') {
      const id = options.reportId || state.report?.id;
      if (id && (!state.report || state.report.id !== Number(id))) state.report = (await api(`/api/reports/${id}`)).report;
      renderReport();
    }
    view.focus({preventScroll:true});
  } catch (error) { toast(error.message, true); if (!view.children.length) route('home', {replace:true}); }
}
function navigateBack() {
  if (state.route === 'due' || state.route === 'today') {
    if (history.state?.fromHome && history.length > 1) history.back();
    else route('home', {replace:true});
    return;
  }
  if (state.route === 'settings' || state.route === 'stats') return route('home');
  if (state.route === 'add') { state.editing = null; state.draft = null; return route('library'); }
  if (state.route === 'report') return route('reports');
  if (state.route === 'reports' || state.route === 'library') return route('home');
  if (history.length > 1) history.back(); else route('home', {replace:true});
}

async function ensureDashboard() {
  state.dashboard = await api('/api/dashboard');
  if (!state.activeCollection || !state.dashboard.collections.some(c => c.id === state.activeCollection)) {
    state.activeCollection = state.dashboard.collections[0]?.id || 0;
    localStorage.setItem('activeCollection', state.activeCollection);
  }
  return state.dashboard;
}
function collectionOptions(selected) { return (state.dashboard?.collections || []).map(c => `<option value="${c.id}" ${Number(selected) === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join(''); }
function setActiveCollection(collectionId) { state.activeCollection = Number(collectionId) || 0; localStorage.setItem('activeCollection', state.activeCollection); }
function dueCollectionOptions(selected) { return (state.dashboard?.collections || []).map(c => `<option value="${c.id}" ${Number(selected) === c.id ? 'selected' : ''}>${esc(c.name)} · ${c.due} 待复习</option>`).join(''); }

/** Shared practice-count picker (library + home due). idPrefix keeps DOM ids distinct. */
function countPickerIds(idPrefix) {
  if (idPrefix === 'due-count') {
    return {
      inputId: 'due-custom-count',
      hintId: 'due-count-hint',
      optionClass: 'count-option due-count-option',
      optionSelector: '.due-count-option',
      setAction: 'set-due-count',
      scope: view,
    };
  }
  if (idPrefix === 'report-count') {
    return {
      inputId: 'report-custom-count',
      hintId: 'report-count-hint',
      optionClass: 'count-option report-count-option',
      optionSelector: '.report-count-option',
      setAction: 'set-report-count',
      scope: $('#dialog') || document,
    };
  }
  return {
    inputId: 'custom-count',
    hintId: 'count-hint',
    optionClass: 'count-option',
    optionSelector: '.count-option',
    setAction: 'set-count',
    scope: document,
  };
}

function renderCountPicker({
  idPrefix,
  max,
  quickOptions = [5, 10, 20],
  filterQuick = false,
  startAction,
  startLabel,
  groupAriaLabel,
  initialHint,
  emptyHtml = null,
  includeStartButton = true,
}) {
  const cfg = countPickerIds(idPrefix);
  let optionsInner;
  if (emptyHtml != null && !max) {
    optionsInner = emptyHtml;
  } else {
    const nums = filterQuick ? quickOptions.filter(n => n <= max) : quickOptions;
    const buttons = nums.map(n => `<button class="${cfg.optionClass}" data-action="${cfg.setAction}" data-count="${n}">${n} 句</button>`).join('');
    const inputMax = idPrefix === 'count' ? Math.max(max, 1) : max;
    const startButton = includeStartButton ? `<button class="btn primary" data-action="${startAction}" ${!max ? 'disabled' : ''}>${startLabel}</button>` : '';
    optionsInner = `${buttons}<button class="${cfg.optionClass} active" data-action="${cfg.setAction}" data-count="all">全部</button><label class="custom-count">自定义<input id="${cfg.inputId}" type="number" min="1" max="${inputMax}" inputmode="numeric" placeholder="1-${max}"></label>${startButton}`;
  }
  return `<div class="count-options" role="group" aria-label="${groupAriaLabel}">${optionsInner}</div><p id="${cfg.hintId}" class="status-note">${initialHint}</p>`;
}

function selectCountOption(idPrefix, button) {
  const cfg = countPickerIds(idPrefix);
  $$(cfg.optionSelector, cfg.scope).forEach(option => option.classList.toggle('active', option === button));
  const input = $(`#${cfg.inputId}`);
  if (input) input.value = '';
}

/**
 * Sync hint / start disabled from custom input (document-level input handler calls this).
 * clearActive: when true (typing in custom field), deselect quick options first.
 * set-due-count calls with clearActive false so the clicked button stays active.
 */
function bindCountPicker(idPrefix, max, { mode = 'soft', defaultHint = '', startAction, clearActive = false } = {}) {
  const cfg = countPickerIds(idPrefix);
  const input = $(`#${cfg.inputId}`);
  const hint = $(`#${cfg.hintId}`);
  if (!input || !hint) return;

  if (mode === 'strict') {
    if (clearActive) $$(cfg.optionSelector, cfg.scope).forEach(option => option.classList.remove('active'));
    const start = startAction ? $(`[data-action="${startAction}"]`, cfg.scope) : null;
    const value = input.value.trim();
    if (!value) {
      if (start) start.disabled = false;
      hint.textContent = defaultHint || `本句集有 ${max} 句待复习，可从到期最早的句子开始。`;
      return;
    }
    const count = Number(value);
    const valid = Number.isInteger(count) && count >= 1 && count <= max;
    if (start) start.disabled = !valid;
    hint.textContent = valid ? `将复习 ${count} 句。` : `请输入 1 到 ${max} 之间的整数。`;
    return;
  }

  // soft (collection practice): only react when input has a value; never disable start
  if (!input.value) return;
  if (clearActive) $$(cfg.optionSelector, cfg.scope).forEach(option => option.classList.remove('active'));
  if (Number(input.value) > max) {
    hint.textContent = `当前句集只有 ${max} 句，开始时将自动调整为全部。`;
  } else {
    hint.textContent = `将随机练习 ${Math.max(1, Number(input.value) || 1)} 句。`;
  }
}

function readCountPickerSelection(idPrefix, { trimCustom = false } = {}) {
  const cfg = countPickerIds(idPrefix);
  const raw = $(`#${cfg.inputId}`)?.value ?? '';
  const custom = trimCustom ? raw.trim() : raw;
  const selected = $(`${cfg.optionSelector}.active`, cfg.scope)?.dataset.count || 'all';
  return { custom, selected, count: custom || selected };
}

function renderDuePicker(data, active) {
  const due = active?.due || 0;
  const picker = renderCountPicker({
    idPrefix: 'due-count',
    max: due,
    filterQuick: true,
    startAction: 'start-due-practice',
    startLabel: '开始复习',
    groupAriaLabel: '本轮待复习数量',
    initialHint: due ? `本句集有 ${due} 句待复习，可从到期最早的句子开始。` : '请选择有待复习句子的句集后再开始。',
    emptyHtml: '<span class="status-note">该句集当前没有待复习句子。</span><button class="btn primary" data-action="start-due-practice" disabled>开始复习</button>',
  });
  return `<div class="card practice-picker home-practice-picker"><div><h2>选择本轮复习</h2><p>先选句集，再决定本轮练习数量。</p><label class="field">练习句集<select id="due-collection" aria-label="练习句集">${dueCollectionOptions(active?.id)}</select></label></div>${picker}</div>`;
}

async function renderHome() {
  const data = await ensureDashboard(); const active = data.collections.find(c => c.id === state.activeCollection) || data.collections[0]; const progress = active?.total ? Math.round(active.learned * 100 / active.total) : 0;
  view.innerHTML = `<section class="page home-page"><div class="page-head"><div><h1>根据中文翻译，补全日语句子</h1></div></div><div class="card hero-card"><div class="collection-title"><span class="collection-icon">文</span><div><h2>${esc(active?.name || '还没有句集')}</h2><p>${active?.learned || 0} 已学习 / ${active?.total || 0} 总数量</p></div></div><label class="home-collection-switch">切换句集<select id="home-collection" aria-label="切换句集">${collectionOptions(active?.id)}</select></label><div class="progress" aria-label="学习进度 ${progress}%"><span style="width:${progress}%"></span></div><div class="hero-bottom" aria-label="学习状态"><button type="button" class="metric metric-button" data-route="due" aria-label="查看待复习句子，共 ${active?.due || 0} 句"><span class="metric-copy"><strong>${active?.due || 0}</strong><span>待复习</span></span><span class="metric-arrow" aria-hidden="true">›</span></button><button type="button" class="metric metric-button" data-route="today" aria-label="查看今日学习句子，共 ${active?.today || 0} 句"><span class="metric-copy"><strong>${active?.today || 0}</strong><span>今日学习</span></span><span class="metric-arrow" aria-hidden="true">›</span></button></div><button class="btn primary" data-action="start-due" ${!active?.due ? 'disabled' : ''}>开始句子重组</button></div>${state.homeDuePicker ? renderDuePicker(data, active) : ''}<div class="card section-card"><div class="section-title"><h2>句子合集</h2><button class="link-button" data-action="new-collection">＋ 新建</button></div>${data.collections.map(c => `<button class="collection-row" data-action="open-collection" data-id="${c.id}"><span class="row-icon">文</span><span class="row-main"><strong>${esc(c.name)}</strong><small>已学 ${c.learned}，共 ${c.total}</small></span><span class="arrow">›</span></button>`).join('')}</div><button class="card section-card home-stats-entry" data-route="reports"><div class="section-title"><h2>练习历史</h2><span class="arrow">›</span></div><p class="status-note" style="margin:0">每轮练习都会保留，可随时回看报告</p></button><button class="card section-card home-stats-entry" data-route="stats"><div class="section-title"><h2>学习概览</h2><span class="arrow">›</span></div><p class="status-note" style="margin:0">近期学习 · 复习安排 · 记忆掌握度</p></button></section>`;
  setChrome();
}

async function renderStudyStatus(status, collectionId) {
  const dashboard = await ensureDashboard();
  const requestedId = Number(collectionId);
  const active = dashboard.collections.find(item => item.id === requestedId)
    || dashboard.collections.find(item => item.id === state.activeCollection)
    || dashboard.collections[0];
  if (!active) throw new Error('当前没有可查看的句集');
  setActiveCollection(active.id);
  const data = await api(`/api/collections/${active.id}/study-status/${status}`);
  const isDue = status === 'due';
  const title = isDue ? '待复习句子' : '今日学习';
  const summary = isDue ? `${data.total} 句待复习` : `今日学习 ${data.total} 句`;
  const emptyText = isDue ? '当前没有待复习的句子' : '今天还没有学习过句子';
  const rows = data.sentences.map(sentence => {
    const time = isDue ? sentence.next_review_at : sentence.today_last_review_at;
    const timeText = isDue ? `下次复习 ${formatDate(time)}` : `今天最后学习 ${formatDate(time)}`;
    return `<article class="library-row study-status-row"><div><div class="library-jp" lang="ja">${esc(sentence.japanese)}</div><div class="status-translation">${esc(sentence.chinese)}</div><div class="row-stats"><span>${timeText}</span>${isDue ? '<span class="due-label">已到期</span>' : ''}</div></div></article>`;
  }).join('');
  view.innerHTML = `<section class="page study-status-page"><div class="page-head"><div><h1>${title}</h1><p>“${esc(data.collection.name)}” · ${summary}</p></div></div><div class="card library-list study-status-list">${rows || `<div class="empty">${emptyText}</div>`}</div></section>`;
  setChrome();
}

function addForm(data = {}) { return `<section class="page"><div class="page-head"><div><h1>${state.editing ? '编辑句子' : '添加句子'}</h1><p>输入中文和完整原句，再检查自动生成的词块。</p></div></div><div class="card form-card"><div class="form-grid"><label class="field full">所属句集<select id="collection">${collectionOptions(data.collection_id || state.activeCollection)}</select></label><label class="field">中文翻译<textarea id="chinese" placeholder="例如：即使下雨，我也想去散步。">${esc(data.chinese || '')}</textarea></label><label class="field">完整日语原句<textarea id="japanese" lang="ja" placeholder="例如：雨が降っても、散歩に行きたいです。">${esc(data.japanese || '')}</textarea></label></div><div class="form-actions"><button class="btn primary" data-action="organize">自动分块</button></div></div><div id="preview-slot"></div></section>`; }
async function renderAdd() { await ensureDashboard(); view.innerHTML = addForm(state.editing || {}); if (state.editing) { state.draft = {chunks:state.editing.chunks.map(x => ({...x})), practiceStructure:(state.editing.practiceStructure || []).map(x => ({...x})), source:state.editing.chunkSource || 'legacy', schemaVersion:state.editing.chunkSchemaVersion || 1, manuallyEdited:Boolean(state.editing.chunksManuallyEdited), sentenceFurigana:state.editing.furigana}; renderPreview(); } setChrome(); }
function fixedSlotPreview(structure, chunks, {interactive = false} = {}) {
  const map = Object.fromEntries((chunks || []).map(chunk => [chunk.id, chunk]));
  return (structure || []).map(element => {
    if (element.type === 'fixed') return `<span class="fixed-element" lang="ja">${esc(element.text)}</span>`;
    const chunk = map[element.chunkId];
    if (!chunk) return '';
    const index = chunks.indexOf(chunk);
    return interactive
      ? `<button class="chunk answer-preview-slot ${state.selectedChunks.includes(index) ? 'selected' : ''}" lang="ja" data-action="select-chunk" data-index="${index}">${esc(chunk.text)}</button>`
      : `<span class="answer-preview-slot" lang="ja">${esc(chunk.text)}</span>`;
  }).join('');
}
function markDraftManual() {
  if (!state.draft) return;
  state.draft.source = 'manual';
  state.draft.manuallyEdited = true;
  state.draft.correctOrder = state.draft.chunks.map(chunk => chunk.id);
}
function manualChunkId() {
  return globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID().slice(0, 16) : `manual-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}
function renderPreview() {
  const slot = $('#preview-slot'); if (!slot || !state.draft) return;
  const previewJp = rubyHtml(state.draft.sentenceFurigana) || esc($('#japanese').value);
  const sourceLabel = state.draft.source === 'fallback' ? '安全降级分块' : (state.draft.manuallyEdited ? '人工调整词块' : 'GiNZA 文节分块');
  slot.innerHTML = `<div class="card preview"><div class="preview-head"><div><h3>分块预览</h3><p>横线词块参与练习；标点和空白固定在原位。</p></div></div><div class="preview-fields"><div><span>所属句集</span><strong>${esc($('#collection').selectedOptions[0]?.textContent || '')}</strong></div><div><span>中文翻译</span><p>${esc($('#chinese').value)}</p></div><div><span>日语原句</span><p class="preview-jp" lang="ja">${previewJp}</p></div></div><div class="preview-structure" aria-label="固定标点与可练习词块结构">${fixedSlotPreview(state.draft.practiceStructure, state.draft.chunks, {interactive:true})}</div><div class="chunk-tools"><button class="btn outline" data-action="split-chunk">拆分词块</button><button class="btn outline" data-action="merge-chunks">合并相邻词块</button></div><p class="status-note">分块方式：${sourceLabel}；固定标点不会进入候选区。</p><div class="form-actions"><button class="btn outline" data-action="organize">重新分块</button><button class="btn primary" data-action="save-sentence">确认保存</button></div></div>`;
}

async function renderLibrary(collectionId = state.activeCollection) {
  await ensureDashboard(); state.activeCollection = Number(collectionId) || state.activeCollection; localStorage.setItem('activeCollection', state.activeCollection);
  const data = await api(`/api/sentences?collectionId=${state.activeCollection}`); const total = data.sentences.length;
  const countPicker = renderCountPicker({
    idPrefix: 'count',
    max: total,
    filterQuick: false,
    startAction: 'start-collection',
    startLabel: '开始练习',
    groupAriaLabel: '本轮题目数量',
    initialHint: `本句集共 ${total} 句。`,
  });
  view.innerHTML = `<section class="page"><div class="page-head"><div><h1>句集详情</h1><p>筛选、查找，或勾选句子开始专项练习。</p></div><button class="btn primary" data-route="add">＋ 添加句子</button></div><div class="card practice-picker"><div><h2>开始练习</h2><p>从本句集中随机抽取题目。</p></div>${countPicker}</div><div class="toolbar"><select id="library-collection">${collectionOptions(state.activeCollection)}</select><input id="library-search" type="search" placeholder="搜索中文或日语"><select id="library-sort"><option value="created">按创建时间</option><option value="error">按错误率</option><option value="recent">按最近练习</option></select></div><div class="section-title library-section-title"><h2>共 <span id="library-count">${total}</span> 条</h2><div class="library-bulk-actions"><button class="btn outline" data-action="manage-collection">管理句集</button><button class="btn outline" data-action="toggle-select-all" id="select-all-btn">全选</button><button class="btn outline" data-action="rechunk-selected" id="rechunk-selected-btn" disabled>重新分块</button><button class="btn outline" data-action="move-selected" id="move-selected-btn" disabled>转移选中句子</button><button class="btn primary" data-action="practice-selected">专项练习</button></div></div><div id="library-list" class="card library-list"></div></section>`;
  renderLibraryRows(data.sentences); setChrome();
}
function renderLibraryRows(items) { const list = $('#library-list'); $('#library-count').textContent = items.length; list.innerHTML = items.length ? items.map(s => `<div class="library-row"><input type="checkbox" class="sentence-check" value="${s.id}" aria-label="选择句子"><div><div class="library-jp" lang="ja">${esc(s.japanese)}</div><div>${esc(s.chinese)}</div><div class="row-stats"><span>FSRS ${s.last_review_at ? '已学习' : '新卡'}</span>${s.stability == null ? '' : `<span>稳定度 ${Number(s.stability).toFixed(2)}</span>`}${s.difficulty == null ? '' : `<span>难度 ${Number(s.difficulty).toFixed(2)}</span>`}<span>下次 ${formatDate(s.next_review_at)}</span></div></div><div class="row-actions"><button class="small-btn" data-action="edit-sentence" data-id="${s.id}">编辑</button><button class="small-btn" data-action="delete-sentence" data-id="${s.id}">删除</button></div></div>`).join('') : `<div class="empty">这个句集还没有句子，先添加第一句吧。</div>`; updateLibrarySelectionButtons(); }
async function reloadLibrary() { const query = new URLSearchParams({collectionId:$('#library-collection').value, search:$('#library-search').value, sort:$('#library-sort').value}); renderLibraryRows((await api('/api/sentences?' + query)).sentences); }

async function startPractice(payload) {
  const result = await api('/api/practice/sessions', {method:'POST', body:JSON.stringify(payload)});
  if (result.notice) toast(result.notice);
  state.practice = {
    sessionId: result.sessionId,
    sentences: result.sentences,
    index: 0,
    items: result.sentences.map(sentence => ({
      slotAssignments: Array(sentence.chunks.length).fill(null), checked: false, result: null,
      candidates: shuffle(sentence.chunks.map(chunk => chunk.id)),
      submitting: false, attemptStatuses: [], pendingAttempt: null,
      finalized: false, completion: null, questionStartedAt: Date.now(),
    })),
    submittingRound: false,
    exiting: false,
  };
  route('practice');
}
function shuffle(items) { const result = [...items]; for (let i = result.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [result[i], result[j]] = [result[j], result[i]]; } return result; }
let clientAttemptSequence = 0;
function createClientAttemptId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  clientAttemptSequence += 1;
  return `check-${Date.now()}-${clientAttemptSequence}-${Math.random().toString(36).slice(2)}`;
}
function currentPracticeItem(practice = state.practice) {
  return practice?.items?.[practice.index] || null;
}
function validCheckStatuses(item) {
  return (item?.attemptStatuses || []).filter(status => status === 'correct' || status === 'wrong');
}
function completedPracticeCount(practice = state.practice) {
  if (!practice) return 0;
  return practice.items.filter(item => (
    validCheckStatuses(item).length > 0
    || (item.finalized && item.completion?.status !== 'unanswered')
  )).length;
}
function practiceUnansweredIndexes(practice = state.practice) {
  if (!practice) return [];
  return practice.items.flatMap((item, index) => validCheckStatuses(item).length ? [] : [index]);
}
function practiceAnswerOrder(item = currentPracticeItem()) {
  return (item?.slotAssignments || []).filter(id => id != null);
}
function resetPracticeSlotAssignments(item) {
  if (!item) return;
  item.slotAssignments = Array(item.candidates.length).fill(null);
}
function selectionHtml(s, item, map) {
  // Grade by chunk text so duplicate surfaces (e.g. two 「し」) match regardless of which id instance was used.
  const correctTexts = (s.correctOrder || []).map(id => map[id]?.text || '');
  let slotIndex = 0;
  const structure = (s.practiceStructure || []).map(element => {
    if (element.type === 'fixed') return `<span class="fixed-element" lang="ja">${esc(element.text)}</span>`;
    const index = slotIndex++;
    const id = item.slotAssignments[index];
    if (id == null) return `<span class="answer-slot empty" data-slot-index="${index}" aria-label="第 ${index + 1} 个空词块槽位"></span>`;
    const text = map[id]?.text || '';
    const cls = item.checked ? (text === correctTexts[index] ? 'good' : 'bad') : '';
    return `<button class="answer-slot chosen ${cls}" lang="ja" data-slot-index="${index}" data-action="unchoose" data-index="${index}" data-id="${id}" ${item.checked ? 'disabled' : ''}>${chunkRubyHtml(s, map[id])}</button>`;
  }).join('');
  return `<div class="chosen-list answer-sequence">${structure}</div>`;
}
function practiceAnswerComplete(item = currentPracticeItem()) { return Boolean(item) && item.slotAssignments.length === item.candidates.length && item.slotAssignments.every(id => id != null); }
function practiceReadyToCheck(item = currentPracticeItem(), practice = state.practice) { return Boolean(practice) && Boolean(item) && !item.checked && !item.submitting && !practice.submittingRound && !practice.exiting; }
function moveSelectedTo(id, targetIndex) {
  const p = state.practice;
  const item = currentPracticeItem(p);
  if (!p || !item || item.checked || item.submitting || p.submittingRound) return;
  const assignments = item.slotAssignments;
  if (!Number.isInteger(targetIndex) || targetIndex < 0 || targetIndex >= assignments.length) return;
  const from = assignments.indexOf(id);
  if (from === targetIndex) return;
  if (from !== -1) {
    [assignments[from], assignments[targetIndex]] = [assignments[targetIndex], assignments[from]];
  } else {
    if (!item.candidates.includes(id)) return;
    const displaced = assignments[targetIndex];
    if (displaced != null) {
      let emptyIndex = -1;
      for (let offset = 1; offset < assignments.length; offset += 1) {
        const index = (targetIndex + offset) % assignments.length;
        if (assignments[index] == null) { emptyIndex = index; break; }
      }
      if (emptyIndex === -1) return;
      assignments[emptyIndex] = displaced;
    }
    assignments[targetIndex] = id;
  }
  updatePracticeSelection();
}
function removeSelectedId(id, slotIndex = null) {
  const p = state.practice;
  const item = currentPracticeItem(p);
  if (!p || !item || item.checked || item.submitting || p.submittingRound) return;
  const from = Number.isInteger(slotIndex) && item.slotAssignments[slotIndex] === id
    ? slotIndex
    : item.slotAssignments.indexOf(id);
  if (from === -1) return;
  item.slotAssignments[from] = null;
  updatePracticeSelection();
}
function updatePracticeSelection() {
  const p = state.practice, item = currentPracticeItem(p), s = p?.sentences[p.index];
  if (!p || !item || !s) return;
  const map = Object.fromEntries(s.chunks.map(c => [c.id, c]));
  const composer = $('#practice-composer'); if (!composer) return;
  composer.innerHTML = selectionHtml(s, item, map);
  $$('.candidate', view).forEach(button => { const used = item.slotAssignments.includes(button.dataset.id); button.disabled = used || item.checked || item.submitting || p.submittingRound || p.exiting; button.classList.toggle('used', used); });
  const checkButton = $('[data-action="check"]', view);
  if (checkButton) checkButton.disabled = !practiceReadyToCheck(item, p);
}

/** Practice-page pointer drag session (document-level; survives innerHTML re-renders of chips only via abort). */
let practiceDrag = null;
let suppressPracticeClick = false;
const PRACTICE_DRAG_THRESHOLD = 8;

function pointInRect(x, y, el) {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
}

function clearPracticeDropPreview() {
  $$('.practice-drop-preview').forEach(el => el.remove());
}

function setCandidateDropRemove(on) {
  const area = $('.candidate-area', view);
  if (area) area.classList.toggle('drop-remove', Boolean(on));
}

function cleanupPracticeDrag({ keepSuppress = false } = {}) {
  if (!practiceDrag) return;
  const session = practiceDrag;
  practiceDrag = null;
  if (session.ghost) session.ghost.remove();
  if (session.originEl) {
    session.originEl.classList.remove('dragging');
    try { if (session.pointerId != null) session.originEl.releasePointerCapture(session.pointerId); } catch {}
  }
  clearPracticeDropPreview();
  setCandidateDropRemove(false);
  if (session.didDrag && keepSuppress) {
    suppressPracticeClick = true;
    setTimeout(() => { suppressPracticeClick = false; }, 0);
  }
}

function abortPracticeDrag() {
  cleanupPracticeDrag({ keepSuppress: true });
}

function positionPracticeGhost(session, clientX, clientY) {
  if (!session.ghost) return;
  session.ghost.style.left = `${clientX - session.offsetX}px`;
  session.ghost.style.top = `${clientY - session.offsetY}px`;
}

function computePracticeDropIndex(clientX, clientY) {
  const list = $('#practice-composer .chosen-list');
  if (!list) return null;
  const slots = $$('.answer-slot', list);
  if (!slots.length) return null;
  const items = slots.map(el => {
    const r = el.getBoundingClientRect();
    return { i:Number(el.dataset.slotIndex), r };
  });
  const rows = [];
  for (const item of items) {
    let row = rows.find(row => Math.abs(row[0].r.top - item.r.top) < Math.min(row[0].r.height, item.r.height) / 2);
    if (!row) { row = []; rows.push(row); }
    row.push(item);
  }
  let bestRow = rows[0];
  let bestDist = Infinity;
  for (const row of rows) {
    const top = Math.min(...row.map(x => x.r.top));
    const bottom = Math.max(...row.map(x => x.r.bottom));
    if (clientY >= top && clientY <= bottom) { bestRow = row; bestDist = -1; break; }
    const dist = clientY < top ? top - clientY : clientY - bottom;
    if (dist < bestDist) { bestDist = dist; bestRow = row; }
  }
  let bestItem = bestRow[0];
  let bestHorizontalDistance = Infinity;
  for (const item of bestRow) {
    const distance = clientX < item.r.left ? item.r.left - clientX : (clientX > item.r.right ? clientX - item.r.right : 0);
    const centerDistance = Math.abs(clientX - (item.r.left + item.r.width / 2));
    const bestCenterDistance = Math.abs(clientX - (bestItem.r.left + bestItem.r.width / 2));
    if (distance < bestHorizontalDistance || (distance === bestHorizontalDistance && centerDistance < bestCenterDistance)) {
      bestItem = item;
      bestHorizontalDistance = distance;
    }
  }
  return bestItem.i;
}

function updatePracticeDropIndicator(session, clientX, clientY) {
  const composer = $('#practice-composer');
  const candidateArea = $('.candidate-area', view);
  const overCandidate = pointInRect(clientX, clientY, candidateArea);
  const overComposer = pointInRect(clientX, clientY, composer);
  session.overCandidate = overCandidate;
  session.overComposer = overComposer;
  setCandidateDropRemove(overCandidate && session.source === 'chosen');

  if (overCandidate || !overComposer) {
    session.dropIndex = null;
    clearPracticeDropPreview();
    return;
  }

  const dropIndex = computePracticeDropIndex(clientX, clientY);
  session.dropIndex = dropIndex;
  const list = $('#practice-composer .chosen-list');
  if (!list || dropIndex == null) { clearPracticeDropPreview(); return; }
  const slots = $$('.answer-slot', list);
  const target = slots.find(element => Number(element.dataset.slotIndex) === dropIndex);
  if (!target) { clearPracticeDropPreview(); return; }
  const currentPreview = $('.practice-drop-preview', target);
  if (currentPreview) return;
  clearPracticeDropPreview();
  const preview = document.createElement('span');
  preview.className = 'practice-drop-preview';
  preview.setAttribute('aria-hidden', 'true');
  preview.innerHTML = session.previewHtml;
  target.appendChild(preview);
}

function beginPracticeDrag(session, event) {
  session.dragging = true;
  session.didDrag = true;
  session.originEl.classList.add('dragging');
  const rect = session.originEl.getBoundingClientRect();
  session.offsetX = event.clientX - rect.left;
  session.offsetY = event.clientY - rect.top;
  const ghost = session.originEl.cloneNode(true);
  ghost.classList.add('drag-ghost');
  ghost.classList.remove('dragging');
  ghost.removeAttribute('data-action');
  ghost.disabled = false;
  ghost.style.width = `${rect.width}px`;
  ghost.style.height = `${rect.height}px`;
  document.body.appendChild(ghost);
  session.ghost = ghost;
  positionPracticeGhost(session, event.clientX, event.clientY);
  try { session.originEl.setPointerCapture(session.pointerId); } catch {}
  updatePracticeDropIndicator(session, event.clientX, event.clientY);
}

function onPracticePointerDown(event) {
  if (event.button != null && event.button !== 0) return;
  const practice = state.practice;
  const item = currentPracticeItem(practice);
  if (state.route !== 'practice' || !practice || !item || item.checked || item.submitting || practice.submittingRound) return;
  if (practiceDrag) return;
  const chip = event.target.closest?.('.chosen, .candidate');
  if (!chip || !view.contains(chip)) return;
  if (chip.disabled || chip.classList.contains('used')) return;
  const id = chip.dataset.id;
  if (!id) return;
  const source = chip.classList.contains('chosen') ? 'chosen' : 'candidate';
  if (source === 'candidate' && (!item.candidates.includes(id) || item.slotAssignments.includes(id))) return;
  if (source === 'chosen' && !item.slotAssignments.includes(id)) return;
  practiceDrag = {
    pointerId: event.pointerId,
    id,
    source,
    startX: event.clientX,
    startY: event.clientY,
    originEl: chip,
    previewHtml: chip.innerHTML,
    dragging: false,
    didDrag: false,
    ghost: null,
    dropIndex: null,
    overCandidate: false,
    overComposer: false,
    offsetX: 0,
    offsetY: 0,
  };
}

function onPracticePointerMove(event) {
  const session = practiceDrag;
  if (!session || event.pointerId !== session.pointerId) return;
  if (!session.dragging) {
    const dx = event.clientX - session.startX;
    const dy = event.clientY - session.startY;
    if (dx * dx + dy * dy < PRACTICE_DRAG_THRESHOLD * PRACTICE_DRAG_THRESHOLD) return;
    if (!session.originEl.isConnected) { abortPracticeDrag(); return; }
    beginPracticeDrag(session, event);
  }
  event.preventDefault();
  positionPracticeGhost(session, event.clientX, event.clientY);
  updatePracticeDropIndicator(session, event.clientX, event.clientY);
}

function onPracticePointerUp(event) {
  const session = practiceDrag;
  if (!session || event.pointerId !== session.pointerId) return;
  if (!session.didDrag) {
    cleanupPracticeDrag();
    return;
  }
  event.preventDefault();
  updatePracticeDropIndicator(session, event.clientX, event.clientY);
  const { id, source, dropIndex, overCandidate, overComposer } = session;
  cleanupPracticeDrag({ keepSuppress: true });
  if (overCandidate && source === 'chosen') {
    removeSelectedId(id);
    return;
  }
  if (overComposer && dropIndex != null) {
    moveSelectedTo(id, dropIndex);
  }
}

function onPracticePointerCancel(event) {
  if (!practiceDrag || event.pointerId !== practiceDrag.pointerId) return;
  abortPracticeDrag();
}

function practiceDialogBusy() {
  if (state.route !== 'practice') return false;
  return Boolean(state.practice?.exiting || state.practice?.submittingRound || currentPracticeItem()?.submitting);
}
function onPracticeKeyDown(event) {
  if (event.key === 'Escape' && (isExitPracticeDialogOpen() || isUnansweredPracticeDialogOpen() || isIncompleteAnswerDialogOpen())) {
    event.preventDefault();
    if (!practiceDialogBusy()) closeDialog();
    return;
  }
  if (event.key === 'Escape' && practiceDrag) {
    event.preventDefault();
    abortPracticeDrag();
  }
}

document.addEventListener('pointerdown', onPracticePointerDown);
document.addEventListener('pointermove', onPracticePointerMove, { passive: false });
document.addEventListener('pointerup', onPracticePointerUp);
document.addEventListener('pointercancel', onPracticePointerCancel);
document.addEventListener('keydown', onPracticeKeyDown);
function renderPractice() {
  const p = state.practice;
  const item = currentPracticeItem(p);
  if (!p || !item) return route('home', {replace:true});
  const s = p.sentences[p.index], map = Object.fromEntries(s.chunks.map(c => [c.id, c]));
  const pct = Math.round((p.index + 1) * 100 / p.sentences.length);
  const ready = practiceReadyToCheck(item, p), busy = item.submitting || p.submittingRound || p.exiting;
  const last = p.index === p.sentences.length - 1;
  const candidatesHtml = item.candidates.map(id => `<button class="candidate ${item.slotAssignments.includes(id) ? 'used' : ''}" lang="ja" data-action="choose" data-id="${id}" ${item.slotAssignments.includes(id) || item.checked || busy ? 'disabled' : ''}>${chunkRubyHtml(s, map[id])}</button>`).join('');
  view.innerHTML = `<section class="page practice-page"><div class="practice-nav"><button class="back" data-action="exit-practice">←　句子重组</button><div class="thin-progress" aria-label="练习进度"><span style="width:${pct}%"></span></div><button class="exit" data-action="exit-practice">${p.index + 1} / ${p.sentences.length}　退出</button></div><h1 class="practice-title">句子重组</h1><div class="prompt-scene"><div class="learner-art" aria-label="日语学习人物插图"><i class="body"></i><i class="head"></i><i class="hair"></i></div><div class="card speech">${esc(s.chinese)}</div></div><div id="practice-composer" class="card composer" aria-live="polite" aria-label="句子答案槽位">${selectionHtml(s, item, map)}</div><div class="candidate-area"><div class="chunk-list">${candidatesHtml}</div></div>${item.checked ? answerDetails(s, map, item) : ''}<div class="practice-actions"><button class="btn outline practice-prev" data-action="previous-question" ${p.index === 0 || busy ? 'disabled' : ''}>上一题</button><button class="btn outline practice-next" data-action="${last ? 'submit-round' : 'next-question'}" ${busy ? 'disabled' : ''}>${last ? '提交本轮' : '下一题'}</button><button class="btn ghost practice-reset" data-action="reset" ${item.checked || busy ? 'disabled' : ''}>重置</button>${item.checked ? `<button class="btn outline retry-current" data-action="retry-current" ${busy ? 'disabled' : ''}>重新练习本题</button>` : ''}<button class="btn primary practice-check" data-action="check" ${!ready ? 'disabled' : ''}>${item.checked ? '已核对' : '核对答案'}</button></div></section>`;
  setChrome(true);
}
function answerDetails(s, map, item) {
  const user = practiceAnswerOrder(item).map(id => map[id]?.text || '').join('');
  const correctJp = rubyHtml(s.furigana) || esc(s.japanese);
  return `<div class="card answer-card"><h3>${item.result?.correct ? '回答正确' : '正确答案'}</h3>${!item.result?.correct ? `<div class="report-line"><span>你的排列</span><strong lang="ja">${esc(user || '（未作答）')}</strong></div>` : ''}<div class="correct-display" lang="ja">${correctJp}</div><p>${esc(s.chinese)}</p></div>`;
}
function openIncompleteAnswerDialog() {
  const p = state.practice;
  const item = currentPracticeItem(p);
  if (!practiceReadyToCheck(item, p) || practiceAnswerComplete(item)) return;
  openDialog(`<div class="incomplete-answer-dialog"><h1>直接查看答案？</h1><p class="incomplete-answer-copy">当前句子尚未排列完成。直接查看答案会将本次回答记录为错误，并参与本题的自动评分。</p><p id="incomplete-answer-error" class="form-error incomplete-answer-error" role="alert"></p><div class="form-actions"><button class="btn outline" data-action="continue-incomplete-answer">继续作答</button><button class="btn primary" data-action="confirm-incomplete-answer">直接查看答案</button></div></div>`, { className: 'incomplete-answer-modal', label: '直接查看答案？' });
}
function isIncompleteAnswerDialogOpen() {
  const dialog = $('#dialog');
  return Boolean(dialog && !dialog.classList.contains('hidden') && $('.incomplete-answer-dialog', dialog));
}
async function record(action, { confirmIncomplete = false, button = null } = {}) {
  const p = state.practice;
  const item = currentPracticeItem(p);
  if (!p || !item || item.submitting || p.submittingRound || action !== 'check') return;
  if (!practiceReadyToCheck(item, p)) return;
  if (!practiceAnswerComplete(item) && !confirmIncomplete) { openIncompleteAnswerDialog(); return; }
  const s = p.sentences[p.index];
  const answerOrder = practiceAnswerOrder(item);
  const answerKey = JSON.stringify(answerOrder);
  if (!item.pendingAttempt || item.pendingAttempt.answerKey !== answerKey) {
    item.pendingAttempt = { id: createClientAttemptId(), answerKey };
  }
  const durationMs = Math.max(0, Date.now() - (item.questionStartedAt || Date.now()));
  const dialogButtons = button ? $$('button', $('#dialog')) : [];
  const oldLabel = button?.textContent || '';
  if (button) {
    dialogButtons.forEach(item => { item.disabled = true; });
    button.textContent = '正在查看…';
    const errorEl = $('#incomplete-answer-error');
    if (errorEl) errorEl.textContent = '';
  }
  item.submitting = true;
  renderPractice();
  try {
    item.result = await api(`/api/practice/sessions/${p.sessionId}/attempts`, {method:'POST', body:JSON.stringify({sentenceId:s.id, action, attemptId:item.pendingAttempt.id, answerOrder, durationMs})});
    item.attemptStatuses.push(item.result.status);
    item.pendingAttempt = null;
    item.checked = true;
  } catch (error) {
    item.submitting = false;
    renderPractice();
    if (button) {
      dialogButtons.forEach(item => { item.disabled = false; });
      button.textContent = oldLabel;
      const errorEl = $('#incomplete-answer-error');
      if (errorEl) errorEl.textContent = error.message;
    }
    throw error;
  }
  item.submitting = false;
  if (button) closeDialog();
  renderPractice();
}
function navigatePractice(delta) {
  const p = state.practice;
  const item = currentPracticeItem(p);
  if (!p || !item || item.submitting || p.submittingRound) return;
  const nextIndex = p.index + delta;
  if (nextIndex < 0 || nextIndex >= p.sentences.length) return;
  abortPracticeDrag();
  p.index = nextIndex;
  const nextItem = currentPracticeItem(p);
  if (nextItem && !nextItem.checked) nextItem.questionStartedAt = Date.now();
  renderPractice();
}
function roundSubmissionPayload(practice, confirmUnanswered) {
  return {
    confirmUnanswered,
    clientUnansweredCount: practiceUnansweredIndexes(practice).length,
    draftAnswers: practice.sentences.map((sentence, index) => ({
      sentenceId: sentence.id,
      answerOrder: practiceAnswerOrder(practice.items[index]),
    })),
  };
}
function openUnansweredPracticeDialog(count) {
  const p = state.practice;
  if (!p) return;
  p.serverUnansweredCount = count;
  openDialog(`<div class="unanswered-practice-dialog"><h1>本轮还有 ${count} 题未回答</h1><p class="unanswered-practice-copy">未回答的题目不会写入 FSRS 复习记录，也不会被判定为忘记。它们的记忆状态、稳定度、难度和下次复习时间都不会发生变化；如果题目原本已经到期，它仍会保持待复习状态。</p><p id="unanswered-practice-error" class="form-error unanswered-practice-error" role="alert"></p><div class="form-actions"><button class="btn outline" data-action="continue-unanswered">返回继续作答</button><button class="btn primary" data-action="confirm-submit-unanswered">仍然提交</button></div></div>`, { className: 'unanswered-practice-modal', label: `本轮还有 ${count} 题未回答` });
}
function isUnansweredPracticeDialogOpen() {
  const dialog = $('#dialog');
  return Boolean(dialog && !dialog.classList.contains('hidden') && $('.unanswered-practice-dialog', dialog));
}
function continueUnansweredPractice() {
  const p = state.practice;
  if (!p || p.submittingRound) return;
  const first = practiceUnansweredIndexes(p)[0];
  closeDialog();
  if (Number.isInteger(first)) p.index = first;
  renderPractice();
}
async function submitPracticeRound({ confirmUnanswered = false, button = null } = {}) {
  const p = state.practice;
  if (!p || p.submittingRound) return;
  p.submittingRound = true;
  let dialogButtons = [];
  let oldLabel = '';
  if (button) {
    dialogButtons = $$('button', $('#dialog'));
    dialogButtons.forEach(item => { item.disabled = true; });
    oldLabel = button.textContent;
    button.textContent = '正在提交…';
    const errorEl = $('#unanswered-practice-error');
    if (errorEl) errorEl.textContent = '';
  } else {
    renderPractice();
  }
  try {
    await api(`/api/practice/sessions/${p.sessionId}/complete`, {method:'POST', body:JSON.stringify(roundSubmissionPayload(p, confirmUnanswered))});
    if (typeof window.clearStatsCache === 'function') window.clearStatsCache();
    state.report = (await api(`/api/reports/${p.sessionId}`)).report;
    closeDialog();
    route('report', {reportId:p.sessionId});
  } catch (error) {
    p.submittingRound = false;
    if (!confirmUnanswered && error.status === 409 && error.data?.requiresConfirmation) {
      renderPractice();
      openUnansweredPracticeDialog(Number(error.data.unansweredCount || 0));
      return;
    }
    if (button) {
      dialogButtons.forEach(item => { item.disabled = false; });
      button.textContent = oldLabel;
      const errorEl = $('#unanswered-practice-error');
      if (errorEl) errorEl.textContent = error.message;
    } else {
      renderPractice();
    }
    throw error;
  }
}

function ratingSummaryText(report) { const c = report.ratingCounts || {}; return `忘记 ${c.again || 0} · 模糊 ${c.hard || 0} · 认识 ${c.good || 0} · 轻松掌握 ${c.easy || 0}${c.skipped ? ` · 跳过 ${c.skipped}` : ''}${report.unansweredCount ? ` · 未回答 ${report.unansweredCount}` : ''}`; }
function historyRoundText(report) { const completed = Number(report.completedCount ?? ((report.correct || 0) + (report.wrong || 0) + (report.skipped || 0))), unanswered = Number(report.unansweredCount || 0); return report.endedEarly ? `提前结束 · 原计划 ${report.total} 句 · 完成 ${completed} 句 · 未完成 ${unanswered} 句` : `本轮 ${report.total} 句`; }
async function renderReports() { const data = await api('/api/reports'); view.innerHTML = `<section class="page"><div class="page-head"><div><h1>练习历史</h1><p>每轮练习都会保留，可随时重新打开。</p></div></div><div class="card section-card">${data.reports.length ? data.reports.map(r => `<div class="history-row"><button class="row-open" data-action="open-report" data-id="${r.id}"><span class="row-icon fsrs-report-icon">FSRS</span><span class="row-main"><strong>${formatDate(r.completed_at)}</strong><small>${historyRoundText(r)} · ${ratingSummaryText(r)}</small></span></button><div class="row-actions"><button class="small-btn" data-action="delete-report" data-id="${r.id}" aria-label="删除这条记录">删除</button><span class="arrow">›</span></div></div>`).join('') : '<div class="empty">完成一次练习后，报告会出现在这里。</div>'}</div></section>`; setChrome(); }
function renderReport() { const r = state.report; if (!r) return route('reports', {replace:true}); const c = r.ratingCounts || {}; const skipped = c.skipped || 0, unanswered = Number(r.unansweredCount || 0), completed = Number(r.completedCount ?? ((r.correct || 0) + (r.wrong || 0) + (r.skipped || 0))), endedEarly = Boolean(r.endedEarly); const completedItems = (r.items || []).filter(item => item.status !== 'unanswered'), unansweredItems = (r.items || []).filter(item => item.status === 'unanswered'); const details = r.items?.length ? `<div class="report-items-section"><div class="report-section-heading"><h2>已完成题目</h2><span>${completed} 题</span></div>${completedItems.length ? reportItems(completedItems) : '<div class="card empty">没有已完成题目明细。</div>'}</div>${unansweredItems.length ? `<div class="report-items-section unanswered-items"><div class="report-section-heading"><h2>未完成题目</h2><span>${unanswered} 题 · 未计入 FSRS</span></div>${reportItems(unansweredItems)}</div>` : ''}` : reportItems([]); view.innerHTML = `<section class="page"><div class="page-head report-head"><div><h1>本轮练习报告${endedEarly ? '<span class="early-exit-badge">提前结束</span>' : ''}</h1><p>${formatDate(r.completed_at || r.created_at)}</p></div><div class="report-actions"><button class="btn outline" data-action="home">返回首页</button><button class="btn primary" data-action="open-retry-round">再练一轮</button></div></div>${endedEarly ? `<p class="early-exit-report-note">本轮已提前结束：原计划 ${r.total} 题，实际完成 ${completed} 题，未完成 ${unanswered} 题。未完成题目未计入正确率或 FSRS。</p>` : ''}<div class="report-summary"><div class="card stat-card"><strong>${r.total}</strong>原计划</div><div class="card stat-card"><strong>${completed}</strong>实际完成</div><div class="card stat-card"><strong>${unanswered}</strong>未完成</div><div class="card stat-card"><strong>${c.again || 0}</strong>忘记</div><div class="card stat-card"><strong>${c.hard || 0}</strong>模糊</div><div class="card stat-card"><strong>${c.good || 0}</strong>认识</div><div class="card stat-card"><strong>${c.easy || 0}</strong>轻松掌握</div></div>${unanswered ? `<p class="report-unanswered-note">${unanswered} 题未完成，未计入 FSRS，也未判定为错误或遗忘。</p>` : ''}${skipped ? `<p class="report-skip-note">另有 ${skipped} 句历史跳过记录，未计入 FSRS 评分。</p>` : ''}<div id="report-items">${details}</div></section>`; setChrome(); }
function reportItems(items) { return items.length ? items.map(item => { const unanswered = item.status === 'unanswered'; const statusLabel = unanswered ? '未回答' : (item.ratingLabel ? `FSRS · ${esc(item.ratingLabel)}` : '跳过'); return `<article class="card report-item ${item.status}" data-status="${item.status}"><div class="section-title"><h3>${esc(item.chinese)}</h3><strong>${statusLabel}</strong></div><div class="report-line"><span>你的排列</span><div lang="ja">${esc(item.answerText || '（未作答）')}</div></div><div class="report-line"><span>正确句子</span><div lang="ja">${esc(item.japanese)}</div></div>${unanswered ? '<div class="report-line"><span>说明</span><div>未计入 FSRS</div></div>' : ''}</article>`; }).join('') : '<div class="card empty">这份旧报告的题目明细已不可用。</div>'; }

async function renderSettings() {
  const [authCfg, tzCfg, fsrsCfg] = await Promise.all([api('/api/settings/auth'), api('/api/settings/timezone'), api('/api/settings/fsrs')]);
  const timezoneCard = `<form id="timezone-form" class="card"><div class="settings-title"><div><h2>时区</h2><p>"今日学习"和「统计」页的分桶都按自然日归类，这里设置的时区决定自然日的分界点；不设置时默认按服务器所在时区计算。</p></div><span class="config-status ${tzCfg.timezone ? 'ok' : 'warn'}">${tzCfg.timezone ? '已设置' : '未设置'}</span></div><label class="field">时区<select name="timezone" id="timezone-select">${timezoneOptionsHtml(tzCfg.timezone)}</select></label>${tzCfg.timezone ? '' : `<p class="status-note">当前按服务器时区（UTC${tzCfg.serverUtcOffset}）计算，如果你实际所在地区和服务器不同，建议在上方选择你自己的时区。</p>`}<div class="form-actions"><button class="btn primary" type="submit">保存时区设置</button></div></form>`;
  view.innerHTML = `<section class="page"><div class="page-head"><div><h1>设置</h1><p>管理网站访问认证、时区、复习调度与使用说明。</p></div></div><div class="settings-grid"><form id="auth-form" class="card"><div class="settings-title"><div><h2>访问认证</h2><p>密码仅保存安全哈希，页面不会显示原密码。</p></div><span class="config-status ${authCfg.configured ? 'ok' : 'warn'}">${authCfg.configured ? '已启用' : '未启用'}</span></div><label class="field">用户名<input name="username" value="${esc(authCfg.username || '')}" autocomplete="username"></label><label class="field">新密码 ${authCfg.configured ? '<small>留空表示不修改</small>' : ''}<input name="password" type="password" autocomplete="new-password"></label><label class="check-row"><input name="clearAuth" type="checkbox">关闭应用认证</label><p class="status-note">关闭后将不再要求应用登录，请确认这符合你的访问策略。</p><div class="form-actions"><button class="btn primary" type="submit">保存认证设置</button></div></form>${timezoneCard}<div class="card"><div class="settings-title"><div><h2>复习调度</h2><p>所有句子统一由官方 FSRS 系统安排复习。</p></div><span class="config-status ok">FSRS</span></div><div class="preview-fields"><div><span>当前调度系统</span><strong>${esc(fsrsCfg.system)}</strong></div><div><span>目标保持率</span><strong>${Math.round(fsrsCfg.desiredRetention * 100)}%</strong></div><div><span>最大复习间隔</span><strong>${fsrsCfg.maximumIntervalDays} 天</strong></div><div><span>FSRS 版本</span><strong>${esc(fsrsCfg.version)}</strong></div></div><p class="status-note">核对答案只保存原始作答，正式提交时由后端自动评分：第一次核对即答对为“认识”，连续第二轮起仍首次答对为“轻松掌握”；第一次答错、第二次答对为“模糊”；第一次答错后未再次核对，或第二次仍然答错，为“忘记”。从未核对的题目不计入 FSRS。</p></div><div class="card settings-help"><h2>使用说明</h2><p>输入中文翻译和完整日语原句，点击“自动分块”，检查词块后确认保存。</p><p>分块完全在本机使用标准 GiNZA ja_ginza 文节模型完成；标点与空白固定显示，不会进入候选词块，也不会把句子发送到外部服务。</p><p>可在「学习概览」查看近期学习、未来复习安排和当前记忆状态。</p><button class="btn outline" data-action="logout">退出登录</button></div></div></section>`;
  setChrome();
}

document.addEventListener('click', async event => {
  if (event.target.id === 'dialog') {
    if (!practiceDialogBusy()) closeDialog();
    return;
  }
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.route) { if (button.dataset.route === state.route) return; state.editing = null; const fromHome = state.route === 'home' && (button.dataset.route === 'due' || button.dataset.route === 'today'); route(button.dataset.route, {fromHome}); return; }
  const action = button.dataset.action;
  if (suppressPracticeClick && (action === 'choose' || action === 'unchoose')) return;
  try {
    if (action === 'home') route('home');
    else if (action === 'back') navigateBack();
    else if (action === 'new-collection') { const name = prompt('新句集名称：'); if (name) { await api('/api/collections', {method:'POST', body:JSON.stringify({name})}); state.dashboard = null; await renderHome(); } }
    else if (action === 'open-collection') { state.activeCollection = Number(button.dataset.id); route('library', {collectionId:state.activeCollection}); }
    else if (action === 'start-due') { const active = state.dashboard?.collections.find(c => c.id === state.activeCollection); if (!active?.due) { toast('当前句集没有待复习句子'); return; } state.homeDuePicker = true; await renderHome(); }
    else if (action === 'set-due-count') {
      selectCountOption('due-count', button);
      const due = Number($('#due-custom-count')?.max || 0);
      bindCountPicker('due-count', due, { mode: 'strict', startAction: 'start-due-practice', defaultHint: `本句集有 ${due} 句待复习，可从到期最早的句子开始。` });
    }
    else if (action === 'start-due-practice') {
      const collectionId = Number($('#due-collection')?.value), active = state.dashboard?.collections.find(c => c.id === collectionId);
      if (!active?.due) { toast('所选句集当前没有待复习句子'); return; }
      const { custom, selected } = readCountPickerSelection('due-count', { trimCustom: true });
      if (custom) {
        const count = Number(custom);
        if (!Number.isInteger(count) || count < 1 || count > active.due) { toast(`请输入 1 到 ${active.due} 之间的整数`); return; }
      }
      state.homeDuePicker = false;
      await startPractice({ collectionId, count: custom || selected });
    }
    else if (action === 'set-count') { selectCountOption('count', button); }
    else if (action === 'start-collection') {
      const { custom, selected } = readCountPickerSelection('count', { trimCustom: false });
      await startPractice({ scope: 'collection', collectionId: state.activeCollection, count: custom || selected });
    }
    else if (action === 'open-retry-round') await openRetryRoundDialog();
    else if (action === 'set-report-count') {
      selectCountOption('report-count', button);
      const max = Number($('#report-custom-count')?.max || 0);
      bindCountPicker('report-count', max, { mode: 'strict', startAction: 'start-report-round', defaultHint: '优先选择本轮未回答题目，其余按到期顺序补充。' });
    }
    else if (action === 'start-report-round') {
      const reportId = Number($('#dialog').dataset.retryReportId || 0);
      const max = Number($('#report-custom-count')?.max || 0);
      const { custom, selected } = readCountPickerSelection('report-count', { trimCustom: true });
      const count = custom ? Number(custom) : selected;
      if (!reportId || !max) return;
      if (custom && (!Number.isInteger(count) || count < 1 || count > max)) {
        bindCountPicker('report-count', max, { mode: 'strict', startAction: 'start-report-round', defaultHint: '优先选择本轮未回答题目，其余按到期顺序补充。' });
        return;
      }
      button.disabled = true;
      const oldLabel = button.textContent;
      button.textContent = '正在开始…';
      try {
        await startPractice({ scope: 'report_retry', reportId, count });
        closeDialog();
      } catch (error) {
        button.disabled = false;
        button.textContent = oldLabel;
        const hint = $('#report-count-hint');
        if (hint) { hint.textContent = error.message; hint.classList.add('error-text'); }
      }
    }
    else if (action === 'organize') { const japanese = $('#japanese').value, chinese = $('#chinese').value; button.disabled = true; const old = button.textContent; button.textContent = '正在分块…'; try { state.draft = await api('/api/sentences/organize', {method:'POST', body:JSON.stringify({japanese, chinese})}); state.draft.manuallyEdited = false; state.selectedChunks = []; renderPreview(); } finally { button.disabled = false; button.textContent = old; } }
    else if (action === 'select-chunk') { const i = Number(button.dataset.index), at = state.selectedChunks.indexOf(i); if (at >= 0) state.selectedChunks.splice(at, 1); else { if (state.selectedChunks.length >= 2) state.selectedChunks.shift(); state.selectedChunks.push(i); } state.selectedChunks.sort((a,b) => a-b); renderPreview(); }
    else if (action === 'split-chunk') {
      if (state.selectedChunks.length !== 1) throw new Error('请先选中一个要拆分的词块');
      const i = state.selectedChunks[0], item = state.draft.chunks[i];
      const pos = Number(prompt(`“${item.text}” 在第几个字符后拆分？`, Math.max(1, Math.floor(item.text.length / 2))));
      if (!Number.isInteger(pos) || pos <= 0 || pos >= item.text.length) throw new Error('拆分位置必须位于词块内部');
      const left = {id:manualChunkId(), text:item.text.slice(0,pos), start:item.start, end:item.start + pos};
      const right = {id:manualChunkId(), text:item.text.slice(pos), start:item.start + pos, end:item.end};
      const structureIndex = state.draft.practiceStructure.findIndex(element => element.type === 'slot' && element.chunkId === item.id);
      if (structureIndex < 0) throw new Error('当前词块结构无效，请重新自动分块');
      state.draft.chunks.splice(i, 1, left, right);
      state.draft.practiceStructure.splice(structureIndex, 1,
        {type:'slot', chunkId:left.id, start:left.start, end:left.end},
        {type:'slot', chunkId:right.id, start:right.start, end:right.end});
      markDraftManual(); state.selectedChunks = []; renderPreview();
    }
    else if (action === 'merge-chunks') {
      const [a,b] = state.selectedChunks;
      if (state.selectedChunks.length !== 2 || b !== a + 1) throw new Error('请按顺序选中两个相邻词块');
      const x = state.draft.chunks[a], y = state.draft.chunks[b];
      const xIndex = state.draft.practiceStructure.findIndex(element => element.type === 'slot' && element.chunkId === x.id);
      const yIndex = state.draft.practiceStructure.findIndex(element => element.type === 'slot' && element.chunkId === y.id);
      if (xIndex < 0 || yIndex !== xIndex + 1 || x.end !== y.start) throw new Error('固定标点两侧的词块不能合并');
      const merged = {id:manualChunkId(), text:x.text + y.text, start:x.start, end:y.end};
      state.draft.chunks.splice(a, 2, merged);
      state.draft.practiceStructure.splice(xIndex, 2, {type:'slot', chunkId:merged.id, start:merged.start, end:merged.end});
      markDraftManual(); state.selectedChunks = []; renderPreview();
    }
    else if (action === 'save-sentence') {
      const payload = {collectionId:Number($('#collection').value), chinese:$('#chinese').value, japanese:$('#japanese').value, chunks:state.draft.chunks, correctOrder:state.draft.chunks.map(c => c.id), practiceStructure:state.draft.practiceStructure, chunkSource:state.draft.source, chunksManuallyEdited:Boolean(state.draft.manuallyEdited)};
      const wasEditing = Boolean(state.editing);
      if (wasEditing) await api(`/api/sentences/${state.editing.id}`, {method:'PUT', body:JSON.stringify(payload)});
      else await api('/api/sentences', {method:'POST', body:JSON.stringify(payload)});
      if (typeof window.clearStatsCache === 'function') window.clearStatsCache();
      toast('句子已保存');
      state.editing = null; state.draft = null; state.selectedChunks = [];
      setTimeout(() => { const link = document.querySelector('link[href*="faces.css"]'); if (link) link.href = `/api/fonts/faces.css?t=${Date.now()}`; }, 2800);
      if (wasEditing) { route('library'); }
      else {
        const collectionId = Number($('#collection')?.value);
        if (collectionId) { state.activeCollection = collectionId; localStorage.setItem('activeCollection', collectionId); }
        const chinese = $('#chinese'), japanese = $('#japanese'), slot = $('#preview-slot');
        if (chinese) chinese.value = '';
        if (japanese) japanese.value = '';
        if (slot) slot.innerHTML = '';
        window.scrollTo(0, 0);
        chinese?.focus();
      }
    }
    else if (action === 'practice-selected') { const ids = selectedSentenceIds(); if (!ids.length) throw new Error('请至少勾选一条句子'); await startPractice({sentenceIds:ids}); }
    else if (action === 'edit-sentence') { state.editing = (await api(`/api/sentences/${button.dataset.id}`)).sentence; route('add', {editingId:state.editing.id}); }
    else if (action === 'delete-sentence') { if (confirm('确定删除这条句子吗？')) { await api(`/api/sentences/${button.dataset.id}`, {method:'DELETE'}); if (typeof window.clearStatsCache === 'function') window.clearStatsCache(); reloadLibrary(); } }
    else if (action === 'close-dialog') {
      if (!practiceDialogBusy()) closeDialog();
    }
    else if (action === 'manage-collection') { await ensureDashboard(); openManageCollectionDialog(); }
    else if (action === 'rename-collection') { const name = $('#rename-collection-name')?.value.trim(); if (!name) throw new Error('句集名称不能为空'); const id = state.activeCollection; await api(`/api/collections/${id}`, {method:'PATCH', body:JSON.stringify({name})}); state.dashboard = null; closeDialog(); toast('句集已重命名'); await renderLibrary(id); }
    else if (action === 'delete-collection-ask') openDeleteCollectionConfirm();
    else if (action === 'delete-collection-confirm') { const id = state.activeCollection; await api(`/api/collections/${id}?cascade=1`, {method:'DELETE'}); state.dashboard = null; if (typeof window.clearStatsCache === 'function') window.clearStatsCache(); closeDialog(); toast('句集已删除'); await renderLibrary(); }
    else if (action === 'toggle-select-all') toggleVisibleSentenceSelection();
    else if (action === 'rechunk-selected') { const ids = selectedSentenceIds(); if (!ids.length) throw new Error('请至少勾选一条句子'); openRechunkSentencesDialog(ids); }
    else if (action === 'confirm-rechunk-sentences') await confirmRechunkSentences(button);
    else if (action === 'move-selected') { const ids = selectedSentenceIds(); if (!ids.length) throw new Error('请至少勾选一条句子'); await ensureDashboard(); openMoveSentencesDialog(ids); }
    else if (action === 'confirm-move-sentences') { const ids = ($('#dialog').dataset.moveIds || '').split(',').filter(Boolean).map(Number); const targetCollectionId = Number($('#move-target-collection')?.value); if (!ids.length) throw new Error('请至少勾选一条句子'); if (!targetCollectionId) throw new Error('请选择目标句集'); const result = await api('/api/sentences/move', {method:'POST', body:JSON.stringify({sentenceIds:ids, targetCollectionId})}); state.dashboard = null; closeDialog(); toast(`已转移 ${result.moved} 句`); await renderLibrary(state.activeCollection); }
    else if (action === 'choose') { const p = state.practice, item = currentPracticeItem(p), id = button.dataset.id; if (!p || !item || item.checked || item.submitting || p.submittingRound || !item.candidates.includes(id) || item.slotAssignments.includes(id)) return; const targetIndex = item.slotAssignments.findIndex(assignedId => assignedId == null); if (targetIndex === -1) return; moveSelectedTo(id, targetIndex); }
    else if (action === 'unchoose') { const p = state.practice, item = currentPracticeItem(p), index = Number(button.dataset.index), id = button.dataset.id; if (!p || !item || item.checked || item.submitting || p.submittingRound || !Number.isInteger(index) || index < 0 || index >= item.slotAssignments.length || item.slotAssignments[index] !== id) return; removeSelectedId(id, index); }
    else if (action === 'reset') { const p = state.practice, item = currentPracticeItem(p); if (!p || !item || item.checked || item.submitting || p.submittingRound) return; resetPracticeSlotAssignments(item); updatePracticeSelection(); }
    else if (action === 'check') await record('check');
    else if (action === 'continue-incomplete-answer') { if (!practiceDialogBusy()) closeDialog(); }
    else if (action === 'confirm-incomplete-answer') await record('check', {confirmIncomplete:true, button});
    else if (action === 'retry-current') { const item = currentPracticeItem(); if (!item || item.submitting || state.practice?.submittingRound) return; resetPracticeSlotAssignments(item); item.checked = false; item.result = null; item.submitting = false; item.pendingAttempt = null; item.questionStartedAt = Date.now(); renderPractice(); }
    else if (action === 'previous-question') navigatePractice(-1);
    else if (action === 'next-question') navigatePractice(1);
    else if (action === 'submit-round') await submitPracticeRound();
    else if (action === 'continue-unanswered') continueUnansweredPractice();
    else if (action === 'confirm-submit-unanswered') await submitPracticeRound({confirmUnanswered:true, button});
    else if (action === 'exit-practice') openExitPracticeDialog();
    else if (action === 'confirm-exit-practice') await confirmExitPractice(button);
    else if (action === 'abandon-practice') await abandonPractice();
    else if (action === 'open-report') { state.report = (await api(`/api/reports/${button.dataset.id}`)).report; route('report', {reportId:state.report.id}); }
    else if (action === 'delete-report') openDeleteReportConfirm(button.dataset.id);
    else if (action === 'delete-report-confirm') {
      const id = $('#dialog').dataset.reportId;
      await api(`/api/reports/${id}`, {method:'DELETE'});
      closeDialog();
      toast('记录已删除');
      await renderReports();
    }
    else if (action === 'logout') { await api('/api/auth/logout', {method:'POST', body:'{}'}); showLogin(); }
    else if (action && action.startsWith('stats-') && typeof handleStatsAction === 'function') {
      const handled = handleStatsAction(action, button);
      if (handled) await handled;
    }
  } catch (error) { toast(error.message, true); }
});

document.addEventListener('change', event => {
  if (event.target.classList.contains('sentence-check')) { updateLibrarySelectionButtons(); return; }
  if (event.target.id === 'home-collection' || event.target.id === 'due-collection') { setActiveCollection(event.target.value); if (event.target.id === 'due-collection') state.homeDuePicker = true; renderHome().catch(error => toast(error.message, true)); return; }
  if (event.target.id === 'library-collection') { state.activeCollection = Number(event.target.value); localStorage.setItem('activeCollection', state.activeCollection); route('library', {collectionId:state.activeCollection, replace:true}); }
  if (event.target.id === 'library-sort') reloadLibrary();
});
document.addEventListener('input', event => {
  if (event.target.id === 'library-search') { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(reloadLibrary, 250); }
  if (event.target.id === 'custom-count') {
    bindCountPicker('count', Number(event.target.max), { mode: 'soft', clearActive: true });
  }
  if (event.target.id === 'due-custom-count') {
    const due = Number(event.target.max);
    bindCountPicker('due-count', due, {
      mode: 'strict',
      startAction: 'start-due-practice',
      defaultHint: `本句集有 ${due} 句待复习，可从到期最早的句子开始。`,
      clearActive: true,
    });
  }
  if (event.target.id === 'report-custom-count') {
    const max = Number(event.target.max);
    bindCountPicker('report-count', max, {
      mode: 'strict',
      startAction: 'start-report-round',
      defaultHint: '优先选择本轮未回答题目，其余按到期顺序补充。',
      clearActive: true,
    });
  }
});
document.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    if (event.target.id === 'login-form') { const body = Object.fromEntries(new FormData(event.target)); await api('/api/auth/login', {method:'POST', body:JSON.stringify(body)}); hideLogin(); await loadTimezoneState(); const entry = history.state || {route:'home'}; route(entry.route || 'home', {...entry, replace:true}); }
    else if (event.target.id === 'auth-form') { const form = new FormData(event.target), clearAuth = form.get('clearAuth') === 'on'; const body = {username:form.get('username'), password:form.get('password'), clearAuth}; await api('/api/settings/auth', {method:'PUT', body:JSON.stringify(body)}); toast(clearAuth ? '应用认证已关闭' : '访问认证已保存并立即生效'); await renderSettings(); }
    else if (event.target.id === 'timezone-form') { const tz = new FormData(event.target).get('timezone') || ''; const result = await api('/api/settings/timezone', {method:'PUT', body:JSON.stringify({timezone: tz})}); state.timezone = result.timezone || ''; state.dashboard = null; if (typeof window.clearStatsCache === 'function') window.clearStatsCache(); toast('时区设置已保存'); await renderSettings(); }
  } catch (error) { if (event.target.id === 'login-form') $('#login-error').textContent = error.message; else toast(error.data?.details?.join('；') || error.message, true); }
});
window.addEventListener('popstate', event => { const entry = event.state || {route:'home'}; route(entry.route || 'home', {...entry, fromPop:true}); });

(async () => {
  try {
    const hashRoute = window.location.hash.replace(/^#/, '');
    const initialRoute = new Set(['due', 'today']).has(hashRoute) ? hashRoute : 'home';
    const initialEntry = {route:initialRoute, collectionId:state.activeCollection, fromHome:Boolean(history.state?.fromHome)};
    const auth = await api('/api/auth/status');
    history.replaceState(initialEntry, '', `#${initialRoute}`);
    if (auth.configured && !auth.authenticated) { showLogin(); }
    else { await loadTimezoneState(); route(initialRoute, {...initialEntry, replace:true}); }
  } catch (error) { toast(error.message, true); }
})();
