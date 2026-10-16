"""Lane 4 action drivers: ring, lock-screen message, lock-now.

All live effects sit behind injected factories so unit tests never touch
the real session/system bus, audio, sockets or files. Production wiring
is lazy: import touches no bus, and every bus call uses a
`GLib.VariantType` reply type (never a string) with tuple unpacking.

Ring reuses the shell's own path (`Ringer` in the shell's `call_ui`):
feedbackd `TriggerFeedback(app_id, "phone-incoming-call", hints, 0)` on
the session bus, where timeout 0 loops until `EndFeedback`, with the
`paplay`/`pw-play` loop (shell `sound`) as fallback. The hints request
the `full` profile for this one event plus the `important` override, so
the ring still sounds when the daemon sits in `quiet`/`silent`; without
them the event honors the active profile and is silent under `silent`.
The daemon may still ignore `important` unless the app is allow-listed
(`allow-important`), which is why the sound loop stays as fallback. No
volume is ever written: this module contains no ALSA/PipeWire volume
writes by design.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from pathlib import Path

from gi.repository import Gio, GLib

LOG = logging.getLogger("xx-find.actions")

FEEDBACK_NAME = "org.sigxcpu.Feedback"
FEEDBACK_PATH = "/org/sigxcpu/Feedback"
FEEDBACK_IFACE = "org.sigxcpu.Feedback"
FIND_APP_ID = "io.piercingxx.Find"
RINGER_EVENT = "phone-incoming-call"
RING_DURATION_S = 60.0

RING_FEEDBACK_PROFILE = "full"
RING_HINT_PROFILE = "profile"
RING_HINT_IMPORTANT = "important"

LOGIN1_NAME = "org.freedesktop.login1"
LOGIN1_PATH = "/org/freedesktop/login1"
LOGIN1_MANAGER = "org.freedesktop.login1.Manager"

SHELL_SOCKET_SUFFIX = "xx-wm.sock"
MESSAGE_FILE_NAME = "find-message.json"
MESSAGE_TEXT_MAX = 280
CALLBACK_MAX = 64

_DBUS_TIMEOUT_MS = 5000
_RING_TRIGGER_TIMEOUT_MS = 2000


def _reply_type(fmt: str) -> GLib.VariantType:
    return GLib.VariantType.new(fmt)


def shell_socket_path() -> Path:
    """Shell IPC socket, same layout as the shell's own `ipc` module."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or str(
        Path.home() / ".local" / "share" / "xx-wm")
    return Path(runtime) / SHELL_SOCKET_SUFFIX


def ipc_send(command: str, path: Path | None = None) -> bool:
    """Best-effort one-line send to the shell IPC server. True if sent."""
    target = Path(path) if path is not None else shell_socket_path()
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(0.5)
            sock.connect(str(target))
            sock.send(command.encode("utf-8"))
        finally:
            sock.close()
    except OSError as exc:
        LOG.debug("shell ipc %r not sent: %s", command, exc)
        return False
    return True


def _open_session_bus():
    from gi.repository import Gio as _Gio
    return _Gio.bus_get_sync(_Gio.BusType.SESSION, None)


def _open_system_bus():
    from gi.repository import Gio as _Gio
    return _Gio.bus_get_sync(_Gio.BusType.SYSTEM, None)


def ring_feedback_hints() -> dict:
    """Per-event feedbackd hints that request ring-through.

    `profile: "full"` asks for the full-profile feedbacks for this one
    event; `important: True` asks to override the global feedback level
    (the alarm-clock path). Names/values follow feedbackd's D-Bus API
    (`data/org.sigxcpu.Feedback.xml`: `profile` overrides the profile
    for the event, `important` overrides the global level together with
    `profile`, `sound-file` is not used here). Valid profiles are
    `full`/`quiet`/`silent` (`feedbackd(8)`); the daemon may ignore
    `important` unless the app is in `allow-important`.
    """
    return {RING_HINT_PROFILE: GLib.Variant("s", RING_FEEDBACK_PROFILE),
            RING_HINT_IMPORTANT: GLib.Variant("b", True)}


def ring_feedback_params(app_id: str, event: str) -> GLib.Variant:
    """`(ssa{sv}i)` params for `TriggerFeedback`, built in one step.

    The hints dict stays a plain dict of Variants so real PyGObject packs
    it directly; never nest a prebuilt `a{sv}` Variant here (real
    PyGObject raises `KeyError: 0`, fakes accept it).
    """
    return GLib.Variant("(ssa{sv}i)",
                        (app_id, event, ring_feedback_hints(), 0))


def _trigger_feedback(bus, app_id: str, event: str,
                      timeout_ms: int = _RING_TRIGGER_TIMEOUT_MS) -> int | None:
    try:
        reply = bus.call_sync(
            FEEDBACK_NAME, FEEDBACK_PATH, FEEDBACK_IFACE,
            "TriggerFeedback",
            ring_feedback_params(app_id, event),
            _reply_type("(u)"), Gio.DBusCallFlags.NONE,
            timeout_ms, None)
    except Exception as exc:
        LOG.debug("TriggerFeedback failed: %s", exc)
        return None
    try:
        return int(reply.unpack()[0])
    except Exception:
        return None


def _end_feedback(bus, feedback_id: int,
                  timeout_ms: int = _RING_TRIGGER_TIMEOUT_MS) -> None:
    try:
        bus.call_sync(
            FEEDBACK_NAME, FEEDBACK_PATH, FEEDBACK_IFACE,
            "EndFeedback", GLib.Variant("(u)", (int(feedback_id),)),
            None, Gio.DBusCallFlags.NONE, timeout_ms, None)
    except Exception as exc:
        LOG.debug("EndFeedback failed: %s", exc)


