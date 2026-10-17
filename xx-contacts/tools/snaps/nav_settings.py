# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
from xx_contacts.settings_ui import SettingsPage

win._navigation.push(SettingsPage(win._navigation, win, win._config,
                                  win._config_path))
