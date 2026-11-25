const API_BASE = window.location.protocol === 'file:' ? 'http://127.0.0.1:5174' : '';
// Navigation and unsaved edits only; saved parameters always come from the API.
const state = {personas: {
  xiaotian: {id:'xiaotian', name:'小甜', schedule:{}, scheduleDates:{}},
  jisoo: {id:'jisoo', name:'智秀', schedule:{}, scheduleDates:{}}
}};
const initialRoute = new URLSearchParams(window.location.search);
const viewNames = {home:'选择你的伙伴', onboarding:'创建伙伴', overview:'我的人设', intimacy:'关系与亲密度', schedule:'日程安排', location:'人物位置',
  persona:'个性化设置', providers:'能力设置', deployment:'部署绑定', runtime:'Runtime 隔离', flow:'工作流诊断'};
let activeView = Object.hasOwn(viewNames, initialRoute.get('view')) ? initialRoute.get('view') : 'home';
const requestedPersona = initialRoute.get('persona_id');
let activePersona = Object.hasOwn(state.personas, requestedPersona) ? requestedPersona : 'xiaotian';
let companionCatalogLoading = /^(companion_[a-f0-9]{32})$/.test(requestedPersona || '');
let scheduleViewMode = new URLSearchParams(window.location.search).get('schedule_view') === 'week' ? 'week' : 'month';
let activeScheduleDate = todayKey();
let scheduleMonthCursor = dateFromKey(activeScheduleDate);
scheduleMonthCursor.setDate(1);
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const WEEK_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
function clone(value) { return JSON.parse(JSON.stringify(value)); }
const scheduleDocuments = new Map();
const runtimeDays = new Map();
async function apiJSON(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout ?? 8000);
  try {
    const response = await fetch(`${API_BASE}${path}`, { ...options, signal: controller.signal });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || (response.status === 404 ? '当前服务没有运行时接口，请启动本地控制台服务' : `HTTP ${response.status}`));
    }
    return await response.json();
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('本地服务响应超时，请重试');
    if (error instanceof TypeError) throw new Error('无法连接本地控制台服务');
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
async function loadBranchLabel() {
  const label = $('#branchName');
  if (!label) return;
  try {
    const health = await apiJSON('/api/health', {cache: 'no-store'});
    label.textContent = health.service === 'persona-console' && health.branch ? health.branch : '分支未知';
  } catch {
    label.textContent = '分支未知';
  }
}
function invalidateDays(persona) {
  for (const key of runtimeDays.keys()) if (key.startsWith(`${persona}:`)) runtimeDays.delete(key);
}
function columns(groups) {
  return Object.fromEntries(Object.entries(groups || {}).map(([key, blocks]) =>
    [key, blocks.map(block => [block.start, block.end, block.activity, block.tone || ''])]));
}
async function loadSchedule(persona) {
  const doc = scheduleDocuments.get(persona);
  if (doc.loading || doc.busy) return;
  doc.loading = true; doc.error = null;
  try {
    doc.data = await apiJSON(`/api/schedule?persona_id=${encodeURIComponent(persona)}`);
    state.personas[persona].schedule = columns(doc.data.week);
    state.personas[persona].scheduleDates = columns(doc.data.dates);
  } catch (error) { doc.error = error.message; }
  finally {
    doc.loading = false;
    if (activeView === 'schedule' && activePersona === persona) renderSchedule();
  }
}

