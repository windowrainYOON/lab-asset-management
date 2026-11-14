/**
 * 출퇴근 기록 sheet: one tab per month "YYYY-MM" (날짜 | 요일 | one column per member), and a 설정
 * tab (출근 시간, 지각 허용(분), 근무 요일, 출근 자동 범위(분), 어제 퇴근 마감, 늦은 퇴근 기준).
 * A cell is the clock-in time "09:02", or clock-in and clock-out "09:02–19:10" (a clock-out earlier
 * than the clock-in is the next day's dawn: "09:02–03:40"), or a word (휴가, 출장 …).
 * Clock-out is optional; it only matters for the late-leave rule: leaving at or after 늦은 퇴근 기준
 * (03:00) the next morning means the next day's clock-in is not counted late, when both days are
 * 근무 요일. Same rules as the desktop app (app/core/attendance.py).
 */

var WEEK_ORDER_ = '월화수목금토일';      // Monday first, like the desktop app
var SPAN_SEP_ = '–';                    // en dash between clock-in and clock-out

function attSettings_(book) {
  var s = { start: '09:00', grace: 10, workdays: [1, 2, 3, 4, 5],   // 0 = 일 … 6 = 토
            window: 60, cutoff: '08:30', lateOut: '03:00' };
  var sh = book.getSheetByName('설정');
  if (!sh || sh.getLastRow() < 1) return s;
  sh.getRange(1, 1, sh.getLastRow(), 2).getDisplayValues().forEach(function (r) {
    var k = String(r[0]).trim(), v = String(r[1]).trim(), t;
    if (k === '출근 시간') { t = parseHm_(v); if (t) s.start = t; }
    else if (k.indexOf('지각 허용') === 0) { var n = parseInt(v.replace('분', ''), 10); if (n >= 0) s.grace = n; }
    else if (k.indexOf('출근 자동 범위') === 0) { var w = parseInt(v.replace('분', ''), 10); if (w >= 0) s.window = w; }
    else if (k === '어제 퇴근 마감') { t = parseHm_(v); if (t) s.cutoff = t; }
    else if (k === '늦은 퇴근 기준') { t = parseHm_(v); if (t) s.lateOut = t; }
    else if (k === '근무 요일' && v) {
      var days = [];
      for (var i = 0; i < v.length; i++) {
        var wd = WEEK_ORDER_.indexOf(v.charAt(i));
        if (wd >= 0) days.push((wd + 1) % 7);
      }
      if (days.length) s.workdays = days;
    }
  });
  return s;
}

/** '9:02', '09:02', '9시 2분', '09:02:00', '오전 9:02:00' → '09:02' (else null). */
function parseHm_(v) {
  var s = String(v || '');
  var m = s.match(/(\d{1,2})\s*[:시]\s*(\d{1,2})/);
  if (!m || +m[1] > 23 || +m[2] > 59) return null;
  var h = +m[1];
  if (/오후|PM/i.test(s) && h < 12) h += 12;
  if (/오전|AM/i.test(s) && h === 12) h = 0;
  return ('0' + h).slice(-2) + ':' + ('0' + m[2]).slice(-2);
}

/** A time cell → {in, out} ('09:02' / '09:02–19:10' / '9:02 ~ 3:40' …); a word → null. */
function parseSpan_(v) {
  var s = String(v == null ? '' : v).trim();
  if (!s) return null;
  var m = s.match(/^(.+?)\s*[–~\-]\s*(.+)$/);
  if (m && /\d\s*[:시]\s*\d/.test(m[1]) && parseHm_(m[1]) && parseHm_(m[2]) && s.length <= 25) {
    return { 'in': parseHm_(m[1]), out: parseHm_(m[2]) };
  }
  if (parseHm_(s) && s.length <= 11) return { 'in': parseHm_(s), out: '' };
  return null;
}

/** Cell as stored by the web app: '09:02', '09:02–19:10', or the word as it is. */
function normCell_(v) {
  var sp = parseSpan_(v);
  if (!sp) return String(v == null ? '' : v).trim();
  return sp.out ? sp['in'] + SPAN_SEP_ + sp.out : sp['in'];
}
function inOf_(v) { var sp = parseSpan_(v); return sp ? sp['in'] : ''; }
function outOf_(v) { var sp = parseSpan_(v); return sp ? sp.out : ''; }

