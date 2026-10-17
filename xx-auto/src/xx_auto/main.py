"""xx-auto drive screen: MPRIS now-playing card, tiles, settings, sheets.

Headless-safe import: Gtk/Adw load lazily so pure tests never need a
display. Worker threads own every D-Bus round trip; results post back
with GLib.idle_add.
"""

from __future__ import annotations

import os
import threading

APP_CSS = """
.xx-auto-title { font-size: 40px; font-weight: 300; }
.xx-auto-body { font-size: 24px; font-weight: 300; }
.xx-auto-time { font-size: 24px; font-weight: 300; font-feature-settings: "tnum"; }
.xx-auto-target { min-width: 72px; min-height: 72px; padding: 2px; }
.xx-auto-play { min-width: 88px; min-height: 88px; border-radius: 44px; padding: 2px; }
.xx-auto-tile { min-width: 140px; min-height: 80px; padding: 4px; }
.xx-auto-title.xx-auto-compact-title { font-size: 30px; }
.xx-auto-play.xx-auto-compact-play { min-width: 72px; min-height: 72px; border-radius: 36px; }
"""


def _require_gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: F401

    return Adw, Gdk, Gio, GLib, Gtk, Pango


def _label(Gtk, Pango, text, cls, size_cls=None, chars=20):
    line = Gtk.Label(label=text)
    line.add_css_class(cls)
    if size_cls:
        line.add_css_class(size_cls)
    line.set_xalign(0.0)
    line.set_max_width_chars(chars)
    line.set_ellipsize(Pango.EllipsizeMode.END)
    return line


def _fit(label, Pango, text, chars) -> None:
    label.set_text(text)
    if len(text) > chars:
        label.set_ellipsize(Pango.EllipsizeMode.END)
    else:
        label.set_ellipsize(Pango.EllipsizeMode.NONE)


def _wrap(Gtk, Pango, text, cls, size_cls=None):
    line = Gtk.Label(label=text)
    line.add_css_class(cls)
    if size_cls:
        line.add_css_class(size_cls)
    line.set_xalign(0.0)
    line.set_wrap(True)
    line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    line.set_max_width_chars(24)
    return line


class DriveSnapshot:
    """What the drive page shows; live MPRIS or seeded demo data."""

    def __init__(self, tracks=None, installed=None, maps_title="Maps",
                 maps_sub="xx-maps", note=None) -> None:
        from .mpris import sort_players

        self.tracks = sort_players(list(tracks or []))
        self.installed = dict(installed or {})
        self.maps_title = maps_title
        self.maps_sub = maps_sub
        self.note = note


def _active_index(tracks) -> int | None:
    from .session_pick import PlayerState, active

    return active([PlayerState(t.is_playing, t.has_item) for t in tracks])


