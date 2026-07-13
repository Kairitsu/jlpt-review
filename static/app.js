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

const secondaryRoutes = new Set(['library', 'add', 'reports', 'report', 'settings']);
function setChrome(practice = false) {
  const header = $('#main-header');
  header.classList.toggle('hidden', practice);
  if (!practice) {
    const secondary = secondaryRoutes.has(state.route);
    header.innerHTML = `<button class="brand plain-button" data-action="${secondary ? 'back' : 'home'}" aria-label="${secondary ? '返回上一页' : '返回首页'}">${secondary ? '<span class="back-arrow" aria-hidden="true">←</span>' : '<span class="brand-mark">文</span>'}<strong>背句子</strong></button><button class="icon-button" data-route="settings" aria-label="设置" title="设置"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></svg></button>`;
  }
  $('#bottom-nav').classList.toggle('hidden', practice);
  $('#fab').classList.toggle('hidden', practice || state.route === 'add' || state.route === 'settings');
  $$('#bottom-nav button').forEach(button => button.classList.toggle('active', button.dataset.route === state.route));
}
function historyEntry(name) {
  return {route:name, collectionId:state.activeCollection, reportId:state.report?.id, editingId:state.editing?.id};
}
async function route(name, options = {}) {
  const allowed = new Set(['home','library','add','reports','report','settings','practice']);
  if (!allowed.has(name)) name = 'home';
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
  if (state.route === 'settings') return route('home');
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
function dueCountOptions(due) { return [5,10,20].filter(count => count <= due).map(count => `<button class="count-option due-count-option" data-action="set-due-count" data-count="${count}">${count} 句</button>`).join(''); }
function renderDuePicker(data, active) {
  const due = active?.due || 0;
  return `<div class="card practice-picker home-practice-picker"><div><h2>选择本轮复习</h2><p>先选句集，再决定本轮练习数量。</p><label class="field">练习句集<select id="due-collection" aria-label="练习句集">${dueCollectionOptions(active?.id)}</select></label></div><div class="count-options" role="group" aria-label="本轮待复习数量">${due ? `${dueCountOptions(due)}<button class="count-option due-count-option active" data-action="set-due-count" data-count="all">全部</button><label class="custom-count">自定义<input id="due-custom-count" type="number" min="1" max="${due}" placeholder="1-${due}"></label><button class="btn primary" data-action="start-due-practice">开始复习</button>` : '<span class="status-note">该句集当前没有待复习句子。</span><button class="btn primary" data-action="start-due-practice" disabled>开始复习</button>'}</div><p id="due-count-hint" class="status-note">${due ? `本句集有 ${due} 句待复习，可从到期最早的句子开始。` : '请选择有待复习句子的句集后再开始。'}</p></div>`;
}
function updateDueCountHint() {
  const input = $('#due-custom-count'), hint = $('#due-count-hint'), start = $('[data-action="start-due-practice"]', view);
  if (!input || !hint || !start) return;
  const due = Number(input.max), value = input.value.trim();
  if (!value) { start.disabled = false; hint.textContent = `本句集有 ${due} 句待复习，可从到期最早的句子开始。`; return; }
  const count = Number(value), valid = Number.isInteger(count) && count >= 1 && count <= due;
  start.disabled = !valid;
  hint.textContent = valid ? `将复习 ${count} 句。` : `请输入 1 到 ${due} 之间的整数。`;
}

async function renderHome() {
  const data = await ensureDashboard(); const active = data.collections.find(c => c.id === state.activeCollection) || data.collections[0]; const progress = active?.total ? Math.round(active.learned * 100 / active.total) : 0;
  view.innerHTML = `<section class="page home-page"><div class="page-head"><div><h1>今天也来背一句</h1><p>从中文出发，把日语句子拼回完整模样。</p></div></div><div class="card hero-card"><div class="collection-title"><span class="collection-icon">文</span><div><h2>${esc(active?.name || '还没有句集')}</h2><p>${active?.learned || 0} 已学习 / ${active?.total || 0} 总数量</p></div></div><label class="home-collection-switch">切换句集<select id="home-collection" aria-label="切换句集">${collectionOptions(active?.id)}</select></label><div class="progress" aria-label="学习进度 ${progress}%"><span style="width:${progress}%"></span></div><div class="hero-bottom"><div class="metric"><strong>${active?.due || 0}</strong><span>待复习</span></div><div class="metric"><strong>${active?.today || 0}</strong><span>今日学习</span></div></div><button class="btn primary" data-action="start-due" ${!active?.due ? 'disabled' : ''}>开始背句子</button></div>${state.homeDuePicker ? renderDuePicker(data, active) : ''}<div class="card section-card"><div class="section-title"><h2>句子合集</h2><button class="link-button" data-action="new-collection">＋ 新建</button></div>${data.collections.map(c => `<button class="collection-row" data-action="open-collection" data-id="${c.id}"><span class="row-icon">文</span><span class="row-main"><strong>${esc(c.name)}</strong><small>已学 ${c.learned}，共 ${c.total}</small></span><span class="arrow">›</span></button>`).join('')}</div></section>`;
  setChrome();
}

function addForm(data = {}) { return `<section class="page"><div class="page-head"><div><h1>${state.editing ? '编辑句子' : '添加句子'}</h1><p>输入中文和完整原句，再检查自动生成的词块。</p></div></div><div class="card form-card"><div class="form-grid"><label class="field full">所属句集<select id="collection">${collectionOptions(data.collection_id || state.activeCollection)}</select></label><label class="field">中文翻译<textarea id="chinese" placeholder="例如：即使下雨，我也想去散步。">${esc(data.chinese || '')}</textarea></label><label class="field">完整日语原句<textarea id="japanese" lang="ja" placeholder="例如：雨が降っても、散歩に行きたいです。">${esc(data.japanese || '')}</textarea></label></div><div class="form-actions"><button class="btn primary" data-action="organize">自动分块</button></div></div><div id="preview-slot"></div></section>`; }
async function renderAdd() { await ensureDashboard(); view.innerHTML = addForm(state.editing || {}); if (state.editing) { state.draft = {chunks:state.editing.chunks.map(x => ({...x})), source:'saved'}; renderPreview(); } setChrome(); }
function renderPreview() {
  const slot = $('#preview-slot'); if (!slot || !state.draft) return;
  slot.innerHTML = `<div class="card preview"><div class="preview-head"><div><h3>分块预览</h3><p>确认原句与词块顺序后保存。</p></div></div><div class="preview-fields"><div><span>所属句集</span><strong>${esc($('#collection').selectedOptions[0]?.textContent || '')}</strong></div><div><span>中文翻译</span><p>${esc($('#chinese').value)}</p></div><div><span>日语原句</span><p class="preview-jp" lang="ja">${esc($('#japanese').value)}</p></div></div><div class="chunk-list preview-chunks" aria-label="按原顺序排列的词块">${state.draft.chunks.map((c, i) => `<button class="chunk ${state.selectedChunks.includes(i) ? 'selected' : ''}" data-action="select-chunk" data-index="${i}">${esc(c.text)}</button>`).join('')}</div><div class="chunk-tools"><button class="btn outline" data-action="split-chunk">拆分词块</button><button class="btn outline" data-action="merge-chunks">合并相邻词块</button><button class="btn outline" data-action="edit-chunk">修改词块</button></div><p class="status-note">分块方式：SudachiPy + SudachiDict-full 多粒度分析</p><div class="form-actions"><button class="btn outline" data-action="organize">重新分块</button><button class="btn primary" data-action="save-sentence">确认保存</button></div></div>`;
}

async function renderLibrary(collectionId = state.activeCollection) {
  await ensureDashboard(); state.activeCollection = Number(collectionId) || state.activeCollection; localStorage.setItem('activeCollection', state.activeCollection);
  const data = await api(`/api/sentences?collectionId=${state.activeCollection}`); const total = data.sentences.length;
  view.innerHTML = `<section class="page"><div class="page-head"><div><h1>句集详情</h1><p>筛选、查找，或勾选句子开始专项练习。</p></div><button class="btn primary" data-route="add">＋ 添加句子</button></div><div class="card practice-picker"><div><h2>开始练习</h2><p>从本句集中随机抽取题目。</p></div><div class="count-options" role="group" aria-label="本轮题目数量">${[5,10,20].map(n => `<button class="count-option" data-action="set-count" data-count="${n}">${n} 句</button>`).join('')}<button class="count-option active" data-action="set-count" data-count="all">全部</button><label class="custom-count">自定义<input id="custom-count" type="number" min="1" max="${Math.max(total, 1)}" placeholder="1-${total}"></label><button class="btn primary" data-action="start-collection" ${!total ? 'disabled' : ''}>开始练习</button></div><p id="count-hint" class="status-note">本句集共 ${total} 句。</p></div><div class="toolbar"><select id="library-collection">${collectionOptions(state.activeCollection)}</select><input id="library-search" type="search" placeholder="搜索中文或日语"><select id="library-sort"><option value="created">按创建时间</option><option value="error">按错误率</option><option value="recent">按最近练习</option></select></div><div class="section-title"><h2>共 <span id="library-count">${total}</span> 条</h2><div><button class="btn outline" data-action="manage-collection">管理句集</button> <button class="btn primary" data-action="practice-selected">专项练习</button></div></div><div id="library-list" class="card library-list"></div></section>`;
  renderLibraryRows(data.sentences); setChrome();
}
function renderLibraryRows(items) { const list = $('#library-list'); $('#library-count').textContent = items.length; list.innerHTML = items.length ? items.map(s => `<div class="library-row"><input type="checkbox" class="sentence-check" value="${s.id}" aria-label="选择句子"><div><div class="library-jp" lang="ja">${esc(s.japanese)}</div><div>${esc(s.chinese)}</div><div class="row-stats"><span>练习 ${s.study_count}</span><span>正确 ${s.correct_count}</span><span>错误 ${s.wrong_count}</span><span>连续 ${s.correct_streak}</span><span>下次 ${formatDate(s.next_review_at)}</span></div></div><div class="row-actions"><button class="small-btn" data-action="edit-sentence" data-id="${s.id}">编辑</button><button class="small-btn" data-action="delete-sentence" data-id="${s.id}">删除</button></div></div>`).join('') : `<div class="empty">这个句集还没有句子，先添加第一句吧。</div>`; }
async function reloadLibrary() { const query = new URLSearchParams({collectionId:$('#library-collection').value, search:$('#library-search').value, sort:$('#library-sort').value}); renderLibraryRows((await api('/api/sentences?' + query)).sentences); }

async function startPractice(payload) {
  const result = await api('/api/practice/sessions', {method:'POST', body:JSON.stringify(payload)});
  if (result.notice) toast(result.notice);
  state.practice = {sessionId:result.sessionId, sentences:result.sentences, index:0, selected:[], checked:false, result:null, candidates:[], submitting:false};
  prepareQuestion(); route('practice');
}
function shuffle(items) { const result = [...items]; for (let i = result.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [result[i], result[j]] = [result[j], result[i]]; } return result; }
function prepareQuestion() { const p = state.practice, s = p.sentences[p.index]; p.selected = []; p.checked = false; p.result = null; p.submitting = false; p.candidates = shuffle(s.chunks.map(c => c.id)); }
function selectionHtml(s, p, map) { return p.selected.length ? `<div class="chosen-list">${p.selected.map((id, i) => `<button class="chosen ${p.checked ? (id === s.correctOrder[i] ? 'good' : 'bad') : ''}" data-action="unchoose" data-index="${i}" ${p.checked ? 'disabled' : ''}>${esc(map[id]?.text || '')}</button>`).join('')}</div>` : `<div class="placeholder">看中文翻译，点击下方词块，组成句子</div>`; }
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
  view.innerHTML = `<section class="page practice-page"><div class="practice-nav"><button class="back" data-action="exit-practice">←　背句子</button><div class="thin-progress"><span style="width:${pct}%"></span></div><button class="exit" data-action="exit-practice">${p.index + 1} / ${p.sentences.length}　退出</button></div><h1 class="practice-title">句子拼写</h1><div class="prompt-scene"><div class="learner-art" aria-label="日语学习人物插图"><i class="body"></i><i class="head"></i><i class="hair"></i></div><div class="card speech">${esc(s.chinese)}</div></div><div id="practice-composer" class="card composer">${selectionHtml(s, p, map)}</div><div class="candidate-area"><div class="chunk-list">${p.candidates.map(id => `<button class="candidate ${p.selected.includes(id) ? 'used' : ''}" data-action="choose" data-id="${id}" ${p.selected.includes(id) || p.checked || busy ? 'disabled' : ''}>${esc(map[id].text)}</button>`).join('')}</div></div>${p.checked ? answerDetails(s, map, p) : ''}<div class="practice-actions"><button class="btn outline" data-action="skip" ${p.checked || busy ? 'disabled' : ''}>跳过练习</button><button class="btn ghost" data-action="reset" ${p.checked || busy ? 'disabled' : ''}>重置</button>${p.checked ? '<button class="btn outline retry-current" data-action="retry-current">重新练习本题</button>' : ''}<button class="btn primary" data-action="${p.checked ? 'next' : 'check'}" ${!p.checked && !ready ? 'disabled' : ''}>${p.checked ? '下一题' : '核对答案'}</button></div></section>`;
  setChrome(true);
}
function answerDetails(s, map, p) { const user = p.selected.map(id => map[id]?.text || '').join(''); return `<div class="card answer-card"><h3>${p.result?.correct ? '回答正确' : '正确答案'}</h3>${!p.result?.correct ? `<div class="report-line"><span>你的排列</span><strong lang="ja">${esc(user || '（未作答）')}</strong></div>` : ''}<div class="correct-display" lang="ja">${esc(s.japanese)}</div><p>${esc(s.chinese)}</p></div>`; }
async function record(action) {
  const p = state.practice;
  if (!p || p.submitting) return;
  if (action === 'check' && !practiceReadyToCheck(p)) { toast('请先把所有词块摆放完整'); return; }
  const s = p.sentences[p.index];
  p.submitting = true;
  renderPractice();
  try {
    p.result = await api(`/api/practice/sessions/${p.sessionId}/attempts`, {method:'POST', body:JSON.stringify({sentenceId:s.id, action, answerOrder:p.selected})});
    p.checked = true;
  } catch (error) {
    p.submitting = false;
    renderPractice();
    throw error;
  }
  p.submitting = false;
  renderPractice();
}
async function nextQuestion() { const p = state.practice; if (p.index < p.sentences.length - 1) { p.index++; prepareQuestion(); renderPractice(); return; } await api(`/api/practice/sessions/${p.sessionId}/complete`, {method:'POST', body:'{}'}); state.report = (await api(`/api/reports/${p.sessionId}`)).report; route('report', {reportId:p.sessionId}); }

