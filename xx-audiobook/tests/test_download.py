"""M4 downloads + offline: resume, checksum, gating, park, backup."""

import json
import urllib.request

from xx_audiobook import api as api_module
from xx_audiobook import download as download_module
from xx_audiobook import repos as repos_module
from xx_audiobook import store as store_module


def _repo(server, tmp_path, scheduler=None, free_bytes=None):
    db = store_module.Database.in_memory()
    files = download_module.BookFiles(tmp_path / "data")
    api = api_module.AudiobookApi(server.api_base, "tok")
    sched = scheduler or download_module.RecordingScheduler()
    repo = download_module.DownloadRepository(
        api, db, files, sched, free_bytes=free_bytes)
    downloader = download_module.BookDownloader(api, db, files)
    return db, files, api, repo, downloader


def test_enqueue_records_manifest_and_schedules(fake_server, tmp_path):
    db, _files, _api, repo, _down = _repo(fake_server, tmp_path)
    try:
        sched = repo._scheduler
        ok, error = repo.enqueue("it-1", "The Long Way", True)
        assert (ok, error) == (True, None)
        row = db.downloads().get("it-1")
        assert row.state == "Queued"
        assert row.bytes_total == 4096 + 8192
        files = db.downloads().files("it-1")
        assert [f.file_id for f in files] == ["f-a", "f-b"]
        assert all(f.sha256 for f in files)
        assert sched.scheduled == [("it-1", True)]
    finally:
        db.close()


def test_download_completes_and_caches_offline(fake_server, tmp_path):
    db, files, _api, repo, downloader = _repo(fake_server, tmp_path)
    try:
        assert repo.enqueue("it-1", "The Long Way", True)[0] is True
        outcome = downloader.download("it-1")
        assert isinstance(outcome, download_module.Done)
        assert repo.is_complete("it-1") is True
        for fid, size in (("f-a", 4096), ("f-b", 8192)):
            local = repo.local_file("it-1", fid)
            assert local is not None
            with open(local, "rb") as handle:
                assert len(handle.read()) == size
        row = db.downloads().get("it-1")
        assert row.state == "Done"
        assert row.bytes_done == 4096 + 8192
        assert db.details().get("it-1") is not None
        assert files.cover_file("it-1").is_file()
        assert files.cover_file("it-1").stat().st_size > 0
    finally:
        db.close()


def test_resume_after_dropped_connection(fake_server, tmp_path):
    db, files, _api, repo, downloader = _repo(fake_server, tmp_path)
    try:
        assert repo.enqueue("it-1", "The Long Way", True)[0] is True
        raw = urllib.request.urlopen(
            fake_server.api_base + "api/items/it-1/files/f-a",
            timeout=10).read()
        assert len(raw) == 4096
        files.ensure_dirs("it-1")
        part = files.track_file("it-1", 0, "f-a")
        part = part.with_name(part.name + ".part")
        part.write_bytes(raw[:1024])
        outcome = downloader.download("it-1")
        assert isinstance(outcome, download_module.Done)
        resumed = [c for c in fake_server.calls
                   if c[0] == "GET"
                   and c[1] == "/audiobook/api/items/it-1/files/f-a"
                   and c[2].get("range", "").startswith("bytes=1024-")]
        assert resumed, "expected a Range resume at byte 1024"
        assert repo.is_complete("it-1") is True
    finally:
        db.close()


def test_checksum_mismatch_keeps_nothing(fake_server, tmp_path):
    db, files, _api, repo, downloader = _repo(fake_server, tmp_path)
    try:
        assert repo.enqueue("it-1", "The Long Way", True)[0] is True
        rows = db.downloads().files("it-1")
        bad = [store_module.DownloadFile(
            item_id=r.item_id, file_id=r.file_id, idx=r.idx, size=r.size,
            sha256=("0" * 64), mime=r.mime) for r in rows]
        db.downloads().upsert_files(bad)
        outcome = downloader.download("it-1")
        assert isinstance(outcome, download_module.Failed)
        row = db.downloads().get("it-1")
        assert row.state == "Failed"
        assert "Checksum" in (row.error or "")
        for r in db.downloads().files("it-1"):
            assert r.verified is False
            assert r.local_path is None
            assert repo.local_file("it-1", r.file_id) is None
        assert repo.is_complete("it-1") is False
        book_dir = files.book_dir("it-1")
        leftovers = ([p for p in book_dir.iterdir()]
                     if book_dir.is_dir() else [])
        assert leftovers == [], f"unverified bytes kept: {leftovers}"
    finally:
        db.close()


