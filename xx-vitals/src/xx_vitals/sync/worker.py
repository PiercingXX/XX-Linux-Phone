"""Worker threads for login and "Sync now": never block the GTK loop.

Network I/O and libsecret `*_sync` calls run on a daemon thread; results
reach GTK widgets through GLib.idle_add, falling back to a direct call when
GLib is unavailable (headless tests).
"""

from __future__ import annotations

import threading
from collections.abc import Callable


def run_in_background(work: Callable[[], object], on_done: Callable[[object], None] | None = None) -> threading.Thread:
    def runner() -> None:
        try:
            outcome = work()
        except BaseException as exc:  # keep the failure observable, never silent
            outcome = exc
        _deliver(on_done, outcome)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread


def _deliver(callback, outcome) -> None:
    if callback is None:
        return
    try:
        from gi.repository import GLib

        GLib.idle_add(lambda: (callback(outcome), False)[1])
    except ImportError:
        callback(outcome)


def should_sync(pending_change_count: int) -> bool:
    """Port of work/SyncDecision.kt: never wake the radio for an empty delta."""
    return pending_change_count > 0


def run_sync_pass(engine) -> dict:
    """One push/pull pass: push only when the queue is non-empty, pull always.

    Returns {"paused": bool, "pending": int}; paused is True when a 507
    stopped the drain with the queue kept.
    """
    paused = False
    if should_sync(engine.pending_change_count()):
        paused = bool(engine.push())
    engine.pull()
    return {"paused": paused, "pending": engine.pending_change_count()}


def login_in_background(
    xxchat_url: str,
    username: str,
    password: str,
    secrets,
    on_done=None,
    transport=None,
):
    from xx_vitals.sync import auth as auth_module

    def work():
        result = auth_module.fabric_login(xxchat_url, username, password, transport=transport)
        if isinstance(result, auth_module.LoginSuccess):
            host = _nas_host_for_token()
            if host:
                secrets.store_token(host, result.token)
            _save_xxchat_host(xxchat_url)
            _clear_relogin_flag()
        return result

    return run_in_background(work, on_done)


def sync_now_in_background(build_engine: Callable, on_done=None) -> threading.Thread:
    def work():
        return run_sync_pass(build_engine())

    return run_in_background(work, on_done)


def _nas_host_for_token() -> str | None:
    try:
        from xx_vitals.prefs import load as load_prefs

        host = load_prefs().server_url.strip()
        return host or None
    except OSError:
        return None


def _save_xxchat_host(xxchat_url: str) -> None:
    try:
        from xx_vitals import prefs as prefs_module
        from xx_vitals.sync import auth as auth_module

        parsed = auth_module.parse_host_url(xxchat_url)
        if isinstance(parsed, auth_module.HostOk):
            cfg = prefs_module.load()
            cfg.xxchat_url = parsed.url
            prefs_module.save(cfg)
    except OSError:
        pass


def _clear_relogin_flag() -> None:
    try:
        from xx_vitals import prefs as prefs_module

        cfg = prefs_module.load()
        if cfg.relogin_required:
            cfg.relogin_required = False
            prefs_module.save(cfg)
    except OSError:
        pass
