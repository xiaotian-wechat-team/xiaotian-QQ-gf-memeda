window.PersonaOnboarding = (() => {
  const endpoint = '/api/onboarding';
  const setupEndpoint = endpoint + '/setup';
  const role = requestedPersona || '';
  const imageTypes = 'image/jpeg,image/png,image/webp';
  const phases = ['捏脸与授权', '选择人设', '定制需求', '确认创建'];
  const viewNames = ['左侧', '左 45°', '正面', '右 45°', '右侧'];
  const orientations = ['lprofile', 'l34', 'front', 'r34', 'rprofile'];
  const stageNames = {checking:'检查参考图', face:'脸部五视图', body_front:'正面全身基准', body:'全身五视图'};
  let catalog, catalogError, draft, wizardError, pending = false, selectedPreset = null, filter = null, poll;

  const url = (view, id = null) => `/?view=${view}${id ? '&persona_id=' + encodeURIComponent(id) : ''}`;
  const token = () => catalog?.csrf_token;
  const report = (root, message) => { const target = root.querySelector('[data-companion-error]'); if (target) target.textContent = message || ''; };
  const button = (label, action, kind = '') => `<button type="button" class="button ${kind ? 'button-ghost' : ''}" data-action="${action}">${label}</button>`;
  const format = value => typeof value === 'boolean' ? (value ? '开启' : '关闭') :
    Array.isArray(value) ? value.map(row => typeof row === 'object' ? Object.values(row).join(' · ') : row).join('、') : String(value ?? '待补充');

  async function write(action, data = {}, timeout = 15000) {
    return apiJSON(`${endpoint}/${action}`, {method:'POST', timeout, headers:{
      'Content-Type':'application/json', 'X-Console-Token':token() || ''}, body:JSON.stringify(data)});
  }

  async function initialize() {
    if (activeView === 'home') { home(); return; }
    try {
      catalog = await apiJSON(activeView === 'onboarding' ? setupEndpoint : endpoint);
      catalogError = null;
      for (const person of (catalog.people || []).filter(row => row.created)) {
        state.personas[person.id] ||= {id:person.id, name:person.name, schedule:{}, scheduleDates:{}};
      }
      if (Object.hasOwn(state.personas, role)) activePersona = role;
    } catch (error) { catalogError = error.message; }
    finally { companionCatalogLoading = false; render(); }
    if (activeView === 'onboarding' && !draft && !catalogError) refresh();
  }

  function galleryRun(duplicate) {
    return `<div class="gallery-run"${duplicate ? ' aria-hidden="true"' : ''}>
      ${HomePortraits.hangings.map(prints => `<div class="gallery-hanging">
        ${prints.map(([id, name, subject, sourceFacing]) => `<div class="gallery-print" data-portrait="${id}" data-subject="${subject}">
            <img src="${HomePortraits.imageUrl(id)}"${sourceFacing === 'left' ? ' class="gallery-mirrored"' : ''}
              ${HomePortraits.credits[id] ? `title="${attr(HomePortraits.credits[id])}"` : ''}
              alt="${duplicate ? '' : attr(name)}" draggable="false" decoding="async">
          </div>`).join('')}
      </div>`).join('')}
    </div>`;
  }

  function defaultGallery(wall) {
    wall.innerHTML = `
        <div class="gallery-track">${galleryRun(false)}${galleryRun(true)}</div>
        <button class="gallery-motion" type="button" aria-label="暂停画廊滚动" aria-pressed="false" title="暂停画廊滚动"></button>`;
    const motion = wall.querySelector('.gallery-motion');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const setGalleryFocus = () => { wall.tabIndex = reducedMotion.matches ? 0 : -1; };
    setGalleryFocus();
    reducedMotion.addEventListener('change', setGalleryFocus);
    motion.onclick = () => {
      const pause = motion.getAttribute('aria-pressed') === 'false';
      motion.setAttribute('aria-pressed', String(pause));
      motion.title = pause ? '继续画廊滚动' : '暂停画廊滚动';
    };
  }

  function home() {
    const root = $('#view-home');
    if (root.firstElementChild) return;
    const demo = initialRoute.get('demo');
    const exhibition = window.PortraitExhibitions;
    const preview = exhibition && Object.hasOwn(exhibition.titles, demo);
    document.title = preview ? exhibition.titles[demo] + ' · 伙伴画廊' : '伙伴画廊';
    const enterLabel = '开始挑选你的伙伴';
    root.innerHTML = `<div class="${preview ? 'exhibit-stage' : 'gallery-wall'}" role="region" aria-label="人物画廊"></div>
      <button class="gallery-enter" type="button" data-action="begin">
        <span aria-live="polite">${enterLabel}</span><span class="gallery-arrow" aria-hidden="true"></span>
      </button>`;
    if (preview) {
      root.dataset.exhibition = demo;
      exhibition.mount(root.firstElementChild, demo, HomePortraits.hangings.flat());
    } else {
      defaultGallery(root.firstElementChild);
    }

    const begin = root.querySelector('[data-action="begin"]');
    const label = begin.firstElementChild;
    begin.onclick = async () => {
      begin.disabled = true;
      label.textContent = '正在进入…';
      begin.removeAttribute('title');
      try {
        const {beginRequestId = crypto.randomUUID(), ...entryState} = history.state || {};
        history.replaceState({...entryState, beginRequestId}, '');
        catalog = await apiJSON(setupEndpoint);
        const person = await write('begin', {request_id:beginRequestId});
        history.replaceState(entryState, '');
        window.location.assign(url('onboarding', person.id));
      } catch (error) {
        label.textContent = '进入失败，点击重试';
        begin.title = error.message;
        begin.disabled = false;
      }
    };

    window.addEventListener('pageshow', event => {
      if (event.persisted) { begin.disabled = false; label.textContent = enterLabel; begin.removeAttribute('title'); }
    });
  }

  async function refresh() {
    if (!role) { wizardError = '请从首页点击「开始挑选你的伙伴」进入创建流程。'; wizard(); return; }
    try {
      draft = await apiJSON(`${endpoint}/state?persona_id=${encodeURIComponent(role)}`, {timeout:14000});
      wizardError = null;
    } catch (error) { wizardError = error.message; }
    if (activeView === 'onboarding') wizard();
  }

  async function move(action, payload = {}, timeout) {
    if (pending || !draft) return;
    const root = $('#view-onboarding');
    pending = true;
    clearTimeout(poll);
    report(root, '正在处理…');
    root.querySelectorAll('button').forEach(node => { if (!node.disabled) { node.disabled = true; node.dataset.processing = ''; } });
    try {
      const next = await write(action, {persona_id:draft.id, revision:draft.revision, ...payload}, timeout);
      if (action === 'login') draft.subscription = next;
      else draft = next;
      wizardError = null;
      if (draft.phase === 'created') window.location.replace(url('overview', draft.id));
      else { pending = false; wizard(); }
    } catch (error) {
      pending = false;
      report(root, error.message);
      root.querySelectorAll('[data-processing]').forEach(node => { node.disabled = false; delete node.dataset.processing; });
      if (!root.querySelector('[data-wizard-refresh]')) {
        root.querySelector('[data-companion-error]').insertAdjacentHTML('afterend', '<button type="button" class="button button-ghost" data-wizard-refresh>刷新当前进度</button>');
        root.querySelector('[data-wizard-refresh]').onclick = refresh;
      }
    }
  }

  function fileData(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve({name:file.name, data:String(reader.result).split(',', 2)[1]});
      reader.onerror = () => reject(new Error('读取原图失败，请重新选择'));
      reader.readAsDataURL(file);
    });
  }

  function subscription(info, pendingHint = '在新标签页完成授权后，本页会自动更新。') {
    if (info?.status === 'connected') return `<div class="companion-auth">
      <strong>ChatGPT 已连接 ${info.plan ? '· ' + attr(info.plan) : ''}</strong>${button('重新登录', 'login', 'ghost')}</div>`;
    if (info?.status === 'pending' && /^https:\/\//.test(info.auth_url || '')) return `<section class="companion-auth">
      <span>${pendingHint}</span>
      <div class="button-row"><a class="button" rel="noopener noreferrer" target="_blank" href="${attr(info.auth_url)}">打开 ChatGPT 授权页 ↗</a>
      ${button('取消授权', 'cancel_login', 'ghost')}</div></section>`;
    return `<section class="companion-auth"><span>${attr(info?.message || '使用自己的 ChatGPT 订阅生成外貌与人设。')}</span>
      ${button('使用 ChatGPT 登录', 'login')}</section>`;
  }

  function uploadForm(values, canReuse) {
    return `<form id="companion-photos" class="companion-form"><div class="companion-pair">
      <label>外貌性别<select name="gender" required>
        <option value="">请选择</option><option value="m" ${values['identity.gender'] === 'm' ? 'selected' : ''}>男</option>
        <option value="f" ${values['identity.gender'] === 'f' ? 'selected' : ''}>女</option></select></label>
      <label>身高（cm，选填）<input type="number" name="height" min="50" max="250" step="any" value="${attr(values['body.height_cm'])}"></label></div>
      <div class="companion-pair"><label class="companion-file">清晰脸部参考
        <input type="file" name="face" accept="${imageTypes}" ${canReuse ? '' : 'required'}></label>
      <label class="companion-file">完整全身参考
        <input type="file" name="body" accept="${imageTypes}" ${canReuse ? '' : 'required'}></label></div>
      <label>补充样图（选填）<input type="file" name="extras" accept="${imageTypes}" multiple></label>
      <p>支持 JPEG、PNG、WebP，最多 16 张。单张最多 12 MB，合计最多 64 MB。${canReuse ? '照片栏留空时沿用已上传的原图。' : ''}</p>
      <label class="companion-check"><input type="checkbox" name="adult" required> 我确认样图是同一位成年人物</label>
      <button class="button" type="submit">${canReuse ? '按这些参数重新生成' : '上传并生成脸部五视图'}</button></form>`;
  }

  function image(name) {
    return `${endpoint}/image?persona_id=${encodeURIComponent(draft.id)}&name=${encodeURIComponent(name)}`;
  }

  function sheet(kind, title) {
    return `<figure class="companion-sheet"><figcaption>${title}</figcaption>
      <a href="${image(kind + '/sheet.png')}" target="_blank" rel="noopener"><img src="${image(kind + '/sheet.png')}" alt="${title}总览"></a>
      <details><summary>检查五张单视图</summary><div class="companion-angles">${orientations.map((key, index) => {
        const src = image(kind + '/' + key + '.png');
        return `<a href="${src}" target="_blank" rel="noopener"><img src="${src}" alt="${viewNames[index]}"><small>${viewNames[index]}</small></a>`;
      }).join('')}</div></details></figure>`;
  }

  function appearance() {
    const task = draft.appearance;
    const auth = subscription(draft.subscription);
    if (draft.subscription?.status !== 'connected') return `<h3>连接你的 ChatGPT 订阅</h3>${auth}`;
    const status = task?.status;
    let content;
    if (['queued', 'running', 'cancelling'].includes(status)) {
      content = `<div class="companion-progress" role="status"><span class="pulse"></span>正在处理 · ${stageNames[task.stage] || '等待生成'}</div>
        ${status === 'queued' ? button('继续生成', 'continue_appearance', 'ghost') : ''}`;
    } else if (status === 'stage_review') {
      const front = task.stage === 'body_front';
      content = `<h4>确认${stageNames[task.stage]}</h4>${front ?
        `<img class="companion-front" src="${image('body_front.png')}" alt="正面全身基准">` : sheet('face', '脸部五视图')}
        ${button(front ? '确认并生成全身五视图' : '确认并建立正面全身基准', 'continue_appearance')}`;
    } else if (draft.appearance_matches && ['review', 'approved'].includes(status)) {
      content = `<h4>确认这个伙伴的外貌</h4>${sheet('face', '脸部五视图')}${sheet('body', '全身五视图')}
        ${button('确认外貌并继续', 'approve_appearance')}`;
    } else {
      content = `${task?.error ? `<p class="persona-error" role="alert">${attr(task.error)}</p>` : ''}
        ${['failed', 'interrupted', 'cancelled'].includes(status) ? button('从上个阶段重试', 'continue_appearance', 'ghost') : ''}
        ${draft.resume ? '<p>外貌参数需要重新确认。你可以沿用原图并按当前参数生成。</p>' : ''}
        ${uploadForm(draft.values, !!task)}`;
    }
    if (['stage_review','review','approved'].includes(status) && draft.appearance_matches) {
      content += `<details class="companion-replace"><summary>替换图片或调整外貌参数</summary>${uploadForm(draft.values, true)}</details>`;
    }
    return `<h3>确认长相</h3>${auth}${content}`;
  }

  function presets() {
    if (filter === null) filter = draft.values['identity.gender'] || 'm';
    const rows = catalog?.presets.filter(row => row.fields['identity.gender'] === filter) || [];
    return `<h3>选一个你喜欢的相处方式</h3>
      <div class="companion-filters" aria-label="预设性别">${[['m', '男生'], ['f', '女生']].map(([id,label]) =>
        `<button class="button button-ghost" data-filter="${id}" aria-pressed="${filter === id}">${label}</button>`).join('')}
        ${button('返回捏脸', 'revise_appearance', 'ghost')}</div>
      <div class="companion-presets" role="group" aria-label="人设预设">${rows.map(row => {
        const v = row.fields;
        return `<div class="companion-preset-choice"><button type="button" class="companion-preset" data-preset="${row.id}" aria-pressed="${selectedPreset === row.id}">
          <span class="companion-preset-title"><strong>${attr(v['identity.name'])}</strong><span>${attr(row.title)}</span></span>
          <span class="companion-facts">${attr(v['identity.age_stated'])} 岁 · ${attr(v['body.height_cm'])} cm · ${attr(v['identity.role'])}</span>
          <span>${attr(v['school.institution'])} · ${attr(v['school.major'])}</span>
          <span>${attr(v['identity.relationship_to_user'])}</span>
          <span>${attr(v['personality.traits_summary'])}</span>
          <span class="companion-quote">「${attr(row.example)}」</span></button></div>`;
      }).join('')}</div>
      <div class="companion-preset-actions" hidden>
        <label class="companion-check"><input type="checkbox" data-use-height> 采用预设身高，并重新确认变更后的外貌参数</label>
        ${button('选择并继续', 'choose')}
      </div>`;
  }

  function requestForm(edit = false) {
    return `<form class="companion-form" id="companion-request">
      <label>你想和伙伴怎么相处？
        <textarea name="request" required rows="5" maxlength="4000" placeholder="例如：我喜欢她直接一点。工作日白天少打扰，周末可以约我看展。">${attr(draft.request)}</textarea></label>
      <div class="button-row">${button('重新选择预设', 'back', 'ghost')}<button class="button" type="submit">${edit ? '按修改后的需求继续' : '提交我的想法'}</button></div></form>`;
  }

  function answers() {
    const rows = draft.questions.filter(row => row.answer !== null);
    if (!rows.length) return '';
    return `<ol class="companion-answers">${rows.map(row => `<li><strong>${attr(row.question)}</strong><p>${attr(row.answer)}</p></li>`).join('')}</ol>`;
  }

  function askUser() {
    if (draft.phase === 'thinking') return `<h3>整理你确认的需求</h3>${answers()}
      <p class="companion-progress" role="status"><span class="pulse"></span>正在检查还有哪些细节需要确认</p>
      ${button('返回需求', 'cancel_question', 'ghost')}`;
    if (draft.phase === 'retry') return `<h3>继续建档</h3>${answers()}<p class="persona-error">${attr(draft.error)}</p>
      <div class="button-row">${button('返回捏脸与授权', 'revise_appearance', 'ghost')}${button('返回需求', 'cancel_question', 'ghost')}
      ${button('重试建档', 'retry')}</div>`;
    const question = draft.questions.at(-1);
    const options = question.options.map((value,index) => `<button type="button" class="button button-ghost" data-answer="${index}">${attr(value)}</button>`).join('');
    return `<h3>确认一个细节</h3>${answers()}
      <p class="companion-question">${attr(question.question)}</p>
      <div class="button-row companion-options">${options}</div>
      <form class="companion-form" id="companion-answer"><label>自己回答<textarea name="answer" rows="3" required maxlength="3000"></textarea></label>
      <div class="button-row">${button('返回需求', 'cancel_question', 'ghost')}<button class="button" type="submit">提交回答</button></div></form>`;
  }

  function preview() {
    const values = draft.values;
    const specs = draft.fields;
    const main = ['identity.role','identity.age_stated','school.institution','school.major','body.height_cm','identity.relationship_to_user',
      'personality.traits_summary','speech.style','chat.feedback','proactive.style'];
    const details = (fields) => `<dl class="companion-details">${fields.map(f => `<div><dt>${attr(f.label)}</dt><dd>${attr(format(values[f.id]))}</dd></div>`).join('')}</dl>`;
    const changes = specs.filter(field => field.changed);
    return `<h3>确认 ${attr(values['identity.name'])} 的人设</h3>${details(specs.filter(field => main.includes(field.id)))}
      ${changes.length ? `<details class="companion-replace"><summary>检查从预设修改的字段</summary>${details(changes)}</details>` : ''}
      <details class="companion-replace"><summary>检查全部已填字段</summary>${details(specs)}</details>
      ${draft.unfilled.length ? `<details class="companion-replace"><summary>查看留空的可选字段</summary><p>${attr(draft.unfilled.join('、'))}</p></details>` : ''}
      <details class="companion-replace"><summary>检查周日程</summary><dl class="companion-details">${Object.entries(draft.week).map(([day, blocks]) =>
        `<div><dt>${attr(day)}</dt><dd>${blocks.map(b => `${attr(b.start)}–${attr(b.end)} ${attr(b.activity)}`).join('<br>')}</dd></div>`).join('')}</dl></details>
      ${answers()}<details class="companion-replace"><summary>修改定制需求</summary>${requestForm(true)}</details>
      <div class="button-row">${button('确认并创建工作台', 'confirm')}</div>`;
  }

  function wizard() {
    clearTimeout(poll);
    const root = $('#view-onboarding');
    if (!draft) {
      root.innerHTML = `<article class="card companion-panel"><p role="status">${attr(wizardError || catalogError || '正在恢复你的创建进度…')}</p>
       <a href="/" class="button button-ghost">返回首页</a>${role && (wizardError || catalogError) ? button('重试', 'refresh', 'ghost') : ''}</article>`;
      root.querySelector('[data-action="refresh"]')?.addEventListener('click', catalogError ? initialize : refresh);
      return;
    }
    if (draft.phase === 'created') { window.location.replace(url('overview', draft.id)); return; }
    const step = {appearance:0,preset:1,request:2,asking:2,thinking:2,retry:2,preview:3}[draft.phase];
    const body = {
      appearance, preset:presets,
      request:() => `<h3>告诉伙伴你的想法</h3><p>在确认创建前，Agent 会逐个问清影响人设的细节。</p>${requestForm()}`,
      asking:askUser, thinking:askUser, retry:askUser, preview,
    }[draft.phase]();
    root.innerHTML = `<ol class="companion-steps">${phases.map((phase,index) =>
       `<li ${index === step ? 'aria-current="step"' : ''} class="${index < step ? 'done' : ''}">${phase}</li>`).join('')}</ol>
      <article class="card companion-panel">${body}<div class="persona-error" data-companion-error role="alert">${attr(wizardError)}</div></article>`;

    root.querySelectorAll('[data-filter]').forEach(node => node.onclick = () => { filter = node.dataset.filter; selectedPreset = null; wizard(); });
    const cards = [...root.querySelectorAll('[data-preset]')];
    const controls = root.querySelector('.companion-preset-actions');
    const select = node => {
      selectedPreset = node.dataset.preset;
      cards.forEach(card => card.setAttribute('aria-pressed', String(card === node)));
      node.after(controls);
      controls.hidden = false;
    };
    const selected = cards.find(card => card.dataset.preset === selectedPreset);
    if (selected) select(selected);
    cards.forEach(node => node.onclick = () => {
      select(node);
      if (matchMedia('(max-width: 640px)').matches) controls.scrollIntoView({block:'nearest'});
    });
    root.querySelectorAll('[data-action]').forEach(node => node.onclick = () => {
      const action = node.dataset.action;
      let data = {};
      if (action === 'choose') data = {preset:selectedPreset, use_height:root.querySelector('[data-use-height]').checked};
      if (action === 'continue_appearance') data = {stage:draft.appearance?.stage};
      if (action === 'confirm') data = {preview_digest:draft.preview_digest};
      move(action, data, action === 'confirm' ? 30000 : undefined);
    });
    const uploaded = root.querySelector('#companion-photos');
    if (uploaded) uploaded.onsubmit = async event => {
      event.preventDefault();
      if (pending) return;
      const form = uploaded.elements, face = form.face.files[0], body = form.body.files[0], extras = [...form.extras.files];
      if ((face || body || extras.length || !draft.task) && !(face && body)) { report(root, '请同时提供清晰脸部和完整全身参考。'); return; }
      const files = face ? [face, body, ...extras] : null;
      if (files && (files.length > 16 || files.some(file => file.size > 12 * 1024 ** 2) ||
          files.reduce((sum, file) => sum + file.size, 0) > 64 * 1024 ** 2)) { report(root, '样图数量或大小超过上传限制。'); return; }
      try {
        const photos = files && await Promise.all(files.map(fileData));
        move('generate', {photos, adult:form.adult.checked, gender:form.gender.value, height:form.height.value ? Number(form.height.value) : null}, 90000);
      } catch (error) { report(root, error.message); }
    };
    const request = root.querySelector('#companion-request');
    if (request) request.onsubmit = event => { event.preventDefault(); move('request', {request:request.elements.request.value}); };
    const question = draft.questions.at(-1);
    const answer = text => move('answer', {answer:text, question:question.id});
    root.querySelectorAll('[data-answer]').forEach(option => option.onclick = () => answer(question.options[Number(option.dataset.answer)]));
    const reply = root.querySelector('#companion-answer');
    if (reply) reply.onsubmit = event => { event.preventDefault(); answer(reply.elements.answer.value); };
    if (!pending && (draft.phase === 'thinking' || (draft.phase === 'appearance' &&
        (draft.subscription?.status === 'pending' || ['queued','running','cancelling'].includes(draft.appearance?.status))))) {
      poll = setTimeout(refresh, 2300);
    }
  }

  const qqLabels = {unbound:'待连接', verified:'已核实 Bot QQ', starting:'正在启动', connecting:'正在连接',
    verifying:'正在核对 Bot QQ', connected:'私聊已启用', reconnecting:'正在重连', account_changed:'Bot QQ 已变化，请重新探测'};
  const receiptLabels = {confirmed:'OneBot 已确认', failed:'发送失败', unknown:'发送结果待确认', reply_failed:'回复处理失败，请检查订阅或连接'};
  const subscriptionLabels = {connected:'已连接', pending:'等待授权', signed_out:'请在 07 连接 ChatGPT', unavailable:'暂不可用'};
  function renderQQ(root, persona, diagnostics = false) {
    if (!root) return;
    const path = `${endpoint}/${diagnostics ? 'diagnostics' : 'qq'}?persona_id=${encodeURIComponent(persona)}`;
    root.innerHTML = `<h3>${diagnostics ? '连接与版本检查' : 'QQ 私聊绑定'}</h3>
      <div data-qq-status><p role="status">${diagnostics ? '正在核对订阅、素材和 QQ…' : '正在读取 QQ 状态…'}</p></div>
      ${diagnostics ? '' : `<div data-qq-binding hidden>
        <form class="companion-form" data-qq-form>
          <div class="companion-pair"><label>你的 QQ 号<input name="peer" required inputmode="numeric" pattern="[1-9][0-9]{4,11}"></label>
          <label>伙伴怎么称呼你（选填）<input name="name" maxlength="40"></label></div>
          <label>OneBot 正向 WebSocket 地址<input name="endpoint" required placeholder="ws://127.0.0.1:3001"></label>
          <label>OneBot Access Token<input name="token" type="password" required autocomplete="off" maxlength="512"></label>
          <button class="button button-ghost" type="submit">探测连接</button>
        </form>
        <p data-qq-edit-note role="status" hidden>绑定信息已变化，请重新探测连接。</p>
        <div class="button-row companion-qq-actions">${button('确认 Bot QQ 并启用私聊', 'enable')}
          ${button('停用私聊', 'disable', 'ghost')}${button('连接 ChatGPT 订阅', 'login', 'ghost')}</div>
        <div data-qq-auth></div>
      </div>`}
      ${button('刷新 QQ 状态', 'refresh', 'ghost')}<div class="persona-error" data-qq-read-error role="alert"></div>
      <div class="persona-error" data-companion-error role="alert"></div>`;
    const panel = root.querySelector('[data-qq-status]');
    const form = root.querySelector('[data-qq-form]');
    const authPanel = root.querySelector('[data-qq-auth]');
    const readError = root.querySelector('[data-qq-read-error]');
    const refreshButton = root.querySelector('[data-action="refresh"]');
    const enable = root.querySelector('[data-action="enable"]');
    const disable = root.querySelector('[data-action="disable"]');
    const fields = ['peer', 'name', 'endpoint'];
    let data, verifiedGeneration, busy = false, reading = false, timer;

    function fill(values) {
      if (!form) return;
      fields.forEach(key => { form.elements[key].value = values[key] || ''; });
      form.elements.token.value = '';
      verifiedGeneration = values.generation;
      root.querySelector('[data-qq-binding]').hidden = false;
    }

    function verified() {
      return data?.generation && verifiedGeneration === data.generation && !form.elements.token.value &&
        fields.every(key => form.elements[key].value === (data[key] || ''));
    }

    function controls() {
      refreshButton.disabled = busy;
      if (!form || !data) return;
      const restoring = data.enabled && data.state === 'starting';
      enable.textContent = restoring ? '恢复私聊连接' : '确认 Bot QQ 并启用私聊';
      enable.hidden = !data.bot || (data.enabled && !restoring);
      enable.disabled = busy || !verified();
      disable.hidden = !data.enabled;
      disable.disabled = busy;
      form.querySelector('button').disabled = busy;
      form.querySelectorAll('input').forEach(input => { input.readOnly = busy; });
      root.querySelector('[data-action="login"]').disabled = busy;
      root.querySelector('[data-qq-edit-note]').hidden = !data.bot || Boolean(verified());
    }

    function update(next) {
      data = next;
      const latest = data.receipt;
      const checks = diagnostics ? `<dl class="companion-details">
        <div><dt>ChatGPT 授权</dt><dd>${attr(data.checks.subscription.message || subscriptionLabels[data.checks.subscription.status] || '待确认')}</dd></div>
        <div><dt>脸部五视图</dt><dd>${data.checks.appearance.face ? '总览和五张单图已校验' : '待应用标准素材'}</dd></div>
        <div><dt>全身五视图</dt><dd>${data.checks.appearance.body ? '总览和五张单图已校验' : '待应用标准素材'}</dd></div>
        <div><dt>已应用版本</dt><dd><code>${attr(data.selected_version)}</code></dd></div></dl>` : '';
      const content = `<p class="companion-qq-status" role="status"><strong>${diagnostics ? 'QQ 私聊 · ' : ''}${qqLabels[data.state] || '待确认'}</strong>
          ${data.bot ? `<span>Bot QQ ${attr(data.bot)} · 你的 QQ ${attr(data.peer)}</span>` : ''}</p>
        ${latest ? `<p>最近回复 · ${attr(receiptLabels[latest.state] || latest.state)} · 版本 <code title="${attr(latest.version)}">${attr(latest.version?.slice(0,12) || '待确认')}</code>${latest.message_id ? ` · 消息 ${attr(latest.message_id)}` : ''}</p>` : ''}
        ${data.state === 'connected' ? '<p>现在用你的 QQ 号给上方 Bot 发一条私聊消息。</p>' : ''}${checks}`;
      panel.innerHTML = content;
      controls();
    }

    function schedule() {
      clearTimeout(timer);
      if (root.isConnected && !busy && ['starting','connecting','verifying','reconnecting'].includes(data?.state)) {
        timer = setTimeout(refresh, 2500);
      }
    }

    async function refresh() {
      if (!root.isConnected || busy || reading) return;
      reading = true;
      const previous = data;
      try {
        const next = await apiJSON(path);
        if (!root.isConnected || busy || previous !== data) return;
        if (!data) fill(next);
        readError.textContent = '';
        update(next);
      } catch (error) {
        if (!root.isConnected || busy || previous !== data) return;
        if (!data) panel.replaceChildren();
        readError.textContent = error.message;
      } finally {
        reading = false;
        schedule();
      }
    }

    async function submit(action, values = {}) {
      if (busy || !data) return;
      if (action === 'enable' && !verified()) { controls(); return; }
      const generation = data.generation;
      busy = true;
      clearTimeout(timer);
      controls();
      report(root, action === 'enable' ? '正在检查 ChatGPT 文本推理与 Bot QQ…' : '正在检查…');
      try {
        const login = action === 'login' || action === 'cancel_login';
        const next = await write(login ? action : 'qq/' + action, {persona_id:persona, generation, ...values},
                                 action === 'enable' ? 90000 : 20000);
        if (!root.isConnected) return;
        if (action === 'login') {
          authPanel.innerHTML = subscription(next, '在新标签页完成授权，然后回到这里启用私聊。');
          authPanel.querySelector('[data-action="cancel_login"]')?.addEventListener('click', () => submit('cancel_login'));
        } else if (action === 'cancel_login') {
          authPanel.replaceChildren();
        } else {
          if (action === 'verify') fill(next);
          else if (verifiedGeneration === generation) verifiedGeneration = next.generation;
          readError.textContent = '';
          update(next);
        }
        report(root, '');
      } catch (error) { report(root, error.message); }
      finally { busy = false; controls(); schedule(); }
    }

    refreshButton.onclick = refresh;
    if (form) {
      form.oninput = () => { verifiedGeneration = null; controls(); };
      form.onsubmit = event => { event.preventDefault(); submit('verify', Object.fromEntries(new FormData(form))); };
      enable.onclick = () => submit('enable', {bot:data.bot});
      disable.onclick = () => submit('disable');
      root.querySelector('[data-action="login"]').onclick = () => submit('login');
    }
    refresh();
  }

  return {initialize, home, wizard, token, renderQQ};
})();
window.PersonaOnboarding.initialize();
