#!/usr/bin/env python3
"""Seed a HOME dir with realistic xx-captions data through the real APIs.

Creates: xx-wm theme config, xx-captions config, an installed phone-default
model (placeholder bytes, never downloaded), and a week of call transcripts
with Android-family names, millisecond line offsets and epoch timestamps.

Usage: demo_seed.py HOME [--theme amoled]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "common"))

from xx_captions import config, models  # noqa: E402
from xx_captions.store import TranscriptStore  # noqa: E402

DAY = 86400

CALLS: list[tuple[str, float, list[tuple[str, int]]]] = [
    ("Mara Voss", 0.2, [
        ("front entrance? broken sign", 1200),
        ("that's the one. got the case", 5400),
        ("cab's outside whenever you're ready", 9800),
    ]),
    ("+1 (614) 555-0199", 1.1, [
        ("hi, this is ridgeline dental", 800),
        ("confirming your cleaning tuesday at ten thirty", 4100),
        ("just reply C to confirm", 8600),
    ]),
    ("Dez Okafor", 2.3, [
        ("yeah i'm five minutes out", 1500),
        ("bring the charger if you still have it", 5900),
    ]),
    ("Mom", 4.6, [
        ("call me when you land, okay?", 2000),
    ]),
    ("Ada Lovelace", 6.8, [
        ("the analytical engine weaves algebraical patterns", 1100),
        ("just like the jacquard loom weaves flowers and leaves", 5300),
        ("the engine can arrange numbers like letters", 9400),
        ("poetical science, really", 13200),
    ]),
]


def seed(home: str, theme: str = "amoled") -> None:
    from pathlib import Path

    home_path = Path(home)
    now = int(time.time())

    shell_conf = home_path / ".config" / "xx-wm"
    shell_conf.mkdir(parents=True, exist_ok=True)
    (shell_conf / "config.json").write_text(
        json.dumps({"theme": theme}) + "\n", encoding="utf-8")

    data = config.load(home_path)
    data.update({
        "engine": "sherpa-onnx",
        "fallback_engine": "vosk",
        "model": config.DEFAULT_CONFIG["model"],
        "language": "en",
        "call_transcription_enabled": True,
        "announce_tone": False,
    })
    config.save(data, home_path)

    model_id = config.DEFAULT_CONFIG["model"]
    entry = config.MODEL_CATALOG[model_id]
    for name in entry["files"]:
        target = models.model_dir(model_id, home_path) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(b"demo-model-bytes")

    store = TranscriptStore(home_path)
    try:
        if store.list_transcripts():
            return
        for peer, days_ago, lines in CALLS:
            started = now - int(days_ago * DAY)
            call_id = store.begin_call(peer, started=started)
            for text, offset_ms in lines:
                store.add_line(call_id, text, offset_ms=offset_ms)
            duration_s = (lines[-1][1] // 1000) + 14 if lines else 30
            store.end_call(call_id, ended=started + duration_s)
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home")
    parser.add_argument("--theme", default="amoled")
    args = parser.parse_args()
    seed(args.home, args.theme)
    print(f"seeded {args.home} (theme={args.theme})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
