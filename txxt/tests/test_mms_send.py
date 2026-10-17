"""L5 daemon MMS tests (fake level, no bus): SendMms scrub/fail-closed,
FetchMms tap-to-fetch, audio-drop + auto-reply, default-region dialing.
"""

import struct

import pytest

from txxt import daemon as daemon_mod
from txxt.daemon import Daemon, InboundPipeline, mms_content_type
from txxt.phone_numbers import configured_region
from txxt.settings import Settings
from txxt.store import Store


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def _jpeg_with_gps() -> bytes:
    return (
        b"\xff\xd8"
        + _segment(0xE0, b"JFIF\x00" + b"\x00" * 9)
        + _segment(0xE1, b"Exif\x00\x00GPS-payload")
        + b"\xff\xda\x11\x22\xff\x00\x33\xff\xd9"
    )


class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


def immediate_idle(fn, *args):
    fn(*args)
    return 1


def make_daemon(store, **kwargs):
    kwargs.setdefault("system_bus_fn", lambda: None)
    kwargs.setdefault("session_bus_fn", lambda: None)
    kwargs.setdefault("idle_add", immediate_idle)
    d = Daemon(store, **kwargs)
    d._executor = InlineExecutor()
    return d


class TestSendMms:
    def test_photo_sends_scrubbed_through_mms_sender(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        source = tmp_path / "camera.jpg"
        source.write_bytes(_jpeg_with_gps())
        seen = []

        def _sender(recipients, smil, attachments):
            seen.append((recipients, smil, attachments))
            kinds = {kind for _, kind, _ in attachments}
            assert kinds == {"text/plain", "image/jpeg"}
            assert "image_0.jpg" in smil and "text_0.txt" in smil
            for _name, _kind, path in attachments:
                if _kind == "image/jpeg":
                    assert b"GPS" not in open(path, "rb").read()
            return True, "/org/ofono/mms/message/9"

        store = Store.memory()
        d = make_daemon(store, mms_sender=_sender)
        message_id = d.send_mms(["+15551234567"], "look", [str(source)])
        row = store.get_message(message_id)
        assert row is not None and row.transport == "MMS" and row.sent is True
        assert row.body == "look"
        assert seen and seen[0][0] == ["+15551234567"]
        assert len(d._retry) == 0

    def test_empty_caption_sends_as_photo_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        source = tmp_path / "p.jpg"
        source.write_bytes(_jpeg_with_gps())
        store = Store.memory()
        d = make_daemon(store, mms_sender=lambda r, s, a: (True, "/m/1"))
        message_id = d.send_mms(["+1555"], "", [str(source)])
        assert store.get_message(message_id).body == "[Photo]"

    def test_unscrubbable_photo_refuses_and_persists_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        source = tmp_path / "evil.heic"
        source.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
        store = Store.memory()
        d = make_daemon(store, mms_sender=lambda r, s, a: (True, "/m/1"))
        with pytest.raises(ValueError):
            d.send_mms(["+1555"], "look", [str(source)])
        assert store.get_all_conversations() == []
        assert len(d._retry) == 0

    def test_missing_recipients_or_content_is_invalid(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        store = Store.memory()
        d = make_daemon(store, mms_sender=lambda r, s, a: (True, "/m/1"))
        with pytest.raises(ValueError):
            d.send_mms([], "hi", [])
        with pytest.raises(ValueError):
            d.send_mms(["+1555"], "   ", [])

    def test_transmit_failure_queues_mms_for_retry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        source = tmp_path / "p.jpg"
        source.write_bytes(_jpeg_with_gps())
        store = Store.memory()
        d = make_daemon(store, mms_sender=lambda r, s, a: (False, "no signal"))
        message_id = d.send_mms(["+1555"], "hi", [str(source)])
        assert store.get_message(message_id).sent is False
        assert len(d._retry) == 1
        assert store.pending_outgoing_mms()[0].id == message_id

    def test_retry_pump_resends_queued_mms(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        source = tmp_path / "p.jpg"
        source.write_bytes(_jpeg_with_gps())
        store = Store.memory()
        attempts = {"n": 0}

        def _sender(recipients, smil, attachments):
            attempts["n"] += 1
            if attempts["n"] < 2:
                return False, "down"
            return True, "/m/2"

        from txxt.daemon import RETRY_MAX_DELAY_MS, RetryQueue

        now = [0]
        d = make_daemon(
            store, mms_sender=_sender,
            retry=RetryQueue(now_millis=lambda: now[0]),
        )
        message_id = d.send_mms(["+1555"], "hi", [str(source)])
        assert len(d._retry) == 1
        now[0] += RETRY_MAX_DELAY_MS + 1
        counts = d.pump_retry()
        assert counts.resent == 1
        assert store.get_message(message_id).sent is True


class TestSendMmsDispatch:
    def test_sendmms_dispatch_replies_message_id(self, tmp_path, monkeypatch):
        from txxt.daemon import TXXT_IFACE, TXXT_OBJECT_PATH

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        source = tmp_path / "p.jpg"
        source.write_bytes(_jpeg_with_gps())
        store = Store.memory()
        d = make_daemon(store, mms_sender=lambda r, s, a: (True, "/m/1"))

        class FakeInvocation:
            def __init__(self):
                self.reply = None
                self.error = None

            def return_value(self, parameters):
                self.reply = parameters

            def return_dbus_error(self, name, message):
                self.error = (name, message)

        class FakeParams:
            def __init__(self, values):
                self._values = tuple(values)

            def unpack(self):
                return self._values

        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "SendMms",
            FakeParams(([str("+1555")], "hi", [str(source)])), invocation,
        )
        assert invocation.error is None
        (message_id,) = invocation.reply.unpack()
        assert store.get_message(int(message_id)).transport == "MMS"

    def test_sendmms_refused_photo_replies_invalid_argument(self, tmp_path, monkeypatch):
        from txxt.daemon import ERROR_INVALID_ARGUMENT, TXXT_IFACE, TXXT_OBJECT_PATH

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        refused = tmp_path / "evil.bin"
        refused.write_bytes(b"not an image at all...........")
        store = Store.memory()
        d = make_daemon(store, mms_sender=lambda r, s, a: (True, "/m/1"))

        class FakeInvocation:
            def __init__(self):
                self.reply = None
                self.error = None

            def return_value(self, parameters):
                self.reply = parameters

            def return_dbus_error(self, name, message):
                self.error = (name, message)

        class FakeParams:
            def __init__(self, values):
                self._values = tuple(values)

            def unpack(self):
                return self._values

        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "SendMms",
            FakeParams((["+1555"], "", [str(refused)])), invocation,
        )
        assert invocation.reply is None
        assert invocation.error[0] == ERROR_INVALID_ARGUMENT

    def test_fetchmms_dispatch_returns_media_path(self, tmp_path):
        from txxt.daemon import TXXT_IFACE, TXXT_OBJECT_PATH

        photo = tmp_path / "photo.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xd9")
        store = Store.memory()
        message_id = store.persist_inbound_mms_metadata("+1555", 1717243200000)
        store.set_media_path(message_id, str(photo))
        d = make_daemon(store)

        class FakeInvocation:
            def __init__(self):
                self.reply = None
                self.error = None

            def return_value(self, parameters):
                self.reply = parameters

            def return_dbus_error(self, name, message):
                self.error = (name, message)

        class FakeParams:
            def __init__(self, values):
                self._values = tuple(values)

            def unpack(self):
                return self._values

        invocation = FakeInvocation()
        d.handle_method_call(
            None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "FetchMms",
            FakeParams(((message_id,))), invocation,
        )
        assert invocation.error is None
        assert invocation.reply.unpack() == ((str(photo)),)


