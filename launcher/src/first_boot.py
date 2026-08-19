from __future__ import annotations

import gi
import subprocess
from collections.abc import Callable

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk, Pango

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from config import THEME_PRESETS, ShellConfig

_WIZARD_CSS = b"""
.wizard-root {
    background: #000000;
    color: #f4f4f4;
}
.wizard-title {
    font-size: 22pt;
    font-weight: 300;
    color: #f4f4f4;
}
.wizard-subtitle {
    font-size: 11pt;
    color: #9a9a9a;
}
.wizard-label {
    font-size: 12pt;
    color: #f4f4f4;
}
.pin-dots {
    font-size: 18pt;
    letter-spacing: 0.3em;
    font-family: monospace;
    color: #f4f4f4;
    min-height: 36px;
}
.pin-key {
    font-size: 17pt;
    font-weight: 300;
    min-width: 80px;
    min-height: 64px;
    border-radius: 50%;
    background: #111111;
    color: #f4f4f4;
    border: none;
    padding: 0;
}
.pin-key:hover { background: #1e1e1e; }
.pin-key.del {
    font-size: 14pt;
    background: transparent;
    color: #9a9a9a;
}
.pin-key.del:hover { background: #111111; }
.pin-sub {
    font-size: 7pt;
    color: #9a9a9a;
    margin-top: -2px;
}
.wizard-next {
    font-size: 12pt;
    min-height: 48px;
    border-radius: 14px;
    background: #f4f4f4;
    color: #000000;
    border: none;
}
.wizard-next:hover {
    background: #e0e0e0;
}
.wizard-skip {
    font-size: 11pt;
    color: #9a9a9a;
}
.theme-row {
    font-size: 12pt;
    min-height: 44px;
    border-radius: 12px;
    padding: 0 16px;
}
.tz-list,
.tz-list row {
    background: transparent;
    color: inherit;
    font-size: 12pt;
}
.tz-list row {
    min-height: 40px;
    border-radius: 10px;
    padding: 0 12px;
}
.tz-list row:selected {
    background: alpha(currentColor, 0.18);
    color: inherit;
}
"""

# Per-preset swatch rows for the theme step, generated from the palette
_THEME_ROWS_CSS = ''.join(
    f'.theme-row-{p.key} {{'
    f' background: {p.background}; color: {p.foreground};'
    f' border: 2px solid {p.border}; }}'
    f'.theme-row-{p.key}.selected {{ border-color: {p.accent}; }}'
    for p in THEME_PRESETS.values()
).encode()

_TIMEZONES = [
    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver',
    'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Europe/Paris',
    'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Australia/Sydney',
]


def keyboard_step_available() -> bool:
    """True when squeekboard + the Colemak layouts are installed (18.3)."""
    import shutil
    from pathlib import Path
    if not shutil.which('squeekboard'):
        return False
    return any(
        path.exists() for path in (
            Path.home() / '.local' / 'share' / 'squeekboard' / 'keyboards',
            Path('/usr/share/xx-wm/squeekboard'),
        )
    )


