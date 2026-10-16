"""Importer tests: policy apply/export, call-log dedupe, blocklist."""
from xx_dialer.codecs import decode_policy, encode_policy
from xx_dialer.importers import (
    apply_policy,
    default_config,
    export_policy,
    import_blocklist_text,
    import_calllog_text,
    import_policy_text,
)

POLICY_JSON = ('{"schema":1,"enforcementMode":"enforcing",'
               '"unknownWindow":{"startMinute":480,"endMinute":1080,'
               '"daysMask":31},'
               '"businessWindow":{"startMinute":540,"endMinute":1140,'
               '"daysMask":127},'
               '"hiddenCallerPolicy":"SILENCE","stirAction":"OFF",'
               '"repeatCallerEnabled":false,"ringRepeat":"twice",'
               '"silencedNotifPolicy":"daily","answerInteraction":"tap",'
               '"groupRecentsEnabled":false,'
               '"tiers":[{"lookupKey":"abc/","tier":"biz","addedAt":5}],'
               '"patterns":[{"e164Prefix":"1425555","wildcards":4,'
               '"action":"block","preset":null,"createdAt":6}]}')

CALLLOG_JSON = ('{"schema":1,"entries":['
                '{"number":"+14155550100","type":2,"date":1700000000000,'
                '"duration":30,"cachedName":"Ada"},'
                '{"number":null,"type":3,"date":1700000100000,'
                '"duration":0,"cachedName":null}]}')


def test_policy_text_applies_windows_policies_and_rows(store):
    app_config = default_config()
    report = import_policy_text(POLICY_JSON, app_config, store)
    assert report is not None
    assert app_config["unknown_window"] == {"start": 480, "end": 1080,
                                            "days": 31}
    assert app_config["hidden_caller_policy"] == "silence"
    assert app_config["stir_action"] == "off"
    assert app_config["repeat_caller_enabled"] is False
    assert app_config["group_recents"] is False
    assert app_config["ring_repeat"] == "twice"
    assert app_config["silenced_notif_policy"] == "daily"
    assert app_config["enforcement_mode"] == "observing"
    assert report.patterns_added == 1 and report.tiers_added == 1
    assert store.keys_for_tier("biz") == ["abc/"]


def test_policy_apply_replaces_wholesale_and_keeps_valid_config(store):
    app_config = default_config()
    import_policy_text(POLICY_JSON, app_config, store)
    second = ('{"schema":1,"tiers":[],"patterns":[],'
              '"unknownWindow":{"startMinute":99999,"endMinute":5,'
              '"daysMask":0}}')
    import_policy_text(second, app_config, store)
    assert store.all_patterns() == [] and store.all_tiers() == []
    assert app_config["unknown_window"] == {"start": 480, "end": 1080,
                                            "days": 31}


def test_policy_export_survives_an_android_round_trip(store):
    app_config = default_config()
    import_policy_text(POLICY_JSON, app_config, store)
    snapshot = export_policy(app_config, store)
    assert decode_policy(encode_policy(snapshot)) == snapshot


def test_calllog_import_dedupes_on_number_and_date(store):
    first = import_calllog_text(CALLLOG_JSON, store)
    assert (first.inserted, first.duplicates) == (2, 0)
    second = import_calllog_text(CALLLOG_JSON, store)
    assert (second.inserted, second.duplicates) == (0, 2)
    assert import_calllog_text("{garbage", store).inserted == 0


def test_blocklist_text_lands_in_blocked_numbers(store):
    parsed = import_blocklist_text("+14155550100\n+14155550100\njunk\n",
                                   store)
    assert parsed.numbers == ["+14155550100", "+14155550100"]
    assert parsed.skipped == 1
    assert store.all_blocked() == ["+14155550100"]


def test_apply_policy_with_unknown_answer_interaction_keeps_current(store):
    app_config = default_config()
    snapshot = decode_policy(POLICY_JSON)
    assert snapshot is not None
    snapshot.answer_interaction = "swipe"
    apply_policy(snapshot, app_config, store)
    assert app_config["answer_interaction"] == "tap"