function parseYmd_(v) {
  var m = String(v || '').match(/(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})/);
  if (!m) return null;
  return m[1] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2);
}

function hmMin_(t) { var p = t.split(':'); return +p[0] * 60 + +p[1]; }
function minHm_(x) { x = ((x % 1440) + 1440) % 1440; return ('0' + Math.floor(x / 60)).slice(-2) + ':' + ('0' + (x % 60)).slice(-2); }

function isLate_(t, s) { return hmMin_(t) > hmMin_(s.start) + s.grace; }

/** The cell of the day before decides: left at or after 늦은 퇴근 기준 the next dawn, both days 근무 요일. */
function lateLeave_(prevCell, prevYmd, s) {
  var sp = parseSpan_(prevCell);
  if (!sp || !sp.out || hmMin_(sp.out) >= hmMin_(sp['in'])) return false;      // no clock-out, or same evening
  if (hmMin_(sp.out) < hmMin_(s.lateOut)) return false;
  return s.workdays.indexOf(dow_(prevYmd)) >= 0 && s.workdays.indexOf(dow_(addDays_(prevYmd, 1))) >= 0;
}

/** Late, unless the day before was a late leave (then 'excused'). → 'late' | 'excused' | '' */
function lateState_(t, prevCell, prevYmd, s) {
  if (!t || !isLate_(t, s)) return '';
  return lateLeave_(prevCell, prevYmd, s) ? 'excused' : 'late';
}

/** Read one month tab: {headerRow, people:[{name,col}], days:{ymd:{row, cells:{name:value}}}} */
function readMonth_(sh) {
  var n = sh.getLastRow(), w = sh.getLastColumn();
  var out = { headerRow: 0, people: [], days: {}, width: w };
  if (n < 1 || w < 1) return out;
  var vals = sh.getRange(1, 1, n, w).getDisplayValues();
  for (var i = 0; i < Math.min(10, vals.length); i++) {
    if (String(vals[i][0]).trim() === '날짜') { out.headerRow = i + 1; break; }
  }
  if (!out.headerRow) return out;
  var head = vals[out.headerRow - 1];
  for (var c = 1; c < head.length; c++) {
    var name = String(head[c]).trim();
    if (name && name !== '요일') out.people.push({ name: name, col: c + 1 });
  }
  for (var r = out.headerRow; r < vals.length; r++) {
    var d = parseYmd_(vals[r][0]);
    if (!d) continue;
    var cells = {};
    out.people.forEach(function (p) {
      var v = normCell_(vals[r][p.col - 1]);
      if (v) cells[p.name] = v;
    });
    out.days[d] = { row: r + 1, cells: cells };
  }
  return out;
}

function monthDates_(ym) {
  var p = ym.split('-'), y = +p[0], m = +p[1];
  var last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  var out = [];
  for (var d = 1; d <= last; d++) out.push(ym + '-' + ('0' + d).slice(-2));
  return out;
}

/** Make the month tab: header copied from the latest earlier month tab (or the members), all dates. */
function createMonth_(book, ym) {
  var prev = book.getSheets().map(function (s) { return s.getName(); })
    .filter(function (n) { return /^\d{4}-\d{2}$/.test(n) && n < ym; }).sort().pop();
  var head;
  if (prev) {
    var pm = readMonth_(book.getSheetByName(prev));
    head = ['날짜', '요일'].concat(pm.people.map(function (p) { return p.name; }));
  }
  if (!head || head.length <= 2) head = ['날짜', '요일'].concat(members_().map(function (m) { return m.name; }));
  var after = prev ? book.getSheetByName(prev).getIndex() : book.getSheets().length;
  var sh = book.insertSheet(ym, after);
  var dates = monthDates_(ym);
  var rows = [head].concat(dates.map(function (d) {
    var r = [d, weekday_(d)];
    while (r.length < head.length) r.push('');
    return r;
  }));
  var rng = sh.getRange(1, 1, rows.length, head.length);
  rng.setNumberFormat('@');
  rng.setValues(rows);
  sh.getRange(1, 1, 1, head.length).setFontWeight('bold');
  sh.setFrozenRows(1);
  sh.setFrozenColumns(2);
  return sh;
}

/**
 * One person's cell of one day: {ym, sh, row, col, value}. make = create the month tab, the
 * person's column and the date row when missing (else sh/row/col may be 0 and value '').
 */
