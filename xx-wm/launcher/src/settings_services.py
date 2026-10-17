"""Curated services section (ref service-manager.sh).

A fixed list, never a unit browser: state reads unprivileged (systemd
`GetUnit`/`ActiveState`/`GetUnitFileState`, OpenRC `rc-service status`
and `rc-update show` in a worker thread); state changes go through the
root helper's `ManageService`, whose allowlist holds exactly these
units — xx-wm itself, dbus and logind are never touched. Stop uses the
power-menu arm-then-confirm (two taps, the arm resetting on any other
action); failed units list read-only.
"""
from __future__ import annotations

from dataclasses import dataclass

import settings_page

try:
    from power_menu import ConfirmState
except Exception:
    # Collection-time stub gi packages (installed by some test modules
    # at import) cannot build power_menu's Gtk.Window subclass. The
    # fallback is the same two-tap machine, pinned by test; window.py
    # imports PowerMenu lazily for the same reason.
    class ConfirmState:
        """First tap arms, second tap confirms (power-menu pattern)."""

        def __init__(self) -> None:
            self.pending = None

        def tap(self, action: str) -> bool:
            if self.pending == action:
                self.pending = None
                return True
            self.pending = action
            return False

        def reset(self) -> None:
            self.pending = None


@dataclass(frozen=True)
class ServiceEntry:
    key: str
    title: str
    detail: str
    systemd_units: tuple
    openrc_names: tuple


SERVICES: tuple = (
    ServiceEntry('modem', 'Modem', 'Mobile data modem',
                 ('ModemManager.service',), ('modemmanager',)),
    ServiceEntry('eg25', 'EG25 modem helper', 'Quectel manager, when present',
                 ('eg25-manager.service',), ('eg25-manager',)),
    ServiceEntry('network', 'Network', 'WiFi and wired connections',
                 ('NetworkManager.service',), ('networkmanager',)),
    ServiceEntry('bluetooth', 'Bluetooth', 'Bluetooth devices',
                 ('bluetooth.service',), ('bluetooth',)),
    ServiceEntry('ssh', 'SSH server', 'Remote shell access',
                 ('sshd.service', 'ssh.service'), ('sshd',)),
    ServiceEntry('tailscale', 'Tailscale', 'Private mesh VPN',
                 ('tailscaled.service',), ('tailscaled',)),
    ServiceEntry('waydroid', 'Waydroid container', 'Android compatibility',
                 ('waydroid-container.service',), ('waydroid',)),
)

SERVICE_ACTIONS = ('Start', 'Stop', 'Restart', 'Enable', 'Disable')


def state_text(state) -> str:
    """One-line row subtitle for an (active, enabled) pair."""
    try:
        active, enabled = state
    except (TypeError, ValueError):
        return 'Unavailable'
    if active is None:
        return 'Unavailable'
    if active == 'active' or active == 'activating' or active is True:
        base = 'On'
    elif active == 'failed':
        base = 'Failed'
    else:
        base = 'Off'
    if enabled == 'enabled' or enabled is True:
        base += ', starts at boot'
    return base


