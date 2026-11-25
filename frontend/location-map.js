/* Persona facts: data/persona_dossier.json. Map regions are approximate, not GPS.
 * Projection and sources: assets/bda-campus-map.svg and docs/location-map.md. */
(() => {
  const places = {
    dorm: {label: '宿舍', point: [645, 350], note: '宿舍区示意 · 不定位具体楼栋或房间', color: '#c98194'},
    creative: {label: '教学区', point: [653, 433], note: '教学区示意 · 创意学院具体楼栋待确认', color: '#9b8ab8'},
    campus: {label: '北舞校园', point: [620, 591], note: '校园范围 · 标记位于校内广场示意点', color: '#86a184'},
    dining: {label: '食堂', point: [340, 510], note: '综合楼餐饮区域示意', color: '#c6a264'}
  };
  // Junctions follow mapped campus service roads. A trip never cuts through buildings.
  const junctions = {west: [322, 324], east: [639, 293], southeast: [669, 586], southwest: [350, 616],
    ...Object.fromEntries(Object.entries(places).map(([id, place]) => [id, place.point]))};
  const edges = [['west', 'east'], ['east', 'dorm'], ['dorm', 'creative'], ['creative', 'southeast'],
    ['southeast', 'campus'], ['campus', 'southwest'], ['southwest', 'dining'], ['dining', 'west']];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  function resolve(label, config, persona) {
    if (persona !== 'xiaotian' || !label) return null;
    const id = config?.location_aliases?.[label] || Object.entries(config?.nodes || {}).find(([key, name]) => label === key || label === name)?.[0];
    return places[id] ? id : null;
  }
  function routePoints(from, to) {
    if (!places[from] || !places[to] || from === to) return null;
    const distances = {[from]: 0}, previous = {}, remaining = new Set(Object.keys(junctions));
    while (remaining.size) {
      const current = [...remaining].sort((a, b) => (distances[a] ?? Infinity) - (distances[b] ?? Infinity))[0];
      if (!Number.isFinite(distances[current])) return null;
      if (current === to) break;
      remaining.delete(current);
      for (const edge of edges) {
        const neighbor = edge[0] === current ? edge[1] : edge[1] === current ? edge[0] : null;
        if (!neighbor || !remaining.has(neighbor)) continue;
        const a = junctions[current], b = junctions[neighbor], distance = distances[current] + Math.hypot(b[0] - a[0], b[1] - a[1]);
        if (distance < (distances[neighbor] ?? Infinity)) { distances[neighbor] = distance; previous[neighbor] = current; }
      }
    }
    const ids = [to];
    while (ids[0] !== from) { if (!previous[ids[0]]) return null; ids.unshift(previous[ids[0]]); }
    return ids.map(id => junctions[id]);
  }
  const cityName = value => {
    const label = String(value || '').trim();
    return window.LocationGeography?.cities?.[label.replace(/市$/, '')] ? label.replace(/市$/, '') : label;
  };
  function agentMap(timeline, config, persona) {
    const plan = timeline.map_plan, entries = Object.entries(plan.places || {});
    const campusOnly = entries.length && entries.every(([, place]) => places[place.node_id] && place.id.startsWith('known:'));
    const coordinates = entries.map(([, place]) => place.coordinates);
    const latitude = coordinates.reduce((sum, p) => sum + p[1], 0) / Math.max(1, coordinates.length);
    const cos = Math.cos(latitude * Math.PI / 180), km = 111.195;
    const xs = coordinates.map(p => p[0] * cos * km), ys = coordinates.map(p => -p[1] * km);
    const center = coordinates.length ? [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2] : [0, 0];
    const width = Math.max(1.5, Math.max(...xs) - Math.min(...xs)), height = Math.max(1.2, Math.max(...ys) - Math.min(...ys));
    const scale = Math.min(680 / width, 470 / height);
    const project = point => campusOnly ? [(point[0] - 116.31065) * 200000, (39.94735 - point[1]) * 261000] :
      [500 + (point[0] * cos * km - center[0]) * scale, 380 + (-point[1] * km - center[1]) * scale];
    const mapped = Object.fromEntries(entries.map(([id, place], index) => [id, {...place,
      point: campusOnly ? places[place.node_id].point : project(place.coordinates),
      color: ['#c98194', '#86a184', '#9b8ab8', '#c6a264'][index % 4],
      note: `${place.address || place.city || ''} · ${{city:'城市级示意',area:'区域示意',street:'街道参考',station:'交通站点参考',address:'地址参考',unknown:'地点类型待确认'}[place.precision] || '真实场馆参考'}${place.note ? ' · ' + place.note : ''}`} ]));
    const resolveId = value => {
      const id = typeof value === 'string' ? value : value?.place_id;
      if (mapped[id]) return id;
      const known = resolve(typeof value === 'string' ? value : value?.node_id || value?.location, config, persona);
      return mapped['known:' + known] ? 'known:' + known : Object.keys(mapped).find(key => mapped[key].label === id) || null;
    };
    const cityLabels = [...new Set(entries.map(([, p]) => p.city?.replace(/市$/, '')).filter(Boolean))];
    return {kind: campusOnly ? 'campus' : 'regional', agent: true, places: mapped, project,
      local: width < 50 && height < 50, overview: width >= 50 || height >= 50, background: plan.background || [],
      scaleKm: campusOnly ? 117 / 261000 * km : 117 / scale,
      title: campusOnly ? '北京舞蹈学院' : cityLabels.join(' · ') || '当天行程地图',
      subtitle: '按生效日程规划 · 自动选址标注 Agent 安排', tag: '卡通日程地图',
      resolve: resolveId, key: node => node?.place_id || `unknown:${node?.start_minute ?? ''}:${node?.location || ''}`,
      routePoints(from, to) {
        if (!mapped[from] || !mapped[to] || from === to) return null;
        const route = plan.routes.find(r => r.from_id === from && r.to_id === to);
        if (route?.points?.length > 1) return route.points.map(project);
        if (campusOnly) return routePoints(mapped[from].node_id, mapped[to].node_id);
        return [mapped[from].point, mapped[to].point];
      }};
  }
  function describe({timeline, config, persona}) {
    if (timeline.map_plan) return agentMap(timeline, config, persona);
    const nodes = timeline.nodes || [], catalog = window.LocationGeography?.cities || {};
    const campusCity = cityName(config?.city || '北京');
    const campusId = node => resolve(typeof node === 'string' ? node : node?.node_id || node?.location, config, persona);
    const recordedCity = node => cityName(node?.city) || (catalog[cityName(node?.location)] ? cityName(node.location) : '');
    const regional = nodes.some(node => (node.city && (cityName(node.city) !== campusCity || !campusId(node))) ||
      node.from_city || node.to_city || catalog[cityName(node.location)]);
    if (!regional) return {kind: 'campus', places, title: persona === 'xiaotian' ? '北京舞蹈学院' : '人物地图',
      subtitle: persona === 'xiaotian' ? '北京 · 海淀 · 万寿寺路 1 号' : '从已确认的地点开始',
      tag: '二维校园地图', scale: '约 50 米', resolve: campusId,
      key: node => node.node_id || node.location, routePoints};
    const cityFor = node => typeof node === 'string' ? (catalog[cityName(node)] ? cityName(node) : campusId(node) ? campusCity : '') :
      recordedCity(node) || (campusId(node) ? campusCity : '');
    const names = [...new Set(nodes.flatMap(node => node.status === 'in_transit' ?
      [cityName(node.from_city), cityName(node.to_city)] : [cityFor(node)]).filter(Boolean))];
    const coordinates = names.filter(name => catalog[name]).map(name => catalog[name]);
    const latitude = coordinates.length ? coordinates.reduce((total, point) => total + point[1], 0) / coordinates.length : 39;
    const cos = Math.cos(latitude * Math.PI / 180), km = 111.195;
    const xs = coordinates.map(p => p[0] * cos * km), ys = coordinates.map(p => -p[1] * km);
    const center = coordinates.length ? [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2] : [0, 0];
    const scale = Math.min(660 / Math.max(120, Math.max(...xs) - Math.min(...xs)), 460 / Math.max(100, Math.max(...ys) - Math.min(...ys)));
    const project = point => [500 + (point[0] * cos * km - center[0]) * scale, 380 + (-point[1] * km - center[1]) * scale];
    const cities = Object.fromEntries(names.map((name, i) => [name, {label: name, point: catalog[name] ? project(catalog[name]) : null,
      note: '城市级示意 · 日程未提供具体停留点，不代表实时 GPS 或街道定位', color: i % 2 ? '#bb89a1' : '#89a492'}]));
    return {kind: 'regional', places: cities, title: names.join(' · ') || '城市行程',
      subtitle: '按当天日程展开 · 城市级位置', tag: '二维城市地图',
      scaleKm: 117 / scale, project, resolve: value => {
        const name = cityFor(value); return cities[name]?.point ? name : null;
      }, key: node => cityFor(node) || (node.city ? `${node.city} · ${node.location}` : node.node_id || node.location),
      routePoints: (from, to) => {
        const a = cities[from]?.point, b = cities[to]?.point;
        if (!a || !b || from === to) return null;
        // Directional schematic arcs, never an invented railway or navigation route.
        const dx = b[0] - a[0], dy = b[1] - a[1], length = Math.hypot(dx, dy);
        return [a, [(a[0] + b[0]) / 2 - dy / length * 40, (a[1] + b[1]) / 2 + dx / length * 40], b];
      }};
  }
  function regionalBackground(model) {
    const point = coordinates => model.project(coordinates).map(v => v.toFixed(1)).join(',');
    const land = (window.LocationGeography?.land || []).map(ring => `<path d="M${ring.map(point).join(' L')}Z"/>`).join('');
    const grid = [];
    for (let lon = 105; lon <= 125; lon++) grid.push(`<path d="M${point([lon, 25])}L${point([lon, 43])}"/>`);
    for (let lat = 25; lat <= 43; lat++) grid.push(`<path d="M${point([105, lat])}L${point([125, lat])}"/>`);
    const towns = Object.values(model.places).filter(place => place.point).map(place => {
      const [x, y] = place.point;
      return `<g transform="translate(${x} ${y})" class="loc-city-illustration"><ellipse cy="15" rx="80" ry="28"/><path d="M-49 2v-33h20V2m5 0v-48h26V2m8 0v-25h25V2m5 0v-37h20V2"/><path class="loc-city-windows" d="M-43-24h8m-8 10h8m17-23h14m-14 12h14m-14 12h14m18-3h12m15-10h8m-8 12h8"/></g>`;
    }).join('');
    const features = (model.background || []).map(feature => `<path class="loc-geo-${escape(feature.kind)}" d="M${feature.points.map(point).join(' L')}"/>`).join('');
    const local = model.agent && model.local;
    return `<rect width="1000" height="760" fill="${local ? '#f3f1e5' : '#dcecee'}"/><g class="loc-regional-land">${land}</g><g class="loc-regional-grid">${grid.join('')}</g><g class="loc-geo-features">${features}</g>${towns}`;
  }
  function journey(value, nodes, config, persona, model = describe({timeline: {nodes}, config, persona})) {
    if (value?.status !== 'in_transit') return null;
    let from = value.from_place_id || value.from_city || value.from, to = value.to_place_id || value.to_city || value.to;
    if (value.type === 'node' && (!from || !to)) {
      const index = nodes.findIndex(node => node.start_minute === value.start_minute && node.end_minute === value.end_minute);
      // Only adjacent confirmed stops establish endpoints; don't jump across unknown activities.
      const before = nodes[index - 1], after = nodes[index + 1];
      if (before?.status !== 'stationary' || after?.status !== 'stationary') return null;
      from ||= before; to ||= after;
    }
    const source = model.resolve(from), target = model.resolve(to), points = model.routePoints(source, target);
    return points && value.end_minute > value.start_minute ? {from: source, to: target, points, start: value.start_minute, end: value.end_minute,
      mode: value.mode || (model.kind === 'regional' ? '城际交通' : '步行')} : null;
  }
  function create({element, persona, name, config, timeline, value, date, clock, onSelect}) {
    const $ = selector => element.querySelector(selector);
    const model = describe({timeline, config, persona}), mapPlaces = model.places;
    const mapped = Object.keys(mapPlaces).filter(id => model.agent || model.kind === 'regional' ? mapPlaces[id].point : persona === 'xiaotian' && config?.nodes?.[id]);
    if (!mapped.length) {
      element.innerHTML = model.kind === 'regional' ? `<div class="loc-map-unavailable"><span>⌖</span><h4>${escape(model.title)}</h4><p>日程已记录城市，地图坐标待补充；具体活动可在地点详情查看。</p></div>` : '<div class="loc-map-unavailable"><span>⌖</span><h4>这位人物还没有地图</h4><p>地点确认后，再把足迹放到真实地图上。</p></div>';
      return {destroy() {}, select() {}, update() {}};
    }
    let current = value, selected = null, preview = null, raf = 0, disposed = false;
    let zoom = 1, pan = [0, 0], drag = null, dragging = false;
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    const options = [];
    function addOption(route) {
      const from = model.resolve(route.from), to = model.resolve(route.to);
      const points = model.routePoints(from, to);
      if (points && !options.some(item => item.from === from && item.to === to)) options.push({...route, from, to, points});
    }
    const scheduled = timeline.nodes.map(node => ({node, trip: journey(node, timeline.nodes, config, persona, model)})).filter(item => item.trip);
    scheduled.forEach(({node, trip}) => addOption({...trip, minutes: trip.end - trip.start, enter: node.enter, exit: node.exit}));
    if (model.kind === 'campus') (config.routes || []).forEach(addOption);
    timeline.transfers.forEach((item, index) => {
      if (item.same_location || timeline.nodes[index]?.status !== 'stationary' || timeline.nodes[index + 1]?.status !== 'stationary') return;
      if (model.kind === 'regional' && !item.known) return;
      addOption({...item, minutes: item.route_minutes || null, mode: item.known ? item.mode : '步行示意'});
    });
    const labelAnchors = [], cityLabels = new Set();
    const markers = mapped.map(id => {
      const place = mapPlaces[id], [x, y] = place.point;
      const crowded = model.agent && labelAnchors.some(p => Math.hypot(x-p[0],y-p[1]) < 130);
      if (!crowded) labelAnchors.push(place.point);
      const city = place.city?.replace(/市$/, ''), showCity = model.overview && city && !cityLabels.has(city);
      if (city) cityLabels.add(city);
      return `<g class="loc-map-pin ${crowded ? 'loc-pin-crowded' : ''}" data-map-place="${escape(id)}" role="button" tabindex="0" aria-label="查看${escape(model.agent || model.kind === 'regional' ? place.label : config.nodes[id])}" transform="translate(${x} ${y})" style="--pin-color:${place.color}"><rect x="-23" y="-24" width="${Math.min(320, 50 + place.label.length * 14)}" height="48" fill="transparent" pointer-events="all"/><circle class="loc-pin-halo" r="22"/><circle class="loc-pin-ring" r="10"/><circle class="loc-pin-core" r="3"/><text class="loc-pin-title" x="20" y="5">${escape(place.label)}</text>${showCity ? `<text x="-14" y="-30" class="loc-pin-city">${escape(city)}</text>` : ''}</g>`;
    }).join('');
    element.innerHTML = `<div class="loc-map-canvas">
      <svg class="loc-campus-svg ${model.kind === 'regional' ? 'loc-regional-svg' : ''}" viewBox="0 0 1000 760" aria-label="${escape(model.title)}二维地图，点击地点查看活动" role="group">
        <defs><filter id="loc-avatar-shadow" x="-80%" y="-50%" width="260%" height="230%"><feDropShadow dx="0" dy="3" stdDeviation="3" flood-color="#725263" flood-opacity=".2"/></filter></defs>
        <g data-map-world>${model.kind === 'regional' ? regionalBackground(model) : '<image href="assets/bda-campus-map.svg" width="1000" height="760"/>'}
          <g class="loc-scheduled-paths">${scheduled.map(({trip}) => `<path d="M${trip.points.map(p => p.join(',')).join(' L')}"/>`).join('')}</g>
          <path class="loc-path-underlay"/><path class="loc-path-route"/><path class="loc-path-done"/>
          <g class="loc-map-pins">${markers}</g>
          <g class="loc-avatar" visibility="hidden" aria-label="人物当前位置">
            <ellipse class="loc-avatar-shadow" cy="3" rx="15" ry="5"/>
            <g class="loc-avatar-person" filter="url(#loc-avatar-shadow)">
              <g class="loc-person-body"><path d="M-10-31Q-19-12-10-6L10-6Q18-16 10-31" fill="#54434b"/>
                <g class="loc-leg loc-leg-left"><path d="M-5-11-6 0" stroke="#5d5364" stroke-width="5" stroke-linecap="round"/><path d="M-6 1h-5" stroke="#fffaf5" stroke-width="5" stroke-linecap="round"/></g>
                <g class="loc-leg loc-leg-right"><path d="M5-11 6 0" stroke="#5d5364" stroke-width="5" stroke-linecap="round"/><path d="M6 1h5" stroke="#fffaf5" stroke-width="5" stroke-linecap="round"/></g>
                <path d="M-7-25Q0-29 7-25L10-11Q0-7-10-11Z" fill="#d684a1" stroke="#b66686" stroke-width="1"/>
                <path class="loc-arm loc-arm-left" d="M-7-23-12-14" stroke="#f5d6c1" stroke-width="4" stroke-linecap="round"/>
                <path class="loc-arm loc-arm-right" d="M7-23 12-14" stroke="#f5d6c1" stroke-width="4" stroke-linecap="round"/>
                <circle cy="-34" r="12" fill="#55444c"/><ellipse cy="-32" rx="9" ry="10" fill="#f8dec9"/>
                <path d="M-11-34Q-10-49 3-46 15-43 11-31L7-36-2-39-8-32Z" fill="#55444c"/>
                <circle cx="-3.5" cy="-32" r="1.1" fill="#55444c"/><circle cx="3.5" cy="-32" r="1.1" fill="#55444c"/>
                <path d="M-2-27Q0-25 2-27" fill="none" stroke="#ba7f7f" stroke-width="1.2" stroke-linecap="round"/>
                <path d="M8-43 13-46 15-41 10-40Z" fill="#eab0bf"/>
              </g>
            </g><g class="loc-vehicle" display="none"><rect x="-16" y="-26" width="32" height="23" rx="8" fill="#ba85a3" stroke="white" stroke-width="2"/><rect x="-11" y="-21" width="22" height="9" rx="3" fill="#f5e9ee"/><circle cx="-10" cy="-2" r="4" fill="#685a66"/><circle cx="10" cy="-2" r="4" fill="#685a66"/></g>
            <g class="loc-avatar-label" transform="translate(0 -67)"><rect x="-54" y="-13" width="108" height="25" rx="12"/><text text-anchor="middle" y="4">${escape(name)}</text></g>
          </g>
        </g>
        <g class="loc-compass" transform="translate(938 57)"><circle r="27"/><path d="m0-15 7 23-7-5-7 5Z"/><text y="-34" text-anchor="middle">N</text></g>
        <g class="loc-map-scale" transform="translate(37 714)"><path d="M0-5v5h117v-5"/><text x="58" y="17" text-anchor="middle">约 50 米</text></g>
      </svg>
      <div class="loc-map-mode"><i></i><span data-map-mode>日程位置</span></div>
      <div class="loc-map-tools"><button type="button" data-map-zoom="1" aria-label="放大地图">+</button><button type="button" data-map-zoom="-1" aria-label="缩小地图">−</button><button type="button" data-map-reset aria-label="重置地图">⌖</button></div>
      <div class="loc-map-caption" data-map-caption></div>
    </div>
    <div class="loc-map-player"><div class="loc-map-player-top"><span class="loc-preview-label">路线预览</span><select aria-label="选择预览路线" data-map-route ${options.length ? '' : 'disabled'}>${options.length ? options.map((route, index) => `<option value="${index}">${escape(mapPlaces[route.from].label)} → ${escape(mapPlaces[route.to].label)}${route.enter ? ` · ${escape(route.enter)}–${escape(route.exit)}` : ''}</option>`).join('') : '<option>暂无已定位的路线</option>'}</select><button type="button" class="loc-button" data-map-play ${options.length ? '' : 'disabled'}>▶ 播放</button><button type="button" class="loc-text-button" data-map-live hidden>返回当前</button></div><div class="loc-map-player-status"><span data-map-progress-label>动画预览独立于真实日程</span><span data-map-progress-value></span></div><div class="loc-map-play-progress"><i></i></div>${model.kind === 'regional' ? `<div class="loc-city-journeys">${scheduled.map(({node, trip}) => `<div><time>${escape(node.enter)}–${escape(node.exit)}</time><strong>${escape(mapPlaces[trip.from].label)} → ${escape(mapPlaces[trip.to].label)}</strong><span>${trip.end - trip.start} 分钟</span></div>`).join('')}</div>` : ''}</div>`;
    const svg = $('.loc-campus-svg'), world = $('[data-map-world]'), avatar = $('.loc-avatar');
    const label = $('.loc-avatar-label text'), routePath = $('.loc-path-route'), pathDone = $('.loc-path-done');
    const play = $('[data-map-play]'), liveButton = $('[data-map-live]');
    let pathKey = '', pathLength = 0, lastStatus = '', lastRefresh = -Infinity;
    function setSelected(id, userInitiated = false) {
      selected = model.resolve(id);
      if (!mapped.includes(selected)) selected = null;
      element.querySelectorAll('[data-map-place]').forEach(pin => { pin.classList.toggle('is-selected', pin.dataset.mapPlace === selected); pin.setAttribute('aria-pressed', String(pin.dataset.mapPlace === selected)); });
      if (selected) onSelect?.(selected, mapPlaces[selected], userInitiated);
    }
    element.querySelectorAll('[data-map-place]').forEach(pin => {
      const select = () => { if (!dragging) setSelected(pin.dataset.mapPlace, true); };
      pin.addEventListener('click', select);
      pin.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(pin.dataset.mapPlace, true); } });
    });
    function transform() {
      pan[0] = Math.min(0, Math.max(1000 * (1 - zoom), pan[0])); pan[1] = Math.min(0, Math.max(760 * (1 - zoom), pan[1]));
      world.setAttribute('transform', `translate(${pan[0]} ${pan[1]}) scale(${zoom})`);
      $('[data-map-zoom="-1"]').disabled = zoom <= 1; $('[data-map-zoom="1"]').disabled = zoom >= 2.5;
      const km = model.scaleKm / zoom;
      $('.loc-map-scale text').textContent = model.kind === 'regional' ? (km >= 1 ? `约 ${Math.round(km)} 千米` : `约 ${Math.max(10, Math.round(km * 1000 / 10) * 10)} 米`) : `约 ${Math.round(50 / zoom)} 米`;
    }
    element.querySelectorAll('[data-map-zoom]').forEach(button => button.onclick = () => {
      const next = Math.min(2.5, Math.max(1, zoom + Number(button.dataset.mapZoom) * .5)), factor = next / zoom;
      pan = [500 - (500 - pan[0]) * factor, 380 - (380 - pan[1]) * factor]; zoom = next; transform();
    });
    $('[data-map-reset]').onclick = () => { zoom = 1; pan = [0, 0]; transform(); };
    svg.addEventListener('pointerdown', event => {
      if (zoom <= 1 || event.button !== 0) return;
      const rect = svg.getBoundingClientRect();
      drag = {x: event.clientX, y: event.clientY, pan: [...pan], scale: 1000 / rect.width}; dragging = false;
    });
    svg.addEventListener('pointermove', event => {
      if (!drag) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (Math.hypot(dx, dy) > 5) { dragging = true; svg.setPointerCapture(event.pointerId); }
      if (dragging) { pan = [drag.pan[0] + dx * drag.scale, drag.pan[1] + dy * drag.scale]; transform(); }
    });
    svg.addEventListener('pointerup', () => { drag = null; });
    svg.addEventListener('pointercancel', () => { drag = null; dragging = false; });
    svg.addEventListener('click', () => { dragging = false; });
    function resetPreview() { preview = null; play.textContent = '▶ 播放'; liveButton.hidden = true; lastStatus = ''; lastRefresh = -Infinity; }
    liveButton.onclick = resetPreview;
    $('[data-map-route]').onchange = resetPreview;
    play.onclick = () => {
      if (preview && !preview.finished) {
        if (preview.paused) { preview.started = performance.now() - preview.elapsed; preview.paused = false; play.textContent = 'Ⅱ 暂停'; }
        else { preview.elapsed = performance.now() - preview.started; preview.paused = true; play.textContent = '▶ 继续'; }
      } else {
        const route = options[Number($('[data-map-route]').value)];
        if (!route) return;
        preview = {...route, started: performance.now(), elapsed: 0, paused: false, finished: false};
        play.textContent = 'Ⅱ 暂停'; liveButton.hidden = false;
      }
      lastStatus = '';
    };
    function frame(now) {
      if (disposed || !element.isConnected) return;
      raf = requestAnimationFrame(frame);
      if (document.hidden) { lastRefresh = now; return; }
      if (now - lastRefresh < (reducedMotion.matches || (!preview && current.status !== 'in_transit') ? 1000 : 30)) return;
      lastRefresh = now;
      const nowClock = clock(), isToday = nowClock.date === date;
      const trip = preview || (isToday ? journey(current, timeline.nodes, config, persona, model) : null);
      const liveId = isToday && current.status === 'stationary' ? model.resolve(current) : null;
      let fraction = 0, point = liveId ? mapPlaces[liveId].point : null, walking = false, caption = '', status = '';
      if (trip) {
        if (preview) {
          if (!preview.paused && !preview.finished) preview.elapsed = now - preview.started;
          fraction = Math.min(1, preview.elapsed / 12000);
          if (fraction >= 1 && !preview.finished) { preview.finished = true; play.textContent = '↻ 重播'; }
          walking = !preview.paused && !preview.finished;
          status = preview.finished ? '预览已到达' : preview.paused ? '预览已暂停' : '正在预览';
          caption = `${mapPlaces[trip.from].label} → ${mapPlaces[trip.to].label} · ${trip.mode || '步行'}${trip.minutes ? ` ${trip.minutes} 分钟` : ''}（12 秒演示）`;
        } else {
          fraction = Math.min(1, Math.max(0, (nowClock.minute - trip.start) / (trip.end - trip.start)));
          walking = fraction < 1;
          status = '正在路上'; caption = `${mapPlaces[trip.from].label} → ${mapPlaces[trip.to].label} · 预计 ${current.eta || ''} 到达`;
        }
        const key = `${trip.from}/${trip.to}`;
        if (key !== pathKey) {
          pathKey = key;
          const d = 'M' + trip.points.map(p => p.join(',')).join(' L');
          for (const path of element.querySelectorAll('.loc-path-route,.loc-path-done,.loc-path-underlay')) path.setAttribute('d', d);
          pathLength = routePath.getTotalLength();
        }
        const p = routePath.getPointAtLength(fraction * pathLength); point = [p.x, p.y];
        pathDone.style.strokeDasharray = `${fraction * pathLength} ${pathLength + 1}`;
      } else {
        status = !isToday ? '计划位置' : current.status === 'in_transit' ? '路程待定位' : liveId ? (model.agent ? current.precision === 'city' ? '所在城市' : '此刻在这里' : model.kind === 'regional' ? '所在城市' : '此刻在这里') : '位置待确认';
        caption = !isToday ? '点击地图上的地点，查看当天活动' : liveId ? `${name}在${model.kind === 'regional' ? mapPlaces[liveId].label : current.location} · ${current.activity || '停留中'}` : current.status === 'in_transit' ? '当前路程的起终点尚未定位' : '当前地点还无法标在地图上';
      }
      for (const path of element.querySelectorAll('.loc-path-route,.loc-path-done,.loc-path-underlay')) path.style.display = trip ? '' : 'none';
      avatar.setAttribute('visibility', point ? 'visible' : 'hidden');
      if (point) avatar.setAttribute('transform', `translate(${point[0].toFixed(2)} ${point[1].toFixed(2)})`);
      const isWalk = !trip?.mode || /步行|走/.test(trip.mode);
      $('.loc-avatar-person').style.display = trip && !isWalk ? 'none' : '';
      $('.loc-vehicle').setAttribute('display', trip && !isWalk ? '' : 'none');
      avatar.classList.toggle('is-walking', walking && isWalk && !reducedMotion.matches);
      label.textContent = `${name}${preview ? ' · 预览' : trip ? ' · 途中' : ''}`;
      element.querySelectorAll('[data-map-place]').forEach(pin => pin.classList.toggle('has-person', pin.dataset.mapPlace === liveId && !trip));
      const statusKey = `${status}/${caption}`;
      if (lastStatus !== statusKey) {
        lastStatus = statusKey;
        $('[data-map-mode]').textContent = status; $('.loc-map-mode').classList.toggle('is-preview', Boolean(preview));
        $('[data-map-caption]').textContent = caption;
        $('[data-map-progress-label]').textContent = preview ? (preview.finished ? '预览完成 · 未改变实际位置' : '动画预览 · 未改变实际位置') : trip ? (model.kind === 'regional' ? '时间进度示意 · 非实时轨迹' : '按出发与到达时间显示进度') : '动画预览独立于真实日程';
      }
      $('[data-map-progress-value]').textContent = trip ? `${Math.round(fraction * 100)}%` : '';
      $('.loc-map-play-progress i').style.width = `${trip ? fraction * 100 : 0}%`;
    }
    function motionChanged() { lastRefresh = -Infinity; }
    reducedMotion.addEventListener('change', motionChanged);
    transform(); raf = requestAnimationFrame(frame);
    return {
      select: setSelected,
      update(next) { current = next; lastStatus = ''; lastRefresh = -Infinity; },
      destroy() { disposed = true; cancelAnimationFrame(raf); reducedMotion.removeEventListener('change', motionChanged); }
    };
  }
  window.LocationMap = {create, describe, resolve, routePoints, journey, places};
})();
