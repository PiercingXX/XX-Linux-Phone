"""The shared private-bus fixture serves drivers without the host bus.

Drivers inherit DBUS_SESSION_BUS_ADDRESS from this fixture, so a
service one test started can neither leak into later tests nor into
the developer's real session. Skips when dbus-daemon or gi is
missing.
"""
import os

import pytest


def _bus_address_host() -> str:
    return (os.environ.get("DBUS_SESSION_BUS_ADDRESS") or "").split(
        ",", 1)[0]


def test_private_bus_is_usable_and_not_the_host_bus(private_bus):
    Gio = pytest.importorskip("gi.repository.Gio")
    assert private_bus
    host = _bus_address_host()
    if host:
        assert private_bus.split(",", 1)[0] != host
    connection = Gio.DBusConnection.new_for_address_sync(
        private_bus,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    try:
        names = connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "ListNames",
            None, None, Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()[0]
    finally:
        connection.close_sync(None)
    assert "org.freedesktop.DBus" in list(names)
