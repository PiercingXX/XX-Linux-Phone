"""Lane 6: owner UI, rate limiting, decrypt helper, JS vector check.

The server stores ciphertext only; the committed JS-crypto check replays
the lane-2 canonical vectors plus a fresh sealed box through
`src/xx_find/owner.js` under the first engine on PATH
(`node`/`deno`/`qjs`) and skips cleanly when none is installed.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess

import pytest

from xx_find import crypto, protocol, server
from xx_find.server import RateLimiter

NOW = 1_800_000_000


@pytest.fixture
def store():
    owned = server.Store()
    owned.set_owner_password("correct horse")
    return owned


def _login(store, password="correct horse", ip="10.0.0.1"):
    return server.handle_request("POST", "/login", {}, json.dumps(
        {"password": password}), store, NOW, ip)


def _session(store, ip="10.0.0.1"):
    status, _, text = _login(store, ip=ip)
    assert status == 200
    return json.loads(text)["session"]


def _signed_envelope(device, counter):
    _, signing_hex = crypto.generate_owner_signing_keypair()
    cmd = protocol.build_command(device, counter, NOW + 120, "locate")
    return protocol.sign_envelope(cmd, signing_hex)


def test_rate_limiter_sliding_window():
    gate = RateLimiter(2, 60)
    assert gate.allowed("k", 100) is True
    assert gate.allowed("k", 101) is True
    assert gate.allowed("k", 102) is False
    assert gate.allowed("other", 102) is True
    assert gate.allowed("k", 161) is True
    gate.clear("k")
    assert gate.allowed("k", 162) is True


def test_login_limited_per_ip_and_account(store):
    store.login_limiter = RateLimiter(2, 900)
    bad = json.dumps({"password": "nope"})
    assert server.handle_request(
        "POST", "/login", {}, bad, store, NOW, "10.0.0.1")[0] == 401
    assert server.handle_request(
        "POST", "/login", {}, bad, store, NOW, "10.0.0.1")[0] == 401
    assert server.handle_request(
        "POST", "/login", {}, bad, store, NOW, "10.0.0.1")[0] == 429
    assert server.handle_request(
        "POST", "/login", {}, bad, store, NOW, "10.0.0.2")[0] == 429


def test_login_success_clears_counters(store):
    store.login_limiter = RateLimiter(2, 900)
    bad = json.dumps({"password": "nope"})
    server.handle_request("POST", "/login", {}, bad, store, NOW, "10.0.0.3")
    assert _login(store, ip="10.0.0.3")[0] == 200
    assert server.handle_request(
        "POST", "/login", {}, bad, store, NOW, "10.0.0.3")[0] == 401


def test_command_limited_per_ip_and_device(store):
    store.command_limiter = RateLimiter(2, 60)
    token = _session(store)
    headers = {"x-session": token}
    store.pair_device("phone-a")
    store.pair_device("phone-b")

    def post(device, counter, ip="10.0.0.9"):
        envelope = _signed_envelope(device, counter)
        return server.handle_request(
            "POST", "/v1/command", headers, json.dumps(
                {"device_id": device, "envelope": envelope}),
            store, NOW, ip)[0]

    assert post("phone-a", 1) == 200
    assert post("phone-a", 2) == 200
    assert post("phone-a", 3) == 429
    assert post("phone-a", 4, ip="10.0.0.10") == 429
    assert post("phone-b", 1, ip="10.0.0.10") == 200


def test_api_devices_and_ciphertext_report(store):
    session = _session(store)
    headers = {"x-session": session}
    token = store.pair_device("phone-1")
    pub, _ = crypto.generate_owner_box_keypair()
    sealed = crypto.seal_report(b'{"lat": 47.0}', pub)
    report = protocol.build_report_envelope("phone-1", 1, NOW, sealed)
    status, _, _ = server.handle_request(
        "POST", "/v1/report", {"x-device-token": token},
        json.dumps({"device_id": "phone-1", "report": report}),
        store, NOW)
    assert status == 200
    status, _, text = server.handle_request(
        "GET", "/api/devices", headers, "", store, NOW)
    assert status == 200
    (entry,) = json.loads(text)["devices"]
    assert entry["has_report"] is True
    assert entry["last_report_epoch"] == NOW
    status, _, text = server.handle_request(
        "GET", "/api/report?device=phone-1", headers, "", store, NOW)
    assert status == 200
    assert json.loads(text)["report"]["box"] == report["box"]
    for banned in ("lat", "lon", "latitude", "longitude", "plaintext"):
        assert '"%s"' % banned not in text
    status, _, _ = server.handle_request(
        "GET", "/api/report?device=phone-1", {}, "", store, NOW)
    assert status == 401


def test_ui_shell_embeds_crypto_without_plaintext_or_trackers(store):
    status, _, login = server.handle_request("GET", "/", {}, "", store,
                                             NOW)
    assert status == 200 and "owner login" in login
    session = _session(store)
    status, _, app = server.handle_request(
        "GET", "/", {"x-session": session}, "", store, NOW)
    assert status == 200 and "OpenStreetMap" in app
    assert "boxSealOpen" in app and "edSign" in app
    assert "tile.openstreetmap.org" in app
    assert "google" not in app.lower()
    assert server._owner_js().strip().startswith("/* xx-find owner")


def test_browser_signed_command_queues_through_ui_api(store):
    session = _session(store)
    store.pair_device("phone-1")
    verify_hex, signing_hex = crypto.generate_owner_signing_keypair()
    del verify_hex
    cmd = protocol.build_command("phone-1", 1, NOW + 120, "locate")
    envelope = protocol.sign_envelope(cmd, signing_hex)
    status, _, text = server.handle_request(
        "POST", "/v1/command", {"x-session": session},
        json.dumps({"device_id": "phone-1", "envelope": envelope}),
        store, NOW)
    assert status == 200
    _, _, text = server.handle_request(
        "GET", "/api/devices", {"x-session": session}, "", store, NOW)
    assert json.loads(text)["devices"][0]["pending"] == 1


def test_decrypt_cli_round_trip(tmp_path, capsys):
    from xx_find.decrypt import main, open_report
    pub, secret = crypto.generate_owner_box_keypair()
    plain = protocol.encode_location_plaintext(
        47.37, 8.54, 12.5, NOW, 42, "geoclue")
    sealed = crypto.seal_report(plain, pub)
    assert open_report(sealed, bytes.fromhex(secret)) == json.loads(plain)
    encoded = base64.b64encode(sealed).decode("ascii")
    assert main(["--secret-key", secret, "--box", encoded]) == 0
    assert json.loads(capsys.readouterr().out)["lat"] == 47.37
    report_file = tmp_path / "report.json"
    report_file.write_text(json.dumps(
        protocol.build_report_envelope("phone-1", 1, NOW, sealed)))
    assert main(["--secret-key", secret, "--report-file",
                 str(report_file)]) == 0
    assert main(["--secret-key", "00" * 32, "--box", encoded]) == 1
    assert main(["--secret-key", secret, "--box", "!!!"]) == 1


def test_decrypt_cli_reads_store_file(tmp_path, capsys):
    from xx_find.decrypt import main
    owned = server.Store(tmp_path / "store.json")
    owned.pair_device("phone-1")
    pub, secret = crypto.generate_owner_box_keypair()
    sealed = crypto.seal_report(
        protocol.encode_location_plaintext(1.0, 2.0, 3.0, NOW, None,
                                           "manual"), pub)
    owned.submit_report(
        "phone-1",
        protocol.build_report_envelope("phone-1", 1, NOW, sealed), NOW)
    assert main(["--secret-key", secret, "--store",
                 str(tmp_path / "store.json"),
                 "--device", "phone-1"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "manual"
    assert main(["--secret-key", secret, "--store",
                 str(tmp_path / "store.json"),
                 "--device", "ghost"]) == 1


JS_DRIVER = r"""
var cases = JSON.parse(XXFIND.bytesToUtf8(XXFIND.base64ToBytes(FIXTURES_B64)));
var seed = XXFIND.hexToBytes(RFC_SEED);
cases.vectors.forEach(function (item, i) {
  var cmd = JSON.parse(item.json);
  var bytes = XXFIND.bytesToUtf8(XXFIND.canonical(cmd));
  if (bytes !== item.bytes) {
    throw new Error("canonical mismatch at " + i);
  }
  var sig = XXFIND.bytesToHex(XXFIND.edSign(XXFIND.canonical(cmd), seed));
  if (sig !== item.sig) {
    throw new Error("signature mismatch at " + i);
  }
});
var opened = XXFIND.bytesToUtf8(XXFIND.boxSealOpen(
  XXFIND.hexToBytes(cases.seal.box),
  XXFIND.hexToBytes(cases.seal.pub),
  XXFIND.hexToBytes(cases.seal.sec)));
