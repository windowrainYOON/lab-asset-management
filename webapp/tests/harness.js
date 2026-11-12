/* Runs the Apps Script files in Node with small in-memory stand-ins for the Google services.
 * Only what the server uses is implemented. Usage: const h = require('./harness'); h.load(opts) */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const SERVER = path.join(__dirname, '..', 'server');

function colNum(letters) {
  let n = 0;
  for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n;
}

class Range {
  constructor(sheet, row, col, nr, nc) { Object.assign(this, { sheet, row, col, nr, nc }); }
  getValues() {
    const out = [];
    for (let r = 0; r < this.nr; r++) {
      const line = [];
      for (let c = 0; c < this.nc; c++) {
        const v = (this.sheet.data[this.row - 1 + r] || [])[this.col - 1 + c];
        line.push(v === undefined || v === null ? '' : v);
      }
      out.push(line);
    }
    return out;
  }
  getDisplayValues() { return this.getValues().map(r => r.map(v => String(v))); }
  setValues(vals) {
    vals.forEach((line, r) => line.forEach((v, c) => { this.sheet.set(this.row + r, this.col + c, v); this.sheet.writes.push([this.row + r, this.col + c, v]); }));
    return this;
  }
  setValue(v) {
    // like Sheets: a leading ' marks text and is not shown
    this.sheet.set(this.row, this.col, typeof v === 'string' && v.startsWith("'") ? v.slice(1) : v);
    this.sheet.writes.push([this.row, this.col, v]);
    return this;
  }
  setNumberFormat() { return this; }
  setFontWeight() { return this; }
}

class Sheet {
  constructor(book, name, data) { this.book = book; this.name = name; this.data = data || []; this.writes = []; }
  getName() { return this.name; }
  getIndex() { return this.book.sheets.indexOf(this) + 1; }
  set(r, c, v) {
    while (this.data.length < r) this.data.push([]);
    const row = this.data[r - 1];
    while (row.length < c) row.push('');
    row[c - 1] = typeof v === 'string' && v.startsWith("'") ? v.slice(1) : v;
  }
  getLastRow() {
    for (let r = this.data.length; r > 0; r--) if ((this.data[r - 1] || []).some(v => v !== '' && v !== undefined && v !== null)) return r;
    return 0;
  }
  getLastColumn() {
    let w = 0;
    this.data.forEach(row => { for (let c = row.length; c > w; c--) if (row[c - 1] !== '' && row[c - 1] !== undefined) { w = c; break; } });
    return w;
  }
  getRange(a, b, c, d) {
    if (typeof a === 'string') {
      const m = a.match(/^([A-Z]+)\d*:([A-Z]+)\d*$/);
      return new Range(this, 1, colNum(m[1]), 1000, colNum(m[2]) - colNum(m[1]) + 1);
    }
    return new Range(this, a, b, c || 1, d || 1);
  }
  deleteRow(r) { this.data.splice(r - 1, 1); }
  setFrozenRows() {} setFrozenColumns() {}
}

class Book {
  constructor(id, tabs) {
    this.id = id;
    this.sheets = Object.keys(tabs || {}).map(n => new Sheet(this, n, tabs[n]));
  }
  getId() { return this.id; }
  getUrl() { return 'https://docs.google.com/spreadsheets/d/' + this.id; }
  getSheets() { return this.sheets; }
  getSheetByName(n) { return this.sheets.find(s => s.name === n) || null; }
  insertSheet(n, index) {
    const s = new Sheet(this, n, []);
    if (index === undefined) this.sheets.push(s); else this.sheets.splice(index, 0, s);
    return s;
  }
  deleteSheet(s) { this.sheets.splice(this.sheets.indexOf(s), 1); }
}

