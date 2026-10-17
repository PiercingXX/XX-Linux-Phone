"""Silenced-call record cards over org.freedesktop.Notifications.

The shell owns the incoming surface and its own missed-call card; this
module posts the dialer's post-hoc record — ``Silenced call`` /
``N silenced calls`` with the reason string — obeying the three-state
``silenced_notif_policy`` (immediately / daily summary / never) and
collapsing bursts inside the 60 s window into one card (port of
``ring/SilencedNotifier.kt`` + ``ring/SilenceBatcher.kt``). The daily
summary stashes into the store and flushes opportunistically on each new
silenced call and on every app start — no alarms.

Single cards carry the recovery actions Call back, Ring next time and
Block; batch cards carry none (same rule as the Android original). Every
bus failure degrades to a dropped card, never a raise — a refused card
must not crash the app.

All Gio use is injectable: ``notify`` replaces the FDN sender, the
action handlers replace modem/contacts work, and the store/config come
from plain paths, so headless tests drive everything through fakes.
"""
from __future__ import annotations

import time

from xx_dialer import applog, config as app_config
from xx_dialer import recents
from xx_dialer.store import Store, db_path

NOTIF_BUS = "org.freedesktop.Notifications"
NOTIF_PATH = "/org/freedesktop/Notifications"
NOTIF_IFACE = "org.freedesktop.Notifications"

ACTION_TARGET_HINT = "x-xx-wm-action-target"

REPLACES_SILENCED = 1

APP_NAME = "XX Dialer"

_ACTION_CALL_BACK = "call-back"
_ACTION_RING_NEXT = "ring-next-time"
_ACTION_BLOCK = "block-contact"

_ACTION_ALIASES = {
    "call-back": _ACTION_CALL_BACK,
    "callback": _ACTION_CALL_BACK,
    "ring-next-time": _ACTION_RING_NEXT,
    "star": _ACTION_RING_NEXT,
    "block-contact": _ACTION_BLOCK,
    "block": _ACTION_BLOCK,
}

_ACTION_DEDUPE_MS = 30_000


def action_target() -> str:
    """``"<bus> <path> <interface>"`` for the action-target hint.

    Read off the policy service constants so the hint can never drift
    from the name the shell must call back; the shell parses it with
    ``notif_daemon.parse_action_target`` and calls
    ``HandleNotificationAction(u id, s action_key)`` on it with
    auto-start, so a tap still works after this process idle-exited.
    """
    try:
        from xx_dialer import service as policy_service

        return "%s %s %s" % (policy_service.BUS_NAME,
                             policy_service.OBJECT_PATH,
                             policy_service.INTERFACE)
    except Exception:
        return ("io.piercingxx.XXDialer.Policy"
                " /io/piercingxx/XXDialer/Policy"
                " io.piercingxx.XXDialer.Policy1")


def _now_ms() -> int:
    return int(time.time() * 1000)


def notify_params(glib, app_name: str, replaces_id: int, summary: str,
                  body: str, actions: list[tuple[str, str]],
                  hints: dict) -> object:
    """Build the real ``Notify`` params tuple as one ``GLib.Variant``.

    The ``a{sv}`` hints ride as a plain dict of per-value Variants:
    PyGObject's generic builder accepts that (its own documented
    shape), but a pre-built ``Variant("a{sv}", ...)`` nested in the
    tuple raises ``KeyError`` at send time — which used to turn every
    real card into a silent drop while the injected-sink tests stayed
    green.
    """
    flat: list[str] = []
    for key, label in actions:
        flat.extend([key, label])
    return glib.Variant(
        "(susssasa{sv}i)",
        (app_name, replaces_id, "", summary, body, flat,
         {name: glib.Variant("s", value)
          for name, value in hints.items()}, 0))


