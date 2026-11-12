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
  'tap': { fn: tap_ },
  'attendance.month': { fn: attendanceMonth_ },
  'schedule.range': { fn: scheduleRange_ },
  'equipment.list': { fn: function () { return equipmentList_(); } },
  'reservation.list': { fn: reservationList_ },
  'reservation.create': { fn: reservationCreate_ },
  'reservation.update': { fn: reservationUpdate_ },
  'reservation.cancel': { fn: reservationCancel_ },
  'work.board': { fn: workBoard_ },
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

// ---- home: everything the first screen needs, in one call -------------------------------

function home_(body, member) {
  var out = { member: member.name, now: stamp_() };
  var parts = {
    attendance: function () { return attendanceToday_(member); },
    reservations: function () {
      var today = today_();
      return reservationList_({ from: today, to: addDays_(today, 6) }, member).reservations
        .filter(function (r) { return r.mine && r.end > stamp_(); }).slice(0, 3);
    },
    work: function () {
      var w = workBoard_({}, member);
      return { mine: w.mine.slice(0, 3), overdue: w.overdue.length };
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
