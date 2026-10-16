"""Privacy tests: data/config dirs are 0700, written files are 0600."""

import json
import os
import stat

from xx_contacts import privacy
from xx_contacts.app_config import AppConfig
from xx_contacts.store import ContactData, ContactStore, PhoneEntry


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_private_dir_repaired_to_0700(tmp_path):
    target = str(tmp_path / "xx-contacts")
    os.makedirs(target, mode=0o755)
    assert _mode(target) == 0o755
    privacy.ensure_private_dir(target)
    assert _mode(target) == 0o700


def test_secure_write_is_0600_in_0700_dir(tmp_path):
    path = str(tmp_path / "sub" / "config.json")
    os.makedirs(os.path.dirname(path), mode=0o755)
    privacy.secure_write_text(path, '{"a": 1}\n')
    assert _mode(path) == 0o600
    assert _mode(os.path.dirname(path)) == 0o700
    with open(path, encoding="utf-8") as handle:
        assert json.load(handle) == {"a": 1}


def test_store_open_creates_private_data_and_photos_dirs(tmp_path):
    data_dir = tmp_path / "xx-contacts"
    os.makedirs(str(data_dir), mode=0o755)
    db_path = str(data_dir / "contacts.db")
    store = ContactStore(db_path)
    try:
        assert _mode(str(data_dir)) == 0o700
        assert _mode(os.path.join(str(data_dir), "photos")) == 0o700
        assert _mode(db_path) == 0o600
    finally:
        store.close()


def test_store_wal_and_shm_are_0600(tmp_path):
    db_path = str(tmp_path / "contacts.db")
    store = ContactStore(db_path)
    try:
        store.create(ContactData(
            display_name="Ada Lovelace",
            phones=[PhoneEntry(number_raw="+1 555 0100",
                                number_digits="15550100")]))
        wal = db_path + "-wal"
        assert os.path.exists(wal), "expected a WAL sidecar after a write"
        assert _mode(db_path) == 0o600
        assert _mode(wal) == 0o600
        shm = db_path + "-shm"
        if os.path.exists(shm):
            assert _mode(shm) == 0o600
        assert _mode(str(tmp_path)) == 0o700
    finally:
        store.close()


def test_store_repairs_world_readable_db_and_dirs(tmp_path):
    db_path = str(tmp_path / "contacts.db")
    store = ContactStore(db_path)
    store.close()
    os.chmod(db_path, 0o644)
    os.chmod(str(tmp_path), 0o755)
    reopened = ContactStore(db_path)
    try:
        assert _mode(db_path) == 0o600
        assert _mode(str(tmp_path)) == 0o700
    finally:
        reopened.close()


def test_store_open_restores_umask(tmp_path):
    before = os.umask(0o022)
    os.umask(before)
    store = ContactStore(str(tmp_path / "contacts.db"))
    store.close()
    after = os.umask(0o022)
    os.umask(after)
    assert before == after == 0o022


def test_config_save_is_0600(tmp_path):
    path = str(tmp_path / "xx-contacts" / "config.json")
    AppConfig(has_phone_only=True).save(path)
    assert _mode(path) == 0o600
    assert _mode(os.path.dirname(path)) == 0o700
    assert AppConfig.load(path).has_phone_only is True


def test_memory_store_needs_no_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = ContactStore(":memory:")
    try:
        assert store.count() == 0
    finally:
        store.close()
