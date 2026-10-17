from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

LOCK_SCREEN_PRIVACY = ('sender-only', 'content', 'nothing')
ALERT_STYLES = ('silent', 'vibrate', 'sound')


@dataclass
class Settings:
    lock_screen_privacy: str = 'sender-only'
    alert_style: str = 'sound'
    default_region: str = 'US'
    mms_auto_download: bool = False
    mms_auto_reply: bool = False
    mms_auto_reply_overrides: dict | None = None

    @classmethod
    def defaults(cls) -> 'Settings':
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> 'Settings':
        base = cls.defaults()
        if not isinstance(data, dict):
            return base
        if data.get('lock_screen_privacy') in LOCK_SCREEN_PRIVACY:
            base.lock_screen_privacy = data['lock_screen_privacy']
        if data.get('alert_style') in ALERT_STYLES:
            base.alert_style = data['alert_style']
        # Retired appearance keys (auto_sync_theme, theme_preset,
        # font_mode): the shell theme is the single theme source, so
        # existing configs carrying them still load — they are ignored.
        region = data.get('default_region')
        if isinstance(region, str) and region.strip():
            base.default_region = region.strip().upper()
        if isinstance(data.get('mms_auto_download'), bool):
            base.mms_auto_download = data['mms_auto_download']
        if isinstance(data.get('mms_auto_reply'), bool):
            base.mms_auto_reply = data['mms_auto_reply']
        overrides = data.get('mms_auto_reply_overrides')
        if isinstance(overrides, dict):
            base.mms_auto_reply_overrides = {
                str(k): str(v) for k, v in overrides.items()
            }
        return base

    def to_dict(self) -> dict:
        return asdict(self)


def config_path() -> Path:
    root = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(root) / 'txxt' / 'config.json'


def load(path: Path | None = None) -> Settings:
    target = Path(path) if path is not None else config_path()
    try:
        raw = target.read_text(encoding='utf-8')
    except OSError:
        return Settings.defaults()
    try:
        data = json.loads(raw)
    except ValueError:
        return Settings.defaults()
    return Settings.from_dict(data)


def save(settings: Settings, path: Path | None = None) -> Path:
    target = Path(path) if path is not None else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(settings.to_dict(), indent=2), encoding='utf-8')
    return target
