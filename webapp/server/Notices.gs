/**
 * 공지사항 sheet (first tab): ID 날짜 제목 내용 작성자 중요 열람. 열람 = names, comma separated.
 * Pinned (★) first, then newest first. Anyone may write, change or delete (trust based).
 */

function noticeRow_(r) {
  return {
    id: r.ID, date: r['날짜'] || '', title: r['제목'] || '', body: r['내용'] || '', author: r['작성자'] || '',
    pinned: truthy_(r['중요']), readers: splitNames_(String(r['열람'] || '').replace(/\n/g, ','))
  };
}

function noticeSort_(a, b) {
  if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
  return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
}

function noticesAll_() {
  return cached_('notices', 'all', function () {
    return { list: table_(book_('notices'), 0).rows.map(noticeRow_).filter(function (n) { return n.id; }).sort(noticeSort_) };
  }).list;
}

/** action notice.list */
function noticeList_(body, member) {
  var total = members_().length;
  return {
    members: total,
    notices: noticesAll_().map(function (n) {
      return { id: n.id, date: n.date, title: n.title, author: n.author, pinned: n.pinned,
               preview: n.body.replace(/\s+/g, ' ').slice(0, 80), readCount: n.readers.length,
               read: includesName_(n.readers, member.name), mine: samePerson_(n.author, member.name) };
    })
  };
}

/** action notice.get: {id} — also marks it read by me. */
function noticeGet_(body, member, device) {
  var n = null;
  noticesAll_().forEach(function (x) { if (x.id === body.id) n = x; });
  if (!n) throw fail_('지워진 공지예요.', 'gone');
  if (!includesName_(n.readers, member.name)) {
    withLock_(function () {
      var t = table_(book_('notices'), 0);
      var rec = t.byId(n.id);
      if (!rec) return;
      var readers = splitNames_(String(rec['열람'] || '').replace(/\n/g, ','));
      if (!includesName_(readers, member.name)) {
        readers.push(member.name);
        t.update(n.id, { '열람': readers.join(', ') });
      }
      n.readers = readers;
      flushRole_('notices');
    });
  }
  var names = members_().map(function (m) { return m.name; });
  return { notice: n, members: names, mine: samePerson_(n.author, member.name) };
}

function noticeInput_(body) {
  var title = String(body.title || '').trim().slice(0, 200);
  if (!title) throw fail_('제목을 넣어 주세요.', 'rule');
  return { '제목': title, '내용': String(body.body || '').slice(0, 20000), '중요': body.pinned ? '★' : '' };
}

/** action notice.create: {title, body, pinned} */
function noticeCreate_(body, member, device) {
  var fields = noticeInput_(body);
  return withLock_(function () {
    var t = table_(book_('notices'), 0);
    var rec = { 'ID': newId_(t.ids()), '날짜': stamp_(), '작성자': member.name, '열람': member.name };
    for (var k in fields) rec[k] = fields[k];
    t.append(rec);
    audit_(member.name, device, '공지 쓰기', '공지사항', rec.ID, null, rec);
    flushRole_('notices');
    return { id: rec.ID };
  });
}

/** action notice.update: {id, title, body, pinned, confirm?} */
function noticeUpdate_(body, member, device) {
  var fields = noticeInput_(body);
  return withLock_(function () {
    var t = table_(book_('notices'), 0);
    var rec = t.byId(body.id);
    if (!rec) throw fail_('그 사이에 지워진 공지예요.', 'gone');
    needConfirm_(rec['작성자'], member, body, '공지');
    var ch = t.update(body.id, fields);
    if (Object.keys(ch.after).length) audit_(member.name, device, '공지 고치기', '공지사항', body.id, ch.before, ch.after);
    flushRole_('notices');
    return { id: body.id };
  });
}

/** action notice.delete: {id, confirm?} */
function noticeDelete_(body, member, device) {
  return withLock_(function () {
    var t = table_(book_('notices'), 0);
    var rec = t.byId(body.id);
    if (!rec) throw fail_('이미 지워진 공지예요.', 'gone');
    needConfirm_(rec['작성자'], member, body, '공지');
    var removed = t.remove(body.id);
    delete removed._row;
    audit_(member.name, device, '공지 삭제(행 삭제)', '공지사항', body.id, removed, null);
    flushRole_('notices');
    return { deleted: body.id };
  });
}