def test_416_verifies_what_we_have(fake_server, tmp_path):
    db, files, _api, repo, downloader = _repo(fake_server, tmp_path)
    try:
        assert repo.enqueue("it-1", "The Long Way", True)[0] is True
        raw = urllib.request.urlopen(
            fake_server.api_base + "api/items/it-1/files/f-a",
            timeout=10).read()
        files.ensure_dirs("it-1")
        target = files.track_file("it-1", 0, "f-a")
        part = target.with_name(target.name + ".part")
        part.write_bytes(raw)

        def _raise_416(url, headers):
            raise download_module._HttpStatus(416)

        downloader._range_get = _raise_416
        row = [r for r in db.downloads().files("it-1")
               if r.file_id == "f-a"][0]
        outcome = downloader._fetch(row, lambda _have: None)
        assert isinstance(outcome, download_module.Done)
        assert target.is_file() and target.stat().st_size == 4096
    finally:
        db.close()


def test_metered_blocked_but_allowed_when_not_wifi_only(
        fake_server, tmp_path):
    db, _files, api, repo, _down = _repo(fake_server, tmp_path)
    try:
        assert repo.enqueue("it-1", "The Long Way", True)[0] is True
        outcome = download_module.run_one(
            "it-1", api, db, repo._files, is_signed_in=lambda: True,
            network=download_module.NetState.Metered, wifi_only=True)
        assert isinstance(outcome, download_module.Retry)
        assert outcome.message == download_module.WAITING_FOR_WIFI
        assert db.downloads().get("it-1").state == "Queued"
        assert repo.is_complete("it-1") is False
        outcome = download_module.run_one(
            "it-1", api, db, repo._files, is_signed_in=lambda: True,
            network=download_module.NetState.Metered, wifi_only=False)
        assert isinstance(outcome, download_module.Done)
        assert repo.is_complete("it-1") is True
    finally:
        db.close()


def test_offline_parks_as_queued_retry(fake_server, tmp_path):
    db, _files, api, repo, _down = _repo(fake_server, tmp_path)
    try:
        assert repo.enqueue("it-1", "The Long Way", True)[0] is True
        outcome = download_module.run_one(
            "it-1", api, db, repo._files, is_signed_in=lambda: True,
            network=download_module.NetState.Offline, wifi_only=False)
        assert isinstance(outcome, download_module.Retry)
        assert outcome.message == download_module.OFFLINE_MESSAGE
    finally:
        db.close()


def test_storage_low_refuses_new_download(fake_server, tmp_path):
    sched = download_module.RecordingScheduler()
    db, _files, _api, repo, _down = _repo(
        fake_server, tmp_path, scheduler=sched, free_bytes=0)
    try:
        ok, error = repo.enqueue("it-1", "The Long Way", True)
        assert ok is False
        assert error == download_module.STORAGE_LOW_MESSAGE
        assert sched.scheduled == []
        row = db.downloads().get("it-1")
        assert row is not None and row.state == "Failed"
    finally:
        db.close()


def test_signed_out_parks_and_resumes_on_sign_in(fake_server, tmp_path):
    db, _files, api, repo, _down = _repo(fake_server, tmp_path)
    try:
        assert repo.enqueue("it-1", "The Long Way", True)[0] is True
        outcome = download_module.run_one(
            "it-1", api, db, repo._files, is_signed_in=lambda: False,
            network=download_module.NetState.Online, wifi_only=True)
        assert isinstance(outcome, download_module.Failed)
        assert outcome.message == download_module.SIGNED_OUT
        row = db.downloads().get("it-1")
        assert row.state == "Failed"
        assert row.error == download_module.SIGNED_OUT
        assert repo.resume_signed_out(True) == 1
        assert db.downloads().get("it-1").state == "Queued"
    finally:
        db.close()