function attSlot_(book, ymd, member, device, make) {
  var ym = ymd.slice(0, 7);
  var sh = book.getSheetByName(ym);
  var slot = { ym: ym, ymd: ymd, sh: sh, row: 0, col: 0, value: '' };
  if (!sh) {
    if (!make) return slot;
    sh = slot.sh = createMonth_(book, ym);
    audit_(member.name, device, '월 탭 만들기', '출퇴근 기록', ym, null, { tab: ym });
  }
  var m = readMonth_(sh);
  m.people.forEach(function (p) { if (p.name === member.name) slot.col = p.col; });
  if (!slot.col && make) {
    slot.col = Math.max(m.width, 2) + 1;
    sh.getRange(m.headerRow, slot.col).setNumberFormat('@').setValue(member.name).setFontWeight('bold');
    sh.getRange(m.headerRow + 1, slot.col, Math.max(sh.getLastRow() - m.headerRow, 1), 1).setNumberFormat('@');
    audit_(member.name, device, '열 추가', '출퇴근 기록', ym, null, { column: member.name });
  }
  var info = m.days[ymd];
  slot.row = info ? info.row : 0;
  if (!slot.row && make) {
    slot.row = sh.getLastRow() + 1;
    sh.getRange(slot.row, 1, 1, 2).setNumberFormat('@').setValues([[ymd, weekday_(ymd)]]);
  }
  slot.value = info && slot.col ? (info.cells[member.name] || '') : '';
  return slot;
}

function setSlot_(slot, value) {
  slot.sh.getRange(slot.row, slot.col).setNumberFormat('@').setValue(value);
  slot.value = value;
}

/**
 * What a tap at time t means. → {kind, choices}
 * kind: 'in' (today's clock-in), 'out' (today's clock-out), 'out_y' (yesterday's clock-out),
 *       'already' (clocked in within the auto range, nothing to do), 'text' (a word in today's cell).
 * Before 어제 퇴근 마감 with a clock-in yesterday: yesterday's clock-out, unless it is inside the
 * clock-in range and today has no clock-in yet; both can be switched. Inside the clock-in range
 * (출근 시간 ± 출근 자동 범위) a first tap is the clock-in; a later tap after the range is a clock-out.
 */
function tapPlan_(t, today, yesterday, s) {
  var tm = hmMin_(t), st = hmMin_(s.start);
  var inD = inOf_(today), inY = inOf_(yesterday);
  if (tm < hmMin_(s.cutoff) && inY) {
    return { kind: !today && tm >= st - s.window ? 'in' : 'out_y', choices: ['out_y', 'in'] };
  }
  if (!today) return { kind: 'in', choices: [] };
  if (!inD) return { kind: 'text', choices: [] };
  if (tm <= st + s.window) return { kind: 'already', choices: [] };
  return { kind: 'out', choices: ['in', 'out'] };
}

/** Do one kind for the tap: writes at most one cell. → the answer for the phone. */
function tapApply_(ctx, kind) {
  var s = ctx.settings, t = ctx.t, d = ctx.today, y = ctx.yesterday, who = ctx.member.name;
  var r = { kind: kind, time: t, day: d.ymd, inTime: '', outTime: '', late: '', lateLeave: false, text: '' };
  if (kind === 'in') {
    if (d.value && !inOf_(d.value)) { kind = r.kind = 'text'; }
    else if (inOf_(d.value)) { kind = r.kind = 'already'; }
  }
  if (kind === 'in') {
    setSlot_(d, t);
    audit_(who, ctx.device, '출근', '출퇴근 기록', d.ymd, null, { time: t });
    r.result = '기록';
    r.late = lateState_(t, y.value, y.ymd, s);
    r.message = t + ' 출근' + (r.late === 'late' ? ' (지각)' : r.late === 'excused' ? ' (늦은 퇴근 다음날이라 지각 아님)' : ' 기록됨');
  } else if (kind === 'out') {
    var before = d.value, nv = inOf_(d.value) + SPAN_SEP_ + t;
    setSlot_(d, nv);
    audit_(who, ctx.device, '퇴근', '출퇴근 기록', d.ymd, { value: before }, { value: nv });
    r.result = '기록'; r.inTime = inOf_(nv); r.outTime = t;
    r.message = t + ' 퇴근 기록됨';
  } else if (kind === 'out_y') {
    var yb = y.value, yv = inOf_(y.value) + SPAN_SEP_ + t;
    setSlot_(y, yv);
    audit_(who, ctx.device, '퇴근(전날)', '출퇴근 기록', y.ymd, { value: yb }, { value: yv });
    r.result = '기록'; r.day = y.ymd; r.inTime = inOf_(yv); r.outTime = t;
    r.lateLeave = lateLeave_(yv, y.ymd, s);
    r.message = '어제 ' + t + ' 퇴근 기록됨' + (r.lateLeave ? '. 오늘은 지각으로 세지 않아요.' : '');
  } else if (kind === 'already') {
    r.result = '이미 출근'; r.time = inOf_(d.value);
    r.late = lateState_(r.time, y.value, y.ymd, s);
    r.message = '이미 ' + r.time + '에 출근했어요.';
  } else {
    r.result = '텍스트 있음'; r.time = ''; r.text = d.value;
    r.message = '오늘 칸에 "' + d.value + '"이(가) 적혀 있어 그대로 뒀어요.';
  }
  return r;
}

