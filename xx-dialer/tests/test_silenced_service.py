"""Silenced-record cards have one owner: the policy service process.

The service posts the batched cards when ``NoteCall`` reports an
unanswered silenced incoming (the GUI never posts); the final test
proves the real service emits a real ``Notify`` with no GUI running.
"""
import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

from xx_dialer import config as app_config
from xx_dialer.service import PolicyService
from xx_dialer.store import MirrorRow, PatternRow, Store

ADA = "+14155550100"
BO = "+14155550200"


class _Sink:
    """Injected FDN sender: captures cards, hands out server ids."""

    def __init__(self):
        self.cards = []
        self.next_id = 40

    def __call__(self, replaces_id, summary, body, actions, hints,
                 e164=""):
        self.next_id += 1
        self.cards.append({"replaces": replaces_id,
                           "summary": summary, "body": body,
                           "actions": list(actions), "hints": hints,
                           "server": self.next_id, "e164": e164})
        return self.next_id


class _StubContacts:
    """Service-process contacts stand-in: records star/block writes."""

    def __init__(self):
        self.revision = 0
        self.starred: list[str] = []
        self.blocked: list[str] = []

    def set_star(self, uid, starred):
        if starred:
            self.starred.append(uid)

    def set_blocked(self, uid, blocked):
        if blocked:
            self.blocked.append(uid)
        return []


def _silenced_service(tmp_path):
    """Enforcing service where ADA and BO always screen to Silence.

    Uses the service's own action wiring (Call back via the injected
    placer, star/block via the attached contacts stand-in); only the
    FDN sender is sunk so no bus is touched.
    """
    settings = app_config.load(tmp_path)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.add_pattern(PatternRow(
            e164_prefix="14155550100", wildcards=0, action="silence",
            created_at=1))
        store.add_pattern(PatternRow(
            e164_prefix="14155550200", wildcards=0, action="silence",
            created_at=2))
    sink = _Sink()
    placed: list[str] = []
    contacts = _StubContacts()
    service = PolicyService(db_file=tmp_path / "calls.db",
                            home=tmp_path, call_placer=placed.append)
    service._notifier._notify = sink
    service.attach_contacts_client(contacts)
    return service, sink, placed, contacts


def test_service_posts_one_card_for_silenced_missed(tmp_path):
    from xx_dialer import recents as _recents

    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    assert service.check_number(ADA) == ("silence", "silenced by pattern")
    service.note_call(ADA, "incoming", int(time.time() * 1000), 0,
                      "missed")
    assert len(sink.cards) == 1
    (card,) = sink.cards
    assert card["summary"] == "Silenced call"
    assert _recents.format_number(ADA) in card["body"] \
        and "by pattern" in card["body"]
    assert [key for key, _label in card["actions"]] == [
        "call-back:%s" % ADA, "ring-next-time:%s" % ADA,
        "block-contact:%s" % ADA]
    assert card["hints"]["x-xx-wm-action-target"] == (
        "io.piercingxx.XXDialer.Policy /io/piercingxx/XXDialer/Policy"
        " io.piercingxx.XXDialer.Policy1")


def test_service_collapses_burst_into_one_card(tmp_path):
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    service.check_number(ADA)
    service.note_call(ADA, "incoming", int(time.time() * 1000), 0,
                      "missed")
    service.check_number(BO)
    service.note_call(BO, "incoming", int(time.time() * 1000), 0,
                      "missed")
    assert [card["summary"] for card in sink.cards] == [
        "Silenced call", "2 silenced calls"]
    assert sink.cards[-1]["actions"] == []


def test_service_posts_nothing_unless_silenced_unanswered(tmp_path):
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    service.check_number(ADA)
    service.note_call(ADA, "incoming", int(time.time() * 1000), 30_000,
                      "answered")
    assert sink.cards == []
    service.note_call(ADA, "outgoing", int(time.time() * 1000), 0,
                      "missed")
    assert sink.cards == []
    service.note_call("+19995550100", "incoming",
                      int(time.time() * 1000), 0, "missed")
    assert sink.cards == []
    with Store(tmp_path / "calls.db") as store:
        store.add_blocked(BO)
    assert service.check_number(BO) == ("block", "blocked")
    service.note_call(BO, "incoming", int(time.time() * 1000), 0,
                      "missed")
    assert sink.cards == []


