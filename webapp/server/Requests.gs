/**
 * 업무 요청: a short memo to the people of one 업무 (no title).
 * Tab '요청' in the 업무 workbook (made on first use, the desktop app reads and writes the same tab):
 *   ID 날짜 업무 ID 업무 보낸 사람 받는 사람 내용 상태 확인한 사람 확인 시각 메일
 * 상태: '보냄' → '확인함' (a receiver pressed 확인). 메일 = when the mail went out.
 * Requests made on the web app are mailed at once. Rows the desktop app adds have an empty 메일 cell;
 * the time trigger sendPendingRequestMails() mails them every 5 minutes (installRequestMail() sets it up).
 */

var REQ_TAB_ = '요청';
var REQ_HEAD_ = ['ID', '날짜', '업무 ID', '업무', '보낸 사람', '받는 사람', '내용', '상태', '확인한 사람', '확인 시각', '메일'];
var REQ_MAIL_DAYS_ = 3;   // the trigger never mails rows older than this (no surprise mail for old rows)

/** The 요청 tab, made with its header when it is not there yet. */
function reqTable_() {
  var book = book_('work');
  var sh = book.getSheetByName(REQ_TAB_);
  if (!sh) {
    sh = book.insertSheet(REQ_TAB_);
    sh.getRange(1, 1, 1, REQ_HEAD_.length).setNumberFormat('@').setValues([REQ_HEAD_]);
    if (sh.setFrozenRows) sh.setFrozenRows(1);
  }
  var t = new SheetTable_(sh, 1);
  REQ_HEAD_.forEach(function (h) { t.ensureColumn(h); });
  return t;
}

function reqRow_(r) {
  return {
    id: r.ID, date: r['날짜'] || '', workId: r['업무 ID'] || '', work: r['업무'] || '', from: r['보낸 사람'] || '',
    to: splitNames_(String(r['받는 사람'] || '').replace(/\n/g, ',')), memo: r['내용'] || '',
    acked: r['상태'] === '확인함', ackBy: r['확인한 사람'] || '', ackAt: r['확인 시각'] || '', mailed: r['메일'] || ''
  };
}

function requestsAll_() {
  return cached_('work', 'requests', function () {
    var book = book_('work');
    if (!book.getSheetByName(REQ_TAB_)) return { list: [] };
    var rows = table_(book, REQ_TAB_).rows.map(reqRow_).filter(function (x) { return x.id; });
    rows.sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : 0; });
    return { list: rows };
  }).list;
}

/** Who gets a request for this 업무: an owner duty → its people; otherwise this turn's people (else everyone on it). */
function workTargets_() {
  var d = workData_();
  var open = {};
  d.tasks.forEach(function (t) { if (!t.done && !open[t.projectId]) open[t.projectId] = t; });
  return d.projects.map(function (p) {
    var people = p.kind === 'owner' ? p.members : (open[p.id] ? open[p.id].people : p.members);
    return { id: p.id, name: p.name, kind: p.kind, people: people.length ? people : p.members };
  });
}

function reqView_(x, member) {
  return { id: x.id, date: x.date, workId: x.workId, work: x.work, from: x.from, to: x.to, memo: x.memo,
           acked: x.acked, ackBy: x.ackBy, toMe: includesName_(x.to, member.name), fromMe: samePerson_(x.from, member.name) };
}

/** action request.list → the 업무 to pick from, and requests to me / from me (newest first). */
function requestList_(body, member) {
  var all = requestsAll_().map(function (x) { return reqView_(x, member); });
  return {
    works: workTargets_().map(function (w) { w.mine = includesName_(w.people, member.name); return w; }),
    toMe: all.filter(function (x) { return x.toMe; }).slice(0, 40),
    fromMe: all.filter(function (x) { return x.fromMe; }).slice(0, 20)
  };
}

