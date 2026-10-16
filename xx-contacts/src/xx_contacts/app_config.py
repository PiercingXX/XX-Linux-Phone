"""App preferences. Pure module: JSON file at a given path, no GTK."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from xx_contacts.privacy import secure_write_text


@dataclass
class AppConfig:
    has_phone_only: bool = False
    show_photos: bool = False
    default_region: str = "US"
    me_uid: str = ""
    publish_to_eds: bool = False

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        cfg = cls()
        if isinstance(data.get("has_phone_only"), bool):
            cfg.has_phone_only = data["has_phone_only"]
        if isinstance(data.get("show_photos"), bool):
            cfg.show_photos = data["show_photos"]
        region = data.get("default_region")
        if isinstance(region, str) and region.strip():
            cfg.default_region = region.strip().upper()
        me_uid = data.get("me_uid")
        if isinstance(me_uid, str) and me_uid.strip():
            cfg.me_uid = me_uid.strip()
        if isinstance(data.get("publish_to_eds"), bool):
            cfg.publish_to_eds = data["publish_to_eds"]
        return cfg

    def save(self, path: str) -> None:
        secure_write_text(path, json.dumps(asdict(self), indent=2) + "\n")


def default_config_path(home: str) -> str:
    return os.path.join(home, ".config", "xx-contacts", "config.json")
