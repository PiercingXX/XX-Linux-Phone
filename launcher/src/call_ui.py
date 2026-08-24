from __future__ import annotations

import gi
from collections.abc import Callable
from datetime import datetime

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk
from config import DESTRUCTIVE_TINT_BG, DANGER_RED, ON_DANGER_FG, ShellConfig, ThemePreset

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell


def theme_css(preset: ThemePreset) -> str:
    return f"""
.call-root {{
    background: {preset.background};
    color: {preset.foreground};
}}
.call-caller {{
    font-size: 28pt;
    font-weight: 300;
    color: {preset.foreground};
}}
.call-number {{
    font-size: 14pt;
    color: {preset.muted};
}}
.call-status {{
    font-size: 12pt;
    color: {preset.muted};
    letter-spacing: 0.1em;
}}
.call-timer {{
    font-size: 18pt;
    font-weight: 300;
    color: {preset.foreground};
    font-variant-numeric: tabular-nums;
}}
.call-btn {{
    font-size: 12pt;
    min-width: 100px;
    min-height: 72px;
    border-radius: 20px;
    border: none;
    padding: 0;
}}
.btn-accept {{
    background: {preset.accent};
    color: {preset.background};
}}
.btn-accept:hover {{ background: mix({preset.accent}, {preset.background}, 0.85); }}
.btn-decline {{
    background: {preset.surface};
    color: {preset.muted};
}}
.btn-decline:hover {{ background: {preset.surface_alt}; }}
.btn-hangup {{
    background: {DANGER_RED};
    color: {ON_DANGER_FG};
}}
.btn-hangup:hover {{ background: {DESTRUCTIVE_TINT_BG}; }}
.btn-mute, .btn-speaker {{
    background: {preset.surface};
    color: {preset.foreground};
}}
.btn-mute.active, .btn-speaker.active {{
    background: {preset.accent};
    color: {preset.background};
}}
.btn-mute:hover, .btn-speaker:hover {{ background: {preset.surface_alt}; }}
.call-bar-root {{
    background: {preset.surface};
    color: {preset.foreground};
    padding: 8px 16px;
}}
.call-bar-label {{
    font-size: 11pt;
    color: {preset.foreground};
}}
"""


def _set_audio_route(earpiece: bool) -> None:
    """
    Switch PulseAudio/PipeWire card profile for in-call audio routing.
    Tries common UCM profile names used by Linux phone distributions.
    Failures are silent — not every device/OS uses the same profile names.
    """
    import subprocess

    def _pactl(*args: str) -> bool:
        try:
            subprocess.Popen(['pactl', *args], close_fds=True)
            return True
        except FileNotFoundError:
            return False

    if earpiece:
        # Try profiles in order of likelihood on Linux phones
        for profile in ('Voice Call', 'voice-call', 'HiFi Voice Call'):
            _pactl('set-card-profile', '0', profile)
        # Try explicit earpiece sink port (PipeWire / UCM)
        for port in ('output-earpiece', '[Out] Earpiece', 'Earpiece'):
            _pactl('set-sink-port', '@DEFAULT_SINK@', port)
    else:
        # Restore normal speaker/headphone output
        for profile in ('HiFi', 'hifi', 'A2DP Sink'):
            _pactl('set-card-profile', '0', profile)
        for port in ('output-speaker', '[Out] Speaker', 'Speaker'):
            _pactl('set-sink-port', '@DEFAULT_SINK@', port)


def _resolve_call_path() -> str | None:
    from modem_monitor import active_call_path
    return active_call_path()


# Completion contract shared with modem_monitor: (success, error_message).
CallResultCallback = Callable[[bool, str | None], None]
# Injectable transport seam: start the async action, deliver via callback.
CallActionFn = Callable[[str, CallResultCallback], None]


def _log_action_failure(action: str, call_path: str | None,
                        error: str | None = None) -> None:
    from shell_log import get_logger
    detail = f': {error}' if error else ''
    get_logger('call_ui').warning('failed to %s call (path=%s)%s', action, call_path, detail)


def _default_accept(call_path: str, on_done: CallResultCallback) -> None:
    from modem_monitor import accept_call
    accept_call(call_path, on_done)


def _default_hangup(call_path: str, on_done: CallResultCallback) -> None:
    from modem_monitor import hangup_call
    hangup_call(call_path, on_done)