def test_offline_play_gate_prefers_verified_file_urls(
        fake_server, tmp_path):
    from xx_audiobook.player import FakeBackend
    from xx_audiobook.service.player_service import PlayerService

    db, _files, api, repo, downloader = _repo(fake_server, tmp_path)
    service = None
    try:
        assert repo.enqueue("it-1", "The Long Way", True)[0] is True
        assert isinstance(downloader.download("it-1"),
                          download_module.Done)
        dead = api_module.AudiobookApi("http://127.0.0.1:9/audiobook/",
                                       "tok")
        service = PlayerService(db, dead, FakeBackend(),
                                local_file=repo.local_file)
        book, _resume, _rate = service.open_book_sync("it-1")
        assert book.session_id is None
        assert all(t.uri.startswith("file://")
                   for t in book.timeline.tracks)
        service.finish_open((book, 0, 1.0))
        assert service.engine._backend.headers[0] == {}
        assert download_module.DownloadRepository(
            dead, db, repo._files).is_complete("it-1") is True
    finally:
        if service is not None:
            service._queue.join()
        db.close()


def test_history_export_import_round_trips_lww(tmp_path):
    del tmp_path
    db = store_module.Database.in_memory()
    try:
        now = 1790003600000
        db.progress().upsert(store_module.Progress(
            "it-1", 4100.0, 7200.0, False, now, 1.0))
        db.history().insert(store_module.HistoryEvent(
            "it-1", "pause", 4100.0, 1.0, now + 5))
        text = download_module.export_history(db)
        doc = json.loads(text)
        assert doc["schema"] == 1
        assert doc["entries"][0]["item_id"] == "it-1"
        fresh = store_module.Database.in_memory()
        try:
            assert download_module.import_history(fresh, text) == 1
            assert fresh.progress().get("it-1").position_s == 4100.0
            assert download_module.import_history(fresh, text) == 1
            fresh.progress().upsert(store_module.Progress(
                "it-1", 100.0, 7200.0, False, now + 9999, 1.0))
            assert download_module.import_history(fresh, text) == 0
            assert fresh.progress().get("it-1").position_s == 100.0
            assert download_module.import_history(
                fresh, '{"schema": 99, "entries": []}') == 0
            assert download_module.import_history(fresh, "garbage") == 0
        finally:
            fresh.close()
    finally:
        db.close()


def test_systemd_units_ship_with_safe_exec():
    from pathlib import Path

    root = Path(__file__).parent.parent
    template = root / "data/systemd/xx-audiobook-download@.service.in"
    sync_service = root / "data/systemd/xx-audiobook-sync.service.in"
    timer = root / "data/systemd/xx-audiobook-sync.timer"
    launcher = root / "data/xx-audiobook-download.in"
    assert template.is_file() and sync_service.is_file()
    assert timer.is_file() and launcher.is_file()
    assert "xx-audiobook-download %i" in template.read_text()
    assert "--retry-queued" in sync_service.read_text()
    assert "OnUnitActiveSec=15min" in timer.read_text()


def test_download_center_state_api(fake_server, tmp_path):
    db, files, api, repo, _down = _repo(fake_server, tmp_path)
    try:
        center = download_module.DownloadsState(api, db, files=files,
                                                scheduler=repo._scheduler)
        assert center.wifi_only() is True
        center.set_wifi_only(False)
        assert center.wifi_only() is False
        ok, _error = center.enqueue("it-1", "The Long Way")
        assert ok is True
        status = center.status("it-1")
        assert status["state"] == "Queued"
        assert status["is_complete"] is False
        assert center.status("nope")["state"] == "Missing"
        used = center.storage_used_bytes()
        assert used >= 0
        assert set(center.storage_state()) == {
            "used_bytes", "free_bytes", "low"}
        center.set_wifi_only(True)
        center._network.on_facts(download_module.Facts(True, True, False))
        assert center.block_reason() == download_module.WAITING_FOR_WIFI
        assert repos_module.PROMPT_THRESHOLD_S == 60.0
    finally:
        db.close()


def test_safe_id_cannot_escape_its_folder():
    from xx_audiobook.download import safe_id
    for bad in ("", ".", "..", "../etc", "/abs", ".hidden"):
        out = safe_id(bad)
        assert out and "/" not in out and not out.startswith(".")
    assert safe_id("book-42_v1.2") == "book-42_v1.2"