function tapContext_(book, member, device, today, t) {
  var yday = addDays_(today, -1);
  return { settings: attSettings_(book), member: member, device: device, t: t,
           today: attSlot_(book, today, member, device, true),
           yesterday: attSlot_(book, yday, member, device, false) };
}

/** action tap: {picc, cmac} → {result, kind, choices, time, day, inTime, outTime, late, lateLeave, message} */
function tap_(body, member, device) {
  var uid = '', counter = '';
  if (body.picc || body.cmac) {
    var key = prop_('SDM_KEY');
    try {
      var tag = sdmVerify_(body.picc, body.cmac, key, prop_('SDM_MAC_KEY'));
      uid = tag.uid; counter = tag.counter;
    } catch (e) {
      log_('거부', member.name, device, '', '', e.message);
      throw e;
    }
  } else if (prop_('ALLOW_PLAIN_TAP') !== '1') {
    throw fail_('출퇴근은 연구실 태그를 찍어야 기록돼요.', 'need_tag');
  }

  return withLock_(function () {
    if (uid) {
      try { tagCounter_(uid, counter); }
      catch (e) { log_('거부', member.name, device, uid, counter, e.message); throw e; }
    }
    var cache = CacheService.getScriptCache();
    var recent = cache.get('tap:' + member.name);
    if (recent) {      // the phone read the tag twice: show the same answer, write nothing
      log_('중복', member.name, device, uid, counter, '');
      var again = JSON.parse(recent);
      again.repeat = true;
      return again;
    }
    var day = today_(), t = hhmm_();
    var book = book_('attendance');
    var ctx = tapContext_(book, member, device, day, t);
    var plan = tapPlan_(t, ctx.today.value, ctx.yesterday.value, ctx.settings);
    var snap = [ctx.today, ctx.yesterday].filter(function (x) { return x.sh && x.row && x.col; })
      .map(function (x) { return { ymd: x.ymd, value: x.value }; });
    var r = tapApply_(ctx, plan.kind);
    r.choices = r.kind === plan.kind ? plan.choices : [];
    cache.put('tap:' + member.name, JSON.stringify(r), 60);
    if (r.choices.length) {
      cache.put('tapundo:' + member.name, JSON.stringify({ today: day, t: t, choices: r.choices, kind: r.kind, snap: snap }), 600);
    } else cache.remove('tapundo:' + member.name);
    log_(r.result === '기록' ? r.kind === 'in' ? '출근' : '퇴근' : r.result, member.name, device, uid, counter, '');
    flushRole_('attendance');
    return r;
  });
}