class CallBar(Gtk.Window):
    """Persistent in-call bar shown at the top of home screen during an active call."""

    def __init__(self, on_expand: Callable[[], None],
                 config: ShellConfig | None = None) -> None:
        super().__init__(title='PiercingXX Call Bar')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.TOP)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, False)
            LayerShell.set_exclusive_zone(self, 48)
        else:
            self.set_default_size(420, 48)

        self._on_expand = on_expand
        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()
        self._shown = False
        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )
        self.apply_theme()
        self.set_child(self._build())

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this bar's display-level sheet. The shell window passes
        the freshly resolved preset on hot-reload fan-out; standalone falls
        back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._config.theme)
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        root.add_css_class('call-bar-root')

        self._label = Gtk.Label(label='In call', xalign=0)
        self._label.add_css_class('call-bar-label')
        self._label.set_hexpand(True)

        expand_btn = Gtk.Button(label='↓')
        expand_btn.add_css_class('flat')
        expand_btn.add_css_class('call-bar-label')
        expand_btn.connect('clicked', lambda _b: self._on_expand())

        root.append(self._label)
        root.append(expand_btn)
        return root

    def show_bar(self, number: str) -> None:
        text = number or 'In call'
        if self._shown and self._label.get_text() == text:
            return
        self._label.set_text(text)
        if not self._shown:
            self._shown = True
            self.present()

    def hide_bar(self) -> None:
        if not self._shown:
            return
        self._shown = False
        self.hide()

    def update(self, caller: str, timer_text: str) -> None:
        self._label.set_text(f'{caller}  ·  {timer_text}')


