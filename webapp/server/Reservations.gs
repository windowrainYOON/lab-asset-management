/**
 * 장비 예약 workbook: 장비 (ID 이름 위치 담당자 상태 색 단위(분) 최대(시간) 메모) and
 * 예약 (ID 장비 ID 장비 예약자 시작 끝 용도 알림(분) 만든 시각). Same rules as the desktop app
 * (app/core/reservations.py): no booking on 점검 중, times on the unit, length ≤ max, no overlap.
 * Anyone may change or cancel any booking (trust based); cancel deletes the row, and the audit log
 * keeps the whole row.
 */

var EQ_COLORS_ = ['#0a84ff', '#30a46c', '#f76b15', '#8e4ec6', '#12a594', '#e5484d', '#ffb224', '#3e63dd'];

function samePerson_(a, b) {
  a = String(a || '').trim().toLowerCase(); b = String(b || '').trim().toLowerCase();
  return !!a && a === b;
}

function equipment_() {
  return table_(book_('reservations'), '장비').rows.map(function (r, i) {
    var step = parseInt(r['단위(분)'], 10);
    var max = parseFloat(r['최대(시간)']);
    return {
      id: r.ID, name: r['이름'], location: r['위치'] || '', owner: r['담당자'] || '',
      state: r['상태'] || '사용 가능', color: r['색'] || EQ_COLORS_[i % EQ_COLORS_.length],
      step: step > 0 ? step : 30, maxHours: max > 0 ? max : 0, note: r['메모'] || ''
    };
  }).filter(function (e) { return e.id && e.name; });
}

function normTime_(s) {
  var m = String(s || '').trim().replace('T', ' ').match(/^(\d{4})[-.](\d{1,2})[-.](\d{1,2})\s+(\d{1,2}):(\d{2})/);
  if (!m) return '';
  return m[1] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2) + ' ' + ('0' + m[4]).slice(-2) + ':' + m[5];
}

function resRow_(r) {
  return {
    id: r.ID, equipmentId: r['장비 ID'], equipment: r['장비'], person: r['예약자'],
    start: normTime_(r['시작']), end: normTime_(r['끝']), purpose: r['용도'] || '',
    notify: parseInt(r['알림(분)'], 10) || 0, created: r['만든 시각'] || ''
  };
}

function reservations_() {
  return table_(book_('reservations'), '예약').rows.map(resRow_)
    .filter(function (r) { return r.id && r.start && r.end; })
    .sort(function (a, b) { return a.start < b.start ? -1 : a.start > b.start ? 1 : 0; });
}

/** action equipment.list */
function equipmentList_() {
  return cached_('reservations', 'equipment', function () { return { equipment: equipment_() }; });
}

/** action reservation.list: {from, to} days (default today) → bookings that touch those days. */
function reservationList_(body, member) {
  var from = /^\d{4}-\d{2}-\d{2}$/.test(body.from || '') ? body.from : today_();
  var to = /^\d{4}-\d{2}-\d{2}$/.test(body.to || '') ? body.to : from;
  var all = cached_('reservations', 'all', function () { return { list: reservations_(), equipment: equipment_() }; });
  var s = from + ' 00:00', e = addDays_(to, 1) + ' 00:00';
  return {
    from: from, to: to, equipment: all.equipment, now: stamp_(),
    reservations: all.list.filter(function (r) { return r.start < e && s < r.end; }).map(function (r) {
      r.mine = samePerson_(r.person, member.name);
      return r;
    })
  };
}

function minutesOfDay_(t) { return +t.slice(11, 13) * 60 + +t.slice(14, 16); }

/** Error text when the booking breaks a rule, else ''. */
function resProblem_(res, eq, all, old) {
  if (!eq) return '장비를 찾지 못했어요.';
  if (!res.start || !res.end) return '시작과 끝 시각을 넣어 주세요.';
  if (res.end <= res.start) return '끝 시각이 시작보다 늦어야 해요.';
  if (!old && eq.state === '점검 중') return eq.name + '은(는) 점검 중이라 예약할 수 없어요.';
  if (minutesOfDay_(res.start) % eq.step || minutesOfDay_(res.end) % eq.step) {
    return eq.name + ' 예약은 ' + eq.step + '분 단위예요.';
  }
  var hours = (timeOf_(res.end).getTime() - timeOf_(res.start).getTime()) / 3600000;
  if (eq.maxHours && hours > eq.maxHours + 1e-9) return eq.name + '은(는) 한 번에 ' + eq.maxHours + '시간까지 예약할 수 있어요.';
  var now = stamp_();
  if (!old && res.start < now) return '지난 시각에는 예약할 수 없어요.';
  if (old) {
    if (old.start <= now) {
      if (res.start !== old.start || res.equipmentId !== old.equipmentId) return '이미 시작한 예약은 끝 시각만 바꿀 수 있어요.';
      if (res.end < now) return '끝 시각을 지금보다 앞으로 당길 수는 없어요.';
    } else if (res.start < now) return '지난 시각으로 옮길 수 없어요.';
  }
  var clash = null;
  all.forEach(function (o) {
    if (!clash && o.equipmentId === res.equipmentId && o.id !== res.id && res.start < o.end && o.start < res.end) clash = o;
  });
  if (clash) return clash.person + '님 예약(' + clash.start.slice(11) + '–' + clash.end.slice(11) + ')과 겹쳐요.';
  return '';
}

