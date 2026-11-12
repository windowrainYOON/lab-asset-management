/**
 * 웹앱 DB: a sheet in its own folder "Lab Web App" (not inside the shared Lab Management folder).
 * Tabs: 구성원·기기, 태그, 태깅로그, 감사로그. Made by setup(), which the owner runs once.
 */

var DB_TABS_ = {
  '구성원·기기': ['이름', '영문 이름', '기기 ID', '처음 고른 날', '마지막 사용', '기기 수', '상태'],
  '태그': ['태그 UID', '마지막 카운터', '설치 위치', '활성', '처음 찍은 날'],
  '태깅로그': ['일시', '이름', '기기 ID', '태그 UID', '카운터', '결과'],
  '감사로그': ['일시', '이름', '기기 ID', '작업', '파일', '행 ID', '수정 전', '수정 후']
};

/** Run once from the editor: makes the folder and the DB sheet, and remembers its id. */
function setup() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('DB_SHEET_ID');
  var book;
  if (id) {
    book = SpreadsheetApp.openById(id);
  } else {
    var folders = DriveApp.getFoldersByName('Lab Web App');
    var folder = folders.hasNext() ? folders.next() : DriveApp.createFolder('Lab Web App');
    book = SpreadsheetApp.create('웹앱 DB');
    DriveApp.getFileById(book.getId()).moveTo(folder);
    props.setProperty('DB_SHEET_ID', book.getId());
  }
  Object.keys(DB_TABS_).forEach(function (name) {
    var sh = book.getSheetByName(name) || book.insertSheet(name);
    var head = DB_TABS_[name];
    var cur = sh.getRange(1, 1, 1, head.length).getValues()[0].map(asText_);
    if (cur.join('') === '') {
      sh.getRange(1, 1, 1, head.length).setValues([head]).setFontWeight('bold');
      sh.setFrozenRows(1);
      sh.getRange('A:Z').setNumberFormat('@');
    }
  });
  var blank = book.getSheetByName('Sheet1') || book.getSheetByName('시트1');
  if (blank && book.getSheets().length > 1 && blank.getLastRow() === 0) book.deleteSheet(blank);
  entrySecret_();
  syncMembers_();
  Logger.log('웹앱 DB: ' + book.getUrl());
  Logger.log('연구실 구성원 ' + members_().length + '명을 읽었습니다.');
  return book.getUrl();
}

function db_() {
  var id = prop_('DB_SHEET_ID');
  if (!id) throw fail_('서버 준비가 아직 안 됐어요 (setup).', 'config');
  return SpreadsheetApp.openById(id);
}

function dbTable_(name) { return table_(db_(), name); }

/** Make 구성원·기기 match the settings JSON members (add new, mark the removed 비활성). */
function syncMembers_() {
  var t = dbTable_('구성원·기기');
  var names = {};
  members_().forEach(function (m) {
    names[m.name] = true;
    var hit = null;
    t.rows.forEach(function (r) { if (r['이름'] === m.name) hit = r; });
    if (!hit) t.append({ '이름': m.name, '영문 이름': m.english || '', '상태': '활성' });
    else if (hit['상태'] !== '활성' || hit['영문 이름'] !== (m.english || '')) {
      setCells_(t, hit._row, { '상태': '활성', '영문 이름': m.english || '' });
    }
  });
  t.rows.forEach(function (r) {
    if (r['이름'] && !names[r['이름']] && r['상태'] !== '비활성') setCells_(t, r._row, { '상태': '비활성' });
  });
}

function setCells_(t, row, changes) {
  for (var k in changes) {
    var c = t.col[k];
    if (c) t.sheet.getRange(row, c).setNumberFormat('@').setValue(cellText_(changes[k]));
  }
}

/** Record that this phone is used by this member (at most once an hour unless `force`). */
function touchDevice_(name, device, force) {
  var cache = CacheService.getScriptCache();
  var key = 'touch:' + device + ':' + name;
  if (!force && cache.get(key)) return;
  cache.put(key, '1', 3600);
  try {
    withLock_(function () {
      syncMembers_();
      var t = dbTable_('구성원·기기');
      var r = null;
      t.rows.forEach(function (x) { if (x['이름'] === name) r = x; });
      if (!r) return;
      var devices = splitNames_(r['기기 ID']);
      if (devices.indexOf(device) < 0) devices.push(device);
      setCells_(t, r._row, {
        '기기 ID': devices.join(', '),
        '처음 고른 날': r['처음 고른 날'] || today_(),
        '마지막 사용': stamp_(),
        '기기 수': String(devices.length)
      });
    });
  } catch (e) { /* bookkeeping only: never block the request */ }
}

/** 태깅로그 line. */
function log_(result, name, device, uid, counter, extra) {
  try {
    dbTable_('태깅로그').append({
      '일시': fmt_(now_(), 'yyyy-MM-dd HH:mm:ss'), '이름': name || '', '기기 ID': device || '',
      '태그 UID': uid || '', '카운터': counter === undefined ? '' : String(counter),
      '결과': extra ? result + ' (' + extra + ')' : result
    });
  } catch (e) { /* the log must not break a tap */ }
}

/** 감사로그 line for every write. before/after are objects (stored as JSON). */
function audit_(who, device, action, file, id, before, after) {
  dbTable_('감사로그').append({
    '일시': fmt_(now_(), 'yyyy-MM-dd HH:mm:ss'), '이름': who, '기기 ID': device || '', '작업': action,
    '파일': file, '행 ID': id || '',
    '수정 전': before ? JSON.stringify(before) : '', '수정 후': after ? JSON.stringify(after) : ''
  });
}

/** Check and advance a tag's counter. Unknown tags with a valid signature are added. */
function tagCounter_(uid, counter) {
  var t = dbTable_('태그');
  var r = null;
  t.rows.forEach(function (x) { if (x['태그 UID'] === uid) r = x; });
  if (!r) {
    t.append({ '태그 UID': uid, '마지막 카운터': String(counter), '활성': '켬', '처음 찍은 날': stamp_() });
    return;
  }
  if (/^(끔|x|no|false|0|off)$/i.test(r['활성'] || '')) throw fail_('사용하지 않는 태그예요.', 'tag_off');
  if (counter <= +(r['마지막 카운터'] || -1)) throw fail_('이미 쓴 태그 주소예요. 태그를 다시 찍어 주세요.', 'replay');
  setCells_(t, r._row, { '마지막 카운터': String(counter) });
}

/** Optional: nightly copies of the four written files into Lab Web App/백업 (14 days). */
function backup() {
  var folder = DriveApp.getFileById(prop_('DB_SHEET_ID')).getParents().next();
  var it = folder.getFoldersByName('백업');
  var dest = it.hasNext() ? it.next() : folder.createFolder('백업');
  var day = today_();
  ['attendance', 'reservations', 'notices', 'order_requests'].forEach(function (role) {
    var f = DriveApp.getFileById(ids_()[role]);
    f.makeCopy(day + ' ' + f.getName(), dest);
  });
  var cutoff = now_().getTime() - 14 * 86400000;
  var files = dest.getFiles();
  while (files.hasNext()) {
    var f = files.next();
    if (f.getDateCreated().getTime() < cutoff) f.setTrashed(true);
  }
}

/** Run once to turn on the nightly backup (03:00). */
function installBackup() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'backup') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('backup').timeBased().everyDays(1).atHour(3).create();
}
