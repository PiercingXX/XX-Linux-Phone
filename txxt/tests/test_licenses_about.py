"""Settings ends in an About section opening the licenses page."""
from pathlib import Path

import txxt


def _src(name: str) -> str:
    return (Path(txxt.__file__).parent / name).read_text()


def test_settings_about_section_opens_licenses():
    src = _src("settings_page.py")
    assert "about_content" in src
    assert "licenses_page" in src
    assert "standard_license_entries" in src


def test_about_table_names_this_app():
    from xx_ui import widgets

    entries = widgets.standard_license_entries("TxxT")
    assert entries[0].name == "TxxT"
    assert "PyNaCl" not in {entry.name for entry in entries}
