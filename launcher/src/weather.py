"""
Weather widget backend — Open-Meteo current conditions, no API key.

Coordinates come from the `weather_lat`/`weather_lon` config floats (set
manually — iio location is device-gated). Results cache to
~/.cache/xx-wm/weather.json and refresh at most every 15 minutes.
Offline or unset → the widget silently shows `--°`. Fetches run on a daemon
thread; the UI callback is dispatched via an injectable dispatcher
(GLib.idle_add at runtime, synchronous in tests).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

REFRESH_SECONDS = 15 * 60
PLACEHOLDER = '--°'

_API = ('https://api.open-meteo.com/v1/forecast'
        '?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code')

# WMO weather interpretation codes, collapsed to short text
_WMO_TEXT = [
    ((0,), 'Clear'),
    ((1, 2), 'Partly cloudy'),
    ((3,), 'Cloudy'),
    ((45, 48), 'Fog'),
    ((51, 53, 55, 56, 57), 'Drizzle'),
    ((61, 63, 65, 66, 67, 80, 81, 82), 'Rain'),
    ((71, 73, 75, 77, 85, 86), 'Snow'),
    ((95, 96, 99), 'Storm'),
]


def describe(code: int) -> str:
    for codes, text in _WMO_TEXT:
        if code in codes:
            return text
    return ''


def format_current(temp_c: float, code: int) -> str:
    text = describe(code)
    degrees = f'{round(temp_c)}°'
    return f'{degrees} {text}'.strip()


def default_cache_path() -> Path:
    return Path.home() / '.cache' / 'xx-wm' / 'weather.json'


def _http_fetch(lat: float, lon: float) -> dict:
    url = _API.format(lat=lat, lon=lon)
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode('utf-8'))
    current = payload.get('current') or {}
    return {
        'temp_c': float(current['temperature_2m']),
        'code': int(current.get('weather_code', -1)),
    }


class WeatherProvider:
    def __init__(self, config: object,
                 cache_path: Path | None = None,
                 fetcher: Callable[[float, float], dict] | None = None,
                 clock: Callable[[], float] = time.time,
                 dispatch: Callable[..., object] | None = None) -> None:
        self._config = config
        self._cache_path = cache_path or default_cache_path()
        self._fetch = fetcher or _http_fetch
        self._clock = clock
        self._dispatch = dispatch
        self._fetching = False

    # -- coordinates -------------------------------------------------------

    def coordinates(self) -> tuple[float, float] | None:
        try:
            lat = float(self._config.data.get('weather_lat'))
            lon = float(self._config.data.get('weather_lon'))
        except (TypeError, ValueError):
            return None
        return lat, lon

    # -- cache -------------------------------------------------------------

    def _load_cache(self) -> dict | None:
        try:
            raw = json.loads(self._cache_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(raw, dict) or 'fetched_at' not in raw:
            return None
        return raw

    def _store_cache(self, data: dict) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(data), encoding='utf-8')
        except OSError:
            pass

    def cache_is_fresh(self) -> bool:
        cached = self._load_cache()
        if cached is None:
            return False
        try:
            return self._clock() - float(cached['fetched_at']) < REFRESH_SECONDS
        except (TypeError, ValueError):
            return False

    # -- public ------------------------------------------------------------

    def current_text(self) -> str:
        """Cached conditions as `18° Clear`; `--°` when unset/offline/stale-empty."""
        cached = self._load_cache()
        if cached is None:
            return PLACEHOLDER
        try:
            return format_current(float(cached['temp_c']), int(cached['code']))
        except (KeyError, TypeError, ValueError):
            return PLACEHOLDER

    def refresh(self, on_done: Callable[[str], None],
                force: bool = False) -> None:
        """Refresh if stale (or forced); on_done always gets the display text."""
        coords = self.coordinates()
        if coords is None:
            on_done(PLACEHOLDER)
            return
        if not force and self.cache_is_fresh():
            on_done(self.current_text())
            return
        if self._fetching:
            return
        self._fetching = True
        threading.Thread(target=self._refresh_worker, args=(coords, on_done),
                         daemon=True, name='weather-fetch').start()

    def _refresh_worker(self, coords: tuple[float, float],
                        on_done: Callable[[str], None]) -> None:
        text = self.current_text()
        try:
            data = self._fetch(*coords)
            data['fetched_at'] = self._clock()
            self._store_cache(data)
            text = format_current(float(data['temp_c']), int(data['code']))
        except Exception:
            pass  # offline: keep whatever the cache had
        finally:
            self._fetching = False
        dispatch = self._dispatch
        if dispatch is None:
            from gi.repository import GLib
            dispatch = GLib.idle_add
        dispatch(lambda: on_done(text) and False or False)