function needConfirm_(owner, member, body, what) {
  if (!samePerson_(owner, member.name) && !body.confirm) {
    var e = fail_(owner + '님의 ' + what + '이에요. 그래도 바꿀까요?', 'confirm');
    e.owner = owner;
    throw e;
  }
}

/** action reservation.create: {equipmentId, start, end, purpose, notify} */
function reservationCreate_(body, member, device) {
  return withLock_(function () {
    var t = table_(book_('reservations'), '예약');
    var eq = equipment_().filter(function (e) { return e.id === body.equipmentId; })[0];
    var res = {
      id: newId_(t.ids()), equipmentId: body.equipmentId, equipment: eq ? eq.name : '',
      person: member.name, start: normTime_(body.start), end: normTime_(body.end),
      purpose: String(body.purpose || '').slice(0, 300), notify: Math.max(0, parseInt(body.notify, 10) || 0)
    };
    var problem = resProblem_(res, eq, t.rows.map(resRow_), null);
    if (problem) throw fail_(problem, 'rule');
    var rec = {
      'ID': res.id, '장비 ID': res.equipmentId, '장비': res.equipment, '예약자': res.person,
      '시작': res.start, '끝': res.end, '용도': res.purpose, '알림(분)': res.notify ? String(res.notify) : '',
      '만든 시각': stamp_()
    };
    t.append(rec);
    audit_(member.name, device, '예약 만들기', '장비 예약', res.id, null, rec);
    flushRole_('reservations');
    res.mine = true;
    return { reservation: res };
  });
}

/** action reservation.update: {id, start, end, purpose, notify, equipmentId?, confirm?} */
function reservationUpdate_(body, member, device) {
  return withLock_(function () {
    var t = table_(book_('reservations'), '예약');
    var rec = t.byId(body.id);
    if (!rec) throw fail_('그 사이에 지워진 예약이에요.', 'gone');
    var old = resRow_(rec);
    needConfirm_(old.person, member, body, '예약');
    var eqId = body.equipmentId || old.equipmentId;
    var eq = equipment_().filter(function (e) { return e.id === eqId; })[0];
    var res = {
      id: old.id, equipmentId: eqId, equipment: eq ? eq.name : old.equipment, person: old.person,
      start: body.start !== undefined ? normTime_(body.start) : old.start,
      end: body.end !== undefined ? normTime_(body.end) : old.end,
      purpose: body.purpose !== undefined ? String(body.purpose).slice(0, 300) : old.purpose,
      notify: body.notify !== undefined ? Math.max(0, parseInt(body.notify, 10) || 0) : old.notify
    };
    var problem = resProblem_(res, eq, t.rows.map(resRow_), old);
    if (problem) throw fail_(problem, 'rule');
    var ch = t.update(old.id, {
      '장비 ID': res.equipmentId, '장비': res.equipment, '시작': res.start, '끝': res.end,
      '용도': res.purpose, '알림(분)': res.notify ? String(res.notify) : ''
    });
    if (Object.keys(ch.after).length) audit_(member.name, device, '예약 고치기', '장비 예약', old.id, ch.before, ch.after);
    flushRole_('reservations');
    res.mine = samePerson_(res.person, member.name);
    return { reservation: res };
  });
}

/** action reservation.cancel: {id, confirm?} — deletes the row. */
function reservationCancel_(body, member, device) {
  return withLock_(function () {
    var t = table_(book_('reservations'), '예약');
    var rec = t.byId(body.id);
    if (!rec) throw fail_('이미 취소된 예약이에요.', 'gone');
    needConfirm_(rec['예약자'], member, body, '예약');
    var removed = t.remove(body.id);
    delete removed._row;
    audit_(member.name, device, '예약 취소(행 삭제)', '장비 예약', body.id, removed, null);
    flushRole_('reservations');
    return { cancelled: body.id };
  });
}