class CallUI(Gtk.Window):
    """
    Full-screen call surface.
    - Incoming call: show_incoming(caller, number) — accept/decline buttons
    - Active call: show_active(caller, number) — timer, mute, speaker, hangup
    """

    def __init__(
        self,
        on_accept: Callable[[], None] | None = None,
        on_decline: Callable[[], None] | None = None,
        on_hangup: Callable[[], None] | None = None,
        accept_fn: CallActionFn | None = None,
        hangup_fn: CallActionFn | None = None,
        config: ShellConfig | None = None,
    ) -> None:
        super().__init__(title='PiercingXX Call')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self._on_accept = on_accept or (lambda: None)
        self._on_decline = on_decline or (lambda: None)
        self._on_hangup = on_hangup or (lambda: None)
        self._accept_fn = accept_fn or _default_accept
        self._hangup_fn = hangup_fn or _default_hangup
        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()
        self._incoming_call_path: str | None = None
        self._pending_action: str | None = None
        self._current_caller = ''
        self._current_number = ''
        self._muted = False
        self._speakerphone = False
        self._call_start: datetime | None = None
        self._timer_id: int | None = None

        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )
        self.apply_theme()

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self._stack.add_named(self._build_incoming(), 'incoming')
        self._stack.add_named(self._build_active(), 'active')

        root = Gtk.Box()
        root.add_css_class('call-root')
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.append(self._stack)
        self.set_child(root)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this surface's display-level sheet. The shell window
        passes the freshly resolved preset on hot-reload fan-out; standalone
        falls back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._config.theme)
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build_incoming(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_top(100)
        page.set_margin_start(32)
        page.set_margin_end(32)

        self._inc_status = Gtk.Label(label='INCOMING CALL', xalign=0)
        self._inc_status.add_css_class('call-status')

        self._inc_caller = Gtk.Label(label='', xalign=0)
        self._inc_caller.add_css_class('call-caller')

        self._inc_number = Gtk.Label(label='', xalign=0)
        self._inc_number.add_css_class('call-number')

        spacer = Gtk.Box()
        spacer.set_vexpand(True)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        btn_row.set_halign(Gtk.Align.CENTER)
        btn_row.set_margin_bottom(64)

        decline_btn = Gtk.Button(label='Decline')
        decline_btn.add_css_class('call-btn')
        decline_btn.add_css_class('btn-decline')
        decline_btn.connect('clicked', self._on_decline_clicked)

        accept_btn = Gtk.Button(label='Accept')
        accept_btn.add_css_class('call-btn')
        accept_btn.add_css_class('btn-accept')
        accept_btn.connect('clicked', self._on_accept_clicked)

        btn_row.append(decline_btn)
        btn_row.append(accept_btn)

        page.append(self._inc_status)
        page.append(self._inc_caller)
        page.append(self._inc_number)
        page.append(spacer)
        page.append(btn_row)
        return page

    def _build_active(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_top(100)
        page.set_margin_start(32)
        page.set_margin_end(32)

        self._act_caller = Gtk.Label(label='', xalign=0)
        self._act_caller.add_css_class('call-caller')

        self._act_number = Gtk.Label(label='', xalign=0)
        self._act_number.add_css_class('call-number')

        self._act_timer = Gtk.Label(label='0:00', xalign=0)
        self._act_timer.add_css_class('call-timer')
        self._act_timer.set_margin_top(12)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)

        ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        ctrl_row.set_halign(Gtk.Align.CENTER)
        ctrl_row.set_margin_bottom(20)

        self._mute_btn = Gtk.ToggleButton(label='Mute')
        self._mute_btn.add_css_class('call-btn')
        self._mute_btn.add_css_class('btn-mute')
        self._mute_btn.connect('toggled', self._on_mute_toggled)

        self._speaker_btn = Gtk.ToggleButton(label='Speaker')
        self._speaker_btn.add_css_class('call-btn')
        self._speaker_btn.add_css_class('btn-speaker')
        self._speaker_btn.connect('toggled', self._on_speaker_toggled)

        ctrl_row.append(self._mute_btn)
        ctrl_row.append(self._speaker_btn)

        hangup_btn = Gtk.Button(label='Hang up')
        hangup_btn.add_css_class('call-btn')
        hangup_btn.add_css_class('btn-hangup')
        hangup_btn.set_margin_bottom(48)
        hangup_btn.set_halign(Gtk.Align.CENTER)
        hangup_btn.connect('clicked', self._on_hangup_clicked)

        page.append(self._act_caller)
        page.append(self._act_number)
        page.append(self._act_timer)
        page.append(spacer)
        page.append(ctrl_row)
        page.append(hangup_btn)
        return page

    def show_incoming(self, caller: str, number: str, call_path: str | None = None) -> None:
        self._incoming_call_path = call_path or _resolve_call_path()
        self._pending_action = None
        self._current_caller = caller
        self._current_number = number
        self._inc_caller.set_text(caller or number)
        self._inc_number.set_text(number if caller else '')
        self._stack.set_visible_child_name('incoming')
        self.present()

    def show_active(self, caller: str, number: str) -> None:
        self._current_caller = caller
        self._current_number = number
        # Attached mid-call (answered before the UI appeared, or shell
        # started during the call): resolve the path so hangup can work.
        self._incoming_call_path = self._incoming_call_path or _resolve_call_path()
        self._pending_action = None
        self._act_caller.set_text(caller or number)
        self._act_number.set_text(number if caller else '')
        self._call_start = datetime.now()
        self._stack.set_visible_child_name('active')
        self.present()
        if self._timer_id is None:
            self._timer_id = GLib.timeout_add_seconds(1, self._tick_timer)
        _set_audio_route(earpiece=True)

    def end_call(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._incoming_call_path = None
        # Any in-flight accept/hangup reply is now stale; drop the guard so
        # the next call starts with responsive buttons.
        self._pending_action = None
        _set_audio_route(earpiece=False)
        self.hide()

    def _tick_timer(self) -> bool:
        if self._call_start:
            elapsed = int((datetime.now() - self._call_start).total_seconds())
            mins, secs = divmod(elapsed, 60)
            self._act_timer.set_text(f'{mins}:{secs:02d}')
        return True

    def _begin_call_action(
        self,
        action: str,
        fn: CallActionFn,
        finish: Callable[[str, bool, str | None], None],
    ) -> None:
        """
        Dispatch an async call action with double-fire + staleness guards.

        - One action in flight at a time: clicks while ``_pending_action`` is
          set are ignored, so a wedged bus can't stack transitions.
        - The completion callback only acts if the tracked call is still the
          one that was dispatched (a remote hangup mid-flight invalidates it).
        """
        if self._pending_action is not None:
            return
        path = self._incoming_call_path
        if path is None:
            _log_action_failure(action, None, 'no active call')
            return
        self._pending_action = action

        def _on_done(success: bool, error: str | None) -> None:
            self._pending_action = None
            if self._incoming_call_path != path:
                return  # call ended/replaced while the request was in flight
            finish(path, success, error)

        fn(path, _on_done)

    def _on_accept_clicked(self, _btn: Gtk.Button) -> None:
        self._begin_call_action('accept', self._accept_fn, self._finish_accept)

    def _finish_accept(self, call_path: str, success: bool, error: str | None) -> None:
        if not success:
            _log_action_failure('accept', call_path, error)
            return
        self._on_accept()
        self.show_active(self._current_caller, self._current_number)

    def _on_decline_clicked(self, _btn: Gtk.Button) -> None:
        self._begin_call_action('decline', self._hangup_fn, self._finish_decline)

    def _finish_decline(self, call_path: str, success: bool, error: str | None) -> None:
        if not success:
            _log_action_failure('decline', call_path, error)
            return
        self._on_decline()
        self._incoming_call_path = None
        self.hide()

    def _on_hangup_clicked(self, _btn: Gtk.Button) -> None:
        self._begin_call_action('hang up', self._hangup_fn, self._finish_hangup)

    def _finish_hangup(self, call_path: str, success: bool, error: str | None) -> None:
        if not success:
            _log_action_failure('hang up', call_path, error)
            return
        self._on_hangup()
        self.end_call()

    def _on_mute_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._muted = btn.get_active()
        self._set_microphone_mute(self._muted)

    def _on_speaker_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._speakerphone = btn.get_active()
        self._set_speakerphone(self._speakerphone)

    def _set_microphone_mute(self, muted: bool) -> None:
        import subprocess
        try:
            subprocess.Popen(['pactl', 'set-source-mute', '@DEFAULT_SOURCE@', '1' if muted else '0'])
        except FileNotFoundError:
            pass

    def _set_speakerphone(self, enabled: bool) -> None:
        _set_audio_route(earpiece=not enabled)