function toast(message) {
  const node = $('#toast'); node.textContent = message; node.classList.add('show');
  clearTimeout(window.__toastTimer); window.__toastTimer = setTimeout(() => node.classList.remove('show'), 2400);
}
function esc(value) {
  if (value === null || value === undefined || value === '') return '<span class="null-value">null</span>';
  return String(value).replace(/[&<>'"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c]));
}
function attr(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c])); }
function personaSelector() {
  return `<label class="persona-switch">当前人物<select aria-label="当前人物" data-persona-switch>${Object.entries(state.personas).map(([id, persona]) => `<option value="${id}" ${id === activePersona ? 'selected' : ''}>${attr(persona.name)}</option>`).join('')}</select></label>`;
}
function bindPersonaSwitch(root) {
  root.querySelector('[data-persona-switch]')?.addEventListener('change', event => { activePersona = event.target.value; persistView(); render(); });
}
function persistView() {
  if (['home', 'onboarding'].includes(activeView)) return;
  const url = new URL(window.location);
  url.searchParams.set('view', activeView);
  if (activePersona.startsWith('companion_') || activePersona === requestedPersona) url.searchParams.set('persona_id', activePersona);
  else url.searchParams.delete('persona_id');
  history.replaceState(null, '', url);
}
function writeHeaders(persona) {
  const token = persona.startsWith('companion_') ? window.PersonaOnboarding?.token() : null;
  return {'Content-Type':'application/json', ...(token ? {'X-Console-Token':token} : {})};
}
function todayKey(date) {
  if (!date) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date()).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
