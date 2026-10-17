"""Session-bus policy endpoint ``io.piercingxx.XXDialer.Policy1``.

The service owns ``io.piercingxx.XXDialer.Policy`` at
``/io/piercingxx/XXDialer/Policy`` — never the GTK app id
``io.piercingxx.XXDialer`` (a service on the app id makes the GUI
register as a secondary instance and exit with "Failed to register:
No such interface org.gtk.Actions").

The shell consults ``CheckNumber`` before its first ring and reports
outcomes with ``NoteCall``; on a BLOCK verdict the SHELL does the
hangup (PORT_SPEC review note 6 — the dialer never hangs up incoming
calls itself). Method handlers never block the main loop: the engine
runs on a worker thread over warm SQLite data only (never a contacts
round trip) and replies via ``invocation.return_value`` from
``GLib.idle_add``.

This process also owns the silenced-call record cards: when
``NoteCall`` reports an unanswered incoming whose screen row says
Silence, the bundled notifier posts the batched card (the GUI never
posts). Shell consult shape (what this service answers): one async
``CheckNumber`` per incoming call with auto-start allowed and a
2000 ms timeout, unpacking the ``(ss)`` reply tuple; any error or
timeout fails open to ring. There is deliberately no
``bus_name_has_owner("io.piercingxx.XXDialer.Policy")`` probe first: an
activatable service has no owner until the bus starts it on the first
method call, so probing would keep the service dead after a reboot
and screening silently off.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from xx_dialer import config as app_config
from xx_dialer import facts as engine
from xx_dialer import ringer as cards
from xx_dialer.store import Store, db_path

BUS_NAME = "io.piercingxx.XXDialer.Policy"
OBJECT_PATH = "/io/piercingxx/XXDialer/Policy"
INTERFACE = "io.piercingxx.XXDialer.Policy1"

CONTACTS_BUS_NAME = "io.piercingxx.Contacts"
CONTACTS_OBJECT_PATH = "/io/piercingxx/Contacts"
CONTACTS_INTERFACE = "io.piercingxx.Contacts1"

OWNER_PROBE_TIMEOUT_MS = 500
CONSULT_TIMEOUT_MS = 2_000
COLD_LOOKUP_TIMEOUT_MS = 800
FAIL_OPEN_VERDICT = "ring"
FAIL_OPEN_REASON = "fail-open"

ERROR_INVALID_ARGUMENT = INTERFACE + ".InvalidArgument"

INTROSPECTION_XML = """\
<node>
  <interface name="io.piercingxx.XXDialer.Policy1">
    <method name="CheckNumber">
      <arg direction="in" type="s" name="number"/>
      <arg direction="out" type="s" name="verdict"/>
      <arg direction="out" type="s" name="reason"/>
    </method>
    <method name="NoteCall">
      <arg direction="in" type="s" name="number"/>
      <arg direction="in" type="s" name="direction"/>
      <arg direction="in" type="x" name="started_ms"/>
      <arg direction="in" type="x" name="duration_ms"/>
      <arg direction="in" type="s" name="disposition"/>
    </method>
    <method name="HandleNotificationAction">
      <arg direction="in" type="u" name="id"/>
      <arg direction="in" type="s" name="action_key"/>
    </method>
    <signal name="VerdictDecided">
      <arg type="s" name="e164"/>
      <arg type="s" name="verdict"/>
      <arg type="s" name="reason"/>
    </signal>
    <signal name="BypassChanged">
      <arg type="b" name="active"/>
      <arg type="t" name="expires_unix"/>
    </signal>
  </interface>
