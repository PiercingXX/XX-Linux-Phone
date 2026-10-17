#!/usr/bin/env python3
"""Fill a HOME dir with realistic xx-share data through the real store APIs.

Usage: demo_seed.py HOME_DIR [--theme amoled]

Writes the xx-share config (device name, PINs, favorites), a received
history via HistoryStore, and a few demo files to pick as the chosen
files, all under HOME_DIR. Peer devices are network state, so they are
injected by the snap --script through the window's real peer-render
path instead (see tools/snap_*.py payloads in snap_all.sh).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent.parent / "src"))
sys.path.insert(0, str(HERE.parent.parent.parent / "common"))

PEERS = [
    ("Mara's Pixel", "9f2ac41107be4d5aa91c2f83dd40e6b1f33c98d0aa14be27c0d1f88aa17e2c11",
     "192.168.1.21", "mobile"),
    ("Living-Room TV", "41bd9c0e88f04a2d9b77aa10c3f55e901bd34c8822af601d77e990bc1234abcd",
     "192.168.1.34", "desktop"),
    ("Jonas ThinkPad", "c7d10e5f99a44b2c8d01ef77aa5460d2c9e88d1a3f74b0c5d6e1234f88776655",
     "192.168.1.42", "desktop"),
]

HISTORY = [
    ("Mara's Pixel", PEERS[0][1], "IMG_20240612_184410.jpg", 2_412_388),
    ("Mara's Pixel", PEERS[0][1], "birthday-dinner.mp4", 48_312_770),
    ("Jonas ThinkPad", PEERS[2][1], "slides-final-really-final.pdf", 1_048_576),
    ("Jonas ThinkPad", PEERS[2][1], "holiday-cabin.jpg", 3_801_022),
    ("Living-Room TV", PEERS[1][1], "movie-night.mkv", 210_763_776),
    ("Mara's Pixel", PEERS[0][1], "notes.txt", 1_204),
]

DEMO_FILES = [
    ("IMG_20240613_091522.jpg", 3_145_728),
    ("concert-clip.mp4", 26_214_400),
    ("reading-list.pdf", 524_288),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("home")
    parser.add_argument("--theme", default=None)
    args = parser.parse_args()

    home = Path(args.home)
    home.mkdir(parents=True, exist_ok=True)

    from config import ShareConfig
    from favorites import set_auto_accept, set_favorite
    from history import HistoryStore

    config = ShareConfig(home / ".config" / "xx-share")
    config.data["alias"] = "My Phone"
    config.data["require_pin"] = "2468"
    config.data["visible"] = True
    config.data["auto_accept_favorites"] = True
    config.save()
    set_favorite(config, PEERS[0][1], True)
    set_favorite(config, PEERS[2][1], True)
    set_auto_accept(config, True)

    store = HistoryStore(
        home / ".local" / "share" / "xx-share" / "history.json")
    for alias, fingerprint, name, size in HISTORY:
        store.record(alias, fingerprint, name, size,
                     home / "Downloads" / name)

    demo_dir = home / "demo-files"
    demo_dir.mkdir(parents=True, exist_ok=True)
    for name, size in DEMO_FILES:
        path = demo_dir / name
        with open(path, "wb") as handle:
            handle.truncate(size)

    if args.theme:
        wm_config = home / ".config" / "xx-wm" / "config.json"
        wm_config.parent.mkdir(parents=True, exist_ok=True)
        wm_config.write_text(json.dumps({"theme": args.theme}) + "\n",
                             encoding="utf-8")

    print(f"seeded {home}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
