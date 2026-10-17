"""Log ring is bounded and never emits secrets."""

from xx_audiobook import applog


def test_redact_scrubs_bearer_tokens():
    head = "Authorization: Bearer "
    assert applog.redact(head + "abc123") == "Authorization: Bearer [REDACTED]"
    assert "abc123" not in applog.redact("GET /x " + head + "abc.123_xyz")


def test_redact_scrubs_passwords_and_tokens():
    assert applog.redact("login password=hunter2 failed") == \
        "login password=[REDACTED] failed"
    assert applog.redact('{"password": "hunter2"}') == \
        '{"password": "[REDACTED]"}'
    assert applog.redact("access_token=tok_abc retry") == \
        "access_token=[REDACTED] retry"


def test_ring_bounded_and_capped():
    applog.reset()
    logger = applog.get_logger()
    for index in range(applog.RING_SIZE + 10):
        logger.info("line %d", index)
    try:
        assert len(applog.read_recent(limit=10_000)) == applog.RING_SIZE
        assert len(applog.read_recent(limit=5)) == 5
    finally:
        applog.reset()


def test_logger_never_emits_secrets():
    applog.reset()
    logger = applog.get_logger()
    try:
        logger.info("sync header Authorization: Bearer abc123")
        assert "abc123" not in "\n".join(applog.read_recent())
    finally:
        applog.reset()
