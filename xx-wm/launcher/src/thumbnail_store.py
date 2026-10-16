"""Last-seen recents stills. Capture is output-sized; we keep a tiny RGBA.

Keyed by app_id (one window per app is the phone case). Own surfaces are
never stored. LRU-capped so a long session cannot grow without bound.
"""

from __future__ import annotations

from dataclasses import dataclass

_OWN_APP_ID = 'io.piercingxx.XXWM'
_MAX_ITEMS = 8
_MAX_EDGE = 240

# wl_shm.format
_ARGB8888 = 0
_XRGB8888 = 1
_XBGR8888 = 0x34324258
_ABGR8888 = 0x34324142


@dataclass(frozen=True)
class Thumbnail:
    width: int
    height: int
    rgba: bytes  # tightly packed RGBA8888


def downsample_shm(
    data: bytes,
    width: int,
    height: int,
    stride: int,
    shm_format: int,
    y_invert: bool = False,
    max_edge: int = _MAX_EDGE,
) -> Thumbnail:
    """Nearest-neighbour downscale + convert to RGBA. No extra deps."""
    if width <= 0 or height <= 0 or stride < 4 or len(data) < stride:
        raise ValueError('invalid shm buffer')
    scale = max(width, height) / max_edge
    if scale < 1:
        scale = 1.0
    tw = max(1, int(width / scale))
    th = max(1, int(height / scale))
    xbgr = shm_format in (_XBGR8888, _ABGR8888)
    opaque = shm_format in (_XRGB8888, _XBGR8888)
    out = bytearray(tw * th * 4)
    for dy in range(th):
        sy = int(dy * scale)
        if y_invert:
            sy = height - 1 - sy
        row = sy * stride
        dst = dy * tw * 4
        for dx in range(tw):
            off = row + int(dx * scale) * 4
            b, g, r, a = data[off], data[off + 1], data[off + 2], data[off + 3]
            if xbgr:
                r, b = b, r
            if opaque:
                a = 255
            o = dst + dx * 4
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            out[o + 3] = a
    return Thumbnail(tw, th, bytes(out))


class ThumbnailStore:
    def __init__(self, max_items: int = _MAX_ITEMS) -> None:
        self._max = max_items
        self._items: dict[str, Thumbnail] = {}
        self._order: list[str] = []
        self._callbacks: list[object] = []

    def get(self, app_id: str) -> Thumbnail | None:
        if not app_id or app_id == _OWN_APP_ID:
            return None
        return self._items.get(app_id)

    def put(self, app_id: str, thumb: Thumbnail) -> None:
        if not app_id or app_id == _OWN_APP_ID:
            return
        if app_id in self._items:
            self._order.remove(app_id)
        self._items[app_id] = thumb
        self._order.append(app_id)
        while len(self._order) > self._max:
            old = self._order.pop(0)
            self._items.pop(old, None)
        for cb in list(self._callbacks):
            try:
                cb()  # type: ignore[operator]
            except Exception:
                pass

    def on_change(self, callback: object) -> None:
        self._callbacks.append(callback)
