"""Uninstall a drawer app, behind a confirm (Android parity).

A Flatpak app uninstalls via ``flatpak uninstall --user/--system <ref>``
(the scope follows the .desktop file location); anything else resolves
the owning package of the .desktop file and removes it through PackageKit
over D-Bus. Entries with no resolvable target report unsupported and the
menu hides the row. Nothing here touches the main loop: the Flatpak child
runs in a worker thread (callers post back via GLib.idle_add) and the
PackageKit flow is fully async D-Bus with injectable connections, so tests
drive it with fakes and never uninstall anything real.
"""
from __future__ import annotations

from pathlib import Path, PurePath

_PK_BUS = 'org.freedesktop.PackageKit'
_PK_PATH = '/org/freedesktop/PackageKit'
_PK_IFACE = 'org.freedesktop.PackageKit'
_PK_TXN_IFACE = 'org.freedesktop.PackageKit.Transaction'

_PK_CALL_TIMEOUT_MS = 120_000


def _home_dir(home_dir: str | None = None) -> str:
    return home_dir or str(Path.home())


def flatpak_scope(desktop_path: str, home_dir: str | None = None,
                  system_flatpak_dirs: tuple = ('/var/lib/flatpak',),
                  ) -> str | None:
    """'user' / 'system' for a Flatpak-exported .desktop path, else None."""
    try:
        path = str(desktop_path or '')
        if 'flatpak' not in PurePath(path).parts:
            return None
        if path.startswith(_home_dir(home_dir) + '/.local/share/flatpak/'):
            return 'user'
        for rooted in system_flatpak_dirs or ():
            if rooted and path.startswith(str(rooted).rstrip('/') + '/'):
                return 'system'
    except Exception:
        return None
    return None


def flatpak_ref(entry: object) -> str:
    """Flatpak app id from a drawer entry (the export file stem)."""
    app_id = str(getattr(entry, 'app_id', '') or '')
    return app_id[:-8] if app_id.endswith('.desktop') else app_id


def _desktop_filename(entry: object) -> str:
    try:
        return str(entry.desktop_app.get_filename() or '')
    except Exception:
        return ''


def plan_uninstall(entry: object, home_dir: str | None = None,
                   system_flatpak_dirs: tuple = ('/var/lib/flatpak',),
                   ) -> tuple:
    """One of ('flatpak', scope, ref), ('packagekit', desktop_path),
    ('unsupported',). Pure: safe to call from menu building."""
    import os
    filename = _desktop_filename(entry)
    if not filename or not os.path.isfile(filename):
        return ('unsupported',)
    scope = flatpak_scope(filename, home_dir=home_dir,
                          system_flatpak_dirs=system_flatpak_dirs)
    if scope is not None:
        return ('flatpak', scope, flatpak_ref(entry))
    if 'flatpak' in PurePath(filename).parts:
        return ('unsupported',)
    return ('packagekit', filename)


def run_flatpak_uninstall(scope: str, ref: str, on_done,
                          runner=None) -> None:
    """Run the Flatpak removal synchronously; the caller threads it and
    posts `on_done(ok, message)` back to the main loop."""
    if runner is None:
        import subprocess
        runner = subprocess.run
    try:
        proc = runner(['flatpak', 'uninstall', '-y', '--' + scope, ref],
                      capture_output=True, text=True, timeout=120)
    except Exception as error:
        on_done(False, 'Uninstall failed: %s' % error)
        return
    if proc.returncode == 0:
        on_done(True, '%s uninstalled.' % ref)
    else:
        detail = (getattr(proc, 'stderr', '') or '').strip()
        on_done(False, detail or 'Flatpak refused the uninstall.')


def _variant(signature: str, value):
    from gi.repository import GLib
    return GLib.Variant(signature, value)


def _call_flags():
    from gi.repository import Gio
    return Gio.DBusCallFlags.NONE


def _signal_flags():
    from gi.repository import Gio
    return Gio.DBusSignalFlags.NONE


def _default_bus_async(callback) -> None:
    from gi.repository import Gio
    Gio.bus_get(Gio.BusType.SYSTEM, None, callback)


