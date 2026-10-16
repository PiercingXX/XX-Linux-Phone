"""Application shell: four tabs, entry FAB, XX-WM theme hookup.

Bottom tab bar is text-only buttons; Home is always visible while Journal /
Browse / Profile follow the persisted toggles. Entry sheets open as Adw
dialogs. The XX-WM theme file is read at startup and watched with
Gio.File.monitor for hot-reload, falling back to libadwaita dark/light.
"""

from __future__ import annotations

TABS = ("home", "journal", "browse", "profile")


def visible_tabs(config) -> list[str]:
    tabs = ["home"]
    if getattr(config, "show_journal_tab", True):
        tabs.append("journal")
    if getattr(config, "show_browse_tab", True):
        tabs.append("browse")
    if getattr(config, "show_profile_tab", True):
        tabs.append("profile")
    return tabs


_theme_provider = None


def apply_xx_wm_theme() -> str:
    from gi.repository import Gdk, Gtk

    from xx_vitals.theme import load_xx_wm_config, preset_css, resolve_preset

    global _theme_provider
    preset = resolve_preset(load_xx_wm_config())
    display = Gdk.Display.get_default()
    if display is None:
        return preset.key
    provider = Gtk.CssProvider()
    provider.load_from_data(preset_css(preset).encode("utf-8"))
    if _theme_provider is not None:
        try:
            Gtk.StyleContext.remove_provider_for_display(display, _theme_provider)
        except (TypeError, AttributeError):
            pass
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _theme_provider = provider
    return preset.key


def watch_xx_wm_config(on_change=None, path=None) -> object | None:
    """Watch the shell config for hot-reload without a restart.

    The parent directory is watched, not the file itself: editors save
    atomically (write temp + rename), which replaces the inode and
    silently kills a file monitor. Only events naming config.json
    reach `on_change`.
    """
    from pathlib import Path

    try:
        from gi.repository import Gio

        from xx_vitals.theme import XX_WM_CONFIG_PATH
    except ImportError:
        return None
    target = Path(path or XX_WM_CONFIG_PATH)
    try:
        watched = Gio.File.new_for_path(str(target.parent))
        monitor = watched.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
    except Exception:
        return None
    if on_change is not None and monitor is not None:
        wanted = target.name

        def _on_event(_monitor, changed, other, _event) -> None:
            names: list[str] = []
            for candidate in (changed, other):
                try:
                    names.append(candidate.get_basename())
                except AttributeError:
                    continue
            if wanted in names:
                on_change()

        monitor.connect("changed", _on_event)
    return monitor


def build_window(app, db, config, secrets) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk, Pango

    from xx_vitals.ui import browse as browse_page
    from xx_vitals.ui import home as home_page
    from xx_vitals.ui import journal as journal_page
    from xx_vitals.ui import profile as profile_page

    apply_xx_wm_theme()
    window = Adw.ApplicationWindow(application=app)
    window.set_title("XX-Vitals")
    window.set_default_size(360, 720)
    header = Adw.HeaderBar()
    header.set_show_start_title_buttons(False)
    header.set_show_end_title_buttons(False)
    header.set_title_widget(Gtk.Label(label="XX-Vitals"))
    stack = Gtk.Stack()
    stack.add_named(home_page.build_home_page(db, config), "home")
    stack.add_named(journal_page.build_journal_page(db, config.effort_goal), "journal")
    stack.add_named(browse_page.build_browse_page(db), "browse")
    stack.add_named(profile_page.build_profile_page(db, config, secrets), "profile")
    profile_page.maybe_start_fallback(db, config, window)
    switcher = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    switcher.set_halign(Gtk.Align.CENTER)
    for tab in visible_tabs(config):
        tab_label = Gtk.Label(label=tab.capitalize())
        tab_label.set_ellipsize(Pango.EllipsizeMode.END)
        tab_label.set_wrap(False)
        button = Gtk.Button()
        button.set_child(tab_label)
        button.connect("clicked", lambda _b, name=tab: stack.set_visible_child_name(name))
        switcher.append(button)
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    layout.set_margin_start(4)
    layout.set_margin_end(4)
    layout.append(stack)
    layout.append(switcher)
    toast_overlay = Adw.ToastOverlay(child=layout)
    view = Adw.ToolbarView()
    view.add_top_bar(header)
    view.set_content(toast_overlay)
    window.set_content(view)
    return window
