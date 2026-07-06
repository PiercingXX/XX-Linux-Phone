from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime

import gi

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from config import ShellConfig

_CSS = b"""
.lock-root {
    background: #000000;
    color: #f4f4f4;
}
.lock-root paned,
.lock-root paned > separator {
    background: transparent;
    background-color: transparent;
    min-height: 0;
    min-width: 0;
    border: none;
    padding: 0;
    margin: 0;
}
.lock-clock {
    font-size: 64pt;
    font-weight: 300;
    font-family: 'Space Mono', monospace;
    color: #f4f4f4;
}
.lock-date {
    font-size: 16pt;
    font-family: 'Space Mono', monospace;
    color: #9a9a9a;
}
.lock-dots {
    font-size: 22pt;
    letter-spacing: 0.4em;
    font-family: monospace;
    color: #f4f4f4;
    min-height: 48px;
}
.lock-error {
    font-size: 12pt;
    font-family: 'Space Mono', monospace;
    color: #ff6b6b;
}
.lock-lockout {
    font-size: 13pt;
    font-family: 'Space Mono', monospace;
    color: #ff9a3c;
    min-height: 24px;
}
.lock-key {
    font-size: 22pt;
    font-weight: 300;
    font-family: 'Space Mono', monospace;
    min-width: 110px;
    min-height: 88px;
    border-radius: 50%;
    background: #111111;
    color: #f4f4f4;
    border: none;
    padding: 0;
}
.lock-key:hover { background: #1e1e1e; }
.lock-key:disabled { opacity: 0.25; }
.lock-key.del {
    font-size: 18pt;
    background: transparent;
    color: #9a9a9a;
}
.lock-key.del:hover { background: #111111; }
.lock-sub {
    font-size: 8pt;
    color: #9a9a9a;
    margin-top: -2px;
}
.lock-unlock-btn {
    font-size: 14pt;
    font-family: 'Space Mono', monospace;
    min-height: 60px;
    min-width: 200px;
    border-radius: 30px;
    background: transparent;
    color: #f4f4f4;
    border: none;
    box-shadow: none;
}
.lock-unlock-btn:hover { background: rgba(255,255,255,0.08); }
.lock-unlock-btn:disabled { opacity: 0.25; }
.lock-fp-hint {
    font-size: 11pt;
    font-family: 'Space Mono', monospace;
    color: #5a5a5a;
    margin-top: 8px;
}
@keyframes shake {
    0%   { margin-left: 0; }
    20%  { margin-left: -14px; }
    40%  { margin-left: 14px; }
    60%  { margin-left: -8px; }
    80%  { margin-left: 8px; }
    100% { margin-left: 0; }
}
.shake { animation: shake 0.35s ease; }
"""

_KEYPAD: list[tuple[str, str]] = [
    ('1', ''),    ('2', 'ABC'), ('3', 'DEF'),
    ('4', 'GHI'), ('5', 'JKL'), ('6', 'MNO'),
    ('7', 'PQRS'),('8', 'TUV'), ('9', 'WXYZ'),
    ('',  ''),    ('0', ''),    ('←', ''),
]

_MAX_PIN = 8
_FAIL_THRESHOLD = 5     # wrong attempts before first lockout


def _lockout_secs(fail_count: int) -> int:
    """Seconds to lock the keypad after fail_count total wrong attempts."""
    if fail_count < _FAIL_THRESHOLD:
        return 0
    # 30s per attempt beyond the threshold, capped at 5 min
    return min((fail_count - _FAIL_THRESHOLD + 1) * 30, 300)


