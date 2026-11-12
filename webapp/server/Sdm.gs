/**
 * NTAG 424 DNA "Secure Dynamic Messaging" check (NXP AN12196), in plain JavaScript because
 * Apps Script has no AES. The tag URL carries
 *   picc = AES-128-CBC(K_meta, IV 0) of [tag 0xC7 | UID 7 bytes | read counter 3 bytes LSB first | random]
 *   cmac = the 8 odd bytes of CMAC(K_session, MAC input) where
 *          K_session = CMAC(K_file, 3C C3 00 01 00 80 | UID | counter) and the MAC input is empty
 *          (the tag is set up with SDMMACInputOffset = SDMMACOffset).
 * The key is script property SDM_KEY (32 hex). SDM_MAC_KEY, when set, is the file read key and
 * SDM_KEY only the meta read key.
 */

// ---- AES-128 (FIPS-197) ------------------------------------------------------------------
var AES_ = (function () {
  var sbox = [], inv = [], x2 = [];
  for (var i = 0; i < 256; i++) x2[i] = (i << 1) ^ (i & 0x80 ? 0x11b : 0);
  // build the S-box from the multiplicative inverse + affine map
  var p = 1, q = 1;
  do {
    p = p ^ (p << 1) ^ (p & 0x80 ? 0x1b : 0); p &= 0xff;
    q ^= q << 1; q ^= q << 2; q ^= q << 4; q &= 0xff; if (q & 0x80) q ^= 0x09;
    var s = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4));
    s = (s ^ 0x63) & 0xff;
    sbox[p] = s; inv[s] = p;
  } while (p !== 1);
  sbox[0] = 0x63; inv[0x63] = 0;

  function mul(a, b) {
    var r = 0;
    while (b) { if (b & 1) r ^= a; a = x2[a]; b >>= 1; }
    return r;
  }

  function expand(key) {
    var w = key.slice(0, 16), rcon = 1;
    for (var i = 16; i < 176; i += 4) {
      var t = w.slice(i - 4, i);
      if (i % 16 === 0) {
        t = [sbox[t[1]] ^ rcon, sbox[t[2]], sbox[t[3]], sbox[t[0]]];
        rcon = x2[rcon];
      }
      for (var j = 0; j < 4; j++) w[i + j] = w[i - 16 + j] ^ t[j];
    }
    return w;
  }

  function addKey(s, w, round) { for (var i = 0; i < 16; i++) s[i] ^= w[round * 16 + i]; }

  function encrypt(block, w) {
    var s = block.slice(0, 16);
    addKey(s, w, 0);
    for (var r = 1; r <= 10; r++) {
      for (var i = 0; i < 16; i++) s[i] = sbox[s[i]];
      s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3], s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]];
      if (r < 10) {
        for (var c = 0; c < 4; c++) {
          var a0 = s[4 * c], a1 = s[4 * c + 1], a2 = s[4 * c + 2], a3 = s[4 * c + 3];
          s[4 * c] = x2[a0] ^ x2[a1] ^ a1 ^ a2 ^ a3;
          s[4 * c + 1] = a0 ^ x2[a1] ^ x2[a2] ^ a2 ^ a3;
          s[4 * c + 2] = a0 ^ a1 ^ x2[a2] ^ x2[a3] ^ a3;
          s[4 * c + 3] = x2[a0] ^ a0 ^ a1 ^ a2 ^ x2[a3];
        }
      }
      addKey(s, w, r);
    }
    return s;
  }

  function decrypt(block, w) {
    var s = block.slice(0, 16);
    addKey(s, w, 10);
    for (var r = 9; r >= 0; r--) {
      s = [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11], s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]];
      for (var i = 0; i < 16; i++) s[i] = inv[s[i]];
      addKey(s, w, r);
      if (r > 0) {
        for (var c = 0; c < 4; c++) {
          var a0 = s[4 * c], a1 = s[4 * c + 1], a2 = s[4 * c + 2], a3 = s[4 * c + 3];
          s[4 * c] = mul(a0, 14) ^ mul(a1, 11) ^ mul(a2, 13) ^ mul(a3, 9);
          s[4 * c + 1] = mul(a0, 9) ^ mul(a1, 14) ^ mul(a2, 11) ^ mul(a3, 13);
          s[4 * c + 2] = mul(a0, 13) ^ mul(a1, 9) ^ mul(a2, 14) ^ mul(a3, 11);
          s[4 * c + 3] = mul(a0, 11) ^ mul(a1, 13) ^ mul(a2, 9) ^ mul(a3, 14);
        }
      }
    }
    return s;
  }

  return { expand: expand, encrypt: encrypt, decrypt: decrypt };
})();

