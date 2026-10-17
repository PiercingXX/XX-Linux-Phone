"""Lane 4 ringer tests: batching, three-state policy, actions, fail-soft."""
from xx_dialer import config as app_config
from xx_dialer.ringer import Notifier
from xx_dialer.store import Store

WALL = 1789651200000
ADA = "+14155550100"
BO = "+14155550200"


class _Sink:
    """Injected FDN sender: captures cards, hands out server ids."""

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


def test_immediate_single_card_with_actions(tmp_path):
    from xx_dialer import recents as _recents

    notifier, sink = _notifier(tmp_path)
    notifier.screened(ADA, "Silence", "unknown caller", at_ms=WALL)
    assert len(sink.cards) == 1
    (card,) = sink.cards
    assert card["summary"] == "Silenced call"
    assert _recents.format_number(ADA) in card["body"] \
        and "unknown caller" in card["body"]
    assert [key for key, _label in card["actions"]] == [
        "call-back:%s" % ADA, "ring-next-time:%s" % ADA,
        "block-contact:%s" % ADA]
    assert card["e164"] == ADA
    assert card["hints"]["x-xx-wm-action-target"] == (
        "io.piercingxx.XXDialer.Policy /io/piercingxx/XXDialer/Policy"
        " io.piercingxx.XXDialer.Policy1")


def test_burst_collapses_into_one_line(tmp_path):
    notifier, sink = _notifier(tmp_path)
    notifier.screened(ADA, "Silence", "unknown caller", at_ms=WALL)
    notifier.screened(BO, "Silence", "unknown caller",
                      at_ms=WALL + 30_000)
    notifier.screened(ADA, "Silence", "unknown caller",
                      at_ms=WALL + 59_000)
    assert [card["summary"] for card in sink.cards] == [
        "Silenced call", "2 silenced calls", "3 silenced calls"]
    assert sink.cards[-1]["actions"] == []
    notifier.screened(ADA, "Silence", "unknown caller",
                      at_ms=WALL + 59_000 + 60_001)
    assert sink.cards[-1]["summary"] == "Silenced call"


def test_non_silence_posts_nothing(tmp_path):
    notifier, sink = _notifier(tmp_path)
    notifier.screened(ADA, "Ring", "starred", at_ms=WALL)
    notifier.screened(ADA, "block", "blocked", at_ms=WALL)
    assert sink.cards == []


def test_never_policy_posts_nothing(tmp_path):
    settings = app_config.load(tmp_path)
    settings["silenced_notif_policy"] = "never"
    app_config.save(settings, tmp_path)
    notifier, sink = _notifier(tmp_path)
    notifier.screened(ADA, "Silence", "unknown caller", at_ms=WALL)
    assert sink.cards == []


def test_daily_stash_and_flush(tmp_path):
    settings = app_config.load(tmp_path)
    settings["silenced_notif_policy"] = "daily"
    app_config.save(settings, tmp_path)
    notifier, sink = _notifier(tmp_path)
    notifier.screened(ADA, "Silence", "unknown caller", at_ms=WALL)
    notifier.screened(BO, "Silence", "unknown caller",
                      at_ms=WALL + 1_000)
    assert sink.cards == []
    with Store(tmp_path / "calls.db") as store:
        assert len(store.read_digest()) == 2
    assert notifier.flush_daily_if_due(WALL + 1_000) is False
    assert notifier.flush_daily_if_due(
        WALL + 24 * 60 * 60 * 1000 + 1_000) is True
    assert sink.cards[-1]["summary"] == "2 silenced calls"
    with Store(tmp_path / "calls.db") as store:
        assert store.read_digest() == []


def test_actions_dispatch_and_dismiss(tmp_path):
    calls: list[str] = []
    stars: list[str] = []
    blocks: list[str] = []
    notifier, sink = _notifier(
        tmp_path, on_call_back=calls.append,
        on_ring_next=stars.append, on_block=blocks.append)
    notifier.screened(ADA, "Silence", "unknown caller", at_ms=WALL)
    notifier.handle_action("call-back:%s" % ADA)
    assert calls == [ADA]
    notifier.handle_action("ring-next-time:%s" % ADA)
    assert stars == [ADA]
    notifier.handle_action("block-contact:%s" % ADA)
    assert blocks == [ADA]
    notifier.handle_action("callback:%s" % ADA)
    notifier.handle_action("star:%s" % ADA)
    notifier.handle_action("block:%s" % ADA)
    assert calls == [ADA, ADA]
    assert stars == [ADA, ADA]
    assert blocks == [ADA, ADA]
    notifier.handle_action("bogus")
    notifier.handle_action("call-back:")
    notifier.handle_action("")
    assert calls == [ADA, ADA]


def test_post_batch_bypasses_policy(tmp_path):
    settings = app_config.load(tmp_path)
    settings["silenced_notif_policy"] = "never"
    app_config.save(settings, tmp_path)
    notifier, sink = _notifier(tmp_path)
    notifier.post_batch(3, ADA, "unknown caller")
    assert sink.cards[-1]["summary"] == "3 silenced calls"


def test_notify_params_build_real_variant(tmp_path):
    """The real Notify tuple must construct under real PyGObject.

    A pre-built ``a{sv}`` Variant nested in the tuple raises KeyError
    at send time (silently dropping every real card); the plain-dict
    shape builds fine, with and without hints.
    """
    import pytest

    GLib = pytest.importorskip("gi.repository.GLib")

    from xx_dialer.ringer import notify_params

    params = notify_params(
        GLib, "XX Dialer", 1, "Silenced call", "body",
        [("callback:+14155550100", "Call back")],
        {"sound-file": "/tones/ring.wav"})
    unpacked = params.unpack()
    assert unpacked[3] == "Silenced call"
    assert unpacked[5] == ["callback:+14155550100", "Call back"]
    empty = notify_params(GLib, "XX Dialer", 1, "Silenced call",
                          "body", [], {})
    assert empty.unpack()[3] == "Silenced call"


def test_no_bus_never_raises(tmp_path):
    notifier = Notifier(db_file=tmp_path / "calls.db",
                        home=tmp_path)
    notifier.screened(ADA, "Silence", "unknown caller", at_ms=WALL)
    notifier.handle_action("callback:%s" % ADA)
    notifier.dismiss_silenced()
    assert notifier.flush_daily_if_due(WALL) is False
