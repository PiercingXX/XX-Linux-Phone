"""Log file rotation, crash persistence and the redacted share dump."""

from xx_audiobook import applog


def test_live_token_never_reaches_ring_or_file():
    applog.reset()
    try:
        logger = applog.get_logger()
        logger.info("sync header Authorization: Bearer live-secret-9")
        logger.info("login password=hunter2 failed")
        logger.info("contact me@example.com now")
        logger.info("pair otpauth://totp/x?secret=ABC now")
        text = "\n".join(applog.read_recent())
        assert "live-secret-9" not in text
        assert "hunter2" not in text
        assert "me@example.com" not in text
        assert "otpauth://totp" not in text
        from xx_audiobook import paths as paths_module
        logged = paths_module.log_file().read_text(encoding="utf-8")
        assert "live-secret-9" not in logged
        assert "hunter2" not in logged
    finally:
        applog.reset()


def test_rotation_keeps_a_capped_sibling():
    applog.reset()
    try:
        logger = applog.get_logger()
        from xx_audiobook import paths as paths_module
        path = paths_module.log_file()
        path.write_text("x" * (applog.FILE_CAP_BYTES + 10),
                        encoding="utf-8")
        logger.info("after rotation")
        assert (path.parent / applog.PREV_NAME).is_file()
        assert path.stat().st_size < applog.FILE_CAP_BYTES
    finally:
        applog.reset()


def test_crash_persists_redacted_with_recent_ring():
    applog.reset()
    try:
        logger = applog.get_logger()
        logger.info("before the fall")
        try:
            raise RuntimeError("boom with Bearer live-secret-9")
        except RuntimeError as exc:
            applog.persist_crash(exc)
        crashed = applog.last_crash()
        assert crashed is not None
        assert "live-secret-9" not in crashed
        assert "before the fall" in crashed
        assert "RuntimeError" in crashed
    finally:
        applog.reset()


def test_share_text_is_capped_and_redacted(monkeypatch):
    applog.reset()
    try:
        logger = applog.get_logger()
        for index in range(400):
            logger.info("padding line %d with some context words", index)
        shared = applog.share_text()
        assert len(shared.encode("utf-8")) <= applog.SHARE_CAP_BYTES
        assert "--- live ---" in shared
        monkeypatch.setattr(applog, "SHARE_CAP_BYTES", 100)
        assert len(applog.share_text().encode("utf-8")) <= 100
    finally:
        applog.reset()


def test_no_crash_means_no_crash_section():
    applog.reset()
    try:
        applog.get_logger().info("hello")
        assert applog.last_crash() is None
        assert "last crash" not in applog.share_text()
    finally:
        applog.reset()
