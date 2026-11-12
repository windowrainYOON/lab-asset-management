/* Server tests with made-up data. Run: node tests/run.js */
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const { load } = require('./harness');

const PIN = 'test-pin-42';
function pinRecord(pin, version) {
  const salt = crypto.randomBytes(16);
  // the desktop app does the same with Python hashlib.pbkdf2_hmac('sha256', pin, salt, 2000)
  return { salt: salt.toString('base64'), hash: crypto.pbkdf2Sync(pin, salt, 2000, 32, 'sha256').toString('base64'),
           iterations: 2000, version: version || 1 };
}

const MEMBERS = [{ name: '가나다', email: 'a@example.com', english: 'Gana' },
                 { name: '라마바', email: 'b@example.com' }, { name: '사아자', email: 'c@example.com' }];
const CONFIG = {
  members: MEMBERS,
  webapp: { pin: pinRecord(PIN, 1), pin_history: [] },
  calendar: { calendars: [{ id: 'labcal', name: '연구실', color: '#3949ab' }] },
  categories: { CATFILE: { owner: { name: '라마바', email: 'b@example.com' }, sort_order: 0 } },
  orders: { contact: { name: '사아자', email: 'c@example.com' } },
  location_sets: []
};
const IDS = { attendance: 'ATT', reservations: 'RES', notices: 'NOT', order_requests: 'ORD', work: 'WRK',
              budget: 'BUD', overlay: 'CONFIG' };

function books() {
  return {
    ATT: {
      '2026-11': [['날짜', '요일', '가나다', '라마바'],
        ...Array.from({ length: 30 }, (_, i) => {
          const d = '2026-11-' + String(i + 1).padStart(2, '0');
          return [d, '월화수목금토일'[(new Date(d).getUTCDay() + 6) % 7], i === 10 ? '' : '', i === 10 ? '휴가' : ''];
        })],
      '설정': [['항목', '값'], ['출근 시간', '09:00'], ['지각 허용(분)', '10'], ['근무 요일', '월,화,수,목,금']]
    },
    RES: {
      '장비': [['ID', '이름', '위치', '담당자', '상태', '색', '단위(분)', '최대(시간)', '메모'],
        ['eq000001', '공초점 현미경', '301호', '가나다', '사용 가능', '#0a84ff', '30', '4', ''],
        ['eq000002', 'FACS', '302호', '라마바', '점검 중', '', '60', '', '']],
      '예약': [['ID', '장비 ID', '장비', '예약자', '시작', '끝', '용도', '알림(분)', '만든 시각'],
        ['r0000001', 'eq000001', '공초점 현미경', '라마바', '2026-11-12 10:00', '2026-11-12 12:00', '이미징', '', '2026-11-10 09:00']]
    },
    NOT: { '공지사항': [['ID', '날짜', '제목', '내용', '작성자', '중요', '열람'],
      ['n0000001', '2026-11-01 09:00', '대청소', '금요일 대청소', '라마바', '★', '라마바'],
      ['n0000002', '2026-11-05 09:00', '세미나', '수요일 세미나', '사아자', '', '']] },
    ORD: { '주문 요청': [['ID', '요청일', '요청자', '요청자 이메일', '품목', '제조사', 'Cat#', '수량', '단가', '링크', '카테고리',
      '담당자', '담당자 이메일', '용도·메모', '상태', '담당자 메일', '주문일', '입고일', '알림']] },
    WRK: {
      '프로젝트': [['ID', '이름', '종류', '담당자', '주기', '시작일', '기간', '업무 이름', '메모', '회차 인원'],
        ['p0000001', '청소 당번', '당번제', '가나다, 라마바', '매주', '2026-11-02', '', '청소', '', '1']],
      '업무': [['ID', '프로젝트 ID', '프로젝트', '날짜', '담당자', '업무', '완료', '완료 시각'],
        ['t0000001', 'p0000001', '청소 당번', '2026-11-04', '라마바', '청소', 'O', ''],
        ['t0000002', 'p0000001', '청소 당번', '2026-11-13', '가나다', '청소', '', ''],
        ['t0000003', 'p0000001', '청소 당번', '2026-11-20', '라마바', '청소', '', '']]
    },
    BUD: {
      Sheet1: [['날짜', '내용', '수입', '지출', '잔액'],
        ['2026.10.01', '이월', ' ₩ 100,000 ', '', ' ₩ 100,000 '],
        ['2026.10.20', '간식', '', ' ₩ 12,930 ', ' ₩ 87,070 '],
        ['2026.11.11', '가나다 랩비', ' ₩ 19,360 ', '', '']],
      '랩비 2026.10': [['Lab fee 2026.10'], ['기준일', '2026-11-10'], ['Current balance', '₩87,070'], ['Target amount', '₩300,000'],
        ['Lab fee', '₩19,360'], [], ['Name', '이름', 'Unpaid carryover', 'Lab fee', 'Total', '직접 수정', '메모'],
        ['Gana', '가나다', '₩0', '₩19,360', '₩19,360'], ['Lama', '라마바', '₩5,000', '₩19,360', '₩24,360'],
        ['Total', '', '₩5,000', '₩38,720', '₩43,720']]
    },
    DBX: { '구성원·기기': [['이름', '영문 이름', '기기 ID', '처음 고른 날', '마지막 사용', '기기 수', '상태']],
           '태그': [['태그 UID', '마지막 카운터', '설치 위치', '활성', '처음 찍은 날']],
           '태깅로그': [['일시', '이름', '기기 ID', '태그 UID', '카운터', '결과']],
           '감사로그': [['일시', '이름', '기기 ID', '작업', '파일', '행 ID', '수정 전', '수정 후']] }
  };
}

