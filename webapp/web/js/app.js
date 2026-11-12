/* Lab web app (GitHub Pages). Talks only to the Apps Script server in config.js.
 * All data on screen is set with textContent (never parsed as HTML). */
(function () {
  'use strict';

  var API = (window.LAB_CONFIG || {}).apiUrl || '';
  var DAYS = ['일', '월', '화', '수', '목', '금', '토'];
  var KEEP = 400 * 24 * 3600; // cookie life (seconds), refreshed on every visit

  // ---------- icons (static markup only) ----------
  var I = {
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    cal: '<rect x="4" y="5.5" width="16" height="14.5" rx="2.5"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>',
    scope: '<path d="M9 4h4l1 6h-6z"/><path d="M11 10v4M7 20h10M9 20c0-3 1-5 3-6M14 14a4 4 0 0 1 3 4"/>',
    check: '<rect x="4.5" y="4.5" width="15" height="15" rx="3"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    flask: '<path d="M9.5 3.5h5M10 3.5v6L5 18.5a1.5 1.5 0 0 0 1.3 2h11.4a1.5 1.5 0 0 0 1.3-2L14 9.5v-6"/><path d="M7.5 15h9"/>',
    mega: '<path d="M4 10v4h3l7 4V6l-7 4z"/><path d="M17.5 9.5a3.5 3.5 0 0 1 0 5"/>',
    cart: '<path d="M3.5 5h2l2 10h10l2-7H7"/><circle cx="9" cy="19" r="1.3"/><circle cx="16.5" cy="19" r="1.3"/>',
    won: '<rect x="3.5" y="6" width="17" height="12" rx="2.5"/><circle cx="12" cy="12" r="2.5"/><path d="M6.5 9v6M17.5 9v6"/>',
    nfc: '<path d="M7 8a6 6 0 0 1 0 8M10 6a9 9 0 0 1 0 12M13 4a12 12 0 0 1 0 16"/>',
    person: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    refresh: '<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4.5v4h-4"/>',
    lock: '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    back: '<path d="M15 5l-7 7 7 7"/>',
    bigcheck: '<circle cx="12" cy="12" r="10"/><path d="m7.5 12.3 3 3 6-6.3"/>',
    bigwarn: '<circle cx="12" cy="12" r="10"/><path d="M12 7v6M12 16.5v.5"/>',
    chevr: '<path d="m9 5 7 7-7 7"/>',
    chevl: '<path d="m15 5-7 7 7 7"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M4 7h16M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2M6 7l.8 12a1.6 1.6 0 0 0 1.6 1.5h7.2a1.6 1.6 0 0 0 1.6-1.5L18 7"/><path d="M10 11v6M14 11v6"/>',
    tick: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>',
    send: '<path d="M12 19V6M6.5 11.5 12 6l5.5 5.5"/>'
  };
  function icon(name, cls) {
    var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('class', cls || 'hig-icon');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('aria-hidden', 'true');
    s.innerHTML = I[name];
    return s;
  }
  function chevron() {
    var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('class', 'hig-list__chevron');
    s.setAttribute('viewBox', '0 0 8 14');
    s.setAttribute('aria-hidden', 'true');
    s.innerHTML = '<path d="M1 1l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';
    return s;
  }

  // ---------- DOM helper: el('div.a.b', {attr}, children...) ----------
  function el(spec, attrs) {
    var parts = spec.split('.');
    var node = document.createElement(parts[0] || 'div');
    if (parts.length > 1) node.className = parts.slice(1).join(' ');
    var kids = [].slice.call(arguments, 2);
    if (attrs && (typeof attrs !== 'object' || attrs.nodeType || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
    for (var k in attrs || {}) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k.slice(0, 2) === 'on') node.addEventListener(k.slice(2), v);
      else if (k === 'style') node.setAttribute('style', v);
      else if (k === 'value') node.value = v;
      else if (k === 'checked') node.checked = !!v;
      else node.setAttribute(k, v === true ? '' : v);
    }
    (function add(list) {
      list.forEach(function (c) {
        if (c === null || c === undefined || c === false) return;
        if (Array.isArray(c)) add(c);
        else node.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
      });
    })(kids);
    return node;
  }

  // ---------- storage: cookie + localStorage, never expires while used ----------
  function cookieGet(k) {
    var m = document.cookie.match(new RegExp('(?:^|; )' + k + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
  }
  function store(k, v) {
    if (v === undefined) {
      var c = cookieGet(k), l = '';
      try { l = localStorage.getItem(k) || ''; } catch (e) { /* private mode */ }
      return c || l;
    }
    var secure = location.protocol === 'https:' ? '; Secure' : '';
    if (v === null) {
      document.cookie = k + '=; Max-Age=0; Path=/; SameSite=Lax' + secure;
      try { localStorage.removeItem(k); } catch (e) { /* ignore */ }
      return '';
    }
    document.cookie = k + '=' + encodeURIComponent(v) + '; Max-Age=' + KEEP + '; Path=/; SameSite=Lax' + secure;
    try { localStorage.setItem(k, v); } catch (e) { /* ignore */ }
    return v;
  }
  function deviceId() {
    var d = store('lab_device');
    if (!/^[A-Za-z0-9_-]{8,64}$/.test(d)) {
      var b = new Uint8Array(12);
      crypto.getRandomValues(b);
      d = 'd' + Array.prototype.map.call(b, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
    }
    return store('lab_device', d);
  }
  function refreshStore() {
    ['lab_device', 'lab_key', 'lab_member'].forEach(function (k) { var v = store(k); if (v) store(k, v); });
  }

  // ---------- server ----------
  function api(action, data) {
    var body = Object.assign({ action: action, device: deviceId(), key: store('lab_key'), member: store('lab_member') }, data || {});
    if (!API) return Promise.reject(new Error('서버 주소가 아직 없어요 (config.js).'));
    return fetch(API, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'text/plain;charset=utf-8' } })
      .then(function (r) { return r.json(); }, function () { throw new Error('인터넷 연결을 확인해 주세요.'); })
      .then(function (res) {
        if (res.ok) { afterWrite(action); return res; }
        if (res.code === 'need_pin') { store('lab_key', null); boot(); }
        else if (res.code === 'need_member') { store('lab_member', null); boot(); }
        var e = new Error(res.error || '문제가 생겼어요.');
        e.code = res.code; e.owner = res.owner;
        throw e;
      });
  }
  /** Writes that may touch someone else's item: ask once, then send again with confirm. */
  function write(action, data, verb) {
    return api(action, data).catch(function (e) {
      if (e.code !== 'confirm') throw e;
      return ask(e.owner + '님의 항목이에요', '그래도 ' + (verb || '바꿀') + '까요? 누가 바꿨는지 기록에 남아요.', verb === '취소할' || verb === '지울' ? '확인' : '바꾸기', verb === '취소할' || verb === '지울')
        .then(function (yes) {
          if (!yes) { var x = new Error(''); x.code = 'cancelled'; throw x; }
          return api(action, Object.assign({ confirm: true }, data));
        });
    });
  }

  // ---------- saved data: read once, show at once, refresh on pull / after 5 min / after my edits ----------
  var CACHE_KEY = 'lab_cache_v1';
  var STALE_MS = 5 * 60 * 1000;
  var READS = ['home', 'attendance.month', 'schedule.range', 'equipment.list', 'reservation.list', 'work.board',
               'request.list', 'notice.list', 'notice.get', 'order.list', 'budget.lab', 'budget.mine'];
  // a write → the saved answers it can change (dropped, so the screen asks the server again)
  var TOUCHES = { reservation: ['reservation.', 'home', 'schedule.'], notice: ['notice.', 'home'], order: ['order.', 'home'],
                  tap: ['home', 'attendance.'], request: ['request.', 'home'] };
  var saved = readSaved();
  var tracking = null;   // keys a screen's fetcher asked for
  var bypass = false;    // true while a refresh is fetching (skip the saved answers)
  function readSaved() {
    try {
      var c = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
      if (c && c.member === store('lab_member') && c.items) return c.items;
    } catch (e) { /* private mode or broken: start empty */ }
    return {};
  }
  var saveTimer = null;
  function writeSaved() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      try { localStorage.setItem(CACHE_KEY, JSON.stringify({ member: store('lab_member'), items: saved })); } catch (e) { /* full: memory only */ }
    }, 300);
  }
  function ckey(action, data) { return action + ' ' + JSON.stringify(data || {}); }
  function putSaved(k, data) { saved[k] = { t: Date.now(), d: data }; writeSaved(); }
  function dropSaved(prefixes) {
    Object.keys(saved).forEach(function (k) { if (!prefixes || prefixes.some(function (p) { return k.indexOf(p) === 0; })) delete saved[k]; });
    writeSaved();
  }
  function afterWrite(action) {
    if (READS.indexOf(action) >= 0 || action === 'members' || action === 'batch' || action === 'enter') return;
    if (action === 'pick' || action === 'cache.clear') return dropSaved(null);
    var t = TOUCHES[action.split('.')[0]];
    if (t) dropSaved(t);
  }
  /** A read through the saved answers. Same answer as api() but instant when it was read before. */
  function cached(action, data) {
    var k = ckey(action, data);
    if (tracking) tracking.push(k);
    var hit = saved[k];
    if (hit && !bypass) return Promise.resolve(hit.d);
    return api(action, data).then(function (r) {
      var before = saved[k];
      putSaved(k, r);
      if (action === 'notice.get' && !(before && before.d.notice && before.d.notice.readers.indexOf(store('lab_member')) >= 0)) markNoticeRead(data.id);
      return r;
    });
  }
  /** Opening a notice marks it read on the server: mirror that in the saved lists instead of asking again. */
  function markNoticeRead(id) {
    Object.keys(saved).forEach(function (k) {
      var d = saved[k].d;
      if (k.indexOf('notice.list') === 0) (d.notices || []).forEach(function (n) { if (n.id === id) n.read = true; });
      if (k.indexOf('home') === 0 && d.notices) {
        var was = (d.notices.latest || []).concat([]).filter(function (n) { return n.id === id && !n.read; }).length;
        (d.notices.latest || []).forEach(function (n) { if (n.id === id) n.read = true; });
        if (was && d.notices.unread) d.notices.unread--;
      }
    });
    writeSaved();
  }
  function oldest(keys) {
    var t = Infinity;
    keys.forEach(function (k) { if (saved[k] && saved[k].t < t) t = saved[k].t; });
    return t === Infinity ? Date.now() : t;
  }
  function fetchedText(t) {
    var d = new Date(t), now = new Date();
    var h = d.getHours(), when = (h < 12 ? '오전 ' : '오후 ') + ((h + 11) % 12 + 1) + ':' + pad(d.getMinutes());
    var day = ymdOf(d) === ymdOf(now) ? '' : ymdOf(d) === addDays(ymdOf(now), -1) ? '어제 ' : (d.getMonth() + 1) + '월 ' + d.getDate() + '일 ';
    return day + when + '에 받음';
  }
  /** What the app reads when it opens. home (the first screen) comes first; the rest follow in one call.
   * Keys match the screens' own reads, so a tab opened later shows at once. */
  function openingCalls() {
    var t = todayYmd(), mon = addDays(t, -((dateOf(t).getDay() + 6) % 7));
    return [['home'], ['schedule.range', { from: mon, to: addDays(mon, 6) }],
      ['reservation.list', { from: t, to: t }], ['reservation.list', { from: t, to: addDays(t, 30) }], ['work.board'],
      ['request.list'], ['notice.list'], ['order.list'], ['budget.lab', { limit: 8 }]];
  }
  function readAll(calls) {
    calls = calls || openingCalls();
    return api('batch', { calls: calls.map(function (c) { return { action: c[0], data: c[1] || {} }; }) }).then(function (r) {
      r.results.forEach(function (x, i) { if (x && x.ok) putSaved(ckey(calls[i][0], calls[i][1]), x); });
    }, function (e) {
      if (e.code !== 'bad_request') throw e;
      // a server that has no batch yet: the same reads one by one, side by side
      return Promise.all(calls.map(function (c) {
        return api(c[0], c[1]).then(function (x) { putSaved(ckey(c[0], c[1]), x); }, function () {});
      }));
    });
  }

  // ---------- dates ----------
  function pad(n) { return ('0' + n).slice(-2); }
  function ymdOf(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function todayYmd() { return ymdOf(new Date()); }
  function dateOf(ymd) { var p = ymd.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function addDays(ymd, n) { var d = dateOf(ymd); d.setDate(d.getDate() + n); return ymdOf(d); }
  function dow(ymd) { return DAYS[dateOf(ymd).getDay()]; }
  function longDay(ymd) { var d = dateOf(ymd); return (d.getMonth() + 1) + '월 ' + d.getDate() + '일 ' + DAYS[d.getDay()] + '요일'; }
  function shortDay(ymd) { var d = dateOf(ymd); return (d.getMonth() + 1) + '월 ' + d.getDate() + '일 (' + DAYS[d.getDay()] + ')'; }
  function nowStamp() { var d = new Date(); return ymdOf(d) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function hm(stamp) { return String(stamp || '').slice(11, 16); }
  function won(n) { return n === null || n === undefined || isNaN(n) ? '' : Number(n).toLocaleString('ko-KR') + '원'; }

  // ---------- pieces ----------
  var app = document.getElementById('app');
  var state = { route: '', refresh: null, busy: false };

  function status(kind, text, plain) {
    return el('span.hig-status.hig-status--' + kind + (plain ? '.hig-status--plain' : ''), el('span.hig-status__dot'), text);
  }
  function sectionHeader(text, trail) { return el('div.hig-section-header.sh', text, trail ? el('span', trail) : null); }
  function footnote(text) { return el('div.hig-section-footer.foot', text); }
  function iconButton(name, label, onclick) {
    return el('button.hig-button.hig-button--icon.hig-button--plain', { 'aria-label': label, onclick: onclick }, icon(name));
  }
  function textButton(text, onclick, strong) {
    return el('button.hig-button.hig-button--plain.txtbtn', { onclick: onclick, style: strong ? 'font-weight:600' : null }, text);
  }
  function navbar(title, lead, trail) {
    return el('nav.hig-navbar.hig-glass.topbar', el('div.hig-navbar__leading', lead), el('div.hig-navbar__title', title),
      el('div.hig-navbar__trailing', trail));
  }
  function backButton(to) { return iconButton('back', '뒤로', function () { go(to); }); }
  function row(o) {
    var kids = [];
    if (o.icon) kids.push(el('span.tile', { style: 'background:' + (o.color || 'var(--accent)') }, icon(o.icon)));
    if (o.lead) kids.push(o.lead);
    kids.push(el('span.hig-list__text', el('span.hig-list__title' + (o.accent ? '.acc' : '') + (o.danger ? '.danger' : ''), o.title),
      o.sub ? el('span.hig-list__subtitle' + (o.wrap ? '.wrap-sub' : ''), o.sub) : null));
    if (o.detail !== undefined && o.detail !== '') kids.push(o.detail && o.detail.nodeType ? o.detail : el('span.hig-list__detail.num', o.detail));
    if (o.trail) kids.push(o.trail);
    if (o.onclick) kids.push(chevron());
    return el('li.hig-list__row', { 'data-interactive': o.onclick ? true : null, onclick: o.onclick,
      style: o.sub && o.rich !== false ? 'min-height:var(--hig-row-rich)' : null }, kids);
  }
  function list(rows) { return el('ul.hig-list.m', rows); }
  function chips(options, value, onpick) {
    return el('div.hig-filterbar', options.map(function (o) {
      return el('button.hig-chip.hig-chip--choice', { 'aria-pressed': o[0] === value ? 'true' : 'false', onclick: function () { onpick(o[0]); } }, o[1]);
    }));
  }
  function segmented(options, value, onpick) {
    return el('div.seg-wrap', el('div.hig-segmented.hig-segmented--full', { role: 'radiogroup' }, options.map(function (o) {
      return el('button.hig-segmented__item', { role: 'radio', 'aria-checked': o[0] === value ? 'true' : 'false', onclick: function () { onpick(o[0]); } }, o[1]);
    })));
  }
  function formRow(label, control, hint) {
    return el('div.hig-form-row', el('label.hig-form-row__label', label), el('div.hig-form-row__control', control),
      hint ? el('div.hig-form-row__hint', hint) : null);
  }
  function select(options, value) {
    var s = el('select.hig-select__native', options.map(function (o) {
      return el('option', { value: o[0], selected: String(o[0]) === String(value) ? true : null }, o[1]);
    }));
    var wrap = el('div.hig-select', s);
    var chev = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chev.setAttribute('class', 'hig-select__chev'); chev.setAttribute('viewBox', '0 0 24 24');
    chev.innerHTML = '<path d="m7.5 9.5 4.5-4.5 4.5 4.5M7.5 14.5l4.5 4.5 4.5-4.5"/>';
    wrap.appendChild(chev);
    wrap.input = s;
    return wrap;
  }
  function spinner() { return el('div.loading', el('div.hig-spinner')); }
  function toast(text) {
    var t = el('div.toast', { role: 'status' }, text);
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2600);
  }
  function ask(title, message, okText, destructive) {
    return new Promise(function (resolve) {
      var scrim = el('div.hig-scrim');
      function done(v) { scrim.remove(); box.remove(); resolve(v); }
      var box = el('div.hig-alert', { role: 'alertdialog' },
        el('div.hig-alert__main', el('div.hig-alert__body', el('div.hig-alert__title', title), message ? el('div.hig-alert__message', message) : null)),
        el('div.hig-alert__actions',
          el('button.hig-alert__action', { onclick: function () { done(false); } }, '취소'),
          el('button.hig-alert__action' + (destructive ? '.hig-alert__action--destructive' : '.hig-alert__action--default'), { onclick: function () { done(true); } }, okText || '확인')));
      document.body.appendChild(scrim);
      document.body.appendChild(box);
    });
  }
  function tell(title, message) {
    return new Promise(function (resolve) {
      var scrim = el('div.hig-scrim');
      var box = el('div.hig-alert', { role: 'alertdialog' },
        el('div.hig-alert__main', el('div.hig-alert__body', el('div.hig-alert__title', title), message ? el('div.hig-alert__message', message) : null)),
        el('div.hig-alert__actions', el('button.hig-alert__action.hig-alert__action--default', { onclick: function () { scrim.remove(); box.remove(); resolve(); } }, '확인')));
      document.body.appendChild(scrim);
      document.body.appendChild(box);
    });
  }
  function failed(e) { if (e && e.code !== 'cancelled' && e.message) tell('저장하지 못했어요', e.message); }

  /** A bottom sheet with 취소 / title / 저장. save() returns a promise; resolve closes. */
  function sheet(title, body, save, saveText) {
    var scrim = el('div.hig-scrim', { onclick: close });
    var err = el('p.err');
    var saveBtn = textButton(saveText || '저장', function () {
      err.textContent = '';
      saveBtn.disabled = true;
      Promise.resolve().then(save).then(function (ok) { if (ok !== false) close(); })
        .catch(function (e) { if (e && e.code !== 'cancelled') err.textContent = e.message; })
        .then(function () { saveBtn.disabled = false; });
    }, true);
    var box = el('div.hig-sheet.app-sheet', { role: 'dialog', 'aria-label': title }, el('div.hig-sheet__grabber'),
      el('div.sheet-bar', textButton('취소', close), el('span.sheet-t', title), saveBtn), body, err);
    function close() { scrim.remove(); box.remove(); }
    document.body.appendChild(scrim);
    document.body.appendChild(box);
    return { close: close, error: err };
  }

  var TABS = [['home', 'grid', '요약'], ['schedule', 'cal', '일정'], ['res', 'scope', '예약'], ['work', 'check', '업무'], ['lab', 'flask', '연구실']];
  var TAB_OF = { home: 'home', me: 'home', schedule: 'schedule', res: 'res', eq: 'res', work: 'work',
                 lab: 'lab', notices: 'lab', notice: 'lab', orders: 'lab', order: 'lab', budget: 'lab', ledger: 'lab' };
  function tabbar(sel) {
    return el('div.hig-tabbar.hig-glass.tabbar', { role: 'tablist' }, TABS.map(function (t) {
      return el('a.hig-tab', { role: 'tab', href: '#/' + t[0], 'aria-selected': t[0] === sel ? 'true' : 'false' }, icon(t[1], 'hig-icon hig-icon--large'), t[2]);
    }));
  }

  /** Render a screen: parts = [top..., content...]. */
  function screen(parts, opts) {
    opts = opts || {};
    app.textContent = '';
    var view = el('main.view' + (opts.tab === false ? '.notab' : ''), parts);
    app.appendChild(view);
    if (opts.tab !== false) app.appendChild(tabbar(TAB_OF[state.route.split('/')[0]] || 'home'));
    if (!opts.keepScroll) window.scrollTo(0, 0);
    return view;
  }
  /** Load data then render. Saved answers show at once; otherwise a spinner first, an error with retry on failure.
   * state.refresh = 'pull' | 'quiet': ask the server again for this screen's reads only and redraw in place
   * (nothing changes on screen until the new answer is in, and nothing is redrawn when it is the same). */
  function load(top, fetcher, render, opts) {
    opts = opts || {};
    var route = state.route;
    var mode = state.refresh;
    state.refresh = null;
    var keys = [];
    tracking = keys; bypass = !!mode;
    var before = null;
    var p;
    try { p = fetcher(); } finally { tracking = null; bypass = false; }
    if (mode) before = JSON.stringify(keys.map(function (k) { return saved[k] && saved[k].d; }));
    var holder = null;
    if (!mode) {
      holder = el('div', spinner());
      screen([top, holder], opts);
    }
    var done = p.then(function (data) {
      if (state.route !== route) return;
      var cap = keys.length && top ? el('div.fetched', fetchedText(oldest(keys))) : null;
      if (mode) {
        var cur = document.querySelector('main.view .fetched');
        if (cur && cap) cur.textContent = cap.textContent;
        var same = JSON.stringify(keys.map(function (k) { return saved[k] && saved[k].d; })) === before;
        if (same || uiBusy()) return;
        holder = el('div');
        screen([top, holder], Object.assign({}, opts, { keepScroll: true }));
      } else holder.textContent = '';
      holder.appendChild(el('div', cap, render(data)));
      if (!mode && keys.length && Date.now() - oldest(keys) > STALE_MS) setTimeout(function () { refresh('quiet'); }, 0);
    }).catch(function (e) {
      if (state.route !== route || e.code === 'need_pin' || e.code === 'need_member') return;
      if (mode) { if (mode === 'pull') toast(e.message || '새로 받지 못했어요'); return; }
      holder.textContent = '';
      holder.appendChild(el('div', el('p.empty-note', e.message),
        el('div.bottom-act', el('button.hig-button.hig-button--full', { onclick: function () { route_(); } }, '다시 시도'))));
    });
    state.loading = done;
    return done;
  }
  function largeTitle(text, trail) { return el('div.lt-row', el('h1.hig-large-title', text), trail || null); }

  // ---------- first visit: lab password, then the name ----------
  function pinScreen() {
    var input = el('input.hig-input', { type: 'password', autocomplete: 'current-password', inputmode: 'text', 'aria-label': '비밀번호' });
    var err = el('p.err');
    var btn = el('button.hig-button.hig-button--prominent.hig-button--full.big', { type: 'submit' }, '다음');
    var form = el('form.hig-form.regf', { onsubmit: function (ev) {
      ev.preventDefault();
      err.textContent = '';
      btn.disabled = true;
      api('enter', { pin: input.value }).then(function (r) { store('lab_key', r.key); boot(); })
        .catch(function (e) { err.textContent = e.message; input.select(); })
        .then(function () { btn.disabled = false; });
    } }, formRow('비밀번호', input, '연구실 공지나 담당자에게 물어보세요'), err, el('div.bottom-act', btn));
    screen([el('div.reg', el('span.reg-ic', icon('lock')), el('h2.nt', { style: 'text-align:center' }, '연구실 비밀번호'),
      el('p.reg-p', '처음 한 번만 넣으면 이 휴대폰에서는 계속 열려요.')), form], { tab: false });
    setTimeout(function () { input.focus(); }, 50);
  }

  function pickScreen(back) {
    var chosen = '';
    var btn = el('button.hig-button.hig-button--prominent.hig-button--full.big', { disabled: true, onclick: function () {
      btn.disabled = true;
      api('pick', { member: chosen }).then(function () { store('lab_member', chosen); toast(chosen + '님, 반가워요'); boot(); })
        .catch(function (e) { tell('저장하지 못했어요', e.message); btn.disabled = false; });
    } }, '이름을 골라 주세요');
    var ul = el('ul.hig-list.m.pick');
    load(el('div.reg', { style: 'padding-top:28px' }, el('span.reg-ic', icon('nfc')), el('h2.nt', { style: 'text-align:center' }, '누구신가요?'),
      el('p.reg-p', '처음 한 번만 골라 주세요. 이 휴대폰에 저장돼요.')),
      function () { return api('members'); },
      function (r) {
        var cur = store('lab_member');
        r.members.forEach(function (m) {
          var li = el('li.hig-list__row', { 'data-interactive': true, onclick: function () {
            chosen = m.name;
            [].forEach.call(ul.children, function (x) { var c = x.querySelector('.chk'); if (c) c.remove(); });
            li.appendChild(icon('tick', 'hig-icon chk'));
            btn.disabled = false;
            btn.textContent = m.name + '(으)로 시작';
          } }, el('span.hig-list__title', m.name));
          if (m.name === cur) li.appendChild(el('span.hig-list__detail', '지금 이름'));
          ul.appendChild(li);
        });
        return [ul, el('div.bottom-act', btn), back ? el('div.cancel-row', textButton('그대로 두기', function () { go('me'); })) : null];
      }, { tab: false });
  }

  // ---------- a tag tap ----------
  function tapScreen(tag) {
    load(null, function () { return api('tap', tag); }, function (r) {
      var kind = r.result === '기록' ? (r.message.indexOf('지각') >= 0 ? 'warn' : 'ok') : r.result === '이미 출근' || r.result === '중복' ? 'ok' : 'warn';
      return [
        el('div.tap', icon(kind === 'ok' ? 'bigcheck' : 'bigwarn', 'bigok' + (kind === 'warn' ? ' warn' : '')),
          el('div.tap-t', r.result === '기록' ? '출근 기록됨' : r.result === '텍스트 있음' ? '그대로 뒀어요' : '이미 출근했어요'),
          r.time ? el('div.tap-time', r.time) : el('div.tap-time', { style: 'font-size:34px;line-height:44px' }, r.text || ''),
          el('div.tap-who', store('lab_member') + ' · ' + shortDay(r.day)),
          r.result === '기록' ? (kind === 'warn' ? status('warning', '지각') : status('success', '정시 출근')) : null),
        footnote(r.message + ' 같은 날 다시 찍어도 첫 출근 시각이 그대로 남아요.'),
        el('div.bottom-act', el('button.hig-button.hig-button--prominent.hig-button--full.big', { onclick: function () { go('home'); } }, '홈으로'))
      ];
    }, { tab: false }).then(null, null);
  }

  // ---------- 요약 ----------
  function subHead(text, n) { return el('div.sub-h', text, n ? el('small', n + '건') : null); }
  function rangeText(from, to) {
    var a = dateOf(from), b = dateOf(to);
    return (a.getMonth() + 1) + '월 ' + a.getDate() + '일–' + (a.getMonth() === b.getMonth() ? '' : (b.getMonth() + 1) + '월 ') + b.getDate() + '일';
  }
  function whenText(stamp) {
    var d = String(stamp || '').slice(0, 10), t = hm(stamp);
    if (d === todayYmd()) return t || '오늘';
    if (d === addDays(todayYmd(), -1)) return '어제';
    return d ? shortDay(d) : '';
  }
  /** One 업무 요청: who · 업무 · when, with 확인 for a receiver. */
  function askRow(x) {
    var who = x.fromMe && !x.toMe ? '→' : (x.from || '?').charAt(0);
    var meta = x.fromMe && !x.toMe ? x.to.join(', ') + '에게 · ' + x.work + ' · ' + whenText(x.date) : x.from + ' · ' + x.work + ' · ' + whenText(x.date);
    var trail;
    if (x.toMe && !x.acked) {
      trail = el('button.rq-ok', { onclick: function (e) {
        e.stopPropagation();
        var btn = e.currentTarget;
        btn.disabled = true;
        write('request.ack', { id: x.id }).then(function () {
          btn.replaceWith(el('span.rq-st.done', '확인함'));
          toast('확인했어요');
          refresh('quiet');
        }, function (err) { btn.disabled = false; failed(err); });
      } }, '확인');
    } else trail = el('span.rq-st' + (x.acked ? '.done' : ''), x.acked ? '확인함' : '확인 전');
    return el('li.rq-row', x.toMe && !x.acked ? el('span.udot') : null, el('span.rq-av', who),
      el('div.rq-tx', el('div.rq-memo', x.memo), el('div.rq-meta', meta)), trail);
  }
  function homeScreen() {
    load(largeTitle('요약', iconButton('person', '내 정보', function () { go('me'); })), function () { return cached('home'); }, function (r) {
      var a = r.attendance || {};
      var out = [];
      if (a.error) out.push(el('p.empty-note', a.error));
      else {
        var badge = a.mine ? (/^\d\d:\d\d$/.test(a.mine) ? (a.mineLate ? status('warning', '지각') : status('success', '정시 출근')) : status('neutral', a.mine))
          : (a.work ? status('info', '출근 전') : status('neutral', a.reason || '쉬는 날'));
        out.push(el('div.hig-card.today',
          el('div.today-top', el('span.hig-card__eyebrow', longDay(a.today)), badge),
          a.mine ? el('div.big-time' + (/^\d\d:\d\d$/.test(a.mine) ? '' : '.word'), a.mine) : el('div.big-time.word', a.work ? '아직 기록 없음' : (a.reason || '쉬는 날')),
          el('div.today-sub', '지각 기준 ' + a.deadline + ' 이후'),
          el('div.hint', icon('nfc', 'hig-icon hig-icon--small'), a.mine ? '출입문 태그로 기록했어요. 다시 찍어도 바뀌지 않아요.' : '출입문 태그를 휴대폰 윗부분에 대면 기록돼요.')));
        var st = a.stats || {};
        out.push(el('div.stats',
          el('div.stat', el('span.stat-n', String(st.present || 0), el('small', '일')), el('span.stat-l', '이번 달 출근')),
          el('div.stat', el('span.stat-n', String(st.late || 0), el('small', '회')), el('span.stat-l', '지각')),
          el('div.stat', el('span.stat-n', String(a.present), el('small', '/' + a.total)), el('span.stat-l', '오늘 연구실'))));
      }
      var wk = r.week || {};
      out.push(sectionHeader('나의 일', wk.from ? rangeText(wk.from, wk.to) : null));
      function arr(v) { return Array.isArray(v) ? v : []; }
      function err(v) { return v && v.error ? el('p.empty-note', v.error) : null; }

      // this week's duties (overdue first)
      var duties = arr(r.duties);
      out.push(subHead('이번 주 당번', duties.length));
      out.push(err(r.duties) || (duties.length ? list(duties.map(function (t) {
        var when = t.overdue ? shortDay(t.date) + ' · 끝나지 않음' : t.date === todayYmd() ? '오늘 내 차례' : shortDay(t.date);
        return row({ icon: 'check', color: t.overdue ? '#ff3b30' : '#34c759', title: t.project || t.title, danger: t.overdue,
          sub: when + (t.title && t.title !== t.project ? ' · ' + t.title : ''), onclick: function () { go('work'); } });
      })) : el('p.empty-note.sm', '이번 주 당번이 없어요.')));

      // requests to me: 업무 요청 + order requests I handle
      var asks = arr(r.asks), orders = arr(r.requests);
      out.push(subHead('나에게 온 요청', asks.length + orders.length));
      if (asks.length) out.push(el('ul.rq-list', asks.map(askRow)));
      if (orders.length) out.push(list(orders.map(function (o) {
        return row({ icon: 'cart', color: '#ff9500', title: o.item, sub: '주문 요청 · ' + o.requester + ' · ' + whenText(o.date),
          trail: el('span.pill' + (o.state === '주문함' ? '.blue' : ''), o.state), onclick: function () { go('order/' + o.id); } });
      })));
      if (!asks.length && !orders.length) out.push(err(r.asks) || err(r.requests) || el('p.empty-note.sm', '나에게 온 요청이 없어요.'));

      // my schedule this week
      var sch = arr(r.schedule);
      out.push(subHead('이번 주 내 일정', sch.length));
      out.push(err(r.schedule) || (sch.length ? list(sch.map(function (i) {
        var day = i.start.slice(0, 10);
        var time = i.allDay ? (i.end && i.end !== day ? '–' + shortDay(i.end).replace(/ \(.\)$/, '') : '종일') : hm(i.start) + '–' + hm(i.end);
        var extra = i.kind === 'reservation' ? i.purpose : i.calendar;
        return row({ icon: i.kind === 'reservation' ? 'scope' : 'cal', color: i.kind === 'reservation' ? '#0a84ff' : '#5856d6', title: i.title,
          sub: shortDay(day) + ' ' + time + (extra ? ' · ' + extra : ''),
          onclick: function () { go(i.kind === 'reservation' ? 'res/' + day : 'schedule'); } });
      })) : el('p.empty-note.sm', '이번 주 내 일정이 없어요.')));

      // all notices
      var n = r.notices || {};
      out.push(sectionHeader('공지사항', n.unread ? '안 읽음 ' + n.unread : null));
      out.push(n.error ? el('p.empty-note', n.error) : list((n.latest || []).map(function (x) {
        return row({ icon: 'mega', color: '#ff375f', title: x.title, sub: x.author + ' · ' + whenText(x.date),
          trail: x.read ? null : el('span.udot.in'), onclick: function () { go('notice/' + x.id); } });
      }).concat([row({ title: '공지 모두 보기', accent: true, onclick: function () { go('notices'); } })])));
      out.push(el('div', { style: 'height:12px' }));
      return out;
    });
  }

  function meScreen() {
    var me = store('lab_member');
    screen([navbar('내 정보', backButton('home')),
      el('div.me-head', el('span.avatar', me.slice(-2, -1) || me.charAt(0)), el('div', el('div.me-n', me), el('div.me-r', '이 휴대폰에 저장된 이름'))),
      list([row({ title: '기기 ID', detail: deviceId().slice(0, 9) + '…' }),
            row({ title: '로그인', detail: '계속 유지 (로그아웃 없음)' })]),
      sectionHeader('이름 바꾸기'),
      list([row({ title: '다른 이름으로 바꾸기…', accent: true, onclick: function () {
        var at = +(sessionStorage.getItem('tag_at') || 0);
        if (Date.now() - at < 10 * 60 * 1000) pickScreen(true);
        else tell('연구실 태그를 먼저 찍어 주세요', '이름은 태그로 연 화면에서만 바꿀 수 있어요. 태그를 찍고 10분 안에 다시 눌러 주세요.');
      } })]),
      footnote('잘못 골랐거나 휴대폰을 같이 쓰게 됐을 때 써요. 새 휴대폰은 태그를 찍고 비밀번호와 이름을 한 번 넣으면 돼요.'),
      sectionHeader('문제가 있을 때'),
      list([row({ title: '최신 내용 다시 받기', onclick: function () { api('cache.clear').then(function () { dropSaved(null); return readAll(); }).then(function () { toast('새로 받았어요'); }, failed); } })]),
      footnote('아이폰 Safari는 이 사이트를 7일 넘게 열지 않으면 저장된 이름을 지울 수 있어요. 그러면 이름을 다시 한 번 고르면 돼요.')]);
  }

  // ---------- 일정 ----------
  var schedView = { mine: false, day: '' };
  function scheduleScreen() {
    var day = schedView.day || todayYmd();
    var start = addDays(day, -((dateOf(day).getDay() + 6) % 7)); // Monday
    function chooseDay(d) { schedView.day = d; scheduleScreen(); }
    var top = [largeTitle('일정'),
      chips([['all', '전체'], ['mine', '내 일정만']], schedView.mine ? 'mine' : 'all', function (v) { schedView.mine = v === 'mine'; scheduleScreen(); }),
      el('div.month-row', iconButton('chevl', '지난주', function () { chooseDay(addDays(day, -7)); }),
        el('span.month', (dateOf(start).getMonth() + 1) + '월 ' + dateOf(start).getDate() + '일 주'),
        iconButton('chevr', '다음 주', function () { chooseDay(addDays(day, 7)); }))];
    load(top, function () { return cached('schedule.range', { from: start, to: addDays(start, 6) }); }, function (r) {
      var items = r.items.filter(function (i) { return !schedView.mine || i.mine; });
      var byDay = {};
      items.forEach(function (i) {
        var s = i.start.slice(0, 10), e = (i.end || i.start).slice(0, 10);
        for (var d = s; d <= e && d <= r.to; d = addDays(d, 1)) if (d >= r.from) (byDay[d] = byDay[d] || []).push(i);
      });
      var strip = el('div.week');
      for (var k = 0; k < 7; k++) (function (d) {
        var n = (byDay[d] || []).length;
        strip.appendChild(el('button.day' + (d === day ? '.sel' : '') + (d === todayYmd() ? '.now' : ''), { onclick: function () { chooseDay(d); } },
          el('span', dow(d)), el('b', String(dateOf(d).getDate())), el('i.dots', n ? '•••'.slice(0, Math.min(n, 3)) : '')));
      })(addDays(start, k));
      var out = [strip];
      var any = false;
      for (var j = 0; j < 7; j++) {
        var d = addDays(start, j);
        if (d < day || !byDay[d]) continue;
        any = true;
        out.push(sectionHeader(longDay(d)));
        out.push(list(byDay[d].map(function (i) { return eventRow(i, d); })));
      }
      if (!any) out.push(el('p.empty-note', schedView.mine ? '이번 주 남은 내 일정이 없어요.' : '이번 주 남은 일정이 없어요.'));
      (r.errors || []).forEach(function (e) { out.push(footnote(e)); });
      return out;
    });
  }
  function eventRow(i, day) {
    var time = i.allDay ? '하루 종일' : (i.start.slice(0, 10) < day ? '계속' : hm(i.start));
    var kind = { event: '일정', reservation: '예약', work: '업무' }[i.kind];
    var sub = i.kind === 'reservation' ? hm(i.start) + '–' + hm(i.end) + (i.purpose ? ' · ' + i.purpose : '')
      : i.kind === 'work' ? (i.people || []).join(', ') + ' · ' + i.project + (i.done ? ' · 완료' : '')
      : [i.allDay ? '' : hm(i.start) + '–' + hm(i.end), i.location, i.calendar].filter(Boolean).join(' · ');
    return el('li.hig-list__row.ev', { 'data-interactive': true, style: 'min-height:var(--hig-row-rich)', onclick: function () {
      if (i.kind === 'reservation') go('res/' + i.start.slice(0, 10));
      else if (i.kind === 'work') go('work');
      else tell(i.title, [sub, i.description].filter(Boolean).join('\n\n'));
    } }, el('span.evbar', { style: 'background:' + safeColor(i.color) }), el('span.evtime', time),
      el('span.hig-list__text', el('span.hig-list__title', i.title), el('span.hig-list__subtitle', sub)), el('span.kind', kind));
  }
  function safeColor(c) { return /^#[0-9a-fA-F]{3,8}$/.test(c || '') ? c : '#8e8e93'; }

  // ---------- 장비 예약 ----------
  var resView = { mine: false };
  function resScreen(day) {
    day = /^\d{4}-\d{2}-\d{2}$/.test(day || '') ? day : todayYmd();
    var top = [navbar('장비 예약', iconButton('refresh', '새로 고침', function () { refresh('pull'); }), iconButton('plus', '새 예약', function () { resSheet(null, day, null, state.res); })),
      el('div.month-row', iconButton('chevl', '전날', function () { go('res/' + addDays(day, -1)); }),
        el('span.month', shortDay(day) + (day === todayYmd() ? ' 오늘' : '')),
        iconButton('chevr', '다음날', function () { go('res/' + addDays(day, 1)); })),
      chips([['all', '모든 장비'], ['mine', '내 예약']], resView.mine ? 'mine' : 'all', function (v) { resView.mine = v === 'mine'; resScreen(day); })];
    load(top, function () { return cached('reservation.list', resView.mine ? { from: todayYmd(), to: addDays(todayYmd(), 30) } : { from: day, to: day }); }, function (r) {
      state.res = r;
      if (resView.mine) {
        var mine = r.reservations.filter(function (x) { return x.mine && x.end > r.now; });
        return [mine.length ? list(mine.map(function (x) {
          return row({ title: x.equipment, sub: shortDay(x.start.slice(0, 10)) + ' ' + hm(x.start) + '–' + hm(x.end) + (x.purpose ? ' · ' + x.purpose : ''),
            onclick: function () { resSheet(x, x.start.slice(0, 10), null, r); } });
        })) : el('p.empty-note', '앞으로 30일 동안 내 예약이 없어요.'), footnote('예약을 누르면 수정하거나 취소할 수 있어요.')];
      }
      var cards = r.equipment.map(function (eq) {
        var mineRes = r.reservations.filter(function (x) { return x.equipmentId === eq.id; });
        var tl = el('div.tl');
        mineRes.forEach(function (x) {
          var s = Math.max(8 * 60, x.start.slice(0, 10) < day ? 0 : toMin(x.start)), e = Math.min(22 * 60, x.end.slice(0, 10) > day ? 24 * 60 : toMin(x.end));
          if (e <= s) return;
          tl.appendChild(el('span.blk' + (x.mine ? '.mine' : ''), { style: 'left:' + ((s - 480) / 840 * 100).toFixed(1) + '%;width:' + ((e - s) / 840 * 100).toFixed(1) + '%;background:' + safeColor(eq.color) }));
        });
        if (day === todayYmd()) {
          var nm = new Date().getHours() * 60 + new Date().getMinutes();
          if (nm > 480 && nm < 1320) tl.appendChild(el('span.nowline', { style: 'left:' + ((nm - 480) / 840 * 100).toFixed(1) + '%' }));
        }
        var my = mineRes.filter(function (x) { return x.mine; })[0];
        var next = eq.state === '점검 중' ? '점검 중이라 새 예약을 받지 않아요' :
          (my ? '내 예약 ' + hm(my.start) + '–' + hm(my.end) + ' · ' : '') + nextFree(eq, mineRes, day);
        return el('div.hig-card.eq', { 'data-interactive': true, onclick: function () { go('eq/' + eq.id + '/' + day); } },
          el('div.eq-top', el('span.sw', { style: 'background:' + safeColor(eq.color) }), el('span.eq-n', eq.name),
            eq.state === '점검 중' ? status('warning', '점검 중', true) : status('success', '사용 가능', true)),
          el('div.eq-loc', [eq.location, eq.step + '분 단위', eq.maxHours ? '최대 ' + eq.maxHours + '시간' : '', eq.owner ? '담당 ' + eq.owner : ''].filter(Boolean).join(' · ')),
          tl, el('div.tl-ax', ['8', '12', '16', '20', '22'].map(function (t) { return el('span', t); })), el('div.eq-next', next));
      });
      return [cards.length ? el('div.eqs', cards) : el('p.empty-note', '등록된 장비가 없어요. 데스크톱 앱에서 장비를 추가해요.'),
        footnote('카드를 누르면 그 장비의 하루 시간표가 열리고, 빈 칸을 누르거나 + 로 새 예약을 만들어요.')];
    });
  }
  function toMin(stamp) { return +stamp.slice(11, 13) * 60 + +stamp.slice(14, 16); }
  function minText(m) { return pad(Math.floor(m / 60)) + ':' + pad(m % 60); }
  function nextFree(eq, res, day) {
    var from = 8 * 60;
    if (day === todayYmd()) { var n = new Date(); from = Math.max(from, Math.ceil((n.getHours() * 60 + n.getMinutes()) / eq.step) * eq.step); }
    if (day < todayYmd()) return '지난 날';
    for (var t = from; t < 22 * 60; t += eq.step) {
      var busy = res.some(function (x) { var s = x.start.slice(0, 10) < day ? 0 : toMin(x.start), e = x.end.slice(0, 10) > day ? 1440 : toMin(x.end); return s <= t && t < e; });
      if (!busy) return '다음 빈 시간 ' + minText(t);
    }
    return '오늘은 22시까지 꽉 찼어요';
  }

  function eqScreen(id, day) {
    day = /^\d{4}-\d{2}-\d{2}$/.test(day || '') ? day : todayYmd();
    load(navbar('시간표', backButton('res/' + day), iconButton('plus', '새 예약', function () { resSheet(null, day, { eq: id }, state.res); })),
      function () { return cached('reservation.list', { from: day, to: day }); }, function (r) {
        state.res = r;
        var eq = r.equipment.filter(function (e) { return e.id === id; })[0];
        if (!eq) return el('p.empty-note', '장비를 찾지 못했어요.');
        var res = r.reservations.filter(function (x) { return x.equipmentId === id; });
        var rows = [];
        var now = nowStamp();
        for (var t = 8 * 60; t < 22 * 60; t += Math.max(eq.step, 30)) (function (t) {
          var stamp = day + ' ' + minText(t);
          var hit = res.filter(function (x) { return x.start <= stamp && stamp < x.end; })[0];
          if (hit) {
            if (hit.start !== stamp && hit.start.slice(0, 10) === day && rows.length) return;
            rows.push(el('li.hig-list__row', { 'data-interactive': true, onclick: function () { resSheet(hit, day, null, r); } },
              el('span.slot-t', minText(t)), el('span.sw', { style: 'background:' + safeColor(eq.color) + (hit.mine ? '' : ';opacity:.5') }),
              el('span.hig-list__text', el('span.hig-list__title', hit.person + (hit.mine ? ' (나)' : '')), el('span.hig-list__subtitle', hm(hit.start) + '–' + hm(hit.end) + (hit.purpose ? ' · ' + hit.purpose : ''))), chevron()));
          } else if (stamp < now) {
            rows.push(el('li.hig-list__row', el('span.slot-t.slot-past', minText(t)), el('span.hig-list__title.slot-past', '지남')));
          } else {
            rows.push(el('li.hig-list__row', { 'data-interactive': eq.state !== '점검 중' ? true : null, onclick: eq.state === '점검 중' ? null : function () {
              resSheet(null, day, { eq: id, start: minText(t) }, r);
            } }, el('span.slot-t', minText(t)), el('span.hig-list__title' + (eq.state === '점검 중' ? '' : '.slot-free'), eq.state === '점검 중' ? '점검 중' : '비어 있음 · 눌러서 예약')));
          }
        })(t);
        return [el('div.notice', el('h2.nt', eq.name), el('div.nm', shortDay(day) + ' · ' + [eq.location, eq.step + '분 단위', eq.maxHours ? '최대 ' + eq.maxHours + '시간' : ''].filter(Boolean).join(' · '))),
          el('ul.hig-list.m.slots', rows), eq.note ? footnote(eq.note) : null];
      });
  }

  /** New or edit booking sheet. data = the loaded reservation.list answer (equipment + that day's bookings). */
  function resSheet(res, day, preset, data) {
    var equipment = (data && data.equipment) || [];
    if (!equipment.length) { cached('equipment.list').then(function (r) { resSheet(res, day, preset, { equipment: r.equipment, reservations: [] }); }, failed); return; }
    preset = preset || {};
    var eqSel = select(equipment.filter(function (e) { return res || e.state !== '점검 중'; }).map(function (e) { return [e.id, e.name]; }), res ? res.equipmentId : preset.eq || '');
    var date = el('input.hig-input', { type: 'date', value: res ? res.start.slice(0, 10) : day });
    var startSel = select([], ''), endSel = select([], '');
    var purpose = el('input.hig-input', { value: res ? res.purpose : '', maxlength: 300, placeholder: '예: 세포 이미징' });
    var notify = select([[0, '없음'], [10, '10분 전'], [30, '30분 전'], [60, '1시간 전']], res ? res.notify : 30);
    var check = el('div.okline');
    var dayRes = (data && data.reservations) || [];
    var started = res && res.start <= nowStamp();
    function eqNow() { return equipment.filter(function (e) { return e.id === eqSel.input.value; })[0] || equipment[0]; }
    function fill(sel, from, to, step, value) {
      sel.input.textContent = '';
      for (var t = from; t <= to; t += step) sel.input.appendChild(el('option', { value: minText(t), selected: minText(t) === value ? true : null }, minText(t)));
    }
    function times() {
      var eq = eqNow(), step = eq ? eq.step : 30;
      var s0 = res ? hm(res.start) : preset.start || '';
      if (!s0 && date.value === todayYmd()) {
        var n = new Date(), m = Math.ceil((n.getHours() * 60 + n.getMinutes()) / step) * step;
        if (m < 24 * 60) s0 = minText(m);
      }
      if (!res && !startSel.input.value && eq) {   // a new booking: start at the first free time
        var m0 = toMin('0000-00-00 ' + (s0 || '09:00'));
        var busy = function (m) {
          var st = date.value + ' ' + minText(m);
          return dayRes.some(function (x) { return x.equipmentId === eq.id && x.start <= st && st < x.end; });
        };
        while (m0 < 22 * 60 && busy(m0)) m0 += step;
        s0 = minText(m0);
      }
      var keepS = startSel.input.value || s0, keepE = endSel.input.value || (res ? hm(res.end) : '');
      fill(startSel, 0, 24 * 60 - step, step, keepS || '09:00');
      if (!startSel.input.value) startSel.input.selectedIndex = Math.floor(9 * 60 / step);
      var s = toMin('0000-00-00 ' + startSel.input.value);
      if (!keepE || toMin('0000-00-00 ' + keepE) <= s) keepE = minText(Math.min(s + step * Math.max(1, Math.round(60 / step)), 24 * 60));
      fill(endSel, s + step, 24 * 60, step, keepE === '24:00' ? '24:00' : keepE);
      validate();
    }
    function validate() {
      var eq = eqNow();
      var s = date.value + ' ' + startSel.input.value, e = endSel.input.value === '24:00' ? addDays(date.value, 1) + ' 00:00' : date.value + ' ' + endSel.input.value;
      var problem = '';
      if (eq && eq.state === '점검 중' && !res) problem = '점검 중인 장비예요';
      else if (eq && eq.maxHours && (toMinFull(e) - toMinFull(s)) / 60 > eq.maxHours) problem = '한 번에 ' + eq.maxHours + '시간까지예요';
      else if (!res && s < nowStamp()) problem = '지난 시각이에요';
      else if (date.value === day || !data) {
        var clash = dayRes.filter(function (x) { return x.equipmentId === (eq && eq.id) && (!res || x.id !== res.id) && s < x.end && x.start < e; })[0];
        if (clash) problem = clash.person + '님 예약(' + hm(clash.start) + '–' + hm(clash.end) + ')과 겹쳐요';
      }
      check.textContent = '';
      check.appendChild(problem ? status('error', problem, true) : status('success', '비어 있어요' + (eq ? ' · ' + eq.step + '분 단위' + (eq.maxHours ? ' · 최대 ' + eq.maxHours + '시간' : '') : ''), true));
      return { s: s, e: e };
    }
    function toMinFull(stamp) { return dateOf(stamp.slice(0, 10)).getTime() / 60000 + toMin(stamp); }
    eqSel.input.addEventListener('change', times);
    startSel.input.addEventListener('change', function () { endSel.input.value = ''; times(); });
    endSel.input.addEventListener('change', validate);
    date.addEventListener('change', function () {
      if (date.value === day) { dayRes = (data && data.reservations) || []; validate(); return; }
      api('reservation.list', { from: date.value, to: date.value }).then(function (r) { dayRes = r.reservations; validate(); }, function () { dayRes = []; validate(); });
    });
    if (started) { eqSel.input.disabled = true; date.disabled = true; startSel.input.disabled = true; }
    var body = el('form.hig-form.regf', { onsubmit: function (ev) { ev.preventDefault(); } },
      formRow('장비', eqSel), formRow('날짜', date),
      el('div.two', formRow('시작', startSel), formRow('끝', endSel)), check,
      formRow('용도', purpose), formRow('알림', notify),
      res && !res.mine ? footnote(res.person + '님의 예약이에요. 바꾸면 기록에 남아요.') : footnote('예약자는 내 이름으로 들어가요. 저장할 때 서버가 겹침·단위·최대 시간을 다시 확인해요.' + (started ? ' 이미 시작한 예약은 끝 시각만 바꿀 수 있어요.' : '')),
      res ? el('div.cancel-row', el('button.hig-delete.hig-delete--label', { type: 'button', onclick: function () {
        ask('예약을 취소할까요?', res.equipment + ' ' + shortDay(res.start.slice(0, 10)) + ' ' + hm(res.start) + '–' + hm(res.end), '예약 취소', true).then(function (yes) {
          if (!yes) return;
          write('reservation.cancel', { id: res.id }, '취소할').then(function () { sh.close(); toast('예약을 취소했어요'); route_(); }, failed);
        });
      } }, icon('trash'), '예약 취소…')) : null);
    var sh = sheet(res ? '예약 수정' : '새 예약', body, function () {
      var t = validate();
      var payload = { equipmentId: eqSel.input.value, start: t.s, end: t.e, purpose: purpose.value, notify: +notify.input.value };
      if (res) payload.id = res.id;
      return write(res ? 'reservation.update' : 'reservation.create', payload, '바꿀').then(function () {
        toast(res ? '예약을 고쳤어요' : '예약했어요');
        route_();
      });
    });
    times();
  }

  // ---------- 업무 ----------
  state.reqDraft = { workId: '', memo: '' };
  /** Bottom sheet listing the 업무 to send a request to (담당 first, then this turn of 당번·마일스톤). */
  function workPicker(works, current, onpick) {
    var scrim = el('div.hig-scrim', { onclick: close });
    function close() { scrim.remove(); box.remove(); }
    function item(w) {
      return el('li.hig-list__row', { 'data-interactive': true, style: 'min-height:var(--hig-row-rich)', onclick: function () { close(); onpick(w); } },
        el('span.rq-dot.big', { style: 'background:' + (w.kind === 'owner' ? '#5e5ce6' : '#34c759') }),
        el('span.hig-list__text', el('span.hig-list__title', w.name),
          el('span.hig-list__subtitle', (w.kind === 'owner' ? '담당' : '이번 차례') + ' · ' + (w.people.join(', ') || '담당자 없음'))),
        w.id === current ? el('span.tick', icon('tick')) : null);
    }
    var owners = works.filter(function (w) { return w.kind === 'owner'; }), turns = works.filter(function (w) { return w.kind !== 'owner'; });
    var box = el('div.hig-sheet.app-sheet', { role: 'dialog', 'aria-label': '어느 업무에 요청할까요?' }, el('div.hig-sheet__grabber'),
      el('div.sheet-bar', el('span'), el('span.sheet-t', '어느 업무에 요청할까요?'), textButton('닫기', close)),
      el('div.pk-scroll',
        owners.length ? [el('div.pk-h', '담당'), list(owners.map(item))] : null,
        turns.length ? [el('div.pk-h', '당번 · 마일스톤'), list(turns.map(item))] : null,
        works.length ? null : el('p.empty-note', '업무가 없어요. 데스크톱 앱에서 업무를 만들어 주세요.')));
    document.body.appendChild(scrim);
    document.body.appendChild(box);
  }
  function requestCard(works) {
    var d = state.reqDraft;
    var w = works.filter(function (x) { return x.id === d.workId; })[0] || null;
    var ta = el('textarea.rq-ta', { placeholder: '무엇을 부탁할까요?', rows: 3, 'aria-label': '요청 내용' });
    ta.value = d.memo || '';
    var send = el('button.rq-send', { disabled: true }, icon('send'), '보내기');
    function check() { send.disabled = !(w && ta.value.trim()); }
    ta.addEventListener('input', function () { d.memo = ta.value; check(); });
    var to;
    function toBtn() {
      var b = el('button.rq-to', { type: 'button' },
        w ? [el('span.rq-dot', { style: 'background:' + (w.kind === 'owner' ? '#5e5ce6' : '#34c759') }), el('span.rq-w', w.name),
             el('span.rq-p', '받는 사람 · ' + (w.people.join(', ') || '없음'))] : el('span.rq-w.ph', '업무 고르기'),
        el('span.rq-cv', '⌄'));
      b.addEventListener('click', function () {
        workPicker(works, d.workId, function (pick) { d.workId = pick.id; w = pick; var nb = toBtn(); b.replaceWith(nb); check(); });
      });
      return b;
    }
    to = toBtn();
    send.addEventListener('click', function () {
      send.disabled = true;
      write('request.create', { workId: w.id, memo: ta.value.trim() }).then(function (r) {
        state.reqDraft = { workId: '', memo: '' };
        toast(r.to.join(', ') + '님에게 보냈어요');
        refresh('quiet');
      }, function (e) { check(); failed(e); });
    });
    check();
    var card = el('div.rq-card', el('div.rq-h', '업무 요청하기'), to, ta,
      el('div.rq-foot', el('span.rq-hint', '받는 사람에게 메일도 가요'), send));
    return card;
  }
  function workScreen() {
    load(largeTitle('업무'), function () { return Promise.all([cached('work.board'), cached('request.list')]); }, function (res) {
      var w = res[0], q = res[1];
      var me = store('lab_member');
      var out = [requestCard(q.works || [])];
      var toMe = (q.toMe || []).slice(0, 10), fromMe = (q.fromMe || []).slice(0, 5);
      var open = toMe.filter(function (x) { return !x.acked; }).length;
      out.push(sectionHeader('나에게 온 요청', open ? String(open) : null));
      out.push(toMe.length ? el('ul.rq-list', toMe.map(askRow)) : el('p.empty-note.sm', '아직 받은 요청이 없어요.'));
      if (fromMe.length) {
        out.push(sectionHeader('내가 보낸 요청'));
        out.push(el('ul.rq-list', fromMe.map(askRow)));
      }
      var cards = w.projects.map(function (p) {
        if (p.kind === 'milestone') {
          return el('div.hig-card.pj', el('div.pj-top', el('span.pj-n', p.name), el('span.pj-k', '마일스톤 · 남은 ' + p.remaining)),
            el('ul.hig-list.kv', (p.steps || []).map(function (t) {
              return row({ title: t.title, sub: (t.people || []).join(', '), detail: t.date ? shortDay(t.date) : '' });
            })));
        }
        function cell(label, t, mineCls) {
          return el('div.pj-c' + (mineCls ? '.me' : ''), el('span.pj-l', label), el('b', t ? t.people.join(', ') : '–'), el('span', t && t.date ? shortDay(t.date) : ''));
        }
        var mineNext = p.next && p.next.people.indexOf(me) >= 0;
        return el('div.hig-card.pj', el('div.pj-top', el('span.pj-n', p.name), el('span.pj-k', [p.freq, p.duty !== p.name ? p.duty : ''].filter(Boolean).join(' · '))),
          el('div.pj-grid', cell('이번', p.current, p.mineNow), cell('다음', p.next, mineNext)));
      });
      out.push(sectionHeader('당번'), cards.length ? el('div.pjs', cards) : el('p.empty-note.sm', '당번 업무가 없어요.'));
      var owners = w.owners || [];
      if (owners.length) {
        out.push(sectionHeader('담당', '일정표에 안 나와요'));
        out.push(list(owners.map(function (o) {
          return row({ title: o.name, sub: (o.people.join(', ') || '담당자 없음') + (o.mine ? ' (나)' : ''), wrap: true,
            trail: o.mine ? el('span.mine-tag', '내 담당') : null });
        })));
      }
      out.push(footnote(w.overdue.length ? '지난 날짜인데 완료 표시가 안 된 내 업무가 ' + w.overdue.length + '개 있어요. 완료 표시와 담당 정하기는 데스크톱 앱에서 해요.'
        : '완료 표시와 담당 정하기는 데스크톱 앱 업무 탭에서 해요.'));
      return out;
    });
  }

  // ---------- 연구실 ----------
  function labScreen() {
    load(largeTitle('연구실'), function () { return cached('home'); }, function (r) {
      var n = r.notices || {}, o = r.orders || {};
      return [list([
        row({ icon: 'mega', color: '#ff375f', title: '공지사항', trail: n.unread ? el('span.hig-badge', String(n.unread)) : null, onclick: function () { go('notices'); } }),
        row({ icon: 'cart', color: '#ff9500', title: '주문 요청', detail: o.handle ? '처리할 것 ' + o.handle : (o.mine ? '내 요청 ' + o.mine : ''), onclick: function () { go('orders'); } }),
        row({ icon: 'won', color: '#34c759', title: '예산', onclick: function () { go('budget'); } })]),
        sectionHeader('최근 공지'),
        (n.latest || []).length ? list(n.latest.map(noticeRow)) : el('p.empty-note', '공지가 없어요.')];
    });
  }
  function noticeRow(x) {
    return row({ title: x.title, sub: x.author + ' · ' + x.date.slice(0, 10) + (x.preview ? ' · ' + x.preview : ''),
      lead: x.pinned ? status('warning', '중요', true) : null, trail: x.read ? null : el('span.hig-badge', '새'), onclick: function () { go('notice/' + x.id); } });
  }
  function noticesScreen() {
    load(navbar('공지사항', backButton('lab'), iconButton('plus', '새 공지', function () { noticeSheet(null); })), function () { return cached('notice.list'); }, function (r) {
      return r.notices.length ? list(r.notices.map(noticeRow)) : el('p.empty-note', '공지가 없어요. + 로 첫 공지를 써 보세요.');
    });
  }
  function noticeScreen(id) {
    var top = navbar('공지', backButton('notices'));
    load(top, function () { return cached('notice.get', { id: id }); }, function (r) {
      var n = r.notice;
      top.querySelector('.hig-navbar__trailing').appendChild(textButton('수정', function () { noticeSheet(n); }));
      return [el('div.notice', n.pinned ? status('warning', '중요') : null, el('h2.nt', n.title), el('div.nm', n.author + ' · ' + n.date), el('p.nbody', n.body)),
        sectionHeader('열람 ' + n.readers.length + '/' + r.members.length),
        el('div.rds', r.members.map(function (m) { return el('span.rd' + (n.readers.indexOf(m) >= 0 ? '' : '.no'), m); })),
        footnote('공지를 열면 내 이름이 "열람" 칸에 더해져요. 누구나 고칠 수 있고, 누가 고쳤는지 기록에 남아요.'),
        el('div.cancel-row', el('button.hig-delete.hig-delete--label', { onclick: function () {
          ask('공지를 지울까요?', n.title, '지우기', true).then(function (yes) {
            if (yes) write('notice.delete', { id: n.id }, '지울').then(function () { toast('공지를 지웠어요'); go('notices'); }, failed);
          });
        } }, icon('trash'), '공지 지우기…'))];
    });
  }
  function noticeSheet(n) {
    var title = el('input.hig-input', { value: n ? n.title : '', maxlength: 200, placeholder: '제목' });
    var body = el('textarea.hig-input.ta', { maxlength: 20000, placeholder: '내용' });
    body.value = n ? n.body : '';
    var pin = el('input.hig-switch', { type: 'checkbox', 'aria-label': '중요', checked: n && n.pinned });
    var form = el('form.hig-form.regf', { onsubmit: function (ev) { ev.preventDefault(); } }, formRow('제목', title), formRow('내용', body),
      el('ul.hig-list', el('li.hig-list__row', el('span.hig-list__title', '중요 (맨 위에 고정)'), pin)),
      footnote('작성자는 내 이름, 날짜는 서버 시각으로 들어가요. 데스크톱 앱에도 2분 안에 보여요.'));
    sheet(n ? '공지 수정' : '새 공지', form, function () {
      var data = { title: title.value, body: body.value, pinned: pin.checked };
      if (n) data.id = n.id;
      return write(n ? 'notice.update' : 'notice.create', data).then(function (r) {
        toast(n ? '고쳤어요' : '게시했어요');
        if (n) route_(); else go('notice/' + r.id);
      });
    }, n ? '저장' : '게시');
    setTimeout(function () { title.focus(); }, 50);
  }

  var orderView = { filter: 'all' };
  var STATE_KIND = { '요청': 'info', '주문함': 'warning', '입고됨': 'success', '취소': 'neutral' };
  function ordersScreen(filter) {
    if (filter) orderView.filter = filter;
    var top = [navbar('주문 요청', backButton('lab'), iconButton('plus', '새 주문 요청', function () { orderSheet(null, state.orderMeta); })),
      chips([['all', '전체'], ['handle', '내가 처리'], ['mine', '내 요청']], orderView.filter, function (v) { orderView.filter = v; ordersScreen(); })];
    load(top, function () { return cached('order.list'); }, function (r) {
      state.orderMeta = r;
      var list0 = r.orders.filter(function (o) { return orderView.filter === 'all' || (orderView.filter === 'handle' ? o.mineHandle : o.mineRequest); });
      var wait = list0.filter(function (o) { return o.state === '요청'; }), doing = list0.filter(function (o) { return o.state === '주문함'; }),
        done = list0.filter(function (o) { return o.state === '입고됨' || o.state === '취소'; }).slice(0, 30);
      function orow(o) {
        return row({ title: o.item, sub: [o.company, o.catalog, o.qty ? o.qty + '개' : '', '요청 ' + o.requester, o.date.slice(5, 10).replace('-', '/')].filter(Boolean).join(' · '),
          trail: status(STATE_KIND[o.state], o.state), onclick: function () { go('order/' + o.id); } });
      }
      var out = [];
      if (wait.length) out.push(sectionHeader('처리 대기 ' + wait.length), list(wait.map(orow)));
      if (doing.length) out.push(sectionHeader('주문함 ' + doing.length), list(doing.map(orow)));
      if (done.length) out.push(sectionHeader('끝남 (최근)'), list(done.map(orow)));
      if (!out.length) out.push(el('p.empty-note', '주문 요청이 없어요. + 로 새 요청을 만들어요.'));
      out.push(footnote('요청을 누르면 상세가 열리고, 거기서 주문함·입고됨으로 바꿔요. 담당자 없는 카테고리는 ' + (r.contact || '주문 담당자') + '님에게 가요.'));
      return out;
    });
  }
  function orderScreen(id) {
    var top = navbar('주문 요청', backButton('orders'));
    load(top, function () { return cached('order.list'); }, function (r) {
      state.orderMeta = r;
      var o = r.orders.filter(function (x) { return x.id === id; })[0];
      if (!o) return el('p.empty-note', '지워진 요청이에요.');
      top.querySelector('.hig-navbar__trailing').appendChild(textButton('수정', function () { orderSheet(o, r); }));
      function setState(s) {
        write('order.status', { id: o.id, state: s }).then(function (x) { toast(s + (x.mailed ? ' · 요청자에게 메일 보냄' : '')); route_(); }, failed);
      }
      return [el('div.notice', status(STATE_KIND[o.state], o.state), el('h2.nt', o.item), el('div.nm', '요청 ' + o.requester + ' · ' + o.date + (o.owner ? ' · 담당 ' + o.owner : ''))),
        list([row({ title: '제조사 · Cat#', detail: [o.company, o.catalog].filter(Boolean).join(' · ') || '–' }),
          row({ title: '수량 · 단가', detail: [o.qty, o.price].filter(Boolean).join(' · ') || '–' }),
          row({ title: '카테고리', detail: o.category || '선택 안 함' }),
          o.link ? el('li.hig-list__row', el('span.hig-list__title', '링크'), /^https?:\/\//i.test(o.link) ? el('a.hig-list__detail.linkish', { href: o.link, target: '_blank', rel: 'noopener noreferrer' }, '열기') : el('span.hig-list__detail', o.link)) : null,
          o.note ? row({ title: '용도·메모', sub: o.note, wrap: true, rich: false }) : null,
          row({ title: '담당자 메일', detail: o.mailed || '안 보냄' }),
          o.ordered ? row({ title: '주문일', detail: o.ordered }) : null,
          o.received ? row({ title: '입고일', detail: o.received }) : null,
          row({ title: '상태 알림', detail: o.notify ? '켬' : '끔' })]),
        sectionHeader('상태'),
        segmented([['요청', '요청'], ['주문함', '주문함'], ['입고됨', '입고됨']], o.state, function (s) { if (s !== o.state) setState(s); }),
        footnote('주문함을 고르면 주문일, 입고됨을 고르면 입고일이 오늘로 채워지고, 알림이 켜져 있으면 요청자에게 메일이 가요.'),
        o.state !== '취소' ? el('div.cancel-row', el('button.hig-delete.hig-delete--label', { onclick: function () {
          ask('요청을 취소할까요?', o.item + ' · 행은 남고 상태만 취소로 바뀌어요.', '요청 취소', true).then(function (yes) { if (yes) setState('취소'); });
        } }, icon('trash'), '요청 취소…')) : null];
    });
  }
  function orderSheet(o, meta) {
    meta = meta || { categories: [] };
    function field(v, opts) { return el('input.hig-input', Object.assign({ value: v || '' }, opts || {})); }
    var item = field(o && o.item, { maxlength: 300, placeholder: '필수' }), company = field(o && o.company), catalog = field(o && o.catalog);
    var qty = field(o ? o.qty : '1', { inputmode: 'numeric' }), price = field(o && o.price, { inputmode: 'numeric', placeholder: '예: 32000' });
    var link = field(o && o.link, { type: 'url', placeholder: 'https://' });
    var cat = select([['', '선택 안 함']].concat(meta.categories.map(function (c) { return [c.name, c.name + (c.owner ? ' · ' + c.owner : '')]; })), o ? o.category : '');
    var note = el('textarea.hig-input.ta', { style: 'min-height:90px' }); note.value = o ? o.note : '';
    var notify = el('input.hig-switch', { type: 'checkbox', 'aria-label': '상태 알림', checked: o ? o.notify : true });
    var form = el('form.hig-form.regf', { onsubmit: function (ev) { ev.preventDefault(); } },
      formRow('품목', item), el('div.two', formRow('제조사', company), formRow('Cat#', catalog)), el('div.two', formRow('수량', qty), formRow('단가', price)),
      formRow('링크', link), formRow('카테고리', cat, '고른 카테고리의 담당자에게 요청 메일이 가요'), formRow('용도·메모', note),
      el('ul.hig-list', el('li.hig-list__row', el('span.hig-list__title', '상태가 바뀌면 나에게 메일'), notify)));
    sheet(o ? '요청 수정' : '새 주문 요청', form, function () {
      var data = { item: item.value, company: company.value, catalog: catalog.value, qty: qty.value, price: price.value, link: link.value,
                   category: cat.input.value, note: note.value, notify: notify.checked };
      if (o) data.id = o.id;
      return write(o ? 'order.update' : 'order.create', data).then(function (r) {
        toast(o ? '고쳤어요' : (r.mailed ? '요청했어요 · 담당자에게 메일 보냄' : '요청했어요'));
        if (o) route_(); else go('order/' + r.id);
      });
    }, o ? '저장' : '요청');
  }

  function budgetScreen(all) {
    load(navbar(all ? '장부' : '예산', backButton(all ? 'budget' : 'lab')), function () { return cached('budget.lab', { limit: all ? 2000 : 8 }); }, function (b) {
      var out = [];
      var me = b.mine;
      function entry(e) {
        return row({ title: e.desc || '–', sub: e.date,
          detail: el('span.hig-list__detail.num', { style: e.income ? 'color:var(--green)' : null }, e.income ? '+' + won(e.income) : '−' + won(e.expense || 0)) });
      }
      if (all) return [list([row({ title: '현재 잔액', detail: won(b.balance) })]), sectionHeader('전체 ' + b.entries + '건 (최근부터)'), list(b.ledger.map(entry))];
      if (b.round) {
        out.push(el('div.hig-card.fee',
          el('div.today-top', el('span.hig-card__eyebrow', '내 랩비 · ' + b.round.label + ' 회차'),
            me ? status(me.state === '납부 완료' ? 'success' : me.state === '일부 납부' ? 'warning' : me.state === '없음' ? 'neutral' : 'error', me.state === '납부 완료' ? '납부 확인' : me.state) : null),
          me ? el('div.big-time', won(me.total).replace('원', ''), el('small', '원')) : el('div.big-time.word', '명단에 없어요'),
          el('div.today-sub', me ? (me.state === '납부 완료' ? (me.paidOn ? me.paidOn + ' 장부에 "' + me.name + ' 랩비" 입금이 있어요' : '입금 확인') : me.paid ? won(me.paid) + ' 입금 · 남은 ' + won(me.left) : '장부에 "' + me.name + ' 랩비" 입금이 아직 없어요')
            : '랩비 탭에 내 이름이 없어요.'),
          me ? el('ul.hig-list.kv', row({ title: '지난 회차 미납', detail: won(me.carryover) }), row({ title: '이번 랩비', detail: won(me.fee) }),
            row({ title: '합계', detail: el('span.hig-list__detail.num', el('b', won(me.total))) })) : null));
      }
      out.push(sectionHeader('연구실 전체'), list([row({ title: '현재 잔액', detail: won(b.balance) })]));
      out.push(sectionHeader('최근 내역', textButton('전체 보기', function () { go('ledger'); })), list(b.ledger.map(entry)));
      if (b.round) {
        var paid = b.round.lines.filter(function (l) { return l.state === '납부 완료' || l.state === '없음'; }).length;
        out.push(sectionHeader('이번 회차 납부 ' + paid + '/' + b.round.lines.length),
          list(b.round.lines.map(function (l) {
            return row({ title: l.name, sub: l.carryover ? '미납 이월 ' + won(l.carryover) : '', rich: false,
              detail: el('span.hig-list__detail', status(l.state === '납부 완료' ? 'success' : l.state === '일부 납부' ? 'warning' : l.state === '없음' ? 'neutral' : 'error', l.state + (l.left && l.state !== '미납' ? ' · 남은 ' + won(l.left) : ''), true)) });
          })));
      }
      out.push(footnote('연구실 안에서만 쓰는 앱이라 모두가 잔액·장부·납부 현황까지 볼 수 있어요. 예산 고치기는 데스크톱 앱에서 해요.'));
      return out;
    });
  }

  // ---------- routing ----------
  function go(path) { if (location.hash === '#/' + path) route_(); else location.hash = '#/' + path; }
  function closeOverlays() {
    [].forEach.call(document.querySelectorAll('body > .hig-sheet, body > .hig-scrim, body > .hig-alert'), function (n) { n.remove(); });
  }
  function route_() {
    closeOverlays();
    state.refresh = null;
    show();
    window.scrollTo(0, 0);
  }
  /** Ask the server again for what the current screen shows. mode 'pull' (user pulled) or 'quiet' (5-minute refresh). */
  function refresh(mode) {
    if (state.busy || !store('lab_key') || !store('lab_member') || state.route === 'tap' || state.route === 'me') return Promise.resolve();
    state.busy = true;
    if (mode === 'quiet') banner(true);
    state.loading = null;
    state.refresh = mode;
    show();
    state.refresh = null;
    return Promise.resolve(state.loading).then(function () {
      state.busy = false; banner(false);
      if (mode === 'pull') toast('새로 받았어요');
    }, function () { state.busy = false; banner(false); });
  }
  function show() {
    var path = (location.hash || '#/home').replace(/^#\/?/, '') || 'home';
    state.route = path;
    var p = path.split('/');
    switch (p[0]) {
      case 'me': return meScreen();
      case 'schedule': return scheduleScreen();
      case 'res': return resScreen(p[1]);
      case 'eq': return eqScreen(p[1], p[2]);
      case 'work': return workScreen();
      case 'lab': return labScreen();
      case 'notices': return noticesScreen();
      case 'notice': return noticeScreen(p[1]);
      case 'orders': return ordersScreen(p[1]);
      case 'order': return orderScreen(p[1]);
      case 'budget': return budgetScreen(false);
      case 'ledger': return budgetScreen(true);
      default: state.route = 'home'; return homeScreen();
    }
  }

  function theme() {
    var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    var meta = document.querySelector('meta[name=theme-color]');
    if (meta) meta.setAttribute('content', dark ? '#000000' : '#f5f5f7');
  }

  function boot() {
    if (!store('lab_key')) return pinScreen();
    if (!store('lab_member')) return pickScreen(false);
    if (savedFor !== store('lab_member')) { saved = readSaved(); savedFor = store('lab_member'); }
    var tag = sessionStorage.getItem('pending_tap');
    if (tag) {
      sessionStorage.removeItem('pending_tap');
      state.route = 'tap';
      return tapScreen(JSON.parse(tag));
    }
    opening();
  }
  var savedFor = store('lab_member');
  /** A sheet or alert is open, or the person is typing: a background refresh must not redraw now. */
  function uiBusy() {
    var a = document.activeElement;
    return !!(document.querySelector('body > .hig-sheet, body > .hig-alert') || (a && /^(TEXTAREA|INPUT|SELECT)$/.test(a.tagName)));
  }
  /** App opened. First time: the loading screen while 요약 (home) is read, then 요약 shows and the other
   * screens' data follows in the background (a tab opened before that asks for its own data at once).
   * Later: the saved screens at once, and everything read again in the background. */
  function opening() {
    if (!Object.keys(saved).length) {
      var calls = openingCalls();
      var ld = loadingScreen();
      return cached('home').then(function () {
        ld.finish(route_);
        state.busy = true;
        readAll(calls.slice(1)).then(function () { state.busy = false; }, function () { state.busy = false; });
      }, function (e) {
        ld.close();
        if (e.code !== 'need_pin' && e.code !== 'need_member') route_();
      });
    }
    route_();
    state.busy = true;
    banner(true);
    readAll().then(function () {
      state.busy = false;
      state.refresh = 'quiet';
      if (!uiBusy()) show();
      state.refresh = null;
      banner(false);
    }, function () { state.busy = false; banner(false); });
  }
  // a different line each time the loading screen shows (never the same one twice in a row)
  var QUOTES = ['오늘 실험은 한 번에 될 거예요. 아마도요.', '실패한 실험도 데이터예요. 그렇다고 믿어요.',
    '피펫 팁은 넉넉하게, 마음은 여유롭게', '오늘의 n수가 내일의 figure가 됩니다', '논문은 언젠가 나와요. 진짜로요.',
    '커피 한 잔 하고 시작해도 괜찮아요', '대조군도 소중한 하루 보내세요', '오늘도 p < 0.05 같은 하루 되세요',
    '라벨은 꼼꼼히, 기억은 믿지 말기', '장비도 쉬어야 해요. 당신도요.', '세포는 기다려 주지 않지만 점심은 챙기세요',
    '재현되는 하루가 되길 바라요'];
  function pickQuote() {
    var last = -1;
    try { last = +(localStorage.getItem('lab_quote') || -1); } catch (e) { /* private mode */ }
    var i = Math.floor(Math.random() * (QUOTES.length - 1));
    if (i >= last && last >= 0) i++;
    try { localStorage.setItem('lab_quote', String(i)); } catch (e) { /* private mode */ }
    return QUOTES[i];
  }
  var LOADING_MIN_MS = 1800;   // the intro (blue wash, mark, line) plays at least this long
  /** The first-open loading screen: blue wash, the mark drawn in lines, a witty line typed out, ripples while waiting. */
  function loadingScreen() {
    app.textContent = '';
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var svg = '<svg viewBox="0 0 96 96" aria-hidden="true"><circle class="ld-pulse" cx="48" cy="48" r="30"/><circle class="ld-pulse p2" cx="48" cy="48" r="30"/>' +
      '<circle class="ld-ring r2" cx="48" cy="48" r="44"/><circle class="ld-ring r1" cx="48" cy="48" r="30"/><path class="ld-chk" d="M35 49 l9 9 l18 -19"/></svg>';
    var mark = el('div.ld-mark');
    mark.innerHTML = svg;
    var line = el('div.ld-s', { 'aria-live': 'polite' });
    var stt = el('span', '요약 받는 중');
    var box = el('div.ld' + (still ? '.still' : ''), { role: 'status', 'aria-label': '처음 화면을 준비하고 있어요' },
      el('div.ld-wash'), mark, line, el('div.ld-st', stt, el('small', '처음 한 번만 잠깐 걸려요')), el('div.ld-bar', el('i')));
    document.body.appendChild(box);
    var t0 = Date.now(), text = pickQuote(), timer = null;
    function typeLine() {
      if (still) { line.textContent = text; box.classList.add('loop'); return; }
      var i = 0;
      timer = setInterval(function () {
        line.textContent = text.slice(0, ++i);
        if (i >= text.length) { clearInterval(timer); box.classList.add('loop'); }
      }, 38);
    }
    var start = setTimeout(typeLine, still ? 0 : 1250);
    function close() { clearTimeout(start); clearInterval(timer); box.remove(); }
    return {
      close: close,
      /** Data is in: let the intro finish, draw the screen behind, then fade the loading screen out. */
      finish: function (draw) {
        setTimeout(function () {
          clearInterval(timer); line.textContent = text; box.classList.add('loop');
          stt.textContent = '준비됐어요';
          draw();
          setTimeout(function () {
            box.classList.add('out');
            setTimeout(close, 600);
          }, 250);
        }, Math.max(0, LOADING_MIN_MS - (Date.now() - t0)));
      }
    };
  }
  var bannerEl = null;
  function banner(on) {
    if (on && !bannerEl) { bannerEl = el('div.fresh-pill', { role: 'status' }, el('span.mini-spin'), '최신 내용 받는 중'); document.body.appendChild(bannerEl); }
    if (!on && bannerEl) { bannerEl.remove(); bannerEl = null; }
  }
  // pull down at the top of a screen → refresh that screen's data
  (function () {
    var y0 = null, dy = 0, pill = null, LIMIT = 70;
    function setPill(text, spin) {
      if (!pill) { pill = el('div.pull-pill'); document.body.appendChild(pill); }
      pill.textContent = '';
      pill.appendChild(spin ? el('span.mini-spin') : el('span.pull-arrow', '↓'));
      pill.appendChild(document.createTextNode(text));
      pill.style.transform = 'translate(-50%, ' + Math.min(dy * 0.5, LIMIT) + 'px)';
    }
    function clear() { if (pill) { pill.remove(); pill = null; } y0 = null; dy = 0; }
    window.addEventListener('touchstart', function (e) {
      y0 = (window.scrollY <= 0 && !state.busy && e.touches.length === 1 && !document.querySelector('body > .hig-sheet, body > .hig-alert')
        && store('lab_key') && store('lab_member') && state.route !== 'tap') ? e.touches[0].clientY : null;
      dy = 0;
    }, { passive: true });
    window.addEventListener('touchmove', function (e) {
      if (y0 === null) return;
      dy = e.touches[0].clientY - y0;
      if (dy <= 8 || window.scrollY > 0) { if (pill) { pill.remove(); pill = null; } return; }
      setPill(dy * 0.5 >= LIMIT ? '놓으면 새로고침' : '당겨서 새로고침', false);
    }, { passive: true });
    window.addEventListener('touchend', function () {
      if (y0 === null) return;
      if (dy * 0.5 >= LIMIT) {
        dy = LIMIT * 2; setPill('새로 받는 중', true);
        refresh('pull').then(clear, clear);
      } else clear();
    }, { passive: true });
  })();
  // back to the app after a while → refresh what is on screen if it is older than 5 minutes
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible' || !store('lab_key') || !store('lab_member')) return;
    var keys = Object.keys(saved);
    if (keys.length && Date.now() - oldest(keys) > STALE_MS) refresh('quiet');
  });
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

  // tag URL: ?picc=…&cmac=… → remember for this tab, clean the address bar
  (function () {
    var q = new URLSearchParams(location.search);
    if (q.get('picc') || q.get('cmac') || q.has('tap')) {
      sessionStorage.setItem('pending_tap', JSON.stringify({ picc: q.get('picc') || '', cmac: q.get('cmac') || '' }));
      sessionStorage.setItem('tag_at', String(Date.now()));
      history.replaceState(null, '', location.pathname + '#/home');
    }
  })();
  theme();
  if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', theme);
  refreshStore();
  window.addEventListener('hashchange', function () { if (store('lab_key') && store('lab_member')) route_(); });
  boot();
})();
