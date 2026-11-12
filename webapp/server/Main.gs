/**
 * The web app entry. The phone sends POST with Content-Type text/plain and a JSON body
 * {action, device, key, member, ...}. Every answer is {ok:true, ...} or {ok:false, error, code}.
 */

// A function, not a top-level value: Apps Script runs files in editor order, so a value here
// could point at functions of files that have not run yet.
function actions_() {
  return {
  // need the entry key only
  'members': { fn: function (b) { return membersList_(); }, member: false },
  'pick': { fn: function (b, m, d) { return pick_(b, d); }, member: false },
  // need a member
  'home': { fn: home_ },
  'batch': { fn: batch_ },
  'tap': { fn: tap_ },
  'attendance.month': { fn: attendanceMonth_ },
  'schedule.range': { fn: scheduleRange_ },
  'equipment.list': { fn: function () { return equipmentList_(); } },
  'reservation.list': { fn: reservationList_ },
  'reservation.create': { fn: reservationCreate_ },
  'reservation.update': { fn: reservationUpdate_ },
  'reservation.cancel': { fn: reservationCancel_ },
  'work.board': { fn: workBoard_ },
  'request.list': { fn: requestList_ },
  'request.create': { fn: requestCreate_ },
  'request.ack': { fn: requestAck_ },
  'notice.list': { fn: noticeList_ },
  'notice.get': { fn: noticeGet_ },
  'notice.create': { fn: noticeCreate_ },
  'notice.update': { fn: noticeUpdate_ },
  'notice.delete': { fn: noticeDelete_ },
  'order.list': { fn: orderList_ },
  'order.create': { fn: orderCreate_ },
  'order.update': { fn: orderUpdate_ },
  'order.status': { fn: orderStatus_ },
  'budget.mine': { fn: budgetMine_ },
  'budget.lab': { fn: budgetLab_ },
  'cache.clear': { fn: function () { clearCaches_(); return { cleared: true }; } }
  };
}

function doPost(e) {
  var body;
  try {
    body = JSON.parse(e && e.postData && e.postData.contents || '{}');
  } catch (err) {
    return json_({ ok: false, error: '요청을 읽지 못했어요.', code: 'bad_request' });
  }
  return json_(handle_(body));
}

/** Health check (no data). */
function doGet() {
  return json_({ ok: true, app: 'lab-webapp', time: stamp_() });
}

function handle_(body) {
  READ_CACHE_ = {};
  try {
    var action = String(body.action || '');
    if (action === 'enter') return ok_(enter_(body));
    var spec = actions_()[action];
    if (!spec) throw fail_('알 수 없는 요청이에요.', 'bad_request');
    var device = checkKey_(body);
    var member = null;
    if (spec.member !== false) {
      member = checkMember_(body);
      touchDevice_(member.name, device, false);
    }
    return ok_(spec.fn(body, member, device));
  } catch (err) {
    if (err && err.userFacing) {
      var out = { ok: false, error: err.message, code: err.code };
      if (err.owner) out.owner = err.owner;
      return out;
    }
    console.error(err && err.stack || err);
    return { ok: false, error: '서버에서 문제가 생겼어요. 잠시 뒤 다시 해 주세요.', code: 'server' };
  }
}

function ok_(data) {
  data = data || {};
  data.ok = true;
  return data;
}

// ---- read cache: 30 s per file, dropped on every write to that file ---------------------

function cacheVersion_(role) {
  return CacheService.getScriptCache().get('ver:' + role) || '0';
}

function cached_(role, key, fn) {
  var cache = CacheService.getScriptCache();
  var k = role + ':' + cacheVersion_(role) + ':' + key;
  var hit = cache.get(k);
  if (hit) return JSON.parse(hit);
  var v = fn();
  try { cache.put(k, JSON.stringify(v), 30); } catch (e) { /* over 100 KB: not cached */ }
  return v;
}

function flushRole_(role) {
  CacheService.getScriptCache().put('ver:' + role, Utilities.getUuid().slice(0, 8), 21600);
  READ_CACHE_ = {};
}

