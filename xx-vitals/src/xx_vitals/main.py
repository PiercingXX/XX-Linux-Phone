#!/usr/bin/env python3
"""XX-Vitals entry point: Adw.Application with the four-tab shell.

Theme comes from the XX-WM config with hot-reload (see ui/shell), falling
back to libadwaita dark/light otherwise. `--sync-once` runs one headless
push/pull pass for the systemd user timer (no display needed).
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from xx_vitals import __app_id__
from xx_vitals.paths import ensure_app_dirs
from xx_vitals.prefs import load as load_prefs


class VitalsApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is not None:
            window.present()
            return
        from xx_vitals.store.db import Database
        from xx_vitals.sync.auth import LibsecretBackend
        from xx_vitals.ui import shell

        ensure_app_dirs()
        db = Database()
        db.init()
        window = shell.build_window(self, db, load_prefs(), LibsecretBackend())
        self._theme_monitor = shell.watch_xx_wm_config(shell.apply_xx_wm_theme)
        window.present()


def sync_once() -> int:
    """One headless push/pull pass for `xx-vitals-sync.service`.

    Everything blocking (libsecret, HTTP) runs on a worker thread; the
    outcome is plain copy on stdout. Exit codes: 0 synced, 1 transient
    failure, 2 not configured, 3 re-login required.
    """
    import threading

    from xx_vitals.store.db import Database
    from xx_vitals.sync import auth as auth_module
    from xx_vitals.sync import worker as worker_module
    from xx_vitals.sync.engine import SyncConfigException

    ensure_app_dirs()
    config = load_prefs()
    finished: dict = {}

    def work() -> dict:
        from xx_vitals.sync import http_api
        from xx_vitals.sync.engine import SyncEngine

        secrets = auth_module.LibsecretBackend()
        credentials = auth_module.load_credentials(config, secrets)
        if not credentials.can_sync:
            raise SyncConfigException(
                "sync not configured: set the NAS host and sign in on Profile"
            )
        hosts = {h for h in (config.server_url, config.xxchat_url) if h and h.strip()}
        api = http_api.HttpSyncApi(
            config.server_url,
            lambda: auth_module.load_credentials(config, secrets).token,
            lambda: set(hosts),
            on_auth_expired=lambda: _mark_relogin(config),
        )
        db = Database()
        db.init()
        return worker_module.run_sync_pass(
            SyncEngine(db, db, db, db, db, api)
        )

    def runner() -> None:
        try:
            finished["ok"] = work()
        except BaseException as exc:  # surface the taxonomy, never a traceback
            finished["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "ok" in finished:
        summary = finished["ok"]
        if summary.get("paused"):
            print("NAS storage full — queue kept, will retry")
        print(f"Sync done: {summary.get('pending', 0)} queued")
        return 0
    exc = finished.get("error")
    if isinstance(exc, SyncConfigException):
        print(str(exc))
        return 2
    from xx_vitals.sync.engine import SyncAuthException
    from xx_vitals.sync import status as status_model

    notice = status_model.notice_for_exception(exc) if exc is not None else None
    if isinstance(exc, SyncAuthException):
        print(notice or str(exc))
        return 3
    print(notice or f"sync failed: {exc}")
    return 1


def _mark_relogin(config) -> None:
    from xx_vitals import prefs as prefs_module

    try:
        stored = prefs_module.load()
        stored.relogin_required = True
        prefs_module.save(stored)
    except OSError:
        config.relogin_required = True


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--sync-once" in raw:
        return sync_once()
    app = VitalsApplication()
    return app.run(sys.argv if argv is None else [sys.argv[0], *raw])


if __name__ == "__main__":
    raise SystemExit(main())
