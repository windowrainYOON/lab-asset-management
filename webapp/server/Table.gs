/**
 * A sheet tab used as a table: row 1 = header text, one record per row, `ID` column as the key.
 * Columns are found by header TEXT (the desktop app does the same), so their order may change.
 * Writes touch only the cells that change and re-find the row by ID right before writing.
 */

/* Methods are plain function declarations (hoisted), attached in the constructor, so the table works
 * however Apps Script orders or evaluates the files. The name avoids clashing with built-in globals. */
function SheetTable_(sheet, headerRow) {
  this.sheet = sheet;
  this.headerRow = headerRow || 1;
  this._load = tbl_load_;
  this.byId = tbl_byId_;
  this.ids = tbl_ids_;
  this.ensureColumn = tbl_ensureColumn_;
  this.append = tbl_append_;
  this.update = tbl_update_;
  this.remove = tbl_remove_;
  this._load();
}

function tbl_load_() {
  var sh = this.sheet;
  var lastRow = sh.getLastRow(), lastCol = Math.max(sh.getLastColumn(), 1);
  var values = lastRow >= this.headerRow
    ? sh.getRange(this.headerRow, 1, lastRow - this.headerRow + 1, lastCol).getValues() : [[]];
  this.headers = (values[0] || []).map(asText_);
  this.col = {};
  for (var i = 0; i < this.headers.length; i++) {
    if (this.headers[i] && !(this.headers[i] in this.col)) this.col[this.headers[i]] = i + 1;
  }
  this.rows = [];
  for (var r = 1; r < values.length; r++) {
    var row = values[r];
    if (row.every(function (v) { return asText_(v) === ''; })) continue;
    var rec = { _row: this.headerRow + r };
    for (var c = 0; c < this.headers.length; c++) {
      if (this.headers[c]) rec[this.headers[c]] = asText_(row[c]);
    }
    this.rows.push(rec);
  }
}

function tbl_byId_(id) {
  for (var i = 0; i < this.rows.length; i++) if (this.rows[i].ID === id) return this.rows[i];
  return null;
}

function tbl_ids_() {
  var o = {};
  this.rows.forEach(function (r) { if (r.ID) o[r.ID] = true; });
  return o;
}

/** Add a missing header at the right end (only for columns the web app owns). */
function tbl_ensureColumn_(name) {
  if (this.col[name]) return this.col[name];
  var c = this.headers.length + 1;
  this.sheet.getRange(this.headerRow, c).setNumberFormat('@').setValue(name);
  this.headers.push(name);
  this.col[name] = c;
  return c;
}

/** Append a record after the last data row. Every value is written as text. */
function tbl_append_(rec) {
  var width = this.headers.length;
  var line = [];
  for (var c = 0; c < width; c++) {
    var h = this.headers[c];
    line.push(h && rec[h] !== undefined ? cellText_(rec[h]) : '');
  }
  var row = Math.max(this.sheet.getLastRow(), this.headerRow) + 1;
  var range = this.sheet.getRange(row, 1, 1, width);
  range.setNumberFormat('@');
  range.setValues([line]);
  rec._row = row;
  this.rows.push(rec);
  return rec;
}

/** Change some fields of the record with this ID. Returns {before, after} or throws if gone. */
function tbl_update_(id, changes) {
  this._load();                        // re-read: the desktop app may have moved rows
  var rec = this.byId(id);
  if (!rec) throw fail_('그 사이에 지워진 항목이에요. 새로 고쳐 주세요.', 'gone');
  var before = {}, after = {};
  for (var k in changes) {
    if (!this.col[k]) continue;
    var v = cellText_(changes[k]);
    if (rec[k] === String(changes[k])) continue;
    before[k] = rec[k];
    after[k] = changes[k];
    var cell = this.sheet.getRange(rec._row, this.col[k]);
    cell.setNumberFormat('@');
    cell.setValue(v);
    rec[k] = String(changes[k]);
  }
  return { before: before, after: after, record: rec };
}

/** Delete the row with this ID. Returns the record that was removed. */
function tbl_remove_(id) {
  this._load();
  var rec = this.byId(id);
  if (!rec) throw fail_('그 사이에 지워진 항목이에요.', 'gone');
  this.sheet.deleteRow(rec._row);
  this._load();
  return rec;
}

function table_(book, name, headerRow) {
  var sh = typeof name === 'number' ? book.getSheets()[name] : book.getSheetByName(name);
  if (!sh) throw fail_('시트 탭을 찾지 못했습니다: ' + name, 'config');
  return new SheetTable_(sh, headerRow);
}

/** Script lock around a write; waits up to 20 s. */
function withLock_(fn) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) throw fail_('다른 사람이 저장하는 중이에요. 잠시 뒤 다시 해 주세요.', 'busy');
  try { return fn(); } finally { lock.releaseLock(); }
}

/** Per-request read cache so one call reads a tab once. */
var READ_CACHE_ = {};
function cachedTable_(role, tab, headerRow) {
  var key = role + '|' + tab;
  if (!READ_CACHE_[key]) READ_CACHE_[key] = table_(book_(role), tab, headerRow);
  return READ_CACHE_[key];
}
