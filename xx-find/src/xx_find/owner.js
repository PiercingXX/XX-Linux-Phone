/* xx-find owner web crypto: canonical JSON, Ed25519 signing, sealed-box
   open, tile math. Pure logic, no DOM and no network: the same file runs
   in the browser, under node/deno/qjs for the vector check, and is
   embedded verbatim into the server UI pages. ASCII only. */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.XXFIND = api;
})(typeof globalThis !== "undefined" ? globalThis
  : (typeof self !== "undefined" ? self : this), function () {
  "use strict";

  var JS_SAFE_INT = 9007199254740991;

  function u8(n) {
    return new Uint8Array(n);
  }

  function concat() {
    var total = 0, i, out, off;
    for (i = 0; i < arguments.length; i++) {
      total += arguments[i].length;
    }
    out = u8(total);
    off = 0;
    for (i = 0; i < arguments.length; i++) {
      out.set(arguments[i], off);
      off += arguments[i].length;
    }
    return out;
  }

  function hexToBytes(hex) {
    if (typeof hex !== "string" || hex.length % 2 !== 0 ||
        !/^[0-9a-fA-F]*$/.test(hex)) {
      throw new Error("bad hex");
    }
    var out = u8(hex.length / 2), i;
    for (i = 0; i < out.length; i++) {
      out[i] = parseInt(hex.substr(i * 2, 2), 16);
    }
    return out;
  }

  function bytesToHex(bytes) {
    var s = "", i;
    for (i = 0; i < bytes.length; i++) {
      s += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    }
    return s;
  }

  function base64ToBytes(text) {
    var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" +
      "0123456789+/";
    var clean = String(text).replace(/\s+/g, "");
    if (clean.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(clean)) {
      throw new Error("bad base64");
    }
    var pad = 0;
    if (clean.charAt(clean.length - 1) === "=") { pad++; }
    if (clean.charAt(clean.length - 2) === "=") { pad++; }
    var out = u8(clean.length / 4 * 3 - pad);
    var i, j = 0, n;
    for (i = 0; i < clean.length; i += 4) {
      n = (chars.indexOf(clean.charAt(i)) << 18) |
        (chars.indexOf(clean.charAt(i + 1)) << 12) |
        ((clean.charAt(i + 2) === "=" ? 0
          : chars.indexOf(clean.charAt(i + 2))) << 6) |
        (clean.charAt(i + 3) === "=" ? 0
          : chars.indexOf(clean.charAt(i + 3)));
      if (j < out.length) { out[j++] = (n >>> 16) & 255; }
      if (j < out.length) { out[j++] = (n >>> 8) & 255; }
      if (j < out.length) { out[j++] = n & 255; }
    }
    return out;
  }

  function bytesToBase64(bytes) {
    var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" +
      "0123456789+/";
    var s = "", i, n;
    for (i = 0; i < bytes.length; i += 3) {
      n = (bytes[i] << 16) |
        ((i + 1 < bytes.length ? bytes[i + 1] : 0) << 8) |
        (i + 2 < bytes.length ? bytes[i + 2] : 0);
      s += chars.charAt((n >>> 18) & 63) + chars.charAt((n >>> 12) & 63) +
        (i + 1 < bytes.length ? chars.charAt((n >>> 6) & 63) : "=") +
        (i + 2 < bytes.length ? chars.charAt(n & 63) : "=");
    }
    return s;
  }

  function utf8ToBytes(text) {
    var units = [], i, c;
    for (i = 0; i < text.length; i++) {
      c = text.charCodeAt(i);
      if (c < 0x80) {
        units.push(c);
      } else if (c < 0x800) {
        units.push(0xc0 | (c >>> 6), 0x80 | (c & 0x3f));
      } else if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length) {
        var lo = text.charCodeAt(i + 1);
        if (lo >= 0xdc00 && lo <= 0xdfff) {
          var cp = 0x10000 + ((c - 0xd800) << 10) + (lo - 0xdc00);
          units.push(0xf0 | (cp >>> 18), 0x80 | ((cp >>> 12) & 0x3f),
            0x80 | ((cp >>> 6) & 0x3f), 0x80 | (cp & 0x3f));
          i++;
        } else {
          units.push(0xe0 | (c >>> 12), 0x80 | ((c >>> 6) & 0x3f),
            0x80 | (c & 0x3f));
        }
      } else {
        units.push(0xe0 | (c >>> 12), 0x80 | ((c >>> 6) & 0x3f),
          0x80 | (c & 0x3f));
      }
    }
    return new Uint8Array(units);
  }

  function bytesToUtf8(bytes) {
    var s = "", i = 0, b, c2, c3, c4;
    while (i < bytes.length) {
      b = bytes[i++];
      if (b < 0x80) {
        s += String.fromCharCode(b);
      } else if ((b & 0xe0) === 0xc0 && i < bytes.length) {
        c2 = bytes[i++];
        s += String.fromCharCode(((b & 0x1f) << 6) | (c2 & 0x3f));
      } else if ((b & 0xf0) === 0xe0 && i + 1 < bytes.length) {
        c2 = bytes[i++];
        c3 = bytes[i++];
        s += String.fromCharCode(((b & 0x0f) << 12) |
          ((c2 & 0x3f) << 6) | (c3 & 0x3f));
      } else if ((b & 0xf8) === 0xf0 && i + 2 < bytes.length) {
        c2 = bytes[i++];
        c3 = bytes[i++];
        c4 = bytes[i++];
        var cp = ((b & 0x07) << 18) | ((c2 & 0x3f) << 12) |
          ((c3 & 0x3f) << 6) | (c4 & 0x3f);
        cp -= 0x10000;
        s += String.fromCharCode(0xd800 + (cp >>> 10),
          0xdc00 + (cp & 0x3ff));
      } else {
        throw new Error("bad utf-8");
      }
    }
    return s;
  }

  /* SHA-512 (FIPS 180-4). Constants are the fractional parts of the
     cube roots of the first 80 primes; generated offline from that
     definition and checked against hashlib test vectors. */
  var SHA512_K = [
    0x428a2f98, 0xd728ae22, 0x71374491, 0x23ef65cd,
    0xb5c0fbcf, 0xec4d3b2f, 0xe9b5dba5, 0x8189dbbc,
    0x3956c25b, 0xf348b538, 0x59f111f1, 0xb605d019,
    0x923f82a4, 0xaf194f9b, 0xab1c5ed5, 0xda6d8118,
    0xd807aa98, 0xa3030242, 0x12835b01, 0x45706fbe,
    0x243185be, 0x4ee4b28c, 0x550c7dc3, 0xd5ffb4e2,
    0x72be5d74, 0xf27b896f, 0x80deb1fe, 0x3b1696b1,
    0x9bdc06a7, 0x25c71235, 0xc19bf174, 0xcf692694,
    0xe49b69c1, 0x9ef14ad2, 0xefbe4786, 0x384f25e3,
    0x0fc19dc6, 0x8b8cd5b5, 0x240ca1cc, 0x77ac9c65,
    0x2de92c6f, 0x592b0275, 0x4a7484aa, 0x6ea6e483,
    0x5cb0a9dc, 0xbd41fbd4, 0x76f988da, 0x831153b5,
    0x983e5152, 0xee66dfab, 0xa831c66d, 0x2db43210,
    0xb00327c8, 0x98fb213f, 0xbf597fc7, 0xbeef0ee4,
    0xc6e00bf3, 0x3da88fc2, 0xd5a79147, 0x930aa725,
    0x06ca6351, 0xe003826f, 0x14292967, 0x0a0e6e70,
    0x27b70a85, 0x46d22ffc, 0x2e1b2138, 0x5c26c926,
    0x4d2c6dfc, 0x5ac42aed, 0x53380d13, 0x9d95b3df,
    0x650a7354, 0x8baf63de, 0x766a0abb, 0x3c77b2a8,
    0x81c2c92e, 0x47edaee6, 0x92722c85, 0x1482353b,
    0xa2bfe8a1, 0x4cf10364, 0xa81a664b, 0xbc423001,
    0xc24b8b70, 0xd0f89791, 0xc76c51a3, 0x0654be30,
    0xd192e819, 0xd6ef5218, 0xd6990624, 0x5565a910,
    0xf40e3585, 0x5771202a, 0x106aa070, 0x32bbd1b8,
    0x19a4c116, 0xb8d2d0c8, 0x1e376c08, 0x5141ab53,
    0x2748774c, 0xdf8eeb99, 0x34b0bcb5, 0xe19b48a8,
    0x391c0cb3, 0xc5c95a63, 0x4ed8aa4a, 0xe3418acb,
    0x5b9cca4f, 0x7763e373, 0x682e6ff3, 0xd6b2b8a3,
    0x748f82ee, 0x5defb2fc, 0x78a5636f, 0x43172f60,
    0x84c87814, 0xa1f0ab72, 0x8cc70208, 0x1a6439ec,
    0x90befffa, 0x23631e28, 0xa4506ceb, 0xde82bde9,
    0xbef9a3f7, 0xb2c67915, 0xc67178f2, 0xe372532b,
    0xca273ece, 0xea26619c, 0xd186b8c7, 0x21c0c207,
    0xeada7dd6, 0xcde0eb1e, 0xf57d4f7f, 0xee6ed178,
    0x06f067aa, 0x72176fba, 0x0a637dc5, 0xa2c898a6,
    0x113f9804, 0xbef90dae, 0x1b710b35, 0x131c471b,
    0x28db77f5, 0x23047d84, 0x32caab7b, 0x40c72493,
    0x3c9ebe0a, 0x15c9bebc, 0x431d67c4, 0x9c100d4c,
    0x4cc5d4be, 0xcb3e42b6, 0x597f299c, 0xfc657e2a,
    0x5fcb6fab, 0x3ad6faec, 0x6c44198c, 0x4a475817
  ];

  function sha512(msg) {
    var ml = msg.length;
    var rem = (ml + 1 + 16) % 128;
    var zeros = rem === 0 ? 0 : 128 - rem;
    var total = ml + 1 + zeros + 16;
    var data = u8(total);
    var i, j;
    data.set(msg, 0);
    data[ml] = 0x80;
    var bitHi = Math.floor(ml / 536870912);
    var bitLo = (ml % 536870912) * 8;
    bitLo = bitLo % 4294967296;
    var carry = Math.floor((ml % 536870912) * 8 / 4294967296);
    bitHi = (bitHi + carry) >>> 0;
    data[total - 8] = (bitHi >>> 24) & 255;
    data[total - 7] = (bitHi >>> 16) & 255;
    data[total - 6] = (bitHi >>> 8) & 255;
    data[total - 5] = bitHi & 255;
    data[total - 4] = (bitLo >>> 24) & 255;
    data[total - 3] = (bitLo >>> 16) & 255;
    data[total - 2] = (bitLo >>> 8) & 255;
    data[total - 1] = bitLo & 255;
    /* High 64 bits of the 128-bit length stay zero: messages here
       are far below 2^61 bytes. */
    var hHi = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    var hLo = [0xf3bcc908, 0x84caa73b, 0xfe94f82b, 0x5f1d36f1,
      0xade682d1, 0x2b3e6c1f, 0xfb41bd6b, 0x137e2179];
    var wHi = new Array(80), wLo = new Array(80);
    function rotr(hi, lo, n) {
      if (n === 32) {
        return [lo, hi];
      }
      if (n > 32) {
        n -= 32;
        var t = hi;
        hi = lo;
        lo = t;
      }
      return [((hi >>> n) | (lo << (32 - n))) >>> 0,
        ((lo >>> n) | (hi << (32 - n))) >>> 0];
    }
    function add64(aH, aL, bH, bL) {
      var lo = (aL + bL) >>> 0;
      return [(aH + bH + (lo < aL ? 1 : 0)) >>> 0, lo];
    }
    for (i = 0; i < total; i += 128) {
      for (j = 0; j < 16; j++) {
        var p = i + j * 8;
        wHi[j] = ((data[p] << 24) | (data[p + 1] << 16) |
          (data[p + 2] << 8) | data[p + 3]) >>> 0;
        wLo[j] = ((data[p + 4] << 24) | (data[p + 5] << 16) |
          (data[p + 6] << 8) | data[p + 7]) >>> 0;
      }
      for (j = 16; j < 80; j++) {
        var r1 = rotr(wHi[j - 2], wLo[j - 2], 19);
        var r2 = rotr(wHi[j - 2], wLo[j - 2], 61);
        var s0h = (r1[0] ^ r2[0] ^ (wHi[j - 2] >>> 6)) >>> 0;
        var s0l = (r1[1] ^ r2[1] ^
          (((wLo[j - 2] >>> 6) | (wHi[j - 2] << 26)) >>> 0)) >>> 0;
        var q1 = rotr(wHi[j - 15], wLo[j - 15], 1);
        var q8 = rotr(wHi[j - 15], wLo[j - 15], 8);
        var s1h = (q1[0] ^ q8[0] ^ (wHi[j - 15] >>> 7)) >>> 0;
        var s1l = (q1[1] ^ q8[1] ^
          (((wLo[j - 15] >>> 7) | (wHi[j - 15] << 25)) >>> 0)) >>> 0;
        var t = add64(wHi[j - 7], wLo[j - 7], s0h, s0l);
        t = add64(t[0], t[1], s1h, s1l);
        t = add64(t[0], t[1], wHi[j - 16], wLo[j - 16]);
        wHi[j] = t[0];
        wLo[j] = t[1];
      }
      var aHi = hHi[0], aLo = hLo[0];
      var bHi = hHi[1], bLo = hLo[1];
      var cHi = hHi[2], cLo = hLo[2];
      var dHi = hHi[3], dLo = hLo[3];
      var eHi = hHi[4], eLo = hLo[4];
      var fHi = hHi[5], fLo = hLo[5];
      var gHi = hHi[6], gLo = hLo[6];
      var hHi0 = hHi[7], hLo0 = hLo[7];
      for (j = 0; j < 80; j++) {
        var e14 = rotr(eHi, eLo, 14);
        var e18 = rotr(eHi, eLo, 18);
        var e41 = rotr(eHi, eLo, 41);
        var eS1h = (e14[0] ^ e18[0] ^ e41[0]) >>> 0;
        var eS1l = (e14[1] ^ e18[1] ^ e41[1]) >>> 0;
        var chH = ((eHi & fHi) ^ (~eHi & gHi)) >>> 0;
        var chL = ((eLo & fLo) ^ (~eLo & gLo)) >>> 0;
        var t1 = add64(hHi0, hLo0, eS1h, eS1l);
        t1 = add64(t1[0], t1[1], chH, chL);
        t1 = add64(t1[0], t1[1], SHA512_K[j * 2], SHA512_K[j * 2 + 1]);
        t1 = add64(t1[0], t1[1], wHi[j], wLo[j]);
        var a28 = rotr(aHi, aLo, 28);
        var a34 = rotr(aHi, aLo, 34);
        var a39 = rotr(aHi, aLo, 39);
        var aS0h = (a28[0] ^ a34[0] ^ a39[0]) >>> 0;
        var aS0l = (a28[1] ^ a34[1] ^ a39[1]) >>> 0;
        var mjH = ((aHi & bHi) ^ (aHi & cHi) ^ (bHi & cHi)) >>> 0;
        var mjL = ((aLo & bLo) ^ (aLo & cLo) ^ (bLo & cLo)) >>> 0;
        var t2 = add64(aS0h, aS0l, mjH, mjL);
        hHi0 = gHi;
        hLo0 = gLo;
        gHi = fHi;
        gLo = fLo;
        fHi = eHi;
        fLo = eLo;
        var te = add64(dHi, dLo, t1[0], t1[1]);
        eHi = te[0];
        eLo = te[1];
        dHi = cHi;
        dLo = cLo;
        cHi = bHi;
        cLo = bLo;
        bHi = aHi;
        bLo = aLo;
        var ta = add64(t1[0], t1[1], t2[0], t2[1]);
        aHi = ta[0];
        aLo = ta[1];
      }
      var hh;
      hh = add64(hHi[0], hLo[0], aHi, aLo);
      hHi[0] = hh[0]; hLo[0] = hh[1];
      hh = add64(hHi[1], hLo[1], bHi, bLo);
      hHi[1] = hh[0]; hLo[1] = hh[1];
      hh = add64(hHi[2], hLo[2], cHi, cLo);
      hHi[2] = hh[0]; hLo[2] = hh[1];
      hh = add64(hHi[3], hLo[3], dHi, dLo);
      hHi[3] = hh[0]; hLo[3] = hh[1];
      hh = add64(hHi[4], hLo[4], eHi, eLo);
      hHi[4] = hh[0]; hLo[4] = hh[1];
      hh = add64(hHi[5], hLo[5], fHi, fLo);
      hHi[5] = hh[0]; hLo[5] = hh[1];
      hh = add64(hHi[6], hLo[6], gHi, gLo);
      hHi[6] = hh[0]; hLo[6] = hh[1];
      hh = add64(hHi[7], hLo[7], hHi0, hLo0);
      hHi[7] = hh[0]; hLo[7] = hh[1];
    }
    var digest = u8(64);
    for (j = 0; j < 8; j++) {
      digest[j * 8] = (hHi[j] >>> 24) & 255;
      digest[j * 8 + 1] = (hHi[j] >>> 16) & 255;
      digest[j * 8 + 2] = (hHi[j] >>> 8) & 255;
      digest[j * 8 + 3] = hHi[j] & 255;
      digest[j * 8 + 4] = (hLo[j] >>> 24) & 255;
      digest[j * 8 + 5] = (hLo[j] >>> 16) & 255;
      digest[j * 8 + 6] = (hLo[j] >>> 8) & 255;
      digest[j * 8 + 7] = hLo[j] & 255;
    }
    return digest;
  }

  /* Canonical JSON: byte-identical to the Python agent contract
     (sort_keys, separators without whitespace, ensure_ascii, no
     floats, no null). Keys sort by Unicode code point. */
  function hex4(c) {
    var s = c.toString(16);
    while (s.length < 4) { s = "0" + s; }
    return s;
  }

  function quoteString(s) {
    var out = '"', i, c;
    for (i = 0; i < s.length; i++) {
      c = s.charCodeAt(i);
      if (c === 0x22) {
        out += '\\"';
      } else if (c === 0x5c) {
        out += "\\\\";
      } else if (c === 0x08) {
        out += "\\b";
      } else if (c === 0x09) {
        out += "\\t";
      } else if (c === 0x0a) {
        out += "\\n";
      } else if (c === 0x0c) {
        out += "\\f";
      } else if (c === 0x0d) {
        out += "\\r";
      } else if (c < 0x20 || c === 0x7f || c >= 0x80) {
        out += "\\u" + hex4(c);
      } else {
        out += s.charAt(i);
      }
    }
    return out + '"';
  }

  function toCodePoints(s) {
    var pts = [], i = 0, c, lo;
    while (i < s.length) {
      c = s.charCodeAt(i);
      if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
        lo = s.charCodeAt(i + 1);
        if (lo >= 0xdc00 && lo <= 0xdfff) {
          pts.push(0x10000 + ((c - 0xd800) << 10) + (lo - 0xdc00));
          i += 2;
          continue;
        }
      }
      pts.push(c);
      i++;
    }
    return pts;
  }

  function compareKeys(a, b) {
    var pa = toCodePoints(a), pb = toCodePoints(b);
    var n = pa.length < pb.length ? pa.length : pb.length;
    var i;
    for (i = 0; i < n; i++) {
      if (pa[i] !== pb[i]) {
        return pa[i] - pb[i];
      }
    }
    return pa.length - pb.length;
  }

  function canonicalValue(v) {
    var i, keys, parts;
    if (v === null) {
      throw new Error("command args allow strings, integers and " +
        "booleans only");
    }
    if (v === true) {
      return "true";
    }
    if (v === false) {
      return "false";
    }
    if (typeof v === "number") {
      if (!isFinite(v) || Math.floor(v) !== v ||
          v < -JS_SAFE_INT || v > JS_SAFE_INT) {
        throw new Error("command args allow strings, integers and " +
          "booleans only (no floats)");
      }
      return String(v);
    }
    if (typeof v === "string") {
      return quoteString(v);
    }
    if (Object.prototype.toString.call(v) === "[object Array]") {
      parts = [];
      for (i = 0; i < v.length; i++) {
        parts.push(canonicalValue(v[i]));
      }
      return "[" + parts.join(",") + "]";
    }
    if (typeof v === "object") {
      keys = [];
      for (i in v) {
        if (Object.prototype.hasOwnProperty.call(v, i)) {
          keys.push(i);
        }
      }
      keys.sort(compareKeys);
      parts = [];
      for (i = 0; i < keys.length; i++) {
        parts.push(quoteString(keys[i]) + ":" +
          canonicalValue(v[keys[i]]));
      }
      return "{" + parts.join(",") + "}";
    }
    throw new Error("message is not JSON-serializable");
  }

  function canonical(obj) {
    return utf8ToBytes(canonicalValue(obj));
  }

  /* Curve25519 field and Ed25519 signing (RFC 8032). BigInt keeps the
     code short; D is derived from its definition, and the signature
     output is checked against the lane-2 vectors. */
  var P255 = (1n << 255n) - 19n;
  var ED_L = (1n << 252n) + 27742317777372353535851937790883648493n;
  var ED_D = ((P255 - 121665n) * modPow255(121666n, P255 - 2n)) % P255;
  var ED_GX = 15112221349535400772501151409588531511454012693041857206046113283949847762202n;
  var ED_GY = 46316835694926478169428394003475163141307993866256225615783033603165251855960n;
  var ED_BASE = {x: ED_GX, y: ED_GY, z: 1n, t: (ED_GX * ED_GY) % P255};
  var ED_IDENT = {x: 0n, y: 1n, z: 1n, t: 0n};

  function modPow255(base, exp) {
    var result = 1n, b = base % P255;
    var e = exp;
    while (e > 0n) {
      if (e & 1n) {
        result = (result * b) % P255;
      }
      b = (b * b) % P255;
      e >>= 1n;
    }
    return result;
  }

  function leToBig(bytes) {
    var v = 0n, i;
    for (i = bytes.length - 1; i >= 0; i--) {
      v = (v << 8n) + BigInt(bytes[i]);
    }
    return v;
  }

  function bigToLe(v, n) {
    var out = u8(n), i, x = v;
    for (i = 0; i < n; i++) {
      out[i] = Number(x & 255n);
      x >>= 8n;
    }
    return out;
  }

  function modP(x) {
    var r = x % P255;
    return r < 0n ? r + P255 : r;
  }

  function edAdd(p, q) {
    var a = modP((p.y - p.x) * (q.y - q.x));
    var b = modP((p.y + p.x) * (q.y + q.x));
    var c = modP(p.t * 2n * ED_D * q.t);
    var d = modP(p.z * 2n * q.z);
    var e = modP(b - a);
    var f = modP(d - c);
    var g = modP(d + c);
    var h = modP(b + a);
    return {
      x: modP(e * f),
      y: modP(g * h),
      z: modP(f * g),
      t: modP(e * h)
    };
  }

  function edDouble(p) {
    return edAdd(p, p);
  }

  function edMult(point, scalar) {
    var acc = ED_IDENT, base = point, s = scalar, i;
    var bits = s.toString(2);
    for (i = bits.length - 1; i >= 0; i--) {
      if (bits[i] === "1") {
        acc = edAdd(acc, base);
      }
      base = edDouble(base);
    }
    return acc;
  }

  function edEncode(point) {
    var zi = modPow255(point.z, P255 - 2n);
    var x = (point.x * zi) % P255;
    var y = (point.y * zi) % P255;
    var out = bigToLe(y, 32);
    if ((x & 1n) === 1n) {
      out[31] |= 0x80;
    }
    return out;
  }

  function edSign(message, seed) {
    if (seed.length !== 32) {
      throw new Error("bad signing key");
    }
    var h = sha512(seed);
    var aBytes = h.slice(0, 32);
    aBytes[0] &= 248;
    aBytes[31] &= 63;
    aBytes[31] |= 64;
    var a = leToBig(aBytes) % ED_L;
    var r = leToBig(sha512(concat(h.slice(32, 64), message))) % ED_L;
    var bigR = edMult(ED_BASE, r);
    var bigA = edMult(ED_BASE, a);
    var encR = edEncode(bigR);
    var encA = edEncode(bigA);
    var k = leToBig(sha512(concat(encR, encA, message))) % ED_L;
    var s = (r + k * a) % ED_L;
    return concat(encR, bigToLe(s, 32));
  }

  /* X25519 (RFC 7748) Montgomery ladder. */
  function x25519(scalar, point) {
    var clamped = scalar.slice(0);
    clamped[0] &= 248;
    clamped[31] &= 127;
    clamped[31] |= 64;
    var n = leToBig(clamped);
    var x1 = leToBig(point) % (1n << 255n);
    var x2 = 1n, z2 = 0n, x3 = x1, z3 = 1n, swap = 0n, i, bit;
    for (i = 255; i >= 0; i--) {
      bit = (n >> BigInt(i)) & 1n;
      var s = swap ^ bit;
      if (s === 1n) {
        var t = x2; x2 = x3; x3 = t;
        t = z2; z2 = z3; z3 = t;
      }
      swap = bit;
      var a = (x2 + z2) % P255;
      var aa = (a * a) % P255;
      var b = (x2 - z2 + P255) % P255;
      var bb = (b * b) % P255;
      var e = (aa - bb + P255) % P255;
      var c = (x3 + z3) % P255;
      var d = (x3 - z3 + P255) % P255;
      var da = (d * a) % P255;
      var cb = (c * b) % P255;
      x3 = ((da + cb) % P255) ** 2n % P255;
      z3 = (x1 * (((da - cb + P255) % P255) ** 2n % P255)) % P255;
      x2 = (aa * bb) % P255;
      z2 = (e * ((aa + 121665n * e) % P255)) % P255;
    }
    if (swap === 1n) {
      var t2 = x2; x2 = x3; x3 = t2;
      t2 = z2; z2 = z3; z3 = t2;
    }
    var zi = modPow255(z2, P255 - 2n);
    return bigToLe((x2 * zi) % P255, 32);
  }

  function x25519Base(scalar) {
    var base = u8(32);
    base[0] = 9;
    return x25519(scalar, base);
  }

  /* Salsa20 core, HSalsa20, secretbox: the XSalsa20-Poly1305 half of
     crypto_box_seal_open. Verified against PyNaCl round-trips. */
  function rotl32(v, n) {
    return (((v << n) | (v >>> (32 - n))) >>> 0);
  }

  function salsa20Core(input, feedforward) {
    var x = [
      input[0], input[1], input[2], input[3],
      input[4], input[5], input[6], input[7],
      input[8], input[9], input[10], input[11],
      input[12], input[13], input[14], input[15]
    ];
    var i;
    for (i = 0; i < 10; i++) {
      x[4] ^= rotl32((x[0] + x[12]) >>> 0, 7);
      x[8] ^= rotl32((x[4] + x[0]) >>> 0, 9);
      x[12] ^= rotl32((x[8] + x[4]) >>> 0, 13);
      x[0] ^= rotl32((x[12] + x[8]) >>> 0, 18);
      x[9] ^= rotl32((x[5] + x[1]) >>> 0, 7);
      x[13] ^= rotl32((x[9] + x[5]) >>> 0, 9);
      x[1] ^= rotl32((x[13] + x[9]) >>> 0, 13);
      x[5] ^= rotl32((x[1] + x[13]) >>> 0, 18);
      x[14] ^= rotl32((x[10] + x[6]) >>> 0, 7);
      x[2] ^= rotl32((x[14] + x[10]) >>> 0, 9);
      x[6] ^= rotl32((x[2] + x[14]) >>> 0, 13);
      x[10] ^= rotl32((x[6] + x[2]) >>> 0, 18);
      x[3] ^= rotl32((x[15] + x[11]) >>> 0, 7);
      x[7] ^= rotl32((x[3] + x[15]) >>> 0, 9);
      x[11] ^= rotl32((x[7] + x[3]) >>> 0, 13);
      x[15] ^= rotl32((x[11] + x[7]) >>> 0, 18);
      x[1] ^= rotl32((x[0] + x[3]) >>> 0, 7);
      x[2] ^= rotl32((x[1] + x[0]) >>> 0, 9);
      x[3] ^= rotl32((x[2] + x[1]) >>> 0, 13);
      x[0] ^= rotl32((x[3] + x[2]) >>> 0, 18);
      x[6] ^= rotl32((x[5] + x[4]) >>> 0, 7);
      x[7] ^= rotl32((x[6] + x[5]) >>> 0, 9);
      x[4] ^= rotl32((x[7] + x[6]) >>> 0, 13);
      x[5] ^= rotl32((x[4] + x[7]) >>> 0, 18);
      x[11] ^= rotl32((x[10] + x[9]) >>> 0, 7);
      x[8] ^= rotl32((x[11] + x[10]) >>> 0, 9);
      x[9] ^= rotl32((x[8] + x[11]) >>> 0, 13);
      x[10] ^= rotl32((x[9] + x[8]) >>> 0, 18);
      x[12] ^= rotl32((x[15] + x[14]) >>> 0, 7);
      x[13] ^= rotl32((x[12] + x[15]) >>> 0, 9);
      x[14] ^= rotl32((x[13] + x[12]) >>> 0, 13);
      x[15] ^= rotl32((x[14] + x[13]) >>> 0, 18);
    }
    var out = new Array(16), k;
    for (k = 0; k < 16; k++) {
      out[k] = feedforward === false ? x[k] >>> 0
        : (x[k] + input[k]) >>> 0;
    }
    return out;
  }

  function leWords(bytes, off, count) {
    var words = [], i, j;
    for (i = 0; i < count; i++) {
      var v = 0;
      for (j = 0; j < 4; j++) {
        v |= bytes[off + i * 4 + j] << (j * 8);
      }
      words.push(v >>> 0);
    }
    return words;
  }

  function hsalsa20(shared, nonce16) {
    var k = leWords(shared, 0, 8);
    var n = leWords(nonce16, 0, 4);
    var state = [0x61707865, k[0], k[1], k[2], k[3],
      0x3320646e, n[0], n[1], n[2], n[3],
      0x79622d32, k[4], k[5], k[6], k[7], 0x6b206574];
    var out = salsa20Core(state, false);
    return wordsToBytes([out[0], out[5], out[10], out[15],
      out[6], out[7], out[8], out[9]]);
  }

  function wordsToBytes(words) {
    var out = u8(words.length * 4), i;
    for (i = 0; i < words.length; i++) {
      out[i * 4] = words[i] & 255;
      out[i * 4 + 1] = (words[i] >>> 8) & 255;
      out[i * 4 + 2] = (words[i] >>> 16) & 255;
      out[i * 4 + 3] = (words[i] >>> 24) & 255;
    }
    return out;
  }

  function salsa20Stream(key, nonce, length) {
    var k = leWords(key, 0, 8);
    var n = leWords(nonce, 0, 2);
    var out = u8(length);
    var counter = 0, pos = 0, i;
    while (pos < length) {
      var state = [0x61707865, k[0], k[1], k[2], k[3],
        0x3320646e, n[0], n[1], counter >>> 0,
        (counter / 4294967296) >>> 0,
        0x79622d32, k[4], k[5], k[6], k[7], 0x6b206574];
      var block = wordsToBytes(salsa20Core(state));
      var take = Math.min(64, length - pos);
      for (i = 0; i < take; i++) {
        out[pos + i] = block[i];
      }
      pos += take;
      counter++;
    }
    return out;
  }

  function secretboxOpen(box, nonce24, key) {
    if (box.length < 16) {
      throw new Error("sealed box too short");
    }
    var subkey = hsalsa20(key, nonce24.slice(0, 16));
    var stream = salsa20Stream(subkey, nonce24.slice(16, 24),
      32 + box.length);
    var polyKey = stream.slice(0, 32);
    var cipher = box.slice(16);
    var tag = poly1305(polyKey, cipher);
    var i, ok = true;
    for (i = 0; i < 16; i++) {
      if (tag[i] !== box[i]) {
        ok = false;
      }
    }
    if (!ok) {
      throw new Error("sealed box failed to open");
    }
    var plain = u8(cipher.length);
    for (i = 0; i < cipher.length; i++) {
      plain[i] = cipher[i] ^ stream[32 + i];
    }
    return plain;
  }

  /* Poly1305 one-time authenticator (RFC 8439). */
  var POLY_P = (1n << 130n) - 5n;

  function poly1305(key32, msg) {
    var r = leToBig(key32.slice(0, 16)) &
      0x0ffffffc0ffffffc0ffffffc0fffffffn;
    var s = leToBig(key32.slice(16, 32));
    var h = 0n, i, j;
    for (i = 0; i < msg.length; i += 16) {
      var chunk = u8(17);
      var take = Math.min(16, msg.length - i);
      for (j = 0; j < take; j++) {
        chunk[j] = msg[i + j];
      }
      chunk[take] = 1;
      h = ((h + leToBig(chunk.slice(0, 17))) * r) % POLY_P;
    }
    h = (h + s) % (1n << 128n);
    return bigToLe(h, 16);
  }

  /* BLAKE2b (RFC 7693) with BigInt state; keyless, outlen <= 64. */
  var BLAKE_IV = [
    0x6a09e667f3bcc908n, 0xbb67ae8584caa73bn,
    0x3c6ef372fe94f82bn, 0xa54ff53a5f1d36f1n,
    0x510e527fade682d1n, 0x9b05688c2b3e6c1fn,
    0x1f83d9abfb41bd6bn, 0x5be0cd19137e2179n
  ];
  var BLAKE_SIGMA = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
    [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
    [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
    [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
    [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
    [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
    [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
    [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
    [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0]
  ];
  var MASK64 = 0xffffffffffffffffn;

  function blake2b(outlen, data) {
    var param0 = 0x01010000n + BigInt(outlen);
    var h = [
      BLAKE_IV[0] ^ param0, BLAKE_IV[1], BLAKE_IV[2], BLAKE_IV[3],
      BLAKE_IV[4], BLAKE_IV[5], BLAKE_IV[6], BLAKE_IV[7]
    ];
    function rotr64(x, n) {
      n = BigInt(n);
      return ((x >> n) | (x << (64n - n))) & MASK64;
    }
    function g(v, a, b, c, d, x, y) {
      v[a] = (v[a] + v[b] + x) & MASK64;
      v[d] = rotr64(v[d] ^ v[a], 32);
      v[c] = (v[c] + v[d]) & MASK64;
      v[b] = rotr64(v[b] ^ v[c], 24);
      v[a] = (v[a] + v[b] + y) & MASK64;
      v[d] = rotr64(v[d] ^ v[a], 16);
      v[c] = (v[c] + v[d]) & MASK64;
      v[b] = rotr64(v[b] ^ v[c], 63);
    }
    function compress(block, t, last) {
      var v = h.slice(0);
      var i, j;
      for (i = 0; i < 8; i++) {
        v.push(BLAKE_IV[i]);
      }
      v[12] = (v[12] ^ BigInt(t >>> 0)) & MASK64;
      v[13] = (v[13] ^ BigInt(Math.floor(t / 4294967296))) & MASK64;
      if (last) {
        v[14] ^= MASK64;
      }
      var m = [];
      for (i = 0; i < 16; i++) {
        var w = 0n;
        for (j = 7; j >= 0; j--) {
          w = (w << 8n) + BigInt(block[i * 8 + j]);
        }
        m.push(w);
      }
      for (var round = 0; round < 12; round++) {
        var s = BLAKE_SIGMA[round % 10];
        g(v, 0, 4, 8, 12, m[s[0]], m[s[1]]);
        g(v, 1, 5, 9, 13, m[s[2]], m[s[3]]);
        g(v, 2, 6, 10, 14, m[s[4]], m[s[5]]);
        g(v, 3, 7, 11, 15, m[s[6]], m[s[7]]);
        g(v, 0, 5, 10, 15, m[s[8]], m[s[9]]);
        g(v, 1, 6, 11, 12, m[s[10]], m[s[11]]);
        g(v, 2, 7, 8, 13, m[s[12]], m[s[13]]);
        g(v, 3, 4, 9, 14, m[s[14]], m[s[15]]);
      }
      for (i = 0; i < 8; i++) {
        h[i] = (h[i] ^ v[i] ^ v[i + 8]) & MASK64;
      }
    }
    var i, off = 0;
    var total = data.length;
    if (total === 0) {
      compress(u8(128), 0, true);
    } else {
      while (off < total) {
        var remaining = total - off;
        var last = remaining <= 128;
        var block = u8(128);
        var take = last ? remaining : 128;
        for (i = 0; i < take; i++) {
          block[i] = data[off + i];
        }
        off += take;
        compress(block, off, last);
      }
    }
    var out = u8(outlen);
    for (i = 0; i < outlen; i++) {
      var word = h[Math.floor(i / 8)];
      out[i] = Number((word >> BigInt(8 * (i % 8))) & 255n);
    }
    return out;
  }

  /* crypto_box_seal_open: ephemeral key || box, nonce = BLAKE2b-24 of
     both public keys, then XSalsa20-Poly1305 open. */
  function boxSealOpen(sealed, recipientPk, recipientSk) {
    if (sealed.length < 48) {
      throw new Error("sealed box too short");
    }
    var epk = sealed.slice(0, 32);
    var box = sealed.slice(32);
    var sk = recipientSk.slice(0);
    sk[0] &= 248;
    sk[31] &= 127;
    sk[31] |= 64;
    var shared = x25519(sk, epk);
    var nonce = blake2b(24, concat(epk, recipientPk));
    var boxKey = hsalsa20(shared, u8(16));
    return secretboxOpen(box, nonce, boxKey);
  }

  /* Slippy-map tile mirror of server.latlon_to_tile, plus the marker
     fraction inside the tile for the positioned marker overlay. */
  var OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

  function latlonToTile(lat, lon, zoom) {
    zoom = Math.max(0, Math.min(19, Math.floor(zoom)));
    lat = Math.max(-85.05112878, Math.min(85.05112878, lat));
    var count = Math.pow(2, zoom);
    var x = Math.floor((lon + 180) / 360 * count);
    var latRad = lat * Math.PI / 180;
    var y = Math.floor((1 - Math.log(Math.tan(latRad) +
      1 / Math.cos(latRad)) / Math.PI) / 2 * count);
    x = Math.max(0, Math.min(count - 1, x));
    y = Math.max(0, Math.min(count - 1, y));
    return [x, y];
  }

  function tileMarkerFraction(lat, lon, zoom) {
    zoom = Math.max(0, Math.min(19, Math.floor(zoom)));
    lat = Math.max(-85.05112878, Math.min(85.05112878, lat));
    var count = Math.pow(2, zoom);
    var fx = (lon + 180) / 360 * count;
    var latRad = lat * Math.PI / 180;
    var fy = (1 - Math.log(Math.tan(latRad) +
      1 / Math.cos(latRad)) / Math.PI) / 2 * count;
    return [fx - Math.floor(fx), fy - Math.floor(fy)];
  }

  function tileUrl(z, x, y) {
    return OSM_TILE_URL.split("{z}").join(String(z))
      .split("{x}").join(String(x)).split("{y}").join(String(y));
  }

  return {
    u8: u8,
    concat: concat,
    hexToBytes: hexToBytes,
    bytesToHex: bytesToHex,
    base64ToBytes: base64ToBytes,
    bytesToBase64: bytesToBase64,
    utf8ToBytes: utf8ToBytes,
    bytesToUtf8: bytesToUtf8,
    sha512: sha512,
    blake2b: blake2b,
    canonical: canonical,
    canonicalValue: canonicalValue,
    edSign: edSign,
    x25519: x25519,
    x25519Base: x25519Base,
    boxSealOpen: boxSealOpen,
    secretboxOpen: secretboxOpen,
    salsa20Stream: salsa20Stream,
    hsalsa20: hsalsa20,
    poly1305: poly1305,
    latlonToTile: latlonToTile,
    tileMarkerFraction: tileMarkerFraction,
    tileUrl: tileUrl,
    OSM_TILE_URL: OSM_TILE_URL
  };
});
