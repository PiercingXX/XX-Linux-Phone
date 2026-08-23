"""Shared test setup: launcher sources importable, HOME never the real one
for tests that opt into the isolated_home fixture."""
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import pytest

try:
    import gi

    try:
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib, Gtk  # noqa: F401
    except (ImportError, ValueError):
        pass
finally:
    _REAL_GI_MODULES = {
        name: mod for name, mod in sys.modules.items()
        if name == 'gi' or name.startswith('gi.')
    }


@pytest.fixture(scope='session', autouse=True)
def _real_gi_modules():
    """Undo collection-time gi stubs before the first test runs.

    Some test modules install stub ``gi`` packages at import time (i.e.
    during collection, before any test runs) and never restore them.
    PyGObject resolves override methods such as
    ``Gtk.CssProvider.load_from_data`` through the live
    ``sys.modules['gi.repository.*']`` entries, so once a stub shadows the
    namespace there, previously imported real-GTK code (hud.py) crashes
    mid-suite even though it imported cleanly. Re-install the captured
    real modules once up front; fakes installed later by fixtures or test
    bodies (which manage their own save/restore) are left untouched.
    """
    saved = {
        name: sys.modules.pop(name) for name in list(sys.modules)
        if name == 'gi' or name.startswith('gi.')
    }
    sys.modules.update(_REAL_GI_MODULES)
    yield
    for name in list(sys.modules):
        if name == 'gi' or name.startswith('gi.'):
            del sys.modules[name]
    sys.modules.update(saved)