async function renderReports() { const data = await api('/api/reports'); view.innerHTML = `<section class="page"><div class="page-head"><div><h1>练习历史</h1><p>每轮练习都会保留，可随时重新打开。</p></div></div><div class="card section-card">${data.reports.length ? data.reports.map(r => `<button class="history-row" data-action="open-report" data-id="${r.id}"><span class="row-icon">${r.accuracy}%</span><span class="row-main"><strong>${formatDate(r.completed_at)}</strong><small>共 ${r.total} · 对 ${r.correct} · 错 ${r.wrong} · 跳过 ${r.skipped}</small></span><span class="arrow">›</span></button>`).join('') : '<div class="empty">完成一次练习后，报告会出现在这里。</div>'}</div></section>`; setChrome(); }
function renderReport() { const r = state.report; if (!r) return route('reports', {replace:true}); view.innerHTML = `<section class="page"><div class="page-head"><div><h1>本轮练习报告</h1><p>${formatDate(r.completed_at || r.created_at)}</p></div><div><button class="btn outline" data-action="toggle-wrong">只看错误</button> <button class="btn primary" data-action="retry-report">重新练习本轮</button></div></div><div class="report-summary"><div class="card stat-card"><strong>${r.total}</strong>总句数</div><div class="card stat-card"><strong>${r.correct}</strong>正确</div><div class="card stat-card"><strong>${r.wrong}</strong>错误</div><div class="card stat-card"><strong>${r.skipped}</strong>跳过</div><div class="card stat-card"><strong>${r.accuracy}%</strong>正确率</div></div><div id="report-items">${reportItems(r.items)}</div></section>`; setChrome(); }
function reportItems(items) { return items.map(item => `<article class="card report-item ${item.status}" data-status="${item.status}"><div class="section-title"><h3>${esc(item.chinese)}</h3><strong>${{correct:'正确',wrong:'错误',skipped:'跳过'}[item.status]}</strong></div><div class="report-line"><span>你的排列</span><div lang="ja">${esc(item.answerText || '（未作答）')}</div></div><div class="report-line"><span>正确句子</span><div lang="ja">${esc(item.japanese)}</div></div></article>`).join(''); }

