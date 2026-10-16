import json

import pytest

from txxt import backup, blocking
from txxt.backup import BackupError
from txxt.phone_numbers import matches, normalize
from txxt.settings import Settings, load, save


def test_settings_defaults_match_android_posture():
    s = Settings.defaults()
    assert s.lock_screen_privacy == 'sender-only'
    assert s.alert_style == 'sound'
    assert s.auto_sync_theme is True


def test_settings_round_trip(tmp_path):
    path = tmp_path / 'config.json'
    save(Settings(lock_screen_privacy='nothing', theme_preset='paper'), path)
    assert load(path).lock_screen_privacy == 'nothing'
    assert load(path).theme_preset == 'paper'


def test_settings_missing_file_gives_defaults(tmp_path):
    assert load(tmp_path / 'absent.json') == Settings.defaults()


def test_settings_invalid_file_gives_defaults(tmp_path):
    bad = tmp_path / 'config.json'
    bad.write_text('{broken', encoding='utf-8')
    assert load(bad) == Settings.defaults()


def test_settings_ignores_unknown_keys_and_bad_values(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({
        'lock_screen_privacy': 'everyone',
        'alert_style': 'loud',
        'future_key': 1,
    }), encoding='utf-8')
    loaded = load(path)
    assert loaded.lock_screen_privacy == 'sender-only'
    assert loaded.alert_style == 'sound'


def test_backup_round_trip():
    data = {
        'conversations': [{'id': 1, 'addresses': '+15551234'}],
        'messages': [{'id': 2, 'thread_id': 1, 'date': 100, 'body': 'hi',
                      'address': '+15551234'}],
        'settings': {'theme_preset': 'forest'},
        'blocklist': {'blocked': []},
    }
    assert backup.deserialize(backup.serialize(data))['messages'] == data['messages']


def test_backup_rejects_wrong_version():
    with pytest.raises(BackupError):
        backup.deserialize('{"version": 99, "conversations": [], "messages": []}')


def test_backup_rejects_negative_ids_and_dates():
    bad = json.dumps({'version': 1, 'conversations': [],
                      'messages': [{'id': -1, 'thread_id': 1, 'date': 5, 'body': 'x'}]})
    with pytest.raises(BackupError):
        backup.deserialize(bad)


def test_backup_rejects_malformed_json():
    with pytest.raises(BackupError):
        backup.deserialize('{nope')


def test_backup_deduplicate_skips_second_import():
    msgs = [{'address': '+15551234', 'date': 10, 'body': 'hi'}]
    known: set = set()
    assert len(backup.deduplicate(msgs, known)) == 1
    assert backup.deduplicate(msgs, known) == []


def test_starred_bypasses_block_and_quarantine():
    book = blocking.InboundFilter(
        blocked_addresses={'+1 555-1234'},
        starred_contacts={'+15551234'},
        quarantine_unknown_senders=True,
    )
    disposition, _ = book.evaluate('+15551234', 'spam here')
    assert disposition is blocking.MessageDisposition.DELIVER


def test_blocked_sender_and_keyword_hit():
    book = blocking.InboundFilter(
        blocked_addresses={'+15550000'},
        content_keywords={'lottery'},
        quarantine_unknown_senders=True,
    )
    assert book.evaluate('+15550000', 'hi')[0] is blocking.MessageDisposition.BLOCK
    assert book.evaluate('+15551111', 'you won the LOTTERY')[0] is blocking.MessageDisposition.BLOCK
    assert book.evaluate('+15551111', 'dinner?')[0] is blocking.MessageDisposition.QUARANTINE
    assert blocking.InboundFilter(
        content_keywords={'lottery'}).evaluate('+15551111', 'dinner?')[0] is \
        blocking.MessageDisposition.DELIVER


def test_phone_normalize_and_matches():
    assert normalize('+1 (555) 123-4567') == '15551234567'
    assert matches('+15551234567', '5551234567')
    assert not matches('+15551234567', '+15557654321')