if (opened !== cases.seal.plain) {
  throw new Error("seal open mismatch");
}
try {
  XXFIND.canonical({ v: 1.5 });
  throw new Error("float accepted");
} catch (e) {
  if (e.message === "float accepted") { throw e; }
}
console.log("VECTORS OK");
"""


def _engines():
    return [engine for engine in ("node", "deno", "qjs") if shutil.which(
        engine) is not None]


def test_js_reproduces_canonical_and_crypto(tmp_path):
    import importlib.util
    from pathlib import Path as _Path
    vectors_path = (_Path(__file__).resolve().parent
                    / "test_protocol.py")
    spec = importlib.util.spec_from_file_location(
        "xx_find_lane6_vectors", vectors_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    CANONICAL_VECTORS, RFC_SEED = (module.CANONICAL_VECTORS,
                                   module.RFC_SEED)
    engines = _engines()
    if not engines:
        pytest.skip("no JS engine (node/deno/qjs) on PATH")
    pub, secret = crypto.generate_owner_box_keypair()
    plain = protocol.encode_location_plaintext(
        47.3769, 8.5417, 12.5, NOW, 42, "geoclue")
    sealed = crypto.seal_report(plain, pub)
    fixtures = {
        "vectors": [{"json": item[0].decode("ascii"),
                     "bytes": item[0].decode("ascii"),
                     "sig": item[1]}
                    for item in CANONICAL_VECTORS],
        "seal": {"box": sealed.hex(), "pub": pub, "sec": secret,
                 "plain": plain.decode("ascii")},
    }
    from pathlib import Path as _Path
    src = (_Path(__file__).resolve().parent.parent
           / "src" / "xx_find" / "owner.js").read_text(encoding="utf-8")
    assert "google" not in src.lower()
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket"):
        assert banned not in src
    driver = src + "\nvar RFC_SEED = %s;\nvar FIXTURES_B64 = %s;\n%s" % (
        json.dumps(RFC_SEED),
        json.dumps(base64.b64encode(
            json.dumps(fixtures).encode("ascii")).decode("ascii")),
        JS_DRIVER)
    driver_path = tmp_path / "vectors.js"
    driver_path.write_text(driver, encoding="utf-8")
    engine = engines[0]
    cmd = [engine, str(driver_path)] if engine != "deno" else [
        engine, "run", str(driver_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "VECTORS OK" in proc.stdout
