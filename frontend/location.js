/* Persona locations are a view of the schedule; editing routes never edits activities. */
(() => {
  const documents = new Map();
  const writes = new Map();
  const clockFormatter = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
  let selectedDate = localClock().date;
  let renderId = 0, cleanup = () => {};
  function localClock() {
    const now = new Date(), parts = Object.fromEntries(clockFormatter.formatToParts(now).map(part => [part.type, part.value]));
    return {date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}`,
      minute: Number(parts.hour) * 60 + Number(parts.minute) + Number(parts.second) / 60 + now.getMilliseconds() / 60000};
  }
  function duration(minutes) {
    const value = Math.max(0, Math.floor(minutes));
    const hours = Math.floor(value / 60), rest = value % 60;
    return hours ? `${hours}小时${rest ? `${rest}分` : ''}` : `${rest}分钟`;
  }
  function icon(name) {
    const paths = {
      pin: '<path d="M20 10c0 6-8 11-8 11S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
      arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
      refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 3M18 18A8 8 0 0 1 5 15"/>',
      clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
      route: '<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M8 5h8a4 4 0 0 1 0 8H8a3 3 0 0 0 0 6h8"/>',
      chevron: '<path d="m9 5 7 7-7 7"/>',
      focus: '<circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="1"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/>',
      plus: '<path d="M12 5v14M5 12h14"/>',
      close: '<path d="m6 6 12 12M6 18 18 6"/>',
      info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>'
    };
    return `<svg class="loc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.pin}</svg>`;
  }
  function groupStops(nodes) {
    const stops = [];
    nodes.forEach((node, index) => {
      const stop = stops.at(-1), previous = stop?.nodes.at(-1);
      if (previous && node.node_id && node.node_id === previous.node_id && node.status === 'stationary' &&
          (!node.city || !previous.city || node.city.replace(/市$/, '') === previous.city.replace(/市$/, '')) &&
          previous.status === 'stationary' && previous.end_minute === node.start_minute) {
        stop.nodes.push(node); stop.lastIndex = index;
      } else stops.push({nodes: [node], lastIndex: index});
    });
    return stops;
  }
  function transferKind(transfer, previous, next) {
    if (!transfer || transfer.same_location) return 'stay';
    if (previous.status === 'in_transit' || next?.status === 'in_transit') return 'scheduled';
    if (!previous.known || !next?.known) return 'unmapped';
    return transfer.known ? 'known' : transfer.route_minutes ? 'short' : 'missing';
  }
  function jumpTo(element) {
    if (!element) return;
    element.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center'});
    element.focus({preventScroll: true});
  }
  function render() {
    cleanup();
    if (activeView !== 'location') return;
    const root = $('#view-location'), persona = activePersona, date = selectedDate, requestId = ++renderId;
    const isCurrent = () => activeView === 'location' && activePersona === persona && requestId === renderId;
    if (!documents.has(persona)) documents.set(persona, {data: null, draft: null, saving: false, error: '', loading: null});
    const doc = documents.get(persona);
    let locationValue = null, map = null, mapModel = null, mapSignature = '', selectedPlace = null, refreshTimer = 0, refreshing = false, retryAgent = false;
    cleanup = () => { map?.destroy(); clearTimeout(refreshTimer); };
    root.innerHTML = `
      <div class="loc-heading"><div><p class="eyebrow">A LITTLE WORLD, EVERY DAY</p><h3>${esc(state.personas[persona].name)}的日常地图</h3><p>看看她停在哪里，又正走向哪里。</p></div><div class="loc-heading-actions">${personaSelector()}<button class="loc-button loc-refresh" id="retryLocation" aria-label="刷新位置">${icon('refresh')}</button></div></div>
      <div class="loc-datebar"><div class="loc-date-controls"><button class="loc-button loc-prev" data-date-step="-1" aria-label="前一天">${icon('chevron')}</button><input id="locationDate" type="date" aria-label="选择日期" value="${attr(date)}"/><button class="loc-button" data-date-step="1" aria-label="后一天">${icon('chevron')}</button><span class="loc-weekday">${weekdayLabelForDate(dateFromKey(date))}</span><button class="loc-today ${date === localClock().date ? 'selected' : ''}" data-location-today>今天</button></div><span class="loc-date-note">${icon('clock')} 日程位置 · 北京时间</span></div>
      <div id="locationAgent" class="loc-agent-status" aria-live="polite"></div>
      <div class="loc-map-layout"><section class="loc-map-panel" aria-labelledby="locationMapTitle">
        <div class="loc-map-heading"><div><h3 id="locationMapTitle">正在读取当天行程</h3><p data-map-subtitle>从已确认的地点开始</p></div><span class="loc-map-tag">二维地图</span></div>
        <div id="locationMap"><div class="loc-loading">正在展开地图…</div></div>
        <div class="loc-map-source"><span>地图仅展示日程已记录的地点</span></div>
      </section><aside class="loc-map-sidebar"><section id="locationNow" class="loc-now" aria-live="polite"><div class="loc-loading">正在读取位置…</div></section><section id="locationDetails" class="loc-place-details" tabindex="-1"><div class="loc-loading">正在整理地点…</div></section></aside></div>
      <div class="loc-bottom-layout"><div><section class="loc-places-panel"><div class="loc-panel-heading"><div><p class="eyebrow">PLACES OF THE DAY</p><h3>今天会去哪里</h3></div><button class="loc-text-button" data-open-schedule>查看日程 ${icon('arrow')}</button></div><div id="locationPlaces"></div><p class="loc-stay-note">${icon('pin')}同一地点只记录停留，原地换活动无需通勤。</p></section><div id="locationAttention"></div></div>
      <section class="loc-routes" aria-labelledby="locationRoutesTitle"><div class="loc-panel-heading"><div><p class="eyebrow">ROUTE BOOK</p><h3 id="locationRoutesTitle">常用路线</h3></div><span id="routeCount" class="loc-count">—</span></div><p class="loc-section-note">设置不同地点间的交通方式与耗时。</p><div id="locationRouteContent"><div class="loc-loading">正在读取路线…</div></div></section></div>`;
    bindPersonaSwitch(root);
    function openSchedule() {
      activeScheduleDate = date; scheduleMonthCursor = dateFromKey(date); scheduleViewMode = 'month'; activeView = 'schedule'; window.render();
    }
    function setDate(next) { selectedDate = next; render(); }
    $('#locationDate').onchange = event => { if (event.target.value && event.target.validity.valid) setDate(event.target.value); };
    root.querySelectorAll('[data-date-step]').forEach(button => button.onclick = () => { const next = dateFromKey(date); next.setDate(next.getDate() + Number(button.dataset.dateStep)); setDate(todayKey(next)); });
    root.querySelector('[data-location-today]').onclick = () => setDate(localClock().date);
    $('#retryLocation').onclick = async () => {
      retryAgent = true;
      if (!doc.saving) {
        try { doc.data = await apiJSON(`/api/location-config?persona_id=${encodeURIComponent(persona)}`); drawRoutes(); }
        catch (error) { doc.error = error.message; }
      }
      if (isCurrent()) fetchLocation();
    };
    root.querySelector('[data-open-schedule]').onclick = openSchedule;
    function editRoutes(route) {
      if (doc.saving || !doc.data) return;
      doc.draft ??= clone(doc.data.routes);
      if (route && doc.draft.length < 32) doc.draft.push(route);
      doc.error = ''; drawRoutes();
      const fields = root.querySelectorAll('#locationRouteContent [data-route="from"]');
      jumpTo(fields[route ? fields.length - 1 : 0]);
    }
    function drawRoutes() {
      if (!isCurrent()) return;
      const content = $('#locationRouteContent');
      $('#routeCount').textContent = doc.data ? `${doc.data.routes.length} 条` : '—';
      if (!doc.data) {
        content.innerHTML = doc.error ? `<div class="loc-empty"><strong>路线读取失败</strong><p>${esc(doc.error)}</p><button class="loc-text-button" data-retry-routes>重新读取</button></div>` : '<div class="loc-loading">正在读取路线…</div>';
        content.querySelector('[data-retry-routes]')?.addEventListener('click', () => { doc.error = ''; render(); });
        return;
      }
      if (doc.draft === null) {
        content.innerHTML = `<div class="loc-saved-routes">${doc.data.routes.map(route => `<div class="loc-saved-route"><div class="loc-route-points"><span>${esc(route.from)}</span>${icon('arrow')}<span>${esc(route.to)}</span></div><div class="loc-route-meta"><span>${icon('route')}${esc(route.mode)}</span><strong>${esc(route.minutes)}<small> 分钟</small></strong></div></div>`).join('') || '<div class="loc-empty"><strong>还没有常用路线</strong><p>添加第一条路线，串起不同地点。</p></div>'}</div><div class="loc-route-actions">${doc.data.routes.length ? '<button class="loc-button" data-edit-routes>编辑路线</button>' : ''}<button class="loc-button loc-add" id="addRoute">${icon('plus')} 添加路线</button></div>`;
        content.querySelector('[data-edit-routes]')?.addEventListener('click', () => editRoutes());
        $('#addRoute').onclick = () => editRoutes({from: '', to: '', mode: '步行', minutes: ''});
        return;
      }
      content.innerHTML = `<form id="locationRouteForm"><fieldset class="loc-route-fields" ${doc.saving ? 'disabled' : ''}><div id="routeRows">${doc.draft.map((route, index) => `
        <div class="loc-route-editor" data-route-index="${index}"><div class="loc-route-editor-head"><strong>路线 ${String(index + 1).padStart(2, '0')}</strong><button type="button" class="loc-delete" data-route-delete="${index}" aria-label="删除路线 ${index + 1}">${icon('close')}</button></div>
        <label>出发地<input data-route="from" maxlength="80" required value="${attr(route.from)}" placeholder="如：宿舍" /></label>
        <label>目的地<input data-route="to" maxlength="80" required value="${attr(route.to)}" placeholder="如：教学楼" /></label>
        <div class="loc-route-field-pair"><label>交通方式<input data-route="mode" maxlength="40" required value="${attr(route.mode)}" /></label><label>耗时（分钟）<input data-route="minutes" type="number" min="1" max="240" step="1" required value="${attr(route.minutes)}" /></label></div></div>`).join('') || '<p class="loc-section-note">保存后将移除全部常用路线。</p>'}</div>
        <button type="button" class="loc-text-button loc-add-row" id="addRoute" ${doc.draft.length >= 32 ? 'disabled' : ''}>${icon('plus')} 添加一条路线</button>
        <p class="loc-form-error" role="alert" ${doc.error ? '' : 'hidden'}>${doc.error ? esc(doc.error) : ''}</p>
        <div class="loc-route-actions"><button type="button" class="loc-button" data-cancel-routes>取消</button><button type="submit" class="loc-button loc-primary" id="saveRoutes">${doc.saving ? '正在保存…' : '保存路线'}</button></div></fieldset></form>`;
      content.querySelectorAll('[data-route]').forEach(input => input.oninput = () => {
        const index = Number(input.closest('[data-route-index]').dataset.routeIndex);
        doc.error = ''; content.querySelector('.loc-form-error').hidden = true;
        doc.draft[index][input.dataset.route] = input.dataset.route === 'minutes' && input.value !== '' ? Number(input.value) : input.value;
      });
      content.querySelectorAll('[data-route-delete]').forEach(button => button.onclick = () => { doc.draft.splice(Number(button.dataset.routeDelete), 1); drawRoutes(); });
      $('#addRoute').onclick = () => editRoutes({from: '', to: '', mode: '步行', minutes: ''});
      content.querySelector('[data-cancel-routes]').onclick = () => { doc.draft = null; doc.error = ''; drawRoutes(); };
      $('#locationRouteForm').onsubmit = async event => {
        event.preventDefault();
        if (doc.saving) return;
        const routes = doc.draft.map(route => ({from: route.from.trim(), to: route.to.trim(), mode: route.mode.trim(), minutes: Number(route.minutes)}));
        const nodeId = label => doc.data.location_aliases?.[label] || Object.entries(doc.data.nodes || {}).find(([id, name]) => label === id || label === name)?.[0] || label;
        if (routes.some(route => !route.from || !route.to || !route.mode || !Number.isInteger(route.minutes) || route.minutes < 1 || route.minutes > 240)) {
          doc.error = '请填写完整路线，耗时应为 1–240 的整数分钟。'; drawRoutes(); return;
        }
        if (routes.some(route => nodeId(route.from) === nodeId(route.to))) { doc.error = '同一地点无需设置路线，请选择不同的出发地和目的地。'; drawRoutes(); return; }
        doc.saving = true; doc.error = ''; drawRoutes();
        const pending = apiJSON('/api/location-config', {method: 'POST', headers: writeHeaders(persona), body: JSON.stringify({persona_id: persona, routes})});
        writes.set(persona, pending);
        try { await pending; doc.data = {...doc.data, routes}; doc.draft = null; toast('路线已保存'); }
        catch (error) { doc.error = `保存失败：${error.message}`; }
        finally {
          doc.saving = false;
          if (writes.get(persona) === pending) writes.delete(persona);
          if (isCurrent()) { if (doc.draft === null) render(); else drawRoutes(); }
        }
      };
    }


    function drawDetails() {
      if (!locationValue) return;
      const nodes = locationValue.timeline.nodes, clock = localClock(), valid = !locationValue.timeline.errors.length;
      const matches = nodes.filter(node => mapModel.key(node) === selectedPlace && node.status !== 'in_transit');
      const config = doc.data || {}, placeId = mapModel.resolve(selectedPlace), place = mapModel.places[placeId || selectedPlace];
      const label = place ? place.label : matches[0]?.location || config.nodes?.[selectedPlace] || '当天地点';
      const stops = groupStops(matches), isToday = date === clock.date;
      const currentActivity = node => isToday && valid && locationValue.type === 'node' && locationValue.start_minute === node.start_minute && locationValue.end_minute === node.end_minute;
      const assigned = matches.some(node => node.assignment === 'agent');
      $('#locationDetails').innerHTML = `<div class="loc-place-detail-head"><span class="loc-detail-icon" style="--place-color:${place?.color || '#b5a3aa'}">${icon('pin')}</span><div><p class="eyebrow">${placeId ? 'ON THE MAP' : 'PLACE DETAILS'}</p><h3>${esc(label)}</h3></div><span class="loc-count">${matches.length} 项活动</span></div><p class="loc-place-note">${esc(place?.note || (selectedPlace ? '地址尚未确认，暂不放置地图标记' : '点击地图上的地点，查看这一天的活动'))}</p>
        ${mapModel.agent ? `<div class="loc-assignment-note"><span class="loc-assignment">${assigned ? 'Agent 安排' : '日程指定 / 常用地点'}</span><p>${esc(matches[0]?.location_reason || '')}</p>${place?.source ? `<a href="${attr(place.source)}" target="_blank" rel="noopener noreferrer">查看地点来源 ↗</a>` : ''}</div>` : ''}
        <div class="loc-place-visits">${stops.map(stop => `<article class="loc-visit"><header><time>${esc(stop.nodes[0].enter)}–${esc(stop.nodes.at(-1).exit)}</time><span>停留 ${duration(stop.nodes.at(-1).end_minute - stop.nodes[0].start_minute)}</span></header>${stop.nodes.map(node => `<div class="loc-visit-activity ${currentActivity(node) ? 'is-current' : ''}"><span>${esc(node.activity || '活动待补充')}</span>${currentActivity(node) ? '<b>此刻</b>' : ''}${stop.nodes.length > 1 ? `<small>${esc(node.enter)}–${esc(node.exit)}</small>` : ''}</div>`).join('')}</article>`).join('') || '<div class="loc-empty"><p>这一天没有安排在这里的活动。</p></div>'}</div>`;
      root.querySelectorAll('[data-select-place]').forEach(button => { const active = button.dataset.selectPlace === selectedPlace; button.classList.toggle('selected', active); button.setAttribute('aria-pressed', String(active)); });
    }
    function selectPlace(key) { selectedPlace = key; map?.select(key); drawDetails(); }
    function drawLocation(value) {
      locationValue = value;
      const agent = value.agent;
      $('#locationAgent').innerHTML = agent?.status === 'ready' ? `<span>✧ 地点已随日程更新</span><small>自动选址显示“Agent 安排” · 地图为日程示意</small>` : '';
      if (agent?.status === 'partial') $('#locationAgent').innerHTML = '<span>✧ 部分地点仍待确认</span><small>保留已找到的地点与范围；点击右上角刷新重新检索</small>';
      if (agent && ['pending', 'error'].includes(agent.status)) {
        map?.destroy(); map = null; mapSignature = ''; selectedPlace = null;
        const error = agent.status === 'error';
        $('#locationAgent').innerHTML = `<span>${error ? esc(agent.error) : '✧ 正在根据当前日程规划地点…'}</span><small>${error ? '点击右上角刷新重试' : '正在核对真实地点，完成后自动更新'}</small>`;
        $('#locationMapTitle').textContent = '当天行程地图';
        $('#locationMap').innerHTML = `<div class="loc-map-unavailable"><span>⌖</span><h4>${error ? '地点规划暂未完成' : '正在展开新的日程'}</h4><p>${error ? '保留原日程，重新规划后再显示地图。' : '正在查找地点和路线…'}</p></div>`;
        $('#locationNow').innerHTML = '<div class="loc-empty"><h3>位置正在确认</h3><p>地点规划完成后，显示与当前日程对应的位置。</p></div>';
        $('#locationDetails').innerHTML = ''; $('#locationPlaces').innerHTML = ''; $('#locationAttention').innerHTML = '';
        return;
      }
      const timeline = value.timeline, nodes = timeline.nodes, stops = groupStops(nodes), clock = localClock();
      const isToday = date === clock.date, valid = !timeline.errors.length, config = doc.data || {};
      mapModel = window.LocationMap.describe({timeline, config, persona});
      $('#locationMapTitle').textContent = mapModel.title;
      root.querySelector('[data-map-subtitle]').textContent = mapModel.subtitle;
      root.querySelector('.loc-map-tag').textContent = mapModel.tag;
      root.querySelector('.loc-map-source').innerHTML = mapModel.agent
        ? '<span>真实地点与道路参考 · 交通连线可能为示意</span><span>地点来源见详情 · <a href="https://map.baidu.com/" target="_blank" rel="noopener noreferrer">百度地图</a> · © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a></span>'
        : mapModel.kind === 'regional'
        ? '<span>城市坐标与海岸线参考真实地理 · 连线为行程示意</span><a href="https://www.naturalearthdata.com/downloads/50m-cultural-vectors/50m-populated-places/" target="_blank" rel="noopener noreferrer">Natural Earth · 公共领域</a>'
        : persona === 'xiaotian' ? '<span>真实地理参考 · 区域与步行路线为示意</span><span><a href="https://www.bda.edu.cn/xygk/xydt/index.htm" target="_blank" rel="noopener noreferrer">北舞校园图</a> · © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a></span>' : '<span>地图仅展示已确认的地点</span>';
      const activeStop = stops.find(stop => stop.nodes.some(node => valid && isToday && value.type === 'node' && value.start_minute === node.start_minute && value.end_minute === node.end_minute));
      const first = activeStop?.nodes[0], last = activeStop?.nodes.at(-1);
      const next = isToday ? nodes.find(node => node.status !== 'in_transit' && node.start_minute > clock.minute && mapModel.key(node) !== mapModel.key(value)) : nodes[0];
      const labelFor = node => mapModel.places[mapModel.resolve(node)]?.label || node.location || '位置待确认';
      const status = isToday ? ({stationary: value.precision === 'city' ? '所在城市' : '此刻在这里', in_transit: '正在路上', unknown: '位置待确认'}[value.status] || '位置待确认') : '计划位置';
      const title = !isToday ? `${Number(date.slice(5, 7))}月${Number(date.slice(8))}日` : value.status === 'stationary' ? labelFor(value) : value.status === 'in_transit' ? (value.from_city && value.to_city ? `${value.from_city} → ${value.to_city}` : '正在途中') : '位置待确认';
      const activity = isToday ? value.activity || (value.status === 'in_transit' ? value.location : nodes.length ? '当前时段的位置尚未确认' : '今天还没有日程安排') : `${nodes.length} 项活动 · 点击地点查看详情`;
      $('#locationNow').innerHTML = `<div class="loc-current"><div class="loc-current-top"><span class="loc-status ${value.status === 'unknown' && isToday ? 'unconfirmed' : ''}"><i></i>${status}</span><span class="loc-updated">${clock.time}</span></div><div class="loc-current-main"><h3>${esc(title)}</h3><p>${esc(activity)}</p>${value.assignment === 'agent' ? '<span class="loc-assignment">Agent 安排</span>' : ''}</div>${first && value.status === 'stationary' ? `<div class="loc-stay-duration">${icon('clock')} ${esc(first.enter)}–${esc(last.exit)} <span data-current-duration>已停留 ${duration(clock.minute - first.start_minute)}</span></div>` : value.status === 'in_transit' && isToday ? `<div class="loc-stay-duration">${icon('route')}预计 ${esc(value.eta || '时间待确认')} 到达</div>` : ''}${isToday && value.status === 'stationary' ? `<button class="loc-text-button" data-locate-current>${icon('focus')} 在地图上查看</button>` : ''}</div><div class="loc-next"><span class="loc-next-label">${isToday ? '下一站' : '当天起点'}</span><h4>${next ? esc(labelFor(next)) : '这一天，先到这里'}</h4><p>${next ? `${esc(next.enter)} · ${esc(next.activity)}` : '新的安排会在地图上更新。'}</p>${next ? '<button class="loc-text-button" data-next-place>查看地点 '+icon('arrow')+'</button>' : ''}</div>`;
      root.querySelector('[data-locate-current]')?.addEventListener('click', () => selectPlace(mapModel.key(value)));
      root.querySelector('[data-next-place]')?.addEventListener('click', () => selectPlace(mapModel.key(next)));
      const places = [...new Map(nodes.filter(node => node.status !== 'in_transit').map(node => [mapModel.key(node), node])).entries()];
      if (!places.some(([key]) => key === selectedPlace)) selectedPlace = isToday && value.status === 'stationary' ? mapModel.key(value) : places[0]?.[0];
      $('#locationPlaces').innerHTML = places.length ? `<div class="loc-places-grid">${places.map(([key, node]) => {
        const mapped = mapModel.resolve(node), place = mapModel.places[mapped || key];
        const count = nodes.filter(item => mapModel.key(item) === key && item.status !== 'in_transit').length;
        const assigned = nodes.some(item => mapModel.key(item) === key && item.assignment === 'agent');
        return `<button class="loc-place-button" data-select-place="${attr(key)}" style="--place-color:${place?.color || '#b5a3aa'}"><span class="loc-place-button-icon">${icon('pin')}</span><span><strong>${esc(place?.label || node.location)}</strong><small>${mapped ? `${count} 项活动${node.precision === 'city' ? ' · 城市级' : ''}` : node.city ? '已知城市 · 坐标待补充' : '未定位'}${assigned ? ' · Agent 安排' : ''}${mapModel.key(value) === key && isToday && value.status === 'stationary' ? ' · 此刻' : ''}</small></span>${icon('chevron')}</button>`;
      }).join('')}</div>` : '<div class="loc-empty"><p>这一天还没有行程，安排后就会在这里出现。</p><button class="loc-button" data-empty-schedule>去安排日程</button></div>';
      root.querySelector('.loc-places-panel h3').textContent = isToday ? '今天会去哪里' : '当天的地点';
      root.querySelectorAll('[data-select-place]').forEach(button => button.onclick = () => { selectPlace(button.dataset.selectPlace); if (innerWidth < 700) jumpTo($('#locationDetails')); });
      root.querySelector('[data-empty-schedule]')?.addEventListener('click', openSchedule);
      const signature = JSON.stringify([timeline, config]);
      if (!map || signature !== mapSignature) {
        map?.destroy(); mapSignature = signature;
        map = window.LocationMap.create({element: $('#locationMap'), persona, name: state.personas[persona].name, config, timeline, value, date, clock: localClock,
          onSelect: (id, place, userInitiated) => { selectedPlace = id; drawDetails(); if (userInitiated && innerWidth < 700) jumpTo($('#locationDetails')); }});
        map.select(selectedPlace);
      } else map.update(value);
      drawDetails();
      const links = stops.map(stop => { const previous = stop.nodes.at(-1), next = nodes[stop.lastIndex + 1], transfer = timeline.transfers[stop.lastIndex]; return {transfer, kind: transferKind(transfer, previous, next)}; });
      const pending = links.filter(link => ['missing', 'short'].includes(link.kind));
      $('#locationAttention').innerHTML = `${!valid ? '<div class="loc-attention" role="alert"><strong>日程时间存在冲突</strong><p>调整日程后才能确认当前位置。</p><button class="loc-text-button" data-fix-schedule>调整日程</button></div>' : ''}${pending.length ? `<details class="loc-attention"><summary>${icon('info')} ${pending.length} 段通勤待完善 ${icon('chevron')}</summary><p>不同地点之间，需要路线和足够的通勤时间。</p>${pending.map((link, index) => `<div class="loc-pending-route"><div><strong>${esc(link.transfer.from)} → ${esc(link.transfer.to)}</strong><span>${link.kind === 'short' ? `需要 ${link.transfer.route_minutes} 分钟，日程仅预留 ${link.transfer.gap_minutes} 分钟` : '尚未设置交通方式与耗时'}</span></div><button class="loc-text-button" data-pending-route="${index}">${link.kind === 'short' ? '调整日程' : '设置路线'}</button></div>`).join('')}</details>` : ''}`;
      if (timeline.location_conflicts?.length) $('#locationAttention').insertAdjacentHTML('beforeend', `<div class="loc-attention"><strong>行程时间需要调整</strong>${timeline.location_conflicts.map(item => `<p>${esc(item.activity)}：路线约需 ${item.needed_minutes} 分钟，日程预留 ${item.available_minutes} 分钟。</p>`).join('')}</div>`);
      root.querySelector('[data-fix-schedule]')?.addEventListener('click', openSchedule);
      root.querySelectorAll('[data-pending-route]').forEach(button => button.onclick = () => { const link = pending[Number(button.dataset.pendingRoute)]; if (link.kind === 'short') openSchedule(); else editRoutes({from: link.transfer.from, to: link.transfer.to, mode: '步行', minutes: ''}); });
    }
    async function fetchLocation() {
      if (refreshing || !isCurrent()) return;
      refreshing = true;
      try {
        try { await writes.get(persona); } catch { /* Show the last saved routes. */ }
        const retry = retryAgent; retryAgent = false;
        const value = await apiJSON(`/api/location?persona_id=${encodeURIComponent(persona)}&date=${date}&plan=1${retry ? '&retry=1' : ''}`);
        if (isCurrent()) {
          if (JSON.stringify(value) !== JSON.stringify(locationValue)) drawLocation(value);
          else {
            const clock = localClock();
            const updated = root.querySelector('#locationNow .loc-updated');
            if (updated) updated.textContent = clock.time;
            const durationLabel = root.querySelector('[data-current-duration]');
            if (durationLabel) {
              const stop = groupStops(value.timeline.nodes).find(stop => stop.nodes.some(node => node.start_minute === value.start_minute && node.end_minute === value.end_minute));
              if (stop) durationLabel.textContent = `已停留 ${duration(clock.minute - stop.nodes[0].start_minute)}`;
            }
          }
        }
      } catch (error) {
        if (!isCurrent()) return;
        if (locationValue) { const updated = root.querySelector('#locationNow .loc-updated'); if (updated) updated.textContent = '更新失败'; return; }
        $('#locationNow').innerHTML = `<div class="loc-empty"><h3>暂时无法读取位置</h3><p>${esc(error.message)}</p></div>`;
        $('#locationMap').innerHTML = '<div class="loc-map-unavailable"><h4>地图尚未加载</h4><p>点击右上角刷新重试。</p></div>';
        $('#locationDetails').innerHTML = '';
      } finally {
        refreshing = false; clearTimeout(refreshTimer);
        if (isCurrent()) refreshTimer = setTimeout(() => { if (!document.hidden) fetchLocation(); else refreshTimer = setTimeout(fetchLocation, 15000); }, locationValue?.agent?.status === 'pending' ? 2000 : 15000);
      }
    }
    drawRoutes();
    if (!doc.data && !doc.loading) {
      doc.loading = (async () => {
        try { await writes.get(persona); } catch { /* Load the last saved configuration. */ }
        try { doc.data = await apiJSON(`/api/location-config?persona_id=${encodeURIComponent(persona)}`); doc.error = ''; }
        catch (error) { doc.error = error.message; }
        finally { doc.loading = null; }
      })();
    }
    (async () => {
      await doc.loading;
      if (!isCurrent()) return;
      drawRoutes(); await fetchLocation();
    })();
  }
  window.LocationPage = {render, dispose: () => cleanup(), hasUnsaved: () => [...documents.values()].some(doc => doc.saving || (doc.draft !== null && JSON.stringify(doc.draft) !== JSON.stringify(doc.data?.routes)))};
})();