let failures = 0, count = 0;
function test(name, fn) {
  count++;
  try { fn(); console.log('  ok  ' + name); }
  catch (e) { failures++; console.log('  FAIL ' + name + '\n       ' + (e.stack || e).split('\n').slice(0, 3).join('\n       ')); }
}

function fresh(extra) {
  const h = load(Object.assign({
    books: books(), config: JSON.parse(JSON.stringify(CONFIG)), ids: IDS,
    props: { DB_SHEET_ID: 'DBX' }, now: '2026-11-11T08:55:00+09:00',
    files: { CATFILE: { name: '항체.xlsx', mime: 'application/vnd.google-apps.spreadsheet' } },
    calendars: {
      labcal: [{ id: 'e1', summary: '랩 워크숍', start: { date: '2026-11-20' }, end: { date: '2026-11-21' },
                 extendedProperties: { private: { am_attendance_exclude: '1' } } },
               { id: 'e2', summary: '가나다 미팅', start: { dateTime: '2026-11-12T14:00:00+09:00' }, end: { dateTime: '2026-11-12T15:00:00+09:00' } }],
      'ko.south_korea.official#holiday@group.v.calendar.google.com': []
    }
  }, extra || {}));
  const device = 'dev_' + crypto.randomBytes(6).toString('hex');
  const r = h.call({ action: 'enter', pin: PIN, device });
  assert.ok(r.ok, JSON.stringify(r));
  h.as = (member, body) => h.call(Object.assign({ device, key: r.key, member }, body));
  h.device = device; h.key = r.key;
  return h;
}