def build_ring_action(*, session_bus_factory=None, play=None, stop=None,
                      sleep=None, duration_s: float = RING_DURATION_S,
                      app_id: str = FIND_APP_ID,
                      event: str = RINGER_EVENT):
    """Callable `ring(full_volume=True)` for `AgentDeps`.

    Drives feedbackd first with the `full`+`important` per-event hints
    so the ring still sounds when the daemon sits in `quiet`/`silent`;
    falls back to the injected `play`/`stop` sound loop when feedbackd
    is absent or declines. `full_volume` is accepted for the agent seam
    and intentionally changes no mixer: ring-through comes from the
    requested profile, never from a volume write. Worker-thread only
    (blocking calls with timeouts); tests inject fakes.
    """
    bus_factory = session_bus_factory or _open_session_bus
    do_sleep = sleep or time.sleep

    def _ring(*, full_volume: bool = True) -> None:
        del full_volume
        bus = None
        try:
            bus = bus_factory()
        except Exception as exc:
            LOG.debug("no session bus for ring: %s", exc)
            bus = None
        if bus is not None:
            fid = _trigger_feedback(bus, app_id, event)
            if fid is not None:
                try:
                    do_sleep(max(0.0, float(duration_s)))
                finally:
                    _end_feedback(bus, fid)
                return
            LOG.debug("feedbackd absent, using sound fallback")
        if play is None or stop is None:
            return
        try:
            play()
        except Exception as exc:
            LOG.debug("ring fallback play failed: %s", exc)
            return
        try:
            do_sleep(max(0.0, float(duration_s)))
        finally:
            try:
                stop()
            except Exception as exc:
                LOG.debug("ring fallback stop failed: %s", exc)

    return _ring


def format_find_message(text: object, callback: object,
                        epoch: int) -> dict:
    """Pure lock-screen message record. Never raises: coerces and clips."""
    try:
        body = str(text or "")
    except Exception:
        body = ""
    try:
        call = str(callback or "")
    except Exception:
        call = ""
    body = " ".join(body.split())
    call = " ".join(call.split())
    if len(body) > MESSAGE_TEXT_MAX:
        body = body[:MESSAGE_TEXT_MAX]
    if len(call) > CALLBACK_MAX:
        call = call[:CALLBACK_MAX]
    try:
        when = int(epoch)
    except (TypeError, ValueError):
        when = 0
    return {"v": 1, "text": body, "callback": call, "epoch": when}


def message_path(state_dir: Path) -> Path:
    return Path(state_dir) / MESSAGE_FILE_NAME


def write_find_message(state_dir: Path, record: dict) -> Path:
    """Atomically persist the lock-screen message (0700 dir, 0600 file)."""
    target = message_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    import json as _json

    text = _json.dumps(record, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True)
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    try:
        dfd = os.open(str(target.parent), os.O_DIRECTORY)
    except OSError:
        return target
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return target


def read_find_message(state_dir: Path) -> dict | None:
    """Read back the persisted message, or None when absent/corrupt."""
    import json as _json

    try:
        raw = message_path(state_dir).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        record = _json.loads(raw)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    return record


def clear_find_message(state_dir: Path) -> None:
    try:
        message_path(state_dir).unlink()
    except OSError:
        pass


def build_message_action(*, state_dir_factory=None, ipc_sender=None,
                         now=None):
    """Callable `show_message(text, callback)` for `AgentDeps`.

    Persists the record for the shell lock screen (see
    `docs/shell-integration.md`), then best-effort nudges the shell to
    show the lock screen. IPC failure never fails the command; file
    failure raises so the agent reports it.
    """
    from xx_find import config as _config

    clock = now or time.time

    def _show(text: object, callback: object) -> None:
        if state_dir_factory is not None:
            state_dir = Path(state_dir_factory())
        else:
            state_dir = _config.default_state_dir()
        record = format_find_message(text, callback, int(clock()))
        write_find_message(state_dir, record)
        sender = ipc_sender or ipc_send
        try:
            sender("lock")
        except Exception as exc:
            LOG.debug("message lock nudge failed: %s", exc)

    return _show


def _lock_via_logind(bus, timeout_ms: int = _DBUS_TIMEOUT_MS) -> bool:
    try:
        bus.call_sync(
            LOGIN1_NAME, LOGIN1_PATH, LOGIN1_MANAGER, "LockSessions",
            None, None, Gio.DBusCallFlags.NONE, timeout_ms, None)
    except Exception as exc:
        LOG.debug("logind LockSessions failed: %s", exc)
        return False
    return True


def build_lock_action(*, ipc_sender=None, system_bus_factory=None,
                      timeout_ms: int = _DBUS_TIMEOUT_MS):
    """Callable `lock_now()` for `AgentDeps`.

    Primary path is the shell's own `lock` IPC verb (same socket the
    shell listens on); logind `LockSessions` is the fallback. Raises
    only when both paths fail.
    """
    sender = ipc_sender or ipc_send

    def _lock() -> None:
        ipc_ok = False
        try:
            result = sender("lock")
            ipc_ok = bool(result) if result is not None else True
        except Exception as exc:
            LOG.debug("shell lock ipc failed: %s", exc)
            ipc_ok = False
        if ipc_ok:
            return
        bus = None
        try:
            factory = system_bus_factory or _open_system_bus
            bus = factory()
        except Exception as exc:
            LOG.debug("no system bus for lock: %s", exc)
            bus = None
        if bus is not None and _lock_via_logind(bus, timeout_ms):
            return
        raise RuntimeError("lock failed: shell ipc and logind unavailable")

    return _lock
