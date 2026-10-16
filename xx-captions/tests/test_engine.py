from __future__ import annotations

import pytest

from xx_captions import engine
from xx_captions.dbus_api import ContractError


def _collect() -> tuple[list[engine.Segment], object]:
    seen: list[engine.Segment] = []
    return seen, seen.append


def test_fake_emits_script_in_order() -> None:
    seen, append = _collect()
    fake = engine.FakeEngine()
    fake.start("playback", append)
    assert fake.wait(timeout=5.0)
    assert [segment.text for segment in seen] == ["hello", "hello world"]
    assert seen[0].final is False
    assert seen[1].final is True


def test_fake_rejects_unknown_source() -> None:
    fake = engine.FakeEngine()
    with pytest.raises(ContractError):
        fake.start("bluetooth", lambda segment: None)


def test_fake_rejects_double_start() -> None:
    fake = engine.FakeEngine(script=[engine.Segment("x", True)])
    received: list[engine.Segment] = []
    fake.start("mic", received.append)
    with pytest.raises(engine.EngineError):
        fake.start("mic", received.append)
    fake.stop()


def test_stop_is_idempotent() -> None:
    fake = engine.FakeEngine()
    fake.stop()
    fake.stop()
    assert fake.running is False
    assert fake.source is None


def test_stop_then_restart() -> None:
    seen: list[engine.Segment] = []
    fake = engine.FakeEngine(script=[engine.Segment("again", True)])
    fake.start("call", seen.append)
    assert fake.wait(timeout=5.0)
    fake.stop()
    fake.start("call", seen.append)
    assert fake.wait(timeout=5.0)
    fake.stop()
    assert [segment.text for segment in seen] == ["again", "again"]


def test_all_sources_accepted() -> None:
    for source in engine.SOURCES:
        fake = engine.FakeEngine(script=[])
        received: list[engine.Segment] = []
        fake.start(source, received.append)
        assert fake.source == source
        fake.stop()
