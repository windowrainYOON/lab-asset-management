/**
 * 주문 요청 sheet: ID 요청일 요청자 요청자 이메일 품목 제조사 Cat# 수량 단가 링크 카테고리 담당자 담당자 이메일
 * 용도·메모 상태 담당자 메일 주문일 입고일 알림. Flow 요청 → 주문함 → 입고됨, or 취소 (the row stays).
 * The person in charge = the category's owner (desktop 담당자 지정), else orders.contact.
 * Mails go from the script owner's account (MailApp): to the person in charge on a new request
 * (담당자 메일 = when), and to the requester on a state change when 알림 is 켬.
 */

var ORDER_STATES_ = ['요청', '주문함', '입고됨', '취소'];

/** Inventory categories (names only, from the settings JSON + file names) with their owner. */
function orderCategories_() {
  var cache = CacheService.getScriptCache();
  var hit = cache.get('order_categories');
  if (hit) return JSON.parse(hit);
  var cats = config_().categories || {};
  var out = [];
  Object.keys(cats).forEach(function (fid) {
    try {
      var f = DriveApp.getFileById(fid);
      if (f.isTrashed()) return;
      var name = f.getName().replace(/\.(xlsx|xls|csv)$/i, '');
      var o = cats[fid].owner || {};
      out.push({ name: name, owner: o.name || '', ownerEmail: o.email || '', sort: +cats[fid].sort_order || 0 });
    } catch (e) { /* removed file */ }
  });
  out.sort(function (a, b) { return a.sort - b.sort || (a.name < b.name ? -1 : 1); });
  cache.put('order_categories', JSON.stringify(out), 21600);
  return out;
}

function orderOwner_(category) {
  var hit = null;
  orderCategories_().forEach(function (c) { if (c.name === category && (c.owner || c.ownerEmail)) hit = c; });
  if (hit) return { name: hit.owner, email: hit.ownerEmail };
  var c = (config_().orders || {}).contact || {};
  return { name: c.name || '', email: c.email || '' };
}

function orderRow_(r) {
  return {
    id: r.ID, date: r['요청일'] || '', requester: r['요청자'] || '', item: r['품목'] || '',
    company: r['제조사'] || '', catalog: r['Cat#'] || '', qty: r['수량'] || '', price: r['단가'] || '',
    link: r['링크'] || '', category: r['카테고리'] || '', owner: r['담당자'] || '', note: r['용도·메모'] || '',
    state: ORDER_STATES_.indexOf(r['상태']) >= 0 ? r['상태'] : '요청', mailed: r['담당자 메일'] || '',
    ordered: r['주문일'] || '', received: r['입고일'] || '',
    notify: !/^(끔|x|no|false|0|off|n)$/i.test(String(r['알림'] || '켬').trim())
  };
}

/** action order.list → all requests, newest first, plus the form's choices. */
function orderList_(body, member) {
  var list = cached_('order_requests', 'all', function () {
    return { list: table_(book_('order_requests'), '주문 요청').rows.map(orderRow_).filter(function (o) { return o.id; }) };
  }).list.sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : 0; });
  list.forEach(function (o) {
    o.mineRequest = samePerson_(o.requester, member.name);
    o.mineHandle = samePerson_(o.owner, member.name);
  });
  return {
    orders: list,
    categories: orderCategories_().map(function (c) { return { name: c.name, owner: c.owner }; }),
    contact: orderOwner_('').name
  };
}