function dateFromKey(key) { const [year, month, day] = key.split('-').map(Number); return new Date(year, month - 1, day); }
function weekdayLabelForDate(date) { return WEEK_LABELS[(date.getDay() + 6) % 7]; }
function monthLabel(date) { return `${date.getFullYear()}年${date.getMonth() + 1}月`; }
function scheduleForDate(persona, dateKey) {
  // Month cells and the editor read the same resolved day, including explicit empty overrides.
  return (runtimeDays.get(`${persona.id}:${dateKey}`)?.day?.slots || []).map(slot => [slot.start, slot.end, slot.activity, slot.tone || '']);
}
function hasScheduleOverride(persona, dateKey) { return Boolean(persona.scheduleDates && Object.prototype.hasOwnProperty.call(persona.scheduleDates, dateKey)); }
function render() {
  window.LocationPage?.dispose();
  const creation = ['home', 'onboarding'].includes(activeView);
  $('.app-shell').classList.toggle('companion-shell', creation);
  $$('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === activeView));
  $$('.view').forEach(view => {
    const active = view.id === `view-${activeView}`;
    view.classList.toggle('active-view', active);
    if (!active) view.replaceChildren();
  });
  $('#pageTitle').textContent = viewNames[activeView];
  $('#breadcrumb').textContent = activeView.toUpperCase();
  if (companionCatalogLoading && !creation) return;
  ({home:() => window.PersonaOnboarding?.home(), onboarding:() => window.PersonaOnboarding?.wizard(),
    overview:renderOverview, intimacy:renderIntimacy, schedule:renderSchedule, location:renderLocation,
    persona:renderPersona, providers:renderProviders, deployment:renderDeployment, runtime:renderRuntime, flow:renderFlow})[activeView]();
}

function renderLocation() { window.LocationPage?.render(); }

function renderOverview() { window.Personalization?.renderOverview(); }

function renderIntimacy() { window.Personalization?.renderState(); }

function monthDates() {
  const first = new Date(scheduleMonthCursor.getFullYear(), scheduleMonthCursor.getMonth(), 1);
  const start = new Date(first.getFullYear(), first.getMonth(), 1 - ((first.getDay() + 6) % 7));
  return Array.from({length: 42}, (_, index) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + index));
}
function selectScheduleDate(key) {
  activeScheduleDate = key;
  scheduleMonthCursor = dateFromKey(key); scheduleMonthCursor.setDate(1);
}
function shiftScheduleMonth(step) {
  const next = new Date(scheduleMonthCursor.getFullYear(), scheduleMonthCursor.getMonth() + step, 1);
  const lastDay = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
  activeScheduleDate = todayKey(new Date(next.getFullYear(), next.getMonth(), Math.min(dateFromKey(activeScheduleDate).getDate(), lastDay)));
  scheduleMonthCursor = next;
}
function weekDates(key = activeScheduleDate) {
  const date = dateFromKey(key), offset = (date.getDay() + 6) % 7;
  return Array.from({length: 7}, (_, index) => new Date(date.getFullYear(), date.getMonth(), date.getDate() - offset + index));
}
function shiftScheduleWeek(step) {
  const date = dateFromKey(activeScheduleDate);
  date.setDate(date.getDate() + step * 7);
  selectScheduleDate(todayKey(date));
}
function weekLabel() {
  const dates = weekDates(), first = dates[0], last = dates.at(-1);
  return `${first.getFullYear()}.${first.getMonth() + 1}.${first.getDate()} — ${last.getFullYear() !== first.getFullYear() ? `${last.getFullYear()}.` : ''}${last.getMonth() + 1}.${last.getDate()}`;
}
function weekScheduleState(persona, dateKey = activeScheduleDate) {
  const p = state.personas[persona], doc = scheduleDocuments.get(persona);
  const day = weekdayLabelForDate(dateFromKey(dateKey)), editKey = `week:${day}`;
  const configured = Object.hasOwn(doc.data.week, day), draft = doc.drafts.has(editKey);
  const reference = !configured && !draft;
  const runtime = runtimeDays.get(`${persona}:${dateKey}`);
  return {day, editKey, configured, draft, reference, runtime,
    blocks: draft ? doc.drafts.get(editKey) : configured ? p.schedule[day] : scheduleForDate(p, dateKey)};
}
function buildWeekTabs(persona) {
  return weekDates().map(date => {
    const key = todayKey(date), view = weekScheduleState(persona, key);
    const pending = view.reference && (!view.runtime || view.runtime.loading);
    const error = view.reference && view.runtime?.error;
    const label = error ? '读取失败' : pending ? '加载中…' : `${view.blocks.length} 段 · ${view.reference ? '当天安排' : '周模板'}`;
    return `<button class="day-tab ${key === activeScheduleDate ? 'active' : ''}" data-week-date="${key}" aria-label="${attr(`${key} ${view.day}，${label}`)}" ${key === todayKey() ? 'aria-current="date"' : ''}><strong>${view.day}<span>${date.getMonth() + 1}/${date.getDate()}</span></strong><small>${label}</small>${view.draft ? '<em>未保存</em>' : ''}</button>`;
  }).join('');
}
function bindWeekDates(root) {
  root.querySelectorAll('[data-week-date]').forEach(button => button.onclick = () => { selectScheduleDate(button.dataset.weekDate); renderSchedule(); });
}
function updateWeekTabs(persona) {
  if (activeView !== 'schedule' || activePersona !== persona || scheduleViewMode !== 'week') return;
  const tabs = $('#view-schedule .day-tabs');
  if (tabs) { tabs.innerHTML = buildWeekTabs(persona); bindWeekDates(tabs); }
}
function updateScheduleSummary(persona) {
  updateMonthGrid(persona); updateWeekTabs(persona);
}
function buildMonthGrid(persona) {
  return monthDates().map(date => {
    const key = todayKey(date), runtime = runtimeDays.get(`${persona.id}:${key}`), blocks = scheduleForDate(persona, key);
    const outside = date.getMonth() !== scheduleMonthCursor.getMonth(), active = key === activeScheduleDate, today = key === todayKey(), override = hasScheduleOverride(persona, key);
    const loading = !runtime || runtime.loading, error = runtime?.error;
    const draft = scheduleDocuments.get(persona.id)?.drafts.has(`date:${key}`);
    const count = error ? '读取失败' : loading ? '加载中…' : blocks.length ? `${blocks.length} 段安排` : '暂无安排';
    const summary = error ? '点击刷新日程重试' : loading ? '正在读取当天活动' : blocks[0]?.[2] || '这一天还没有活动';
    return `<button class="month-cell ${outside ? 'outside' : ''} ${active ? 'active' : ''} ${today ? 'today' : ''} ${override ? 'has-override' : ''} ${error ? 'load-error' : ''}" data-date="${key}" aria-label="${attr(`${key}，${count}`)}" ${active ? 'aria-current="date"' : ''} ${loading ? 'aria-busy="true"' : ''}><span class="month-cell-top"><strong>${date.getDate()}</strong>${draft ? '<i>未保存</i>' : override ? '<i>已改</i>' : ''}</span><small>${count}</small><em>${esc(summary)}</em></button>`;
  }).join('');
}
function bindMonthDates(root) {
  root.querySelectorAll('[data-date]').forEach(button => button.onclick = () => { selectScheduleDate(button.dataset.date); renderSchedule(); });
}
function updateMonthGrid(persona) {
  if (activeView !== 'schedule' || activePersona !== persona || scheduleViewMode !== 'month') return;
  const grid = $('#view-schedule .month-grid');
  if (grid) { grid.innerHTML = buildMonthGrid(state.personas[persona]); bindMonthDates(grid); }
}
function loadScheduleDays(persona, keys) {
  const missing = keys.filter(key => !runtimeDays.has(`${persona}:${key}`));
  if (!missing.length) return;
  const pending = {loading: true}, changed = new Set();
  missing.forEach(key => runtimeDays.set(`${persona}:${key}`, pending));
  return apiJSON(`/api/schedule?persona_id=${encodeURIComponent(persona)}&start=${keys[0]}&end=${keys.at(-1)}`).then(value => {
    for (const key of missing) {
      if (runtimeDays.get(`${persona}:${key}`) !== pending) continue;
      const day = value.days?.[key];
      runtimeDays.set(`${persona}:${key}`, day && Array.isArray(day.slots) ? {day} : {error: '服务未返回当天日程，请刷新重试'});
      changed.add(key);
    }
  }).catch(error => {
    for (const key of missing) if (runtimeDays.get(`${persona}:${key}`) === pending) {
      runtimeDays.set(`${persona}:${key}`, {error: error.message}); changed.add(key);
    }
  }).finally(() => {
    if (!changed.size || activeView !== 'schedule' || activePersona !== persona) return;
    const doc = scheduleDocuments.get(persona);
    // Background reads update summaries without replacing a template being edited.
    const needsRuntime = doc?.data && (scheduleViewMode === 'month' ? !doc.drafts.has(`date:${activeScheduleDate}`) : weekScheduleState(persona).reference);
    if (changed.has(activeScheduleDate) && needsRuntime && !doc?.busy) renderSchedule();
    else updateScheduleSummary(persona);
  });
}

