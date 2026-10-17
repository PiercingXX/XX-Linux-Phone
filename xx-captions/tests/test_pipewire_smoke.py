"""Real-PipeWire smoke: runs only with a live PipeWire in reach.

Skips cleanly in containers and the lane jail (no daemon there), and
reports the skip so the report can state PipeWire was unreachable.
"""
from __future__ import annotations

import shutil
import subprocess
import threading

import pytest

from xx_captions import capture


def _daemon_reachable() -> str | None:
    """Default sink name when PipeWire answers, else None."""
    if shutil.which("pw-record") is None or shutil.which("pactl") is None:
        return None
    try:
        info = subprocess.run(["pactl", "info"], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if info.returncode != 0 or "Connection failure" in info.stderr:
        return None
    try:
        sink = subprocess.run(["pactl", "get-default-sink"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    name = sink.stdout.strip()
    return name or None


def test_live_monitor_streams_bytes() -> None:
    sink = _daemon_reachable()
    if sink is None:
        pytest.skip("no running PipeWire within reach "
                    "(pw-record/pactl or daemon missing)")
    got: list[capture.Chunk] = []
    ended = threading.Event()
    cap = capture.PipeCapture(capture.pw_record_argv_for(sink),
                              "playback", got.append,
                              lambda: ended.set())
    cap.start()
    try:
        assert ended.wait(timeout=15.0) or cap.running
        assert got, "live monitor produced no audio in 15 s"
    finally:
        cap.stop()
