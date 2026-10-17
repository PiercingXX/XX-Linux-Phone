"""App identity and the extension points another app would use."""

import pytest

from xx_music import engine as e
from xx_music.appspec import (CORE_TABS, AppSpec, PlayerAction, Tab,
                              load_factory)
from xx_music.fake_engine import FakeEngine
from xx_radio import (__app_id__, __mpris_bus_name__, __player_bus_name__)
from xx_radio.main import SPEC


def _spec(**overrides):
    base = dict(app_id="org.example.Player", name="Example",
                dir_name="example-player", mpris_suffix="example_player",
                engine_factory=lambda _ctx: FakeEngine())
    base.update(overrides)
    return AppSpec(**base)


def test_xx_radio_identity():
    assert SPEC.app_id == __app_id__ == "io.piercingxx.XXRadio"
    assert SPEC.player_bus_name == __player_bus_name__
    assert SPEC.mpris_bus_name == __mpris_bus_name__
    assert SPEC.player_bus_name != SPEC.app_id
    assert SPEC.player_object_path == "/io/piercingxx/XXRadio/Player"
    assert SPEC.player_interface == "io.piercingxx.XXRadio.Player1"
    assert SPEC.desktop_entry == "io.piercingxx.XXRadio"
    assert SPEC.tab_keys() == ["home", "search", "library", "settings"]


def test_derived_app_gets_its_own_names_and_dirs():
    other = _spec()
    assert other.mpris_bus_name == "org.mpris.MediaPlayer2.example_player"
    assert other.player_bus_name == "org.example.Player.Player"
    assert other.paths().data_dir().name == "example-player"
    assert other.paths().data_dir() != SPEC.paths().data_dir()


def test_extra_tabs_sit_before_settings():
    tab = Tab("podcasts", "Podcasts", lambda ctx: None)
    spec = _spec(extra_tabs=(tab,))
    assert spec.tab_keys() == ["home", "search", "library", "podcasts",
                               "settings"]
    with pytest.raises(ValueError):
        _spec(extra_tabs=(Tab("home", "Home", lambda ctx: None),))
    with pytest.raises(ValueError):
        _spec(extra_tabs=(tab, tab))


def test_bad_identity_is_refused():
    with pytest.raises(ValueError):
        _spec(app_id="noDots")
    with pytest.raises(ValueError):
        _spec(mpris_suffix="has-dash")
    with pytest.raises(ValueError):
        _spec(dir_name="../escape")


def test_engine_factory_and_env_override(monkeypatch):
    assert isinstance(_spec().make_engine(), FakeEngine)
    assert SPEC.make_engine().key == "ytm"
    monkeypatch.setenv("XX_RADIO_ENGINE", "fake")
    assert isinstance(SPEC.make_engine(), FakeEngine)
    monkeypatch.setenv("XX_RADIO_ENGINE", "xx_music.fake_engine:create")
    assert isinstance(SPEC.make_engine(), FakeEngine)
    with pytest.raises(ValueError):
        load_factory("no-colon")
    bad = _spec(engine_factory=lambda _ctx: object())
    with pytest.raises(TypeError):
        bad.make_engine()


def test_player_actions_rate_through_the_engine():
    fake = FakeEngine(signed_in=True)
    track = fake.album("al-tide").tracks[0]
    by_id = {action.id: action for action in SPEC.player_actions}
    assert set(by_id) == {"like", "dislike"}
    assert by_id["like"].enabled(fake, track)
    assert not by_id["like"].enabled(fake, None)
    by_id["like"].handler(fake, track)
    assert fake.ratings[track.id] == e.Rating.LIKE
    plain = e.Engine()
    assert not by_id["dislike"].enabled(plain, track)
    duplicate = PlayerAction("x", "X", "", lambda _e, _t: None)
    with pytest.raises(ValueError):
        _spec(player_actions=(duplicate, duplicate))


def test_core_tabs_order():
    assert CORE_TABS[-1] == "settings"