class FirstBootWizard(Gtk.Window):
    """
    First-run setup (PIN, theme, timezone) followed by the usage walkthrough:
    an interactive gesture tour, shade / DnD & Focus / config-file pages, and
    a try-the-keyboard step when squeekboard is present. `tour_only=True`
    replays just the walkthrough (`xx-wm --welcome`).
    """

    def __init__(self, on_complete: Callable[[], None],
                 tour_only: bool = False) -> None:
        super().__init__(title='PiercingXX Setup')
        self._tour_only = tour_only

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            # Zone 0 so the OSK pushes the wizard up (keyboard tour page)
            LayerShell.set_exclusive_zone(self, 0)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self._on_complete = on_complete
        self._config = ShellConfig()
        self._pin_entered = ''
        self._pin_buf = ''
        self._confirm_buf = ''

        self._theme_provider = Gtk.CssProvider()

        provider = Gtk.CssProvider()
        provider.load_from_data(_WIZARD_CSS + _THEME_ROWS_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 4,
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )

        # Instant page switches — slide animations stutter on phone GPUs at
        # scale 3, and text-first means no transition chrome anyway
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)

        self._stack.add_named(self._build_welcome(), 'welcome')
        self._stack.add_named(self._build_pin_step(), 'pin')
        self._stack.add_named(self._build_pin_confirm_step(), 'pin_confirm')
        self._stack.add_named(self._build_theme_step(), 'theme')
        self._stack.add_named(self._build_timezone_step(), 'timezone')

        self._tour_pages = self._build_tour_pages()
        for name, widget in self._tour_pages:
            self._stack.add_named(widget, name)

        if tour_only:
            self._stack.set_visible_child_name(self._tour_pages[0][0])

        root = Gtk.Box()
        root.add_css_class('wizard-root')
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.append(self._stack)
        self.set_child(root)

    @staticmethod
    def is_needed() -> bool:
        from pathlib import Path
        config_path = Path.home() / '.config' / 'xx-wm' / 'config.json'
        return not config_path.exists()

    def _page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_start(24)
        page.set_margin_end(24)
        page.set_margin_bottom(32)
        return page

    def _title_block(self, title_text: str, sub_text: str | None = None) -> Gtk.Box:
        # Top margin drops the title clear of the camera notch so the block
        # sits centered in the top third of the screen
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(88)
        title = Gtk.Label(label=title_text)
        title.add_css_class('wizard-title')
        box.append(title)
        if sub_text:
            sub = Gtk.Label(label=sub_text, justify=Gtk.Justification.CENTER, wrap=True)
            sub.add_css_class('wizard-subtitle')
            box.append(sub)
        return box

    @staticmethod
    def _centered(*widgets: Gtk.Widget) -> Gtk.Box:
        """Vertically centered content zone between title block and footer."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_vexpand(True)
        box.set_valign(Gtk.Align.CENTER)
        for widget in widgets:
            box.append(widget)
        return box

    def _build_welcome(self) -> Gtk.Widget:
        page = self._page()

        next_btn = Gtk.Button(label='Get started')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', lambda _b: self._stack.set_visible_child_name('pin'))

        page.append(self._title_block(
            'PiercingXX', 'Let\'s set up your device.\nThis takes about a minute.'))
        page.append(self._centered())
        page.append(next_btn)
        return page

    def _build_pin_step(self) -> Gtk.Widget:
        page = self._page()

        # Fill width with centered text: halign CENTER + max_width_chars
        # would allocate ~1 char and ellipsize immediately ("…" on first key)
        self._pin_dots = Gtk.Label(label='')
        self._pin_dots.add_css_class('pin-dots')
        self._pin_dots.set_xalign(0.5)
        self._pin_dots.set_max_width_chars(1)
        self._pin_dots.set_hexpand(True)
        self._pin_dots.set_ellipsize(Pango.EllipsizeMode.START)
        self._pin_dots.set_margin_top(4)

        self._pin_hint = Gtk.Label(label='')
        self._pin_hint.add_css_class('wizard-subtitle')
        self._pin_hint.set_halign(Gtk.Align.CENTER)

        self._pin_buf = ''
        keypad = self._build_pin_keypad(
            on_digit=self._pin_add,
            on_delete=self._pin_del,
        )

        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', self._on_pin_next)

        skip_btn = Gtk.Button(label='Skip (no PIN)')
        skip_btn.add_css_class('flat')
        skip_btn.add_css_class('wizard-skip')
        skip_btn.connect('clicked', lambda _b: self._stack.set_visible_child_name('theme'))

        page.append(self._title_block(
            'Set a PIN', 'At least 4 digits. Used to unlock your device.'))
        page.append(self._centered(self._pin_dots, self._pin_hint, keypad))
        page.append(next_btn)
        page.append(skip_btn)
        return page

    def _build_pin_confirm_step(self) -> Gtk.Widget:
        page = self._page()

        self._confirm_dots = Gtk.Label(label='')
        self._confirm_dots.add_css_class('pin-dots')
        self._confirm_dots.set_xalign(0.5)
        self._confirm_dots.set_max_width_chars(1)
        self._confirm_dots.set_hexpand(True)
        self._confirm_dots.set_ellipsize(Pango.EllipsizeMode.START)
        self._confirm_dots.set_margin_top(4)

        self._confirm_buf = ''
        self._pin_error = Gtk.Label(label='')
        self._pin_error.add_css_class('wizard-subtitle')
        self._pin_error.set_halign(Gtk.Align.CENTER)

        keypad = self._build_pin_keypad(
            on_digit=self._confirm_add,
            on_delete=self._confirm_del,
        )

        confirm_btn = Gtk.Button(label='Set PIN')
        confirm_btn.add_css_class('wizard-next')
        confirm_btn.connect('clicked', self._on_pin_confirm)

        page.append(self._title_block('Confirm PIN', 'Enter the same PIN again.'))
        page.append(self._centered(self._confirm_dots, self._pin_error, keypad))
        page.append(confirm_btn)
        return page

    def _build_pin_keypad(self, on_digit: object, on_delete: object) -> Gtk.Grid:
        _KEYS = [
            ('1', ''),    ('2', 'ABC'), ('3', 'DEF'),
            ('4', 'GHI'), ('5', 'JKL'), ('6', 'MNO'),
            ('7', 'PQRS'),('8', 'TUV'), ('9', 'WXYZ'),
            ('',  ''),    ('0', ''),    ('←', ''),
        ]
        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        grid.set_halign(Gtk.Align.CENTER)
        for idx, (digit, sub) in enumerate(_KEYS):
            col, row = idx % 3, idx // 3
            if not digit:
                spacer = Gtk.Box()
                spacer.set_size_request(80, 64)
                grid.attach(spacer, col, row, 1, 1)
            elif digit == '←':
                btn = Gtk.Button(label='←')
                btn.add_css_class('pin-key')
                btn.add_css_class('del')
                btn.connect('clicked', lambda _b, fn=on_delete: fn())
                grid.attach(btn, col, row, 1, 1)
            else:
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                inner.set_halign(Gtk.Align.CENTER)
                inner.set_valign(Gtk.Align.CENTER)
                inner.append(Gtk.Label(label=digit))
                if sub:
                    s = Gtk.Label(label=sub)
                    s.add_css_class('pin-sub')
                    inner.append(s)
                btn = Gtk.Button()
                btn.add_css_class('pin-key')
                btn.set_child(inner)
                btn.connect('clicked', lambda _b, d=digit, fn=on_digit: fn(d))
                grid.attach(btn, col, row, 1, 1)
        return grid

    def _pin_add(self, digit: str) -> None:
        self._pin_buf += digit
        self._pin_dots.set_text('●' * len(self._pin_buf))
        if len(self._pin_buf) >= 4:
            self._pin_hint.set_text('')

    def _pin_del(self) -> None:
        if self._pin_buf:
            self._pin_buf = self._pin_buf[:-1]
            self._pin_dots.set_text('●' * len(self._pin_buf))

    def _confirm_add(self, digit: str) -> None:
        self._confirm_buf += digit
        self._confirm_dots.set_text('●' * len(self._confirm_buf))

    def _confirm_del(self) -> None:
        if self._confirm_buf:
            self._confirm_buf = self._confirm_buf[:-1]
            self._confirm_dots.set_text('●' * len(self._confirm_buf))

    def _build_theme_step(self) -> Gtk.Widget:
        page = self._page()

        # One tappable swatch row per preset — a dropdown popover does not
        # open reliably inside a layer-shell overlay
        self._selected_theme = next(iter(THEME_PRESETS))
        self._theme_rows: dict[str, Gtk.Button] = {}
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for key, preset in THEME_PRESETS.items():
            row = Gtk.Button(label=preset.name)
            row.add_css_class('theme-row')
            row.add_css_class(f'theme-row-{key}')
            row.connect('clicked', self._on_theme_row, key)
            self._theme_rows[key] = row
            rows.append(row)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(rows)

        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', self._on_theme_next)

        page.append(self._title_block('Pick a theme'))
        page.append(scroller)
        page.append(next_btn)
        self._on_theme_row(None, self._selected_theme)
        return page

    def _build_timezone_step(self) -> Gtk.Widget:
        page = self._page()

        self._tz_list = Gtk.ListBox()
        self._tz_list.add_css_class('tz-list')
        self._tz_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        for tz in _TIMEZONES:
            label = Gtk.Label(label=tz, xalign=0)
            self._tz_list.append(label)
        self._tz_list.select_row(self._tz_list.get_row_at_index(0))

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self._tz_list)

        finish_btn = Gtk.Button(label='Done')
        finish_btn.add_css_class('wizard-next')
        finish_btn.connect('clicked', self._on_finish)

        page.append(self._title_block('Timezone'))
        page.append(scroller)
        page.append(finish_btn)
        return page

    # ------------------------------------------------------------------
    # Walkthrough (Workstream 18) — gesture tour + info pages
    # ------------------------------------------------------------------

    def _build_tour_pages(self) -> list[tuple[str, Gtk.Widget]]:
        pages: list[tuple[str, Gtk.Widget]] = [
            ('tour_swipe_up', self._gesture_page(
                'Swipe up', 'Opens the app drawer — every app, searchable.\n\nTry it now.',
                kind='swipe_up')),
            ('tour_swipe_down', self._gesture_page(
                'Swipe down', 'Opens notifications and quick settings.\n\nTry it now.',
                kind='swipe_down')),
            ('tour_swipe_side', self._gesture_page(
                'Swipe sideways', 'Launches your side apps — camera on the '
                'right out of the box, both configurable.\n\nTry it now.',
                kind='swipe_side')),
            ('info_shade', self._info_page(
                'Shade & quick settings',
                'Swipe down anytime: WiFi, Bluetooth, torch and more, brightness '
                'and volume, your notifications, and an inline calendar behind '
                'the date.')),
            ('info_focus', self._info_page(
                'Do Not Disturb & Focus',
                'DnD silences everything except starred contacts and repeat '
                'callers.\n\nFocus pauses distracting apps and holds their '
                'notifications until you are done. Both live in quick settings.')),
        ]
        if keyboard_step_available():
            pages.append(('tour_keyboard', self._keyboard_page()))
        pages.append(('info_files', self._final_page()))
        return pages

    def _advance_tour(self) -> None:
        names = [name for name, _w in self._tour_pages]
        current = self._stack.get_visible_child_name()
        if current not in names:
            self._stack.set_visible_child_name(names[0])
            return
        idx = names.index(current)
        if idx + 1 < len(names):
            self._stack.set_visible_child_name(names[idx + 1])
        else:
            self._finish_tour()

    def _finish_tour(self) -> None:
        self._on_complete()
        GLib.idle_add(self.close)

    def _tour_scaffold(self, title_text: str, body_text: str) -> tuple[Gtk.Box, Gtk.Box]:
        page = self._page()
        page.append(self._title_block(title_text, body_text))
        page.append(self._centered())

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.append(footer)
        return page, footer

    def _skip_button(self) -> Gtk.Button:
        skip = Gtk.Button(label='Skip')
        skip.add_css_class('flat')
        skip.add_css_class('wizard-skip')
        skip.connect('clicked', lambda _b: self._advance_tour())
        return skip

    def _gesture_page(self, title_text: str, body_text: str, kind: str) -> Gtk.Widget:
        page, footer = self._tour_scaffold(title_text, body_text)
        footer.append(self._skip_button())

        if kind == 'long_press':
            gesture = Gtk.GestureLongPress.new()
            gesture.set_touch_only(False)
            gesture.connect('pressed', lambda _g, _x, _y: self._advance_tour())
            page.add_controller(gesture)
        else:
            swipe = Gtk.GestureSwipe.new()
            swipe.set_touch_only(False)

            def _on_swipe(_g: Gtk.GestureSwipe, vx: float, vy: float,
                          k: str = kind) -> None:
                if k == 'swipe_up' and vy < -300 and abs(vy) > abs(vx):
                    self._advance_tour()
                elif k == 'swipe_down' and vy > 300 and abs(vy) > abs(vx):
                    self._advance_tour()
                elif k == 'swipe_side' and abs(vx) > 300 and abs(vx) > abs(vy):
                    self._advance_tour()

            swipe.connect('swipe', _on_swipe)
            page.add_controller(swipe)
        return page

    def _info_page(self, title_text: str, body_text: str) -> Gtk.Widget:
        page, footer = self._tour_scaffold(title_text, body_text)
        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', lambda _b: self._advance_tour())
        footer.append(next_btn)
        return page

    def _keyboard_page(self) -> Gtk.Widget:
        page, footer = self._tour_scaffold(
            'Try the keyboard',
            'PiercingXX ships a Colemak layout. Tap the field — the keyboard '
            'appears whenever you need to type.')
        entry = Gtk.Entry()
        entry.set_placeholder_text('Type something…')
        # The centered content zone sits between the title block and footer
        page.get_first_child().get_next_sibling().append(entry)
        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', lambda _b: self._advance_tour())
        footer.append(next_btn)
        return page

    def _final_page(self) -> Gtk.Widget:
        page, footer = self._tour_scaffold(
            'Make it yours',
            'Long-press the home screen to edit your slots.\n\nEvery shell '
            'preference lives in ~/.config/xx-wm/ — edit it in a '
            'terminal and the shell reloads live. The full reference is '
            'docs/config.md.\n\nReplay this tour anytime with:\n'
            'xx-wm --welcome')
        done_btn = Gtk.Button(label='Done')
        done_btn.add_css_class('wizard-next')
        done_btn.connect('clicked', lambda _b: self._finish_tour())
        footer.append(done_btn)
        return page

    def _on_theme_row(self, _btn: Gtk.Button | None, theme_key: str) -> None:
        self._selected_theme = theme_key
        for key, row in self._theme_rows.items():
            if key == theme_key:
                row.add_css_class('selected')
            else:
                row.remove_css_class('selected')
        preset = THEME_PRESETS[theme_key]
        css = (
            f'.wizard-root {{ background: {preset.background}; color: {preset.foreground}; }}'
            f'.wizard-title {{ color: {preset.foreground}; }}'
            f'.wizard-subtitle {{ color: {preset.muted}; }}'
            f'.wizard-next {{ background: {preset.accent}; color: {preset.background}; }}'
            f'.pin-entry {{ background: {preset.surface}; color: {preset.foreground}; border-color: {preset.border}; }}'
        ).encode()
        self._theme_provider.load_from_data(css)

    def _on_pin_next(self, _btn: Gtk.Widget) -> None:
        if len(self._pin_buf) < 4:
            self._pin_dots.add_css_class('error')
            self._pin_hint.set_text('PIN must be at least 4 digits')
            return
        self._pin_dots.remove_css_class('error')
        self._pin_entered = self._pin_buf
        self._confirm_buf = ''
        self._confirm_dots.set_text('')
        self._pin_error.set_text('')
        self._stack.set_visible_child_name('pin_confirm')

    def _on_pin_confirm(self, _btn: Gtk.Widget) -> None:
        if self._confirm_buf != self._pin_entered:
            self._pin_error.set_text('PINs do not match — try again')
            self._confirm_buf = ''
            self._confirm_dots.set_text('')
            return
        self._config.set_pin(self._pin_entered)
        self._stack.set_visible_child_name('theme')

    def _on_theme_next(self, _btn: Gtk.Button) -> None:
        self._config.set_theme(self._selected_theme)
        self._stack.set_visible_child_name('timezone')

    def _on_finish(self, _btn: Gtk.Button) -> None:
        row = self._tz_list.get_selected_row()
        tz = _TIMEZONES[row.get_index() if row is not None else 0]
        # Setup is durable from here even if the walkthrough is skipped
        self._config.save()
        self._advance_tour()
        # timedatectl blocks on polkit and gsettings can stall — never on the
        # UI thread; the wizard moves on while these land in the background
        import threading
        threading.Thread(
            target=self._apply_system_settings, args=(tz,), daemon=True).start()

    @staticmethod
    def _apply_system_settings(tz: str) -> None:
        try:
            subprocess.run(['timedatectl', 'set-timezone', tz],
                           check=True, timeout=15, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass
        # Colemak is the shipped keyboard layout; squeekboard and the Phosh
        # OSK both follow org.gnome.desktop.input-sources
        try:
            subprocess.run(
                ['gsettings', 'set', 'org.gnome.desktop.input-sources',
                 'sources', "[('xkb', 'us+colemak')]"],
                timeout=15, capture_output=True,
            )
        except (OSError, subprocess.SubprocessError):
            pass
