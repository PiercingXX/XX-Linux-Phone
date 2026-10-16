"""Wayland registry lifetime: the WlRegistry proxy must outlive setup.

pywayland resolves ``new_id`` event arguments (e.g. the foreign-toplevel
``toplevel`` event for a newly opened window) through the live
``WlRegistry`` proxy: ``Message.c_to_arguments`` looks up a display via
``WlRegistry.registry`` (a per-interface ``WeakValueDictionary``) and
raises ``RuntimeError: Cannot find display`` when the proxy was
garbage-collected. Every backend that calls ``display.get_registry()``
must therefore keep the proxy referenced for the connection's lifetime;
a setup-local variable dies with the setup frame and every later
new-toplevel event fails inside a cffi callback (stderr only), so the
switcher never sees apps opened after startup.

Covers ``toplevel_manager.WaylandToplevelBackend``,
``output_power.OutputPower`` and ``capture.VirtualScroller`` with fakes
(no compositor, no display), plus the real pywayland ``new_id`` dispatch
path and a static guard over ``launcher/src``.
"""
import gc
import sys
import types
import weakref
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

REPO = Path(__file__).parent.parent
SRC = REPO / 'launcher' / 'src'


@pytest.fixture(autouse=True)
def hermetic_modules_and_cache(tmp_path, monkeypatch):
    """Contain import side effects: importing the backends loads the real
    pywayland protocol modules, and with a warm binding cache
    ``toplevel_manager``'s import-time resolution additionally imports the
    cached ``xx_wm_wayland_proto`` package and prepends the real cache dir
    to ``sys.path`` — both leak into the binding-vendoring tests that run
    later in the same process."""
    saved_modules = dict(sys.modules)
    saved_path = list(sys.path)
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n not in saved_modules]:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


@pytest.fixture
def fake_gi(monkeypatch):
    """Neutralize GLib pumps/watches so backends connect headless."""
    glib = types.SimpleNamespace(
        PRIORITY_DEFAULT=0,
        IOCondition=types.SimpleNamespace(IN=1, HUP=2),
        unix_fd_add_full=lambda *args: 1,
        timeout_add=lambda *args: 1,
    )
    glib_unix = types.SimpleNamespace(fd_add_full=lambda *args: 1)
    gi_mod = types.ModuleType('gi')
    gi_mod.require_version = lambda *args, **kwargs: None
    repo_mod = types.ModuleType('gi.repository')
    repo_mod.GLib = glib
    repo_mod.GLibUnix = glib_unix
    gi_mod.repository = repo_mod
    monkeypatch.setitem(sys.modules, 'gi', gi_mod)
    monkeypatch.setitem(sys.modules, 'gi.repository', repo_mod)
    return glib


# --- fake compositors (registry is a distinct object, like the real one) ---


class _FakeSeatCls:
    def __init__(self):
        pass


class _FakeToplevelManager:
    def __init__(self):
        self.dispatcher = {}


class _FakeToplevelManagerCls:
    def __init__(self):
        pass


class _ToplevelRegistry:
    def __init__(self, display):
        self.dispatcher = {}
        self._display = display
        self.binds = []

    def bind(self, name, cls, version):
        self.binds.append((name, cls.__name__, version))
        if cls is _FakeSeatCls:
            return _FakeSeatCls()
        return _FakeToplevelManager()


class _ToplevelDisplay:
    def __init__(self):
        self.connected = False
        self.flushed = 0
        self.registry = None

    def connect(self):
        self.connected = True

    def get_registry(self):
        self.registry = _ToplevelRegistry(self)
        return self.registry

    def release_registry(self):
        """Drop the display-side reference, like a collected local would."""
        self.registry = None

    def dispatch(self, block=True):
        advertised = [
            (1, 'wl_seat', 7),
            (2, 'zwlr_foreign_toplevel_manager_v1', 3),
        ]
        for name, iface, version in advertised:
            self.registry.dispatcher['global'](
                self.registry, name, iface, version)

    def flush(self):
        self.flushed += 1

    def get_fd(self):
        return 42


class _FakeToplevelHandle:
    def __init__(self):
        self.dispatcher = {}


def _connect_toplevel_backend():
    import toplevel_manager
    display = _ToplevelDisplay()
    backend = toplevel_manager.WaylandToplevelBackend(
        display=display,
        seat_cls=_FakeSeatCls,
        manager_cls=_FakeToplevelManagerCls,
    )
    return backend, display


def test_toplevel_backend_holds_registry(fake_gi):
    backend, display = _connect_toplevel_backend()
    assert backend.available() is True
    assert backend._registry is not None
    assert backend._registry is display.registry
    ref = weakref.ref(backend._registry)
    display.release_registry()
    del display
    gc.collect()
    assert ref() is backend._registry


def test_toplevel_backend_sees_windows_opened_after_setup(fake_gi):
    """Late foreign-toplevel events must reach the manager (the VM bug)."""
    backend, display = _connect_toplevel_backend()
    events = []
    backend.connect(lambda *args: events.append(args))
    display.release_registry()
    del display
    gc.collect()
    assert backend._registry is not None
    manager = backend._manager
    handle = _FakeToplevelHandle()
    manager.dispatcher['toplevel'](manager, handle)
    handle.dispatcher['app_id'](handle, 'org.example.LateApp')
    handle.dispatcher['title'](handle, 'Late window')
    assert events[-1] == (id(handle), 'org.example.LateApp', 'Late window')


class _PowerRegistry:
    def __init__(self):
        self.dispatcher = {}
        self.binds = []

    def bind(self, name, cls, version):
        self.binds.append((name, cls.__name__, version))
        return cls()


