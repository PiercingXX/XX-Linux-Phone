"""Honest pedometer copy: off by default, no source never fakes zeroes."""

from xx_vitals.collect import status


def test_disabled_is_off_with_manual_fallback():
    line = status.describe(False, False, False)
    assert line.title == status.TITLE_OFF == "Step counting is off"
    assert "Manual activities still count" in line.detail
    assert not line.enabled and not line.has_source


def test_enabled_without_sensor_is_honest_gap():
    line = status.describe(True, False, False)
    assert line.title == status.TITLE_NO_SOURCE == "No step source"
    assert "gaps" in line.detail and "zeroes" in line.detail
    assert "Manual activities still count" in line.detail
    assert line.enabled and not line.has_source


def test_hardware_source_wins_and_is_named():
    line = status.describe(True, True, True)
    assert line.has_source and line.source == status.SOURCE_HARDWARE
    assert line.title == "Step counting is on"


def test_resident_source_when_only_accelerometer():
    line = status.describe(True, False, True)
    assert line.has_source and line.source == status.SOURCE_RESIDENT
    assert line.title == "Step counting is on"


def test_resident_copy_is_honest_about_suspend():
    line = status.describe(True, False, True)
    assert "only counted while the phone is awake" in line.detail
