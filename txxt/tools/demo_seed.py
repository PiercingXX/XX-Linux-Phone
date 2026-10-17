#!/usr/bin/env python3
"""Fill a fake HOME with realistic TxxT data through the real store API.

Usage: demo_seed.py SEED_HOME

Creates SEED_HOME/.local/share + .config + .cache (mode 0700), opens the
real SQLite store there and seeds SMS/MMS threads with Android-style
millisecond ids, a quarantined thread, blocklist terms, starred numbers
and one on-disk MMS photo. Prints the seeded conversation ids as JSON.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent.parent / "src"))
sys.path.insert(0, str(HERE.parent.parent.parent / "common"))

MIN = 60_000
HOUR = 60 * MIN
DAY = 24 * HOUR

MARA = "+16145550101"
UNKNOWN = "+16145550142"
MOM = "+16145550103"
DENTAL = "+16145550104"
DEZ = "+16145550105"
JUNIPER = "+16145550106"
OWEN = "+16145550107"
SPAM = "+16145550199"

CID_MARA = 1792142430343
CID_UNKNOWN = 1792142431343
CID_MOM = 1792142432343
CID_DENTAL = 1792142433343
CID_DEZ = 1792142434343
CID_JUNIPER = 1792142435343
CID_OWEN = 1792142436343
CID_SPAM = 1792142437343


def _png(path: Path, width: int = 320, height: int = 200) -> None:
    """Small valid PNG (stdlib only): muted ridge gradient blocks."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(
        b"\x00" + b"".join(
            bytes(((x * 255) // width, (y * 255) // height, 128)) for x in range(width)
        )
        for y in range(height)
    )
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b""),
    )


def seed(home: Path) -> dict:
    from txxt.main import ensure_data_dir
    from txxt.store import ConversationRow, MessageRow, Store

    for sub in (".local/share", ".config", ".cache"):
        target = home / sub
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
    store = Store(str(ensure_data_dir() / "txxt.db"))
    try:
        now = int(time.time() * 1000)
        mid = now - 10 * DAY

        def msg(cid: int, direction: str, transport: str, body: str,
                stamp: int, sender: str | None = None, read: bool = False,
                sent: bool = True, media: str = "", location: str | None = None) -> int:
            nonlocal mid
            mid += 7
            store.upsert_message(MessageRow(
                id=mid, conversation_id=cid, direction=direction,
                transport=transport, body=body, timestamp_millis=stamp,
                sender_address=sender, is_read=read, sent=sent,
                media_path=media or None, content_location=location,
            ))
            return mid

        def conv(cid: int, address: str, **flags) -> None:
            store.upsert_conversation(ConversationRow(id=cid, participant_addresses=address, **flags))

        conv(CID_MARA, MARA, is_starred=True)
        msg(CID_MARA, "INCOMING", "SMS", "still on for tonight?", now - DAY - 46 * MIN, MARA, True)
        msg(CID_MARA, "OUTGOING", "SMS", "yeah. 9 at the laundromat", now - DAY - 45 * MIN)
        msg(CID_MARA, "INCOMING", "SMS", "front entrance? broken sign", now - 70 * MIN, MARA, True)
        msg(CID_MARA, "OUTGOING", "SMS", "that's the one. got the case", now - 65 * MIN)
        msg(CID_MARA, "INCOMING", "SMS", "on the 42 now. five min out", now - 8 * MIN, MARA)
        store.star(MARA)

        conv(CID_UNKNOWN, UNKNOWN)
        row = store.get_conversation(CID_UNKNOWN)
        store.set_flags(CID_UNKNOWN, row.flags().mute())
        msg(CID_UNKNOWN, "INCOMING", "SMS",
            "Are you around? Trying you again after 19:00.", now - 32 * MIN, UNKNOWN)

        conv(CID_MOM, MOM, is_starred=True)
        msg(CID_MOM, "INCOMING", "SMS",
            "Don't forget Sunday dinner at 6, love you.", now - 73 * MIN, MOM)
        store.star(MOM)

        conv(CID_DENTAL, DENTAL)
        msg(CID_DENTAL, "INCOMING", "SMS",
            "Cleaning Tue 10:30. Reply C to confirm or H to reschedule.", now - 5 * HOUR - 40 * MIN,
            DENTAL, True)

        conv(CID_DEZ, DEZ)
        msg(CID_DEZ, "OUTGOING", "SMS", "Running 10 late, start without me.", now - 9 * HOUR)

        media_dir = Path(str(ensure_data_dir())) / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        photo = media_dir / "IMG_2041.jpg.png"
        _png(photo)
        conv(CID_JUNIPER, JUNIPER)
        msg(CID_JUNIPER, "INCOMING", "SMS",
            "trail map is at parks.example.org/ridge", now - 2 * DAY - 20 * MIN, JUNIPER, True)
        msg(CID_JUNIPER, "INCOMING", "SMS",
            "The north gate trailhead moved past the ranger station, so park at the lower lot "
            "and follow the ridge path east for about a mile until you see the wooden footbridge.",
            now - 2 * DAY - 18 * MIN, JUNIPER, True)
        store.persist_inbound_mms_metadata(JUNIPER, now - 2 * DAY - 17 * MIN, "http://example.com/mms/9")
        msg(CID_JUNIPER, "INCOMING", "MMS", "[Photo]", now - 2 * DAY - 15 * MIN,
            JUNIPER, True, True, str(photo))
        msg(CID_JUNIPER, "OUTGOING", "MMS", "you: sent the photos", now - 90 * MIN,
            None, True, True, str(photo))

        conv(CID_OWEN, OWEN)
        msg(CID_OWEN, "OUTGOING", "SMS", "did you get my last text?", now - 25 * MIN,
            None, True, False)

        conv(CID_SPAM, SPAM)
        store.persist_inbound_sms(SPAM, "spammy hello, claim your prize now", now - 40 * MIN,
                                  quarantined=True)
        store.persist_inbound_sms(SPAM, "last chance before midnight", now - 35 * MIN,
                                  quarantined=True)

        store.blocklist_add("sender", "+16145550200")
        store.blocklist_add("keyword", "loan")
        store.blocklist_add("phrase", "act now")
        from txxt import blocking as blocking_mod

        store.set_setting(blocking_mod.QUARANTINE_SETTING_KEY, True)
        blocking_mod.live_apply_from_store(store)
    finally:
        store.close()
    return {
        "mara": CID_MARA, "unknown": CID_UNKNOWN, "mom": CID_MOM,
        "dental": CID_DENTAL, "dez": CID_DEZ, "juniper": CID_JUNIPER,
        "owen": CID_OWEN, "spam": CID_SPAM,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: demo_seed.py SEED_HOME", file=sys.stderr)
        return 2
    home = Path(argv[1])
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["XDG_DATA_HOME"] = str(home / ".local" / "share")
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    ids = seed(home)
    (home / "ids.json").write_text(json.dumps(ids, indent=2), encoding="utf-8")
    print(json.dumps(ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
