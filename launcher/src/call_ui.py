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

from gi.repository import Gdk, GLib, Gio, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

_CALL_CSS = b"""
.call-root {
    background: #000000;
    color: #f4f4f4;
    font-family: 'Space Mono', monospace;
}
.call-caller {
    font-size: 28pt;
    font-weight: 300;
    color: #f4f4f4;
}
.call-number {
    font-size: 14pt;
    color: #9a9a9a;
}
.call-status {
    font-size: 12pt;
    color: #9a9a9a;
    letter-spacing: 0.1em;
}
.call-timer {
    font-size: 18pt;
    font-weight: 300;
    color: #f4f4f4;
    font-variant-numeric: tabular-nums;
}
.call-btn {
    font-size: 12pt;
    font-family: 'Space Mono', monospace;
    min-width: 100px;
    min-height: 72px;
    border-radius: 20px;
    border: none;
    padding: 0;
}
.btn-accept {
    background: #f4f4f4;
    color: #000000;
}
.btn-accept:hover { background: #e0e0e0; }
.btn-decline {
    background: #1a1a1a;
    color: #9a9a9a;
}
.btn-decline:hover { background: #242424; }
.btn-hangup {
    background: #1a1a1a;
    color: #ff6b6b;
}
.btn-hangup:hover { background: #2a1010; }
.btn-mute, .btn-speaker {
    background: #1a1a1a;
    color: #f4f4f4;
}
.btn-mute.active, .btn-speaker.active {
    background: #f4f4f4;
    color: #000000;
}
.btn-mute:hover, .btn-speaker:hover { background: #242424; }
.call-bar-root {
    background: #111111;
    color: #f4f4f4;
    padding: 8px 16px;
}
.call-bar-label {
    font-size: 11pt;
    font-family: 'Space Mono', monospace;
    color: #f4f4f4;
}
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


class CallBar(Gtk.Window):
    """Persistent in-call bar shown at the top of home screen during an active call."""

    def __init__(self, on_expand: Callable[[], None]) -> None:
        super().__init__(title='PiercingOS Call Bar')

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
        provider = Gtk.CssProvider()
        provider.load_from_data(_CALL_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )
        self.set_child(self._build())

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
    ) -> None:
        super().__init__(title='PiercingOS Call')

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
        self._muted = False
        self._speakerphone = False
        self._call_start: datetime | None = None
        self._timer_id: int | None = None

        provider = Gtk.CssProvider()
        provider.load_from_data(_CALL_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self._stack.add_named(self._build_incoming(), 'incoming')
        self._stack.add_named(self._build_active(), 'active')

        root = Gtk.Box()
        root.add_css_class('call-root')
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.append(self._stack)
        self.set_child(root)

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

    def show_incoming(self, caller: str, number: str) -> None:
        self._inc_caller.set_text(caller or number)
        self._inc_number.set_text(number if caller else '')
        self._stack.set_visible_child_name('incoming')
        self.present()

    def show_active(self, caller: str, number: str) -> None:
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
        _set_audio_route(earpiece=False)
        self.hide()

    def _tick_timer(self) -> bool:
        if self._call_start:
            elapsed = int((datetime.now() - self._call_start).total_seconds())
            mins, secs = divmod(elapsed, 60)
            self._act_timer.set_text(f'{mins}:{secs:02d}')
        return True

    def _on_accept_clicked(self, _btn: Gtk.Button) -> None:
        self._on_accept()

    def _on_decline_clicked(self, _btn: Gtk.Button) -> None:
        self._on_decline()
        self.hide()

    def _on_hangup_clicked(self, _btn: Gtk.Button) -> None:
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
