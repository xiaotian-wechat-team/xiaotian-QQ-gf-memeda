window.PortraitExhibitions = (() => {
  const titles = {
    wall:'美术馆长卷', editorial:'杂志拼版', table:'摄影师的选片桌',
    shelves:'双层画架', fold:'折屏展廊', lightbox:'摄影灯箱', studio:'粉白相纸展架',
  };
  const openingStars = {
    wall:['R18','R29','R07','R23'], editorial:['R22','R29','R23','R09'],
    table:['R18','R11','R25','R01'], shelves:['R07','R30','R20','R11'],
    fold:['R23','R29','R14','R03'], lightbox:['R27','R01','R24','R19'],
    studio:['R07','R29','R18','R11','R02','R30'],
  };
  const openingAccents = {
    wall:[], editorial:['F07','R12','F14','C02'], table:['F04','C01','F10','C03'],
    shelves:['F14','R12','C04','F04'], fold:['F02','F05','F04','C02'],
    studio:['C06','F14','C05','R12','F04','C01'],
  };
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const wrap = (value, length) => (value % length + length) % length;

  function element(className) {
    const node = document.createElement('div');
    node.className = className;
    return node;
  }

  function print([id, name, subject, facing], eager = true) {
    const frame = element('exhibit-print');
    frame.dataset.subject = subject;
    frame.dataset.portrait = id;
    const image = new Image();
    image.alt = name;
    image.draggable = false;
    image.decoding = eager ? 'auto' : 'async';
    // Warm the local catalog at low priority so a fast drag can reveal any frame immediately.
    if (!eager) image.fetchPriority = 'low';
    if (facing === 'left') image.className = 'gallery-mirrored';
    if (HomePortraits.credits[id]) image.title = HomePortraits.credits[id];
    image.src = HomePortraits.imageUrl(id);
    frame.append(image);
    return frame;
  }

  function opening(prints, ids) {
    return [...ids.map(id => prints.find(item => item[0] === id)).filter(Boolean),
      ...prints.filter(item => !ids.includes(item[0]))];
  }

  function triptychs(stars, guests, accentIds) {
    const count = Math.ceil((stars.length + guests.length) / 3);
    const smallStars = stars.slice(count);
    const accents = guests.flatMap((guest, index) => smallStars[index] ? [guest, smallStars[index]] : [guest]);
    const small = opening(accents.concat(smallStars.slice(guests.length)), accentIds);
    const leads = stars.slice(0, count);
    // Spread the smaller prints evenly so a partial final group also has a companion.
    const boundary = i => Math.ceil(i * small.length / leads.length);
    return leads.map((star, i) => [star, ...small.slice(boundary(i), boundary(i + 1))]);
  }

  function control(action, label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.control = action;
    button.setAttribute('aria-label', label);
    button.title = label;
    return button;
  }

  function playback(stage, onChange) {
    const bar = element('exhibit-controls');
    const button = control('motion', '暂停自动播放');
    bar.append(button);
    stage.append(bar);
    let paused = false;
    function sync() {
      const playing = !paused && !reduced.matches && !document.hidden;
      stage.dataset.paused = String(!playing);
      button.setAttribute('aria-pressed', String(paused));
      button.title = paused ? '继续自动播放' : '暂停自动播放';
      button.setAttribute('aria-label', button.title);
      onChange(playing);
    }
    button.onclick = () => { paused = !paused; sync(); };
    document.addEventListener('visibilitychange', sync);
    reduced.addEventListener('change', sync);
    window.addEventListener('pageshow', sync);
    window.addEventListener('pagehide', () => onChange(false));
    sync();
  }

  function duplicate(run) {
    const copy = run.cloneNode(true);
    copy.setAttribute('aria-hidden', 'true');
    copy.querySelectorAll('img').forEach(img => { img.alt = ''; img.fetchPriority = 'low'; });
    return copy;
  }

  function wall(stage, groups) {
    const viewport = element('salon-viewport');
    viewport.setAttribute('aria-label', '横向展墙');
    const track = element('salon-track');
    const run = element('salon-run');
    groups.forEach((group, index) => {
      const bay = element('salon-bay');
      group.forEach(portrait => bay.append(print(portrait, index < 3)));
      run.append(bay);
    });
    track.style.setProperty('--passage', groups.length * 19 + 's');
    track.append(run, duplicate(run));
    viewport.append(track);
    stage.append(viewport);
    playback(stage, () => { viewport.tabIndex = reduced.matches ? 0 : -1; });
  }

  // Each track contains two equal runs. Scrubbing the same timeline keeps the loop seamless.
  function reel(viewport, run, speed, initialPhase = 0) {
    const track = element('exhibit-reel');
    track.append(run, duplicate(run));
    viewport.append(track);
    viewport.tabIndex = 0;
    viewport.setAttribute('role', 'group');
    let duration = Math.max(1, run.getBoundingClientRect().width) * 1000 / speed;
    const film = track.animate([
      {transform:'translate3d(0,0,0)'}, {transform:'translate3d(-50%,0,0)'},
    ], {duration, iterations:Infinity, easing:'linear'});
    film.pause();
    film.currentTime = duration * initialPhase;
    let playing = false;
    let drag = null;
    function sync() {
      if (playing && !drag) film.play();
      else film.pause();
    }
    function scrub(pixels) {
      film.currentTime = wrap((film.currentTime || 0) + pixels * 1000 / speed, duration);
    }
    new ResizeObserver(() => {
      const newDuration = Math.max(1, run.getBoundingClientRect().width) * 1000 / speed;
      if (Math.abs(newDuration - duration) < 1) return;
      const phase = wrap(film.currentTime || 0, duration) / duration;
      duration = newDuration;
      film.effect.updateTiming({duration});
      film.currentTime = phase * duration;
    }).observe(run);
    viewport.onpointerdown = event => {
      if (!event.isPrimary || event.button !== 0) return;
      drag = {id:event.pointerId, x:event.clientX};
      viewport.setPointerCapture(event.pointerId);
      sync();
    };
    viewport.onpointermove = event => {
      if (drag?.id !== event.pointerId) return;
      scrub(drag.x - event.clientX);
      drag.x = event.clientX;
    };
    viewport.onlostpointercapture = () => { drag = null; sync(); };
    viewport.addEventListener('wheel', event => {
      const dx = event.deltaX || (event.shiftKey ? event.deltaY : 0);
      if (!dx || event.ctrlKey || (!event.shiftKey && Math.abs(dx) < Math.abs(event.deltaY))) return;
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientWidth : 1;
      scrub(dx * unit);
    }, {passive:false});
    viewport.onkeydown = event => {
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
      event.preventDefault();
      scrub(viewport.clientWidth * (event.key === 'ArrowRight' ? .7 : -.7));
    };
    return enabled => { playing = enabled; sync(); };
  }

  function gallery(stage, groups, mode) {
    const viewport = element('exhibit-viewport');
    viewport.setAttribute('aria-label', titles[mode] + '，左右拖动或使用方向键浏览');
    stage.append(viewport);
    const run = element('exhibit-run');
    groups.forEach((group, index) => {
      const bay = element('exhibit-bay');
      const [main, ...small] = group.map(person => print(person, index < 3));
      main.classList.add('exhibit-hero');
      if (mode === 'fold') {
        const front = element('screen-leaf screen-front'), side = element('screen-leaf screen-side');
        front.append(main);
        side.append(...small);
        bay.append(front, side);
      } else {
        bay.append(main, ...small);
      }
      run.append(bay);
    });
    const speed = {editorial:35, table:28, shelves:31, fold:29, studio:29};
    playback(stage, reel(viewport, run, speed[mode]));
  }

  function lightbox(stage, stars, guests) {
    const room = element('lightbox-room');
    const top = element('film-window film-top'), bottom = element('film-window film-bottom');
    const focus = element('lightbox-focus');
    top.setAttribute('aria-label', '上方胶片，左右拖动或使用方向键浏览');
    bottom.setAttribute('aria-label', '下方胶片，左右拖动或使用方向键浏览');
    focus.setAttribute('role', 'group');
    focus.setAttribute('aria-label', '主灯箱，左右方向键切换明星肖像');
    focus.tabIndex = 0;
    room.append(top, focus, bottom);
    stage.append(room);

    const stock = stars.flatMap((star, index) => guests[index] ? [star, guests[index]] : [star]);
    const rows = [element('exhibit-run film-run'), element('exhibit-run film-run')];
    // Deal pairs of prints across the two strips, so each has both stars and characters.
    stock.forEach((person, index) => rows[Math.floor(index / 2) % 2].append(print(person, index < 36)));
    const topPlay = reel(top, rows[0], 23, .12);
    const bottomPlay = reel(bottom, rows[1], 23, .2);

    const prev = control('prev', '上一位明星'), next = control('next', '下一位明星');
    prev.className = next.className = 'lightbox-step';
    focus.append(prev, next);
    let current = 0, timer, playing = false;
    function show(index, dissolve) {
      const spread = element('lightbox-spread');
      const lead = print(stars[index]);
      lead.classList.add('exhibit-hero');
      spread.append(print(guests[(index * 2) % guests.length]), lead,
        print(guests[(index * 2 + 1) % guests.length]));
      const old = focus.querySelector('.lightbox-spread:last-of-type');
      focus.querySelectorAll('.lightbox-spread').forEach(node => { if (node !== old) node.remove(); });
      focus.append(spread);
      if (old && dissolve && !reduced.matches) {
        old.setAttribute('aria-hidden', 'true');
        const fade = spread.animate([{opacity:0}, {opacity:1}], {duration:720, easing:'ease-in-out'});
        fade.onfinish = () => old.remove();
      } else old?.remove();
    }
    function schedule() {
      clearTimeout(timer);
      if (playing && stage.isConnected) timer = setTimeout(() => step(1), 6000);
    }
    function step(direction) {
      current = wrap(current + direction, stars.length);
      show(current, true);
      schedule();
    }
    prev.onclick = () => step(-1);
    next.onclick = () => step(1);
    focus.onkeydown = event => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      step(event.key === 'ArrowRight' ? 1 : -1);
    };
    let startX;
    focus.onpointerdown = event => {
      if (!event.isPrimary || event.button !== 0 || event.target.closest('button')) return;
      startX = event.clientX;
      focus.setPointerCapture(event.pointerId);
    };
    focus.onpointerup = event => {
      if (startX != null && Math.abs(event.clientX - startX) > 30) step(event.clientX < startX ? 1 : -1);
      startX = null;
    };
    focus.onpointercancel = () => { startX = null; };
    show(current, false);
    playback(stage, enabled => {
      playing = enabled;
      topPlay(enabled);
      bottomPlay(enabled);
      schedule();
    });
  }

  function mount(stage, mode, portraits) {
    const stars = opening(portraits.filter(([, , kind]) => kind === 'celebrity'), openingStars[mode]);
    const guests = portraits.filter(([, , kind]) => kind !== 'celebrity');
    stage.setAttribute('aria-label', titles[mode]);
    if (mode === 'lightbox') { lightbox(stage, stars, guests); return; }
    const groups = triptychs(stars, guests, openingAccents[mode]);
    if (mode === 'wall') wall(stage, groups);
    else gallery(stage, groups, mode);
  }
  return {titles, mount};
})();
