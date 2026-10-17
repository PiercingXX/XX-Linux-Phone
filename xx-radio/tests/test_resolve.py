"""Resolver: the 4.2 error table, the child worker, cache and jar."""

import io
import json
import os
import stat
import subprocess
import sys
import time

import pytest

from xx_music.engine import EngineError, ErrorKind
from xx_radio.youtube import resolver, resolve_worker

FAKE_YTDLP = '''
INFO = {}
OPTS = []

class DownloadError(Exception):
    pass

class YoutubeDL:
    def __init__(self, opts):
        OPTS.append(dict(opts))
        self.opts = opts
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def extract_info(self, url, download=False):
        vid = url.rsplit("v=", 1)[-1]
        item = INFO.get(vid)
        if isinstance(item, Exception):
            raise item
        if item is None:
            raise DownloadError("ERROR: Video %s is unavailable" % vid)
        return item
'''


@pytest.fixture()
def fake_yt_dlp(tmp_path, monkeypatch):
    """A fake yt_dlp module, controlled through yt_dlp.INFO per test."""
    (tmp_path / "yt_dlp.py").write_text(FAKE_YTDLP)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("yt_dlp", None)
    import yt_dlp

    yield yt_dlp
    sys.modules.pop("yt_dlp", None)


def _info(url="https://rr1---x.googlevideo.com/videoplayback?expire=9999999999",
          **overrides):
    info = dict(url=url, http_headers={"User-Agent": "yt"},
                ext="webm", filesize=5900000, loudness=-8.5)
    info.update(overrides)
    return info


# -- the error table ------------------------------------------------------

TABLE = [
    ("ERROR: Private video", {}, ErrorKind.NOT_FOUND),
    ("This video is unavailable", {}, ErrorKind.NOT_FOUND),
    ("Removed by the uploader", {}, ErrorKind.NOT_FOUND),
    ("members-only content", {}, ErrorKind.NOT_AVAILABLE),
    ("Join this channel to get access", {}, ErrorKind.NOT_AVAILABLE),
    ("Please confirm your age", {}, ErrorKind.AUTH_REQUIRED),
    ("age-restricted video", {}, ErrorKind.AUTH_REQUIRED),
    ("age-restricted video", {"signed_in": True}, ErrorKind.NOT_AVAILABLE),
    ("Sign in to confirm you're not a bot", {}, ErrorKind.RATE_LIMITED),
    ("HTTP Error 403: Forbidden", {}, ErrorKind.RATE_LIMITED),
    ("Requested format is not available", {"helper_ready": False},
     ErrorKind.HELPER_MISSING),
    ("Requested format is not available", {}, ErrorKind.SOURCE_CHANGED),
    ("Only images are available", {"helper_ready": False},
     ErrorKind.HELPER_MISSING),
    ("Unable to extract player response", {}, ErrorKind.SOURCE_CHANGED),
    ("nsig extraction failed", {}, ErrorKind.SOURCE_CHANGED),
    ("Read timed out", {}, ErrorKind.TIMEOUT),
    ("urlopen error [Errno -2] Name or service not known", {},
     ErrorKind.OFFLINE),
    ("Connection refused", {}, ErrorKind.OFFLINE),
    ("something completely new", {}, ErrorKind.OTHER),
]


@pytest.mark.parametrize("message,flags,expected", TABLE)
def test_error_table(message, flags, expected):
    assert resolve_worker.classify_failure(message, **flags) is expected


def test_error_table_is_case_insensitive():
    assert resolve_worker.classify_failure("PRIVATE VIDEO") is \
        ErrorKind.NOT_FOUND


# -- worker behaviour -------------------------------------------------------

def test_worker_resolves_and_reports_expiry(fake_yt_dlp):
    fake_yt_dlp.INFO["abc"] = _info()
    response = resolve_worker.resolve_payload(
        {"items": [{"id": "abc", "low_data": False}],
         "signed_in": False, "helper_ready": True})
    (result,) = response["results"]
    assert result["ok"]
    assert result["uri"].startswith("https://")
    assert result["headers"] == {"User-Agent": "yt"}
    assert result["mime"] == "audio/webm"
    assert result["expires_at"] == 9999999999
    assert result["size_bytes"] == 5900000
    assert result["gain_db"] == -8.5


def test_worker_uses_low_data_formats(fake_yt_dlp):
    fake_yt_dlp.INFO.update({"a": _info(), "b": _info()})
    resolve_worker.resolve_payload(
        {"items": [{"id": "a", "low_data": False},
                   {"id": "b", "low_data": True}],
         "signed_in": False, "helper_ready": True})
    formats = sorted(opts["format"] for opts in fake_yt_dlp.OPTS)
    assert formats == sorted([resolve_worker.FORMAT_FULL,
                              resolve_worker.FORMAT_LOW])
    assert "abr<=72" in resolve_worker.FORMAT_LOW


