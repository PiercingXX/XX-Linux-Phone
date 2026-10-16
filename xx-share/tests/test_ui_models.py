"""History, favorites, and transfer-cancel models without GTK."""
import pytest

from config import ShareConfig
from favorites import (
    auto_accept_enabled,
    favorites_list,
    is_favorite,
    set_auto_accept,
    set_favorite,
)
from history import HistoryStore, load_entries, make_entry, save_entries
from transfers import TransferCancelled, TransferManager, new_transfer_id


def _config(tmp_path):
    return ShareConfig(tmp_path / "config")


def test_favorites_roundtrip(tmp_path):
    config = _config(tmp_path)
    assert favorites_list(config) == []
    assert is_favorite(config, "ab") is False
    set_favorite(config, "AB", True)
    assert is_favorite(config, "ab") is True
    assert favorites_list(config) == ["ab"]
    set_favorite(config, "ab", True)
    assert favorites_list(config) == ["ab"]
    set_favorite(config, "ab", False)
    assert favorites_list(config) == []
    assert auto_accept_enabled(config) is False
    set_auto_accept(config, True)
    assert auto_accept_enabled(config) is True
    again = ShareConfig(tmp_path / "config")
    assert favorites_list(again) == []
    assert auto_accept_enabled(again) is True


def test_favorites_ignore_blank(tmp_path):
    config = _config(tmp_path)
    assert set_favorite(config, "  ", True) == []


def test_history_save_load_roundtrip(tmp_path):
    path = tmp_path / "history.json"
    assert load_entries(path) == []
    save_entries(path, [make_entry("phone", "fp", "a.jpg", 10, "/tmp/a.jpg",
                                   clock=lambda: 123.0)])
    entries = load_entries(path)
    assert len(entries) == 1
    assert entries[0]["sender_alias"] == "phone"
    assert entries[0]["received_at"] == 123.0


def test_history_survives_corrupt_file(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("not json", encoding="utf-8")
    assert load_entries(path) == []
    path.write_text('{"not": "a list"}', encoding="utf-8")
    assert load_entries(path) == []


def test_history_store_caps_and_orders(tmp_path):
    store = HistoryStore(tmp_path / "history.json")
    for index in range(105):
        store.record("phone", "fp", f"{index}.jpg", index, f"/tmp/{index}.jpg")
    assert len(store.entries) == 100
    listed = store.list()
    assert listed[0]["file_name"] == "104.jpg"
    store.clear()
    assert store.list() == []
    assert load_entries(tmp_path / "history.json") == []


def test_transfer_ids_unique():
    assert new_transfer_id() != new_transfer_id()


def test_transfer_cancel_flag():
    manager = TransferManager()
    tid = manager.start("fp", ["a.jpg"])
    assert manager.is_cancelled(tid) is False
    assert manager.describe(tid)["fingerprint"] == "fp"
    assert manager.request_cancel(tid) is True
    assert manager.is_cancelled(tid) is True
    with pytest.raises(TransferCancelled):
        manager.check(tid)
    manager.finish(tid)
    assert manager.request_cancel(tid) is False
    assert manager.describe(tid) is None


def test_transfer_progress_recorded():
    manager = TransferManager()
    tid = manager.start("fp", ["a.jpg"])
    state = manager.record_progress(tid, "f1", 5, 10)
    assert state == {"f1": {"sent": 5, "total": 10}}


def test_send_files_aborts_when_cancelled(tmp_path, monkeypatch):
    import client as client_module
    from client import TransferError, send_files
    from protocol import DeviceInfo

    target = tmp_path / "a.jpg"
    target.write_bytes(b"0123456789")
    calls = {}

    def fake_prepare(*_args, **_kwargs):
        return "sid-1", {"file-0": "tok"}

    def fake_cancel(_host, _port, session_id, timeout=10.0, **_kwargs):
        calls["cancelled"] = session_id

    monkeypatch.setattr(client_module, "prepare_upload", fake_prepare)
    monkeypatch.setattr(client_module, "cancel_session", fake_cancel)
    peer = DeviceInfo(alias="you", fingerprint="bb")
    sender = DeviceInfo(alias="me", fingerprint="aa")
    with pytest.raises(TransferError, match="cancelled"):
        send_files(peer, "10.0.0.6", sender, [target],
                   is_cancelled=lambda: True)
    assert calls["cancelled"] == "sid-1"


def test_progress_reader_aborts_mid_file(tmp_path):
    from client import TransferError, _ProgressReader

    target = tmp_path / "a.bin"
    target.write_bytes(b"x" * 100)
    reader = _ProgressReader(target, 100, lambda _s, _t: None,
                             is_cancelled=lambda: True)
    with pytest.raises(TransferError, match="cancelled"):
        reader.read()
