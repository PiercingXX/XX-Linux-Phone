"""Headless smoke tests: unit conversion and sync-wire helpers."""

from xx_vitals import sync_wire, units


def test_kg_lb_round_trip():
    assert units.kg_to_lb(100.0) == 100.0 / 0.45359237
    assert abs(units.lb_to_kg(units.kg_to_lb(82.3)) - 82.3) < 1e-9


def test_distance_conversions():
    assert units.metres_to_miles(1609.344) == 1609.344 / 1609.344
    assert units.metres_to_km(1500.0) == 1.5
    assert units.km_to_metres(2.0) == 2000.0


def test_display_formatting_follows_units():
    assert "lb" in units.format_weight(70.0, "imperial")
    assert "kg" in units.format_weight(70.0, "metric")
    assert "mi" in units.format_distance(1000.0, "imperial")
    assert "km" in units.format_distance(1000.0, "metric")


def test_iso_round_trip():
    assert sync_wire.from_iso(sync_wire.to_iso(1_700_000_000_000)) == 1_700_000_000_000


def test_status_mapping_matches_android_engine():
    assert sync_wire.map_status(200) == "ok"
    assert sync_wire.map_status(401) == "auth_expired"
    assert sync_wire.map_status(403) == "auth_expired"
    assert sync_wire.map_status(507) == "volume_full"
    assert sync_wire.map_status(422) == "rejected"
    assert sync_wire.map_status(500) == "transient"
    assert sync_wire.map_status(None) == "transient"


def test_cleartext_policy():
    assert sync_wire.check_url("https://nas:8443/v1/daily", set())
    assert not sync_wire.check_url("http://nas:8080/v1/daily", set())
    assert sync_wire.check_url("http://nas:8080/v1/daily", {"nas:8080"})
    assert not sync_wire.check_url("http://evil:8080/v1/daily", {"nas:8080"})