def test_service_card_actions_run_in_service(tmp_path):
    service, sink, placed, contacts = _silenced_service(tmp_path)
    with Store(tmp_path / "calls.db") as store:
        store.upsert_mirror([MirrorRow(
            "uid-ada", ADA, "Ada", False, None, True,
            int(time.time() * 1000))])
    assert service.check_number(ADA) == ("silence", "send to voicemail")
    service.check_number(ADA)
    service.note_call(ADA, "incoming", int(time.time() * 1000), 0,
                      "missed")
    assert len(sink.cards) == 1
    (card,) = sink.cards
    assert service.handle_notification_action(card["server"],
                                              "call-back:%s" % ADA) is True
    assert placed == [ADA]
    assert service.handle_notification_action(card["server"],
                                              "ring-next-time:%s" % ADA) \
        is True
    assert contacts.starred == ["uid-ada"]
    assert service.handle_notification_action(card["server"],
                                              "block-contact:%s" % ADA) \
        is True
    assert contacts.blocked == ["uid-ada"]
    assert service.handle_notification_action(card["server"], "bogus") \
        is False
    assert service.handle_notification_action(999999, "call-back:%s" % ADA) \
        is False
    assert placed == [ADA]


def test_service_block_falls_back_to_local_blocklist(tmp_path):
    service, _sink, _placed, _contacts = _silenced_service(tmp_path)
    service._block_number("+19995550100")
    with Store(tmp_path / "calls.db") as store:
        assert store.is_blocked("+19995550100")


def test_service_daily_policy_stash_and_flush(tmp_path):
    settings = app_config.load(tmp_path)
    settings["silenced_notif_policy"] = "daily"
    app_config.save(settings, tmp_path)
    service, sink, _placed, _contacts = _silenced_service(tmp_path)
    service.check_number(ADA)
    now_ms = int(time.time() * 1000)
    service.note_call(ADA, "incoming", now_ms, 0, "missed")
    assert sink.cards == []
    with Store(tmp_path / "calls.db") as store:
        assert len(store.read_digest()) == 1
    assert service.flush_daily_digest() is False
    assert service._notifier.flush_daily_if_due(
        now_ms + 24 * 60 * 60 * 1000 + 1_000) is True
    assert sink.cards[-1]["summary"] == "Silenced call"


def test_gui_posts_no_silenced_cards(tmp_path):
    """The GUI owns Recents rows/badges only — never a card."""
    from pathlib import Path

    from xx_dialer.main import DialerDeps, DialerView

    with pytest.raises(TypeError):
        DialerDeps(db_file=tmp_path / "calls.db", home=tmp_path,
                   voice=None, contacts=None, notifier=None)
    assert not hasattr(DialerView, "missed_observed")
    assert "notifier" not in DialerView.__init__.__code__.co_varnames
    text = (Path(__file__).parent.parent / "src" / "xx_dialer"
            / "main.py").read_text(encoding="utf-8")
    assert "ringer" not in text
    assert "Notifier" not in text
    assert "missed_observed" not in text