class AutoWindow:
    """Main window; built inside an Adw.ApplicationWindow."""

    def __init__(self, app, settings) -> None:
        Adw, Gdk, Gio, GLib, Gtk, Pango = _require_gtk()
        self._Adw, self._Gdk, self._Gio = Adw, Gdk, Gio
        self._GLib, self._Gtk, self._Pango = GLib, Gtk, Pango
        self.app = app
        self.settings = settings
        self.snapshot = DriveSnapshot()
        self._favourites: list = []
        self._fav_note: str | None = None
        self._players_rows: list = []
        self._car_devices: list = []
        self._inhibit_cookie = 0
        self._poll_timer = 0

        import xx_ui

        self._xx_ui = xx_ui
        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-auto")
        self.win.set_default_size(360, 720)
        self._apply_theme()
        self._load_app_css()
        try:
            self._theme_unsub = xx_ui.on_change(self._on_suite_theme)
        except Exception:
            self._theme_unsub = None

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True)
        self._build_drive()
        self._build_settings()
        self._build_sheet_players()
        self._build_sheet_favourites()
        self._build_sheet_car()
        self._build_sheet_licenses()
        self.win.set_content(self.stack)
        self.show("drive")
        use_landscape = not settings.follow_rotation
        self.set_landscape(use_landscape)
        if settings.keep_screen_on:
            self.win.connect("map", lambda *_: self._update_inhibit())
            self.win.connect("unmap", lambda *_: self._update_inhibit())
        self._start_poll()

    # -- theme ------------------------------------------------------
    def _apply_theme(self) -> None:
        try:
            import xx_ui
            from xx_ui import theme as theme_mod

            if self.settings.always_ink:
                suite = theme_mod.read_config()
                preset = theme_mod.THEME_PRESETS["amoled"]
                active = theme_mod.Theme(
                    key="amoled", background=preset.background,
                    surface=preset.surface, surface_alt=preset.surface_alt,
                    border=preset.border, foreground=preset.foreground,
                    muted=preset.muted, accent=preset.accent,
                    font_family=theme_mod.resolve_font_family(suite),
                    scale=theme_mod.resolve_scale(suite), dark=True)
                xx_ui.css.install(active)
            else:
                xx_ui.apply(self.win)
        except Exception:
            pass

    def _on_suite_theme(self, _theme) -> None:
        if self.settings.always_ink:
            self._apply_theme()

    def _load_app_css(self) -> None:
        try:
            Gtk = self._Gtk
            Gdk = self._Gdk
            provider = Gtk.CssProvider()
            provider.load_from_data(APP_CSS.encode("utf-8"))
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass

    # -- navigation ---------------------------------------------------
    def show(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    def set_landscape(self, on: bool) -> None:
        try:
            if on:
                self.win.set_default_size(720, 360)
            else:
                self.win.set_default_size(360, 720)
            self.drive_columns.set_orientation(
                self._Gtk.Orientation.HORIZONTAL if on
                else self._Gtk.Orientation.VERTICAL)
            if on:
                self.title_label.add_css_class("xx-auto-compact-title")
                self.play_btn.add_css_class("xx-auto-compact-play")
                self.title_label.set_lines(1)
                self.sub_label.set_lines(1)
                self.sub_label.set_ellipsize(self._Pango.EllipsizeMode.END)
                if self.gear.get_parent() is not self.gear_row:
                    try:
                        self.bottom_bar.remove(self.gear)
                    except Exception:
                        pass
                    self.gear_row.append(self.gear)
                self.bottom_bar.set_visible(False)
            else:
                self.title_label.remove_css_class("xx-auto-compact-title")
                self.play_btn.remove_css_class("xx-auto-compact-play")
                self.title_label.set_lines(2)
                self.sub_label.set_lines(10)
                self.sub_label.set_ellipsize(self._Pango.EllipsizeMode.NONE)
                if self.gear.get_parent() is not self.bottom_bar:
                    try:
                        self.gear_row.remove(self.gear)
                    except Exception:
                        pass
                    self.bottom_bar.append(self.gear)
                self.bottom_bar.set_visible(True)
            self._landscape = on
            self.refresh()
        except Exception:
            pass

    # -- drive page ----------------------------------------------------
    def _build_drive(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_margin_start(20)
        page.set_margin_end(20)
        page.set_margin_top(8)
        page.set_margin_bottom(8)

        self.drive_columns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                     spacing=8)
        self.drive_columns.set_vexpand(True)

        self.card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.card_box.set_vexpand(True)
        self.source_label = _label(Gtk, Pango, "", "xx-caption")
        self.title_label = Gtk.Label(label="")
        self.title_label.add_css_class("xx-display")
        self.title_label.add_css_class("xx-auto-title")
        self.title_label.set_xalign(0.0)
        self.title_label.set_wrap(True)
        self.title_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.title_label.set_lines(2)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_max_width_chars(18)
        self.sub_label = _wrap(Gtk, Pango, "", "xx-secondary", "xx-auto-body")
        self.progress = Gtk.ProgressBar()
        self.progress.set_hexpand(True)
        self.time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                spacing=8)
        self.elapsed_label = _label(Gtk, Pango, "", "xx-time", "xx-auto-time")
        self.elapsed_label.set_max_width_chars(7)
        self.duration_label = _label(Gtk, Pango, "", "xx-time", "xx-auto-time")
        self.duration_label.set_max_width_chars(7)
        self.duration_label.set_xalign(1.0)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.time_row.append(self.elapsed_label)
        self.time_row.append(spacer)
        self.time_row.append(self.duration_label)
        self.transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=8)
        self.transport.set_halign(Gtk.Align.CENTER)
        self.prev_btn = self._transport_btn("media-skip-backward-symbolic",
                                            self._on_prev)
        self.play_btn = self._transport_btn("media-playback-start-symbolic",
                                            self._on_play_pause, big=True)
        self.next_btn = self._transport_btn("media-skip-forward-symbolic",
                                            self._on_next)
        for btn in (self.prev_btn, self.play_btn, self.next_btn):
            self.transport.append(btn)
        self.actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                   spacing=8)
        self.actions_row.set_halign(Gtk.Align.CENTER)
        self.note_label = _wrap(Gtk, Pango, "", "xx-secondary")
        for widget in (self.source_label, self.title_label, self.sub_label,
                       self.progress, self.time_row, self.transport,
                       self.actions_row, self.note_label):
            self.card_box.append(widget)

        self.tiles_grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        self.tiles_grid.set_halign(Gtk.Align.FILL)
        self.tile_radio = self._tile("Radio", "Stations", self._on_tile_radio)
        self.tile_audio = self._tile("Audiobook", "xx-audiobook",
                                     self._on_tile_audio)
        self.tile_maps = self._tile("Maps", "xx-maps", self._on_tile_maps)
        self.tile_calls = self._tile("Calls", "Favourites",
                                     self._on_tile_calls)
        self.tiles_grid.attach(self.tile_radio, 0, 0, 1, 1)
        self.tiles_grid.attach(self.tile_audio, 1, 0, 1, 1)
        self.tiles_grid.attach(self.tile_maps, 0, 1, 1, 1)
        self.tiles_grid.attach(self.tile_calls, 1, 1, 1, 1)

        self.right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=6)
        self.right_box.append(self.tiles_grid)
        self.gear_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.gear_row.set_halign(Gtk.Align.END)
        self.right_box.append(self.gear_row)

        self.drive_columns.append(self.card_box)
        self.drive_columns.append(self.right_box)
        page.append(self.drive_columns)

        self.bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.bottom_bar.set_halign(Gtk.Align.END)
        self.gear = Gtk.Button()
        self.gear.add_css_class("xx-btn-icon")
        self.gear.add_css_class("xx-auto-target")
        self.gear.set_tooltip_text("Settings")
        gear_img = Gtk.Image.new_from_icon_name("emblem-system-symbolic")
        gear_img.set_pixel_size(24)
        self.gear.set_child(gear_img)
        self.gear.connect("clicked", lambda *_: self.show("settings"))
        self.bottom_bar.append(self.gear)
        page.append(self.bottom_bar)
        self.stack.add_named(page, "drive")

    def _transport_btn(self, icon, callback, big=False):
        Gtk = self._Gtk
        btn = Gtk.Button()
        btn.add_css_class("xx-btn-outline")
        btn.add_css_class("xx-auto-play" if big else "xx-auto-target")
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(28 if big else 24)
        btn.set_child(img)
        btn.connect("clicked", callback)
        return btn

    def _tile(self, title, subtitle, callback):
        Gtk, Pango = self._Gtk, self._Pango
        btn = Gtk.Button()
        btn.add_css_class("xx-btn-outline")
        btn.add_css_class("xx-auto-tile")
        btn.set_hexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_halign(Gtk.Align.START)
        name = Gtk.Label(label=title)
        name.add_css_class("xx-title")
        name.add_css_class("xx-auto-body")
        name.set_xalign(0.0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(10)
        sub = Gtk.Label(label=subtitle)
        sub.add_css_class("xx-secondary")
        sub.set_xalign(0.0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(14)
        box.append(name)
        box.append(sub)
        btn.set_child(box)
        btn.connect("clicked", callback)
        btn._title_label = name
        btn._sub_label = sub
        return btn

    def _set_tile(self, btn, title, subtitle, enabled=True) -> None:
        btn._title_label.set_text(title)
        btn._sub_label.set_text(subtitle)
        btn.set_sensitive(enabled)
        btn.set_opacity(1.0 if enabled else 0.45)

    # -- drive refresh -------------------------------------------------
    def refresh(self) -> None:
        from . import elapsed as elapsed_mod
        from .drive_tiles import Tile, visible
        from .mpris import display_name

        snap = self.snapshot
        shown = visible(self.settings.surface_quick_pick,
                        self.settings.surface_nav, self.settings.surface_calls)
        idx = _active_index(snap.tracks)
        track = snap.tracks[idx] if idx is not None else None
        show_card = self.settings.surface_now_playing

        for child in list(self._iter_children(self.actions_row)):
            self.actions_row.remove(child)
        if track is not None and show_card:
            _fit(self.source_label, self._Pango,
                 display_name(track).lower() or "playing", 20)
            self.title_label.set_text(track.title or "Unknown title")
            sub = track.artist or ""
            self.sub_label.set_text(sub)
            self.sub_label.set_visible(bool(sub))
            seekable = track.length_us > 0
            self.progress.set_visible(seekable)
            self.time_row.set_visible(seekable)
            if seekable:
                pos_ms = track.position_us // 1000
                dur_ms = track.length_us // 1000
                _fit(self.elapsed_label, self._Pango,
                     elapsed_mod.format_ms(min(pos_ms, dur_ms)), 7)
                _fit(self.duration_label, self._Pango,
                     elapsed_mod.format_ms(dur_ms), 7)
                try:
                    self.progress.set_fraction(
                        min(1.0, pos_ms / max(1, dur_ms)))
                except Exception:
                    self.progress.set_fraction(0.0)
            icon = ("media-playback-pause-symbolic" if track.is_playing
                    else "media-playback-start-symbolic")
            img = self._Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(32)
            self.play_btn.set_child(img)
            self.transport.set_visible(True)
            for action in (track.actions or [])[:3]:
                btn = self._Gtk.Button(label=action.label[:12])
                btn.add_css_class("xx-btn-outline")
                btn.add_css_class("xx-auto-target")
                btn.set_sensitive(action.enabled)
                btn.connect("clicked", self._on_custom_action,
                            track.bus_name, action.action_id)
                self.actions_row.append(btn)
            self.actions_row.set_visible(bool(track.actions))
        else:
            self.transport.set_visible(False)
            self.actions_row.set_visible(False)
            self.progress.set_visible(False)
            self.time_row.set_visible(False)
            if show_card:
                if not snap.tracks:
                    self.source_label.set_text("")
                    self.title_label.set_text("Nothing playing")
                    if not snap.installed.get("radio") and not snap.installed.get(
                            "audiobook"):
                        self.sub_label.set_text(
                            "Install the radio or xx-audiobook from "
                            "xx-apps, and its controls show up here.")
                    else:
                        self.sub_label.set_text(
                            "Start something in the radio or "
                            "xx-audiobook, or press Radio.")
                    self.sub_label.set_visible(True)
                else:
                    self.source_label.set_text("")
                    self.title_label.set_text("Nothing playing")
                    self.sub_label.set_text("")
                    self.sub_label.set_visible(False)
            else:
                self.source_label.set_text("")
                self.title_label.set_text("")
                self.sub_label.set_visible(False)
        self.note_label.set_text(snap.note or "")
        self.note_label.set_visible(
            bool(snap.note) and not getattr(self, "_landscape", False))

        radio_on = snap.installed.get("radio", bool(snap.tracks))
        audio_on = snap.installed.get("audiobook", bool(snap.tracks))
        if Tile.RADIO in shown:
            self.tile_radio.set_visible(True)
            self._set_tile(self.tile_radio, "Radio",
                           "Stations" if radio_on else "not installed",
                           enabled=radio_on)
        else:
            self.tile_radio.set_visible(False)
        if Tile.AUDIOBOOK in shown:
            self.tile_audio.set_visible(True)
            self._set_tile(self.tile_audio, "Audiobook",
                           "xx-audiobook" if audio_on else "not installed",
                           enabled=audio_on)
        else:
            self.tile_audio.set_visible(False)
        if Tile.MAPS in shown:
            self.tile_maps.set_visible(True)
            self._set_tile(self.tile_maps, snap.maps_title, snap.maps_sub)
        else:
            self.tile_maps.set_visible(False)
        if Tile.CALLS in shown:
            self.tile_calls.set_visible(True)
            self._set_tile(self.tile_calls, "Calls", "Favourites")
        else:
            self.tile_calls.set_visible(False)

    def _iter_children(self, box):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            yield child
            child = nxt

    # -- transport actions (worker thread) -------------------------------
    def _run_worker(self, func) -> None:
        def _runner():
            try:
                func()
            except Exception as exc:
                from .applog import LOG

                LOG.w("drive", f"media call failed: {type(exc).__name__}")

        threading.Thread(target=_runner, daemon=True).start()

    def _bus_name_of_active(self):
        idx = _active_index(self.snapshot.tracks)
        if idx is None:
            return None
        return self.snapshot.tracks[idx].bus_name

    def _on_play_pause(self, _btn) -> None:
        bus = self._bus_name_of_active()
        if bus is None:
            return

        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            mpris_mod.send_player_method(conn, bus, "PlayPause")

        self._run_worker(_work)

    def _on_prev(self, _btn) -> None:
        bus = self._bus_name_of_active()
        if bus is None:
            return

        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            mpris_mod.send_player_method(conn, bus, "Previous")

        self._run_worker(_work)

    def _on_next(self, _btn) -> None:
        bus = self._bus_name_of_active()
        if bus is None:
            return

        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            mpris_mod.send_player_method(conn, bus, "Next")

        self._run_worker(_work)

    def _on_custom_action(self, _btn, bus_name, action_id) -> None:
        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            try:
                mpris_mod.activate_action(conn, bus_name, action_id)
            except Exception as exc:
                from .applog import LOG

                LOG.w("drive", f"custom action failed: {type(exc).__name__}")
                self._GLib.idle_add(self._set_note,
                                    "That button did not respond.")

        self._run_worker(_work)

    def _set_note(self, text) -> bool:
        self.snapshot.note = text
        self.refresh()
        return False

    # -- tile actions ------------------------------------------------------
    def _on_tile_radio(self, _btn) -> None:
        self._open_players_sheet()

    def _on_tile_audio(self, _btn) -> None:
        self._open_players_sheet()

    def _on_tile_maps(self, _btn) -> None:
        def _work():
            from . import maps_launch as maps_mod

            target, desktop_id = maps_mod.resolve_maps_app()
            ok = maps_mod.launch_target(target, desktop_id)
            if not ok:
                maps_mod.open_store_listing()
                self._GLib.idle_add(self._set_note, "Maps is not installed.")

        self._run_worker(_work)

    def _on_tile_calls(self, _btn) -> None:
        self.show("favourites")
        self._load_favourites()

    def _open_players_sheet(self) -> None:
        rows = []
        for track in self.snapshot.tracks:
            from .mpris import display_name

            rows.append({"bus": track.bus_name,
                         "title": track.title or display_name(track),
                         "sub": track.artist or display_name(track)})
        self._players_rows = rows
        self._render_players_sheet()
        self.show("players")

    # -- settings page ---------------------------------------------------
    def _build_settings(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_top(16)
        back = Gtk.Button()
        back.add_css_class("xx-btn-icon")
        back.add_css_class("xx-auto-target")
        back_img = Gtk.Image.new_from_icon_name("go-previous-symbolic")
        back_img.set_pixel_size(24)
        back.set_child(back_img)
        back.connect("clicked", lambda *_: self.show("drive"))
        title = _label(Gtk, Pango, "Settings", "xx-display", "xx-auto-title")
        title.add_css_class("xx-auto-compact-title")
        header.append(back)
        header.append(title)
        outer.append(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.set_margin_start(20)
        body.set_margin_end(20)
        body.set_margin_top(8)
        body.set_margin_bottom(12)

        def _section(text):
            caption = _label(Gtk, Pango, text.upper(), "xx-caption")
            body.append(caption)

        _section("Car")
        self._setting_row(body, "Open when the car connects",
                          "Pick a Bluetooth device", "auto_launch")
        car_row = Gtk.Button()
        car_row.add_css_class("xx-btn-text")
        car_row.add_css_class("xx-auto-target")
        car_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        car_box.set_halign(Gtk.Align.START)
        car_box.append(_label(Gtk, Pango, "Car", "xx-title", "xx-auto-body"))
        self._car_sub = _label(Gtk, Pango, "Pick a device", "xx-secondary")
        car_box.append(self._car_sub)
        car_row.set_child(car_box)
        car_row.connect("clicked", lambda *_: self._open_car_sheet())
        body.append(car_row)
        _section("On the drive screen")
        self._setting_row(body, "Now playing", "", "surface_now_playing")
        self._setting_row(body, "Radio and audiobook tiles", "",
                          "surface_quick_pick")
        self._setting_row(body, "Maps tile", "Opens xx-maps in drive mode",
                          "surface_nav")
        self._setting_row(body, "Calls tile", "Your starred contacts.",
                          "surface_calls")
        _section("Screen")
        self._setting_row(body, "Keep the screen on",
                          "Only while this screen is in front",
                          "keep_screen_on")
        self._setting_row(body, "Rotate with the phone", "Off = landscape",
                          "follow_rotation")
        self._setting_row(body, "Always black",
                          "Off = follow the suite theme", "always_ink")
        _section("About")
        lic = Gtk.Button()
        lic.add_css_class("xx-btn-text")
        lic.add_css_class("xx-auto-target")
        lic_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lic_box.set_halign(Gtk.Align.START)
        lic_box.append(_label(Gtk, Pango, "Open-source licenses", "xx-title",
                              "xx-auto-body"))
        lic_box.append(_label(Gtk, Pango, "Libraries and fonts in xx-auto",
                              "xx-secondary"))
        lic.set_child(lic_box)
        lic.connect("clicked", lambda *_: self.show("licenses"))
        body.append(lic)
        from . import licenses as lic_mod

        body.append(_label(Gtk, Pango, lic_mod.VERSION_LINE, "xx-title",
                           "xx-auto-body"))
        local = _wrap(Gtk, Pango, lic_mod.LOCAL_ONLY, "xx-secondary")
        body.append(local)
        scroll.set_child(body)
        outer.append(scroll)
        self.stack.add_named(outer, "settings")

    def _setting_row(self, body, title, subtitle, key) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("xx-auto-target")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        texts.set_hexpand(True)
        texts.set_halign(Gtk.Align.START)
        texts.append(_wrap(Gtk, Pango, title, "xx-title", "xx-auto-body"))
        if subtitle:
            texts.append(_wrap(Gtk, Pango, subtitle, "xx-secondary"))
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(bool(getattr(self.settings, key)))
        switch.connect("notify::active",
                       lambda sw, _ps, k=key: self._on_toggle(k, sw))
        row.append(texts)
        row.append(switch)
        body.append(row)

    def _on_toggle(self, key, switch) -> None:
        from .settings_store import save_settings

        value = bool(switch.get_active())
        if key == "surface_calls" and value:
            value = True
        setattr(self.settings, key, value)
        if key == "auto_launch":
            self._apply_watch_unit(value)
        if key == "always_ink":
            self._apply_theme()
        if key == "follow_rotation":
            self.set_landscape(not value)
        if key == "keep_screen_on":
            self._update_inhibit()
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self._sync_car_sub()
        self.refresh()

    def _sync_car_sub(self) -> None:
        try:
            device = self.settings.auto_launch_device or "Pick a device"
            _fit(self._car_sub, self._Pango, device, 20)
        except Exception:
            pass

    def _apply_watch_unit(self, on: bool) -> None:
        def _work():
            import shutil
            import subprocess

            systemctl = shutil.which("systemctl")
            if not systemctl:
                return
            action = ["enable", "--user", "--now", "xx-auto-watch.service"] \
                if on else ["disable", "--user", "--now",
                            "xx-auto-watch.service"]
            try:
                subprocess.run([systemctl, *action], timeout=15,
                               check=False, capture_output=True)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    # -- sheets ------------------------------------------------------------
    def _sheet_shell(self, name, heading):
        Gtk, Pango = self._Gtk, self._Pango
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_top(16)
        back = Gtk.Button()
        back.add_css_class("xx-btn-icon")
        back.add_css_class("xx-auto-target")
        back_img = Gtk.Image.new_from_icon_name("go-previous-symbolic")
        back_img.set_pixel_size(24)
        back.set_child(back_img)
        back.connect("clicked", lambda *_: self.show("drive"))
        header.append(back)
        heading_label = _label(Gtk, Pango, heading, "xx-display",
                               "xx-auto-title")
        heading_label.add_css_class("xx-auto-compact-title")
        header.append(heading_label)
        outer.append(header)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.set_margin_start(20)
        body.set_margin_end(20)
        body.set_margin_top(8)
        body.set_margin_bottom(12)
        scroll.set_child(body)
        outer.append(scroll)
        self.stack.add_named(outer, name)
        return body

    def _build_sheet_players(self) -> None:
        self._players_body = self._sheet_shell("players", "Players")

    def _render_players_sheet(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        body = self._players_body
        for child in list(self._iter_children(body)):
            body.remove(child)
        if not self._players_rows:
            body.append(_wrap(Gtk, Pango,
                              "No players. Start the radio or xx-audiobook.",
                              "xx-secondary", "xx-auto-body"))
            return
        for item in self._players_rows:
            btn = Gtk.Button()
            btn.add_css_class("xx-btn-text")
            btn.add_css_class("xx-auto-target")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.set_halign(Gtk.Align.START)
            box.append(_label(Gtk, Pango, item["title"], "xx-title",
                              "xx-auto-body"))
            box.append(_label(Gtk, Pango, item["sub"], "xx-secondary"))
            btn.set_child(box)
            btn.connect("clicked", self._on_pick_player, item["bus"])
            body.append(btn)

    def _on_pick_player(self, _btn, bus_name) -> None:
        def _work():
            from gi.repository import Gio

            from . import mpris as mpris_mod

            try:
                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                mpris_mod.send_player_method(conn, bus_name, "Raise")
            except Exception:
                pass
            try:
                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                mpris_mod.send_player_method(conn, bus_name, "Play")
            except Exception as exc:
                from .applog import LOG

                LOG.w("drive", f"quick pick failed: {type(exc).__name__}")

        self._run_worker(_work)
        self.show("drive")

    def _build_sheet_favourites(self) -> None:
        self._fav_body = self._sheet_shell("favourites", "Favourites")

    def _load_favourites(self) -> None:
        def _work():
            from gi.repository import Gio

            from . import contacts_client as cc

            try:
                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                favourites = cc.list_starred_sync(conn)
                self._GLib.idle_add(self._render_favourites, favourites, "")
            except Exception as exc:
                from .applog import LOG

                LOG.w("calls", "starred query failed")
                self._GLib.idle_add(self._render_favourites, [], str(exc))

        threading.Thread(target=_work, daemon=True).start()

    def _render_favourites(self, favourites, error) -> bool:
        Gtk, Pango = self._Gtk, self._Pango
        body = self._fav_body
        for child in list(self._iter_children(body)):
            body.remove(child)
        self._favourites = favourites
        if error and not favourites:
            body.append(_label(Gtk, Pango, "Contacts are not available",
                               "xx-title", "xx-auto-body"))
            body.append(_wrap(Gtk, Pango,
                              "xx-auto reads only your starred contacts, "
                              "and only to show them here.", "xx-secondary"))
            return False
        if not favourites:
            body.append(_label(Gtk, Pango, "No starred contacts",
                               "xx-title", "xx-auto-body"))
            return False
        for fav in favourites:
            btn = Gtk.Button()
            btn.add_css_class("xx-btn-text")
            btn.add_css_class("xx-auto-target")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            img = Gtk.Image.new_from_icon_name("call-start-symbolic")
            img.set_pixel_size(28)
            img.set_valign(Gtk.Align.CENTER)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            texts.set_hexpand(True)
            texts.set_halign(Gtk.Align.START)
            texts.append(_label(Gtk, Pango, fav.name, "xx-title",
                                "xx-auto-body"))
            num = _label(Gtk, Pango, fav.number, "xx-secondary")
            texts.append(num)
            row.append(img)
            row.append(texts)
            btn.set_child(row)
            btn.connect("clicked", self._on_call_favourite, fav.number)
            body.append(btn)
        return False

    def _on_call_favourite(self, _btn, number) -> None:
        def _work():
            try:
                import gi

                gi.require_version("Gio", "2.0")
                from gi.repository import Gio

                from .contacts_client import tel_uri

                Gio.AppInfo.launch_default_for_uri(tel_uri(number), None)
            except Exception:
                from .applog import LOG

                LOG.w("calls", "dial handoff failed")
                self._GLib.idle_add(self._set_note, "Could not place call.")

        self._run_worker(_work)

    def _build_sheet_car(self) -> None:
        self._car_body = self._sheet_shell("car", "Which car?")

    def _open_car_sheet(self) -> None:
        self.show("car")
        self._render_car_sheet()
        self._load_car_devices()

    def _render_car_sheet(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        body = self._car_body
        for child in list(self._iter_children(body)):
            body.remove(child)
        if not self._car_devices:
            body.append(_label(Gtk, Pango, "Bluetooth is off", "xx-title",
                               "xx-auto-body"))
            body.append(_wrap(Gtk, Pango, "Turn it on to see your car.",
                              "xx-secondary"))
            return
        for dev in self._car_devices:
            btn = Gtk.Button()
            btn.add_css_class("xx-btn-text")
            btn.add_css_class("xx-auto-target")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.set_halign(Gtk.Align.START)
            box.append(_label(Gtk, Pango, dev.name or dev.address,
                              "xx-title", "xx-auto-body"))
            box.append(_label(Gtk, Pango, dev.address, "xx-secondary"))
            btn.set_child(box)
            btn.connect("clicked", self._on_pick_car, dev.address)
            body.append(btn)

    def _load_car_devices(self) -> None:
        def _work():
            from . import bluez as bluez_mod

            devices = bluez_mod.list_paired_devices()
            self._GLib.idle_add(self._got_car_devices, devices)

        threading.Thread(target=_work, daemon=True).start()

    def _got_car_devices(self, devices) -> bool:
        self._car_devices = devices
        if self.stack.get_visible_child_name() == "car":
            self._render_car_sheet()
        return False

    def _on_pick_car(self, _btn, address) -> None:
        from .settings_store import save_settings

        self.settings.auto_launch_device = address
        self.settings.auto_launch = True
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self._sync_car_sub()
        self.show("settings")

    def _build_sheet_licenses(self) -> None:
        Gtk, Pango = self._Gtk, self._Pango
        body = self._sheet_shell("licenses", "Licenses")
        from . import licenses as lic_mod

        for title, detail in lic_mod.license_rows():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(_wrap(Gtk, Pango, title, "xx-title", "xx-auto-body"))
            box.append(_wrap(Gtk, Pango, detail, "xx-secondary"))
            body.append(box)

    # -- live poll + inhibit -----------------------------------------------
    def _start_poll(self) -> None:
        try:
            self._poll_timer = self._GLib.timeout_add_seconds(2, self._poll_tick)
            self._GLib.timeout_add_seconds(1, self._first_poll)
        except Exception:
            pass

    def _first_poll(self) -> bool:
        self._poll_tick()
        return False

    def _poll_tick(self) -> bool:
        def _work():
            try:
                from gi.repository import Gio

                from . import mpris as mpris_mod

                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                names = mpris_mod.list_player_names(conn)
                tracks = []
                import time as _time

                now_ms = int(_time.time() * 1000)
                for name in names[:8]:
                    try:
                        track = mpris_mod.read_track(conn, name)
                        track.last_active_ms = now_ms
                        tracks.append(track)
                    except Exception:
                        continue
                self._GLib.idle_add(self._got_tracks, tracks)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()
        return True

    def _got_tracks(self, tracks) -> bool:
        if not tracks and self.snapshot.tracks:
            return False
        if tracks:
            self.snapshot = DriveSnapshot(
                tracks=tracks, installed={"radio": True, "audiobook": True},
                maps_title=self.snapshot.maps_title,
                maps_sub=self.snapshot.maps_sub, note=self.snapshot.note)
            self.refresh()
        return False

    def _update_inhibit(self) -> None:
        try:
            app = self.app
            if self.settings.keep_screen_on and self.win.get_visible():
                if not self._inhibit_cookie:
                    self._inhibit_cookie = app.inhibit(
                        self.win, self._Gtk.ApplicationInhibitFlags.IDLE,
                        "driving")
            elif self._inhibit_cookie:
                app.uninhibit(self._inhibit_cookie)
                self._inhibit_cookie = 0
        except Exception:
            pass

    def present(self) -> None:
        self.refresh()
        self._sync_car_sub()
        self.win.present()

    # -- seed hooks for screenshots/tests ------------------------------------
    def seed_demo(self, scenario: str) -> None:
        from . import demo

        self.settings = demo.demo_settings()
        self.snapshot = demo.snapshot_for(scenario)
        if scenario == "favourites":
            self._favourites = demo.demo_favourites()
            self._render_favourites(self._favourites, "")
        if scenario == "car":
            self._car_devices = demo.demo_car_devices()
            self._render_car_sheet()
        if scenario in ("settings", "licenses"):
            pass
        self.refresh()

    def open_sheet(self, name: str) -> None:
        if name == "players" and not self._players_rows:
            self._open_players_sheet()
        else:
            self.show(name)


class AutoApp:
    def __init__(self) -> None:
        Adw, _Gdk, Gio, _GLib, _Gtk, _Pango = _require_gtk()
        from . import __app_id__

        self._app = Adw.Application(application_id=__app_id__,
                                    flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._app.connect("activate", self._on_activate)
        self.window = None

    def _on_activate(self, app) -> None:
        from .settings_store import load_settings

        app._auto_window = self.window
        settings = load_settings()
        if self.window is None:
            self.window = AutoWindow(app, settings)
            app._auto_window = self.window
        else:
            self.window.settings = settings
            app._auto_window = self.window
        scenario = os.environ.get("XX_AUTO_SCENARIO", "")
        sheet = os.environ.get("XX_AUTO_SHEET", "")
        landscape = os.environ.get("XX_AUTO_LANDSCAPE", "")
        if scenario:
            self.window.seed_demo(scenario)
        if landscape == "1":
            self.window.set_landscape(True)
        self.window.present()
        if sheet:
            self.window.open_sheet(sheet)

    def run(self, argv=None) -> int:
        return self._app.run(argv if argv is not None else [])


def main(argv=None) -> int:
    args = list(argv) if argv is not None else None
    import sys

    if args is None:
        args = sys.argv[1:]
    if "--watch" in args:
        from .car_watch import run_watch

        return run_watch(args)
    app = AutoApp()
    return app.run(args)




