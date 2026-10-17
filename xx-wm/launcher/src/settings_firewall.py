"""Firewall / incoming-access settings section (ref firewall-manager.sh).

Presets only, never free-form ports. The privileged work lives in the
root helper (`SetInboundPreset`, `SetFirewallEnabled`, `GetFirewallState`
in launcher/helper/xx_wm_helper.py): ufw when it is installed, otherwise
a pmOS-style nftables drop-in plus reload. An accept in our own
`inet xxwm-inbound` table cannot override a drop in another base chain,
so when one is detected the section says so instead of pretending the
switches filter anything.

postmarketOS ships no enabled inbound firewall, so there the status
honestly reads off while the switches still program the ufw/nftables
mechanisms for systems that have one.
"""
from __future__ import annotations

import settings_page


def _ports(*specs) -> tuple:
    return tuple(specs)


PRESETS: dict[str, dict] = {
    'nearby': {'title': 'Nearby share',
               'detail': 'LocalSend file sharing',
               'tcp': _ports(53317), 'udp': _ports(53317)},
    'xxdb': {'title': 'Wireless debugging',
             'detail': 'Developer xxdb port range',
             'tcp': _ports((37000, 37999)), 'udp': _ports()},
    'ssh': {'title': 'SSH',
            'detail': 'Remote shell access',
            'tcp': _ports(22), 'udp': _ports()},
    'kdeconnect': {'title': 'KDE Connect',
                   'detail': 'Phone integration',
                   'tcp': _ports((1714, 1764)), 'udp': _ports((1714, 1764))},
    'syncthing': {'title': 'Syncthing',
                  'detail': 'File synchronization',
                  'tcp': _ports(22000), 'udp': _ports(22000, 21027)},
}


def preset_ufw_specs(name: str) -> list[str]:
    """ufw rule specs for a preset (`53317/tcp`, `1714:1764/udp`).

    Raises KeyError for anything but the curated preset names."""
    preset = PRESETS[name]

    def _spec(port, proto: str) -> str:
        if isinstance(port, tuple):
            return '%d:%d/%s' % (port[0], port[1], proto)
        return '%d/%s' % (port, proto)

    return ([_spec(port, 'tcp') for port in preset['tcp']]
            + [_spec(port, 'udp') for port in preset['udp']])


def _nft_port(port) -> str:
    if isinstance(port, tuple):
        return '%d-%d' % (port[0], port[1])
    return str(port)


def render_nft_rules(allowed: set[str]) -> str:
    """The managed `inet xxwm-inbound` table for the allowed presets.

    Accept policy: on a system with no other firewall this changes
    nothing observable (everything is already allowed) while documenting
    intent. Raises KeyError for names outside the preset allowlist."""
    unknown = set(allowed) - set(PRESETS)
    if unknown:
        raise KeyError('unknown inbound preset: %s' % sorted(unknown)[0])
    lines = ['# xx-wm inbound presets (managed; do not edit).',
             '# An accept here cannot override a drop in another base chain.',
             'table inet xxwm-inbound {',
             '    chain input {',
             '        type filter hook input priority filter; policy accept;']
    for name in sorted(allowed):
        preset = PRESETS[name]
        for port in preset['tcp']:
            lines.append('        tcp dport %s accept comment "xxwm %s";'
                         % (_nft_port(port), name))
        for port in preset['udp']:
            lines.append('        udp dport %s accept comment "xxwm %s";'
                         % (_nft_port(port), name))
    lines += ['    }', '}']
    return '\n'.join(lines) + '\n'


