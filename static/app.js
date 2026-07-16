const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const view = $('#view');
const state = {
  route: 'home', dashboard: null,
  activeCollection: Number(localStorage.getItem('activeCollection') || 0),
  draft: null, selectedChunks: [], editing: null, practice: null, report: null,
  routeMeta: {}, homeDuePicker: false,
};

function esc(value = '') { return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function rubyHtml(segments) {
  if (!Array.isArray(segments) || !segments.length) return '';
  return segments.map(seg => seg.ruby
    ? `<ruby>${esc(seg.text)}<rt>${esc(seg.ruby)}</rt></ruby>`
    : esc(seg.text)).join('');
}
function formatDate(value) { if (!value) return '从未'; return new Intl.DateTimeFormat('zh-CN', {dateStyle:'medium', timeStyle:'short'}).format(new Date(value)); }
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
function openDialog(html) { const el = $('#dialog'); el.innerHTML = `<div class="modal">${html}</div>`; el.classList.remove('hidden'); }
function closeDialog() { const el = $('#dialog'); el.classList.add('hidden'); el.innerHTML = ''; }
function updateMoveSelectedBtn() { const btn = $('#move-selected-btn'); if (btn) btn.disabled = !$$('.sentence-check:checked').length; }
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
  openDialog(`<h1>确认删除</h1><p style="text-align:left">删除句集会同时删除其中的全部 ${n} 句句子，以及这些句子的练习记录和记忆数据（正确/错误次数、连续答对、下次复习时间等复习历史），且不可恢复。</p><p class="status-note" style="text-align:left;margin-top:12px">可以先用批量转移把有用的句子移到别处。</p><div class="form-actions"><button class="btn outline" data-action="manage-collection">返回</button><button class="btn danger" data-action="delete-collection-confirm">确定删除</button></div>`);
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

const secondaryRoutes = new Set(['library', 'add', 'reports', 'report', 'settings', 'stats']);
function setChrome(practice = false) {
  const header = $('#main-header');
  header.classList.toggle('hidden', practice);
  if (!practice) {
    const secondary = secondaryRoutes.has(state.route);
    header.innerHTML = `<button class="brand plain-button" data-action="${secondary ? 'back' : 'home'}" aria-label="${secondary ? '返回上一页' : '返回首页'}">${secondary ? '<span class="back-arrow" aria-hidden="true">←</span>' : '<span class="brand-mark">文</span>'}<strong>句子重组</strong></button><button class="icon-button" data-route="settings" aria-label="设置" title="设置"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></svg></button>`;
  }
  $('#bottom-nav').classList.toggle('hidden', practice);
  $('#fab').classList.toggle('hidden', practice || state.route === 'add' || state.route === 'settings' || state.route === 'stats');
  $$('#bottom-nav button').forEach(button => button.classList.toggle('active', button.dataset.route === state.route));
}
function historyEntry(name) {
  return {route:name, collectionId:state.activeCollection, reportId:state.report?.id, editingId:state.editing?.id};
}
async function route(name, options = {}) {
  const allowed = new Set(['home','library','add','reports','report','settings','practice','stats']);
  if (!allowed.has(name)) name = 'home';
  if (state.route === 'stats' && name !== 'stats' && typeof destroyStatsCharts === 'function') destroyStatsCharts();
  state.route = name; state.routeMeta = options;
  if (!options.fromPop) history[options.replace ? 'replaceState' : 'pushState'](historyEntry(name), '', `#${name}`);
  setChrome(name === 'practice');
  if (!options.preserveScroll) window.scrollTo(0, 0);
  try {
    if (name === 'home') await renderHome();
    else if (name === 'library') await renderLibrary(options.collectionId || state.activeCollection);
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
}) {
  const cfg = countPickerIds(idPrefix);
  let optionsInner;
  if (emptyHtml != null && !max) {
    optionsInner = emptyHtml;
  } else {
    const nums = filterQuick ? quickOptions.filter(n => n <= max) : quickOptions;
    const buttons = nums.map(n => `<button class="${cfg.optionClass}" data-action="${cfg.setAction}" data-count="${n}">${n} 句</button>`).join('');
    const inputMax = idPrefix === 'count' ? Math.max(max, 1) : max;
    optionsInner = `${buttons}<button class="${cfg.optionClass} active" data-action="${cfg.setAction}" data-count="all">全部</button><label class="custom-count">自定义<input id="${cfg.inputId}" type="number" min="1" max="${inputMax}" placeholder="1-${max}"></label><button class="btn primary" data-action="${startAction}" ${!max ? 'disabled' : ''}>${startLabel}</button>`;
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
    const start = startAction ? $(`[data-action="${startAction}"]`, view) : null;
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
  view.innerHTML = `<section class="page home-page"><div class="page-head"><div><h1>根据中文翻译，补全日语句子</h1></div></div><div class="card hero-card"><div class="collection-title"><span class="collection-icon">文</span><div><h2>${esc(active?.name || '还没有句集')}</h2><p>${active?.learned || 0} 已学习 / ${active?.total || 0} 总数量</p></div></div><label class="home-collection-switch">切换句集<select id="home-collection" aria-label="切换句集">${collectionOptions(active?.id)}</select></label><div class="progress" aria-label="学习进度 ${progress}%"><span style="width:${progress}%"></span></div><div class="hero-bottom"><div class="metric"><strong>${active?.due || 0}</strong><span>待复习</span></div><div class="metric"><strong>${active?.today || 0}</strong><span>今日学习</span></div></div><button class="btn primary" data-action="start-due" ${!active?.due ? 'disabled' : ''}>开始句子重组</button></div>${state.homeDuePicker ? renderDuePicker(data, active) : ''}<div class="card section-card"><div class="section-title"><h2>句子合集</h2><button class="link-button" data-action="new-collection">＋ 新建</button></div>${data.collections.map(c => `<button class="collection-row" data-action="open-collection" data-id="${c.id}"><span class="row-icon">文</span><span class="row-main"><strong>${esc(c.name)}</strong><small>已学 ${c.learned}，共 ${c.total}</small></span><span class="arrow">›</span></button>`).join('')}</div><button class="card section-card home-stats-entry" data-route="reports"><div class="section-title"><h2>练习历史</h2><span class="arrow">›</span></div><p class="status-note" style="margin:0">每轮练习都会保留，可随时回看报告</p></button><button class="card section-card home-stats-entry" data-route="stats"><div class="section-title"><h2>数据统计</h2><span class="arrow">›</span></div><p class="status-note" style="margin:0">遗忘曲线 · 学习情况 · 记忆持久度</p></button></section>`;
  setChrome();
}

function addForm(data = {}) { return `<section class="page"><div class="page-head"><div><h1>${state.editing ? '编辑句子' : '添加句子'}</h1><p>输入中文和完整原句，再检查自动生成的词块。</p></div></div><div class="card form-card"><div class="form-grid"><label class="field full">所属句集<select id="collection">${collectionOptions(data.collection_id || state.activeCollection)}</select></label><label class="field">中文翻译<textarea id="chinese" placeholder="例如：即使下雨，我也想去散步。">${esc(data.chinese || '')}</textarea></label><label class="field">完整日语原句<textarea id="japanese" lang="ja" placeholder="例如：雨が降っても、散歩に行きたいです。">${esc(data.japanese || '')}</textarea></label></div><div class="form-actions"><button class="btn primary" data-action="organize">自动分块</button></div></div><div id="preview-slot"></div></section>`; }
async function renderAdd() { await ensureDashboard(); view.innerHTML = addForm(state.editing || {}); if (state.editing) { state.draft = {chunks:state.editing.chunks.map(x => ({...x})), source:'saved', sentenceFurigana:state.editing.furigana}; renderPreview(); } setChrome(); }
function renderPreview() {
  const slot = $('#preview-slot'); if (!slot || !state.draft) return;
  const previewJp = (Array.isArray(state.draft.sentenceFurigana) && state.draft.sentenceFurigana.length)
    ? rubyHtml(state.draft.sentenceFurigana) : esc($('#japanese').value);
  slot.innerHTML = `<div class="card preview"><div class="preview-head"><div><h3>分块预览</h3><p>确认原句与词块顺序后保存。</p></div></div><div class="preview-fields"><div><span>所属句集</span><strong>${esc($('#collection').selectedOptions[0]?.textContent || '')}</strong></div><div><span>中文翻译</span><p>${esc($('#chinese').value)}</p></div><div><span>日语原句</span><p class="preview-jp" lang="ja">${previewJp}</p></div></div><div class="chunk-list preview-chunks" aria-label="按原顺序排列的词块">${state.draft.chunks.map((c, i) => `<button class="chunk ${state.selectedChunks.includes(i) ? 'selected' : ''}" lang="ja" data-action="select-chunk" data-index="${i}">${esc(c.text)}</button>`).join('')}</div><div class="chunk-tools"><button class="btn outline" data-action="split-chunk">拆分词块</button><button class="btn outline" data-action="merge-chunks">合并相邻词块</button><button class="btn outline" data-action="edit-chunk">修改词块</button></div><p class="status-note">分块方式：SudachiPy + SudachiDict-full 多粒度分析</p><div class="form-actions"><button class="btn outline" data-action="organize">重新分块</button><button class="btn primary" data-action="save-sentence">确认保存</button></div></div>`;
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
  view.innerHTML = `<section class="page"><div class="page-head"><div><h1>句集详情</h1><p>筛选、查找，或勾选句子开始专项练习。</p></div><button class="btn primary" data-route="add">＋ 添加句子</button></div><div class="card practice-picker"><div><h2>开始练习</h2><p>从本句集中随机抽取题目。</p></div>${countPicker}</div><div class="toolbar"><select id="library-collection">${collectionOptions(state.activeCollection)}</select><input id="library-search" type="search" placeholder="搜索中文或日语"><select id="library-sort"><option value="created">按创建时间</option><option value="error">按错误率</option><option value="recent">按最近练习</option></select></div><div class="section-title"><h2>共 <span id="library-count">${total}</span> 条</h2><div><button class="btn outline" data-action="manage-collection">管理句集</button> <button class="btn outline" data-action="move-selected" id="move-selected-btn" disabled>转移选中句子</button> <button class="btn primary" data-action="practice-selected">专项练习</button></div></div><div id="library-list" class="card library-list"></div></section>`;
  renderLibraryRows(data.sentences); setChrome();
}
function renderLibraryRows(items) { const list = $('#library-list'); $('#library-count').textContent = items.length; list.innerHTML = items.length ? items.map(s => `<div class="library-row"><input type="checkbox" class="sentence-check" value="${s.id}" aria-label="选择句子"><div><div class="library-jp" lang="ja">${esc(s.japanese)}</div><div>${esc(s.chinese)}</div><div class="row-stats"><span>练习 ${s.study_count}</span><span>正确 ${s.correct_count}</span><span>错误 ${s.wrong_count}</span><span>连续 ${s.correct_streak}</span><span>下次 ${formatDate(s.next_review_at)}</span></div></div><div class="row-actions"><button class="small-btn" data-action="edit-sentence" data-id="${s.id}">编辑</button><button class="small-btn" data-action="delete-sentence" data-id="${s.id}">删除</button></div></div>`).join('') : `<div class="empty">这个句集还没有句子，先添加第一句吧。</div>`; updateMoveSelectedBtn(); }
async function reloadLibrary() { const query = new URLSearchParams({collectionId:$('#library-collection').value, search:$('#library-search').value, sort:$('#library-sort').value}); renderLibraryRows((await api('/api/sentences?' + query)).sentences); }

async function startPractice(payload) {
  const result = await api('/api/practice/sessions', {method:'POST', body:JSON.stringify(payload)});
  if (result.notice) toast(result.notice);
  state.practice = {sessionId:result.sessionId, sentences:result.sentences, index:0, selected:[], checked:false, result:null, candidates:[], submitting:false};
  prepareQuestion(); route('practice');
}
function shuffle(items) { const result = [...items]; for (let i = result.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [result[i], result[j]] = [result[j], result[i]]; } return result; }
function prepareQuestion() { const p = state.practice, s = p.sentences[p.index]; p.selected = []; p.checked = false; p.result = null; p.submitting = false; p.candidates = shuffle(s.chunks.map(c => c.id)); p.questionStartedAt = Date.now(); }
function selectionHtml(s, p, map) {
  // Grade by chunk text so duplicate surfaces (e.g. two 「し」) match regardless of which id instance was used.
  const correctTexts = (s.correctOrder || []).map(id => map[id]?.text || '');
  return p.selected.length
    ? `<div class="chosen-list">${p.selected.map((id, i) => {
        const text = map[id]?.text || '';
        const cls = p.checked ? (text === correctTexts[i] ? 'good' : 'bad') : '';
        return `<button class="chosen ${cls}" lang="ja" data-action="unchoose" data-index="${i}" ${p.checked ? 'disabled' : ''}>${esc(text)}</button>`;
      }).join('')}</div>`
    : `<div class="placeholder">看中文翻译，点击下方词块，组成句子</div>`;
}
function practiceReadyToCheck(p = state.practice) { return Boolean(p) && !p.checked && !p.submitting && p.selected.length === p.candidates.length; }
function updatePracticeSelection() {
  const p = state.practice, s = p.sentences[p.index], map = Object.fromEntries(s.chunks.map(c => [c.id, c]));
  const composer = $('#practice-composer'); if (!composer) return;
  composer.innerHTML = selectionHtml(s, p, map);
  $$('.candidate', view).forEach(button => { const used = p.selected.includes(button.dataset.id); button.disabled = used || p.checked || p.submitting; button.classList.toggle('used', used); });
  const checkButton = $('[data-action="check"]', view);
  if (checkButton) checkButton.disabled = !practiceReadyToCheck(p);
}
function renderPractice() {
  const p = state.practice; if (!p) return route('home', {replace:true}); const s = p.sentences[p.index], map = Object.fromEntries(s.chunks.map(c => [c.id, c])); const pct = Math.round(p.index * 100 / p.sentences.length), ready = practiceReadyToCheck(p), busy = p.submitting;
  view.innerHTML = `<section class="page practice-page"><div class="practice-nav"><button class="back" data-action="exit-practice">←　句子重组</button><div class="thin-progress"><span style="width:${pct}%"></span></div><button class="exit" data-action="exit-practice">${p.index + 1} / ${p.sentences.length}　退出</button></div><h1 class="practice-title">句子重组</h1><div class="prompt-scene"><div class="learner-art" aria-label="日语学习人物插图"><i class="body"></i><i class="head"></i><i class="hair"></i></div><div class="card speech">${esc(s.chinese)}</div></div><div id="practice-composer" class="card composer">${selectionHtml(s, p, map)}</div><div class="candidate-area"><div class="chunk-list">${p.candidates.map(id => `<button class="candidate ${p.selected.includes(id) ? 'used' : ''}" lang="ja" data-action="choose" data-id="${id}" ${p.selected.includes(id) || p.checked || busy ? 'disabled' : ''}>${esc(map[id].text)}</button>`).join('')}</div></div>${p.checked ? answerDetails(s, map, p) : ''}<div class="practice-actions"><button class="btn outline" data-action="skip" ${p.checked || busy ? 'disabled' : ''}>跳过练习</button><button class="btn ghost" data-action="reset" ${p.checked || busy ? 'disabled' : ''}>重置</button>${p.checked ? '<button class="btn outline retry-current" data-action="retry-current">重新练习本题</button>' : ''}<button class="btn primary" data-action="${p.checked ? 'next' : 'check'}" ${!p.checked && !ready ? 'disabled' : ''}>${p.checked ? '下一题' : '核对答案'}</button></div></section>`;
  setChrome(true);
}
function answerDetails(s, map, p) {
  const user = p.selected.map(id => map[id]?.text || '').join('');
  const correctJp = (Array.isArray(s.furigana) && s.furigana.length) ? rubyHtml(s.furigana) : esc(s.japanese);
  return `<div class="card answer-card"><h3>${p.result?.correct ? '回答正确' : '正确答案'}</h3>${!p.result?.correct ? `<div class="report-line"><span>你的排列</span><strong lang="ja">${esc(user || '（未作答）')}</strong></div>` : ''}<div class="correct-display" lang="ja">${correctJp}</div><p>${esc(s.chinese)}</p></div>`;
}
async function record(action) {
  const p = state.practice;
  if (!p || p.submitting) return;
  if (action === 'check' && !practiceReadyToCheck(p)) { toast('请先把所有词块摆放完整'); return; }
  const s = p.sentences[p.index];
  const durationMs = Math.max(0, Date.now() - (p.questionStartedAt || Date.now()));
  p.submitting = true;
  renderPractice();
  try {
    p.result = await api(`/api/practice/sessions/${p.sessionId}/attempts`, {method:'POST', body:JSON.stringify({sentenceId:s.id, action, answerOrder:p.selected, durationMs})});
    p.checked = true;
  } catch (error) {
    p.submitting = false;
    renderPractice();
    throw error;
  }
  p.submitting = false;
  renderPractice();
}
async function nextQuestion() { const p = state.practice; if (p.index < p.sentences.length - 1) { p.index++; prepareQuestion(); renderPractice(); return; } await api(`/api/practice/sessions/${p.sessionId}/complete`, {method:'POST', body:'{}'}); if (typeof window.clearStatsCache === 'function') window.clearStatsCache(); state.report = (await api(`/api/reports/${p.sessionId}`)).report; route('report', {reportId:p.sessionId}); }

async function renderReports() { const data = await api('/api/reports'); view.innerHTML = `<section class="page"><div class="page-head"><div><h1>练习历史</h1><p>每轮练习都会保留，可随时重新打开。</p></div></div><div class="card section-card">${data.reports.length ? data.reports.map(r => `<div class="history-row"><button class="row-open" data-action="open-report" data-id="${r.id}"><span class="row-icon">${r.accuracy}%</span><span class="row-main"><strong>${formatDate(r.completed_at)}</strong><small>共 ${r.total} · 对 ${r.correct} · 错 ${r.wrong} · 跳过 ${r.skipped}</small></span></button><div class="row-actions"><button class="small-btn" data-action="delete-report" data-id="${r.id}" aria-label="删除这条记录">删除</button><span class="arrow">›</span></div></div>`).join('') : '<div class="empty">完成一次练习后，报告会出现在这里。</div>'}</div></section>`; setChrome(); }
function renderReport() { const r = state.report; if (!r) return route('reports', {replace:true}); view.innerHTML = `<section class="page"><div class="page-head"><div><h1>本轮练习报告</h1><p>${formatDate(r.completed_at || r.created_at)}</p></div><div><button class="btn outline" data-action="toggle-wrong">只看错误</button> <button class="btn outline" data-action="home">返回首页</button> <button class="btn primary" data-action="retry-report">重新练习本轮</button> <button class="btn outline" data-action="retry-wrong" ${r.wrong === 0 ? 'disabled' : ''}>练习本轮错题</button></div></div><div class="report-summary"><div class="card stat-card"><strong>${r.total}</strong>总句数</div><div class="card stat-card"><strong>${r.correct}</strong>正确</div><div class="card stat-card"><strong>${r.wrong}</strong>错误</div><div class="card stat-card"><strong>${r.skipped}</strong>跳过</div><div class="card stat-card"><strong>${r.accuracy}%</strong>正确率</div></div><div id="report-items">${reportItems(r.items)}</div></section>`; setChrome(); }
function reportItems(items) { return items.map(item => `<article class="card report-item ${item.status}" data-status="${item.status}"><div class="section-title"><h3>${esc(item.chinese)}</h3><strong>${{correct:'正确',wrong:'错误',skipped:'跳过'}[item.status]}</strong></div><div class="report-line"><span>你的排列</span><div lang="ja">${esc(item.answerText || '（未作答）')}</div></div><div class="report-line"><span>正确句子</span><div lang="ja">${esc(item.japanese)}</div></div></article>`).join(''); }

async function renderSettings() {
  const authCfg = await api('/api/settings/auth');
  view.innerHTML = `<section class="page"><div class="page-head"><div><h1>设置</h1><p>管理网站访问认证、复习调度与使用说明。</p></div></div><div class="settings-grid"><form id="auth-form" class="card"><div class="settings-title"><div><h2>访问认证</h2><p>密码仅保存安全哈希，页面不会显示原密码。</p></div><span class="config-status ${authCfg.configured ? 'ok' : 'warn'}">${authCfg.configured ? '已启用' : '未启用'}</span></div><label class="field">用户名<input name="username" value="${esc(authCfg.username || '')}" autocomplete="username"></label><label class="field">新密码 ${authCfg.configured ? '<small>留空表示不修改</small>' : ''}<input name="password" type="password" autocomplete="new-password"></label><label class="check-row"><input name="clearAuth" type="checkbox">关闭应用认证</label><p class="status-note">关闭后将不再要求应用登录，请确认这符合你的访问策略。</p><div class="form-actions"><button class="btn primary" type="submit">保存认证设置</button></div></form><div class="card"><div class="settings-title"><div><h2>复习调度</h2><p>系统统一使用基于遗忘曲线的动态调度，原理如下。</p></div><span class="config-status ok">动态</span></div><p class="status-note">每个句子都有一个"记忆稳定度" S，遵循指数遗忘模型 R(t) = e^(−t/S)，t 是距上次复习的天数。下次复习时间取 R(t) 降到 90% 时的 t，也就是 S 越大间隔越长。</p><p class="status-note">每次作答会更新 S：第一次就答对（认识）S 翻倍；曾经答错、后来答对或错题重练答对（模糊）S 乘以 1.2；答错（忘记）S 重置为初始值 1.0 并立即到期；跳过不影响 S 和到期时间。S 的范围被限制在 0.3 到 365 天之间。</p><p class="status-note">「统计」页展示的理论遗忘曲线以经典艾宾浩斯曲线为参照（约一天后保留率 40%），并随着你的真实作答样本增多，逐渐向你的实际表现靠拢。</p></div><div class="card settings-help"><h2>使用说明</h2><p>输入中文翻译和完整日语原句，点击“自动分块”，检查词块后确认保存。</p><p>分块完全在本机使用 SudachiPy + SudachiDict-full 完成，不会把句子发送到外部服务。</p><p>可在「统计」页查看遗忘曲线、学习情况与记忆持久度。</p><button class="btn outline" data-action="logout">退出登录</button></div></div></section>`;
  setChrome();
}

document.addEventListener('click', async event => {
  if (event.target.id === 'dialog') { closeDialog(); return; }
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.route) { if (button.dataset.route === state.route) return; state.editing = null; route(button.dataset.route); return; }
  const action = button.dataset.action;
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
    else if (action === 'organize') { const japanese = $('#japanese').value, chinese = $('#chinese').value; button.disabled = true; const old = button.textContent; button.textContent = '正在分块…'; try { state.draft = await api('/api/sentences/organize', {method:'POST', body:JSON.stringify({japanese, chinese})}); state.selectedChunks = []; renderPreview(); } finally { button.disabled = false; button.textContent = old; } }
    else if (action === 'select-chunk') { const i = Number(button.dataset.index), at = state.selectedChunks.indexOf(i); if (at >= 0) state.selectedChunks.splice(at, 1); else { if (state.selectedChunks.length >= 2) state.selectedChunks.shift(); state.selectedChunks.push(i); } state.selectedChunks.sort((a,b) => a-b); renderPreview(); }
    else if (action === 'split-chunk') { if (state.selectedChunks.length !== 1) throw new Error('请先选中一个要拆分的词块'); const i = state.selectedChunks[0], item = state.draft.chunks[i], pos = Number(prompt(`“${item.text}” 在第几个字符后拆分？`, Math.max(1, Math.floor(item.text.length / 2)))); if (!Number.isInteger(pos) || pos <= 0 || pos >= item.text.length) throw new Error('拆分位置必须位于词块内部'); state.draft.chunks.splice(i, 1, {id:crypto.randomUUID().slice(0,12), text:item.text.slice(0,pos)}, {id:crypto.randomUUID().slice(0,12), text:item.text.slice(pos)}); state.selectedChunks = []; renderPreview(); }
    else if (action === 'merge-chunks') { const [a,b] = state.selectedChunks; if (state.selectedChunks.length !== 2 || b !== a + 1) throw new Error('请按顺序选中两个相邻词块'); const x = state.draft.chunks[a], y = state.draft.chunks[b]; state.draft.chunks.splice(a, 2, {id:crypto.randomUUID().slice(0,12), text:x.text + y.text}); state.selectedChunks = []; renderPreview(); }
    else if (action === 'edit-chunk') { if (state.selectedChunks.length !== 1) throw new Error('请先选中一个词块'); const i = state.selectedChunks[0], item = state.draft.chunks[i], text = prompt('词块文字（修改后仍须无损还原原句）：', item.text); if (text === null) return; if (!text) throw new Error('词块文字不能为空'); state.draft.chunks[i] = {id:item.id, text}; renderPreview(); }
    else if (action === 'save-sentence') { const payload = {collectionId:Number($('#collection').value), chinese:$('#chinese').value, japanese:$('#japanese').value, chunks:state.draft.chunks, correctOrder:state.draft.chunks.map(c => c.id)}; if (state.editing) await api(`/api/sentences/${state.editing.id}`, {method:'PUT', body:JSON.stringify(payload)}); else await api('/api/sentences', {method:'POST', body:JSON.stringify(payload)}); toast('句子已保存'); state.editing = null; state.draft = null; setTimeout(() => { const link = document.querySelector('link[href*="faces.css"]'); if (link) link.href = `/api/fonts/faces.css?t=${Date.now()}`; }, 2800); route('library'); }
    else if (action === 'practice-selected') { const ids = $$('.sentence-check:checked').map(x => Number(x.value)); if (!ids.length) throw new Error('请至少勾选一条句子'); await startPractice({sentenceIds:ids}); }
    else if (action === 'edit-sentence') { state.editing = (await api(`/api/sentences/${button.dataset.id}`)).sentence; route('add', {editingId:state.editing.id}); }
    else if (action === 'delete-sentence') { if (confirm('确定删除这条句子吗？')) { await api(`/api/sentences/${button.dataset.id}`, {method:'DELETE'}); reloadLibrary(); } }
    else if (action === 'close-dialog') closeDialog();
    else if (action === 'manage-collection') { await ensureDashboard(); openManageCollectionDialog(); }
    else if (action === 'rename-collection') { const name = $('#rename-collection-name')?.value.trim(); if (!name) throw new Error('句集名称不能为空'); const id = state.activeCollection; await api(`/api/collections/${id}`, {method:'PATCH', body:JSON.stringify({name})}); state.dashboard = null; closeDialog(); toast('句集已重命名'); await renderLibrary(id); }
    else if (action === 'delete-collection-ask') openDeleteCollectionConfirm();
    else if (action === 'delete-collection-confirm') { const id = state.activeCollection; await api(`/api/collections/${id}?cascade=1`, {method:'DELETE'}); state.dashboard = null; closeDialog(); toast('句集已删除'); await renderLibrary(); }
    else if (action === 'move-selected') { const ids = $$('.sentence-check:checked').map(x => Number(x.value)); if (!ids.length) throw new Error('请至少勾选一条句子'); await ensureDashboard(); openMoveSentencesDialog(ids); }
    else if (action === 'confirm-move-sentences') { const ids = ($('#dialog').dataset.moveIds || '').split(',').filter(Boolean).map(Number); const targetCollectionId = Number($('#move-target-collection')?.value); if (!ids.length) throw new Error('请至少勾选一条句子'); if (!targetCollectionId) throw new Error('请选择目标句集'); const result = await api('/api/sentences/move', {method:'POST', body:JSON.stringify({sentenceIds:ids, targetCollectionId})}); state.dashboard = null; closeDialog(); toast(`已转移 ${result.moved} 句`); await renderLibrary(state.activeCollection); }
    else if (action === 'choose') { const p = state.practice, id = button.dataset.id; if (!p || p.checked || p.submitting || !p.candidates.includes(id) || p.selected.includes(id) || p.selected.length >= p.candidates.length) return; p.selected.push(id); updatePracticeSelection(); }
    else if (action === 'unchoose') { const p = state.practice, index = Number(button.dataset.index); if (!p || p.checked || p.submitting || !Number.isInteger(index) || index < 0 || index >= p.selected.length) return; p.selected.splice(index, 1); updatePracticeSelection(); }
    else if (action === 'reset') { const p = state.practice; if (!p || p.checked || p.submitting) return; p.selected = []; updatePracticeSelection(); }
    else if (action === 'check') { if (!practiceReadyToCheck()) { toast('请先把所有词块摆放完整'); return; } await record('check'); }
    else if (action === 'skip') await record('skip');
    else if (action === 'retry-current') { state.practice.selected = []; state.practice.checked = false; state.practice.result = null; state.practice.submitting = false; state.practice.questionStartedAt = Date.now(); renderPractice(); }
    else if (action === 'next') await nextQuestion();
    else if (action === 'exit-practice') { if (confirm('退出后，本轮未完成的题目不会生成完整报告。确定退出吗？')) route('home'); }
    else if (action === 'open-report') { state.report = (await api(`/api/reports/${button.dataset.id}`)).report; route('report', {reportId:state.report.id}); }
    else if (action === 'delete-report') openDeleteReportConfirm(button.dataset.id);
    else if (action === 'delete-report-confirm') {
      const id = $('#dialog').dataset.reportId;
      await api(`/api/reports/${id}`, {method:'DELETE'});
      closeDialog();
      toast('记录已删除');
      await renderReports();
    }
    else if (action === 'retry-report') await startPractice({sentenceIds:state.report.items.map(x => x.id)});
    else if (action === 'retry-wrong') {
      const ids = (state.report?.items || []).filter(x => x.status === 'wrong').map(x => x.id);
      if (!ids.length) { toast('本轮没有错题'); return; }
      await startPractice({sentenceIds: ids, retryWrong: true});
    }
    else if (action === 'toggle-wrong') { const only = button.dataset.active !== '1'; button.dataset.active = only ? '1' : '0'; button.textContent = only ? '显示全部' : '只看错误'; $$('.report-item').forEach(x => x.classList.toggle('hidden', only && x.dataset.status !== 'wrong')); }
    else if (action === 'logout') { await api('/api/auth/logout', {method:'POST', body:'{}'}); showLogin(); }
    else if (action && action.startsWith('stats-') && typeof handleStatsAction === 'function') {
      const handled = handleStatsAction(action, button);
      if (handled) await handled;
    }
  } catch (error) { toast(error.message, true); }
});

