"""Milestone 6 acceptance: thread collapse, sync line, search status,
tracking-pixel scrubbing, contacts XML conformance, optimistic
rollback, and static phone-rule guards.

Headless: pure mailbox/bodies/contacts models plus source-text
guards. No network, no keyring, no display.
"""

from pathlib import Path

import pytest

from xx_email import bodies, contacts, db, mailbox
from xx_email.contacts import _first_name
from xx_email.sync_service import SyncOutcome

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_email"
UI = SRC / "ui"
# Lane copies carry xx-contacts under ref/; the real checkout has it as a
# sibling repo.
_ROOT = Path(__file__).resolve().parent.parent
REF_SERVICE = next(
    (p for p in (_ROOT / "ref" / "xx-contacts", _ROOT.parent / "xx-contacts")
     if (p / "src" / "xx_contacts" / "service.py").exists()),
    _ROOT / "ref" / "xx-contacts") / "src" / "xx_contacts" / "service.py"


@pytest.fixture
def conn():
    handle = db.connect(":memory:")
    db.migrate(handle)
    yield handle
    handle.close()


ACCOUNT = "user@dev.xxemail"


def make_thread(tid, **overrides):
    fields = {"account_email": ACCOUNT, "id": tid, "date": 1000,
              "subject": "Sub " + tid,
              "from_address": "Jane Doe <jane@example.com>",
              "from_name": "Jane Doe", "labels_csv": "INBOX",
              "in_inbox": True}
    fields.update(overrides)
    return db.Thread(**fields)


class _Msg:
    def __init__(self, mid, read):
        self.id = mid
        self.read = read


# ---------------------------------------------------------- thread collapse


def test_collapse_expands_only_last_unread():
    messages = [_Msg("m1", True), _Msg("m2", False), _Msg("m3", False)]
    assert mailbox.default_expanded_ids(messages) == {"m3"}


def test_collapse_all_read_expands_newest():
    messages = [_Msg("m1", True), _Msg("m2", True)]
    assert mailbox.default_expanded_ids(messages) == {"m2"}


def test_collapse_empty_thread_expands_nothing():
    assert mailbox.default_expanded_ids([]) == set()


def test_toggle_expanded_flips_membership():
    assert mailbox.toggle_expanded(set(), "m1") == {"m1"}
    assert mailbox.toggle_expanded({"m1"}, "m1") == set()
    assert mailbox.toggle_expanded({"m1"}, "m2") == {"m1", "m2"}


# ------------------------------------------------------------- sync status


def test_sync_status_line_states():
    assert mailbox.sync_status_line() == ""
    assert mailbox.sync_status_line(syncing=True) == "Syncing…"
    assert mailbox.sync_status_line({"ok": True, "new_ids": []}) == "Synced"
    assert mailbox.sync_status_line(
        {"ok": True, "new_ids": ["t1"]}) == "Synced — 1 new message"
    assert mailbox.sync_status_line(
        {"ok": True, "new_ids": ["t1", "t2"]}) == "Synced — 2 new messages"
    assert mailbox.sync_status_line(
        {"ok": False, "offline": True}) == "Offline — showing downloaded mail"
    assert mailbox.sync_status_line(
        {"ok": False, "reauth": True}) == "Sign in again to keep syncing"
    failed = mailbox.sync_status_line({"ok": False, "error": "boom"})
    assert failed.startswith("Sync failed")


def test_sync_status_line_accepts_outcome_objects():
    outcome = SyncOutcome(email=ACCOUNT, ok=True, new_ids=["t1"])
    assert mailbox.sync_status_line(outcome) == "Synced — 1 new message"
    offline = SyncOutcome(email=ACCOUNT, ok=False, offline=True)
    assert mailbox.sync_status_line(offline) == \
        "Offline — showing downloaded mail"
    reauth = SyncOutcome(email=ACCOUNT, ok=False, reauth=True,
                         error="grant gone")
    assert mailbox.sync_status_line(reauth) == "Sign in again to keep syncing"


# ------------------------------------------------------------ search status


def test_search_status_names_offline_fallback():
    assert mailbox.search_status(True, False, 3) == "Server results"
    assert mailbox.search_status(False, False, 2) == "Downloaded results"
    assert mailbox.search_status(False, True, 2) == "Offline results"
    assert mailbox.search_status(False, True, 0) == \
        "Offline — no downloaded matches"
    assert mailbox.search_status(True, False, 0) == "No results on the server"
    assert mailbox.search_status(False, False, 0) == "No results"


# ------------------------------------------------------------- sanitizer


def test_sanitize_strips_style_blocks_and_link_tags():
    dirty = ("<style>p{color:red}</style><link rel=stylesheet href=x>"
             "<p>Hi</p>")
    clean = bodies.sanitize_html(dirty)
    assert "<style" not in clean
    assert "<link" not in clean
    assert "Hi" in clean


