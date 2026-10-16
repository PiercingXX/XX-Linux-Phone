"""On-phone log ring: file under the state dir plus in-memory lines."""

from xx_vitals.core import applog


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    applog.reset()
    return home


def test_log_writes_state_file_and_ring(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    logger = applog.get_logger()
    logger.info("hello one")
    logger.warning("hello two")
    lines = applog.read_recent()
    assert lines[-2:] == ["INFO hello one", "WARNING hello two"]
    stored = (home / ".local" / "state" / "xx-vitals" / "xx-vitals.log").read_text(
        encoding="utf-8")
    assert "hello one" in stored and "hello two" in stored
    applog.reset()


def test_ring_is_bounded_and_read_capped(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    logger = applog.get_logger()
    for index in range(applog.RING_SIZE + 10):
        logger.info("line %d", index)
    assert len(applog.read_recent(limit=10_000)) == applog.RING_SIZE
    assert len(applog.read_recent(limit=5)) == 5
    applog.reset()


def test_redact_scrubs_bearer_tokens():
    from xx_vitals.core import applog

    head = "Authorization: " + "Bearer "
    assert applog.redact(head + "abc123") == "Authorization: [REDACTED]"
    assert applog.redact("GET /v1/changes " + head + "abc.123_xyz") == (
        "GET /v1/changes Authorization: [REDACTED]")
    assert "abc123" not in applog.redact("token abc " + head + "abc123 xyz")


def test_redact_scrubs_passwords_and_tokens():
    from xx_vitals.core import applog

    assert applog.redact("login password=hunter2 failed") == "login password=[REDACTED] failed"
    assert applog.redact('{"password": "hunter2"}') == '{"password": "[REDACTED]"}'
    assert applog.redact("access_token=tok_abc retry") == "access_token=[REDACTED] retry"
    assert applog.redact("session_token: tok_xyz!") == "session_token: [REDACTED]"
    assert applog.redact("fabric login ok for user sam") == "fabric login ok for user sam"


def test_logger_never_emits_secrets(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    logger = applog.get_logger()
    plain = "sync header Authorization: " + "Bearer " + "abc123"
    logger.info(plain)
    logger.info("login with password=hunter2 as sam")
    logger.info("fabric token access_token=f00bar-baz")
    lines = applog.read_recent()
    blob = "\n".join(lines)
    assert "hunter2" not in blob and "f00bar-baz" not in blob
    assert "[REDACTED]" in blob
    stored = (home / ".local" / "state" / "xx-vitals" / "xx-vitals.log").read_text(
        encoding="utf-8")
    assert "hunter2" not in stored and "f00bar-baz" not in stored
    applog.reset()


def test_log_view_lines_include_collector_lines(tmp_path, monkeypatch):
    from xx_vitals import paths

    home = _home(tmp_path, monkeypatch)
    logger = applog.get_logger()
    logger.info("app line one")
    paths.log_file().write_text(
        paths.log_file().read_text(encoding="utf-8")
        + "2026-10-16 INFO one-shot ok steps=120 source=hardware date=2026-10-16\n",
        encoding="utf-8",
    )
    lines = applog.log_view_lines()
    assert any("app line one" in line for line in lines)
    assert any("one-shot ok steps=120" in line for line in lines)
    assert lines == applog.log_view_lines(limit=10_000)[-200:]
    applog.reset()
    assert home is not None


def test_log_view_falls_back_to_ring_without_file(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    logger = applog.get_logger()
    logger.info("ring only")
    from xx_vitals import paths

    paths.log_file().unlink()
    for handler in list(logger.handlers):
        try:
            handler.close()
        except OSError:
            pass
    assert applog.log_view_lines() == ["INFO ring only"]
    applog.reset()


def test_copy_source_helpers_are_headless():
    from xx_vitals.ui import profile

    assert callable(profile.import_takeout)
    assert "Copy logs" in _profile_source() and "get_clipboard" in _profile_source()
    assert "FileDialog" in _profile_source()


def _profile_source() -> str:
    from pathlib import Path

    return (Path(__file__).parent.parent / "src" / "xx_vitals"
            / "ui" / "profile.py").read_text(encoding="utf-8")