function hexBytes_(hex) {
  var h = String(hex || '').replace(/\s/g, '');
  if (!/^([0-9a-fA-F]{2})*$/.test(h)) throw fail_('태그 값이 올바르지 않아요.', 'tag');
  var out = [];
  for (var i = 0; i < h.length; i += 2) out.push(parseInt(h.substr(i, 2), 16));
  return out;
}

function bytesHex_(b) {
  return b.map(function (x) { return ('0' + (x & 0xff).toString(16)).slice(-2); }).join('').toUpperCase();
}

/** AES-CMAC (RFC 4493). */
function cmac_(key, msg) {
  var w = AES_.expand(key);
  function shift(b) {
    var out = [], carry = 0;
    for (var i = 15; i >= 0; i--) { out[i] = ((b[i] << 1) | carry) & 0xff; carry = b[i] >> 7; }
    if (b[0] & 0x80) out[15] ^= 0x87;
    return out;
  }
  var L = AES_.encrypt([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], w);
  var k1 = shift(L), k2 = shift(k1);
  var n = Math.max(1, Math.ceil(msg.length / 16));
  var complete = msg.length > 0 && msg.length % 16 === 0;
  var last = msg.slice((n - 1) * 16);
  if (complete) last = last.map(function (x, i) { return x ^ k1[i]; });
  else {
    last = last.concat([0x80]);
    while (last.length < 16) last.push(0);
    last = last.map(function (x, i) { return x ^ k2[i]; });
  }
  var x = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  for (var b = 0; b < n - 1; b++) {
    var blk = msg.slice(b * 16, b * 16 + 16);
    x = AES_.encrypt(x.map(function (v, i) { return v ^ blk[i]; }), w);
  }
  return AES_.encrypt(x.map(function (v, i) { return v ^ last[i]; }), w);
}

/** {uid, counter} when the tag values are genuine; throws otherwise. */
function sdmVerify_(piccHex, cmacHex, metaKeyHex, fileKeyHex) {
  var picc = hexBytes_(piccHex), mac = hexBytes_(cmacHex);
  if (picc.length !== 16 || mac.length !== 8) throw fail_('태그 값이 올바르지 않아요.', 'tag');
  var kMeta = hexBytes_(metaKeyHex), kFile = hexBytes_(fileKeyHex || metaKeyHex);
  if (kMeta.length !== 16 || kFile.length !== 16) throw fail_('서버에 태그 열쇠가 아직 없어요.', 'config');
  var plain = AES_.decrypt(picc, AES_.expand(kMeta));     // one block, IV 0 → plain = decrypt
  if (plain[0] !== 0xC7) throw fail_('연구실 태그가 아니에요.', 'tag');
  var uid = plain.slice(1, 8), ctr = plain.slice(8, 11);
  var sv2 = [0x3C, 0xC3, 0x00, 0x01, 0x00, 0x80].concat(uid, ctr);
  var kSes = cmac_(kFile, sv2);
  var full = cmac_(kSes, []);
  var want = [full[1], full[3], full[5], full[7], full[9], full[11], full[13], full[15]];
  var diff = 0;
  for (var i = 0; i < 8; i++) diff |= want[i] ^ mac[i];
  if (diff) throw fail_('태그 서명이 맞지 않아요. 다시 찍어 주세요.', 'tag');
  return { uid: bytesHex_(uid), counter: ctr[0] | (ctr[1] << 8) | (ctr[2] << 16) };
}
