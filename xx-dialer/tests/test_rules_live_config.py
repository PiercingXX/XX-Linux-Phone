"""Rules writes reach the running policy service without a restart.

The service reloads config + pattern/block tables on every
``CheckNumber`` (``PolicyService.check_number`` opens a fresh store
handle and ``load``s config per call). These tests prove the Rules
screen's writes land on the very next consult: enforcement flip,
window edit, repeat toggle, a new pattern row, and a blocklist
import.
"""
from datetime import datetime

from xx_dialer import config as app_config
from xx_dialer import importers
from xx_dialer.service import PolicyService
from xx_dialer.store import PatternRow, Store

NIGHT = datetime(2026, 8, 20, 20, 0)
NOON = datetime(2026, 8, 20, 12, 0)


def _enforcing_service(tmp_path):
    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    return PolicyService(db_file=tmp_path / "calls.db", home=tmp_path)


def test_enforcement_flip_applies_to_next_check(tmp_path):
    service = _enforcing_service(tmp_path)
    assert service.check_number("+14155559999", NIGHT)[0] == "silence"
    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "observing"
    app_config.save(settings, tmp_path)
    assert service.check_number("+14155559998", NIGHT)[0] == "ring"
    with Store(service._db_file) as store:
        latest = store.latest_for("+14155559998")
        assert latest is not None and latest.verdict == "Silence"
        assert latest.mode == "observed"


def test_window_edit_applies_to_next_check(tmp_path):
    service = _enforcing_service(tmp_path)
    assert service.check_number("+14155559999", NIGHT)[0] == "silence"
    settings = app_config.load(tmp_path)
    settings["unknown_window"] = {"start": 0, "end": 1439,
                                  "days": 127}
    app_config.save(settings, tmp_path)
    assert service.check_number("+14155559999", NIGHT)[0] == "ring"


def test_repeat_toggle_applies_to_next_check(tmp_path):
    service = _enforcing_service(tmp_path)
    first = service.check_number("+14155550101", NIGHT)
    assert first[0] == "silence"
    second = service.check_number("+14155550101", NIGHT)
    assert second[0] == "ring"
    settings = app_config.load(tmp_path)
    settings["repeat_caller_enabled"] = False
    app_config.save(settings, tmp_path)
    third = service.check_number("+14155550101", NIGHT)
    assert third[0] == "silence"


def test_new_pattern_row_blocks_next_check(tmp_path):
    service = _enforcing_service(tmp_path)
    assert service.check_number("+14255550100", NOON)[0] == "ring"
    with Store(service._db_file) as store:
        store.add_pattern(PatternRow("1425555", 4, "block",
                                     created_at=1))
    verdict, reason = service.check_number("+14255550100", NOON)
    assert verdict == "block"
    assert reason == "blocked by pattern"


def test_blocklist_import_blocks_next_check(tmp_path):
    service = _enforcing_service(tmp_path)
    assert service.check_number("+14155550200", NOON)[0] == "ring"
    with Store(service._db_file) as store:
        importers.import_blocklist_text("+14155550200\n", store)
    verdict, reason = service.check_number("+14155550200", NOON)
    assert verdict == "block"
    assert reason == "blocked"


def test_hidden_policy_edit_applies_to_next_check(tmp_path):
    service = _enforcing_service(tmp_path)
    assert service.check_number("", NOON) == ("ring", "unknown caller")
    settings = app_config.load(tmp_path)
    settings["hidden_caller_policy"] = "block"
    app_config.save(settings, tmp_path)
    assert service.check_number("", NOON)[0] == "block"
