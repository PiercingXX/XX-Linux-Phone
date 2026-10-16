"""Static GTK4 guard for xx-captions (mirrors the xx-wm api guard).

Every signal connected in src/ must exist on a real GTK4/Gdk/Gio
type, removed GTK3 API must not be called, and D-Bus reply_type must
be a GLib.VariantType (never a plain string: real PyGObject raises
TypeError and a broad except turns it into a silent no-op).
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_captions"

_CONNECT_RE = re.compile(r"""\.connect\(\s*['"]([a-z0-9_-]+)(?:::[a-z0-9_-]+)?['"]""")

_FORBIDDEN = {
    r'dlg\.get_filename\(|dialog\.get_filename\(': 'GTK4 file choosers return Gio.File (get_file)',
    r'\bdialog\.run\(': 'GTK4 dialogs have no run()',
    r'Stack\(.*\)\.set_homogeneous|stack\.set_homogeneous\(': 'Gtk.Stack has no set_homogeneous',
    r'GLib\.unix_fd_add\(': 'PyGObject has no GLib.unix_fd_add',
    r"'size-allocate'": 'GTK4 has no size-allocate signal',
    r"unix_fd_add": 'use GLibUnix.fd_add_full, not GLib.unix_fd_add',
    r"KeyboardMode\.ON_DEMAND": 'layer-shell v3 on phoc has no ON_DEMAND; use NONE',
    r'reply_type="': 'reply_type must be GLib.VariantType.new("(...)") or None',
    r"reply_type='": 'reply_type must be GLib.VariantType.new("(...)") or None',
}


def _gi_signal_names() -> set[str]:
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gio, GObject, Gtk
    names: set[str] = set()
    warnings.simplefilter("ignore")
    for module in (Gtk, Gdk, Gio):
        for attr in dir(module):
            obj = getattr(module, attr, None)
            gtype = getattr(obj, "__gtype__", None)
            if gtype is None:
                continue
            try:
                GObject.type_class_ref(gtype)
                names.update(GObject.signal_list_names(gtype))
            except Exception:
                continue
    return names | {"notify"}


def test_every_connected_signal_exists_in_gtk4():
    known = _gi_signal_names()
    unknown = []
    for path in sorted(SRC.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for name in _CONNECT_RE.findall(line):
                if name not in known:
                    unknown.append(f"{path.name}:{lineno}: {name}")
    assert unknown == []


@pytest.mark.parametrize("pattern,why", sorted(_FORBIDDEN.items()))
def test_no_removed_or_invented_api(pattern, why):
    hits = [f"{p.name}:{n}" for p in sorted(SRC.glob("*.py"))
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if re.search(pattern, line) and not line.lstrip().startswith("#")]
    assert hits == [], why


def test_layer_shell_uses_none_keyboard_mode():
    text = (SRC / "overlay.py").read_text()
    assert "KeyboardMode.NONE" in text
    assert "set_exclusive_zone" in text
    assert "set_input_region" in text