def test_worker_without_expiry_stamp_defaults_to_4h(fake_yt_dlp):
    fake_yt_dlp.INFO["abc"] = _info(url="https://example.com/audio")
    before = time.time()
    (result,) = resolve_worker.resolve_payload(
        {"items": [{"id": "abc", "low_data": False}],
         "signed_in": False, "helper_ready": True})["results"]
    assert before + 3.9 * 3600 < result["expires_at"] < time.time() + 4.1 * 3600


def test_worker_without_audio_reports_helper_state(fake_yt_dlp):
    fake_yt_dlp.INFO["abc"] = {"title": "no formats here"}
    for helper_ready, expected in ((False, "helper-missing"),
                                   (True, "source-changed")):
        response = resolve_worker.resolve_payload(
            {"items": [{"id": "abc", "low_data": False}],
             "signed_in": False, "helper_ready": helper_ready})
        (result,) = response["results"]
        assert not result["ok"] and result["kind"] == expected


def test_worker_passes_options_through(fake_yt_dlp):
    fake_yt_dlp.INFO["abc"] = _info()
    resolve_worker.resolve_payload(
        {"items": [{"id": "abc", "low_data": False}],
         "cookie_file": "/run/cookies.txt",
         "player_clients": ["tv", "web_music"],
         "plugin_dirs": ["/data/engine/plugins"],
         "js_runtimes": {"node": "/usr/bin/node"},
         "signed_in": True, "helper_ready": True})
    (opts,) = fake_yt_dlp.OPTS
    assert opts["cookiefile"] == "/run/cookies.txt"
    assert opts["plugin_dirs"] == ["/data/engine/plugins"]
    assert opts["js_runtimes"] == {"node": "/usr/bin/node"}
    assert opts["skip_download"] and opts["noplaylist"]
    assert opts["extractor_args"]["youtube"]["player_client"] == [
        "tv", "web_music"]