console.log('비밀번호 · 입장 열쇠');
test('PBKDF2 in the script equals Python/Node pbkdf2', () => {
  const h = load({ config: CONFIG });
  const salt = crypto.randomBytes(16).toString('base64');
  const want = crypto.pbkdf2Sync('비밀 번호 ✓', Buffer.from(salt, 'base64'), 2000, 32, 'sha256').toString('base64');
  assert.strictEqual(h.run(`pbkdf2_(${JSON.stringify('비밀 번호 ✓')}, ${JSON.stringify(salt)}, 2000)`), want);
});
test('wrong PIN 5 times locks the phone for 10 minutes', () => {
  const h = load({ books: books(), config: CONFIG, ids: IDS, props: { DB_SHEET_ID: 'DBX' } });
  for (let i = 0; i < 5; i++) assert.strictEqual(h.call({ action: 'enter', pin: 'nope', device: 'phone_000001' }).code, 'wrong_pin');
  assert.strictEqual(h.call({ action: 'enter', pin: PIN, device: 'phone_000001' }).code, 'locked');
  assert.ok(h.call({ action: 'enter', pin: PIN, device: 'phone_000002' }).ok);
});
test('the built-in first PIN comes from private/Private.gs, as a hash only', () => {
  const h = load({ config: { members: MEMBERS }, private: { pin: pinRecord('first-pin', 1) } });
  const rec = h.run('pinRecord_()');
  assert.ok(rec.salt && rec.hash && rec.iterations >= 1000 && !('pin' in rec));
  assert.ok(h.call({ action: 'enter', pin: 'first-pin', device: 'phone_000009' }).ok);
  const none = load({ config: { members: MEMBERS } });
  assert.strictEqual(none.call({ action: 'enter', pin: 'first-pin', device: 'phone_000009' }).code, 'config');
});
test('no key / forged key / other phone → need_pin', () => {
  const h = fresh();
  assert.strictEqual(h.call({ action: 'members', device: h.device }).code, 'need_pin');
  assert.strictEqual(h.call({ action: 'members', device: h.device, key: h.key.slice(0, -2) + 'xx' }).code, 'need_pin');
  assert.strictEqual(h.call({ action: 'members', device: 'other_phone1', key: h.key }).code, 'need_pin');
  assert.ok(h.call({ action: 'members', device: h.device, key: h.key }).ok);
});
test('members list has no e-mail; pick records the phone in 구성원·기기', () => {
  const h = fresh();
  const m = h.call({ action: 'members', device: h.device, key: h.key });
  assert.deepStrictEqual(m.members.map(x => x.name), ['가나다', '라마바', '사아자']);
  assert.ok(!JSON.stringify(m).includes('@'));
  assert.ok(h.as('가나다', { action: 'pick' }).ok);
  const rows = h.books.DBX.getSheetByName('구성원·기기').data;
  const me = rows.find(r => r[0] === '가나다');
  assert.ok(me[2].includes(h.device) && me[3] === '2026-11-11' && me[6] === '활성');
});
test('unknown name → need_member', () => {
  assert.strictEqual(fresh().as('외부인', { action: 'home' }).code, 'need_member');
});
test('PIN changed with "keep phones" off (version up) → phones must enter again', () => {
  const h = fresh();
  const cfg = JSON.parse(JSON.stringify(CONFIG)); cfg.webapp.pin = pinRecord('new-pin', 2);
  h.setConfig(cfg);
  assert.strictEqual(h.as('가나다', { action: 'home' }).code, 'need_pin');
  assert.strictEqual(h.call({ action: 'enter', pin: PIN, device: h.device }).code, 'wrong_pin');
  assert.ok(h.call({ action: 'enter', pin: 'new-pin', device: h.device }).ok);
});
test('PIN changed with "keep phones" on (same version) → phones stay in', () => {
  const h = fresh();
  const cfg = JSON.parse(JSON.stringify(CONFIG)); cfg.webapp.pin = pinRecord('new-pin', 1);
  h.setConfig(cfg);
  assert.ok(h.as('가나다', { action: 'home' }).ok);
});