</node>
"""


def ensure_dirs(home: Path | None = None) -> tuple[Path, Path]:
    """Create the data/config dirs (mode 0700) before SQLite/JSON use."""
    base = home if home is not None else Path.home()
    data_dir = base / ".local" / "share" / "xx-dialer"
    config_dir = base / ".config" / "xx-dialer"
    data_dir.mkdir(mode=0x1C0, parents=True, exist_ok=True)
    config_dir.mkdir(mode=0x1C0, parents=True, exist_ok=True)
    return (data_dir, config_dir)


def _default_spawn(fn) -> None:
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()


def _default_idle(fn, *args) -> None:
    from gi.repository import GLib

    GLib.idle_add(fn, *args)


def _default_call_placer(number: str) -> None:
    """Place a callback call; worker-thread safe, result ignored."""
    from xx_dialer import modem

    try:
        client = modem.VoiceClient()
        if not client.start():
            return
    except Exception:
        return
    try:
        client.place(number, lambda _ok, _detail: None)
    except Exception:
        pass


class PolicyService:
    """D-Bus dispatch over the warm store; Gio shapes double as test seams."""

    def __init__(self, db_file: Path | None = None,
                 home: Path | None = None, spawn=None,
                 idle=None, cold_lookup=None, notifier=None,
                 call_placer=None) -> None:
        self._db_file = db_file if db_file is not None else db_path(home)
        self._home = home
        self._spawn = spawn or _default_spawn
        self._idle = idle or _default_idle
        self._connection = None
        self._contacts_client = None
        self._cold_lookup = cold_lookup
        self._cold_lock = threading.Lock()
        self._cold_lookup_done = False
        self._call_placer = call_placer
        if notifier is not None:
            self._notifier = notifier
        else:
            self._notifier = cards.Notifier(
                db_file=self._db_file, home=self._home,
                on_call_back=self._place_call_back,
                on_ring_next=self._star_number,
                on_block=self._block_number)

    def attach_contacts_client(self, client) -> None:
        """Wire the service-process cache sync; the next CheckNumber may
        use one bounded cold lookup until that sync lands (revision 0)."""
        with self._cold_lock:
            self._contacts_client = client
            self._cold_lookup_done = False

    @property
    def contacts_revision(self) -> int | None:
        client = self._contacts_client
        if client is None:
            return None
        try:
            return int(client.revision)
        except Exception:
            return None

    # ---- sync engine core (runs on the worker) -------------------------

    def check_number(self, number: str,
                     now: datetime | None = None,
                     log: bool = True) -> tuple[str, str]:
        moment = now if now is not None else datetime.now()
        cold = self._take_cold_lookup(str(number))
        with Store(self._db_file) as store:
            if cold is not None:
                try:
                    self._apply_cold_tags(store, *cold)
                except Exception:
                    pass
            settings = app_config.load(self._home)
            verdict, reason, _mode = engine.screen_number(
                number, moment, store, settings, log=log)
        return (verdict, reason)

    def _take_cold_lookup(
            self, number: str) -> tuple[str, str, str, list[str]] | None:
        """One bounded contacts lookup for a never-synced cache, once.

        Runs on the worker thread: when the attached client's revision
        is still 0 (its first List has not landed), ask xx-contacts for
        just this number over D-Bus with a short timeout. Additive-only
        and fail-open — any error yields None and the engine screens
        from the cache as usual. At most one attempt per attach.
        """
        if self.contacts_revision != 0:
            return None
        with self._cold_lock:
            if self._cold_lookup_done:
                return None
            self._cold_lookup_done = True
        try:
            if self._cold_lookup is not None:
                found = self._cold_lookup(number)
            else:
                found = self._bus_cold_lookup(number)
        except Exception:
            return None
        if found is None:
            return None
        try:
            uid, display_name, tags = found
        except (TypeError, ValueError):
            return None
        from xx_dialer.policy import canonical_number

        e164 = canonical_number(number, self._region())
        if not uid or not e164:
            return None
        return (e164, str(uid), str(display_name or ""),
                [str(t) for t in tags or []])

    def _bus_cold_lookup(self, number: str):
        """Synchronous LookupNumber, worker-thread only, never the UI."""
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if bus is None:
            return None
        return bus.call_sync(
            CONTACTS_BUS_NAME, CONTACTS_OBJECT_PATH, CONTACTS_INTERFACE,
            "LookupNumber", GLib.Variant("(s)", (number,)),
            GLib.VariantType.new("(ssas)"),
            Gio.DBusCallFlags.NONE, COLD_LOOKUP_TIMEOUT_MS, None,
        ).unpack()

    @staticmethod
    def _apply_cold_tags(store, e164: str, uid: str,
                         display_name: str, tags: list) -> None:
        """Fold one lookup into the cache without removing anything.

        The full client sync stays the source of truth for removals; a
        single-number fallback must not clobber multi-number state, so
        absent tags leave the existing rows alone.
        """
        import time

        from xx_dialer.contacts_client import normalize_tags
        from xx_dialer.store import (
            BIZ_TIER,
            BLOCKED_GROUP,
            MirrorRow,
        )

        clean = set(normalize_tags(tags))
        existing = store.find_mirror(e164)
        store.upsert_mirror([MirrorRow(
            uid, e164,
            display_name or (existing.display_name if existing else ""),
            "starred" in clean or (existing.starred if existing else False),
            existing.custom_ringtone if existing else None,
            existing.send_to_voicemail if existing else False,
            int(time.time() * 1000))])
        if "starred" in clean:
            store.add_star(uid)
        if "business" in clean:
            store.upsert_tier(uid, BIZ_TIER)
        if "blocked" in clean:
            store.upsert_tier(uid, BLOCKED_GROUP)
            store.add_blocked(e164)

    def _region(self) -> str:
        from xx_dialer import policy as _policy

        try:
            settings = app_config.load(self._home)
        except Exception:
            return "US"
        return _policy.region_of(settings)

    def note_call(self, number: str, direction: str, started_ms: int,
                  duration_ms: int, disposition: str) -> None:
        region = self._region()
        with Store(self._db_file) as store:
            engine.note_call(store, number, direction, started_ms,
                             duration_ms, disposition,
                             default_region=region)
        self._maybe_post_silenced(number, direction, disposition)

    def _maybe_post_silenced(self, number: str, direction: str,
                             disposition: str) -> None:
        """Post the silenced-record card for an unanswered silenced call.

        Runs in this process (the single owner of these cards): the
        shell's NoteCall is the trigger, the screen row written by the
        earlier CheckNumber says whether the call was silenced, and the
        bundled notifier applies the three-state policy plus burst
        batching. Answered, outgoing, unscreened and non-silenced calls
        post nothing — a fail-open shell consult leaves no screen row,
        so the shell's own card is the only one there. Never raises.
        """
        try:
            if str(direction or "").strip().lower() != "incoming":
                return
            if str(disposition or "").strip().lower() == "answered":
                return
            from xx_dialer import policy as _policy

            e164 = _policy.canonical_number(number, self._region())
            key = e164 or str(number or "").strip()
            if not key:
                return
            with Store(self._db_file) as store:
                latest = store.latest_for(key)
            if latest is None or latest.verdict != "Silence":
                return
            self._notifier.screened(latest.e164 or e164, latest.verdict,
                                    latest.reason,
                                    at_ms=int(time.time() * 1000))
        except Exception:
            pass

    def flush_daily_digest(self) -> bool:
        """Post the stashed daily summary when its 24 h clock expired."""
        try:
            return bool(self._notifier.flush_daily_if_due())
        except Exception:
            return False

    def _uid_for_number(self, number: str) -> str | None:
        try:
            with Store(self._db_file) as store:
                row = store.find_mirror(number)
        except Exception:
            return None
        return row.lookup_key if row is not None else None

    def _place_call_back(self, number: str) -> None:
        """Notification "Call back": place via ModemManager Voice."""
        try:
            placer = self._call_placer
            if placer is None:
                placer = _default_call_placer
            placer(number)
        except Exception:
            pass

    def _star_number(self, number: str) -> None:
        """Notification "Ring next time": star through the contacts cache."""
        try:
            uid = self._uid_for_number(number)
            client = self._contacts_client
            if uid is not None and client is not None:
                client.set_star(uid, True)
        except Exception:
            pass

    def _block_number(self, number: str) -> None:
        """Notification "Block": block through the contacts cache."""
        try:
            client = self._contacts_client
            uid = self._uid_for_number(number)
            if uid is not None and client is not None:
                try:
                    client.set_blocked(uid, True)
                    return
                except Exception:
                    pass
            from xx_dialer import policy as _policy

            with Store(self._db_file) as store:
                store.add_blocked(
                    _policy.canonical_number(number, self._region())
                    or str(number))
        except Exception:
            pass

    # ---- Gio dispatch ---------------------------------------------------

    def handle_method_call(self, connection, sender, object_path,
                           interface_name, method_name, parameters,
                           invocation) -> None:
        try:
            args = parameters.unpack() if parameters is not None else ()
        except Exception as exc:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, "cannot unpack args: %s" % (exc,))
            return
        if method_name == "CheckNumber":
            self._handle_check_number(connection, args, invocation)
        elif method_name == "NoteCall":
            self._handle_note_call(args, invocation)
        elif method_name == "HandleNotificationAction":
            self._handle_notification_action(args, invocation)
        else:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, "unknown method: %s" % (method_name,))

    def _handle_check_number(self, connection, args,
                             invocation) -> None:
        try:
            (number,) = args
        except (TypeError, ValueError):
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, "CheckNumber takes (s)")
            return

        def _work() -> None:
            try:
                verdict, reason = self.check_number(str(number))
            except Exception as exc:
                self._idle(invocation.return_dbus_error,
                           ERROR_INVALID_ARGUMENT, str(exc))
                return
            self._idle(self._reply_check_number, connection,
                       invocation, str(number), verdict, reason)

        self._spawn(_work)

    def _reply_check_number(self, connection, invocation, number: str,
                            verdict: str, reason: str) -> None:
        from gi.repository import GLib

        self._emit_verdict(connection, number, verdict, reason)
        invocation.return_value(GLib.Variant("(ss)", (verdict, reason)))

    def _handle_note_call(self, args, invocation) -> None:
        try:
            number, direction, started_ms, duration_ms, disposition = args
        except (TypeError, ValueError):
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, "NoteCall takes (ssxxs)")
            return

        def _work() -> None:
            try:
                self.note_call(str(number), str(direction),
                               int(started_ms), int(duration_ms),
                               str(disposition))
            except Exception as exc:
                self._idle(invocation.return_dbus_error,
                           ERROR_INVALID_ARGUMENT, str(exc))
                return
            self._idle(self._reply_note_call, invocation)

        self._spawn(_work)

    def _reply_note_call(self, invocation) -> None:
        from gi.repository import GLib

        invocation.return_value(GLib.Variant("()", ()))

    def handle_notification_action(self, notif_id: object,
                                   action_key: object) -> bool:
        """Shell tap entry: act only on ids this service posted.

        The shell fires this with auto-start on every tap of a card
        carrying the action-target hint, which re-activates an
        idle-exited service; the number rides in the key and the posted
        ids persist in the store, so Call back / Ring next time / Block
        all work with no live memory. Unknown ids do nothing. Never
        raises.
        """
        try:
            return bool(self._notifier.handle_notification_action(
                notif_id, action_key))
        except Exception:
            return False

    def _handle_notification_action(self, args, invocation) -> None:
        try:
            notif_id, action_key = args
            notif_id = int(notif_id)
        except (TypeError, ValueError):
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT,
                "HandleNotificationAction takes (us)")
            return

        def _work() -> None:
            try:
                self.handle_notification_action(notif_id,
                                                str(action_key))
            except Exception as exc:
                self._idle(invocation.return_dbus_error,
                           ERROR_INVALID_ARGUMENT, str(exc))
                return
            self._idle(self._reply_note_call, invocation)

        self._spawn(_work)

    def _emit_verdict(self, connection, number: str, verdict: str,
                      reason: str) -> None:
        if connection is None:
            return
        try:
            from gi.repository import GLib

            connection.emit_signal(
                None, OBJECT_PATH, INTERFACE, "VerdictDecided",
                GLib.Variant("(sss)", (number, verdict, reason)))
        except Exception:
            pass

    def register_on(self, connection) -> int:
        from gi.repository import Gio

        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        iface = node.interfaces[0]
        registration_id = connection.register_object(
            OBJECT_PATH, iface, self.handle_method_call)
        self._connection = connection
        return registration_id


def bus_name_has_owner(name: str,
                       timeout_ms: int = OWNER_PROBE_TIMEOUT_MS,
                       bus=None, gio=None, glib=None) -> bool:
    """True when some process owns ``name`` on the session bus.

    Kept for diagnostics only. Never gate a ``CheckNumber`` consult on
    this: the policy service is D-Bus activatable, so "no owner" often
    means "not started yet", and the consult itself (auto-start
    allowed) is what starts it.
    """
    try:
        if gio is None or glib is None:
            from gi.repository import Gio as _gio, GLib as _glib

            gio = _gio if gio is None else gio
            glib = _glib if glib is None else glib
        if bus is None:
            bus = gio.bus_get_sync(gio.BusType.SESSION, None)
        if bus is None:
            return False
        owner = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetNameOwner",
            glib.Variant("(s)", (name,)), None,
            gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0]
    except Exception:
        return False
    return bool(owner)


def consult_check_number(number: str, on_verdict, bus=None, gio=None,
                         glib=None,
                         timeout_ms: int = CONSULT_TIMEOUT_MS) -> None:
    """Shell-side consult: async CheckNumber, fail-open to ring.

    Calls ``CheckNumber`` directly with auto-start allowed (flags NONE),
    so a D-Bus-activatable service starts on the first consult even when
    nothing owns the name yet. ``on_verdict(verdict, reason)`` fires
    exactly once on the main loop.
    """
    if gio is None or glib is None:
        try:
            from gi.repository import Gio as _gio, GLib as _glib
        except ImportError:
            on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
            return
        gio = _gio if gio is None else gio
        glib = _glib if glib is None else glib
    try:
        if bus is None:
            bus = gio.bus_get_sync(gio.BusType.SESSION, None)
        if bus is None:
            on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
            return
    except Exception:
        on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
        return

    def _on_called(conn, result) -> None:
        try:
            verdict, reason = conn.call_finish(result).unpack()
        except Exception:
            on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
            return
        if verdict not in ("ring", "silence", "block"):
            on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
            return
        on_verdict(str(verdict), str(reason))

    try:
        # Flags NONE (never NO_AUTO_START): the bus daemon activates the
        # service on this first call when nobody owns the name yet.
        bus.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, "CheckNumber",
            glib.Variant("(s)", (number,)), None,
            gio.DBusCallFlags.NONE, timeout_ms, None, _on_called,
        )
    except Exception:
        on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)


def run_service(home: Path | None = None) -> int:
    """Own the bus name and serve Policy1 until the bus drops us.

    Also runs the contacts cache sync in this process (on the main
    loop): without it a star or block set while the dialer UI is closed
    would never reach the screener. The worker-thread engine keeps
    opening its own short-lived Store handles on the same file, so the
    two sides meet only under SQLite's own locking.
    """
    from gi.repository import Gio, GLib

    ensure_dirs(home)
    service = PolicyService(home=home)
    contacts_store = Store(db_path(home))
    from xx_dialer.contacts_client import ContactsClient
    from xx_dialer import policy as _policy

    try:
        region = _policy.region_of(app_config.load(home))
    except Exception:
        region = "US"
    client = ContactsClient(contacts_store, default_region=region)
    loop = GLib.MainLoop()
    state: dict[str, object] = {}

    def on_bus_acquired(connection: Gio.DBusConnection, _name: str) -> None:
        service.register_on(connection)

    def on_name_acquired(_connection: Gio.DBusConnection,
                         _name: str) -> None:
        state["owned"] = True

    def on_name_lost(_connection: Gio.DBusConnection | None,
                     _name: str) -> None:
        loop.quit()

    client.start()
    service.attach_contacts_client(client)

    def _flush_once() -> bool:
        service.flush_daily_digest()
        return False

    try:
        Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                         Gio.BusNameOwnerFlags.NONE,
                         on_bus_acquired, on_name_acquired, on_name_lost)
        GLib.idle_add(_flush_once)
        loop.run()
    finally:
        client.stop()
        contacts_store.close()
    return 0