class Notifier:
    """Policy-gated silenced-call cards with burst batching."""

    def __init__(self, db_file=None, home=None, notify=None,
                 on_call_back=None, on_ring_next=None, on_block=None,
                 bus=None, gio=None, glib=None) -> None:
        from pathlib import Path as _Path

        if db_file is None:
            db_file = db_path(home if home is not None else _Path.home())
        self._db_file = db_file
        self._home = home
        self._notify = notify
        self._on_call_back = on_call_back or (lambda _e164: None)
        self._on_ring_next = on_ring_next or (lambda _e164: None)
        self._on_block = on_block or (lambda _e164: None)
        self._bus = bus
        self._gio = gio
        self._glib = glib
        self._batch = recents.BatchState()
        self._server_ids: dict[int, int] = {}
        self._posted: set[int] = set()
        self._posted_numbers: dict[int, str] = {}
        self._acted: dict[tuple[int, str], int] = {}
        self._actions_watched = False

    # ---- entry points ---------------------------------------------------

    def screened(self, e164: str | None, verdict: str, reason: str,
                 at_ms: int | None = None) -> None:
        """Record one screened incoming call; only Silence posts a card."""
        token = (verdict or "").strip().lower()
        if token not in ("silence", recents.TOKEN_SILENCE.lower()):
            return
        moment = at_ms if at_ms is not None else _now_ms()
        try:
            policy = app_config.load(self._home).get(
                "silenced_notif_policy", "immediate")
        except Exception:
            policy = "immediate"
        if policy == "never":
            return
        if policy == "daily":
            self._stash_daily(e164, reason, moment)
            self.flush_daily_if_due(moment)
            return
        outcome = recents.batch_next(self._batch, moment)
        self._batch = outcome.state
        if isinstance(outcome, recents.BatchBatch):
            self.post_batch(outcome.count, e164, reason)
        else:
            self.post_batch(1, e164, reason)

    def _region(self) -> str:
        try:
            from xx_dialer import policy as _policy

            return _policy.region_of(app_config.load(self._home))
        except Exception:
            return "US"

    def post_batch(self, count: int, e164: str | None,
                   reason: str) -> None:
        """Post a card for ``count`` silenced calls; bypasses the policy."""
        total = max(1, int(count))
        title = recents.silenced_title(total)
        body = recents.silenced_card_line(e164, reason, self._region())
        actions: list[tuple[str, str]] = []
        if total == 1 and (e164 or "").strip():
            number = (e164 or "").strip()
            actions = [
                ("%s:%s" % (_ACTION_CALL_BACK, number), "Call back"),
                ("%s:%s" % (_ACTION_RING_NEXT, number), "Ring next time"),
                ("%s:%s" % (_ACTION_BLOCK, number), "Block"),
            ]
        self._send(REPLACES_SILENCED, title, body, actions,
                   e164=(e164 or "").strip())

    def flush_daily_if_due(self, now_ms: int | None = None) -> bool:
        """Post the stashed daily summary when its 24 h clock expired."""
        moment = now_ms if now_ms is not None else _now_ms()
        try:
            with Store(self._db_file) as store:
                entries = store.read_digest()
                if not entries:
                    return False
                last_flush = store.get_digest_flushed_at()
                if not recents.digest_due(moment, last_flush):
                    return False
                newest = max(entries, key=lambda row: row[0])
                count = len(entries)
                store.clear_digest()
                store.set_digest_flushed_at(moment)
        except Exception:
            return False
        _at, e164, reason = newest
        self.post_batch(count, e164, reason)
        return True

    def dismiss_silenced(self) -> None:
        self._close(REPLACES_SILENCED)

    # ---- notification actions --------------------------------------------

    def handle_action(self, key: str) -> None:
        """Dispatch one FDN action key (``kind:number``). Never raises."""
        try:
            kind, _, number = str(key).partition(":")
            self._dispatch(_ACTION_ALIASES.get(kind.strip()), number.strip())
        except Exception:
            pass

    def _dispatch(self, kind: str | None, number: str) -> bool:
        """Run one canonical action; False when the key is unusable."""
        if kind is None or not number:
            return False
        try:
            if kind == _ACTION_CALL_BACK:
                self._on_call_back(number)
            elif kind == _ACTION_RING_NEXT:
                self._on_ring_next(number)
            elif kind == _ACTION_BLOCK:
                self._on_block(number)
            else:
                return False
            self.dismiss_silenced()
        except Exception:
            return False
        return True

    def handle_notification_action(self, server_id: object,
                                   action_key: object) -> bool:
        """Shell ``HandleNotificationAction`` entry: act on posted ids.

        The shell forwards every tap on a card carrying the
        ``x-xx-wm-action-target`` hint, firing this with auto-start —
        which re-activates an idle-exited service. Only ids this
        service posted (memory, else the ``posted_notif`` table that
        survives the restart) act; anything else returns False having
        done nothing. The number rides in the key (``kind:e164``) so no
        memory is needed; a bare kind falls back to the number stored
        for the id. Never raises.
        """
        try:
            notif_id = int(server_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        try:
            if not self.is_posted(notif_id):
                return False
            kind, _, number = str(action_key or "").partition(":")
            kind = _ACTION_ALIASES.get(kind.strip())
            number = number.strip() or self.posted_number(notif_id)
            if self._already_acted(notif_id, str(action_key or "")):
                return True
            if not self._dispatch(kind, number):
                return False
            self._mark_acted(notif_id, str(action_key or ""))
            applog.write("cards", "action id=%d %s %s" % (
                notif_id, kind, number), self._home)
            return True
        except Exception:
            return False

    def note_posted(self, server_id: object, e164: str = "") -> bool:
        """Record a server id this service posted a card under."""
        try:
            notif_id = int(server_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        if notif_id <= 0:
            return False
        self._posted.add(notif_id)
        if (e164 or "").strip():
            self._posted_numbers[notif_id] = e164.strip()
        try:
            with Store(self._db_file) as store:
                store.record_posted_notif(notif_id, e164 or "",
                                          _now_ms())
        except Exception:
            pass
        return True

    def is_posted(self, server_id: object) -> bool:
        """True when ``server_id`` is a card id this service posted."""
        try:
            notif_id = int(server_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        if notif_id in self._posted:
            return True
        try:
            with Store(self._db_file) as store:
                if store.is_posted_notif(notif_id):
                    self._posted.add(notif_id)
                    return True
        except Exception:
            pass
        return False

    def posted_number(self, server_id: object) -> str:
        """E.164 stored for a posted card id, or "" when unknown."""
        try:
            notif_id = int(server_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return ""
        if notif_id in self._posted_numbers:
            return self._posted_numbers[notif_id]
        try:
            with Store(self._db_file) as store:
                number = store.notif_number(notif_id)
        except Exception:
            return ""
        if number:
            self._posted_numbers[notif_id] = number
        return number

    def _already_acted(self, server_id: int, key: str) -> bool:
        """True when this (id, key) already acted within the window.

        The shell both emits ``ActionInvoked`` and forwards to
        ``HandleNotificationAction`` for one tap, and both reach a live
        service — without this the Call back tap would place two calls.
        """
        now = _now_ms()
        try:
            for seen, _at in [item for item in self._acted.items()
                              if now - item[1] > _ACTION_DEDUPE_MS]:
                del self._acted[seen]
        except Exception:
            pass
        return (int(server_id), str(key)) in self._acted

    def _mark_acted(self, server_id: int, key: str) -> None:
        try:
            self._acted[(int(server_id), str(key))] = _now_ms()
        except Exception:
            pass

    # ---- daily stash -------------------------------------------------------

    def _stash_daily(self, e164: str | None, reason: str,
                     moment: int) -> None:
        try:
            with Store(self._db_file) as store:
                store.stash_silenced(moment, e164, reason,
                                     cap=recents.DIGEST_CAP)
                if not store.get_digest_flushed_at():
                    store.set_digest_flushed_at(moment)
        except Exception:
            pass

    # ---- FDN plumbing (injectable, fail-soft) -------------------------------

    def _send(self, replaces_id: int, summary: str, body: str,
              actions: list[tuple[str, str]], e164: str = "") -> None:
        sender = self._notify
        if sender is None:
            sender = self._fdn_sender()
            if sender is None:
                return
        try:
            server_id = sender(replaces_id, summary, body, actions,
                               self._hints(), e164)
        except Exception:
            return
        try:
            posted_id = int(server_id)
            if posted_id > 0:
                self._server_ids[replaces_id] = posted_id
                self.note_posted(posted_id, e164)
                applog.write("cards", "posted id=%d %s" % (
                    posted_id, summary), self._home)
        except (TypeError, ValueError):
            pass

    def _hints(self) -> dict:
        hints = {ACTION_TARGET_HINT: action_target()}
        try:
            tone = app_config.load(self._home).get("default_tone") or ""
        except Exception:
            tone = ""
        if tone.strip():
            hints["sound-file"] = tone.strip()
        return hints

    def _close(self, replaces_id: int) -> None:
        server_id = self._server_ids.pop(replaces_id, 0)
        if not server_id:
            return
        closer = self._fdn_closer()
        if closer is None:
            return
        try:
            closer(server_id)
        except Exception:
            pass

    def _session_bus(self):
        if self._bus is not None:
            return self._bus
        try:
            from gi.repository import Gio as _gio
        except ImportError:
            return None
        gio = self._gio if self._gio is not None else _gio
        try:
            bus = gio.bus_get_sync(gio.BusType.SESSION, None)
        except Exception:
            return None
        self._bus = bus
        return bus

    def _fdn_sender(self):
        bus = self._session_bus()
        if bus is None:
            return None
        try:
            from gi.repository import Gio as _gio, GLib as _glib
        except ImportError:
            return None
        gio = self._gio if self._gio is not None else _gio
        glib = self._glib if self._glib is not None else _glib
        self._watch_actions(bus, gio)

        def _send(replaces_id: int, summary: str, body: str,
                  actions: list[tuple[str, str]],
                  hints: dict, e164: str = "") -> None:
            def _done(conn, result) -> None:
                try:
                    (server_id,) = conn.call_finish(result).unpack()
                except Exception:
                    return
                self.note_posted(server_id, e164)

            bus.call(NOTIF_BUS, NOTIF_PATH, NOTIF_IFACE, "Notify",
                     notify_params(glib, APP_NAME, replaces_id, summary,
                                   body, actions, hints),
                     None, gio.DBusCallFlags.NONE, 2000, None, _done)

        return _send

    def _fdn_closer(self):
        bus = self._session_bus()
        if bus is None:
            return None
        try:
            from gi.repository import Gio as _gio, GLib as _glib
        except ImportError:
            return None
        gio = self._gio if self._gio is not None else _gio
        glib = self._glib if self._glib is not None else _glib

        def _close(server_id: int) -> None:
            def _done(conn, result) -> None:
                try:
                    conn.call_finish(result)
                except Exception:
                    pass

            bus.call(NOTIF_BUS, NOTIF_PATH, NOTIF_IFACE,
                     "CloseNotification",
                     glib.Variant("(u)", (server_id,)),
                     None, gio.DBusCallFlags.NONE, 2000, None, _done)

        return _close

    def _watch_actions(self, bus, gio) -> None:
        if self._actions_watched:
            return
        try:
            bus.signal_subscribe(
                NOTIF_BUS, NOTIF_IFACE, "ActionInvoked", NOTIF_PATH,
                None, gio.DBusSignalFlags.NONE,
                self._on_action_signal, None)
        except Exception:
            return
        self._actions_watched = True

    def _on_action_signal(self, _conn, _sender, _path, _iface, _signal,
                          params, _user_data) -> None:
        try:
            server_id, key = params.unpack()
        except Exception:
            return
        try:
            seen_id = int(server_id)
            if self._already_acted(seen_id, str(key)):
                return
            self._mark_acted(seen_id, str(key))
        except (TypeError, ValueError):
            pass
        self.handle_action(str(key))
