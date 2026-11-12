/**
 * 예산 입출 내역 (read only). Ledger = the first tab whose header has 날짜 내용 수입 지출 (잔액);
 * lab-fee rounds = tabs "랩비 YYYY.MM" (기준일 …, then Name 이름 Unpaid carryover Lab fee Total 직접 수정 메모).
 * Paid = "<이름> 랩비" deposits in the ledger on/after the round's 기준일 (desktop app budget.py).
 * Everyone sees everything (user decision D2).
 */

function parseDay_(v) {
  var m = String(v || '').match(/(\d{2,4})[.\-\/]\s*(\d{1,2})[.\-\/]\s*(\d{1,2})/);
  if (!m) return '';
  var y = +m[1]; if (y < 100) y += 2000;
  return y + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[3]).slice(-2);
}

function budgetData_() {
  return cached_('budget', 'all', function () {
    var book = book_('budget');
    var ledger = [], rounds = [];
    book.getSheets().forEach(function (sh) {
      var name = sh.getName();
      var n = sh.getLastRow(), w = sh.getLastColumn();
      if (n < 1 || w < 1) return;
      var vals = sh.getRange(1, 1, n, Math.min(w, 12)).getDisplayValues();
      if (name.indexOf('랩비 ') === 0) { rounds.push(readRound_(name, vals)); return; }
      if (ledger.length) return;
      for (var i = 0; i < Math.min(15, vals.length); i++) {
        var cells = vals[i].map(function (c) { return String(c).trim(); });
        if (['날짜', '내용', '수입', '지출'].every(function (h) { return cells.indexOf(h) >= 0; })) {
          var col = {}; ['날짜', '내용', '수입', '지출', '잔액'].forEach(function (h) { col[h] = cells.indexOf(h); });
          var prev = 0;
          for (var r = i + 1; r < vals.length; r++) {
            var row = vals[r];
            var get = function (h) { return col[h] >= 0 ? row[col[h]] : ''; };
            var inc = money_(get('수입')), exp = money_(get('지출')), bal = money_(get('잔액'));
            if (!get('날짜') && !get('내용') && isNaN(inc) && isNaN(exp) && isNaN(bal)) continue;
            if (isNaN(bal)) bal = prev + (isNaN(inc) ? 0 : inc) - (isNaN(exp) ? 0 : exp);
            prev = bal;
            ledger.push({ date: String(get('날짜')).trim(), day: parseDay_(get('날짜')), desc: String(get('내용')).trim(),
                          income: isNaN(inc) ? null : inc, expense: isNaN(exp) ? null : exp, balance: bal });
          }
          break;
        }
      }
    });
    rounds.sort(function (a, b) { return a.label < b.label ? -1 : 1; });
    return { ledger: ledger, rounds: rounds };
  });
}

function readRound_(name, vals) {
  var rnd = { label: name.slice(3), made: '', target: 0, balance: 0, fee: 0, lines: [] };
  var head = -1;
  for (var i = 0; i < vals.length; i++) {
    var k = String(vals[i][0]).trim(), v = vals[i][1];
    if (k === '기준일') rnd.made = parseDay_(v);
    else if (k === 'Target amount') rnd.target = money_(v) || 0;
    else if (k === 'Current balance') rnd.balance = money_(v) || 0;
    else if (k === 'Lab fee' || k === 'Lab fee (직접)') rnd.fee = money_(v) || rnd.fee;
    else if (k === 'Name' && vals[i].map(String).indexOf('Unpaid carryover') >= 0) { head = i; break; }
  }
  if (head < 0) return rnd;
  var h = vals[head].map(function (c) { return String(c).trim(); });
  var c = function (x) { return h.indexOf(x); };
  for (var r = head + 1; r < vals.length; r++) {
    var row = vals[r];
    var en = String(row[c('Name')] || '').trim();
    if (!en || /^total/i.test(en)) continue;
    var carry = money_(row[c('Unpaid carryover')]) || 0;
    var fee = c('Lab fee') >= 0 && !isNaN(money_(row[c('Lab fee')])) ? money_(row[c('Lab fee')]) : rnd.fee;
    var total = c('Total') >= 0 && !isNaN(money_(row[c('Total')])) ? money_(row[c('Total')]) : carry + fee;
    rnd.lines.push({ english: en, name: String(row[c('이름')] || '').trim() || en, carryover: carry, fee: fee, total: total });
  }
  return rnd;
}

function paidSince_(ledger, names, since) {
  var total = 0, last = '';
  ledger.forEach(function (e) {
    if (!e.income || e.desc.indexOf('랩비') < 0) return;
    if (since && (!e.day || e.day < since)) return;
    if (names.some(function (n) { return n && e.desc.indexOf(n) >= 0; })) { total += e.income; last = e.date; }
  });
  return { paid: total, last: last };
}

function lineStatus_(line, data, rnd) {
  var p = paidSince_(data.ledger, [line.name, line.english], rnd.made);
  var state = line.total <= 0 ? '없음' : p.paid >= line.total ? '납부 완료' : p.paid > 0 ? '일부 납부' : '미납';
  return { name: line.name, english: line.english, carryover: line.carryover, fee: line.fee, total: line.total,
           paid: p.paid, paidOn: p.last, state: state, left: Math.max(0, line.total - p.paid) };
}

/** action budget.mine */
function budgetMine_(body, member) {
  var data = budgetData_();
  var rnd = data.rounds[data.rounds.length - 1];
  if (!rnd) return { round: null };
  var line = null;
  rnd.lines.forEach(function (l) {
    if (samePerson_(l.name, member.name) || (member.english && samePerson_(l.english, member.english))) line = l;
  });
  return { round: { label: rnd.label, made: rnd.made, fee: rnd.fee }, mine: line ? lineStatus_(line, data, rnd) : null };
}

/** action budget.lab: {limit} — balance, recent ledger, this round for everyone. */
function budgetLab_(body, member) {
  var data = budgetData_();
  var limit = Math.min(Math.max(parseInt(body.limit, 10) || 15, 1), 2000);
  var rnd = data.rounds[data.rounds.length - 1];
  var last = data.ledger[data.ledger.length - 1];
  var mine = budgetMine_(body, member);
  return {
    balance: last ? last.balance : 0, entries: data.ledger.length,
    ledger: data.ledger.slice(-limit).reverse(),
    round: rnd ? { label: rnd.label, made: rnd.made, fee: rnd.fee, target: rnd.target,
                   lines: rnd.lines.map(function (l) { return lineStatus_(l, data, rnd); }) } : null,
    mine: mine.mine
  };
}
