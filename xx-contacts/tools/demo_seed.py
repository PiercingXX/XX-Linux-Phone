#!/usr/bin/env python3
"""Fill a HOME dir with realistic contacts through the app's real store.

Usage: demo_seed.py HOME_DIR

Creates the XDG layout (mode 0700) and seeds the nine contacts from the
Android reference list through ContactStore/AppConfig, plus a groups-only
entry for the picker/merge paths. Prints KEY: value markers (uids and
Android-style millisecond timestamps) and writes them to
HOME_DIR/seed_uids.json for the snap navigation scripts.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "common"))

from xx_contacts.app_config import AppConfig, default_config_path  # noqa: E402
from xx_contacts.privacy import ensure_private_dir  # noqa: E402
from xx_contacts.store import (  # noqa: E402
    AddressEntry,
    ContactData,
    ContactStore,
    EmailEntry,
    PhoneEntry,
    default_db_path,
    default_photos_dir,
)


def _ms() -> int:
    return int(time.time() * 1000)


def _phone(raw: str, type_: str = "CELL", primary: bool = False) -> PhoneEntry:
    return PhoneEntry(number_raw=raw, type=type_, is_primary=primary)


def seed(home: str) -> dict:
    ensure_private_dir(os.path.join(home, ".config", "xx-contacts"))
    ensure_private_dir(os.path.join(home, ".local", "share", "xx-contacts"))
    ensure_private_dir(default_photos_dir(home))
    store = ContactStore(default_db_path(home))
    markers: dict[str, str] = {}
    now = _ms()
    contacts = [
        ("ada", ContactData(
            display_name="Ada Lovelace", given="Ada", family="Lovelace",
            org="Analytical Engines", title="Engineer",
            birthday="1815-12-10", nickname="Ada",
            note="First program",
            starred=True, groups=["Family"],
            phones=[_phone("+1 555-010-0142", "CELL", True)],
            emails=[EmailEntry(address="ada@analytical.example",
                               type="HOME")],
            addresses=[AddressEntry(street="12 Charles St",
                                    locality="London",
                                    country="United Kingdom",
                                    type="HOME")])),
        ("analytical", ContactData(
            display_name="Analytical Engines", org="Analytical Engines",
            business=True, groups=["Business"],
            phones=[_phone("(555) 010-0198", "WORK", True)],
            emails=[EmailEntry(address="hello@analytical.example",
                               type="WORK")])),
        ("babbage", ContactData(
            display_name="Babbage Plumbing", org="Babbage Plumbing",
            phones=[_phone("+1 555-010-0170", "WORK", True)],
            groups=["Business"])),
        ("grace", ContactData(
            display_name="Grace Hopper", given="Grace", family="Hopper",
            org="US Navy", title="Rear Admiral",
            starred=True, groups=["Family"],
            phones=[_phone("+1 555-010-0117", "CELL", True)],
            emails=[EmailEntry(address="grace@navy.example",
                               type="WORK")])),
        ("katherine", ContactData(
            display_name="Katherine Johnson", given="Katherine",
            family="Johnson", org="NASA", title="Mathematician",
            birthday="1918-08-26",
            phones=[_phone("+1 555-010-0153", "CELL", True)],
            emails=[EmailEntry(address="katherine@nasa.example",
                               type="WORK")])),
        ("linus", ContactData(
            display_name="Linus Torvalds", given="Linus", family="Torvalds",
            org="Linux Foundation", title="Engineer",
            phones=[_phone("+1 555-010-0191", "CELL", True)],
            emails=[EmailEntry(address="linus@kernel.example",
                               type="WORK")])),
        ("marie", ContactData(
            display_name="Marie Curie", given="Marie", family="Curie",
            org="University of Paris", title="Physicist",
            birthday="1867-11-07", groups=["Family"],
            phones=[_phone("+1 555-010-0134", "CELL", True)],
            emails=[EmailEntry(address="marie@radium.example",
                               type="HOME")])),
        ("nikola", ContactData(
            display_name="Nikola Tesla", given="Nikola", family="Tesla",
            org="Tesla Labs", title="Inventor",
            birthday="1856-07-10",
            phones=[_phone("+1 555-010-0186", "CELL", True)])),
        ("rosalind", ContactData(
            display_name="Rosalind Franklin", given="Rosalind",
            family="Franklin", org="King's College",
            title="Chemist", birthday="1920-07-25",
            phones=[_phone("+1 555-010-0129", "CELL", True)],
            emails=[EmailEntry(address="rosalind@kings.example",
                               type="WORK")])),
    ]
    for key, data in contacts:
        uid = store.create(data)
        markers[f"{key}_uid"] = uid
        markers[f"{key}_ms"] = str(now)
        now += 1
    config = AppConfig(show_photos=False, me_uid=markers["ada_uid"],
                       default_region="US")
    config.save(default_config_path(home))
    markers["contact_count"] = str(store.count())
    markers["me_uid"] = markers["ada_uid"]
    with open(os.path.join(home, "seed_uids.json"), "w",
              encoding="utf-8") as handle:
        json.dump(markers, handle, indent=2)
    store.close()
    return markers


def main(argv: list) -> None:
    home = os.path.abspath(argv[1])
    for key, value in seed(home).items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main(sys.argv)