_SILENCED_E2E = textwrap.dedent("""\
    import sys
    sys.path.insert(0, "@SRC@")
    from pathlib import Path
    home = Path("@HOME@")
    from gi.repository import Gio, GLib
    from xx_dialer.service import (
        PolicyService, BUS_NAME, OBJECT_PATH, INTERFACE)

    NOTIF_BUS = "org.freedesktop.Notifications"
    NOTIF_PATH = "/org/freedesktop/Notifications"
    NOTIF_IFACE = "org.freedesktop.Notifications"
    NUMBER_A = "+14155550100"
    NUMBER_B = "+14155550200"

    NOTIF_XML = ("<node><interface name='org.freedesktop.Notifications'>"
                 "<method name='Notify'>"
                 "<arg direction='in' type='s'/>"
                 "<arg direction='in' type='u'/>"
                 "<arg direction='in' type='s'/>"
                 "<arg direction='in' type='s'/>"
                 "<arg direction='in' type='s'/>"
                 "<arg direction='in' type='as'/>"
                 "<arg direction='in' type='a{sv}'/>"
                 "<arg direction='in' type='i'/>"
                 "<arg direction='out' type='u'/>"
                 "</method></interface></node>")

    captured = []
    next_id = [100]

    def on_notify(_conn, _sender, _path, _iface, _method, params,
                  invocation):
        (app, replaces, _icon, summary, body, actions,
         _hints, _timeout) = params.unpack()
        next_id[0] += 1
        captured.append((summary, body, list(actions)))
        invocation.return_value(GLib.Variant("(u)", (next_id[0],)))

    service = PolicyService(home=home)
    loop = GLib.MainLoop()
    state = {}
    bus = None

    def on_notif_acquired(connection, _name):
        node = Gio.DBusNodeInfo.new_for_xml(NOTIF_XML)
        connection.register_object(
            NOTIF_PATH, node.interfaces[0], on_notify)

    def on_bus_acquired(connection, _name):
        service.register_on(connection)

    def on_name_acquired(_connection, _name):
        GLib.idle_add(_do_check_a)

    def on_name_lost(_connection, _name):
        state["error"] = "name lost"
        loop.quit()

    def _do_check_a():
        bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, "CheckNumber",
                 GLib.Variant("(s)", (NUMBER_A,)), None,
                 Gio.DBusCallFlags.NONE, 5000, None, _on_check_a)
        return False

    def _on_check_a(conn, res):
        verdict, _reason = conn.call_finish(res).unpack()
        assert verdict == "silence", verdict
        import time as _time
        bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, "NoteCall",
                 GLib.Variant(
                     "(ssxxs)",
                     (NUMBER_A, "incoming",
                      int(_time.time() * 1000), 0, "missed")),
                 None, Gio.DBusCallFlags.NONE, 5000, None, _on_noted_a)

    def _on_noted_a(conn, res):
        conn.call_finish(res)
        bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, "CheckNumber",
                 GLib.Variant("(s)", (NUMBER_B,)), None,
                 Gio.DBusCallFlags.NONE, 5000, None, _on_check_b)

    def _on_check_b(conn, res):
        verdict, _reason = conn.call_finish(res).unpack()
        assert verdict == "silence", verdict
        import time as _time
        bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, "NoteCall",
                 GLib.Variant(
                     "(ssxxs)",
                     (NUMBER_B, "incoming",
                      int(_time.time() * 1000), 0, "missed")),
                 None, Gio.DBusCallFlags.NONE, 5000, None, _on_noted_b)

    def _on_noted_b(conn, res):
        conn.call_finish(res)
        GLib.timeout_add(2000, _finish)

    def _finish():
        try:
            from xx_dialer import recents as _recents
            assert [title for title, _body, _acts in captured] == [
                "Silenced call", "2 silenced calls"], captured
            _title, body, actions = captured[0]
            assert _recents.format_number(NUMBER_A) in body, body
            assert actions[0::2] == [
                "call-back:%s" % NUMBER_A,
                "ring-next-time:%s" % NUMBER_A,
                "block-contact:%s" % NUMBER_A], actions
            assert captured[1][2] == [], captured
        except AssertionError as err:
            state["error"] = str(err)
        loop.quit()
        return False

    Gio.bus_own_name(Gio.BusType.SESSION, NOTIF_BUS,
                     Gio.BusNameOwnerFlags.NONE,
                     on_notif_acquired, lambda *_a: None,
                     lambda *_a: None)
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                     Gio.BusNameOwnerFlags.NONE,
                     on_bus_acquired, on_name_acquired, on_name_lost)
    GLib.timeout_add_seconds(30, loop.quit)
    loop.run()
    assert not state.get("error"), state.get("error")
    print("SILENCED-E2E-OK cards=%d" % len(captured))
    """)


def test_silenced_card_posted_by_service_without_gui(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session not installed")
    pytest.importorskip("gi.repository.Gio")
    import xx_dialer

    home = tmp_path / "home"
    (home / ".local" / "share" / "xx-dialer").mkdir(parents=True)
    (home / ".config" / "xx-dialer").mkdir(parents=True)
    settings = app_config.load(home)
    settings["enforcement_mode"] = "enforcing"
    app_config.save(settings, home)
    with Store(home / ".local" / "share" / "xx-dialer" / "calls.db") as store:
        store.add_pattern(PatternRow(
            e164_prefix="14155550100", wildcards=0, action="silence",
            created_at=1))
        store.add_pattern(PatternRow(
            e164_prefix="14155550200", wildcards=0, action="silence",
            created_at=2))
    src = str(__import__("pathlib").Path(
        xx_dialer.__file__).parent.parent)
    helper = tmp_path / "silenced_e2e.py"
    helper.write_text(_SILENCED_E2E.replace("@SRC@", src).replace(
        "@HOME@", str(home)))
    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper)],
        capture_output=True, text=True, timeout=90, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "SILENCED-E2E-OK cards=2" in proc.stdout, (
        "stdout=%s stderr=%s" % (proc.stdout[-2000:],
                                 proc.stderr[-2000:]))
