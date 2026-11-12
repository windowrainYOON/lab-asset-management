/**
 * Where the data lives and the shared settings (Asset Manager 설정.json).
 * Script properties: CONFIG_FILE_ID (settings JSON), DB_SHEET_ID (웹앱 DB, made by setup()),
 * ENTRY_SECRET (made automatically), SDM_KEY (NFC tag key, 32 hex), ALLOW_PLAIN_TAP ('1' = accept a
 * tap without the tag signature, for testing before the tags are set up).
 */

// File ids of this lab's sheets and the first lab password (hash only) are private: they live in
// private/Private.gs (gitignored, copy of private.example.gs), which tools/bundle.js puts first in
// paste/Code.gs. The ids are used when the settings JSON still points at an old .xlsx.
var PRIVATE_DEFAULTS_ = (typeof PRIVATE_ !== 'undefined' && PRIVATE_) || {};
var DEFAULT_IDS = PRIVATE_DEFAULTS_.ids || {};
var ROLES_ = ['attendance', 'reservations', 'notices', 'order_requests', 'work', 'budget'];
var SHEET_MIME_ = 'application/vnd.google-apps.spreadsheet';

// The lab password until someone changes it in the desktop app (PBKDF2-SHA256, no plain text).
var DEFAULT_PIN = (PRIVATE_DEFAULTS_.pin && PRIVATE_DEFAULTS_.pin.salt && PRIVATE_DEFAULTS_.pin.hash) ? PRIVATE_DEFAULTS_.pin : null;

var HOLIDAY_CALENDARS = ['ko.south_korea.official#holiday@group.v.calendar.google.com',
                         'ko.south_korea#holiday@group.v.calendar.google.com'];

function prop_(key) { return PropertiesService.getScriptProperties().getProperty(key); }

/** The settings JSON, cached for 60 s. */
function config_(fresh) {
  var cache = CacheService.getScriptCache();
  if (!fresh) {
    var hit = cache.get('config');
    if (hit) return JSON.parse(hit);
  }
  var id = prop_('CONFIG_FILE_ID') || DEFAULT_IDS.overlay;
  if (!id) throw fail_('설정 파일 위치(CONFIG_FILE_ID)가 없어요.', 'config');
  var text = DriveApp.getFileById(id).getBlob().getDataAsString('UTF-8');
  var cfg = JSON.parse(text);
  try { cache.put('config', JSON.stringify(cfg), 60); } catch (e) { /* > 100 KB: just don't cache */ }
  return cfg;
}

/** Drive id of each data file. Only these ids are ever opened (the whitelist). */
function ids_() {
  var cache = CacheService.getScriptCache();
  var hit = cache.get('ids');
  if (hit) return JSON.parse(hit);
  var out = {};
  var sets = (config_().location_sets || []);
  var locs = sets.length ? (sets[0].locations || {}) : {};
  ROLES_.forEach(function (role) {
    var id = locs[role] && locs[role].id;
    var ok = false;
    if (id) {
      try { ok = DriveApp.getFileById(id).getMimeType() === SHEET_MIME_; } catch (e) { ok = false; }
    }
    out[role] = ok ? id : DEFAULT_IDS[role];
  });
  cache.put('ids', JSON.stringify(out), 21600);
  return out;
}

function book_(role) {
  var id = ids_()[role];
  if (!id) throw fail_('데이터 위치를 찾지 못했습니다: ' + role, 'config');
  return SpreadsheetApp.openById(id);
}

/** Lab members from the settings JSON: [{name, email, english}]. */
function members_() {
  return (config_().members || []).filter(function (m) { return m && m.name; });
}

function memberByName_(name) {
  var n = String(name || '').trim();
  var hit = null;
  members_().forEach(function (m) { if (m.name === n) hit = m; });
  return hit;
}
