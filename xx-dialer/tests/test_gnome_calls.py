"""GNOME Calls history importer tests.

Fixtures reproduce the assumed upstream shape (GNOME/calls
src/calls-call-record.c: libgom `CallsCallRecord` with target/inbound/
start/answered/end): inbound + answered stamp imports as incoming,
inbound without one as missed, outbound as outgoing. Missing files,
corrupt databases and unknown schemas import nothing and never raise.
"""
import sqlite3

from xx_dialer.importers import import_gnome_calls

EPOCH_MS = 1700000000000


def make_db(path, ddl, rows):
    remote = sqlite3.connect(str(path))
    remote.execute(ddl)
    for row in rows:
        placeholders = ",".join("?" for _ in row)
        remote.execute("INSERT INTO calls_call_records VALUES (%s)"
                       % placeholders, row)
    remote.commit()
    remote.close()
    return path


DDL = ("CREATE TABLE calls_call_records (id INTEGER PRIMARY KEY,"
       " target TEXT, inbound INTEGER, start TEXT, answered TEXT,"
       " end TEXT, protocol TEXT)")


def test_imports_inbound_outbound_and_missed(tmp_path, store):
    db = make_db(tmp_path / "gnome-calls.db", DDL, [
        (1, "+14155550100", 1, "2023-11-14T22:13:20+00:00",
         "2023-11-14T22:13:30+00:00", "2023-11-14T22:14:20+00:00", "tel"),
        (2, "+14155550200", 0, "2023-11-14T22:15:00+00:00", None,
         "2023-11-14T22:15:40+00:00", "tel"),
        (3, "+14155550300", 1, "2023-11-14T22:16:00+00:00", None, None,
         "tel"),
    ])
    result = import_gnome_calls(store, source=db, only_if_empty=True)
    assert result.imported == 3 and result.skipped == 0
    by_number = {r.number: r for r in
                 store.calls_for("+14155550100") + store.calls_for(
                     "+14155550200") + store.calls_for("+14155550300")}
    assert by_number["+14155550100"].call_type == 1
    assert by_number["+14155550100"].duration == 60
    assert by_number["+14155550200"].call_type == 2
    assert by_number["+14155550200"].duration == 40
    assert by_number["+14155550300"].call_type == 3
    assert by_number["+14155550300"].duration == 0


def test_second_run_dedupes_and_first_run_guard_skips(tmp_path, store):
    db = make_db(tmp_path / "gnome-calls.db", DDL, [
        (1, "+14155550100", 1, "2023-11-14T22:13:20+00:00",
         "2023-11-14T22:13:30+00:00", None, "tel"),
    ])
    assert import_gnome_calls(store, source=db).imported == 1
    rerun = import_gnome_calls(store, source=db, only_if_empty=False)
    assert (rerun.imported, rerun.skipped) == (0, 0)
    guarded = import_gnome_calls(store, source=db, only_if_empty=True)
    assert (guarded.imported, guarded.skipped) == (0, 0)


def test_missing_file_is_silent(store, tmp_path):
    result = import_gnome_calls(store,
                                source=tmp_path / "no-such.db")
    assert (result.imported, result.skipped) == (0, 0)


def test_garbage_file_is_silent(store, tmp_path):
    path = tmp_path / "gnome-calls.db"
    path.write_text("this is not sqlite", encoding="utf-8")
    result = import_gnome_calls(store, source=path)
    assert (result.imported, result.skipped) == (0, 0)


def test_epoch_timestamps_and_unparsable_rows(tmp_path, store):
    db = make_db(tmp_path / "gnome-calls.db", DDL, [
        (1, "+14155550100", 1, EPOCH_MS, EPOCH_MS, EPOCH_MS + 60000,
         "tel"),
        (2, "+14155550200", 0, EPOCH_MS // 1000, None,
         EPOCH_MS // 1000 + 30, "tel"),
        (3, "+14155550300", 1, "not-a-date", None, None, "tel"),
    ])
    result = import_gnome_calls(store, source=db)
    assert (result.imported, result.skipped) == (2, 1)
    rows = {r.number: r for r in store.calls_for("+14155550100")}
    assert rows["+14155550100"].date == EPOCH_MS
    outgoing = {r.number: r for r in store.calls_for("+14155550200")}
    assert outgoing["+14155550200"].date == EPOCH_MS
    assert outgoing["+14155550200"].duration == 30


def test_direction_text_fallback_table(tmp_path, store):
    remote = sqlite3.connect(str(tmp_path / "gnome.db"))
    remote.execute("CREATE TABLE history (number TEXT, direction TEXT,"
                   " date INTEGER)")
    remote.execute("INSERT INTO history VALUES (?, ?, ?)",
                   ("+14155550100", "outgoing", EPOCH_MS))
    remote.execute("INSERT INTO history VALUES (?, ?, ?)",
                   ("+14155550200", "missed", EPOCH_MS + 1))
    remote.commit()
    remote.close()
    result = import_gnome_calls(store, source=tmp_path / "gnome.db")
    assert result.imported == 2
    assert store.calls_for("+14155550100")[0].call_type == 2
    assert store.calls_for("+14155550200")[0].call_type == 3


def test_table_without_identity_columns_imports_nothing(tmp_path, store):
    remote = sqlite3.connect(str(tmp_path / "gnome.db"))
    remote.execute("CREATE TABLE notes (body TEXT)")
    remote.execute("INSERT INTO notes VALUES ('hello')")
    remote.commit()
    remote.close()
    result = import_gnome_calls(store, source=tmp_path / "gnome.db")
    assert (result.imported, result.skipped) == (0, 0)