/** action tap.switch: {kind} — right after a tap, record it as the other choice (10 minutes). */
function tapSwitch_(body, member, device) {
  return withLock_(function () {
    var cache = CacheService.getScriptCache();
    var raw = cache.get('tapundo:' + member.name);
    if (!raw) throw fail_('바꿀 수 있는 시간이 지났어요. 태그를 다시 찍어 주세요.', 'switch_expired');
    var u = JSON.parse(raw);
    if (u.choices.indexOf(body.kind) < 0) throw fail_('이 태그는 그렇게 바꿀 수 없어요.', 'bad_kind');
    var book = book_('attendance');
    var ctx = tapContext_(book, member, device, u.today, u.t);
    if (body.kind !== u.kind) {
      // put the cells back as they were before the tap, then record the other choice
      [ctx.today, ctx.yesterday].forEach(function (slot) {
        u.snap.forEach(function (x) {
          if (x.ymd === slot.ymd && slot.sh && slot.row && slot.col && slot.value !== x.value) setSlot_(slot, x.value);
        });
      });
      audit_(member.name, device, '태그 바꾸기', '출퇴근 기록', u.today, { kind: u.kind }, { kind: body.kind });
    }
    var r = tapApply_(ctx, body.kind);
    r.choices = u.choices;
    u.kind = body.kind;
    cache.put('tapundo:' + member.name, JSON.stringify(u), 600);
    cache.put('tap:' + member.name, JSON.stringify(r), 60);
    log_('바꾸기', member.name, device, '', '', body.kind);
    flushRole_('attendance');
    return r;
  });
}

// ---- calendar days that do not count ---------------------------------------------------

function eventDays_(ev) {
  var s, e;
  if (ev.start && ev.start.date) {
    s = ev.start.date; e = addDays_(ev.end && ev.end.date ? ev.end.date : addDays_(s, 1), -1);
  } else if (ev.start && ev.start.dateTime) {
    var sd = new Date(ev.start.dateTime), ed = new Date(ev.end ? ev.end.dateTime : ev.start.dateTime);
    s = fmt_(sd, 'yyyy-MM-dd');
    ed = new Date(ed.getTime() - 1);
    e = fmt_(ed, 'yyyy-MM-dd');
    if (e < s) e = s;
  } else return [];
  var out = [];
  for (var d = s; d <= e && out.length < 400; d = addDays_(d, 1)) out.push(d);
  return out;
}

function listEvents_(calId, from, to) {
  var out = [], token = null;
  do {
    var resp = Calendar.Events.list(calId, {
      timeMin: dateOf_(from).toISOString(), timeMax: dateOf_(addDays_(to, 1)).toISOString(),
      singleEvents: true, orderBy: 'startTime', maxResults: 250, pageToken: token
    });
    (resp.items || []).forEach(function (ev) { if (ev.status !== 'cancelled') out.push(ev); });
    token = resp.nextPageToken;
  } while (token);
  return out;
}

/** Lab calendars chosen in the desktop app: [{id, name, color}] */
function labCalendars_() {
  var c = config_().calendar || {};
  if (c.calendars && c.calendars.length) return c.calendars.filter(function (x) { return x && x.id; });
  if (c.id) return [{ id: c.id, name: c.name || '', color: '#3949ab' }];
  return [];
}

/** {holidays:{ymd:name}, excluded:{ymd:title}} for from..to, cached 6 h. */
function offDays_(from, to) {
  var cache = CacheService.getScriptCache();
  var key = 'off:' + from + ':' + to;
  var hit = cache.get(key);
  if (hit) return JSON.parse(hit);
  var holidays = {}, excluded = {};
  for (var i = 0; i < HOLIDAY_CALENDARS.length; i++) {
    try {
      listEvents_(HOLIDAY_CALENDARS[i], from, to).forEach(function (ev) {
        var desc = String(ev.description || '').toLowerCase();
        if (desc.indexOf('기념일') >= 0 || desc.indexOf('observance') >= 0) return;
        eventDays_(ev).forEach(function (d) { if (!holidays[d]) holidays[d] = ev.summary || '공휴일'; });
      });
      break;
    } catch (e) { /* try the other holiday calendar */ }
  }
  labCalendars_().forEach(function (c) {
    try {
      listEvents_(c.id, from, to).forEach(function (ev) {
        var p = ev.extendedProperties && ev.extendedProperties['private'];
        if (p && String(p.am_attendance_exclude) === '1') {
          eventDays_(ev).forEach(function (d) { if (!excluded[d]) excluded[d] = ev.summary || '제외'; });
        }
      });
    } catch (e) { /* a calendar the owner cannot see */ }
  });
  var out = { holidays: holidays, excluded: excluded };
  cache.put(key, JSON.stringify(out), 21600);
  return out;
}