def test_tracking_pixels_die_even_when_images_allowed():
    pixel = ('<p>News</p><img src="https://x/open?u=1" width="1" '
             'height="1"><img src="https://x/photo.png" alt="team photo">')
    clean = bodies.sanitize_html(pixel, allow_remote_images=True)
    assert "open?u=1" not in clean
    assert "photo.png" in clean
    assert "News" in clean


def test_render_body_marks_allowed_images_without_fetching():
    html = '<p>News</p><img src="https://x/photo.png" alt="team photo">'
    blocked = bodies.render_body(html, None)
    assert "[image" not in blocked
    assert "News" in blocked
    allowed = bodies.render_body(html, None, allow_remote_images=True)
    assert "[image team photo]" in allowed


def test_has_remote_images_gate():
    assert bodies.has_remote_images('<p>x</p><img src="https://x/y.png">')
    assert not bodies.has_remote_images("<p>no pictures</p>")
    assert not bodies.has_remote_images(None)
    assert not bodies.has_remote_images("")


# -------------------------------------------------------------- contacts


def test_contacts_constants_match_service_xml():
    if not REF_SERVICE.exists():
        pytest.skip("xx-contacts source not found")
    xml = REF_SERVICE.read_text(encoding="utf-8")
    assert contacts.CONTACTS_BUS_NAME == "io.piercingxx.Contacts"
    assert contacts.CONTACTS_OBJECT_PATH == "/io/piercingxx/Contacts"
    assert contacts.CONTACTS_INTERFACE == "io.piercingxx.Contacts1"
    for token in (contacts.CONTACTS_BUS_NAME,
                  contacts.CONTACTS_OBJECT_PATH,
                  contacts.CONTACTS_INTERFACE):
        assert token in xml
    assert '<arg direction="in" type="s" name="query"/>' in xml
    assert '<arg direction="in" type="u" name="limit"/>' in xml
    source = (SRC / "contacts.py").read_text(encoding="utf-8")
    assert '"(su)"' in source


def test_first_name_reads_real_search_tuples():
    assert _first_name([("u1", "Ada Lovelace", "+1555")]) == "Ada Lovelace"
    assert _first_name([("u1", "  ", "+1555")]) is None
    assert _first_name([("u1", "", "")]) is None
    assert _first_name([]) is None


# ------------------------------------------------------ optimistic rollback


def test_failed_server_call_rolls_back_archived_thread(conn):
    db.upsert_threads(conn, [make_thread("t1")])
    undo = mailbox.apply_local_action(conn, ACCOUNT, "ARCHIVE", ["t1"])
    assert db.get_thread(conn, ACCOUNT, "t1").in_inbox is False
    undo.revert(conn)
    row = db.get_thread(conn, ACCOUNT, "t1")
    assert row.in_inbox is True
    assert "INBOX" in row.labels_csv


def test_failed_server_call_rolls_back_star_and_read(conn):
    db.upsert_threads(conn, [make_thread("t1", unread_count=1,
                                        labels_csv="INBOX,UNREAD")])
    star = mailbox.apply_local_action(conn, ACCOUNT, "STAR", ["t1"])
    read = mailbox.apply_local_action(conn, ACCOUNT, "MARK_READ", ["t1"])
    assert db.get_thread(conn, ACCOUNT, "t1").starred is True
    read.revert(conn)
    assert db.get_thread(conn, ACCOUNT, "t1").unread_count == 1
    star.revert(conn)
    assert db.get_thread(conn, ACCOUNT, "t1").starred is False


# ---------------------------------------------------------- static guards


def test_no_browser_engine_in_the_main_process():
    for path in list(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "WebKit" not in text or "no WebKit" in text, path
        assert "require_version(\"WebKit\"" not in text, path


def test_lists_stay_single_click_without_selection():
    text = (UI / "common.py").read_text(encoding="utf-8")
    assert "set_single_click_activate(True)" in text
    assert "NoSelection" in text
    assert "GestureLongPress" in text
    assert "GestureSwipe" in text


def test_search_entry_unfocused_until_tapped_at_bottom():
    text = (UI / "search_page.py").read_text(encoding="utf-8")
    assert "set_can_focus(False)" in text
    assert "PropagationPhase.CAPTURE" in text
    assert text.index("self.append(scrolled)") < \
        text.index("self.append(entry_row)")
    assert "search_status" in text
    assert "Offline results" in (SRC / "mailbox.py").read_text(
        encoding="utf-8")


def test_mailbox_shows_sync_state_line():
    text = (UI / "mailbox_page.py").read_text(encoding="utf-8")
    assert "_sync_line" in text
    assert "sync_status_line" in text
    assert "syncing=True" in text


def test_thread_collapses_and_gates_images():
    text = (UI / "thread_page.py").read_text(encoding="utf-8")
    assert "default_expanded_ids" in text
    assert "toggle_expanded" in text
    assert "Load images" in text
    assert "has_remote_images" in text


def test_hooks_rollback_and_search_fallback():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    assert "_revert_local" in text
    assert "search_local_threads" in text
    assert "_is_offline" in text