function renderSchedule() {
  if (activeView !== 'schedule') return;
  const persona = activePersona, p = state.personas[persona], root = $('#view-schedule');
  if (!scheduleDocuments.has(persona)) scheduleDocuments.set(persona, {data:null, drafts:new Map(), draftReferences:new Map(), busy:false});
  const doc = scheduleDocuments.get(persona);
  doc.draftReferences ||= new Map();
  if (!doc.data) {
    root.innerHTML = `<article class="card"><h3>${doc.error ? '日程读取失败' : '正在读取人物日程…'}</h3><p>${esc(doc.error || '从后台恢复当前人物的周模板和日期覆盖。')}</p>${doc.error ? '<button class="button" data-reload-schedule>重新读取</button>' : ''}</article>`;
    root.querySelector('[data-reload-schedule]')?.addEventListener('click', () => { doc.error = null; renderSchedule(); });
    if (!doc.error) loadSchedule(persona);
    return;
  }
  const isMonth = scheduleViewMode === 'month';
  const activeDate = activeScheduleDate;
  const activeDay = weekdayLabelForDate(dateFromKey(activeDate));
  const week = isMonth ? null : weekScheduleState(persona, activeDate);
  const currentEditKey = isMonth ? `date:${activeDate}` : week.editKey;
  const reference = Boolean(week?.reference), runtime = runtimeDays.get(`${persona}:${activeDate}`);
  const usesRuntime = (isMonth && !doc.drafts.has(currentEditKey)) || reference;
  const loading = usesRuntime && (!runtime || runtime.loading);
  const loadError = usesRuntime && runtime?.error;
  const blocks = isMonth ? doc.drafts.get(currentEditKey) || scheduleForDate(p, activeDate) : week.blocks;
  const unavailable = doc.busy || loading || Boolean(loadError);
  const rows = blocks.map((block,index) => `<div class="schedule-row ${reference ? 'schedule-reference-row' : ''}"><input data-field="start" value="${attr(block[0])}" aria-label="开始时间" ${reference ? 'readonly' : ''} /><span class="time-dash">—</span><input data-field="end" value="${attr(block[1])}" aria-label="结束时间" ${reference ? 'readonly' : ''} /><input class="activity-input" data-field="activity" value="${attr(block[2])}" aria-label="活动" ${reference ? 'readonly' : ''} /><input class="tone-input" data-field="tone" value="${attr(block[3])}" aria-label="相处提示" ${reference ? 'readonly' : ''} />${reference ? '<span></span>' : `<button class="row-delete" data-delete="${index}" title="删除安排">×</button>`}</div>`).join('');
  const sourceNote = doc.busy ? '正在保存当前人物的日程…' : loadError ? `读取失败：${runtime.error}，请刷新重试。` : loading ? '正在读取当天日程…' :
    isMonth ? '当前显示运行时日程；保存后聊天和位置共用这天的修改。' : reference ? `尚未设置固定周模板。下方参考 ${activeDate} 的实际日程，当前仍按日期安排。` :
    `保存后每${activeDay}重复使用；单独修改的日期优先。${hasScheduleOverride(p, activeDate) ? `${activeDate} 已有单日修改，本页显示固定周模板。` : ''}`;
  const actions = reference ? `<button class="button button-ghost" data-new-template ${doc.busy ? 'disabled' : ''}>新建空白模板</button><button class="button" data-copy-template ${unavailable || !blocks.length ? 'disabled' : ''}>以当天日程创建周模板</button>` :
    `<button id="addSchedule" class="button button-ghost" ${unavailable ? 'disabled' : ''}>＋ 添加一段</button>${isMonth || week.configured ? `<button id="resetSchedule" class="button button-ghost" ${unavailable ? 'disabled' : ''}>${isMonth ? '恢复当天模板' : '移除周模板'}</button>` : ''}${!isMonth ? `<button class="button button-ghost" data-discard-template ${week.draft ? '' : 'hidden'} ${doc.busy ? 'disabled' : ''}>${week.configured ? '撤销编辑' : '取消创建'}</button>` : ''}<button id="saveSchedule" class="button" ${unavailable ? 'disabled' : ''}>${isMonth ? '保存这天' : '保存周模板'}</button>`;
  const dateNav = isMonth ? `<button class="icon-button" data-month-step="-1" aria-label="上个月">‹</button><strong>${monthLabel(scheduleMonthCursor)}</strong><button class="icon-button" data-month-step="1" aria-label="下个月">›</button><button class="button button-ghost" data-schedule-today>回到今天</button>` :
    `<button class="icon-button" data-week-step="-1" aria-label="上一周">‹</button><strong>${weekLabel()}</strong><button class="icon-button" data-week-step="1" aria-label="下一周">›</button><button class="button button-ghost" data-schedule-today>回到本周</button>`;
  const emptyTitle = loadError ? '日程读取失败' : loading ? '正在读取当天日程' : reference ? '这一天没有实际安排' : !isMonth && !week.draft ? '已设置为空白周模板' : '尚无安排';
  const emptyHint = loadError ? '点击“刷新日程”重试。' : loading ? '活动加载完成后即可查看。' : reference ? '可以新建空白模板，添加每周重复的安排。' : '点击“添加一段”开始填写。';
  root.innerHTML = `<div class="schedule-intro"><div><p class="eyebrow">DAILY RHYTHM / ${esc(p.name)}</p><h3>让日程成为相处说明书</h3><p>时间、活动和相处提示都保存到当前人物，刷新页面后从后台恢复。</p></div></div><article class="card schedule-card"><div class="schedule-view-toolbar"><div class="view-switch" role="tablist" aria-label="日程视图"><button class="view-switch-item ${!isMonth ? 'active' : ''}" data-schedule-view="week">周视图</button><button class="view-switch-item ${isMonth ? 'active' : ''}" data-schedule-view="month">月视图</button></div><div class="month-nav">${dateNav}<button class="button button-ghost" data-refresh-schedule ${doc.busy ? 'disabled' : ''}>刷新日程</button></div></div>${isMonth ? `<div class="month-weekdays">${WEEK_LABELS.map(day => `<span>${day}</span>`).join('')}</div><div class="month-grid">${buildMonthGrid(p)}</div>` : `<div class="day-tabs">${buildWeekTabs(persona)}</div>`}<div class="schedule-toolbar"><div><h3>${isMonth ? `${activeDate} · ${activeDay}` : `${activeDay} · ${reference ? `${activeDate} 实际日程` : '固定周模板'}`}</h3><p class="card-sub">${esc(sourceNote)}</p>${doc.writeError ? `<p class="persona-error" role="alert">保存失败，编辑仍保留：${esc(doc.writeError)}</p>` : ''}<p data-schedule-dirty class="persona-hint">${doc.drafts.has(currentEditKey) ? '有尚未保存的日程编辑' : ''}</p></div><div class="toolbar-actions">${actions}</div></div>${!isMonth ? `<div class="week-template-note"><span>${reference ? '实际日程仅作参考，创建并保存后才会每周重复。' : '固定周模板跨周生效，按日期单独修改的安排优先。'}</span><button class="text-button" data-edit-reference>查看 / 修改 ${activeDate} 当天 →</button></div>` : ''}<div class="schedule-head"><span>开始</span><span></span><span>结束</span><span>在做什么</span><span>相处提示</span><span></span></div><fieldset class="console-editable" ${unavailable ? 'disabled' : ''}><div id="scheduleRows" class="schedule-rows">${rows || `<div class="empty-schedule"><strong>${emptyTitle}</strong><span>${emptyHint}</span></div>`}</div></fieldset></article>`;
  const readRows = () => $$('#scheduleRows .schedule-row').map(row => ['start','end','activity','tone'].map(field => row.querySelector(`[data-field="${field}"]`).value.trim()));
  root.querySelector('.schedule-intro').insertAdjacentHTML('beforeend', personaSelector());
  bindPersonaSwitch(root);
  const remember = () => {
    doc.drafts.set(currentEditKey, readRows());
    root.querySelector('[data-schedule-dirty]').textContent = '有尚未保存的日程编辑';
    const discard = root.querySelector('[data-discard-template]');
    if (discard) discard.hidden = false;
    updateScheduleSummary(persona);
  };
  if (!reference) root.querySelector('#scheduleRows').addEventListener('input', remember);
  const editRows = action => { const next = readRows(); action(next); doc.drafts.set(currentEditKey, next); renderSchedule(); };
  root.querySelectorAll('[data-schedule-view]').forEach(button => button.onclick = () => { scheduleViewMode = button.dataset.scheduleView; renderSchedule(); });
  bindWeekDates(root);
  bindMonthDates(root);
  root.querySelectorAll('[data-month-step]').forEach(button => button.onclick = () => { shiftScheduleMonth(Number(button.dataset.monthStep)); renderSchedule(); });
  root.querySelectorAll('[data-week-step]').forEach(button => button.onclick = () => { shiftScheduleWeek(Number(button.dataset.weekStep)); renderSchedule(); });
  root.querySelector('[data-schedule-today]').onclick = () => { selectScheduleDate(todayKey()); renderSchedule(); };
  root.querySelector('[data-refresh-schedule]').onclick = () => { doc.data = null; doc.error = null; invalidateDays(persona); renderSchedule(); };
  root.querySelector('[data-copy-template]')?.addEventListener('click', () => {
    doc.drafts.set(currentEditKey, clone(blocks)); doc.draftReferences.set(currentEditKey, activeDate); renderSchedule();
  });
  root.querySelector('[data-new-template]')?.addEventListener('click', () => {
    doc.drafts.set(currentEditKey, []); doc.draftReferences.delete(currentEditKey); renderSchedule();
  });
  root.querySelector('[data-discard-template]')?.addEventListener('click', () => {
    doc.drafts.delete(currentEditKey); doc.draftReferences.delete(currentEditKey); doc.writeError = null; renderSchedule();
  });
  root.querySelector('[data-edit-reference]')?.addEventListener('click', () => { scheduleViewMode = 'month'; renderSchedule(); });
  root.querySelector('#addSchedule')?.addEventListener('click', () => editRows(next => next.push(['12:00','13:00','新的安排','请填写相处提示'])));
  root.querySelectorAll('[data-delete]').forEach(button => button.onclick = () => editRows(next => next.splice(Number(button.dataset.delete), 1)));
  const persist = async reset => {
    if (unavailable || reference) return;
    const next = readRows();
    if (!reset) doc.drafts.set(currentEditKey, next);
    const referenceDate = !isMonth && !reset ? doc.draftReferences.get(currentEditKey) : undefined;
    const payload = {persona_id:persona, ...(isMonth ? {date:activeDate} : {day:activeDay}),
      ...(referenceDate ? {reference_date:referenceDate} : {}), blocks:next, reset};
    doc.busy = true; doc.writeError = null; renderSchedule();
    try {
      await apiJSON('/api/schedule', {method:'POST', headers:writeHeaders(persona), body:JSON.stringify(payload)});
      doc.drafts.delete(currentEditKey); doc.draftReferences.delete(currentEditKey); doc.data = null; invalidateDays(persona);
      toast(reset ? '已恢复当前人物的运行时日程' : '日程已保存到当前人物');
    } catch (error) { doc.writeError = error.message; }
    finally { doc.busy = false; if (activeView === 'schedule' && activePersona === persona) renderSchedule(); }
  };
  root.querySelector('#saveSchedule')?.addEventListener('click', () => persist(false));
  root.querySelector('#resetSchedule')?.addEventListener('click', () => persist(true));
  if (!doc.busy) loadScheduleDays(persona, (isMonth ? monthDates() : weekDates()).map(todayKey));
}