class FirewallSection(settings_page._Section):
    """Settings section: firewall status plus per-preset allow switches."""

    key = 'firewall'
    title = 'Firewall'

    def __init__(self) -> None:
        self._status = None
        self._toggle_btn = None
        self._preset_box = None
        self._note = None
        self._switches: dict[str, object] = {}
        self._updating = False
        self._state: dict | None = None

    def build(self, win) -> list:
        from settings_page import (_section_placeholder, _section_title,
                                   _outline_action)
        from gi.repository import Gtk

        label = _section_title(self.title)
        self._status = _section_placeholder('Checking firewall…')
        self._toggle_btn = _outline_action(
            'Turn on', lambda: self._on_toggle(win))
        self._preset_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=0)
        self._note = _section_placeholder('')
        self._render_presets(win, {})
        return [label, self._status, self._toggle_btn, self._preset_box,
                self._note]

    def refresh(self, win) -> None:
        try:
            import helper_client
            helper_client.get_firewall_state(
                lambda ok, state: self._show_state(
                    win, state if ok else None))
        except Exception:
            self._show_state(win, None)

    # ------------------------------------------------------------ behavior

    def _render_presets(self, win, allowed: dict) -> None:
        from settings_page import _replace_box_children
        from xx_ui import widgets

        rows = []
        names = list(PRESETS)
        for index, name in enumerate(names):
            preset = PRESETS[name]
            switch = widgets.make_switch(
                bool(allowed.get(name, False)),
                lambda active, n=name: self._on_preset(win, n, active))
            self._switches[name] = switch
            rows.append(widgets.setting_row(
                preset['title'], preset['detail'], switch,
                last=index == len(names) - 1))
        _replace_box_children(self._preset_box, rows)

    def _show_state(self, win, state) -> None:
        self._state = state if isinstance(state, dict) else None
        if self._preset_box is None:
            return
        allowed = (self._state or {}).get('allowed') or {}
        if not isinstance(allowed, dict):
            allowed = {}
        self._render_presets(win, allowed)
        if self._status is not None:
            try:
                self._status.set_text(self._status_text())
            except Exception:
                pass
        if self._toggle_btn is not None:
            try:
                child = self._toggle_btn.get_child()
                if child is not None:
                    child.set_label(
                        'Turn off' if self._is_on() else 'Turn on')
            except Exception:
                pass
        if self._note is not None:
            try:
                self._note.set_text(self._note_text())
            except Exception:
                pass

    def _is_on(self) -> bool:
        return bool(self._state and self._state.get('enabled'))

    def _status_text(self) -> str:
        state = self._state
        if not isinstance(state, dict):
            return 'Firewall status unavailable.'
        backend = state.get('backend', 'none')
        if backend == 'none':
            return ('Off — this system has no inbound firewall, '
                    'so all incoming connections are allowed.')
        if backend == 'ufw':
            return ('On (ufw).'
                    if state.get('enabled') else 'Off (ufw disabled).')
        if state.get('enabled'):
            return 'On (nftables presets installed).'
        return 'Off (no nftables presets installed).'

    def _note_text(self) -> str:
        state = self._state
        if not isinstance(state, dict):
            return ''
        if state.get('backend') == 'none':
            return ('Switches still apply: they program ufw rules and the '
                    'nftables drop-in on systems that have a firewall.')
        if state.get('foreign_drop'):
            return ('Another firewall is dropping incoming connections; '
                    'these switches cannot override its drops.')
        return ''

    def _on_preset(self, win, name: str, allowed: bool) -> None:
        try:
            import helper_client
        except ImportError as err:
            win._show_status(f'Could not change {name}: {err}')
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                win._show_status(f'Could not change {name}: {detail}')
            self.refresh(win)

        try:
            helper_client.set_inbound_preset(name, bool(allowed), _done)
        except Exception as err:
            win._show_status(f'Could not change {name}: {err}')

    def _on_toggle(self, win) -> None:
        if self._is_on():
            win.item_actions._show_action_menu(
                self._toggle_btn, 'Turn the firewall off?',
                [('Turn off', lambda: self._apply_enabled(win, False)),
                 ('Cancel', lambda: None)])
            return
        self._apply_enabled(win, True)

    def _apply_enabled(self, win, enabled: bool) -> None:
        try:
            import helper_client
        except ImportError as err:
            win._show_status(f'Could not change firewall: {err}')
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                win._show_status(f'Could not change firewall: {detail}')
            self.refresh(win)

        try:
            helper_client.set_firewall_enabled(bool(enabled), _done)
        except Exception as err:
            win._show_status(f'Could not change firewall: {err}')