/** Unchecked requests to me (for 요약). */
function requestsToMe_(member) {
  return requestsAll_().filter(function (x) { return !x.acked && includesName_(x.to, member.name); })
    .slice(0, 10).map(function (x) { return reqView_(x, member); });
}

function reqMail_(rec) {
  var to = splitNames_(String(rec['받는 사람'] || '')).map(function (n) { var m = memberByName_(n); return m && m.email; })
    .filter(function (e) { return e; });
  if (!to.length) return false;
  var subject = '[Asset Manager] 업무 요청 · ' + rec['업무'] + ' · ' + rec['보낸 사람'];
  var text = rec['보낸 사람'] + '님이 ‘' + rec['업무'] + '’ 업무로 요청을 보냈습니다 (' + rec['날짜'] + ').\n\n' +
    rec['내용'] + '\n\n웹앱의 업무 탭이나 데스크톱 앱의 ‘업무 요청’에서 확인을 누르면 보낸 사람에게 확인함으로 보여요.';
  return sendMail_(to.join(','), subject, text);
}

/** action request.create: {workId, memo} → mailed to the 업무's people at once. */
function requestCreate_(body, member, device) {
  var memo = String(body.memo || '').trim().slice(0, 4000);
  if (!memo) throw fail_('요청 내용을 적어 주세요.', 'rule');
  var w = null;
  workTargets_().forEach(function (x) { if (x.id === body.workId) w = x; });
  if (!w) throw fail_('업무를 골라 주세요.', 'rule');
  if (!w.people.length) throw fail_('이 업무에는 담당자가 없어요. 데스크톱 앱에서 담당자를 먼저 정해 주세요.', 'rule');
  return withLock_(function () {
    var t = reqTable_();
    var rec = { 'ID': newId_(t.ids()), '날짜': stamp_(), '업무 ID': w.id, '업무': w.name, '보낸 사람': member.name,
                '받는 사람': w.people.join(', '), '내용': memo, '상태': '보냄' };
    if (reqMail_(rec)) rec['메일'] = stamp_();
    t.append(rec);
    audit_(member.name, device, '업무 요청', '업무', rec.ID, null, rec);
    flushRole_('work');
    return { id: rec.ID, to: w.people, mailed: rec['메일'] || '' };
  });
}

/** action request.ack: {id} — a receiver says they saw it. */
function requestAck_(body, member, device) {
  return withLock_(function () {
    var t = reqTable_();
    var rec = t.byId(body.id);
    if (!rec) throw fail_('그 사이에 지워진 요청이에요.', 'gone');
    if (rec['상태'] === '확인함') return { id: body.id, acked: true };
    var ch = t.update(body.id, { '상태': '확인함', '확인한 사람': member.name, '확인 시각': stamp_() });
    audit_(member.name, device, '업무 요청 확인', '업무', body.id, ch.before, ch.after);
    flushRole_('work');
    return { id: body.id, acked: true };
  });
}

/** Time trigger: mail the requests the desktop app added (empty 메일 cell, made in the last few days). */
function sendPendingRequestMails() {
  var book = book_('work');
  if (!book.getSheetByName(REQ_TAB_)) return 0;
  var since = stamp_(new Date(now_().getTime() - REQ_MAIL_DAYS_ * 86400000));
  return withLock_(function () {
    var t = reqTable_(), n = 0;
    t.rows.filter(function (r) { return r.ID && !r['메일'] && r['날짜'] >= since; }).forEach(function (r) {
      if (reqMail_(r)) { t.update(r.ID, { '메일': stamp_() }); n++; }
    });
    if (n) flushRole_('work');
    return n;
  });
}

/** Run once in the script editor: mails desktop requests every 5 minutes. */
function installRequestMail() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'sendPendingRequestMails') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('sendPendingRequestMails').timeBased().everyMinutes(5).create();
  Logger.log('데스크톱에서 보낸 업무 요청을 5분마다 메일로 보냅니다.');
}
