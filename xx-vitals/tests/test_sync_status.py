"""Port of SyncUiModelTest: honest gap lines, login states, queue depth,
batch glyphs, and 401/507 surfacing for the Profile tab."""

import base64
import json
import re

from xx_vitals.sync import auth, status
from xx_vitals.sync.engine import SyncAuthException


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _token(user_id="operator-7"):
    claims = json.dumps({"user_id": user_id, "iat": 1, "exp": 2})
    return f"v1.{_b64('k')}.{_b64(claims)}.{_b64('sig')}"


def _creds(**overrides):
    base = {"nas_host": "http://192.168.1.50:8080", "xxchat_host": "https://chat.example.lan",
            "token": None, "relogin_required": False}
    base.update(overrides)
    return auth.SyncCredentials(**base)


def test_absent_host_renders_gap_never_default():
    assert status.describe_host(None) == "— not configured —"
    assert status.describe_host("   ") == "— not configured —"
    assert status.describe_host("http://nas:8080") == "http://nas:8080"


def test_login_states_render_in_words():
    assert status.describe_login(_creds()) == "not signed in"
    assert status.describe_login(_creds(relogin_required=True, token="t")) == "re-login required"
    assert "signed in · operator-7" in status.describe_login(_creds(token=_token()))
    assert status.describe_login(_creds(token="opaque-token")) == "signed in"


def test_absent_timestamps_render_as_dash():
    assert status.describe_when(None) == "—"
    assert status.describe_when(0) == "—"


def test_timestamps_render_as_local_yyyy_mm_dd_hh_mm():
    rendered = status.describe_when(1785542400000)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", rendered)


def test_pending_depth_counted_verbatim():
    assert status.describe_pending(0) == "0 queued"
    assert status.describe_pending(3) == "3 queued"


def test_auth_exception_surfaces_relogin_line():
    notice = status.notice_for_exception(SyncAuthException())
    assert notice == status.AUTH_NOTICE
    assert "sign in again" in notice


def test_volume_full_surfaces_storage_line_and_keeps_queue():
    assert status.failure_notice("volume_full") == status.VOLUME_NOTICE
    assert "queue kept" in status.VOLUME_NOTICE
    assert status.failure_notice("auth_expired") == status.AUTH_NOTICE
    assert status.failure_notice("transient") is None


def test_batch_glyphs_ok_error_warn_running():
    assert status.batch_from_row({"id": 1, "status": "ok"}).glyph == "✓"
    assert status.batch_from_row({"id": 2, "status": "error"}).glyph == "✗"
    assert status.batch_from_row({"id": 3, "status": "warn"}).glyph == "⚠"
    assert status.batch_from_row({"id": 4, "status": "running"}).glyph == "→"
    assert status.batch_from_row({"id": 5, "status": "future-state"}).glyph == "→"


def _batch_row(**overrides):
    row = {"id": 9, "kind": "manual", "started_at": "2026-10-01T08:00:00+00:00",
           "finished_at": None, "rows_read": 3, "rows_inserted": 3,
           "rows_skipped": 0, "status": "ok", "error": None}
    row.update(overrides)
    return row


def test_batch_line_renders_counts_and_error():
    line = status.batch_line(status.batch_from_row(_batch_row()))
    assert "✓" in line and "manual" in line
    assert "3 read, 3 kept, 0 skipped" in line
    with_error = status.batch_line(status.batch_from_row(
        _batch_row(status="error", error="disk full")))
    assert "disk full" in with_error


def test_batches_outcome_empty_history_is_honest_placeholder():
    from xx_vitals.sync.api import FakeSyncApi

    lines, notice = status.batches_outcome(FakeSyncApi())
    assert notice is None
    assert len(lines) == 1 and "first sync" in lines[0]


def test_batches_outcome_401_and_507_surface_notices():
    from xx_vitals.sync.api import AuthExpired, FakeSyncApi, Transient, VolumeFull

    api = FakeSyncApi()
    api.batches_result = AuthExpired()
    assert status.batches_outcome(api)[1] == status.AUTH_NOTICE
    api.batches_result = VolumeFull()
    assert status.batches_outcome(api)[1] == status.VOLUME_NOTICE
    api.batches_result = Transient("offline")
    lines, notice = status.batches_outcome(api)
    assert lines == [] and notice == status.BATCHES_UNAVAILABLE