console.log('출근 태깅');
test('NTAG 424 SDM example (AN12196) decodes', () => {
  const h = load({});
  const r = h.run("sdmVerify_('EF963FF7828658A599F3041510671E88','94EED9EE65337086','00000000000000000000000000000000')");
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), { uid: '04DE5F1EACC040', counter: 61 });
  assert.throws(() => h.run("sdmVerify_('EF963FF7828658A599F3041510671E88','94EED9EE65337087','00000000000000000000000000000000')"));
});
test('a tap without the tag is refused unless ALLOW_PLAIN_TAP=1', () => {
  const h = fresh();
  assert.strictEqual(h.as('가나다', { action: 'tap' }).code, 'need_tag');
});
test('tag tap writes HH:MM once; the same tag URL again is refused (counter)', () => {
  const h = fresh({ props: { DB_SHEET_ID: 'DBX', SDM_KEY: '00000000000000000000000000000000' } });
  const tag = { picc: 'EF963FF7828658A599F3041510671E88', cmac: '94EED9EE65337086' };
  const r = h.as('가나다', Object.assign({ action: 'tap' }, tag));
  assert.ok(r.ok && r.result === '기록' && r.time === '08:55', JSON.stringify(r));
  const sh = h.books.ATT.getSheetByName('2026-11');
  assert.strictEqual(sh.data[11][2], '08:55');
  assert.strictEqual(h.as('가나다', Object.assign({ action: 'tap' }, tag)).code, 'replay');
  const tags = h.books.DBX.getSheetByName('태그').data;
  assert.strictEqual(tags[1][0], '04DE5F1EACC040');
  assert.strictEqual(tags[1][1], '61');
});
test('already in / text cell (휴가) are kept; late is flagged', () => {
  const h = fresh({ props: { DB_SHEET_ID: 'DBX', ALLOW_PLAIN_TAP: '1' } });
  h.setNow('2026-11-11T09:20:00+09:00');
  let r = h.as('라마바', { action: 'tap' });
  assert.strictEqual(r.result, '텍스트 있음');
  assert.strictEqual(h.books.ATT.getSheetByName('2026-11').data[11][3], '휴가');
  r = h.as('가나다', { action: 'tap' });
  assert.ok(r.result === '기록' && r.message.includes('지각'));
  h.setNow('2026-11-11T09:40:00+09:00');
  r = h.as('가나다', { action: 'tap' });
  assert.ok(r.result === '이미 출근' && r.message.includes('09:20'));
});
test('a new month makes the tab; a member without a column gets one at the right end', () => {
  const h = fresh({ props: { DB_SHEET_ID: 'DBX', ALLOW_PLAIN_TAP: '1' } });
  h.setNow('2026-12-01T08:30:00+09:00');
  const r = h.as('사아자', { action: 'tap' });
  assert.ok(r.ok && r.result === '기록', JSON.stringify(r));
  const sh = h.books.ATT.getSheetByName('2026-12');
  assert.deepStrictEqual(sh.data[0], ['날짜', '요일', '가나다', '라마바', '사아자']);
  assert.strictEqual(sh.data.length, 32);
  assert.deepStrictEqual(sh.data[1].slice(0, 2), ['2026-12-01', '화']);
  assert.strictEqual(sh.data[1][4], '08:30');
  const audit = h.books.DBX.getSheetByName('감사로그').data.map(r => r[3]);
  assert.ok(audit.includes('월 탭 만들기') && audit.includes('열 추가') && audit.includes('출근'));
});
test('month view: stats, excluded day from the lab calendar', () => {
  const h = fresh({ props: { DB_SHEET_ID: 'DBX', ALLOW_PLAIN_TAP: '1' } });
  h.as('가나다', { action: 'tap' });
  const m = h.as('가나다', { action: 'attendance.month', ym: '2026-11' });
  const d20 = m.days.find(d => d.date === '2026-11-20');
  assert.ok(!d20.work && d20.reason === '랩 워크숍');
  assert.strictEqual(m.days.find(d => d.date === '2026-11-15').kind, 'weekend');
  assert.deepStrictEqual([m.stats['가나다'].workdays, m.stats['가나다'].present], [8, 1]);
});
test('the tagging log keeps every tap with the phone id', () => {
  const h = fresh({ props: { DB_SHEET_ID: 'DBX', ALLOW_PLAIN_TAP: '1' } });
  h.as('가나다', { action: 'tap' });
  h.as('가나다', { action: 'tap' });
  const log = h.books.DBX.getSheetByName('태깅로그').data.slice(1);
  assert.strictEqual(log.length, 2);
  assert.ok(log.every(r => r[2] === h.device));
});

