"""Last-seen stills via grim.

In-process wlr-screencopy on a second Display blocked in read() and never
delivered frames on this phoc/pywayland pair. grim already speaks the
compositor's capture protocol; we run it off-thread and parse a tiny PPM.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading

log = logging.getLogger(__name__)


class OutputCapture:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False

    def available(self) -> bool:
        return shutil.which('grim') is not None

    def capture(self, callback) -> bool:
        if not self.available():
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(
            target=self._run, args=(callback,), daemon=True,
            name='xx-wm-grim',
        ).start()
        return True

    def shutdown(self) -> None:
        pass

    def _run(self, callback) -> None:
        path = None
        try:
            fd, path = tempfile.mkstemp(prefix='xx-wm-thumb-', suffix='.ppm')
            os.close(fd)
            result = subprocess.run(
                ['grim', '-s', '0.25', '-t', 'ppm', path],
                timeout=5,
                capture_output=True,
            )
            if result.returncode != 0:
                err = (result.stderr or b'').decode('utf-8', 'replace')[-200:]
                log.info('grim capture failed: %s', err)
                return
            thumb = _ppm_to_thumb(path)
            if thumb is None:
                return
            _deliver(callback, thumb)
        except Exception as exc:
            log.info('grim capture error: %s', exc)
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            with self._lock:
                self._busy = False


def _ppm_to_thumb(path: str):
    from thumbnail_store import downsample_shm
    with open(path, 'rb') as fh:
        magic = fh.readline().strip()
        if magic != b'P6':
            log.info('grim ppm magic %r', magic)
            return None
        line = fh.readline()
        while line.startswith(b'#'):
            line = fh.readline()
        parts = line.split()
        if len(parts) < 2:
            dim = line + fh.readline()
            parts = dim.split()
        width, height = int(parts[0]), int(parts[1])
        maxval = int(fh.readline().strip())
        if maxval != 255 or width <= 0 or height <= 0:
            return None
        rgb = fh.read(width * height * 3)
    if len(rgb) < width * height * 3:
        return None
    # Pack RGB as fake XRGB (B,G,R,0) so downsample_shm can convert.
    packed = bytearray(width * height * 4)
    for i in range(width * height):
        r, g, b = rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]
        o = i * 4
        packed[o] = b
        packed[o + 1] = g
        packed[o + 2] = r
        packed[o + 3] = 255
    return downsample_shm(
        bytes(packed), width, height, width * 4, shm_format=1, max_edge=240)


def _deliver(callback, thumb) -> None:
    if threading.current_thread() is threading.main_thread():
        callback(thumb)
        return
    from gi.repository import GLib
    GLib.idle_add(lambda: (callback(thumb), False)[1])
