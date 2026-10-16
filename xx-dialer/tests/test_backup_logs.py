"""Lane 6: backup export/import controller + redacted logs view logic."""
import json

from xx_dialer import applog, codecs, importers
from xx_dialer.store import CallRow, Store
from xx_dialer.ui_backup import BackupController, BackupDeps
from xx_dialer.ui_logs import LogsController, LogsDeps

ADA = "+14155550100"
BO = "+14155550200"


def _controller(tmp_path):
    notes: list[str] = []
    return BackupController(BackupDeps(db_file=tmp_path / "calls.db",
                                       home=tmp_path,
                                       notice=notes.append)), notes


def test_redact_masks_long_runs_only():
    assert applog.redact("call from %s" % ADA) == "call from +*********00"
    assert applog.redact("3 silenced calls") == "3 silenced calls"
    assert applog.redact("2026-10-16 id 42") == "2026-10-16 id 42"
    assert applog.redact("") == ""
    assert applog.redact("1234567") == "*****67"
    assert applog.redact("123456") == "123456"


def test_log_write_list_read_redacts(tmp_path):
    applog.write("cards", "posted id=41 Silenced call %s" % ADA, tmp_path)
    files = applog.list_logs(tmp_path)
    assert [name for name, _size, _mtime in files] == ["xx-dialer.log"]
    text = applog.read_log("xx-dialer.log", tmp_path)
    assert ADA not in text
    assert "*********00" in text
    assert "posted id=41" in text


def test_log_rotation_caps_files(tmp_path):
    for index in range(3000):
        applog.write("t", "line %d %s" % (index, ADA), tmp_path)
    names = sorted(name for name, _s, _m in applog.list_logs(tmp_path))
    assert names[0] == "xx-dialer.log"
    assert len(names) <= 4
    assert applog.read_log(names[0], tmp_path).find(ADA) == -1


def test_log_helpers_never_raise(tmp_path):
    assert applog.list_logs(tmp_path / "missing") == []
    assert applog.read_log("xx-dialer.log", tmp_path / "missing") == ""
    assert applog.read_log("../escape", tmp_path) == ""
    applog.clear_logs(tmp_path / "missing")
    applog.write("t", "x", tmp_path / "missing" / "deep")


def test_export_calllog_round_trip(tmp_path):
    with Store(tmp_path / "calls.db") as store:
        store.add_call(CallRow(date=1700000000000, number=ADA,
                               call_type=1, duration=30,
                               cached_name="Ada"))
        store.add_call(CallRow(date=1700000001000, number=BO,
                               call_type=3, duration=0))
        text = importers.export_calllog(store)
    env = json.loads(text)
    assert env["schema"] == 1
    assert [(e["number"], e["type"], e["date"])
            for e in env["entries"]] == [
                (BO, 3, 1700000001000), (ADA, 1, 1700000000000)]
    with Store(tmp_path / "other.db") as fresh:
        report = importers.import_calllog_text(text, fresh)
        assert (report.inserted, report.duplicates) == (2, 0)
        assert importers.import_calllog_text(text, fresh).duplicates == 2


def test_backup_policy_export_import_stays_observing(tmp_path):
    controller, notes = _controller(tmp_path)
    text = controller.export_policy_text()
    assert json.loads(text)["schema"] == 1
    crafted = json.loads(text)
    crafted["enforcementMode"] = "enforcing"
    status = controller.import_policy_text(json.dumps(crafted))
    assert "observe mode" in status
    assert notes and "observe mode" in notes[-1]
    from xx_dialer import config as app_config
    assert app_config.load(tmp_path)["enforcement_mode"] == "observing"


def test_backup_import_rejects_garbage(tmp_path):
    controller, _notes = _controller(tmp_path)
    assert "Could not read" in controller.import_policy_text("{ nope")
    future = json.loads(codecs.encode_policy(codecs.RingPolicySnapshot()))
    future["schema"] = 99
    assert "Could not read" in controller.import_policy_text(
        json.dumps(future))


def test_backup_counts_and_calllog_import(tmp_path):
    controller, notes = _controller(tmp_path)
    assert controller.counts() == (0, 0, 0)
    entries = codecs.encode_calllog(
        [codecs.CallLogEntry(number=ADA, call_type=1, date=1700000000000,
                             duration=5)])
    assert controller.import_calllog_text(entries) == \
        "Imported 1 calls (0 already here)."
    assert controller.counts() == (0, 0, 1)
    assert "Could not read" in controller.import_calllog_text("{ nope")


def test_logs_controller_redacts_and_clears(tmp_path):
    controller = LogsController(LogsDeps(db_file=tmp_path / "calls.db",
                                         home=tmp_path))
    assert controller.files() == []
    assert controller.preview("xx-dialer.log") == "No log lines yet."
    applog.write("cards", "action id=7 call-back %s" % ADA, tmp_path)
    (name, _size, _mtime), = controller.files()
    assert name == "xx-dialer.log"
    preview = controller.preview(name)
    assert ADA not in preview and "*******00" in preview
    assert controller.clear() == "Cleared the logs."
    assert controller.files() == []