class _PowerDisplay:
    def __init__(self):
        self.connected = False
        self.flushed = 0
        self.registry = None

    def connect(self):
        self.connected = True

    def get_registry(self):
        self.registry = _PowerRegistry()
        return self.registry

    def release_registry(self):
        self.registry = None

    def dispatch(self, block=True):
        for name, iface, version in [
            (7, 'wl_output', 4),
            (8, 'zwlr_output_power_manager_v1', 1),
        ]:
            self.registry.dispatcher['global'](
                self.registry, name, iface, version)

    def flush(self):
        self.flushed += 1

    def get_fd(self):
        return 42


class _FakeOutputCls:
    pass


class _FakePowerObj:
    def __init__(self):
        self.dispatcher = {}
        self.modes = []

    def set_mode(self, mode):
        self.modes.append(mode)

    def destroy(self):
        pass


class _FakePowerManagerCls:
    def __init__(self):
        pass

    def get_output_power(self, _output):
        return _FakePowerObj()


def _connect_power_backend(monkeypatch):
    import output_power
    import wayland_bindings
    monkeypatch.setattr(wayland_bindings, 'ensure_protocol_bindings',
                        lambda _names, _classes: None)
    monkeypatch.setattr(output_power.OutputPower, '_arm_fd_watch',
                        lambda self: None)
    display = _PowerDisplay()
    backend = output_power.OutputPower(display=display)
    backend._connect_main({
        'WlOutput': _FakeOutputCls,
        'ZwlrOutputPowerManagerV1': _FakePowerManagerCls,
        'ZwlrOutputPowerV1': _FakePowerObj,
    })
    return backend, display


def test_output_power_holds_registry(monkeypatch):
    backend, display = _connect_power_backend(monkeypatch)
    assert backend.available is True
    assert backend._registry is not None
    assert backend._registry is display.registry
    ref = weakref.ref(backend._registry)
    display.release_registry()
    del display
    gc.collect()
    assert ref() is backend._registry


class _ScrollRegistry:
    def __init__(self):
        self.dispatcher = {}
        self.binds = []

    def bind(self, name, cls, version):
        self.binds.append((name, cls.__name__, version))
        if cls is _FakeSeatCls:
            return ('seat', name)
        return _ScrollManager()


class _ScrollDisplay:
    instances = []

    def __init__(self):
        _ScrollDisplay.instances.append(self)
        self.registry = _ScrollRegistry()
        self.flushed = 0
        self.disconnected = False

    def connect(self):
        pass

    def get_registry(self):
        return self.registry

    def dispatch(self, block=True):
        handler = self.registry.dispatcher.get('global')
        handler(self.registry, 7, 'wl_seat', 9)
        handler(self.registry, 9, 'zwlr_virtual_pointer_manager_v1', 3)

    def flush(self):
        self.flushed += 1

    def disconnect(self):
        self.disconnected = True


class _ScrollManager:
    def __init__(self):
        self.seats = []

    def create_virtual_pointer(self, seat):
        self.seats.append(seat)
        return types.SimpleNamespace(destroy=lambda: None)


def test_virtual_scroller_holds_registry_for_connection():
    import capture
    _ScrollDisplay.instances.clear()
    bindings = {'WlSeat': _FakeSeatCls,
                'ZwlrVirtualPointerManagerV1': _ScrollManager}
    scroller = capture.VirtualScroller(display_cls=_ScrollDisplay)
    assert scroller._connect_with(bindings, _ScrollDisplay) is True
    display = _ScrollDisplay.instances[-1]
    assert scroller._registry is display.registry
    ref = weakref.ref(scroller._registry)
    display.registry = None
    del display
    gc.collect()
    assert ref() is scroller._registry
    scroller.close()
    assert scroller._registry is None


# --- the real pywayland new_id dispatch path, compositor-free ---


class _StandInRegistryProxy:
    """Lifetime stand-in for a WlRegistryProxy (real ones need a
    compositor connection to construct safely). Only ``_display`` is
    read by the new_id branch of ``Message.c_to_arguments``."""

    def __init__(self, display):
        self._display = display


def test_new_id_dispatch_needs_live_registry_proxy(monkeypatch):
    pywayland = pytest.importorskip('pywayland')
    from pywayland.protocol.wayland import WlRegistry, WlSeat
    from pywayland.protocol_core.argument import Argument, ArgumentType
    from pywayland.protocol_core.message import Message

    def toplevel(manager, new_toplevel):
        """Mirror of zwlr_foreign_toplevel_manager_v1.toplevel(new_id)."""

    message = Message(
        toplevel, (Argument(ArgumentType.NewId, interface=WlSeat),), 1)
    args = pywayland.ffi.new('union wl_argument[]', 1)
    args[0].o = pywayland.ffi.cast('void *', 0x1000)
    made = []
    monkeypatch.setattr(
        WlSeat, 'proxy_class',
        lambda ptr, display: made.append(display) or 'HANDLE')

    sentinel_display = object()
    proxy = _StandInRegistryProxy(sentinel_display)
    key = object()
    WlRegistry.registry[key] = proxy
    try:
        assert message.c_to_arguments(args) == ['HANDLE']
        assert made[-1] is sentinel_display

        del proxy
        gc.collect()
        with pytest.raises(RuntimeError, match='Cannot find display'):
            message.c_to_arguments(args)
    finally:
        WlRegistry.registry.pop(key, None)


# --- static guard: no setup-local get_registry() anywhere ---


def test_no_local_only_get_registry():
    offenders = []
    for path in sorted(SRC.glob('*.py')):
        for lineno, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(), 1):
            stripped = line.strip()
            if 'get_registry()' in stripped and 'self._registry' not in stripped:
                offenders.append(f'{path.name}:{lineno}: {stripped}')
    assert offenders == [], (
        'get_registry() result must be kept on self._registry '
        f'for the connection lifetime: {offenders}'
    )
