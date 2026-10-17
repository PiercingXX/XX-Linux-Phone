"""One design system for the shell and every app."""

from . import css, theme
from .css import apply, install, on_change, reload, render
from .theme import PRESETS, Theme, load, preset_keys

__all__ = ['PRESETS', 'Theme', 'apply', 'css', 'install', 'load',
           'on_change', 'preset_keys', 'reload', 'render', 'theme',
           'widgets']


def __getattr__(name: str):
    # widgets needs real GTK at import time; theme/css stay importable
    # under stub gi (headless shell tests), so widgets loads lazily.
    if name == 'widgets':
        import importlib
        return importlib.import_module('.widgets', __name__)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
