"""L2 fix proof: multi-MB SBR files import with flat memory.

Generates a real multi-MB SMS Backup & Restore document on disk (tens
of MMS each carrying a base64 photo part, the shape that OOMs phones
when read whole), imports it through :func:`backup.import_sbr_file`,
and asserts peak ``tracemalloc`` growth stays a fraction of the file
size — the old ``ET.fromstring`` whole-tree read plus in-RAM photo
bytes peaks at several times the file size.
"""

import base64
import tempfile
import tracemalloc
from pathlib import Path

from txxt import backup
from txxt.store import Store

MMS_COUNT = 40
PHOTO_RAW_BYTES = 300 * 1024
SMS_COUNT = 200


def photo_payload(seed: int) -> bytes:
    """Deterministic photo bytes: every part differs, all verify exactly."""
    return bytes((seed + index) % 256 for index in range(PHOTO_RAW_BYTES))


def write_big_sbr(path: Path) -> dict:
    """Write a multi-MB SBR document incrementally; return expectations."""
    expected = {}
    with open(path, "w", encoding="utf-8") as dest:
        dest.write("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n")
        dest.write(f'<smses count="{SMS_COUNT + MMS_COUNT}">\n')
        for index in range(SMS_COUNT):
            dest.write(
                f'<sms protocol="0" address="+1555000{index % 10:04d}"'
                f' date="{1700000000000 + index}" type="1"'
                f' body="bulk sms {index}" read="1" />\n'
            )
        for index in range(MMS_COUNT):
            blob = photo_payload(index)
            encoded = base64.b64encode(blob).decode("ascii")
            expected[f"mms caption {index}"] = blob
            dest.write(
                f'<mms date="{1700000002 + index}" msg_box="1" read="1">\n'
                "<parts>\n"
                f'<part seq="0" ct="text/plain" text="mms caption {index}" />\n'
                f'<part seq="1" ct="image/jpeg" data="{encoded}" />\n'
                "</parts>\n"
                '<addrs><addr address="insert-address-token" type="137" />'
                '<addr address="+15551234567" type="137" /></addrs>\n'
                "</mms>\n"
            )
            del blob, encoded
        dest.write("</smses>\n")
    return expected


def staged_leftovers() -> list[str]:
    return [
        name for name in Path(tempfile.gettempdir()).iterdir()
        if name.name.startswith("txxt-sbr-")
    ]


class TestSbrStreamingMemory:
    def test_multi_mb_import_stays_flat(self, tmp_path):
        xml_path = tmp_path / "big.xml"
        expected = write_big_sbr(xml_path)
        file_bytes = xml_path.stat().st_size
        assert file_bytes > 10 * 1024 * 1024

        store = Store.memory()
        tracemalloc.start()
        try:
            baseline = tracemalloc.get_traced_memory()[0]
            result = backup.import_sbr_file(
                store, xml_path, media_root=tmp_path / "media"
            )
            _current, peak = tracemalloc.get_traced_memory()
            growth = peak - baseline
        finally:
            tracemalloc.stop()

        assert result.imported == SMS_COUNT + MMS_COUNT
        assert result.photos_restored == MMS_COUNT
        assert result.missing_photos == 0
        assert growth < file_bytes // 3, (
            f"peak growth {growth / 1e6:.1f} MB for a {file_bytes / 1e6:.1f} MB file"
        )

        rows = [
            item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)
        ]
        assert len(rows) == SMS_COUNT + MMS_COUNT
        photos = {item.body: item for item in rows if item.transport == "MMS"}
        assert len(photos) == MMS_COUNT
        for body, blob in list(expected.items())[:5]:
            assert Path(photos[body].media_path).read_bytes() == blob

    def test_staging_dir_is_cleaned(self, tmp_path):
        xml_path = tmp_path / "big.xml"
        write_big_sbr(xml_path)
        before = set(staged_leftovers())
        backup.import_sbr_file(Store.memory(), xml_path, media_root=tmp_path / "m")
        assert set(staged_leftovers()) == before

    def test_truncated_file_rolls_back_everything(self, tmp_path):
        xml_path = tmp_path / "big.xml"
        write_big_sbr(xml_path)
        with open(xml_path, "r+b") as handle:
            handle.truncate(xml_path.stat().st_size - 500)
        store = Store.memory()
        try:
            backup.import_sbr_file(store, xml_path, media_root=tmp_path / "media")
        except backup.BackupError:
            pass
        else:
            raise AssertionError("truncated XML must be refused")
        assert store.get_all_conversations() == []
        assert not (tmp_path / "media").exists() or not any(
            (tmp_path / "media").rglob("*")
        )

    def test_path_string_imports_like_inline_xml(self, tmp_path):
        from test_backup import SBR_XML  # noqa: E402

        store = Store.memory()
        result = backup.import_sbr_xml(store, SBR_XML, media_root=tmp_path / "m")
        assert result.imported == 3
        xml_path = tmp_path / "sbr.xml"
        xml_path.write_text(SBR_XML, encoding="utf-8")
        second = Store.memory()
        result = backup.import_sbr_file(second, str(xml_path), media_root=tmp_path / "m2")
        assert result.imported == 3
        assert result.photos_restored == 1

    def test_double_file_import_never_duplicates(self, tmp_path):
        xml_path = tmp_path / "big.xml"
        write_big_sbr(xml_path)
        store = Store.memory()
        backup.import_sbr_file(store, xml_path, media_root=tmp_path / "media")

        def dump():
            convs = sorted(
                (r.id, r.participant_addresses) for r in store.get_all_conversations()
            )
            msgs = sorted(
                (m.body, m.timestamp_millis)
                for c in store.get_all_conversations()
                for m in store.get_messages_for_conversation(c.id)
            )
            return convs, msgs

        before = dump()
        result = backup.import_sbr_file(store, xml_path, media_root=tmp_path / "media")
        assert dump() == before
        assert result.imported == 0
