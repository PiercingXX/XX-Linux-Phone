"""Settings exposes an About page with the open-source licenses page."""
from pathlib import Path

import xx_contacts


def _src(name: str) -> str:
    return (Path(xx_contacts.__file__).parent / name).read_text()


def test_about_page_exists_with_licenses():
    src = _src("settings_ui.py")
    assert "class AboutPage" in src
    assert "licenses_page" in src
    assert "standard_license_entries" in src


def test_about_table_names_this_app():
    from xx_ui import widgets

    entries = widgets.standard_license_entries("Contacts")
    assert entries[0].name == "Contacts"
    assert "PyNaCl" not in {entry.name for entry in entries}
