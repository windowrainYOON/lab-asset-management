/**
 * 업무 workbook (read only here): 프로젝트 (ID 이름 종류 담당자 주기 시작일 기간 업무 이름 메모 회차 인원)
 * and 업무 (ID 프로젝트 ID 프로젝트 날짜 담당자 업무 완료 완료 시각).
 * The current task of a project = its first task not done (by date); the next = the one after it.
 */

function workData_() {
  return cached_('work', 'all', function () {
    var book = book_('work');
    var projects = table_(book, '프로젝트').rows.filter(function (r) { return r.ID; }).map(function (r) {
      var kind = r['종류'] || '';
      return {
        id: r.ID, name: r['이름'], kind: /마일스톤|milestone/i.test(kind) ? 'milestone' : 'rotation',
        freq: r['주기'] || '', duty: r['업무 이름'] || r['이름'], members: splitNames_(r['담당자'])
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
  var cards = d.projects.map(function (p) {
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
  return { today: today, projects: cards, mine: mine, overdue: overdue };
}

/** Tasks in a date range (for 일정). */
function workBetween_(from, to) {
  return workData_().tasks.filter(function (t) { return t.date >= from && t.date <= to; });
}
