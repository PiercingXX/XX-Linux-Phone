"""Tests for the vendored pywayland protocol package (launcher/src/wayland_proto).

Guards the protocol-vendoring slice:

- the generated modules import cleanly alongside real pywayland and expose
  the classes toplevel_manager binds;
- toplevel_manager prefers the VENDORED import path (asserted via module
  identity of the bound classes), falling back to pywayland.protocol only
  when the vendored package is absent;
- with neither import source available, the manager still degrades to a None
  backend instead of crashing;
- with everything importable, WaylandToplevelBackend gets past class
  resolution and fails at CONNECT against a missing compositor -- the point
  where it previously degraded on every device.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

VENDORED_WAYLAND = 'wayland_proto.wayland'
VENDORED_WLR = 'wayland_proto.wlr_foreign_toplevel_management_unstable_v1'

# sys.modules names that make up the two import sources; blocked (set to
# None) by the degradation test and restored afterwards.
IMPORT_SOURCES = (
    'pywayland',
    'pywayland.client',
    'pywayland.protocol',
    'pywayland.protocol.wayland',
    'pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1',
    'wayland_proto',
    VENDORED_WAYLAND,
    VENDORED_WLR,
)

_ABSENT = object()  # sentinel: name was not in sys.modules


def _fresh_toplevel_manager() -> types.ModuleType:
    """Re-import toplevel_manager, returning the fresh module object."""
    sys.modules.pop('toplevel_manager', None)
    return importlib.import_module('toplevel_manager')


@pytest.fixture
def restore_toplevel_manager():
    """Restore whatever toplevel_manager module other tests had imported."""
    saved = sys.modules.get('toplevel_manager', _ABSENT)
    yield
    sys.modules.pop('toplevel_manager', None)
    if saved is not _ABSENT:
        sys.modules['toplevel_manager'] = saved


def test_vendored_modules_import_and_expose_protocol_classes() -> None:
    pytest.importorskip('pywayland')
    wayland = importlib.import_module(VENDORED_WAYLAND)
    wlr = importlib.import_module(VENDORED_WLR)

    from pywayland.protocol_core import Interface

    assert wayland.WlSeat.name == 'wl_seat'
    assert wlr.ZwlrForeignToplevelManagerV1.name == 'zwlr_foreign_toplevel_manager_v1'
    assert wlr.ZwlrForeignToplevelHandleV1.name == 'zwlr_foreign_toplevel_handle_v1'
    for cls in (
        wayland.WlSeat,
        wlr.ZwlrForeignToplevelManagerV1,
        wlr.ZwlrForeignToplevelHandleV1,
    ):
        assert issubclass(cls, Interface)
    # Requests live on the Proxy classes registry.bind hands back.
    handle_proxy = wlr.ZwlrForeignToplevelHandleV1Proxy
    assert callable(handle_proxy.activate)
    assert callable(handle_proxy.close)


def test_generated_files_carry_provenance_header() -> None:
    proto_dir = Path(__file__).parent.parent / 'launcher' / 'src' / 'wayland_proto'
    for name in ('wayland.py', 'wlr_foreign_toplevel_management_unstable_v1.py'):
        text = (proto_dir / name).read_text()
        assert 'do not edit' in text.lower(), f'{name} missing do-not-edit header'
        assert 'pywayland' in text and 'scanner' in text, f'{name} missing generator version'
        assert 'launcher/data/' in text, f'{name} missing source-XML provenance'


def test_toplevel_manager_prefers_vendored_import_path(
    restore_toplevel_manager,
) -> None:
    pytest.importorskip('pywayland')
    mod = _fresh_toplevel_manager()

    assert mod._PYWAYLAND_AVAILABLE is True
    assert mod.ZwlrForeignToplevelManagerV1.__module__ == VENDORED_WLR
    assert mod.ZwlrForeignToplevelHandleV1.__module__ == VENDORED_WLR
    assert mod.WlSeat.__module__ == VENDORED_WAYLAND


def test_backend_fails_at_connect_not_class_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, restore_toplevel_manager,
) -> None:
    pytest.importorskip('pywayland')
    # Point the client at a socket that cannot exist so the failure mode is
    # deterministic even on a machine running a compositor.
    monkeypatch.setenv('WAYLAND_DISPLAY', 'xx-wm-test-no-such-display')
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))

    mod = _fresh_toplevel_manager()
    assert mod.Display is not None  # class resolution succeeded

    with pytest.raises(RuntimeError, match='cannot connect to Wayland display'):
        mod.WaylandToplevelBackend()
    # The default factory still degrades gracefully instead of crashing.
    assert mod._make_default_backend() is None


def test_degrades_when_neither_import_source_available(
    restore_toplevel_manager,
) -> None:
    saved = {name: sys.modules.get(name, _ABSENT) for name in IMPORT_SOURCES}
    for name in IMPORT_SOURCES:
        sys.modules[name] = None  # blocks `import name` with ImportError
    try:
        mod = _fresh_toplevel_manager()
        assert mod._PYWAYLAND_AVAILABLE is False
        assert mod.Display is None
        assert mod._make_default_backend() is None
    finally:
        for name, value in saved.items():
            if value is _ABSENT:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