function renderPersona() { window.Personalization?.renderEditor(); }

function renderProviders() { window.Personalization?.renderCapabilities(); }

function renderDeployment() { window.Personalization?.renderDeployment(); }
function renderRuntime() { window.Personalization?.renderRuntime(); }
function renderFlow() { window.Personalization?.renderFlow(); }

function download(filename, value) {
  const blob = new Blob([JSON.stringify(value, null, 2)], {type:'application/json'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob); link.download = filename; link.click();
  URL.revokeObjectURL(link.href); toast(`${filename} 已导出`);
}
const nav = $('#nav'), navToggle = $('#navToggle');
function setNavigation(open) {
  navToggle.setAttribute('aria-expanded', String(open));
  nav.classList.toggle('nav-open', open);
}
navToggle.onclick = () => setNavigation(navToggle.getAttribute('aria-expanded') === 'false');
nav.addEventListener('click', event => {
  const button = event.target.closest('.nav-item');
  if (!button) return;
  setNavigation(false);
  activeView = button.dataset.view;
  persistView();
  render();
  if (navToggle.getClientRects().length) $('#pageTitle').focus();
});
$('#exportAll').onclick = () => window.Personalization?.exportCurrent();
$('#resetAll').onclick = () => {
  const doc = scheduleDocuments.get(activePersona);
  if (doc?.busy) return toast('日程正在保存，请稍后刷新');
  if (doc) { doc.data = null; doc.error = null; }
  invalidateDays(activePersona);
  window.Personalization?.reload(activePersona, true);
  render();
};
window.addEventListener('beforeunload', event => {
  if (window.LocationPage?.hasUnsaved() || [...scheduleDocuments.values()].some(doc => doc.drafts.size || doc.busy)) { event.preventDefault(); event.returnValue = ''; }
});
loadBranchLabel();
render();
