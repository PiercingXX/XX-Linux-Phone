"""Location parsing/selection. No modem, no GeoClue daemon."""

from __future__ import annotations

from xx_find import location


def test_gga_fix_parsed():
    fix = location.parse_nmea_latlon(
        "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")
    assert fix is not None
    assert abs(fix[0] - 48.1173) < 1e-3
    assert abs(fix[1] - 11.5167) < 1e-3


def test_rmc_fix_parsed_and_void_rejected():
    fix = location.parse_nmea_latlon(
        "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,,,A*6A")
    assert fix is not None
    assert location.parse_nmea_latlon(
        "$GPRMC,123519,V,4807.038,N,01131.000,E,022.4,084.4,230394,,,N*53") \
        is None


def test_no_fix_sentences_rejected():
    assert location.parse_nmea_latlon(
        "$GPGGA,123519,,,,,,0,,,,,,,,*66") is None
    assert location.parse_nmea_latlon("$GPGSV,1,1,01,02,,,45*7A") is None
    assert location.parse_nmea_latlon("not nmea") is None
    assert location.parse_nmea_latlon(None) is None


def test_cell_normalized():
    cell = location.normalize_cell_3gpp("214", "03", "21071", "7033737")
    assert cell == {"mcc": 214, "mnc": 3, "lac": 21071, "cid": 7033737}
    assert location.normalize_cell_3gpp("214", "xx", "1", "2") is None


def test_pick_fix_prefers_geoclue_then_freshest():
    cell = {"lat": 0.0, "lon": 0.0, "source": "modem-cell", "epoch": 999,
            "acc_m": 10}
    gps = {"lat": 1.0, "lon": 1.0, "source": "modem-gps", "epoch": 1,
           "acc_m": 5}
    clue = {"lat": 2.0, "lon": 2.0, "source": "geoclue", "epoch": 1,
            "acc_m": 5000}
    assert location.pick_fix([cell, gps, clue]) == clue
    assert location.pick_fix([cell, gps]) == gps
    newer = dict(gps, epoch=1000, acc_m=5000)
    older = dict(gps, epoch=2, acc_m=3)
    assert location.pick_fix([newer, older]) == newer


def test_pick_fix_rejects_garbage():
    assert location.pick_fix([]) is None
    assert location.pick_fix([{"source": "geoclue"}]) is None
    assert location.pick_fix([{"lat": 99.0, "lon": 0.0,
                               "source": "geoclue", "epoch": 1}]) is None