class ServicesSection(settings_page._Section):
    """Settings section: the curated service list plus failed units."""

    key = 'services'
    title = 'Services'

    def __init__(self) -> None:
        self._rows_box = None
        self._failed_box = None
        self._confirm = ConfirmState()

    def build(self, win) -> list:
        from settings_page import _section_title
        from gi.repository import Gtk

        label = _section_title(self.title)
        self._rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=0)
        failed_title = _section_title('Failed')
        self._failed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=0)
        return [label, self._rows_box, failed_title, self._failed_box]

    def refresh(self, win) -> None:
        from settings_page import _run_bg
        _run_bg(self._probe, lambda res: self._render(win, res))

    # ------------------------------------------------------------ behavior

    def _probe(self):
        """Worker thread: init, per-service state, failed units. Never raises."""
        import system_settings

        try:
            init = system_settings.detect_init()
        except Exception:
            init = 'unknown'
        states: dict = {}
        try:
            if init == 'systemd':
                for entry in SERVICES:
                    states[entry.key] = self._systemd_state(
                        system_settings, entry)
            elif init == 'openrc':
                for entry in SERVICES:
                    states[entry.key] = self._openrc_state(
                        system_settings, entry)
        except Exception:
            pass
        try:
            eg25 = self._eg25_present(system_settings, init)
        except Exception:
            eg25 = False
        try:
            failed = system_settings.list_failed_units(init)
        except Exception:
            failed = []
        return {'init': init, 'states': states, 'eg25': eg25,
                'failed': failed}

    @staticmethod
    def _systemd_state(system_settings, entry):
        for unit in entry.systemd_units:
            try:
                state = system_settings.systemd_unit_state(unit)
            except Exception:
                state = None
            if state is not None:
                return state
        return None

    @staticmethod
    def _openrc_state(system_settings, entry):
        import os
        for name in entry.openrc_names:
            path = os.path.join('/etc', 'init.d', name)
            try:
                if not os.path.isfile(path):
                    continue
            except Exception:
                continue
            try:
                running, enabled = system_settings.openrc_service_state(name)
            except Exception:
                return None
            active = ('active' if running else 'inactive'
                      if running is not None else None)
            file_state = ('enabled' if enabled else 'disabled'
                          if enabled is not None else None)
            return (active, file_state)
        return None

    @staticmethod
    def _eg25_present(system_settings, init: str) -> bool:
        import os
        if init == 'systemd':
            try:
                return system_settings.systemd_unit_state(
                    'eg25-manager.service') is not None
            except Exception:
                return False
        if init == 'openrc':
            try:
                return os.path.isfile('/etc/init.d/eg25-manager')
            except Exception:
                return False
        return False

    def _render(self, win, res) -> None:
        if self._rows_box is None or self._failed_box is None:
            return
        try:
            states = res.get('states', {})
            eg25 = bool(res.get('eg25'))
            failed = res.get('failed', [])
        except AttributeError:
            return
        from settings_page import _replace_box_children
        from xx_ui import widgets

        rows = []
        visible = [entry for entry in SERVICES
                   if entry.key != 'eg25' or eg25]
        for index, entry in enumerate(visible):
            row = widgets.list_row(
                entry.title, state_text(states.get(entry.key)),
                last=index == len(visible) - 1,
                on_activate=lambda e=entry: self._on_service_menu(win, e))
            rows.append(row)
        _replace_box_children(self._rows_box, rows)
        self._show_failed(win, failed)

    def _show_failed(self, win, failed) -> None:
        if self._failed_box is None:
            return
        from settings_page import (_replace_box_children,
                                   _section_placeholder)
        from xx_ui import widgets

        items = [unit for unit in (failed or []) if unit]
        if not items:
            _replace_box_children(
                self._failed_box, [_section_placeholder('No failed units.')])
            return
        _replace_box_children(self._failed_box, [
            widgets.setting_row(unit, 'failed', last=index == len(items) - 1)
            for index, unit in enumerate(items)])

    def _on_service_menu(self, win, entry) -> None:
        actions = []
        for action in SERVICE_ACTIONS:
            if action == 'Stop':
                actions.append(
                    ('Stop', lambda e=entry: self._on_stop_tapped(win, e)))
            else:
                actions.append(
                    (action,
                     lambda a=action.lower(), e=entry: self._apply(
                         win, e, a)))
        actions.append(('Cancel', lambda: None))
        win.item_actions._show_action_menu(
            self._rows_box, entry.title, actions)

    def _stop_key(self, key: str) -> str:
        return 'stop:%s' % key

    def _on_stop_tapped(self, win, entry) -> str:
        return self._on_stop(win, self._stop_key(entry.key))

    def _on_stop(self, win, stop_key: str) -> str:
        """Arm-then-confirm: the first tap arms, the second executes. The
        arm resets on any other service action (menus close between taps,
        so no timer can expire it). Returns 'armed' or 'confirmed'."""
        if self._confirm.tap(stop_key):
            _key = stop_key.split(':', 1)[1]
            entry = next((item for item in SERVICES if item.key == _key),
                         None)
            if entry is not None:
                self._apply(win, entry, 'stop')
            return 'confirmed'
        try:
            win._show_status('Tap Stop again to stop the service.')
        except Exception:
            pass
        return 'armed'

    def _apply(self, win, entry, action: str) -> None:
        self._confirm.reset()
        try:
            import helper_client
        except ImportError as err:
            win._show_status(f'Could not {action} {entry.title}: {err}')
            return

        def _done(ok: bool, detail) -> None:
            if ok:
                win._show_status(f'{entry.title}: {action}ed.')
            else:
                win._show_status(f'Could not {action} {entry.title}: '
                                 f'{detail}')
            self.refresh(win)

        try:
            helper_client.manage_service(entry.key, action, _done)
        except Exception as err:
            win._show_status(f'Could not {action} {entry.title}: {err}')