class LockScreen(Gtk.Window):
    def __init__(self, on_unlock: Callable[[], None]) -> None:
        super().__init__(title='PiercingOS')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self._on_unlock   = on_unlock
        self._config      = ShellConfig()
        self._pin         = ''
        self._fail_count  = 0
        self._lockout_src: int | None = None
        self._fp_running  = False
        self._keypad_btns: list[Gtk.Button] = []

        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

        self.set_child(self._build())
        self._refresh_clock()
        GLib.timeout_add_seconds(1, self._tick_clock)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class('lock-root')

        # Clock top third
        self.clock_label = Gtk.Label(xalign=0.5)
        self.clock_label.add_css_class('lock-clock')
        self.clock_label.set_hexpand(True)

        self.date_label = Gtk.Label(xalign=0.5)
        self.date_label.add_css_class('lock-date')
        self.date_label.set_hexpand(True)

        clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clock_box.set_hexpand(True)
        clock_box.append(self.clock_label)
        clock_box.append(self.date_label)

        top_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top_pane.set_hexpand(True)
        top_pane.set_vexpand(True)
        sp1 = Gtk.Box(); sp1.set_vexpand(True)
        sp2 = Gtk.Box(); sp2.set_vexpand(True)
        top_pane.append(sp1)
        top_pane.append(clock_box)
        top_pane.append(sp2)

        # PIN area
        self.dots_label = Gtk.Label(label='')
        self.dots_label.add_css_class('lock-dots')
        self.dots_label.set_halign(Gtk.Align.CENTER)

        self.error_label = Gtk.Label(label='')
        self.error_label.add_css_class('lock-error')
        self.error_label.set_halign(Gtk.Align.CENTER)

        self.lockout_label = Gtk.Label(label='')
        self.lockout_label.add_css_class('lock-lockout')
        self.lockout_label.set_halign(Gtk.Align.CENTER)

        keypad = Gtk.Grid(row_spacing=8, column_spacing=8)
        keypad.set_halign(Gtk.Align.CENTER)
        keypad.set_margin_top(8)
        self._keypad_grid = keypad

        for idx, (digit, sub) in enumerate(_KEYPAD):
            col, row = idx % 3, idx // 3
            if not digit:
                ph = Gtk.Box()
                ph.set_size_request(110, 88)
                keypad.attach(ph, col, row, 1, 1)
            elif digit == '←':
                btn = Gtk.Button(label='←')
                btn.add_css_class('lock-key')
                btn.add_css_class('del')
                btn.connect('clicked', self._on_delete)
                lp = Gtk.GestureLongPress.new()
                lp.connect('pressed', lambda *_: self._clear_pin())
                btn.add_controller(lp)
                keypad.attach(btn, col, row, 1, 1)
                self._keypad_btns.append(btn)
            else:
                btn = self._make_key(digit, sub)
                keypad.attach(btn, col, row, 1, 1)
                self._keypad_btns.append(btn)

        self._unlock_btn = Gtk.Button(label='Unlock')
        self._unlock_btn.add_css_class('lock-unlock-btn')
        self._unlock_btn.set_halign(Gtk.Align.CENTER)
        self._unlock_btn.set_margin_top(16)
        self._unlock_btn.connect('clicked', lambda _b: self._check_pin())
        self._keypad_btns.append(self._unlock_btn)

        self._fp_hint = Gtk.Label(label='')
        self._fp_hint.add_css_class('lock-fp-hint')
        self._fp_hint.set_halign(Gtk.Align.CENTER)
        self._update_fp_hint()

        pin_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pin_box.set_halign(Gtk.Align.CENTER)
        pin_box.append(self.dots_label)
        pin_box.append(self.error_label)
        pin_box.append(self.lockout_label)
        pin_box.append(keypad)
        pin_box.append(self._unlock_btn)
        pin_box.append(self._fp_hint)

        bot_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bot_pane.set_hexpand(True)
        bot_pane.set_vexpand(True)
        sp3 = Gtk.Box(); sp3.set_vexpand(True)
        sp4 = Gtk.Box(); sp4.set_vexpand(True)
        bot_pane.append(sp3)
        bot_pane.append(pin_box)
        bot_pane.append(sp4)

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_hexpand(True)
        paned.set_vexpand(True)
        paned.set_wide_handle(False)
        paned.set_resize_start_child(False)
        paned.set_resize_end_child(True)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_start_child(top_pane)
        paned.set_end_child(bot_pane)

        def _set_ratio(w: Gtk.Paned) -> None:
            def _apply() -> bool:
                h = w.get_height()
                if h > 0:
                    w.set_position(h // 3)
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            GLib.idle_add(_apply)
        paned.connect('realize', _set_ratio)

        root.append(paned)
        return root

    def _make_key(self, digit: str, sub: str) -> Gtk.Button:
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.append(Gtk.Label(label=digit))
        if sub:
            s = Gtk.Label(label=sub)
            s.add_css_class('lock-sub')
            inner.append(s)

        btn = Gtk.Button()
        btn.add_css_class('lock-key')
        btn.set_child(inner)
        btn.connect('clicked', self._on_digit, digit)
        return btn

    # ------------------------------------------------------------------
    # PIN input
    # ------------------------------------------------------------------

    def _on_digit(self, _btn: Gtk.Button, digit: str) -> None:
        if self._lockout_src is not None:
            return
        if len(self._pin) < _MAX_PIN:
            self._pin += digit
            self._refresh_dots()

    def _on_delete(self, _btn: Gtk.Button) -> None:
        if self._pin:
            self._pin = self._pin[:-1]
            self._refresh_dots()

    def _clear_pin(self) -> None:
        self._pin = ''
        self._refresh_dots()

    def _refresh_dots(self) -> None:
        self.dots_label.set_text('●' * len(self._pin))
        self.error_label.set_text('')

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def _check_pin(self) -> None:
        if self._lockout_src is not None:
            return
        if not self._pin:
            return
        if self._config.verify_pin(self._pin):
            self._unlock()
        else:
            self._fail_count += 1
            self._pin = ''
            self._refresh_dots()
            self._shake()
            secs = _lockout_secs(self._fail_count)
            if secs > 0:
                self.error_label.set_text('')
                self._start_lockout(secs)
            else:
                remaining = _FAIL_THRESHOLD - self._fail_count
                self.error_label.set_text(
                    f'Incorrect PIN — {remaining} attempt{"s" if remaining != 1 else ""} before lockout'
                )

    def _unlock(self) -> None:
        self._fail_count = 0
        if self._lockout_src is not None:
            GLib.source_remove(self._lockout_src)
            self._lockout_src = None
        self._on_unlock()
        self.set_visible(False)

    # ------------------------------------------------------------------
    # Lockout timer
    # ------------------------------------------------------------------

    def _start_lockout(self, secs: int) -> None:
        self._lockout_until = GLib.get_monotonic_time() + secs * 1_000_000
        self._set_keypad_sensitive(False)
        self._tick_lockout()

    def _tick_lockout(self) -> bool:
        remaining_us = self._lockout_until - GLib.get_monotonic_time()
        if remaining_us <= 0:
            self._lockout_src = None
            self._set_keypad_sensitive(True)
            self.lockout_label.set_text('')
            self.error_label.set_text('Enter your PIN')
            return GLib.SOURCE_REMOVE
        secs = (remaining_us + 999_999) // 1_000_000
        self.lockout_label.set_text(f'Try again in {secs}s')
        self._lockout_src = GLib.timeout_add(1000, self._tick_lockout)
        return GLib.SOURCE_REMOVE  # the re-registered timer handles continuation

    def _set_keypad_sensitive(self, sensitive: bool) -> None:
        for btn in self._keypad_btns:
            btn.set_sensitive(sensitive)

    # ------------------------------------------------------------------
    # Fingerprint unlock
    # ------------------------------------------------------------------

    def try_fingerprint_unlock(self) -> None:
        if not self.get_visible():
            return
        if self._lockout_src is not None:
            return
        if self._fp_running:
            return
        self._fp_running = True
        self._fp_hint.set_label('Checking fingerprint…')
        threading.Thread(target=self._fp_thread, daemon=True, name='fp-verify').start()

    def _fp_thread(self) -> None:
        try:
            user = os.environ.get('USER', os.environ.get('LOGNAME', 'user'))
            r = subprocess.run(
                ['fprintd-verify', '-f', user],
                timeout=10, capture_output=True,
            )
            matched = r.returncode == 0
        except FileNotFoundError:
            matched = False  # fprintd not installed
        except Exception:
            matched = False
        GLib.idle_add(self._fp_result, matched)

    def _fp_result(self, matched: bool) -> bool:
        self._fp_running = False
        if matched:
            self._unlock()
        else:
            self._update_fp_hint()
        return GLib.SOURCE_REMOVE

    def _update_fp_hint(self) -> None:
        try:
            subprocess.run(['fprintd-verify', '--help'],
                           capture_output=True, timeout=1)
            self._fp_hint.set_label('Touch fingerprint sensor to unlock')
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._fp_hint.set_label('')

    # ------------------------------------------------------------------
    # Shake + clock
    # ------------------------------------------------------------------

    def _shake(self) -> None:
        self.dots_label.add_css_class('shake')
        GLib.timeout_add(380, lambda: self.dots_label.remove_css_class('shake') or False)

    def _refresh_clock(self) -> None:
        now = datetime.now()
        self.clock_label.set_text(now.strftime('%H:%M'))
        self.date_label.set_text(now.strftime('%A, %d %b').replace(' 0', ' '))

    def _tick_clock(self) -> bool:
        self._refresh_clock()
        return True
