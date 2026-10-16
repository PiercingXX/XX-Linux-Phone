"""Thin GTK layer: entry dialogs, tab pages, shell. All builders import Gtk/Adw
lazily so the pure models stay headless-testable; this package only wires
widgets to the models and never blocks the main loop (see sync/worker).
"""

__all__ = ["entry", "home", "journal", "browse", "profile", "shell"]