function clearCaches_() {
  ROLES_.concat(['calendar']).forEach(flushRole_);
  CacheService.getScriptCache().removeAll(['config', 'ids', 'order_categories']);
}

// ---- batch: several reads in one call (the app fetches everything once when it opens) ---

// Reads only: no writes, and not notice.get (opening a notice marks it read).
var BATCH_READS_ = ['home', 'attendance.month', 'schedule.range', 'equipment.list', 'reservation.list',
                    'work.board', 'request.list', 'notice.list', 'order.list', 'budget.lab', 'budget.mine'];

/** action batch: {calls: [{action, data}]} → {results: [{ok, ...}]}, one result per call, in order. */
function batch_(body, member, device) {
  var acts = actions_();
  var calls = (body.calls || []).slice(0, 15);
  return {
    results: calls.map(function (c) {
      try {
        var action = String(c && c.action || '');
        if (BATCH_READS_.indexOf(action) < 0) throw fail_('알 수 없는 요청이에요.', 'bad_request');
        var b = Object.assign({}, (c && c.data) || {}, { action: action, device: body.device, key: body.key, member: body.member });
        return ok_(acts[action].fn(b, member, device));
      } catch (err) {
        if (!(err && err.userFacing)) console.error(err && err.stack || err);
        return { ok: false, error: err && err.userFacing ? err.message : '불러오지 못했어요.', code: err && err.code || 'server' };
      }
    })
  };
}

// ---- home (요약): everything the first screen needs, in one call -----------------------

/** Monday of the week that holds ymd ('YYYY-MM-DD'). */
function weekStart_(ymd) {
  var p = String(ymd).split('-');
  var dow = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])).getUTCDay();
  return addDays_(ymd, -((dow + 6) % 7));
}

function home_(body, member) {
  var today = today_(), from = weekStart_(today), to = addDays_(from, 6);
  var out = { member: member.name, now: stamp_(), week: { from: from, to: to } };
  var parts = {
    attendance: function () { return attendanceToday_(member); },
    // my duties this week, plus older ones that are still not done (first)
    duties: function () {
      return workData_().tasks.filter(function (t) {
        return !t.done && t.date && t.date <= to && includesName_(t.people, member.name);
      }).map(function (t) {
        return { id: t.id, title: t.title || t.project, project: t.project, date: t.date, overdue: t.date < today };
      }).filter(function (t) { return t.overdue || t.date >= from; });
    },
    // 업무 요청 to me that I have not checked yet
    asks: function () { return requestsToMe_(member); },
    // order requests where I am the person in charge, still open
    requests: function () {
      return orderList_({}, member).orders.filter(function (o) {
        return o.mineHandle && (o.state === '요청' || o.state === '주문함');
      }).map(function (o) {
        return { id: o.id, item: o.item, requester: o.requester, date: o.date, category: o.category, state: o.state };
      });
    },
    // calendar events about me + my equipment bookings, this week
    schedule: function () {
      var r = scheduleRange_({ from: from, to: to }, member);
      return r.items.filter(function (i) { return i.mine && i.kind !== 'work'; }).map(function (i) {
        return { kind: i.kind, id: i.id, title: i.kind === 'reservation' ? i.title.split(' · ')[0] : i.title,
                 start: i.start, end: i.end, allDay: i.allDay, calendar: i.calendar || '', purpose: i.purpose || '' };
      });
    },
    notices: function () {
      var n = noticeList_({}, member).notices;
      return { unread: n.filter(function (x) { return !x.read; }).length, latest: n.slice(0, 3) };
    },
    orders: function () {
      var o = orderList_({}, member).orders;
      return {
        handle: o.filter(function (x) { return x.mineHandle && (x.state === '요청' || x.state === '주문함'); }).length,
        mine: o.filter(function (x) { return x.mineRequest && (x.state === '요청' || x.state === '주문함'); }).length
      };
    }
  };
  Object.keys(parts).forEach(function (k) {
    try { out[k] = parts[k](); } catch (e) { out[k] = { error: e.userFacing ? e.message : '불러오지 못함' }; }
  });
  return out;
}
