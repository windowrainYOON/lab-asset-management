/** Small helpers shared by every module. Times are Korea time (the script time zone). */

var TZ = 'Asia/Seoul';

function now_() { return new Date(); }

function fmt_(d, pattern) { return Utilities.formatDate(d, TZ, pattern); }
function today_() { return fmt_(now_(), 'yyyy-MM-dd'); }
function stamp_(d) { return fmt_(d || now_(), 'yyyy-MM-dd HH:mm'); }
function hhmm_(d) { return fmt_(d || now_(), 'HH:mm'); }
function ym_(d) { return fmt_(d || now_(), 'yyyy-MM'); }

/** 'YYYY-MM-DD' -> Date at 00:00 Korea time. */
function dateOf_(s) {
  var p = String(s).split('-');
  return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2], -9, 0, 0));
}

/** 'YYYY-MM-DD HH:MM' -> Date (Korea time). */
function timeOf_(s) {
  var m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})/);
  if (!m) return null;
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] - 9, +m[5], 0));
}

function addDays_(ymd, n) {
  var d = dateOf_(ymd);
  d.setTime(d.getTime() + n * 86400000);
  return fmt_(d, 'yyyy-MM-dd');
}

var WEEKDAYS_ = ['일', '월', '화', '수', '목', '금', '토'];
/** 0 = 일 … 6 = 토 for a 'YYYY-MM-DD' day. */
function dow_(ymd) {
  var p = String(ymd).split('-');
  return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])).getUTCDay();
}
function weekday_(ymd) { return WEEKDAYS_[dow_(ymd)]; }

/** New 8-digit lowercase hex id that is not in `taken` (a set-like object). */
function newId_(taken) {
  for (var i = 0; i < 50; i++) {
    var id = Utilities.getUuid().replace(/-/g, '').slice(0, 8).toLowerCase();
    if (!taken || !taken[id]) return id;
  }
  throw new Error('ID를 만들지 못했습니다');
}

/** Text that is safe to put in a sheet cell: no formula, trimmed, length-capped. */
function cellText_(v, max) {
  var s = v === null || v === undefined ? '' : String(v);
  s = s.replace(/\r\n?/g, '\n');
  if (max && s.length > max) s = s.slice(0, max);
  if (/^[=+\-@]/.test(s)) s = "'" + s;
  return s;
}

/** Value read back from a sheet, as plain text ('' for empty). Dates become 'YYYY-MM-DD[ HH:MM]'. */
function asText_(v) {
  if (v === null || v === undefined) return '';
  if (Object.prototype.toString.call(v) === '[object Date]') {
    if (v.getFullYear() < 1900) return fmt_(v, 'HH:mm');           // a time-only cell
    var t = fmt_(v, 'HH:mm');
    return t === '00:00' ? fmt_(v, 'yyyy-MM-dd') : fmt_(v, 'yyyy-MM-dd HH:mm');
  }
  return String(v).trim();
}

/** '₩ 11,350 ', '-48,000', 11350 -> number (NaN when empty). */
function money_(v) {
  if (typeof v === 'number') return v;
  var s = String(v === null || v === undefined ? '' : v).replace(/[^\d.\-]/g, '');
  return s === '' || s === '-' ? NaN : Number(s);
}

function splitNames_(s) {
  return String(s || '').split(/[,，]/).map(function (x) { return x.trim(); }).filter(String);
}

function truthy_(s) {
  return /^(o|y|yes|true|1|중요|★|완료|✓)$/i.test(String(s || '').trim());
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function fail_(msg, code) {
  var e = new Error(msg);
  e.userFacing = true;
  e.code = code || 'bad_request';
  return e;
}
