/**
 * 일정: lab Google calendars (settings JSON calendar.calendars) + 장비 예약 + 업무, for a date range.
 */

function evTime_(t) {
  if (!t) return '';
  if (t.date) return t.date;
  return stamp_(new Date(t.dateTime));
}

/** action schedule.range: {from, to} (at most 42 days) */
function scheduleRange_(body, member) {
  var from = /^\d{4}-\d{2}-\d{2}$/.test(body.from || '') ? body.from : today_();
  var to = /^\d{4}-\d{2}-\d{2}$/.test(body.to || '') ? body.to : addDays_(from, 6);
  if (to < from) to = from;
  if (dateOf_(to).getTime() - dateOf_(from).getTime() > 42 * 86400000) to = addDays_(from, 42);
  var items = [], errors = [];

  var events = cached_('calendar', 'ev:' + from + ':' + to, function () {
    var out = [], errs = [];
    labCalendars_().forEach(function (c) {
      try {
        listEvents_(c.id, from, to).forEach(function (ev) {
          out.push({
            kind: 'event', id: ev.id, title: ev.summary || '(제목 없음)', allDay: !!(ev.start && ev.start.date),
            start: evTime_(ev.start), end: ev.start && ev.start.date ? addDays_(ev.end.date, -1) : evTime_(ev.end),
            calendar: c.name || '', color: c.color || '#3949ab', location: ev.location || '',
            description: String(ev.description || '').slice(0, 2000),
            attendees: (ev.attendees || []).map(function (a) { return (a.email || '').toLowerCase(); })
          });
        });
      } catch (e) { errs.push((c.name || '캘린더') + ': 볼 수 없음'); }
    });
    return { list: out, errors: errs };
  });
  errors = events.errors;
  var email = String(member.email || '').toLowerCase();
  events.list.forEach(function (ev) {
    var e = JSON.parse(JSON.stringify(ev));
    e.mine = e.title.indexOf(member.name) >= 0 || (!!email && e.attendees.indexOf(email) >= 0);
    delete e.attendees;
    items.push(e);
  });

  reservationList_({ from: from, to: to }, member).reservations.forEach(function (r) {
    var eq = null;
    equipmentList_().equipment.forEach(function (x) { if (x.id === r.equipmentId) eq = x; });
    items.push({ kind: 'reservation', id: r.id, title: r.equipment + ' · ' + r.person, start: r.start, end: r.end,
                 allDay: false, color: eq ? eq.color : '#0a84ff', purpose: r.purpose, person: r.person,
                 equipmentId: r.equipmentId, mine: r.mine });
  });

  workBetween_(from, to).forEach(function (t) {
    items.push({ kind: 'work', id: t.id, title: t.title || t.project, project: t.project, start: t.date, end: t.date,
                 allDay: true, color: '#30a46c', people: t.people, done: t.done,
                 mine: includesName_(t.people, member.name) });
  });

  items.sort(function (a, b) {
    if (a.start.slice(0, 10) !== b.start.slice(0, 10)) return a.start < b.start ? -1 : 1;
    if (a.allDay !== b.allDay) return a.allDay ? -1 : 1;
    return a.start < b.start ? -1 : a.start > b.start ? 1 : 0;
  });
  return { from: from, to: to, items: items, errors: errors };
}
