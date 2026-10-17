"""Offline progress queue: record locally, flush against the fake server.

Covers record/queueing, finish/unfinish mapping, the client updated_at,
Unreachable stopping the pass, 4xx dropping the row, attempts bumping,
server-newer LWW, pull/pullAll, and the > 60 s open prompt.
"""

from xx_audiobook import api as api_module
from xx_audiobook import store as store_module
from xx_audiobook.api import ApiError, AudiobookApi, ProgressPut
from xx_audiobook.repos import ProgressRepository, decide_progress


def _repo(server, now=None):
    db = store_module.Database.in_memory()
    client = AudiobookApi(server.api_base, "tok")
    moment = now if now is not None else [1790000000000]
    repo = ProgressRepository(client, db, now_ms=lambda: moment[0])
    return db, repo, moment


def test_record_queues_and_flush_sends_client_clock(fake_server):
    db, repo, moment = _repo(fake_server)
    try:
        repo.record("it-9", 120.0, 7200.0, speed=1.5)
        assert db.progress().get("it-9").position_s == 120.0
        assert repo.pending_count() == 1
        result = repo.flush()
        assert result.sent == 1
        assert result.remaining == 0
        assert result.offline is False
        _method, _path, _headers, body, _query = fake_server.last_call(
            "PUT", "/audiobook/api/me/progress/it-9")
        import json
        sent = json.loads(body)
        assert sent["position_s"] == 120.0
        assert sent["updated_at"] == moment[0] / 1000.0
        assert sent["speed"] == 1.5
    finally:
        db.close()


def test_flush_maps_finish_and_unfinish(fake_server):
    db, repo, _moment = _repo(fake_server)
    try:
        repo.set_finished("it-1", True)
        assert repo.flush().sent == 1
        assert fake_server.last_call(
            "POST", "/audiobook/api/me/progress/it-1/finish") is not None
        repo.set_finished("it-1", False)
        assert repo.flush().sent == 1
        assert fake_server.last_call(
            "POST", "/audiobook/api/me/progress/it-1/unfinish") is not None
        assert fake_server.last_call(
            "PUT", "/audiobook/api/me/progress/it-1") is not None
    finally:
        db.close()


def test_offline_flush_stops_and_keeps_queue(fake_server):
    db, repo, _moment = _repo(fake_server)
    try:
        repo.record("it-9", 10.0, None)
        port = fake_server.port
        fake_server.stop()
        repo._api = AudiobookApi(
            f"http://127.0.0.1:{port}/audiobook/", "tok")
        result = repo.flush()
        assert result.offline is True
        assert result.remaining == 1
        assert result.sent == 0
    finally:
        db.close()


def test_gone_book_drops_row_other_errors_bump(fake_server):
    db = store_module.Database.in_memory()
    try:
        gone = AudiobookApi(fake_server.api_base, "tok",
                            transport=_script_transport([
                                (404, '{"detail":"gone"}')]))
        repo = ProgressRepository(gone, db,
                                  now_ms=lambda: 1790000000000)
        repo.record("ghost", 1.0, None)
        assert repo.flush().remaining == 0

        flaky = AudiobookApi(fake_server.api_base, "tok",
                             transport=_script_transport([
                                 (500, '{"detail":"boom"}')]))
        repo2 = ProgressRepository(flaky, db,
                                   now_ms=lambda: 1790000001000)
        repo2.record("ghost2", 1.0, None)
        assert repo2.flush().remaining == 1
        assert db.pending().get("ghost2").attempts == 1
    finally:
        db.close()


def _script_transport(script):
    def transport(method, url, headers, body, timeout):
        return script.pop(0)
    return transport


def test_server_newer_row_wins_locally(fake_server):
    db, repo, _moment = _repo(fake_server)
    try:
        repo.record("it-1", 120.0, 7200.0, speed=1.5)
        result = repo.flush()
        assert result.sent == 1
        assert db.progress().get("it-1").position_s == 4100.0
        pulled, error = repo.pull("it-1")
        assert error is None
        assert pulled.position_s == 4100.0
        count, error = repo.pull_all()
        assert error is None
        assert count == 1
    finally:
        db.close()


