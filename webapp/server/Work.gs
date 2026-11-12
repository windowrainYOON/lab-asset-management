/**
 * 업무 workbook (read only here): 프로젝트 (ID 이름 종류 담당자 주기 시작일 기간 업무 이름 메모 회차 인원)
 * and 업무 (ID 프로젝트 ID 프로젝트 날짜 담당자 업무 완료 완료 시각).
 * The current task of a project = its first task not done (by date); the next = the one after it.
 * 종류 '담당' = an owner duty: no dates, only the people in charge (not on the schedule).
 */

function workKind_(text) {
  var k = String(text || '');
  if (/담당|owner/i.test(k)) return 'owner';
  return /마일스톤|milestone/i.test(k) ? 'milestone' : 'rotation';
}

function workData_() {
  return cached_('work', 'all', function () {
    var book = book_('work');
    var projects = table_(book, '프로젝트').rows.filter(function (r) { return r.ID; }).map(function (r) {
      var kind = r['종류'] || '';
      return {
        id: r.ID, name: r['이름'], kind: workKind_(kind), memo: r['메모'] || '',
        freq: r['주기'] || '', duty: r['업무 이름'] || r['이름'], members: splitNames_(String(r['담당자'] || '').replace(/\n/g, ','))
      };
    });
    var tasks = table_(book, '업무').rows.filter(function (r) { return r.ID; }).map(function (r) {
      return {
        id: r.ID, projectId: r['프로젝트 ID'], project: r['프로젝트'], date: parseYmd_(r['날짜']) || '',
        people: splitNames_(String(r['담당자'] || '').replace(/\n/g, ',')), title: r['업무'] || '',
        done: truthy_(r['완료']) || /^(✔)$/.test(r['완료'] || '')
      };
    }).sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
    return { projects: projects, tasks: tasks };
  });
}

function includesName_(people, name) {
  return people.some(function (p) { return samePerson_(p, name); });
}

/** action work.board */
function workBoard_(body, member) {
  var d = workData_(), today = today_();
  var byProject = {};
  d.tasks.forEach(function (t) { (byProject[t.projectId] = byProject[t.projectId] || []).push(t); });
  var owners = d.projects.filter(function (p) { return p.kind === 'owner'; }).map(function (p) {
    return { id: p.id, name: p.name, people: p.members, memo: p.memo, mine: includesName_(p.members, member.name) };
  });
  var cards = d.projects.filter(function (p) { return p.kind !== 'owner'; }).map(function (p) {
    var open = (byProject[p.id] || []).filter(function (t) { return !t.done; });
    var card = { id: p.id, name: p.name, kind: p.kind, freq: p.freq, duty: p.duty,
                 current: open[0] || null, next: open[1] || null, remaining: open.length };
    if (p.kind === 'milestone') card.steps = open.slice(0, 6);
    card.mineNow = !!(card.current && includesName_(card.current.people, member.name));
    return card;
  });
  var mine = d.tasks.filter(function (t) {
    return !t.done && t.date >= today && includesName_(t.people, member.name);
  }).slice(0, 12);
  var overdue = d.tasks.filter(function (t) {
    return !t.done && t.date && t.date < today && includesName_(t.people, member.name);
  });
  return { today: today, projects: cards, owners: owners, mine: mine, overdue: overdue };
}

/** Tasks in a date range (for 일정). Owner duties have no dated tasks. */
function workBetween_(from, to) {
  var owner = {};
  workData_().projects.forEach(function (p) { if (p.kind === 'owner') owner[p.id] = true; });
  return workData_().tasks.filter(function (t) { return !owner[t.projectId]; }).filter(function (t) { return t.date >= from && t.date <= to; });
}