document.addEventListener('change', event => {
  if (event.target.classList.contains('sentence-check')) { updateMoveSelectedBtn(); return; }
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
});
document.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    if (event.target.id === 'login-form') { const body = Object.fromEntries(new FormData(event.target)); await api('/api/auth/login', {method:'POST', body:JSON.stringify(body)}); hideLogin(); route('home', {replace:true}); }
    else if (event.target.id === 'auth-form') { const form = new FormData(event.target), clearAuth = form.get('clearAuth') === 'on'; const body = {username:form.get('username'), password:form.get('password'), clearAuth}; await api('/api/settings/auth', {method:'PUT', body:JSON.stringify(body)}); toast(clearAuth ? '应用认证已关闭' : '访问认证已保存并立即生效'); await renderSettings(); }
  } catch (error) { if (event.target.id === 'login-form') $('#login-error').textContent = error.message; else toast(error.data?.details?.join('；') || error.message, true); }
});
window.addEventListener('popstate', event => { const entry = event.state || {route:'home'}; route(entry.route || 'home', {...entry, fromPop:true}); });

(async () => { try { const auth = await api('/api/auth/status'); history.replaceState({route:'home'}, '', '#home'); if (auth.configured && !auth.authenticated) showLogin(); else route('home', {replace:true}); } catch (error) { toast(error.message, true); } })();
