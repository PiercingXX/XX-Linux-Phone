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


class XXWMApplication(Adw.Application):
    def __init__(self, replay_welcome: bool = False) -> None:
        super().__init__(
            application_id='io.piercingxx.XXWM',
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._replay_welcome = replay_welcome
        self._shell: ShellWindow | None = None
        self._ipc: IPCServer | None = None
        self._notif_daemon: NotificationDaemon | None = None
        from focus_mode import HeldNotifications
        self._held_notifications = HeldNotifications()
        GLib.timeout_add_seconds(30, self._tick_focus_release)

    def do_activate(self) -> None:
        if self.props.active_window is not None:
            self.props.active_window.present()
            return

        # The configured font applies launcher-wide, wizard included
        from config import ShellConfig
        from font_theme import apply_global_font
        apply_global_font(ShellConfig().font_family)

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

        # Warm swipe-bound apps in the real session only — under a host
        # shell (dev runs over Phosh) the preloads would grab the screen.
        # Also opt-in via config (default off): preloading is counter to the
        # minimalism directive on weak hardware.
        import os
        from config import should_preload_gesture_apps
        if should_preload_gesture_apps(
                self._shell.config, bool(os.environ.get('XX_WM_SESSION'))):
            GLib.timeout_add_seconds(8, self._preload_gesture_apps)

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

        if self._replay_welcome:
            self._replay_welcome = False
            self._show_welcome_tour()

    def _show_welcome_tour(self) -> None:
        wizard = FirstBootWizard(on_complete=lambda: None, tour_only=True)
        wizard.set_application(self)
        wizard.present()

    def _on_notification(
        self, notif_id: int, app_name: str, summary: str, body: str,
        desktop_entry: str, hints: dict,
    ) -> None:
        _log.debug('notification %d from %s: %s', notif_id, app_name, summary)
        if self._shell is None:
            return
        # "Disable for…" mute: notifications from a muted app are discarded
        # outright until the deadline passes
        config = getattr(self._shell, 'config', None)
        if config is not None and (config.is_app_muted(desktop_entry)
                                   or config.is_app_muted(app_name)):
            _log.debug('notification %d dropped: %s is muted', notif_id, desktop_entry or app_name)
            return

        category = str(hints.get('category', ''))
        urgency = hints.get('urgency', 1)
        is_alarm = category.startswith('alarm') or urgency == 2

        # Focus: notifications from paused apps are held outright — no sound,
        # not in the shade — and released in one batch when focus ends
        focus = getattr(self._shell, 'focus_state', None)
        if focus is not None and focus.is_paused_app(desktop_entry) and not is_alarm:
            self._held_notifications.hold(notif_id, app_name, summary, body, desktop_entry)
            _log.debug('notification %d held by Focus: %s', notif_id, desktop_entry)
            return

        # DnD: notifications still collect in the shade, but silently —
        # no sound. Alarms are always exempt.
        dnd = getattr(self._shell, 'dnd_state', None)
        dnd_silenced = dnd is not None and dnd.is_active() and not is_alarm

        if (config is not None and config.sound_notifications
                and not dnd_silenced and not hints.get('suppress-sound')):
            import sound
            sound.play('notify.wav')

        # Create the shade on first notification so nothing is lost before
        # the user first opens it
        self._shell._ensure_shade().add_notification(
            notif_id, app_name, summary, body, desktop_entry)

    def _tick_focus_release(self) -> bool:
        focus = getattr(self._shell, 'focus_state', None) if self._shell else None
        if focus is None or len(self._held_notifications) == 0:
            return True
        if focus.is_active():
            return True
        for held in self._held_notifications.release_all():
            self._shell._ensure_shade().add_notification(*held)
        return True

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
            def _go_home() -> None:
                if self._shell:
                    self._shell.stack.set_visible_child_name('home')
                    # Hop above the focused app; drops back on next launch
                    self._shell.present_over_apps()
            GLib.idle_add(_go_home)
        elif command == 'gesture.shade':
            GLib.idle_add(lambda: self._shell._show_shade() if self._shell else None)
        elif command == 'gesture.keyboard':
            GLib.idle_add(lambda: self._shell._show_keyboard() if self._shell else None)
        elif command == 'gesture.switcher':
            GLib.idle_add(lambda: self._shell._show_switcher() if self._shell else None)
        elif command == 'welcome':
            GLib.idle_add(self._show_welcome_tour)

    def _preload_gesture_apps(self) -> bool:
        if self._shell:
            self._shell.preload_gesture_apps()
        return GLib.SOURCE_REMOVE

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
    argv = list(argv if argv is not None else sys.argv)
    replay_welcome = '--welcome' in argv
    if replay_welcome:
        argv.remove('--welcome')
    app = XXWMApplication(replay_welcome=replay_welcome)
    return app.run(argv)


if __name__ == '__main__':
    raise SystemExit(main())