def test_worker_main_protocol(monkeypatch, capsys, fake_yt_dlp):
    fake_yt_dlp.INFO["abc"] = _info()
    monkeypatch.setattr(sys, "stdin",
                        io.StringIO(json.dumps({"items": [{"id": "abc"}]})))
    assert resolve_worker.main([]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["results"][0]["ok"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert resolve_worker.main([]) == 2


# -- resolver: cache, batching, cookies --------------------------------------

def _ok_result(track_id, expires_in=3600):
    return {"id": track_id, "ok": True,
            "uri": f"https://upstream.example/{track_id}",
            "headers": {}, "mime": "audio/webm",
            "expires_at": time.time() + expires_in,
            "size_bytes": 100, "gain_db": None}


def _failing_result(track_id, kind="not-found"):
    return {"id": track_id, "ok": False, "kind": kind,
            "error": "gone", "retry_after_s": None}


def test_expiry_cache_and_batching(tmp_path):
    seen = []

    def run(payload):
        seen.append(payload)
        return {"results": [_ok_result(item["id"])
                            for item in payload["items"]]}

    engine = resolver.Resolver(tmp_path, run=run)
    found = engine.resolve_many(["a", "b", "c"])
    assert set(found) == {"a", "b", "c"}
    assert len(seen) == 1  # one worker run for the batch
    assert found["a"].uri.endswith("/a")
    engine.resolve_many(["a", "b"])
    assert len(seen) == 1  # cache hit, no new run
    engine.invalidate("a")
    engine.resolve("a")
    assert len(seen) == 2


def test_expired_entries_reresolve(tmp_path):
    calls = []

    def run(payload):
        calls.append(1)
        stale = len(calls) == 1
        return {"results": [
            _ok_result(item["id"], expires_in=(-10 if stale else 3600))
            for item in payload["items"]]}

    engine = resolver.Resolver(tmp_path, run=run)
    engine.resolve("a")
    assert len(calls) == 1
    engine.resolve("a")  # the cached address already expired
    assert len(calls) == 2


def test_per_track_failure_raises_but_keeps_successes(tmp_path):
    def run(payload):
        return {"results": [_ok_result("a"), _failing_result("b")]}

    engine = resolver.Resolver(tmp_path, run=run)
    with pytest.raises(EngineError) as caught:
        engine.resolve_many(["a", "b"])
    assert caught.value.kind == ErrorKind.NOT_FOUND
    assert engine.cached("a") is not None
    assert engine.cached("b") is None


def test_low_data_flag_reaches_the_worker(tmp_path):
    seen = []

    def run(payload):
        seen.append(payload)
        return {"results": [_ok_result(item["id"])
                            for item in payload["items"]]}

    resolver.Resolver(tmp_path, run=run).resolve("a", low_data=True)
    assert seen[0]["items"] == [{"id": "a", "low_data": True}]


def test_cookie_jar_filters_login_and_locks_down(tmp_path):
    runtime = tmp_path / "run"
    engine = resolver.Resolver(tmp_path / "engine", runtime_dir=runtime,
                               signed_in=True)
    jar = engine.set_cookies(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t999\tLOGIN_INFO\ts3cret\n"
        ".youtube.com\tTRUE\t/\tTRUE\t999\tSID\tabc123\n")
    text = jar.read_text()
    assert "LOGIN_INFO" not in text and "SID" in text
    assert stat.S_IMODE(jar.stat().st_mode) == 0o600
    assert resolver.cookie_file(runtime) == str(jar)
    engine.clear_cookies()
    assert resolver.cookie_file(runtime) is None


def test_signed_in_workers_get_cookies_and_clients(tmp_path):
    seen = []

    def run(payload):
        seen.append(payload)
        return {"results": [_ok_result(item["id"])
                            for item in payload["items"]]}

    runtime = tmp_path / "run"
    engine = resolver.Resolver(tmp_path / "engine", runtime_dir=runtime,
                               signed_in=True, run=run)
    engine.set_cookies(".youtube.com\tTRUE\t/\tTRUE\t999\tSID\tabc\n")
    engine.resolve("a")
    assert seen[0]["cookie_file"].endswith("cookies.txt")
    assert seen[0]["player_clients"] == ["tv", "web_music"]
    assert seen[0]["signed_in"] is True


def test_anonymous_workers_get_defaults(tmp_path):
    seen = []

    def run(payload):
        seen.append(payload)
        return {"results": [_ok_result(item["id"])
                            for item in payload["items"]]}

    resolver.Resolver(tmp_path, run=run).resolve("a")
    assert seen[0]["cookie_file"] is None
    assert seen[0]["player_clients"] is None


def test_worker_python_prefers_the_promoted_venv(tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")
    os.chmod(venv_python, 0o755)
    assert resolver.worker_python(tmp_path) == str(venv_python)
    assert resolver.worker_python(tmp_path / "empty") == sys.executable


def test_transport_failures_map_to_engine_errors(tmp_path, monkeypatch):
    engine = resolver.Resolver(tmp_path, run=lambda payload: payload)

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(args, 45)

    monkeypatch.setattr(subprocess, "run", boom)
    engine._run = None
    with pytest.raises(EngineError) as caught:
        engine.resolve("a")
    assert caught.value.kind == ErrorKind.TIMEOUT

    def dead(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "worker blew up")

    monkeypatch.setattr(subprocess, "run", dead)
    with pytest.raises(EngineError) as caught:
        engine.resolve("a")
    assert caught.value.kind == ErrorKind.OTHER


def test_real_child_process_with_fake_yt_dlp(tmp_path, monkeypatch):
    """The JSON protocol end to end through a real child process."""
    import pathlib

    import xx_radio

    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (bindir / "yt_dlp.py").write_text(FAKE_YTDLP)
    src_root = str(pathlib.Path(xx_radio.__file__).parent.parent)
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([str(bindir), src_root]))
    info = _info()
    payload = {"items": [{"id": "song", "low_data": True}],
               "signed_in": False, "helper_ready": True}
    setup = ("import yt_dlp; yt_dlp.INFO['song'] = %r" % (info,))
    wrapper = tmp_path / "worker_shim.py"
    wrapper.write_text(
        "import runpy, sys\n"
        f"{setup}\n"
        "sys.argv = ['resolve_worker']\n"
        "runpy.run_module('xx_radio.youtube.resolve_worker',\n"
        "                 run_name='__main__')\n")
    done = subprocess.run(
        [sys.executable, str(wrapper)], input=json.dumps(payload),
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    (result,) = json.loads(done.stdout)["results"]
    assert result["ok"] and result["uri"].startswith("https://")


def test_worker_env_puts_the_app_package_first():
    # the engine venv python must still find xx_radio (seen on the FLX1)
    import os
    from pathlib import Path
    from xx_radio.youtube import resolver
    env = resolver._worker_env()
    first = env["PYTHONPATH"].split(os.pathsep)[0]
    assert (Path(first) / "xx_radio" / "youtube" / "resolve_worker.py").is_file()
