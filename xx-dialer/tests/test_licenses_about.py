"""Rules ends in an About section opening the licenses dialog."""
from pathlib import Path

import xx_dialer


def _src(name: str) -> str:
    return (Path(xx_dialer.__file__).parent / name).read_text()


def test_rules_about_section_opens_licenses():
    src = _src("ui_rules.py")
    assert "about_content" in src
    assert "show_licenses_dialog" in src
    assert "standard_license_entries" in src


def test_about_table_names_this_app():
    from xx_ui import widgets

    entries = widgets.standard_license_entries("Dialer")
    assert entries[0].name == "Dialer"
    assert "PyNaCl" not in {entry.name for entry in entries}
