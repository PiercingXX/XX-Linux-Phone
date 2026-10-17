"""Scaffold UI imports display-less and exposes the five-tab shell."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")


def test_main_imports_without_display():
    from xx_audiobook import main as main_module

    assert [tab for tab, _label, _text in main_module.TABS] == [
        "library", "playing", "downloads", "zones", "setup"]


def test_every_tab_has_user_facing_text():
    from xx_audiobook import main as main_module

    for _tab, label, text in main_module.TABS:
        assert label.strip()
        assert text.strip()
        lowered = (label + text).lower()
        for banned in ("lane", "milestone", "todo", "coming"):
            assert banned not in lowered
