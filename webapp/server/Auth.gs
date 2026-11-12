/**
 * Who may call the server.
 * 1. Lab password (enter): checked against settings JSON webapp.pin (PBKDF2-SHA256), or DEFAULT_PIN
 *    until someone sets one in the desktop app. 5 wrong tries per phone per 10 minutes.
 * 2. Entry key: "<device>.<version>.<signature>", signed with script property ENTRY_SECRET.
 *    A key stays valid while webapp.pin.version is unchanged (the desktop app bumps the version
 *    when "이미 들어온 휴대폰은 계속 열어 두기" is off, so every phone has to enter again).
 * 3. Member: the name the phone picked must be in the settings JSON members list.
 */

function pinRecord_() {
  var w = config_().webapp || {};
  var p = w.pin;
  if (p && p.salt && p.hash) return { salt: p.salt, hash: p.hash, iterations: +p.iterations || 2000, version: +p.version || 1 };
  if (DEFAULT_PIN) return DEFAULT_PIN;
  throw fail_('연구실 비밀번호가 아직 정해지지 않았어요. 데스크톱 앱에서 정해 주세요.', 'config');
}

/** PBKDF2-HMAC-SHA256 with one 32-byte block, base64. Same as Python hashlib.pbkdf2_hmac. */
function pbkdf2_(password, saltB64, iterations) {
  var pw = Utilities.newBlob(String(password)).getBytes();
  var salt = Utilities.base64Decode(saltB64);
  var u = Utilities.computeHmacSignature(Utilities.MacAlgorithm.HMAC_SHA_256, salt.concat([0, 0, 0, 1]), pw);
  var t = u.slice();
  for (var i = 1; i < iterations; i++) {
    u = Utilities.computeHmacSignature(Utilities.MacAlgorithm.HMAC_SHA_256, u, pw);
    for (var j = 0; j < t.length; j++) t[j] ^= u[j];
  }
  return Utilities.base64Encode(t);
}

function entrySecret_() {
  var props = PropertiesService.getScriptProperties();
  var s = props.getProperty('ENTRY_SECRET');
  if (!s) {
    s = Utilities.getUuid() + Utilities.getUuid();
    props.setProperty('ENTRY_SECRET', s);
  }
  return s;
}

function sign_(text) {
  var sig = Utilities.computeHmacSha256Signature(text, entrySecret_());
  return Utilities.base64EncodeWebSafe(sig).replace(/=+$/, '');
}

function cleanDevice_(d) {
  var s = String(d || '');
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(s)) throw fail_('이 휴대폰을 알아보지 못했어요. 새로 고쳐 주세요.', 'device');
  return s;
}

/** action enter: {pin, device} → {key} */
function enter_(body) {
  var device = cleanDevice_(body.device);
  var cache = CacheService.getScriptCache();
  var tries = +(cache.get('pinfail:' + device) || 0);
  if (tries >= 5) throw fail_('비밀번호를 여러 번 틀렸어요. 10분 뒤에 다시 해 주세요.', 'locked');
  var rec = pinRecord_();
  var ok = String(body.pin || '').length > 0 && pbkdf2_(body.pin, rec.salt, rec.iterations) === rec.hash;
  if (!ok) {
    cache.put('pinfail:' + device, String(tries + 1), 600);
    log_('접속 비밀번호 틀림', '', device, '', '', '거부');
    throw fail_('비밀번호가 맞지 않아요.' + (tries + 1 >= 5 ? ' 10분 뒤에 다시 해 주세요.' : ''), 'wrong_pin');
  }
  cache.remove('pinfail:' + device);
  var payload = device + '.' + rec.version;
  return { key: payload + '.' + sign_(payload) };
}

/** Throws unless the entry key is genuine and current. */
function checkKey_(body) {
  var device = cleanDevice_(body.device);
  var parts = String(body.key || '').split('.');
  if (parts.length !== 3 || parts[0] !== device) throw fail_('연구실 비밀번호를 넣어 주세요.', 'need_pin');
  var payload = parts[0] + '.' + parts[1];
  if (sign_(payload) !== parts[2]) throw fail_('연구실 비밀번호를 넣어 주세요.', 'need_pin');
  if (+parts[1] !== pinRecord_().version) throw fail_('연구실 비밀번호가 바뀌었어요. 새 비밀번호를 넣어 주세요.', 'need_pin');
  return device;
}

/** The calling member {name, email, english}, or throws (asks the phone to pick a name). */
function checkMember_(body) {
  var m = memberByName_(body.member);
  if (!m) throw fail_('누구인지 골라 주세요.', 'need_member');
  return m;
}

/** action members: names to pick from (no e-mail addresses). */
function membersList_() {
  return { members: members_().map(function (m) { return { name: m.name, english: m.english || '' }; }) };
}

/** action pick: the phone chose a name. */
function pick_(body, device) {
  var m = checkMember_(body);
  touchDevice_(m.name, device, true);
  return { member: m.name };
}
