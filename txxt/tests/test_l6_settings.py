"""L6 settings/blocking/quarantine rules tests: store round-trip, rule sync, dialer import."""

import json

import pytest

from txxt import blocking
from txxt.blocking import MessageDisposition
from txxt.settings import Settings, load, save
from txxt.store import Store


@pytest.fixture(autouse=True)
def _reset_live_filter():
    blocking.live_reset_for_test()
    yield
    blocking.live_reset_for_test()


class TestSettingsStore:
    def test_mms_auto_download_defaults_off(self):
        assert Settings.defaults().mms_auto_download is False

    def test_invalid_values_keep_defaults(self):
        loaded = Settings.from_dict({
            'lock_screen_privacy': 'billboard',
            'alert_style': 'loud',
            'mms_auto_download': 'yes',
            'default_region': '',
        })
        assert loaded.lock_screen_privacy == 'sender-only'
        assert loaded.alert_style == 'sound'
        assert loaded.mms_auto_download is False
        assert loaded.default_region == 'US'

    def test_region_uppercased(self):
        assert Settings.from_dict({'default_region': 'de'}).default_region == 'DE'

    def test_round_trip_through_file(self, tmp_path):
        path = tmp_path / 'config.json'
        original = Settings(
            lock_screen_privacy='content', alert_style='vibrate',
            auto_sync_theme=False, theme_preset='forest',
            font_mode='jetbrains-mono', default_region='DE',
            mms_auto_download=True, mms_auto_reply=True,
        )
        save(original, path)
        assert load(path) == original

    def test_missing_file_gives_defaults(self, tmp_path):
        assert load(tmp_path / 'absent.json') == Settings.defaults()


class TestLiveApplyFromStore:
    def test_blocked_sender_blocks(self):
        store = Store.memory()
        store.blocklist_add('sender', '+15550001111')
        blocking.live_apply_from_store(store)
        disposition, _reason = blocking.live_current().evaluate('+15550001111', 'hi')
        assert disposition is MessageDisposition.BLOCK

    def test_keyword_and_phrase_block(self):
        store = Store.memory()
        store.blocklist_add('keyword', 'loan')
        store.blocklist_add('phrase', 'act now')
        blocking.live_apply_from_store(store)
        current = blocking.live_current()
        assert current.evaluate('+1555', 'a loan offer')[0] is MessageDisposition.BLOCK
        assert current.evaluate('+1555', 'please act now today')[0] is MessageDisposition.BLOCK
        assert current.evaluate('+1555', 'loans are fine')[
            0] is MessageDisposition.DELIVER

    def test_quarantine_hold_defaults_off(self):
        store = Store.memory()
        blocking.live_apply_from_store(store)
        disposition, _reason = blocking.live_current().evaluate('+1999', 'hello?')
        assert disposition is MessageDisposition.DELIVER

    def test_quarantine_hold_when_enabled(self):
        store = Store.memory()
        store.set_setting(blocking.QUARANTINE_SETTING_KEY, True)
        blocking.live_apply_from_store(
            store, known_sender_lookup=lambda sender: False,
        )
        current = blocking.live_current()
        assert current.evaluate('+1999', 'hello?')[0] is MessageDisposition.QUARANTINE
        assert current.evaluate('+1999', 'hello?')[1] is not None

    def test_starred_bypasses_store_rules(self):
        store = Store.memory()
        store.blocklist_add('sender', '+15550001111')
        store.star('+15550001111')
        blocking.live_apply_from_store(store)
        disposition, reason = blocking.live_current().evaluate('+15550001111', 'hi')
        assert disposition is MessageDisposition.DELIVER
        assert reason is not None and reason.can_override

    def test_persistent_override_delivers(self):
        store = Store.memory()
        store.set_setting(blocking.QUARANTINE_SETTING_KEY, True)
        blocking.add_persistent_override(store, '+1999')
        blocking.live_apply_from_store(
            store, known_sender_lookup=lambda sender: False,
        )
        disposition, _reason = blocking.live_current().evaluate('+1999', 'hello?')
        assert disposition is MessageDisposition.DELIVER

    def test_quarantine_release_and_block_flow(self):
        store = Store.memory()
        stamp = 1792142432401
        message_id = store.persist_inbound_sms('+19990001111', 'spammy hello', stamp, quarantined=True)
        row = store.get_message(message_id)
        assert store.get_conversation(row.conversation_id).is_quarantined
        store.quarantine_deliver(row.conversation_id)
        assert not store.get_conversation(row.conversation_id).is_quarantined
        sender = store.quarantine_sender(row.conversation_id)
        store.blocklist_add('sender', sender)
        blocking.live_apply_from_store(store)
        assert blocking.live_current().evaluate(sender, 'again')[
            0] is MessageDisposition.BLOCK


class TestDialerImport:
    def test_unreachable_dialer_imports_nothing(self, tmp_path, monkeypatch):
        from txxt.blocking_page import import_dialer_blocks

        monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
        monkeypatch.delenv('TXXT_DIALER_BLOCKS', raising=False)
        assert import_dialer_blocks(Store.memory()) == (0, 0)

    def test_seeded_block_file_imports(self, tmp_path, monkeypatch):
        from txxt.blocking_page import import_dialer_blocks

        blocks = tmp_path / 'blocked.json'
        blocks.write_text(json.dumps(['+15550002222', '+15550003333']), encoding='utf-8')
        monkeypatch.setenv('TXXT_DIALER_BLOCKS', str(blocks))
        store = Store.memory()
        assert import_dialer_blocks(store) == (2, 2)
        blocking.live_apply_from_store(store)
        assert blocking.live_current().evaluate('+15550002222', 'hi')[
            0] is MessageDisposition.BLOCK
        assert import_dialer_blocks(store) == (2, 2)

    def test_invalid_block_file_imports_nothing(self, tmp_path, monkeypatch):
        from txxt.blocking_page import import_dialer_blocks

        blocks = tmp_path / 'blocked.json'
        blocks.write_text('{not json', encoding='utf-8')
        monkeypatch.setenv('TXXT_DIALER_BLOCKS', str(blocks))
        assert import_dialer_blocks(Store.memory()) == (0, 0)
