"""Static guard: every signal connected in src/ must exist in GTK4, and
known removed APIs must not be used. Mirrors the shell's gtk4 api guard
so the same class of crash can never go green here."""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

_CONNECT_RE = re.compile(r"""\.connect\(\s*['"]([a-z0-9_-]+)(?:::[a-z0-9_-]+)?['"]""")

_FORBIDDEN = {
    r"get_filename\(": "GTK4 file choosers return Gio.File (get_file)",
    r"\bdialog\.run\(": "GTK4 dialogs have no run()",
    r"GLib\.unix_fd_add\(": "PyGObject has no GLib.unix_fd_add",
    r"'size-allocate'": "GTK4 has no size-allocate signal",
}


def _gi_signal_names() -> set[str]:
    import warnings

    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GObject, Gtk
    names: set[str] = set()
    warnings.simplefilter("ignore")
    for module in (Gtk, Gdk, Adw, Gio):
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
            if re.search(pattern, line)]
    assert hits == [], why