def test_queued_finish_survives_later_ticks(fake_server):
    db, repo, _moment = _repo(fake_server)
    try:
        repo.record("it-1", 100.0, 7200.0)
        repo.set_finished("it-1", True)
        repo.record("it-1", 130.0, None)
        assert db.pending().get("it-1").is_finished is True
    finally:
        db.close()


def test_decide_lww_when_close_prompt_when_apart():
    def local(pos, updated_ms, finished=False):
        return store_module.Progress("it-1", pos, 7200.0, finished,
                                     updated_ms)

    def remote(pos, updated_s, finished=False):
        return api_module.Progress("it-1", pos, 7200.0, is_finished=finished,
                                   updated_at=updated_s)

    assert decide_progress(
        local(100.0, 1000), remote(130.0, 2.0))[0] == "take_server"
    assert decide_progress(
        local(100.0, 5000), remote(130.0, 2.0))[0] == "keep_local"
    assert decide_progress(
        local(100.0, 1000), remote(0.0, None))[0] == "keep_local"
    assert decide_progress(None, remote(10.0, 1.0))[0] == "take_server"
    decision, conflict = decide_progress(local(100.0, 1000),
                                         remote(4000.0, 9.0))
    assert decision == "prompt"
    assert conflict.local_s == 100.0
    assert conflict.server_s == 4000.0
    assert conflict.server_is_newer is True
    assert decide_progress(local(100.0, 1000),
                           remote(7200.0, 9.0,
                                  finished=True))[0] == "take_server"


def test_compare_on_open_prompts_and_resolve_is_newest(fake_server):
    db, repo, moment = _repo(fake_server)
    try:
        db.progress().upsert(
            store_module.Progress("it-1", 100.0, 7200.0, False,
                                  moment[0] - 10000000))
        conflict = repo.compare_on_open("it-1")
        assert conflict is not None
        assert conflict.server_s == 4100.0
        repo.resolve(conflict, use_server=True)
        assert db.progress().get("it-1").position_s == 4100.0
        assert db.progress().get("it-1").updated_at_ms == moment[0]
        assert repo.pending_count() == 1
    finally:
        db.close()


def test_compare_on_open_offline_keeps_local(fake_server):
    db = store_module.Database.in_memory()
    try:
        offline = AudiobookApi(fake_server.api_base, "tok",
                               transport=_unreachable_transport)
        repo = ProgressRepository(offline, db,
                                  now_ms=lambda: 1790000000000)
        db.progress().upsert(
            store_module.Progress("it-1", 100.0, 7200.0, False,
                                  1790000000000))
        assert repo.compare_on_open("it-1") is None
    finally:
        db.close()


def _unreachable_transport(method, url, headers, body, timeout):
    raise ApiError.Unreachable("off")


def test_speed_clamps_and_inserts(fake_server):
    db, repo, _moment = _repo(fake_server)
    try:
        repo.record("it-1", 10.0, None)
        repo.set_speed("it-1", 5.0)
        assert repo.speed_for("it-1") == 3.0
        repo.set_speed("never-played", 1.25)
        assert repo.speed_for("never-played") == 1.25
    finally:
        db.close()


def test_mark_synced_acks_queue(fake_server):
    db, repo, _moment = _repo(fake_server)
    try:
        stamp = repo.record("it-1", 61.0, 7200.0)
        repo.mark_synced("it-1", stamp)
        assert repo.pending_count() == 0
    finally:
        db.close()


def test_log_event_writes_history(fake_server):
    db, repo, _moment = _repo(fake_server)
    try:
        repo.log_event("it-1", "pause", 61.0, 1.25)
        assert db.history().last("it-1").event == "pause"
    finally:
        db.close()


def test_lww_fake_server_rejects_stale_write(fake_server):
    client = AudiobookApi(fake_server.api_base, "tok")
    fresh = client.put_progress(
        "it-1", ProgressPut(4200.0, updated_at=1790003700.0))
    assert fresh.applied is True
    stale = client.put_progress(
        "it-1", ProgressPut(10.0, updated_at=1790000000.0))
    assert stale.applied is False
    assert stale.position_s == 4200.0