console.log('장비 예약');
const NEW = { action: 'reservation.create', equipmentId: 'eq000001', start: '2026-11-12 13:00', end: '2026-11-12 15:00', purpose: '실험' };
test('create, then overlap / unit / max / past / 점검 중 are refused', () => {
  const h = fresh();
  const r = h.as('가나다', NEW);
  assert.ok(r.ok, JSON.stringify(r));
  const row = h.books.RES.getSheetByName('예약').data[2];
  assert.deepStrictEqual(row.slice(1, 7), ['eq000001', '공초점 현미경', '가나다', '2026-11-12 13:00', '2026-11-12 15:00', '실험']);
  assert.ok(/^[0-9a-f]{8}$/.test(row[0]));
  const bad = [
    [{ start: '2026-11-12 11:30', end: '2026-11-12 13:00' }, '겹쳐요'],
    [{ start: '2026-11-12 16:15', end: '2026-11-12 17:00' }, '30분 단위'],
    [{ start: '2026-11-12 16:00', end: '2026-11-12 21:00' }, '4시간까지'],
    [{ start: '2026-11-10 16:00', end: '2026-11-10 17:00' }, '지난 시각'],
    [{ start: '2026-11-12 17:00', end: '2026-11-12 16:00' }, '늦어야'],
    [{ equipmentId: 'eq000002', start: '2026-11-13 10:00', end: '2026-11-13 11:00' }, '점검 중']
  ];
  bad.forEach(([chg, text]) => {
    const x = h.as('가나다', Object.assign({}, NEW, chg));
    assert.ok(!x.ok && x.error.includes(text), text + ' → ' + JSON.stringify(x));
  });
});
test("someone else's booking: confirm first, then change; audit keeps before/after", () => {
  const h = fresh();
  let r = h.as('가나다', { action: 'reservation.update', id: 'r0000001', end: '2026-11-12 13:00' });
  assert.strictEqual(r.code, 'confirm'); assert.strictEqual(r.owner, '라마바');
  r = h.as('가나다', { action: 'reservation.update', id: 'r0000001', end: '2026-11-12 13:00', confirm: true });
  assert.ok(r.ok, JSON.stringify(r));
  assert.strictEqual(h.books.RES.getSheetByName('예약').data[1][5], '2026-11-12 13:00');
  const a = h.books.DBX.getSheetByName('감사로그').data.pop();
  assert.ok(a[1] === '가나다' && a[3] === '예약 고치기' && a[6].includes('12:00') && a[7].includes('13:00'));
});
test('a booking already started can only move its end', () => {
  const h = fresh();
  h.setNow('2026-11-12T11:00:00+09:00');
  let r = h.as('라마바', { action: 'reservation.update', id: 'r0000001', start: '2026-11-12 10:30' });
  assert.ok(!r.ok && r.error.includes('끝 시각만'));
  r = h.as('라마바', { action: 'reservation.update', id: 'r0000001', end: '2026-11-12 13:00' });
  assert.ok(r.ok, JSON.stringify(r));
});
test('cancel deletes the row and keeps it in the audit log', () => {
  const h = fresh();
  const r = h.as('라마바', { action: 'reservation.cancel', id: 'r0000001' });
  assert.ok(r.ok);
  assert.strictEqual(h.books.RES.getSheetByName('예약').data.length, 1);
  const a = h.books.DBX.getSheetByName('감사로그').data.pop();
  assert.ok(a[3].startsWith('예약 취소') && JSON.parse(a[6])['용도'] === '이미징');
});
test('list marks my bookings; formula-like text is made safe', () => {
  const h = fresh();
  h.as('가나다', Object.assign({}, NEW, { purpose: '=HYPERLINK("x")' }));
  assert.strictEqual(h.books.RES.getSheetByName('예약').data[2][6], '=HYPERLINK("x")');
  const w = h.books.RES.getSheetByName('예약').writes;
  assert.ok(w.some(x => x[2] === '\'=HYPERLINK("x")'), 'written with a leading quote');
  const l = h.as('가나다', { action: 'reservation.list', from: '2026-11-12' });
  assert.deepStrictEqual(l.reservations.map(r => r.mine), [false, true]);
});

