"""Finding 12 repro (xx-email side): sender resolution must use exact email.

Resolving the sender with ``Search(address)`` lets the digits inside an
address match arbitrary ``+1...`` contacts. The client must call the
exact ``LookupEmail`` method instead and fall back to the raw header on
a miss.

No real ``gi`` import here: several tests in this suite assert ``gi``
never enters ``sys.modules``, so the bus is faked through
``sys.modules`` injection (undone after each test).
"""

import sys
import types

from xx_email import contacts as contacts_mod


class _FakeVariant:
    def __init__(self, type_string, args):
        self._type_string = type_string
        self._args = args

    def get_type_string(self):
        return self._type_string

    def unpack(self):
        return self._args


class _FakeReply:
    def __init__(self, values=None):
        self._values = values

    def unpack(self):
        if self._values is None:
            raise RuntimeError("no such email")
        return self._values


class _FakeConnection:
    def __init__(self, values=None):
        self.calls = []
        self._values = values

    def call(self, bus_name, path, interface, method, parameters,
             _type, _flags, _timeout, _cancellable, callback):
        self.calls.append({
            "bus": bus_name,
            "path": path,
            "interface": interface,
            "method": method,
            "args": parameters.unpack() if parameters is not None else (),
            "type": parameters.get_type_string()
            if parameters is not None else None,
        })
        callback(self, object())

    def call_finish(self, _result):
        return _FakeReply(self._values)


def _install(monkeypatch, values):
    connection = _FakeConnection(values)

    class _FakeApp:
        def get_dbus_connection(self):
            return connection

    fake_gio = types.SimpleNamespace(
        Application=types.SimpleNamespace(
            get_default=lambda: _FakeApp()),
        DBusCallFlags=types.SimpleNamespace(NONE=0),
        Variant=_FakeVariant,
    )
    fake_glib = types.SimpleNamespace(
        Variant=_FakeVariant,
        idle_add=lambda fn, *args: fn(*args),
    )
    fake_repo = types.SimpleNamespace(Gio=fake_gio, GLib=fake_glib)
    fake_gi = types.SimpleNamespace(repository=fake_repo)
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)
    return connection


def _install_no_app(monkeypatch):
    fake_gio = types.SimpleNamespace(
        Application=types.SimpleNamespace(
            get_default=lambda: None),
        DBusCallFlags=types.SimpleNamespace(NONE=0),
        Variant=_FakeVariant,
    )
    fake_glib = types.SimpleNamespace(
        Variant=_FakeVariant,
        idle_add=lambda fn, *args: fn(*args),
    )
    fake_repo = types.SimpleNamespace(Gio=fake_gio, GLib=fake_glib)
    fake_gi = types.SimpleNamespace(repository=fake_repo)
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)


def test_lookup_email_calls_exact_method(monkeypatch):
    connection = _install(monkeypatch, ("uid-1", "Alice"))
    seen = []
    contacts_mod.lookup_email_async("alice@example.com", seen.append)
    assert seen == ["Alice"]
    assert len(connection.calls) == 1
    call = connection.calls[0]
    assert call["bus"] == contacts_mod.CONTACTS_BUS_NAME
    assert call["path"] == contacts_mod.CONTACTS_OBJECT_PATH
    assert call["interface"] == contacts_mod.CONTACTS_INTERFACE
    assert call["method"] == "LookupEmail"
    assert call["type"] == "(s)"
    assert call["args"] == ("alice@example.com",)


def test_lookup_email_miss_calls_back_none(monkeypatch):
    _install(monkeypatch, None)
    seen = ["sentinel"]
    contacts_mod.lookup_email_async("stranger@example.com", seen.append)
    assert seen[-1] is None


def test_lookup_email_without_bus_calls_back_none(monkeypatch):
    _install_no_app(monkeypatch)
    seen = []
    contacts_mod.lookup_email_async("alice@example.com", seen.append)
    assert seen == [None]