async function renderSettings() {
  const authCfg = await api('/api/settings/auth');
  view.innerHTML = `<section class="page"><div class="page-head"><div><h1>设置</h1><p>管理网站访问认证并查看使用说明。</p></div></div><div class="settings-grid"><form id="auth-form" class="card"><div class="settings-title"><div><h2>访问认证</h2><p>密码仅保存安全哈希，页面不会显示原密码。</p></div><span class="config-status ${authCfg.configured ? 'ok' : 'warn'}">${authCfg.configured ? '已启用' : '未启用'}</span></div><label class="field">用户名<input name="username" value="${esc(authCfg.username || '')}" autocomplete="username"></label><label class="field">新密码 ${authCfg.configured ? '<small>留空表示不修改</small>' : ''}<input name="password" type="password" autocomplete="new-password"></label><label class="check-row"><input name="clearAuth" type="checkbox">关闭应用认证</label><p class="status-note">关闭后将不再要求应用登录，请确认这符合你的访问策略。</p><div class="form-actions"><button class="btn primary" type="submit">保存认证设置</button></div></form><div class="card settings-help"><h2>使用说明</h2><p>输入中文翻译和完整日语原句，点击“自动分块”，检查词块后确认保存。</p><p>分块完全在本机使用 SudachiPy + SudachiDict-full 完成，不会把句子发送到外部服务。</p><p>连续答对后的复习间隔为 1、3、7、14、30 天；答错会立即回到待复习。</p><button class="btn outline" data-action="logout">退出登录</button></div></div></section>`;
  setChrome();
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.route) { if (button.dataset.route === state.route) return; state.editing = null; route(button.dataset.route); return; }
  const action = button.dataset.action;
  try {
    if (action === 'home') route('home');
    else if (action === 'back') navigateBack();
    else if (action === 'new-collection') { const name = prompt('新句集名称：'); if (name) { await api('/api/collections', {method:'POST', body:JSON.stringify({name})}); state.dashboard = null; await renderHome(); } }
    else if (action === 'open-collection') { state.activeCollection = Number(button.dataset.id); route('library', {collectionId:state.activeCollection}); }
    else if (action === 'start-due') { const active = state.dashboard?.collections.find(c => c.id === state.activeCollection); if (!active?.due) { toast('当前句集没有待复习句子'); return; } state.homeDuePicker = true; await renderHome(); }
    else if (action === 'set-due-count') { $$('.due-count-option', view).forEach(option => option.classList.toggle('active', option === button)); $('#due-custom-count').value = ''; updateDueCountHint(); }
    else if (action === 'start-due-practice') { const collectionId = Number($('#due-collection')?.value), active = state.dashboard?.collections.find(c => c.id === collectionId); if (!active?.due) { toast('所选句集当前没有待复习句子'); return; } const custom = $('#due-custom-count')?.value.trim(); if (custom) { const count = Number(custom); if (!Number.isInteger(count) || count < 1 || count > active.due) { toast(`请输入 1 到 ${active.due} 之间的整数`); return; } } const selected = $('.due-count-option.active', view)?.dataset.count || 'all'; state.homeDuePicker = false; await startPractice({collectionId, count:custom || selected}); }
    else if (action === 'set-count') { $$('.count-option').forEach(x => x.classList.toggle('active', x === button)); $('#custom-count').value = ''; }
    else if (action === 'start-collection') { const custom = $('#custom-count').value; const selected = $('.count-option.active')?.dataset.count || 'all'; await startPractice({scope:'collection', collectionId:state.activeCollection, count:custom || selected}); }
    else if (action === 'organize') { const japanese = $('#japanese').value, chinese = $('#chinese').value; button.disabled = true; const old = button.textContent; button.textContent = '正在分块…'; try { state.draft = await api('/api/sentences/organize', {method:'POST', body:JSON.stringify({japanese, chinese})}); state.selectedChunks = []; renderPreview(); } finally { button.disabled = false; button.textContent = old; } }
    else if (action === 'select-chunk') { const i = Number(button.dataset.index), at = state.selectedChunks.indexOf(i); if (at >= 0) state.selectedChunks.splice(at, 1); else { if (state.selectedChunks.length >= 2) state.selectedChunks.shift(); state.selectedChunks.push(i); } state.selectedChunks.sort((a,b) => a-b); renderPreview(); }
    else if (action === 'split-chunk') { if (state.selectedChunks.length !== 1) throw new Error('请先选中一个要拆分的词块'); const i = state.selectedChunks[0], item = state.draft.chunks[i], pos = Number(prompt(`“${item.text}” 在第几个字符后拆分？`, Math.max(1, Math.floor(item.text.length / 2)))); if (!Number.isInteger(pos) || pos <= 0 || pos >= item.text.length) throw new Error('拆分位置必须位于词块内部'); state.draft.chunks.splice(i, 1, {id:crypto.randomUUID().slice(0,12), text:item.text.slice(0,pos)}, {id:crypto.randomUUID().slice(0,12), text:item.text.slice(pos)}); state.selectedChunks = []; renderPreview(); }
    else if (action === 'merge-chunks') { const [a,b] = state.selectedChunks; if (state.selectedChunks.length !== 2 || b !== a + 1) throw new Error('请按顺序选中两个相邻词块'); const x = state.draft.chunks[a], y = state.draft.chunks[b]; state.draft.chunks.splice(a, 2, {id:crypto.randomUUID().slice(0,12), text:x.text + y.text}); state.selectedChunks = []; renderPreview(); }
    else if (action === 'edit-chunk') { if (state.selectedChunks.length !== 1) throw new Error('请先选中一个词块'); const i = state.selectedChunks[0], item = state.draft.chunks[i], text = prompt('词块文字（修改后仍须无损还原原句）：', item.text); if (text === null) return; if (!text) throw new Error('词块文字不能为空'); state.draft.chunks[i] = {id:item.id, text}; renderPreview(); }
    else if (action === 'save-sentence') { const payload = {collectionId:Number($('#collection').value), chinese:$('#chinese').value, japanese:$('#japanese').value, chunks:state.draft.chunks, correctOrder:state.draft.chunks.map(c => c.id)}; if (state.editing) await api(`/api/sentences/${state.editing.id}`, {method:'PUT', body:JSON.stringify(payload)}); else await api('/api/sentences', {method:'POST', body:JSON.stringify(payload)}); toast('句子已保存'); state.editing = null; state.draft = null; route('library'); }
    else if (action === 'practice-selected') { const ids = $$('.sentence-check:checked').map(x => Number(x.value)); if (!ids.length) throw new Error('请至少勾选一条句子'); await startPractice({sentenceIds:ids}); }
    else if (action === 'edit-sentence') { state.editing = (await api(`/api/sentences/${button.dataset.id}`)).sentence; route('add', {editingId:state.editing.id}); }
    else if (action === 'delete-sentence') { if (confirm('确定删除这条句子吗？')) { await api(`/api/sentences/${button.dataset.id}`, {method:'DELETE'}); reloadLibrary(); } }
    else if (action === 'manage-collection') { const current = state.dashboard.collections.find(c => c.id === state.activeCollection), choice = prompt('输入“重命名”或“删除”：', '重命名'); if (choice === '重命名') { const name = prompt('新名称：', current.name); if (name) { await api(`/api/collections/${current.id}`, {method:'PATCH', body:JSON.stringify({name})}); state.dashboard = null; await renderLibrary(current.id); } } if (choice === '删除' && confirm(`确定删除空句集“${current.name}”吗？`)) { await api(`/api/collections/${current.id}`, {method:'DELETE'}); state.dashboard = null; await renderLibrary(); } }
    else if (action === 'choose') { const p = state.practice, id = button.dataset.id; if (!p || p.checked || p.submitting || !p.candidates.includes(id) || p.selected.includes(id) || p.selected.length >= p.candidates.length) return; p.selected.push(id); updatePracticeSelection(); }
    else if (action === 'unchoose') { const p = state.practice, index = Number(button.dataset.index); if (!p || p.checked || p.submitting || !Number.isInteger(index) || index < 0 || index >= p.selected.length) return; p.selected.splice(index, 1); updatePracticeSelection(); }
    else if (action === 'reset') { const p = state.practice; if (!p || p.checked || p.submitting) return; p.selected = []; updatePracticeSelection(); }
    else if (action === 'check') { if (!practiceReadyToCheck()) { toast('请先把所有词块摆放完整'); return; } await record('check'); }
    else if (action === 'skip') await record('skip');
    else if (action === 'retry-current') { state.practice.selected = []; state.practice.checked = false; state.practice.result = null; state.practice.submitting = false; renderPractice(); }
    else if (action === 'next') await nextQuestion();
    else if (action === 'exit-practice') { if (confirm('退出后，本轮未完成的题目不会生成完整报告。确定退出吗？')) route('home'); }
    else if (action === 'open-report') { state.report = (await api(`/api/reports/${button.dataset.id}`)).report; route('report', {reportId:state.report.id}); }
    else if (action === 'retry-report') await startPractice({sentenceIds:state.report.items.map(x => x.id)});
    else if (action === 'toggle-wrong') { const only = button.dataset.active !== '1'; button.dataset.active = only ? '1' : '0'; button.textContent = only ? '显示全部' : '只看错误'; $$('.report-item').forEach(x => x.classList.toggle('hidden', only && x.dataset.status !== 'wrong')); }
    else if (action === 'logout') { await api('/api/auth/logout', {method:'POST', body:'{}'}); showLogin(); }
  } catch (error) { toast(error.message, true); }
});

document.addEventListener('change', event => {
  if (event.target.id === 'home-collection' || event.target.id === 'due-collection') { setActiveCollection(event.target.value); if (event.target.id === 'due-collection') state.homeDuePicker = true; renderHome().catch(error => toast(error.message, true)); return; }
  if (event.target.id === 'library-collection') { state.activeCollection = Number(event.target.value); localStorage.setItem('activeCollection', state.activeCollection); route('library', {collectionId:state.activeCollection, replace:true}); }
  if (event.target.id === 'library-sort') reloadLibrary();
});
document.addEventListener('input', event => {
  if (event.target.id === 'library-search') { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(reloadLibrary, 250); }
  if (event.target.id === 'custom-count' && event.target.value) { $$('.count-option').forEach(x => x.classList.remove('active')); const total = Number(event.target.max); if (Number(event.target.value) > total) $('#count-hint').textContent = `当前句集只有 ${total} 句，开始时将自动调整为全部。`; else $('#count-hint').textContent = `将随机练习 ${Math.max(1, Number(event.target.value) || 1)} 句。`; }
  if (event.target.id === 'due-custom-count') { $$('.due-count-option', view).forEach(option => option.classList.remove('active')); updateDueCountHint(); }
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
