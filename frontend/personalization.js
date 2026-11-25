/* Backend persona editor. No persona facts are persisted in localStorage. */
window.Personalization = (() => {
  const documents = new Map();
  let section = 'background';
  const text = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const display = value => value == null || value === '' || (Array.isArray(value) && !value.length) ? '待补充' :
    Array.isArray(value) ? value.map(v => typeof v === 'object' ? Object.values(v).filter(x => typeof x === 'string').join(' · ') : v).join('、') :
    typeof value === 'boolean' ? (value ? '开启' : '关闭') : String(value);
  const shortVersion = value => value ? value.slice(0, 10) : '基线档案';
  const dirty = doc => JSON.stringify(doc.values) !== JSON.stringify(doc.data.draft.values) || doc.candidate !== doc.data.draft.candidate;

  function current() {
    if (!documents.has(activePersona)) {
      documents.set(activePersona, {loading: true});
      load(activePersona);
    }
    return documents.get(activePersona);
  }
  async function load(persona, preserve = false) {
    const old = documents.get(persona);
    if (old?.busy) return;
    if (old) old.busy = true;
    if (old?.data && activePersona === persona) render();
    try {
      const data = await apiJSON(`/api/personalization?persona_id=${encodeURIComponent(persona)}`);
      documents.set(persona, {data, values: preserve && old?.values ? old.values : clone(data.draft.values),
        candidate: preserve && old?.values ? old.candidate : data.draft.candidate, preview: null, errors: {}, busy: false});
      state.personas[persona].name = data.applied.values['identity.name'] || state.personas[persona].name;
    } catch (error) {
      if (old?.data) { old.busy = false; documents.set(persona, old); toast(`读取失败，人物编辑已保留：${error.message}`); }
      else documents.set(persona, {error: error.message});
    }
    if (activePersona === persona) render();
  }
  function loading(root, doc) {
    if (doc.data) return false;
    root.innerHTML = `<article class="card persona-empty"><h3>${doc.error ? '人物资料暂未读取' : '正在读取人物档案…'}</h3><p>${text(doc.error || '从后台恢复已保存的草稿与版本。')}</p>${doc.error ? '<button class="button" data-reload-persona>重新读取</button>' : ''}</article>`;
    root.querySelector('[data-reload-persona]')?.addEventListener('click', () => load(activePersona));
    return true;
  }
  function selector(doc) {
    state.personas[doc.data.persona_id].name = doc.data.applied.values['identity.name'] || state.personas[doc.data.persona_id].name;
    return personaSelector();
  }
  function bindSelector(root) {
    bindPersonaSwitch(root);
    root.querySelectorAll('[data-edit-section]').forEach(button => button.addEventListener('click', () => {
      section = button.dataset.editSection; activeView = 'persona'; render();
    }));
  }
  function status(doc) {
    const runtime = doc.data.runtime;
    return `<span class="tag ${runtime.status === 'loaded' ? 'cyan' : 'yellow'}">${runtime.status === 'loaded' ? '工作流已加载' : runtime.status === 'pending' ? '已选择版本 · 等待工作流加载' : '尚未应用人物版本'}</span>`;
  }
  function renderOverview() {
    const root = $('#view-overview'), doc = current();
    if (loading(root, doc)) return;
    const values = doc.data.applied.values;
    const birthday = values['identity.birthday'];
    let age = /^\d+$/.test(values['identity.age_stated'] || '') ? Number(values['identity.age_stated']) : null;
    if (birthday) { const b = new Date(`${birthday}T00:00:00`), now = new Date(); age = now.getFullYear() - b.getFullYear() - (now.getMonth() < b.getMonth() || (now.getMonth() === b.getMonth() && now.getDate() < b.getDate()) ? 1 : 0); }
    root.innerHTML = `<article class="card persona-cover"><div class="persona-cover-top"><p class="eyebrow">MY PERSONA / 人物档案</p>${selector(doc)}</div><div class="persona-cover-title"><div class="avatar">${text((values['identity.name'] || '待').slice(0, 1))}</div><div><h3>${text(values['identity.name'] || '等待你填写名字')}</h3><p>${text(values['identity.role'] || '身份待补充')}</p></div></div><p class="persona-description">${text(values['personality.traits_summary'] || '从已确认的背景和性格开始，慢慢完善这份人物档案。')}</p><div class="hero-meta">${[age != null ? `${age}岁` : null, values['school.institution'], values['school.major'], values['identity.relationship_to_user']].filter(Boolean).map(v => `<span class="tag">${text(v)}</span>`).join('')}${status(doc)}</div><div class="persona-version">展示版本：${text(shortVersion(doc.data.applied.version))}${dirty(doc) || JSON.stringify(doc.data.draft.values) !== JSON.stringify(values) ? ' · 有尚未应用的草稿' : ''}</div></article><div class="persona-overview-grid">${doc.data.sections.map(group => {
      const specs = doc.data.fields.filter(f => f.section === group.id && f.target === 'dossier');
      const selected = specs.filter(f => values[f.id] != null && display(values[f.id]) !== '待补充');
      const other = doc.data.fields.filter(f => f.section === group.id && f.status === 'ready' && f.target !== 'dossier');
      const complete = doc.data.fields.filter(f => f.section === group.id);
      return `<article class="card persona-summary-card"><div class="card-header"><h3>${text(group.name)}</h3><button class="button button-ghost" data-edit-section="${group.id}">编辑</button></div><p>${text(group.note)}</p>${selected.length ? `<dl>${selected.slice(0, 5).map(f => `<div><dt>${text(f.label)}</dt><dd>${text(display(values[f.id]))}</dd></div>`).join('')}</dl>` : `<p class="persona-muted">${other.length ? other.slice(0, 3).map(f => `${text(f.label)}：${text(display(values[f.id]))}`).join('<br>') : '等待补充资料或接入对应能力'}</p>`}<details class="persona-full-profile"><summary>查看完整${text(group.name)} · ${complete.length}项</summary><dl>${complete.map(f => `<div><dt>${text(f.label)}</dt><dd>${text(f.status === 'pending' ? '待接入' : display(values[f.id]))}</dd></div>`).join('')}</dl></details></article>`;
    }).join('')}</div>`;
    root.insertAdjacentHTML('beforeend', readOnlyState(doc));
    if (doc.data.applied.has_standard_assets) {
      const imageURL = name => `${API_BASE}/api/personalization/applied-image?persona_id=${encodeURIComponent(doc.data.persona_id)}&version=${doc.data.applied.version}&name=${name}`;
      root.insertAdjacentHTML('beforeend', `<article class="card persona-applied-appearance"><h3>当前已应用的标准素材</h3><div class="persona-overview-grid">${[['face','脸部'],['body','全身']].map(([kind,label]) => `<div><figure class="appearance-sheet"><figcaption>${label}五视图总览</figcaption><img src="${imageURL(kind+'/sheet.png')}" alt="已应用${label}五视图"></figure><details><summary>查看五张${label}单视图</summary><div class="appearance-views">${['lprofile','l34','front','r34','rprofile'].map((view,index) => `<img src="${imageURL(kind+'/'+view+'.png')}" alt="已应用${label}${['左侧','左45度','正面','右45度','右侧'][index]}">`).join('')}</div></details></div>`).join('')}</div></article>`);
    }
    bindSelector(root);
  }

  function readOnlyState(doc) {
    const live = doc.data.live_state;
    if (!live?.available) return `<article class="card persona-runtime-card"><h3>当前心情与关系</h3><p>${text(live?.note || '尚未连接运行状态')}</p><p class="persona-hint">心情、亲密度与睡眠来自实际互动，人物设置不会覆盖这些记录。</p></article>`;
    const emotion = live.emotion || {};
    return `<article class="card persona-runtime-card"><h3>当前心情与关系</h3><dl><div><dt>心情</dt><dd>${text(live.mood ?? '暂无评分')}</dd></div><div><dt>精力</dt><dd>${text(live.energy ?? '暂无评分')}</dd></div><div><dt>亲密度</dt><dd>${text(emotion.intimacy ?? '待记录')}</dd></div><div><dt>睡眠</dt><dd>${text(({normal:'清醒',pre_sleep:'睡前',asleep:'已睡'})[live.sleep] || '待记录')}</dd></div></dl><p class="persona-hint">只读 · 更新于 ${text(live.updated_at)}</p></article>`;
  }
  function input(spec, value) {
    const id = `person-${spec.id.replaceAll('.', '-')}`;
    const disabled = spec.status !== 'ready' ? 'disabled' : '';
    const attrs = `id="${id}" data-person-field="${text(spec.id)}" aria-label="${text(spec.label)}" ${disabled}`;
    if (spec.type === 'bool') return `<label class="persona-toggle"><input ${attrs} type="checkbox" ${value ? 'checked' : ''}><span>${value ? '已开启' : '已关闭'}</span></label>`;
    if (['range', 'weights'].includes(spec.type)) {
      const count = spec.type === 'range' ? 2 : 5;
      return `<div class="persona-number-list">${Array.from({length: count}, (_, index) => `<label>${spec.type === 'range' ? (index ? '最长' : '最短') : `${index + 1}张`}<input ${index === 0 ? `id="${id}"` : ''} type="number" data-person-array="${spec.id}" data-index="${index}" min="${spec.min}" max="${spec.max}" step="${spec.type === 'range' ? 1 : .01}" value="${text(value?.[index])}" ${disabled}></label>`).join('')}</div>`;
    }
    if (['clubs', 'roommates'].includes(spec.type)) {
      const keys = spec.type === 'clubs' ? [['name','名称'],['role','角色']] : [['name','姓名'],['major_hint','身份背景'],['traits','性格描述']];
      return `<div class="persona-records">${(value || []).map((row, index) => `<div class="persona-record">${keys.map(([key, label]) => `<label>${label}<input data-person-record="${spec.id}" data-index="${index}" data-key="${key}" value="${text(row[key])}" aria-label="${text(spec.label)} ${index + 1} ${label}" ${disabled}></label>`).join('')}<button class="button button-ghost" data-remove-record="${spec.id}" data-index="${index}" aria-label="删除${text(spec.label)}第${index + 1}项" ${disabled}>删除</button></div>`).join('')}<button class="button button-ghost" data-add-record="${spec.id}" ${disabled}>＋ 添加一项</button></div>`;
    }
    if (['textarea', 'tags'].includes(spec.type)) return `<textarea ${attrs} rows="${spec.type === 'tags' ? 2 : 3}" placeholder="${spec.type === 'tags' ? '每行一项，也可用逗号分隔' : '尚未确认可留空'}">${text(Array.isArray(value) ? value.join('\n') : value)}</textarea>`;
    return `<input ${attrs} type="${spec.type === 'date' ? 'date' : ['number','integer','year'].includes(spec.type) ? 'number' : 'text'}" ${spec.min != null ? `min="${spec.min}"` : ''} ${spec.max != null ? `max="${spec.max}"` : ''} step="${spec.type === 'number' ? 'any' : 1}" value="${text(value)}" placeholder="待补充">`;
  }
  function renderEditor() {
    const root = $('#view-persona'), doc = current();
    if (loading(root, doc)) return;
    const group = doc.data.sections.find(s => s.id === section) || doc.data.sections[0];
    const hasOlderVersion = doc.data.history.some(entry => entry.version && entry.version !== doc.data.applied.version);
    const disabled = doc.busy ? 'disabled' : '';
    root.innerHTML = `<div class="persona-editor-heading"><div><p class="eyebrow">PERSONALIZATION / 人物的每一个细节</p><h3>编辑人物档案</h3><p>背景、表达与外貌，在这里一起完善。</p></div>${selector(doc)}</div><div class="persona-release-line">${status(doc)}<span>应用版本 ${text(shortVersion(doc.data.applied.version))}</span><div class="button-row">${hasOlderVersion ? `<button class="button button-ghost" data-version-history ${disabled}>历史版本</button>` : ''}<button class="button button-ghost" data-refresh-status ${disabled}>刷新状态</button></div></div><div class="persona-editor-layout"><nav class="persona-categories" aria-label="个性化分类">${doc.data.sections.map((g, index) => `<button data-person-section="${g.id}" class="${g.id === group.id ? 'selected' : ''}" aria-current="${g.id === group.id ? 'page' : 'false'}"><span>0${index + 1}</span>${text(g.name)}<i aria-hidden="true">›</i></button>`).join('')}</nav><div class="persona-form-column"><article class="card persona-form-card"><header><p class="eyebrow">${text(group.id)}</p><h3>${text(group.name)}</h3><p>${text(group.note)}</p></header><fieldset class="console-editable" ${doc.busy ? 'disabled' : ''}><div class="persona-fields">${doc.data.fields.filter(f => f.section === group.id).map(spec => `<div class="field persona-field ${spec.type === 'bool' ? 'persona-switch-field' : ''}" data-field-container="${spec.id}"><label for="person-${spec.id.replaceAll('.', '-')}">${text(spec.label)}${spec.status !== 'ready' ? '<span class="tag" aria-hidden="true">待接入</span>' : ''}</label>${input(spec, doc.values[spec.id])}${spec.note ? `<small>${text(spec.note)}</small>` : ''}<span class="field-error" role="alert">${text(doc.errors?.[spec.id])}</span></div>`).join('')}</div>${group.id === 'appearance' ? '<div id="appearanceTasks"></div>' : ''}${group.id === 'image' ? '<p class="persona-hint">标准脸部与身体素材用于身份一致；场景、实际地点与日程约束继续由运行上下文决定。</p>' : ''}${group.id === 'media' ? capabilityCards(doc) : ''}</fieldset></article><div id="personaError" class="persona-error" role="alert"></div></div><aside class="card persona-preview" aria-label="当前编辑预览"><p class="eyebrow">LIVE PREVIEW / 草稿</p><h3>草稿预览</h3><div id="personaLivePreview"></div><p class="persona-hint">这里展示当前编辑；“我的人设”展示已应用档案。</p><button class="button button-ghost" data-person-overview>查看已应用档案</button></aside></div><footer class="persona-action-bar"><div><strong id="personaDraftStatus"></strong><small>草稿修订 ${doc.data.draft.revision}${doc.data.draft.saved_at ? ' · 已保存到后台' : ' · 尚未保存'}</small></div><div class="button-row"><button class="button button-ghost" id="savePersonDraft" ${doc.busy ? 'disabled' : ''}>保存草稿</button><button class="button" id="previewPersonChanges" ${doc.busy ? 'disabled' : ''}>预览变更</button><button class="button" id="applyPerson" ${!doc.preview || dirty(doc) || doc.busy ? 'disabled' : ''}>应用到${text(doc.values['identity.name'] || '人物')}</button></div></footer><dialog id="personaChangesDialog" class="persona-dialog" aria-labelledby="personaDialogTitle"></dialog>`;
    bindSelector(root);
    root.querySelectorAll('[data-person-section]').forEach(button => button.addEventListener('click', () => { section = button.dataset.personSection; renderEditor(); }));
    root.querySelector('[data-person-overview]').onclick = () => { activeView = 'overview'; render(); };
    root.querySelector('[data-refresh-status]').onclick = () => load(activePersona, true);
    root.querySelector('[data-version-history]')?.addEventListener('click', () => showVersionHistory(doc));
    root.querySelectorAll('[data-person-field]').forEach(node => node.addEventListener('input', () => {
      const spec = doc.data.fields.find(f => f.id === node.dataset.personField);
      doc.values[spec.id] = spec.type === 'bool' ? node.checked : spec.type === 'tags' ? node.value.split(/[,，\n]/).map(v => v.trim()).filter(Boolean) : ['number','integer','year'].includes(spec.type) ? node.value === '' ? null : Number(node.value) : node.value.trim() || null;
      if (spec.type === 'bool') node.nextElementSibling.textContent = node.checked ? '已开启' : '已关闭';
      changed(doc);
    }));
    root.querySelectorAll('[data-person-array]').forEach(node => node.addEventListener('input', () => {
      const id = node.dataset.personArray, spec = doc.data.fields.find(f => f.id === id);
      doc.values[id] = Array.from(root.querySelectorAll(`[data-person-array="${id}"]`), n => n.value === '' ? null : Number(n.value));
      changed(doc);
    }));
    root.querySelectorAll('[data-person-record]').forEach(node => node.addEventListener('input', () => {
      doc.values[node.dataset.personRecord][Number(node.dataset.index)][node.dataset.key] = node.value;
      changed(doc);
    }));
    root.querySelectorAll('[data-add-record]').forEach(button => button.onclick = () => {
      const key = button.dataset.addRecord; doc.values[key] = doc.values[key] || []; doc.values[key].push({name: ''}); changed(doc); renderEditor();
    });
    root.querySelectorAll('[data-remove-record]').forEach(button => button.onclick = () => {
      doc.values[button.dataset.removeRecord].splice(Number(button.dataset.index), 1); changed(doc); renderEditor();
    });
    $('#savePersonDraft').onclick = () => run(doc, async () => { await save(doc); toast('人物草稿已保存到后台'); });
    $('#previewPersonChanges').onclick = () => run(doc, async () => {
      if (dirty(doc) || !doc.data.draft.revision) await save(doc);
      doc.preview = await post(doc, 'preview', {revision: doc.data.draft.revision});
      if (doc.preview.draft) { doc.data.draft = clone(doc.preview.draft); doc.values = clone(doc.preview.draft.values); doc.candidate = doc.preview.draft.candidate; }
    }, true);
    $('#applyPerson').onclick = () => showChanges(doc);
    updateLive(doc);
    if (group.id === 'appearance') window.AppearanceTasks?.render($('#appearanceTasks'), doc, changed);
  }
  function changed(doc) {
    doc.preview = null; doc.errors = {};
    updateLive(doc);
  }
  function updateLive(doc) {
    if (documents.get(activePersona) !== doc) return;
    const root = $('#personaLivePreview');
    if (!root) return;
    const values = doc.values;
    const chosen = ['identity.name','identity.relationship_to_user','personality.traits_summary','language.default','image.wardrobe','appearance.description'];
    root.innerHTML = `<h4>${text(values['identity.name'] || '名字待补充')}</h4><dl>${chosen.slice(1).filter(key => values[key]).map(key => `<div><dt>${text(doc.data.fields.find(f => f.id === key)?.label || key)}</dt><dd>${text(display(values[key]))}</dd></div>`).join('')}</dl><div class="persona-preview-tags">${(values['personality.overlays'] || []).map(v => `<span class="tag">${text(v)}</span>`).join('')}</div>`;
    $('#personaDraftStatus').textContent = dirty(doc) ? '有尚未保存的编辑' : doc.preview ? '变更已预览，可以应用' : '草稿与后台一致';
    $('#applyPerson').disabled = !doc.preview || dirty(doc) || doc.busy;
  }
  async function post(doc, action, body) {
    const response = await fetch(`${API_BASE}/api/personalization/${action}`, {method: 'POST', headers: {'Content-Type':'application/json','X-Console-Token':doc.data.csrf_token}, body: JSON.stringify({persona_id:doc.data.persona_id,...body}), signal: AbortSignal.timeout(30000)});
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error || `HTTP ${response.status}`); error.fields = result.fields; throw error; }
    return result;
  }
  async function save(doc) {
    const submitted = clone(doc.values), candidate = doc.candidate;
    const saved = await post(doc, 'draft', {revision:doc.data.draft.revision, values:submitted, candidate});
    // Preserve any edit made while the request was in flight, including task selection.
    const pending = Object.fromEntries(Object.entries(doc.values).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(submitted[key])));
    doc.data.draft = saved;
    doc.values = {...clone(saved.values), ...pending};
    if (doc.candidate === candidate) doc.candidate = saved.candidate;
    doc.preview = null;
    updateLive(doc);
  }
  async function run(doc, action, openPreview = false) {
    if (doc.busy) return;
    doc.busy = true;
    if (documents.get(activePersona) === doc && activeView === 'persona') renderEditor();
    try { await action(); doc.busy = false; if (documents.get(activePersona) === doc) { render(); if (openPreview && activeView === 'persona') showChanges(doc); } }
    catch (error) {
      doc.busy = false; doc.errors = error.fields || {};
      const invalid = doc.data.fields.find(f => doc.errors[f.id]);
      if (documents.get(activePersona) === doc && activeView === 'persona') {
        if (invalid) section = invalid.section;
        renderEditor(); $('#personaError').innerHTML = `${text(error.message)}<button class="button button-ghost" data-refresh-conflict>重新读取并保留本地编辑</button><button class="button button-ghost" data-reset-persona>从已应用资料重新建草稿</button>`;
        $('[data-refresh-conflict]').onclick = () => load(activePersona, true);
        $('[data-reset-persona]').onclick = () => {
          if (!window.confirm('放弃当前草稿和未保存的编辑，改用已应用资料？')) return;
          run(doc, async () => {
            const latest = await apiJSON(`/api/personalization?persona_id=${encodeURIComponent(doc.data.persona_id)}`);
            const reset = await post(doc, 'reset', {revision:latest.draft.revision, expected_version:latest.applied.version});
            doc.data = {...latest, draft:reset}; doc.values = clone(reset.values); doc.candidate = reset.candidate; doc.preview = null;
          });
        };
      }
    }
  }
  function showChanges(doc) {
    if (!doc.preview || dirty(doc)) return;
    const dialog = $('#personaChangesDialog'), p = doc.preview;
    dialog.innerHTML = `<div class="persona-dialog-head"><div><p class="eyebrow">REVIEW CHANGES</p><h3 id="personaDialogTitle">预览变更</h3></div><button class="icon-button" aria-label="关闭预览" data-close>×</button></div><p>草稿修订 ${p.revision} · ${p.changes.length} 项变更</p>${p.warnings.map(v => `<p class="persona-hint">${text(v)}</p>`).join('')}<div class="persona-diff">${p.changes.length ? p.changes.map(c => `<article><strong>${text(c.label)}</strong><div><span>${text(display(c.before))}</span><b>→</b><span>${text(display(c.after))}</span></div></article>`).join('') : '<p>字段与当前档案一致。首次应用将建立可追踪的人物版本。</p>'}</div><details><summary>查看工作流使用的表达策略</summary><pre>${text(p.policy_preview)}</pre></details><p class="persona-hint">${p.gaps.length} 项事实待补充，保持未知。应用完成后显示各工作流的实际加载状态。</p><div class="button-row"><button class="button button-ghost" data-close>继续编辑</button><button class="button" data-confirm-apply>应用到${text(doc.values['identity.name'])}</button></div>`;
    dialog.querySelectorAll('[data-close]').forEach(b => b.onclick = () => dialog.close());
    dialog.querySelector('[data-confirm-apply]').onclick = () => { dialog.close(); run(doc, async () => {
      const data = await post(doc, 'apply', {revision:doc.data.draft.revision, preview_digest:p.preview_digest});
      doc.data = {...data,csrf_token:doc.data.csrf_token}; doc.preview = null;
      toast(data.runtime.status === 'loaded' ? '工作流已加载此人物版本' : '人物版本已选择，等待工作流加载');
    }); };
    dialog.showModal();
  }
  function showVersionHistory(doc) {
    const dialog = $('#personaChangesDialog');
    const expectedVersion = doc.data.applied.version;
    const versions = new Map();
    for (const entry of [...doc.data.history].reverse()) {
      if (entry.version && entry.version !== expectedVersion && !versions.has(entry.version)) {
        versions.set(entry.version, entry.at);
      }
    }
    const options = [...versions].map(([version, at]) => {
      const date = new Date(at).toLocaleString('zh-CN', {dateStyle:'medium', timeStyle:'short'});
      return `<option value="${text(version)}">${text(shortVersion(version))} · ${text(date)}</option>`;
    }).join('');
    dialog.innerHTML = `<div class="persona-dialog-head"><h3 id="personaDialogTitle">回退人设版本</h3>
      <button type="button" class="icon-button" aria-label="关闭历史版本" data-close>×</button></div>
      <p>当前版本 ${text(shortVersion(expectedVersion))}。确认后，工作流会在下一次运行读取所选版本。</p>
      <form><div class="field"><label for="personaHistoryVersion">选择历史版本</label>
        <select id="personaHistoryVersion" name="version" required><option value="" selected disabled>请选择</option>${options}</select>
      </div><div class="button-row"><button type="button" class="button button-ghost" data-close>取消</button>
        <button type="submit" class="button" disabled>确认回退</button></div></form>`;
    const form = dialog.querySelector('form');
    const select = form.elements.version;
    dialog.querySelectorAll('[data-close]').forEach(button => button.onclick = () => dialog.close());
    select.onchange = () => { form.querySelector('button[type="submit"]').disabled = !select.value; };
    form.onsubmit = event => {
      event.preventDefault();
      const version = select.value;
      dialog.close();
      run(doc, async () => {
        const data = await post(doc, 'rollback', {version, expected_version:expectedVersion});
        // Preserve the editing revision so concurrent draft saves stay detectable.
        doc.data = {...data, draft:doc.data.draft, csrf_token:doc.data.csrf_token};
        doc.preview = null;
        toast('已切换到所选人设版本');
      });
    };
    dialog.showModal();
  }
  function capabilityCards(doc) {
    return `<div class="persona-capabilities">${doc.data.capabilities.map(c => `<article><div><strong>${text(c.name)}</strong><span class="tag">${({conditional:'依赖通道与授权',pending:'待接入',disabled:'未开启'})[c.status]}</span></div><p>${text(c.note)}</p></article>`).join('')}</div>`;
  }
  function renderCapabilities() {
    const root = $('#view-providers'), doc = current();
    if (loading(root, doc)) return;
    const names = {chat:'聊天',image:'生图',proactive:'主动互动',reflection:'日记反思',content:'内容采集',voice:'听取语音',caption:'理解图片'};
    const required = doc.data.runtime.required || [];
    const used = new Set(doc.data.runtime.reports.filter(r => r.confirmed && r.version === doc.data.applied.version).map(r => r.consumer));
    root.innerHTML = `<article class="card"><p class="eyebrow">ABILITY / 当前人物</p><div class="card-header"><h3>能力与人物设置一起管理</h3>${selector(doc)}</div><p class="card-sub">每项能力同时受工作流、服务可用性与部署授权约束。</p>${capabilityCards(doc)}<button class="button" data-edit-section="media">前往声音与头像</button></article><article class="card persona-runtime-card"><h3>已选择与实际加载</h3>${status(doc)}<p>应用版本：${text(shortVersion(doc.data.applied.version))}</p><p>${required.map(c => `${text(names[c] || c)}：${used.has(c) ? '已使用此版本' : '等待实际使用'}`).join(' · ') || '尚无可确认的工作流'}</p>${doc.data.runtime.reports.length ? doc.data.runtime.reports.map(r => `<p>${text(names[r.consumer] || r.consumer)} · ${text(shortVersion(r.version))} · ${r.usage === 'run' ? (r.confirmed ? '最近任务已使用' : '历史任务记录') : r.live ? '处理进程在线' : '处理进程已退出'} · ${text(r.at)}</p>`).join('') : '<p>尚未收到运行工作流的版本回报。</p>'}</article>`;
    bindSelector(root);
  }
  function renderState() {
    const root = $('#view-intimacy'), doc = current();
    if (loading(root, doc)) return;
    root.innerHTML = `<div class="persona-editor-heading"><h3>当前人物的关系状态</h3>${selector(doc)}</div>${readOnlyState(doc)}<article class="card"><h3>${text(display(doc.data.applied.values['identity.relationship_to_user']))}</h3><button class="button button-ghost" data-edit-section="background">编辑关系背景</button></article>`;
    bindSelector(root);
  }
  function renderDeployment() {
    const root = $('#view-deployment'), doc = current();
    if (loading(root, doc)) return;
    const data = doc.data, bindings = data.scope.bindings;
    if (data.scope.adapter === 'local_companion') {
      root.innerHTML = `<article class="card"><div class="card-header"><h3>部署绑定</h3>${selector(doc)}</div>
        <section data-companion-qq></section></article>`;
      bindSelector(root);
      window.PersonaOnboarding?.renderQQ(root.querySelector('[data-companion-qq]'), data.persona_id);
      return;
    }
    root.innerHTML = `<article class="card"><div class="card-header"><h3>当前人物的连接状态</h3>${selector(doc)}</div><dl><div><dt>人物</dt><dd>${text(data.persona_id)}</dd></div><div><dt>关系</dt><dd>${text(data.relation_id)}</dd></div><div><dt>环境</dt><dd>${text(data.environment)}</dd></div><div><dt>接入方式</dt><dd>${text(data.scope.adapter)}</dd></div></dl><p class="persona-hint">账号连接与发送授权由部署配置管理。此页只读展示后台记录。</p></article><article class="card"><h3>已记录的消息绑定</h3>${bindings.length ? `<table class="route-table"><thead><tr><th>通道</th><th>联系人类型</th><th>联系人</th></tr></thead><tbody>${bindings.map(b => `<tr><td>${text(b.channel)}</td><td>${text(b.peer_type)}</td><td>${text(b.peer_id)}</td></tr>`).join('')}</tbody></table>` : `<p>${data.scope.adapter === 'legacy' ? '沿用运行主机的既有通道；此控制台未核验账号连接。' : '当前人物在此环境没有已记录的消息绑定。'}</p>`}</article>`;
    bindSelector(root);
  }
  function renderRuntime() {
    const root = $('#view-runtime'), doc = current();
    if (loading(root, doc)) return;
    const {runtime_directory:directory, qq_runtime_directory:qqDirectory} = doc.data.scope;
    const latest = doc.data.runtime.reports.filter(r => r.consumer === 'chat' && r.version)
      .sort((a, b) => b.at.localeCompare(a.at))[0];
    const scope = directory ? `<dl class="companion-details">
      <div><dt>已应用版本</dt><dd><code>${text(doc.data.applied.version)}</code></dd></div>
      <div><dt>最近一次聊天加载</dt><dd>${latest ? `<code>${text(latest.version)}</code>` : '等待首次 QQ 聊天'}</dd></div>
      <div><dt>人物运行目录</dt><dd><code>${text(directory)}</code></dd></div>
      ${qqDirectory ? `<div><dt>QQ 会话目录</dt><dd><code>${text(qqDirectory)}</code></dd></div>` : ''}
    </dl>` : `<p>已选择版本：${text(doc.data.applied.version || '尚未应用')}</p>`;
    root.innerHTML = `<article class="card"><div class="card-header"><h3>当前人物的版本与隔离范围</h3>${selector(doc)}</div><p class="persona-scope-line">人物 ${text(doc.data.persona_id)} · 关系 ${text(doc.data.relation_id)} · 环境 ${text(doc.data.environment)}</p>${scope}${status(doc)}<p class="persona-hint">${directory ? '日程和路线随人物保存。聊天记录、记忆和实时情绪随 Bot QQ 与用户 QQ 的组合保存。' : '人物事实按人物共享，关系故事和互动策略按关系独立。聊天记录、记忆和实时情绪由各工作流维护。'}</p><button class="button button-ghost" data-runtime-refresh>刷新实际加载状态</button></article>`;
    root.querySelector('[data-runtime-refresh]').onclick = () => load(doc.data.persona_id, true);
    bindSelector(root);
  }
  function renderFlow() {
    const root = $('#view-flow'), doc = current();
    if (loading(root, doc)) return;
    const companion = doc.data.scope.adapter === 'local_companion';
    root.innerHTML = `<article class="card"><div class="card-header"><h3>当前人物的工作流</h3>${selector(doc)}</div>
      ${companion ? '<section data-companion-qq></section>' : ''}
      <p class="persona-scope-line">人物 ${text(doc.data.persona_id)} · 关系 ${text(doc.data.relation_id)} · 环境 ${text(doc.data.environment)}</p>
      <p>保存草稿 → 预览变更 → 应用版本 → 工作流实际使用</p>${status(doc)}
      <p class="persona-hint">保存草稿用于继续编辑；应用版本后，各工作流在下一次任务读取该版本。</p>${capabilityCards(doc)}
      <div class="button-row"><button class="button" data-export-persona>导出后台人物配置</button><button class="button button-ghost" data-flow-refresh>刷新工作流状态</button></div></article>`;
    root.querySelector('[data-export-persona]').onclick = exportCurrent;
    root.querySelector('[data-flow-refresh]').onclick = () => load(doc.data.persona_id, true);
    bindSelector(root);
    if (companion) window.PersonaOnboarding?.renderQQ(root.querySelector('[data-companion-qq]'), doc.data.persona_id, true);
  }
  async function exportCurrent() {
    const persona = activePersona;
    try {
      const data = await apiJSON(`/api/personalization?persona_id=${encodeURIComponent(persona)}`);
      const schedule = await apiJSON(`/api/schedule?persona_id=${encodeURIComponent(persona)}`);
      const location = await apiJSON(`/api/location-config?persona_id=${encodeURIComponent(persona)}`);
      download(`${persona}-config.json`, {schema_version:1, persona_id:data.persona_id, relation_id:data.relation_id,
        environment:data.environment, applied:data.applied, saved_draft:data.draft, scope:data.scope,
        runtime:data.runtime, capabilities:data.capabilities, schedule, location});
    } catch (error) { toast(`导出失败：${error.message}`); }
  }
  window.addEventListener('beforeunload', event => { if ([...documents.values()].some(doc => doc.data && dirty(doc))) { event.preventDefault(); event.returnValue = ''; } });
  return {renderOverview, renderEditor, renderCapabilities, renderState, renderDeployment, renderRuntime, renderFlow,
    exportCurrent, reload:load, post, saveDraft:save, run};
})();
render();
