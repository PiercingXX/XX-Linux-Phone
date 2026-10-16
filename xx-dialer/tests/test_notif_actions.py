"""Silenced-card action targets: hint, posted-id guard, restart survival.

Cards carry ``x-xx-wm-action-target`` so the shell forwards taps to
``HandleNotificationAction(u id, s action_key)`` with auto-start —
which re-activates an idle-exited service. Only ids the service posted
act; the number rides in the key, and the posted ids persist in the
store, so all three actions work with no live memory.
"""
from xx_dialer import ringer
from xx_dialer.ringer import Notifier
from xx_dialer.service import PolicyService

ADA = "+14155550100"

TARGET = ("io.piercingxx.XXDialer.Policy /io/piercingxx/XXDialer/Policy"
          " io.piercingxx.XXDialer.Policy1")


class _Sink:
    def __init__(self):
        self.cards = []
        self.next_id = 40

    def __call__(self, replaces_id, summary, body, actions, hints,
                 e164=""):
        self.next_id += 1
        self.cards.append({"replaces": replaces_id,
                           "summary": summary, "body": body,
                           "actions": list(actions), "hints": hints,
                           "server": self.next_id, "e164": e164})
        return self.next_id


def _notifier(tmp_path, **over):
    sink = _Sink()
    params = {"db_file": tmp_path / "calls.db", "home": tmp_path,
              "notify": sink}
    params.update(over)
    return Notifier(**params), sink


def _posted(notifier):
    notifier.post_batch(1, ADA, "unknown caller")
    return notifier


def test_action_target_matches_service_name():
    assert ringer.action_target() == TARGET
    assert ringer.ACTION_TARGET_HINT == "x-xx-wm-action-target"


def test_unposted_id_does_nothing(tmp_path):
    calls: list[str] = []
    notifier, _sink = _notifier(tmp_path, on_call_back=calls.append)
    assert notifier.handle_notification_action(41, "call-back:%s" % ADA) \
        is False
    assert calls == []


def test_posted_id_acts_and_unknown_key_does_not(tmp_path):
    calls: list[str] = []
    notifier, sink = _notifier(tmp_path, on_call_back=calls.append)
    _posted(notifier)
    (card,) = sink.cards
    assert notifier.handle_notification_action(
        card["server"], "call-back:%s" % ADA) is True
    assert calls == [ADA]
    assert notifier.handle_notification_action(
        card["server"], "launch:now") is False


def test_actions_survive_service_restart(tmp_path):
    """Fresh notifier on the same db (empty memory, as after an idle
    exit + bus re-activation) still serves all three actions."""
    placed: list[str] = []
    stars: list[str] = []
    blocks: list[str] = []
    first, sink = _notifier(tmp_path)
    _posted(first)
    (card,) = sink.cards
    server_id = card["server"]
    assert first.is_posted(server_id)

    second, _sink2 = _notifier(
        tmp_path, on_call_back=placed.append,
        on_ring_next=stars.append, on_block=blocks.append)
    assert second._posted == set()
    assert second.handle_notification_action(
        server_id, "call-back:%s" % ADA) is True
    assert second.handle_notification_action(
        server_id, "ring-next-time:%s" % ADA) is True
    assert second.handle_notification_action(
        server_id, "block-contact:%s" % ADA) is True
    assert (placed, stars, blocks) == ([ADA], [ADA], [ADA])
    assert second.handle_notification_action(987654, "call-back:%s" % ADA) \
        is False
    assert (placed, stars, blocks) == ([ADA], [ADA], [ADA])


def test_bare_kind_falls_back_to_stored_number(tmp_path):
    placed: list[str] = []
    first, sink = _notifier(tmp_path)
    _posted(first)
    (card,) = sink.cards
    second, _sink2 = _notifier(tmp_path, on_call_back=placed.append)
    assert second.handle_notification_action(
        card["server"], "call-back") is True
    assert placed == [ADA]


def test_signal_and_forward_act_only_once(tmp_path):
    """One tap reaches a live service twice (ActionInvoked + forward);
    the callback still places exactly one call."""
    placed: list[str] = []
    notifier, sink = _notifier(tmp_path, on_call_back=placed.append)
    _posted(notifier)
    (card,) = sink.cards
    key = "call-back:%s" % ADA

    class _Params:
        def unpack(self):
            return (card["server"], key)

    notifier._on_action_signal(None, None, None, None, None, _Params(),
                               None)
    assert placed == [ADA]
    assert notifier.handle_notification_action(card["server"], key) is True
    assert placed == [ADA]


def test_service_entry_survives_restart(tmp_path):
    """PolicyService.handle_notification_action with a cold notifier."""
    from xx_dialer.store import MirrorRow, Store

    db_file = tmp_path / "calls.db"
    placed: list[str] = []
    first = PolicyService(db_file=db_file, home=tmp_path,
                          call_placer=placed.append)
    sink = _Sink()
    first._notifier._notify = sink
    first._notifier.post_batch(1, ADA, "unknown caller")
    (card,) = sink.cards

    with Store(db_file) as store:
        store.upsert_mirror([MirrorRow("uid-ada", ADA, "Ada", False,
                                       None, False, 1)])
    starred: list[str] = []
    blocked: list[str] = []

    class _Contacts:
        revision = 1

        def set_star(self, uid, on):
            if on:
                starred.append(uid)

        def set_blocked(self, uid, on):
            if on:
                blocked.append(uid)
            return []

    second = PolicyService(db_file=db_file, home=tmp_path,
                           call_placer=placed.append)
    second.attach_contacts_client(_Contacts())
    assert second._notifier._posted == set()
    assert second.handle_notification_action(
        card["server"], "call-back:%s" % ADA) is True
    assert second.handle_notification_action(
        card["server"], "ring-next-time:%s" % ADA) is True
    assert second.handle_notification_action(
        card["server"], "block-contact:%s" % ADA) is True
    assert placed == [ADA]
    assert starred == ["uid-ada"]
    assert blocked == ["uid-ada"]


def test_method_dispatch_replies_empty_tuple():
    """handle_method_call routes HandleNotificationAction (us) -> ()."""
    pytest_glib = __import__("pytest").importorskip("gi.repository.GLib")
    assert pytest_glib is not None

    from pathlib import Path as _Path
    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as home:
        home_path = _Path(home)
        service = PolicyService(db_file=home_path / "calls.db",
                                home=home_path,
                                spawn=lambda fn: fn(),
                                idle=lambda fn, *args: fn(*args))
        service._notifier.note_posted(77, ADA)

        class _Params:
            def unpack(self):
                return (77, "call-back:%s" % ADA)

        class _Invocation:
            def __init__(self):
                self.value = None
                self.error = None

            def return_value(self, variant):
                self.value = variant

            def return_dbus_error(self, name, message):
                self.error = (name, message)

        invocation = _Invocation()
        service.handle_method_call(None, None, None, None,
                                   "HandleNotificationAction", _Params(),
                                   invocation)
        assert invocation.error is None
        assert invocation.value is not None
        assert invocation.value.unpack() == ()