function formatDate(d, tz, pattern) {
  const parts = {};
  new Intl.DateTimeFormat('en-GB', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
    .formatToParts(d).forEach(p => { parts[p.type] = p.value; });
  return pattern.replace('yyyy', parts.year).replace('MM', parts.month).replace('dd', parts.day)
    .replace('HH', parts.hour).replace('mm', parts.minute).replace('ss', parts.second);
}

function signed(buf) { return Array.from(buf).map(b => (b > 127 ? b - 256 : b)); }
function unsigned(arr) { return Buffer.from(arr.map(b => b & 0xff)); }
function asBuf(x) { return typeof x === 'string' ? Buffer.from(x, 'utf8') : unsigned(x); }

/**
 * opts: {books: {id: {tab: rows}}, config: {...settings JSON}, props: {...}, now: Date,
 *        calendars: {calId: [events]}}
 * Returns the script context plus helpers: call(body), books, mails, ctx.
 */
function load(opts) {
  opts = opts || {};
  const books = {};
  Object.keys(opts.books || {}).forEach(id => { books[id] = new Book(id, opts.books[id]); });
  const props = Object.assign({}, opts.props || {});
  const cache = {};
  const mails = [];
  let clock = opts.now ? new Date(opts.now) : new Date();
  const files = { [opts.configId || 'CONFIG']: { name: 'Asset Manager 설정.json', text: JSON.stringify(opts.config || {}) } };
  Object.keys(opts.files || {}).forEach(id => { files[id] = opts.files[id]; });
  let newBook = 0;

  const ctx = {
    console,
    Utilities: {
      formatDate,
      getUuid: () => crypto.randomUUID(),
      newBlob: s => ({ getBytes: () => signed(Buffer.from(String(s), 'utf8')) }),
      base64Decode: s => signed(Buffer.from(s, 'base64')),
      base64Encode: a => unsigned(a).toString('base64'),
      base64EncodeWebSafe: a => unsigned(a).toString('base64').replace(/\+/g, '-').replace(/\//g, '_'),
      MacAlgorithm: { HMAC_SHA_256: 'sha256' },
      computeHmacSignature: (alg, value, key) => signed(crypto.createHmac(alg, asBuf(key)).update(asBuf(value)).digest()),
      computeHmacSha256Signature: (value, key) => signed(crypto.createHmac('sha256', asBuf(key)).update(asBuf(value)).digest())
    },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = String(v); } }) },
    CacheService: { getScriptCache: () => ({
      get: k => (k in cache ? cache[k] : null), put: (k, v) => { cache[k] = String(v); },
      remove: k => { delete cache[k]; }, removeAll: ks => ks.forEach(k => delete cache[k]) }) },
    LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} }) },
    SpreadsheetApp: {
      openById: id => { if (!books[id]) throw new Error('no book ' + id); return books[id]; },
      create: name => { const id = 'DB' + (++newBook); books[id] = new Book(id, { Sheet1: [] }); return books[id]; }
    },
    DriveApp: {
      getFileById: id => {
        const f = files[id] || (books[id] && { name: id, mime: 'application/vnd.google-apps.spreadsheet' });
        if (!f) throw new Error('no file ' + id);
        return { getBlob: () => ({ getDataAsString: () => f.text }), getMimeType: () => f.mime || 'application/json',
                 getName: () => f.name, isTrashed: () => !!f.trashed, moveTo: () => {} };
      },
      getFoldersByName: () => ({ hasNext: () => false }),
      createFolder: () => ({})
    },
    Calendar: { Events: { list: (calId, q) => ({ items: ((opts.calendars || {})[calId] || []).filter(ev => {
      const t = x => new Date(x.dateTime || (x.date + 'T00:00:00+09:00')).getTime();
      return t(ev.start) < new Date(q.timeMax).getTime() && new Date(q.timeMin).getTime() < t(ev.end);
    }) }) } },
    MailApp: { sendEmail: m => { mails.push(m); } },
    ContentService: { createTextOutput: t => ({ text: t, setMimeType() { return this; } }), MimeType: { JSON: 'json' } },
    ScriptApp: {},
    Logger: { log: () => {} }
  };
  vm.createContext(ctx);
  // the clock: every `new Date()` without arguments is the test clock
  vm.runInContext(`
    (function () {
      var RealDate = Date;
      function FakeDate() {
        if (!(this instanceof FakeDate)) return new RealDate(__clock()).toString();
        var a = arguments;
        var d = a.length === 0 ? new RealDate(__clock()) : new (Function.prototype.bind.apply(RealDate, [null].concat([].slice.call(a))))();
        Object.setPrototypeOf(d, FakeDate.prototype);
        return d;
      }
      FakeDate.prototype = Object.create(RealDate.prototype);
      FakeDate.prototype.constructor = FakeDate;
      FakeDate.UTC = RealDate.UTC; FakeDate.parse = RealDate.parse; FakeDate.now = function () { return __clock(); };
      Date = FakeDate;
    })();`, Object.assign(ctx, { __clock: () => clock.getTime() }));
  // private/Private.gs stand-in (the real one is gitignored)
  if (opts.private) vm.runInContext('var PRIVATE_ = ' + JSON.stringify(opts.private) + ';', ctx);
  if (opts.bundle) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'paste', 'Code.gs'), 'utf8'), ctx, { filename: 'Code.gs' });
  } else {
    for (const f of fs.readdirSync(SERVER).filter(f => f.endsWith('.gs')).sort()) {
      vm.runInContext(fs.readFileSync(path.join(SERVER, f), 'utf8'), ctx, { filename: f });
    }
  }
  if (opts.ids) vm.runInContext('DEFAULT_IDS = ' + JSON.stringify(opts.ids), ctx);
  props.CONFIG_FILE_ID = props.CONFIG_FILE_ID || opts.configId || 'CONFIG';

  return {
    ctx, books, mails, props, cache,
    setNow: d => { clock = new Date(d); },
    setConfig: cfg => { files[opts.configId || 'CONFIG'].text = JSON.stringify(cfg); delete cache.config; },
    call: body => JSON.parse(JSON.stringify(ctx.handle_(body))),
    run: code => vm.runInContext(code, ctx)
  };
}

module.exports = { load, Book, formatDate };