class PackageKitRemover:
    """Remove the package owning a .desktop file via PackageKit.

    Async throughout: CreateTransaction, then SearchFiles for the file
    (Package signals collected off the main loop), then RemovePackages for
    the first owner. `connect` injects the system-bus connection for tests.
    """

    def __init__(self, connect=None, on_done=None) -> None:
        self._connect = connect
        self._on_done = on_done or (lambda _ok, _msg: None)
        self._bus = None
        self._txn: str | None = None
        self._packages: list[str] = []
        self._desktop = ''

    def start(self, desktop_path: str) -> None:
        self._desktop = desktop_path
        if self._connect is not None:
            try:
                self._bus = self._connect()
            except Exception as error:
                self._finish(False, 'PackageKit unavailable: %s' % error)
                return
            self._create_transaction()
        else:
            try:
                _default_bus_async(self._on_bus)
            except Exception as error:
                self._finish(False, 'PackageKit unavailable: %s' % error)

    def _on_bus(self, _source, result) -> None:
        from gi.repository import Gio
        try:
            self._bus = Gio.bus_get_finish(result)
        except Exception as error:
            self._finish(False, 'PackageKit unavailable: %s' % error)
            return
        self._create_transaction()

    def _call(self, bus_name: str, path: str, iface: str, method: str,
              params, callback) -> None:
        self._bus.call(bus_name, path, iface, method, params, None,
                       _call_flags(), _PK_CALL_TIMEOUT_MS, None, callback)

    def _create_transaction(self) -> None:
        try:
            self._call(_PK_BUS, _PK_PATH, _PK_IFACE, 'CreateTransaction',
                       _variant('()', ()), self._on_transaction)
        except Exception as error:
            self._finish(False, 'PackageKit error: %s' % error)

    def _on_transaction(self, _conn, result) -> None:
        try:
            self._txn = result.unpack()[0]
        except Exception:
            self._finish(False, 'PackageKit error: no transaction.')
            return
        try:
            self._bus.signal_subscribe(
                None, _PK_TXN_IFACE, 'Package', self._txn, None,
                _signal_flags(), self._on_package)
            self._bus.signal_subscribe(
                None, _PK_TXN_IFACE, 'Finished', self._txn, None,
                _signal_flags(), self._on_search_finished)
            self._call(_PK_BUS, self._txn, _PK_TXN_IFACE, 'SearchFiles',
                       _variant('(tas)', (0, [self._desktop])),
                       self._on_search_queued)
        except Exception as error:
            self._finish(False, 'PackageKit error: %s' % error)

    def _on_search_queued(self, _conn, _result) -> None:
        pass

    def _on_package(self, _conn, _sender, _path, _iface, _signal,
                    params) -> None:
        try:
            _info, package_id, _summary = params.unpack()
        except Exception:
            return
        if package_id and package_id not in self._packages:
            self._packages.append(package_id)

    def _on_search_finished(self, _conn, _sender, _path, _iface, _signal,
                            params) -> None:
        try:
            exit_code, _runtime = params.unpack()
        except Exception:
            self._finish(False, 'PackageKit error during search.')
            return
        if exit_code != 0:
            self._finish(False, 'PackageKit search failed.')
            return
        if not self._packages:
            self._finish(
                False, 'No package owns %s.' % Path(self._desktop).name)
            return
        try:
            self._bus.signal_subscribe(
                None, _PK_TXN_IFACE, 'Finished', self._txn, None,
                _signal_flags(), self._on_remove_finished)
            self._call(_PK_BUS, self._txn or '', _PK_TXN_IFACE,
                       'RemovePackages',
                       _variant('(tasbb)', (0, [self._packages[0]],
                                            False, False)),
                       self._on_remove_queued)
        except Exception as error:
            self._finish(False, 'PackageKit error: %s' % error)

    def _on_remove_queued(self, _conn, _result) -> None:
        pass

    def _on_remove_finished(self, _conn, _sender, _path, _iface, _signal,
                            params) -> None:
        try:
            exit_code, _runtime = params.unpack()
        except Exception:
            self._finish(False, 'PackageKit error during removal.')
            return
        if exit_code == 0:
            self._finish(True, 'Uninstalled.')
        else:
            self._finish(False, 'PackageKit refused the uninstall.')

    def _finish(self, ok: bool, message: str) -> None:
        try:
            self._on_done(ok, message)
        except Exception:
            pass