function dayCounts_(ymd, s, off) {
  if (off.holidays[ymd]) return { work: false, reason: off.holidays[ymd], kind: 'holiday' };
  if (off.excluded[ymd]) return { work: false, reason: off.excluded[ymd], kind: 'event' };
  var w = dow_(ymd);
  if (s.workdays.indexOf(w) < 0) return { work: false, reason: w === 0 || w === 6 ? '주말' : '근무 요일 아님', kind: 'weekend' };
  return { work: true };
}

/** cellOf(ymd, name) for this month plus the last day of the month before (for the late-leave rule). */
function monthCells_(book, ym, month) {
  var prevYm = ym_(dateOf_(addDays_(ym + '-01', -1)));
  var prevSh = book.getSheetByName(prevYm);
  var prev = prevSh ? readMonth_(prevSh) : { days: {} };
  return function (ymd, name) {
    var m = ymd.slice(0, 7) === ym ? month : prev;
    return m.days[ymd] ? m.days[ymd].cells[name] || '' : '';
  };
}

function personStats_(cellOf, ym, name, s, off, today) {
  var st = { workdays: 0, present: 0, late: 0, ontime: 0, excused: 0, lateLeave: 0, avg: '' };
  var mins = [];
  monthDates_(ym).forEach(function (d) {
    var v = cellOf(d, name);
    if (lateLeave_(v, d, s)) st.lateLeave++;
    if (d > today || !dayCounts_(d, s, off).work) return;
    st.workdays++;
    var t = inOf_(v);
    if (t) {
      st.present++;
      mins.push(hmMin_(t));
      var ls = lateState_(t, cellOf(addDays_(d, -1), name), addDays_(d, -1), s);
      if (ls === 'late') st.late++; else st.ontime++;
      if (ls === 'excused') st.excused++;
    }
  });
  if (mins.length) {
    var a = Math.round(mins.reduce(function (x, y) { return x + y; }, 0) / mins.length);
    st.avg = minHm_(a);
  }
  return st;
}

/** action attendance.month: {ym} → whole month for everyone. */
function attendanceMonth_(body, member) {
  var ym = /^\d{4}-\d{2}$/.test(body.ym || '') ? body.ym : ym_();
  return cached_('attendance', 'month:' + ym, function () {
    var book = book_('attendance');
    var s = attSettings_(book);
    var sh = book.getSheetByName(ym);
    var month = sh ? readMonth_(sh) : { people: [], days: {} };
    var cellOf = monthCells_(book, ym, month);
    var dates = monthDates_(ym);
    var off = offDays_(dates[0], dates[dates.length - 1]);
    var today = today_();
    var people = month.people.map(function (p) { return p.name; });
    if (!sh) people = members_().map(function (m) { return m.name; });
    return {
      ym: ym, settings: s, people: people, today: today,
      days: dates.map(function (d) {
        var info = dayCounts_(d, s, off);
        var late = {};
        people.forEach(function (n) {
          var ls = lateState_(inOf_(cellOf(d, n)), cellOf(addDays_(d, -1), n), addDays_(d, -1), s);
          if (ls) late[n] = ls;
        });
        return { date: d, dow: weekday_(d), work: info.work, reason: info.reason || '', kind: info.kind || '',
                 cells: month.days[d] ? month.days[d].cells : {}, late: late };
      }),
      stats: people.reduce(function (o, n) { o[n] = personStats_(cellOf, ym, n, s, off, today); return o; }, {})
    };
  });
}

/** Today for the home screen: my cell, lab count, my month stats. */
function attendanceToday_(member) {
  var m = attendanceMonth_({ ym: ym_() }, member);
  var today = m.today, row = null;
  m.days.forEach(function (d) { if (d.date === today) row = d; });
  var present = 0, late = 0;
  m.people.forEach(function (p) {
    var v = row && row.cells[p];
    if (inOf_(v)) { present++; if (row.late[p] === 'late') late++; }
  });
  var cell = row ? row.cells[member.name] || '' : '';
  var mine = inOf_(cell) || cell;
  var ls = row ? row.late[member.name] || '' : '';
  return {
    today: today, dow: weekday_(today), work: row ? row.work : true, reason: row ? row.reason : '',
    mine: mine, mineOut: outOf_(cell), mineLate: ls === 'late', mineExcused: ls === 'excused',
    present: present, late: late, total: m.people.length,
    deadline: minHm_(hmMin_(m.settings.start) + m.settings.grace),
    stats: m.stats[member.name] || null
  };
}