function orderInput_(body) {
  var f = {};
  var map = { item: '품목', company: '제조사', catalog: 'Cat#', qty: '수량', price: '단가', link: '링크',
              category: '카테고리', note: '용도·메모' };
  for (var k in map) if (body[k] !== undefined) f[map[k]] = String(body[k]).trim().slice(0, k === 'note' ? 2000 : 300);
  if (body.link && !/^https?:\/\//i.test(String(body.link).trim())) throw fail_('링크는 http로 시작해야 해요.', 'rule');
  if (body.notify !== undefined) f['알림'] = body.notify ? '켬' : '끔';
  return f;
}

function sendMail_(to, subject, text) {
  if (!to) return false;
  try {
    MailApp.sendEmail({ to: to, subject: subject, body: text, name: 'Asset Manager (연구실 웹앱)' });
    return true;
  } catch (e) { return false; }
}

/** action order.create */
function orderCreate_(body, member, device) {
  var f = orderInput_(body);
  if (!f['품목']) throw fail_('품목을 넣어 주세요.', 'rule');
  return withLock_(function () {
    var t = table_(book_('order_requests'), '주문 요청');
    var owner = orderOwner_(f['카테고리'] || '');
    var rec = {
      'ID': newId_(t.ids()), '요청일': stamp_(), '요청자': member.name, '요청자 이메일': member.email || '',
      '수량': '1', '담당자': owner.name, '담당자 이메일': owner.email, '상태': '요청', '알림': '켬'
    };
    for (var k in f) rec[k] = f[k];
    if (!rec['수량']) rec['수량'] = '1';
    var o = orderRow_(rec);
    var subject = '[Asset Manager] 주문 요청 1건 · ' + member.name;
    var text = (owner.name || owner.email || '담당자') + '님, ' + member.name + '님이 ' + today_() +
      '에 아래 품목의 주문을 요청했습니다.\n\n' +
      ['품목: ' + o.item, '제조사: ' + o.company, 'Cat#: ' + o.catalog, '수량: ' + o.qty, '단가: ' + o.price,
       '카테고리: ' + o.category, '링크: ' + o.link, '용도·메모: ' + o.note].join('\n') +
      '\n\n앱의 ‘주문’ 탭이나 모바일 웹앱에서 주문함 · 입고됨으로 바꾸면 요청자에게 알림이 갑니다.';
    if (sendMail_(owner.email, subject, text)) rec['담당자 메일'] = stamp_();
    t.append(rec);
    audit_(member.name, device, '주문 요청', '주문 요청', rec.ID, null, rec);
    flushRole_('order_requests');
    return { id: rec.ID, mailed: rec['담당자 메일'] || '' };
  });
}

/** action order.update: change the fields (anyone, any time). */
function orderUpdate_(body, member, device) {
  var f = orderInput_(body);
  return withLock_(function () {
    var t = table_(book_('order_requests'), '주문 요청');
    var rec = t.byId(body.id);
    if (!rec) throw fail_('그 사이에 지워진 요청이에요.', 'gone');
    needConfirm_(rec['요청자'], member, body, '주문 요청');
    if (f['카테고리'] !== undefined && f['카테고리'] !== rec['카테고리']) {
      var owner = orderOwner_(f['카테고리']);
      f['담당자'] = owner.name; f['담당자 이메일'] = owner.email;
    }
    var ch = t.update(body.id, f);
    if (Object.keys(ch.after).length) audit_(member.name, device, '주문 요청 고치기', '주문 요청', body.id, ch.before, ch.after);
    flushRole_('order_requests');
    return { order: orderRow_(ch.record) };
  });
}

/** action order.status: {id, state} */
function orderStatus_(body, member, device) {
  if (ORDER_STATES_.indexOf(body.state) < 0) throw fail_('알 수 없는 상태예요.', 'rule');
  return withLock_(function () {
    var t = table_(book_('order_requests'), '주문 요청');
    var rec = t.byId(body.id);
    if (!rec) throw fail_('그 사이에 지워진 요청이에요.', 'gone');
    var changes = { '상태': body.state };
    if (body.state === '주문함' && !rec['주문일']) changes['주문일'] = today_();
    if (body.state === '입고됨' && !rec['입고일']) changes['입고일'] = today_();
    var ch = t.update(body.id, changes);
    var o = orderRow_(ch.record);
    var mailed = false;
    if (Object.keys(ch.after).length) {
      audit_(member.name, device, '주문 상태 ' + body.state, '주문 요청', body.id, ch.before, ch.after);
      if (o.notify && body.state !== '요청' && !samePerson_(o.requester, member.name)) {
        var when = { '주문함': o.ordered, '입고됨': o.received }[body.state] || '';
        var line = o.requester + '님이 ' + o.date.slice(0, 10) + '에 요청한 ‘' + o.item + '’(' + o.qty + ')이(가) ' +
          body.state + ' 상태가 되었습니다' + (when ? ' (' + when + ').' : '.') + '\n\n담당자: ' + (o.owner || member.name);
        mailed = sendMail_(rec['요청자 이메일'], '[Asset Manager] 주문 ' + body.state + ' · ' + o.item, line);
      }
    }
    flushRole_('order_requests');
    return { order: o, mailed: mailed };
  });
}
