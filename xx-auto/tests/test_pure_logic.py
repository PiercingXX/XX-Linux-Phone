"""Pure-logic ports of the Android JVM tests (SessionPick, Elapsed,
FavouritePick, CarConnectDecision, SettingsMapper, MapsLaunch,
DriveTiles)."""
from xx_auto.car_decision import Outcome, decide
from xx_auto.drive_tiles import Tile, tile_for, visible
from xx_auto.elapsed import format_ms, row
from xx_auto.favourite_pick import Number, NumberType, primary_number
from xx_auto.maps_resolve import Target, resolve
from xx_auto.session_pick import PlayerState, active
from xx_auto.settings_store import Settings, parse_settings


def test_session_pick_playing_wins():
    states = [PlayerState(False, True), PlayerState(True, True)]
    assert active(states) == 1


def test_session_pick_most_recent_item_wins():
    states = [PlayerState(False, False), PlayerState(False, True)]
    assert active(states) == 1


def test_session_pick_none():
    assert active([PlayerState(False, False)]) is None
    assert active([]) is None


def test_elapsed_format():
    assert format_ms(0) == "0:00"
    assert format_ms(754000) == "12:34"
    assert format_ms(2310000) == "38:30"
    assert format_ms(3723000) == "1:02:03"
    assert format_ms(-5000) == "0:00"


def test_elapsed_row_clamps():
    assert row(754000, 2310000) == "12:34 / 38:30"
    assert row(9999000, 2310000) == "38:30 / 38:30"


def test_primary_number_prefers_mobile():
    home = Number("+1 555 0100", NumberType.HOME)
    mobile = Number("+1 555 0142", NumberType.MOBILE)
    assert primary_number([home, mobile]) is mobile
    assert primary_number([home]) is home
    assert primary_number([]) is None


def test_car_decision_open_close_ignore():
    assert decide("connected", "AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff",
                  True, False) is Outcome.OPEN
    assert decide("disconnected", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  True, True) is Outcome.CLOSE
    assert decide("disconnected", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  True, False) is Outcome.IGNORE
    assert decide("connected", "11:22", "AA:BB:CC:DD:EE:FF",
                  True, False) is Outcome.IGNORE
    assert decide("connected", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  False, False) is Outcome.IGNORE
    assert decide("connected", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  True, False, screen_in_front=True) is Outcome.IGNORE
    assert decide("bogus", "AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF",
                  True, False) is Outcome.IGNORE


def test_settings_defaults_and_round_trip():
    settings = parse_settings({})
    assert settings == Settings()
    assert settings.auto_launch is False
    assert settings.surface_calls is False
    assert settings.always_ink is True
    back = parse_settings(settings.to_dict())
    assert back == settings
    assert "auto_launch_device" not in Settings().to_dict()
    assert parse_settings({"auto_launch": "yes"}).auto_launch is False
    assert parse_settings(None) == Settings()


def test_maps_resolve_order():
    assert resolve(True, False) is Target.DRIVE
    assert resolve(False, True) is Target.LAUNCHER
    assert resolve(False, False) is Target.NOT_INSTALLED


def test_drive_tiles_gating():
    assert visible(True, True, False) == {Tile.RADIO, Tile.AUDIOBOOK,
                                          Tile.MAPS}
    assert visible(False, False, False) == set()
    assert Tile.CALLS in visible(True, True, True)
    assert Tile.CALLS not in visible(True, True, False)


def test_drive_tile_for_player():
    assert tile_for("io.piercingxx.Radio") is Tile.RADIO
    assert tile_for("io.piercingxx.Audiobook", "xx-audiobook") is Tile.AUDIOBOOK
    assert tile_for("", "Harbor Radio") is Tile.RADIO
    assert tile_for("org.mpris.vlc", "VLC") is None
