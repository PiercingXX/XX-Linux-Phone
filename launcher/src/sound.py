"""
Sound playback for shell events (ringtone, notifications).

Plays via `paplay` (PipeWire's pactl frontend, present on every target
distro), falling back to `pw-play`. Loop mode respawns the player from a
daemon thread until stopped. Every failure is a silent no-op — sound must
never take the shell down.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

_SOUND_DIR_CANDIDATES = [
    Path('/usr/share/xx-wm/sounds'),
    Path('/usr/local/share/xx-wm/sounds'),
    Path(__file__).resolve().parent.parent / 'data' / 'sounds',
]


def sound_dir() -> Path | None:
    for candidate in _SOUND_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return None


def _player() -> str | None:
    for cmd in ('paplay', 'pw-play'):
        if shutil.which(cmd):
            return cmd
    return None


class SoundPlayer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._looping = False

    def play(self, name: str, loop: bool = False) -> None:
        directory = sound_dir()
        player = _player()
        if directory is None or player is None:
            return
        path = directory / name
        if not path.is_file():
            return
        self.stop()
        with self._lock:
            self._looping = loop
        if loop:
            threading.Thread(target=self._loop_worker, args=(player, path), daemon=True).start()
        else:
            with self._lock:
                self._proc = self._spawn(player, path)

    def _loop_worker(self, player: str, path: Path) -> None:
        while True:
            with self._lock:
                if not self._looping:
                    return
                self._proc = self._spawn(player, path)
                proc = self._proc
            if proc is None:
                return
            proc.wait()
            with self._lock:
                if not self._looping:
                    return

    @staticmethod
    def _spawn(player: str, path: Path) -> subprocess.Popen | None:
        try:
            return subprocess.Popen(
                [player, str(path)], close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None

    def stop(self) -> None:
        with self._lock:
            self._looping = False
            proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass


_default_player = SoundPlayer()


def play(name: str, loop: bool = False) -> None:
    _default_player.play(name, loop=loop)


def stop() -> None:
    _default_player.stop()
