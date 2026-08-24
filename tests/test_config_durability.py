"""ShellConfig durability + PIN hardening: atomic saves (crash-safe replace,
0600 modes) and PBKDF2 pin storage with transparent legacy-sha256 upgrade.
Pure logic, no GTK imports."""
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # ShellConfig() reads ~/.config/xx-wm/config.json in its constructor;
    # point HOME at a temp dir so the dev box's real shell config never
    # leaks into tests.
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def _config(tmp_path):
    config = ShellConfig()
    config.config_dir = tmp_path / 'cfg'
    config.config_path = config.config_dir / 'config.json'
    return config


class TestAtomicSave:
    def test_crash_mid_save_leaves_previous_config_intact(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        config.set_pin('123456')
        before = json.loads(config.config_path.read_text(encoding='utf-8'))
        assert before['pin_hash'].startswith('pbkdf2$')

        def crash(*_args, **_kwargs):
            raise OSError('simulated crash mid-write')

        monkeypatch.setattr(os, 'fsync', crash)
        config.data['theme'] = 'ocean'
        with pytest.raises(OSError):
            config.save()

        after = json.loads(config.config_path.read_text(encoding='utf-8'))
        assert after == before

    def test_saved_file_mode_is_0600(self, tmp_path):
        config = _config(tmp_path)
        config.set_pin('123456')
        mode = stat.S_IMODE(os.stat(config.config_path).st_mode)
        assert mode == 0o600


class TestPinHashing:
    def test_pbkdf2_pin_roundtrip(self, tmp_path):
        config = _config(tmp_path)
        config.set_pin('135790')
        assert config.pin_hash.startswith('pbkdf2$')
        assert config.verify_pin('135790') is True
        assert config.verify_pin('000000') is False

    def test_legacy_sha256_pin_verifies_and_upgrades(self, tmp_path):
        config = _config(tmp_path)
        legacy = hashlib.sha256(b'246810').hexdigest()
        config.data['pin_hash'] = legacy
        config.save()

        assert config.verify_pin('246810') is True

        stored = json.loads(config.config_path.read_text(encoding='utf-8'))['pin_hash']
        assert stored.startswith('pbkdf2$')
        assert stored != legacy

        reloaded = _config(tmp_path)
        reloaded.load()
        assert reloaded.verify_pin('246810') is True

    def test_wrong_pin_rejected_and_legacy_left_unupgraded(self, tmp_path):
        config = _config(tmp_path)
        legacy = hashlib.sha256(b'246810').hexdigest()
        config.data['pin_hash'] = legacy
        config.save()

        assert config.verify_pin('111111') is False
        assert config.pin_hash == legacy

    @pytest.mark.parametrize('junk', [
        '',
        'garbage',
        'deadbeef',
        'pbkdf2$$ab$cd',
        'pbkdf2$notanint$ab$cd',
        'pbkdf2$100000$zz$cd',
        'pbkdf2$100000$abcd$not-hex',
        'héllo-non-ascii',
    ])
    def test_corrupt_stored_hash_fails_safe(self, tmp_path, junk):
        config = _config(tmp_path)
        config.data['pin_hash'] = junk
        assert config.verify_pin('123456') is False

    def test_absent_pin_still_verifies_true(self, tmp_path):
        config = _config(tmp_path)
        assert config.pin_hash is None
        assert config.verify_pin('999999') is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
