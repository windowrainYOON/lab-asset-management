/**
 * 출퇴근 기록 sheet: one tab per month "YYYY-MM" (날짜 | 요일 | one column per member), and a 설정
 * tab (출근 시간, 지각 허용(분), 근무 요일). A tap writes the clock-in time "HH:MM" into an empty
 * cell only. Same rules as the desktop app (app/core/attendance.py).
 */

var WEEK_ORDER_ = '월화수목금토일';      // Monday first, like the desktop app

function attSettings_(book) {
  var s = { start: '09:00', grace: 10, workdays: [1, 2, 3, 4, 5] };   // 0 = 일 … 6 = 토
  var sh = book.getSheetByName('설정');
  if (!sh || sh.getLastRow() < 1) return s;
  sh.getRange(1, 1, sh.getLastRow(), 2).getDisplayValues().forEach(function (r) {
    var k = String(r[0]).trim(), v = String(r[1]).trim();
    if (k === '출근 시간') { var t = parseHm_(v); if (t) s.start = t; }
    else if (k.indexOf('지각 허용') === 0) { var n = parseInt(v.replace('분', ''), 10); if (n >= 0) s.grace = n; }
    else if (k === '근무 요일' && v) {
      var days = [];
      for (var i = 0; i < v.length; i++) {
        var w = WEEK_ORDER_.indexOf(v.charAt(i));
        if (w >= 0) days.push((w + 1) % 7);
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

function parseYmd_(v) {
  var m = String(v || '').match(/(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})/);
  if (!m) return null;
  return m[1] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2);
}

function hmMin_(t) { var p = t.split(':'); return +p[0] * 60 + +p[1]; }

function isLate_(t, s) { return hmMin_(t) > hmMin_(s.start) + s.grace; }

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
      var v = String(vals[r][p.col - 1] || '').trim();
      if (v) cells[p.name] = parseHm_(v) && v.length <= 11 ? parseHm_(v) : v;
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

/** action tap: {picc, cmac} → {result, time, message} */
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
    throw fail_('출근은 연구실 태그를 찍어야 기록돼요.', 'need_tag');
  }

  return withLock_(function () {
    if (uid) {
      try { tagCounter_(uid, counter); }
      catch (e) { log_('거부', member.name, device, uid, counter, e.message); throw e; }
    }
    var cache = CacheService.getScriptCache();
    var day = today_(), ym = day.slice(0, 7), t = hhmm_();
    var recent = cache.get('tap:' + member.name);

    var book = book_('attendance');
    var sh = book.getSheetByName(ym);
    var made = false;
    if (!sh) {
      sh = createMonth_(book, ym);
      made = true;
      audit_(member.name, device, '월 탭 만들기', '출퇴근 기록', ym, null, { tab: ym });
    }
    var m = readMonth_(sh);
    var col = 0;
    m.people.forEach(function (p) { if (p.name === member.name) col = p.col; });
    if (!col) {
      col = Math.max(m.width, 2) + 1;
      sh.getRange(m.headerRow, col).setNumberFormat('@').setValue(member.name).setFontWeight('bold');
      sh.getRange(m.headerRow + 1, col, Math.max(sh.getLastRow() - m.headerRow, 1), 1).setNumberFormat('@');
      audit_(member.name, device, '열 추가', '출퇴근 기록', ym, null, { column: member.name });
    }
    var rowInfo = m.days[day];
    var row = rowInfo ? rowInfo.row : 0;
    if (!row) {
      row = sh.getLastRow() + 1;
      sh.getRange(row, 1, 1, 2).setNumberFormat('@').setValues([[day, weekday_(day)]]);
    }
    var cur = rowInfo ? (rowInfo.cells[member.name] || '') : '';

    var result, message;
    if (!cur) {
      if (recent) { result = '중복'; message = '방금 기록했어요.'; }
      else {
        sh.getRange(row, col).setNumberFormat('@').setValue(t);
        audit_(member.name, device, '출근', '출퇴근 기록', day, null, { time: t });
        result = '기록'; cur = t;
        message = isLate_(t, attSettings_(book)) ? t + ' 출근 (지각)' : t + ' 출근 기록됨';
      }
    } else if (parseHm_(cur) === cur) {
      result = '이미 출근'; message = '이미 ' + cur + '에 출근했어요.';
    } else {
      result = '텍스트 있음'; message = '오늘 칸에 "' + cur + '"이(가) 적혀 있어 그대로 뒀어요.';
    }
    cache.put('tap:' + member.name, '1', 60);
    log_(result, member.name, device, uid, counter, made ? '월 탭 생성' : '');
    flushRole_('attendance');
    return { result: result, time: parseHm_(cur) === cur ? cur : '', text: parseHm_(cur) === cur ? '' : cur,
             message: message, day: day };
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

function personStats_(month, ym, name, s, off, today) {
  var st = { workdays: 0, present: 0, late: 0, ontime: 0, avg: '' };
  var mins = [];
  monthDates_(ym).forEach(function (d) {
    if (d > today || !dayCounts_(d, s, off).work) return;
    st.workdays++;
    var v = month.days[d] && month.days[d].cells[name];
    if (v && parseHm_(v) === v) {
      st.present++;
      mins.push(hmMin_(v));
      if (isLate_(v, s)) st.late++; else st.ontime++;
    }
  });
  if (mins.length) {
    var a = Math.round(mins.reduce(function (x, y) { return x + y; }, 0) / mins.length);
    st.avg = ('0' + Math.floor(a / 60)).slice(-2) + ':' + ('0' + (a % 60)).slice(-2);
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
    var dates = monthDates_(ym);
    var off = offDays_(dates[0], dates[dates.length - 1]);
    var today = today_();
    var people = month.people.map(function (p) { return p.name; });
    if (!sh) people = members_().map(function (m) { return m.name; });
    return {
      ym: ym, settings: s, people: people, today: today,
      days: dates.map(function (d) {
        var info = dayCounts_(d, s, off);
        return { date: d, dow: weekday_(d), work: info.work, reason: info.reason || '', kind: info.kind || '',
                 cells: month.days[d] ? month.days[d].cells : {} };
      }),
      stats: people.reduce(function (o, n) { o[n] = personStats_(month, ym, n, s, off, today); return o; }, {})
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
    if (v && parseHm_(v) === v) { present++; if (isLate_(v, m.settings)) late++; }
  });
  var mine = row ? row.cells[member.name] || '' : '';
  return {
    today: today, dow: weekday_(today), work: row ? row.work : true, reason: row ? row.reason : '',
    mine: mine, mineLate: mine && parseHm_(mine) === mine ? isLate_(mine, m.settings) : false,
    present: present, late: late, total: m.people.length,
    deadline: (function () { var x = hmMin_(m.settings.start) + m.settings.grace; return ('0' + Math.floor(x / 60)).slice(-2) + ':' + ('0' + x % 60).slice(-2); })(),
    stats: m.stats[member.name] || null
  };
}
