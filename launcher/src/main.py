#!/usr/bin/env python3

import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gio

from first_boot import FirstBootWizard
from ipc import IPCServer
from notif_daemon import NotificationDaemon
from shell_log import get_logger, setup_logging
from window import ShellWindow

_log = get_logger('main')


class PiercingShellApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id='io.piercingxx.PiercingShell',
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._shell: ShellWindow | None = None
        self._ipc: IPCServer | None = None
        self._notif_daemon: NotificationDaemon | None = None

    def do_activate(self) -> None:
        if self.props.active_window is not None:
            self.props.active_window.present()
            return

        self._ipc = IPCServer(self._on_ipc_command)
        _setup_logind(self._on_sleep)
        self._notif_daemon = NotificationDaemon(
            on_notify=self._on_notification,
            on_close=self._on_notification_closed,
        )

        if FirstBootWizard.is_needed():
            wizard = FirstBootWizard(on_complete=self._show_shell)
            wizard.set_application(self)
            wizard.present()
        else:
            self._show_shell()

    def _show_shell(self) -> None:
        self._shell = ShellWindow(self)
        self._shell.present()
        _log.info('shell window presented')

        # Display power management: power button + fingerprint wake the screen.
        # Must be started after shell window exists so on_wake can show lock screen.
        from display_manager import DisplayManager, _take_screenshot
        from power_menu import PowerMenu
        self._power_menu = PowerMenu()
        self._power_menu.set_application(self)
        self._display_mgr = DisplayManager(
            on_wake=self._shell._show_lock_screen,
            on_power_menu=self._power_menu.show_menu,
            on_screenshot=_take_screenshot,
            on_fingerprint=self._shell.try_fingerprint_unlock,
        )
        self._shell._display_mgr = self._display_mgr

        # Arrow overlay for back gesture feedback (lisgd fires gesture.back via IPC)
        from back_gesture import BackGestureLayer
        back = BackGestureLayer()
        back.set_application(self)
        self._shell._back_layer = back

    def _on_notification(
        self, notif_id: int, app_name: str, summary: str, body: str, desktop_entry: str
    ) -> None:
        _log.debug('notification %d from %s: %s', notif_id, app_name, summary)
        if self._shell is None:
            return
        shade = getattr(self._shell, '_shade', None)
        if shade is not None:
            shade.add_notification(notif_id, app_name, summary, body, desktop_entry)

    def _on_notification_closed(self, notif_id: int) -> None:
        if self._shell is None:
            return
        shade = getattr(self._shell, '_shade', None)
        if shade is not None:
            shade.dismiss(notif_id)

    def _on_ipc_command(self, command: str) -> None:
        _log.info('IPC command: %s', command)
        if not self._shell:
            return
        if command == 'lock':
            GLib.idle_add(self._shell._show_lock_screen)
        elif command == 'shade.show':
            GLib.idle_add(lambda: self._shell._show_shade() if self._shell else None)
        elif command == 'shade.hide':
            shade = getattr(self._shell, '_shade', None)
            if shade:
                GLib.idle_add(shade.hide_shade)
        elif command == 'switcher.show':
            GLib.idle_add(lambda: self._shell._show_switcher() if self._shell else None)
        elif command == 'switcher.hide':
            switcher = getattr(self._shell, '_switcher', None)
            if switcher:
                GLib.idle_add(switcher.hide_switcher)
        elif command == 'gesture.back':
            GLib.idle_add(self._shell._handle_back)
            back = getattr(self._shell, '_back_layer', None)
            if back:
                GLib.idle_add(back.flash_back, True)
        elif command == 'gesture.home':
            GLib.idle_add(self._shell.stack.set_visible_child_name, 'home')
        elif command == 'gesture.shade':
            GLib.idle_add(lambda: self._shell._show_shade() if self._shell else None)
        elif command == 'gesture.switcher':
            GLib.idle_add(lambda: self._shell._show_switcher() if self._shell else None)

    def _on_sleep(self, sleeping: bool) -> None:
        if sleeping and self._shell:
            _log.info('system suspending — locking screen')
            GLib.idle_add(self._shell._show_lock_screen)


def _setup_logind(on_sleep_callback: object) -> None:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        bus.signal_subscribe(
            'org.freedesktop.login1',
            'org.freedesktop.login1.Manager',
            'PrepareForSleep',
            '/org/freedesktop/login1',
            None,
            Gio.DBusSignalFlags.NONE,
            lambda _c, _s, _p, _i, _sig, params, _ud: on_sleep_callback(params.unpack()[0]),
            None,
        )
        _log.info('subscribed to logind PrepareForSleep')
    except GLib.Error as e:
        _log.warning('could not subscribe to logind: %s', e)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    app = PiercingShellApplication()
    return app.run(argv or sys.argv)


if __name__ == '__main__':
    raise SystemExit(main())
