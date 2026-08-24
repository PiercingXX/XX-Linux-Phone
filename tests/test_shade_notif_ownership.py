"""Honest external-daemon handling in the notification shade (P3-G).

Per the freedesktop Notifications spec, Notify is a METHOD call directed at
whichever process owns org.freedesktop.Notifications — never a broadcast. The
shade used to subscribe to Notify as a signal, dead code that could never fire,
so with mako/dunst running the shade showed nothing while pretending it might.
Now the inert subscription is gone, and at shade build a single GetNameOwner
probe (the same one-call_sync idiom quick_actions uses for NM/BlueZ) detects an
external owner: the shade then shows a one-line muted hint (existing .notif-app
style) and logs info once instead of silently pretending to capture.

notification_shade imports gi.repository.Gdk/Gio/GLib/Gtk (+Adw requirement)
at module top level, but PyGObject may be absent here; minimal fake modules are
injected before import, exactly like test_hud_silent does. Widget construction
is never run — the probe and ownership wiring are exercised on module helpers
and a bare NotificationShade built with object.__new__.
"""
import sys
import types


def _install_fake_gi() -> None:
    """Make `from gi.repository import ...` resolve to stubs."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = _RecordingVariant
    glib.VariantType = lambda *a, **k: None

    gio = types.ModuleType('gi.repository.Gio')
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)

    for name in ('Gdk', 'Gtk'):
        sys.modules[f'gi.repository.{name}'] = types.ModuleType(
            f'gi.repository.{name}')
    # The class statements `class QuickActionsPanel(Gtk.Box)` and
    # `class NotificationShade(Gtk.Window)` evaluate their base classes at
    # import time even though widgets are never constructed headlessly.
    sys.modules['gi.repository.Gtk'].Box = type('Box', (), {})
    sys.modules['gi.repository.Gtk'].Window = type('Window', (), {})

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gio'] = gio


class _RecordingVariant:
    """Duck-typed GLib.Variant remembering its constructor arguments."""

    def __init__(self, *args):
        self.args = args

    def __eq__(self, other):
        return (isinstance(other, _RecordingVariant)
                and self.args == other.args)

    def __repr__(self):
        return f'_Variant{self.args!r}'


_install_fake_gi()

import notification_shade  # noqa: E402  (needs fake gi modules installed first)


class _Reply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return (self._value,)


class _Bus:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []
        self.subscriptions = []

    def call_sync(self, *args):
        self.calls.append(args)
        if not self.replies:
            return _Reply('')
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, _Reply) else _Reply(reply)

    def signal_subscribe(self, *args):
        self.subscriptions.append(args)


# --- the dead Notify signal subscription is gone ------------------------------

class TestNoNotifySignalSubscription:
    def test_subscribe_dbus_never_subscribes_to_notify(self):
        import inspect
        src = inspect.getsource(notification_shade.NotificationShade._subscribe_dbus)
        assert "'Notify'" not in src

    def test_only_the_real_broadcast_signal_is_subscribed(self):
        import inspect
        src = inspect.getsource(notification_shade.NotificationShade._subscribe_dbus)
        assert 'NotificationClosed' in src

    def test_on_dbus_notify_handler_is_deleted(self):
        assert not hasattr(notification_shade.NotificationShade,
                           '_on_dbus_notify')


# --- the name-owner probe ------------------------------------------------------

class TestExternalDaemonProbe:
    def test_owned_name_reports_external_daemon(self, monkeypatch):
        bus = _Bus(replies=[_Reply(':1.42')])
        monkeypatch.setattr(notification_shade, '_session_bus', lambda: bus)

        assert notification_shade._external_daemon_owns_notifications() is True
        call = bus.calls[0]
        assert call[:4] == ('org.freedesktop.DBus', '/org/freedesktop/DBus',
                            'org.freedesktop.DBus', 'GetNameOwner')
        assert call[4] == _RecordingVariant(
            '(s)', ('org.freedesktop.Notifications',))

    def test_unowned_name_reports_no_external_daemon(self, monkeypatch):
        bus = _Bus(replies=[Exception('org.freedesktop.DBus.Error.NameHasNoOwner')])
        monkeypatch.setattr(notification_shade, '_session_bus', lambda: bus)

        assert notification_shade._external_daemon_owns_notifications() is False

    def test_probe_failure_falls_back_to_current_behavior(self, monkeypatch):
        monkeypatch.setattr(notification_shade, '_session_bus',
                            lambda: None)

        assert notification_shade._external_daemon_owns_notifications() is False


# --- shade build wiring ---------------------------------------------------------

class _FakeLabel:
    def __init__(self):
        self.visible = None

    def set_visible(self, value):
        self.visible = value


def _bare_shade() -> notification_shade.NotificationShade:
    shade = object.__new__(notification_shade.NotificationShade)
    shade._external_hint = _FakeLabel()
    return shade


class TestOwnershipWiringAtBuild:
    def test_external_owner_shows_hint_and_logs_info_once(
            self, monkeypatch, caplog):
        monkeypatch.setattr(
            notification_shade, '_external_daemon_owns_notifications',
            lambda: True)
        shade = _bare_shade()

        with caplog.at_level('INFO', logger='piercing.notification_shade'):
            shade._check_external_notif_daemon()

        assert shade._external_hint.visible is True
        infos = [r for r in caplog.records
                 if r.name == 'piercing.notification_shade']
        assert len(infos) == 1

    def test_no_external_owner_keeps_current_behavior(self, monkeypatch,
                                                      caplog):
        monkeypatch.setattr(
            notification_shade, '_external_daemon_owns_notifications',
            lambda: False)
        shade = _bare_shade()

        with caplog.at_level('INFO', logger='piercing.notification_shade'):
            shade._check_external_notif_daemon()

        assert shade._external_hint.visible is None
        assert not [r for r in caplog.records
                    if r.name == 'piercing.notification_shade']

    def test_hint_label_is_built_into_the_content(self):
        import inspect
        src = inspect.getsource(notification_shade.NotificationShade._build_content)
        assert '_external_hint' in src
        assert 'notif-app' in src

    def test_build_checks_ownership_once(self):
        import inspect
        init_src = inspect.getsource(notification_shade.NotificationShade.__init__)
        assert init_src.count('_check_external_notif_daemon') == 1
