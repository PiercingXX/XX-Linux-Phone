from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from xx_captions import config, store


@pytest.fixture
def home(tmp_path: Path) -> Path:
    fake = tmp_path / "home"
    fake.mkdir()
    return fake


def test_call_opt_in_defaults_off(home: Path) -> None:
    assert config.load(home)["call_transcription_enabled"] is False


def test_roundtrip_begin_lines_end(home: Path) -> None:
    db = store.TranscriptStore(home)
    try:
        call_id = db.begin_call("555-0100", started=1700000000)
        db.add_line(call_id, "hello world", 500)
        db.add_line(call_id, "second line", 1500)
        db.end_call(call_id, ended=1700000060)
        rows = db.list_transcripts()
        assert rows == [(call_id, "555-0100", 1700000000, 1700000060)]
        peer, started, ended, lines = db.get_transcript(call_id)
        assert (peer, started, ended) == ("555-0100", 1700000000, 1700000060)
        assert lines == [("hello world", 500), ("second line", 1500)]
    finally:
        db.close()


def test_unknown_id_raises_not_found(home: Path) -> None:
    db = store.TranscriptStore(home)
    try:
        with pytest.raises(store.NotFoundError):
            db.get_transcript("no-such-call")
        with pytest.raises(store.NotFoundError):
            db.add_line("no-such-call", "words", 0)
        with pytest.raises(store.NotFoundError):
            db.end_call("no-such-call")
        assert db.delete_transcript("no-such-call") is False
    finally:
        db.close()


def test_empty_lines_rejected(home: Path) -> None:
    db = store.TranscriptStore(home)
    try:
        call_id = db.begin_call("peer")
        with pytest.raises(ValueError):
            db.add_line(call_id, "   ", 0)
    finally:
        db.close()


def test_list_newest_first_and_delete(home: Path) -> None:
    db = store.TranscriptStore(home)
    try:
        older = db.begin_call("a", started=1000)
        newer = db.begin_call("b", started=2000)
        ids = [row[0] for row in db.list_transcripts()]
        assert ids == [newer, older]
        assert db.delete_transcript(older) is True
        assert [row[0] for row in db.list_transcripts()] == [newer]
        with pytest.raises(store.NotFoundError):
            db.get_transcript(older)
    finally:
        db.close()


def test_database_file_is_0600_in_0700_dirs(home: Path) -> None:
    db = store.TranscriptStore(home)
    try:
        assert stat.S_IMODE(db.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(db.path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(config.data_dir(home).stat().st_mode) == 0o700
    finally:
        db.close()


def test_reopen_repairs_loosened_modes(home: Path) -> None:
    first = store.TranscriptStore(home)
    try:
        path = first.path
    finally:
        first.close()
    os.chmod(path, 0o644)
    second = store.TranscriptStore(home)
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        second.close()


def test_explicit_path_creates_0700_parent(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "calls.sqlite3"
    db = store.TranscriptStore(path=target)
    try:
        assert target.is_file()
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    finally:
        db.close()
