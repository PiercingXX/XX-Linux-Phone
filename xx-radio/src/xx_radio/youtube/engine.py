"""The YouTube Music engine (unofficial; see README.md).

Scaffold stage: status() is real (probe.py); the catalog and playback
methods arrive in the milestones of PORT_SPEC.md and until then keep the
base class answer, ``EngineError(UNSUPPORTED)``. ytmusicapi and yt-dlp are
imported lazily, inside methods, so building the engine never blocks.
"""

from __future__ import annotations

import datetime as _dt

from xx_music.engine import Engine, EngineStatus
from xx_radio.youtube import probe


class YouTubeMusicEngine(Engine):
    key = "ytm"
    name = "YouTube Music"
    capabilities = frozenset()  # grows with each milestone

    def __init__(self, engine_dir) -> None:
        self.engine_dir = engine_dir

    def status(self) -> EngineStatus:
        return probe.status_from(probe.probe(self.engine_dir),
                                 _dt.date.today())


def create(context) -> YouTubeMusicEngine:
    """Engine factory named by xx_radio.main.SPEC."""
    return YouTubeMusicEngine(context.paths.engine_dir())