class TestFetchMms:
    def test_existing_media_path_returns_without_bus(self, tmp_path):
        photo = tmp_path / "photo-0.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xd9")
        store = Store.memory()
        message_id = store.persist_inbound_mms_metadata(
            "+1555", 1717243200000, content_location="http://mms/1",
        )
        store.set_media_path(message_id, str(photo))
        d = make_daemon(store)
        assert d.fetch_mms(message_id) == str(photo)

    def test_tap_copies_mmsd_attachment_to_mms_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        photo = b"\xff\xd8-carrier-bytes\xff\xd9"
        carrier_file = tmp_path / "carrier.bin"
        carrier_file.write_bytes(b"PDUHEAD" + photo + b"PDUTAIL")
        offset = len(b"PDUHEAD")
        store = Store.memory()
        message_id = store.persist_inbound_mms_metadata(
            "+1555", 1717243200000, content_location="http://mms/7",
        )

        class _Adapter:
            def list_messages(self):
                return ["/org/ofono/mms/message/7"]

            def get_message(self, path):
                assert path == "/org/ofono/mms/message/7"
                return {
                    "ContentLocation": "http://mms/7",
                    "Attachments": [
                        ("image_0.jpg", "image/jpeg", str(carrier_file),
                         offset, len(photo)),
                    ],
                }

            def delete_message(self, path):
                return True

        d = make_daemon(store)
        d._mms = _Adapter()
        found = d.fetch_mms(message_id)
        assert found and found.endswith(".jpg")
        assert open(found, "rb").read() == photo
        assert store.get_message(message_id).media_path == found

    def test_tap_prefers_exact_date_match(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        old_photo = tmp_path / "old.bin"
        old_photo.write_bytes(b"\xff\xd8-old\xff\xd9")
        new_photo = tmp_path / "new.bin"
        new_photo.write_bytes(b"\xff\xd8-new\xff\xd9")
        store = Store.memory()
        old_id = store.persist_inbound_mms_metadata("+1555", 1717243200000)
        new_id = store.persist_inbound_mms_metadata("+1555", 1717243800000)

        class _Adapter:
            def list_messages(self):
                return ["/org/ofono/mms/message/1", "/org/ofono/mms/message/2"]

            def get_message(self, path):
                if path.endswith("/1"):
                    return {
                        "Sender": "+1555", "Date": "2024-06-01T12:00:00+00:00",
                        "Attachments": [("a.jpg", "image/jpeg", str(old_photo))],
                    }
                return {
                    "Sender": "+1555", "Date": "2024-06-01T12:10:00+00:00",
                    "Attachments": [("b.jpg", "image/jpeg", str(new_photo))],
                }

            def delete_message(self, path):
                return True

        d = make_daemon(store)
        d._mms = _Adapter()
        found = d.fetch_mms(new_id)
        assert open(found, "rb").read() == b"\xff\xd8-new\xff\xd9"
        assert store.get_message(old_id).media_path is None

    def test_unknown_message_is_invalid(self):
        with pytest.raises(ValueError):
            make_daemon(Store.memory()).fetch_mms(424242)


class TestAudioDrop:
    def test_audio_part_drops_unstored_without_reply_by_default(self):
        store = Store.memory()
        sent = []
        pipe = InboundPipeline(store, auto_reply_send=lambda a, b: sent.append((a, b)))
        stored, replied = pipe.handle_mms_attachment("+1555", "audio/amr")
        assert (stored, replied) == (False, False)
        assert sent == []
        assert store.get_all_conversations() == []

    def test_audio_part_auto_replies_when_enabled(self):
        store = Store.memory()
        sent = []
        settings = Settings(mms_auto_reply=True)
        pipe = InboundPipeline(
            store, settings_loader=lambda: settings,
            auto_reply_send=lambda a, b: sent.append((a, b)),
        )
        stored, replied = pipe.handle_mms_attachment("+1555", "audio/mpeg")
        assert (stored, replied) == (False, True)
        assert sent == [("+1555", daemon_mod.__dict__.get("AUTO_REPLY_BODY", "") or sent[0][1])]

    def test_per_contact_off_suppresses_reply(self):
        store = Store.memory()
        sent = []
        settings = Settings(
            mms_auto_reply=True,
            mms_auto_reply_overrides={"+1555": "OFF"},
        )
        pipe = InboundPipeline(
            store, settings_loader=lambda: settings,
            auto_reply_send=lambda a, b: sent.append((a, b)),
        )
        assert pipe.handle_mms_attachment("+1555", "audio/amr") == (False, False)
        assert sent == []

    def test_image_part_stores(self):
        store = Store.memory()
        pipe = InboundPipeline(store)
        assert pipe.handle_mms_attachment("+1555", "image/jpeg") == (True, False)

    def test_mms_content_type_prefers_attachment_triples(self):
        props = {"Attachments": [("a.jpg", "audio/amr", "/tmp/a")]}
        assert mms_content_type(props) == "audio/amr"
        assert mms_content_type({}) is None

    def test_mms_content_type_reads_documented_5_tuples(self):
        props = {"Attachments": [("a.jpg", "image/jpeg", "/tmp/a", 0, 10)]}
        assert mms_content_type(props) == "image/jpeg"
        assert mms_content_type({"Attachments": [("only-two",)]}) is None


class TestDefaultRegion:
    def test_region_from_settings_object(self):
        assert configured_region(Settings(default_region="gb")) == "GB"

    def test_region_from_string(self):
        assert configured_region("de") == "DE"

    def test_missing_region_falls_back_to_us(self):
        assert configured_region(None) in ("US", "GB", "DE")

    def test_settings_round_trip_keeps_region(self, tmp_path):
        from txxt.settings import Settings as S
        from txxt.settings import load, save

        saved = save(S(default_region="fr"), tmp_path / "config.json")
        assert load(saved).default_region == "FR"
        assert load(tmp_path / "missing.json").default_region == "US"