console.log('공지 · 주문 · 예산 · 업무 · 일정');
test('notices: pinned first, opening marks read, create / edit / delete', () => {
  const h = fresh();
  let l = h.as('가나다', { action: 'notice.list' });
  assert.deepStrictEqual(l.notices.map(n => n.id), ['n0000001', 'n0000002']);
  assert.ok(!l.notices[0].read);
  const g = h.as('가나다', { action: 'notice.get', id: 'n0000001' });
  assert.deepStrictEqual(g.notice.readers, ['라마바', '가나다']);
  assert.strictEqual(h.books.NOT.getSheetByName('공지사항').data[1][6], '라마바, 가나다');
  const c = h.as('가나다', { action: 'notice.create', title: '새 공지', body: '내용', pinned: true });
  const row = h.books.NOT.getSheetByName('공지사항').data[3];
  assert.deepStrictEqual([row[0], row[2], row[4], row[5], row[6]], [c.id, '새 공지', '가나다', '★', '가나다']);
  assert.strictEqual(h.as('가나다', { action: 'notice.update', id: 'n0000002', title: 'x' }).code, 'confirm');
  assert.ok(h.as('가나다', { action: 'notice.update', id: c.id, title: '고친 공지', body: '내용', pinned: false }).ok);
  assert.ok(h.as('가나다', { action: 'notice.delete', id: c.id }).ok);
  assert.strictEqual(h.books.NOT.getSheetByName('공지사항').data.length, 3);
});
test('orders: owner from category, mail sent and stamped, state flow mails requester', () => {
  const h = fresh();
  const c = h.as('가나다', { action: 'order.create', item: 'Anti-GFP', company: 'Abcam', catalog: 'ab290', qty: '2', category: '항체', note: '실험용' });
  assert.ok(c.ok && c.mailed, JSON.stringify(c));
  const row = h.books.ORD.getSheetByName('주문 요청').data[1];
  assert.deepStrictEqual([row[2], row[3], row[4], row[11], row[12], row[14], row[18]], ['가나다', 'a@example.com', 'Anti-GFP', '라마바', 'b@example.com', '요청', '켬']);
  assert.strictEqual(h.mails[0].to, 'b@example.com');
  const s = h.as('라마바', { action: 'order.status', id: c.id, state: '주문함' });
  assert.ok(s.ok && s.mailed && s.order.ordered === '2026-11-11');
  assert.strictEqual(h.mails[1].to, 'a@example.com');
  const l = h.as('라마바', { action: 'order.list' });
  assert.ok(l.orders[0].mineHandle && !l.orders[0].mineRequest);
  assert.deepStrictEqual(l.categories.map(x => x.name), ['항체']);
  const n = h.as('사아자', { action: 'order.create', item: '튜브' });
  assert.strictEqual(h.books.ORD.getSheetByName('주문 요청').data[2][11], '사아자');   // orders.contact
  assert.ok(h.as('가나다', { action: 'order.status', id: n.id, state: '취소' }).ok);
  assert.strictEqual(h.books.ORD.getSheetByName('주문 요청').data.length, 3);           // the row stays
});
test('budget: my lab fee and payment, everyone in the round, balance', () => {
  const h = fresh();
  const m = h.as('가나다', { action: 'budget.mine' });
  assert.deepStrictEqual([m.round.label, m.mine.total, m.mine.paid, m.mine.state], ['2026.10', 19360, 19360, '납부 완료']);
  const lab = h.as('라마바', { action: 'budget.lab' });
  assert.strictEqual(lab.balance, 106430);
  assert.deepStrictEqual(lab.round.lines.map(l => l.state), ['납부 완료', '미납']);
  assert.strictEqual(lab.mine.left, 24360);
});
test('work: this turn / next turn, my next tasks', () => {
  const h = fresh();
  const w = h.as('가나다', { action: 'work.board' });
  assert.strictEqual(w.projects[0].current.id, 't0000002');
  assert.strictEqual(w.projects[0].next.id, 't0000003');
  assert.ok(w.projects[0].mineNow);
  assert.deepStrictEqual(w.mine.map(t => t.id), ['t0000002']);
});
test('schedule: calendar + bookings + work, mine flags', () => {
  const h = fresh();
  const s = h.as('가나다', { action: 'schedule.range', from: '2026-11-12', to: '2026-11-13' });
  assert.deepStrictEqual(s.items.map(i => i.kind + ':' + i.mine), ['reservation:false', 'event:true', 'work:true']);
});
test('home answers in one call', () => {
  const h = fresh({ props: { DB_SHEET_ID: 'DBX', ALLOW_PLAIN_TAP: '1' } });
  h.as('가나다', { action: 'tap' });
  const r = h.as('가나다', { action: 'home' });
  assert.ok(r.ok);
  assert.deepStrictEqual([r.attendance.mine, r.attendance.present, r.attendance.total, r.notices.unread], ['08:55', 1, 2, 2]);
  ['attendance', 'reservations', 'work', 'notices', 'orders'].forEach(k => assert.ok(!r[k].error, k + ': ' + JSON.stringify(r[k])));
});
test('setup() makes the DB tabs and syncs members', () => {
  const h = load({ books: {}, config: CONFIG, props: {} });
  h.run('setup()');
  const id = h.props.DB_SHEET_ID;
  const db = h.books[id];
  assert.deepStrictEqual(db.getSheets().map(s => s.getName()), ['구성원·기기', '태그', '태깅로그', '감사로그']);
  assert.strictEqual(db.getSheetByName('구성원·기기').data.length, 4);
  assert.ok(h.props.ENTRY_SECRET.length > 40);
});

test('a read right after a write sees the write (cache is dropped)', () => {
  const h = fresh();
  assert.strictEqual(h.as('가나다', { action: 'notice.list' }).notices.length, 2);
  const c = h.as('가나다', { action: 'notice.create', title: '바로 보여야 함' });
  assert.strictEqual(h.as('가나다', { action: 'notice.list' }).notices.length, 3);
  assert.ok(h.as('가나다', { action: 'notice.get', id: c.id }).ok);
});
test('the one-file paste version (paste/Code.gs) works the same', () => {
  require('child_process').execFileSync('node', [require('path').join(__dirname, '..', 'tools', 'bundle.js')]);
  const h = fresh({ bundle: true, props: { DB_SHEET_ID: 'DBX', ALLOW_PLAIN_TAP: '1' } });
  assert.strictEqual(h.as('가나다', { action: 'tap' }).result, '기록');
  assert.ok(h.as('가나다', { action: 'home' }).ok);
});

console.log(`\n${count - failures}/${count} passed`);
process.exit(failures ? 1 : 0);
