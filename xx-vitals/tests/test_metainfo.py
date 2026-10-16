"""Metainfo, desktop entry and D-Bus activation stay consistent.

The .desktop is DBusActivatable, so a session service file must ship with
Name == the application id; the metainfo launchable/provides must name the
same ids. Screenshots stay comment placeholders until hosted image URLs
exist (imageless entries fail appstreamcli validation).
"""

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
DATA = REPO / "data"
APP_ID = "io.piercingxx.XXVitals"


def _metainfo_root():
    return ET.parse(DATA / "io.piercingxx.XXVitals.metainfo.xml").getroot()


def test_metainfo_id_launchable_provides_agree():
    root = _metainfo_root()
    assert root.findtext("id") == APP_ID
    launchable = root.find("launchable[@type='desktop-id']")
    assert launchable is not None
    assert launchable.text == f"{APP_ID}.desktop"
    provides = root.find("provides")
    assert provides is not None
    dbus = provides.find("dbus[@type='session']")
    assert dbus is not None and dbus.text == APP_ID
    assert provides.findtext("binary") == "xx-vitals"


def test_metainfo_has_licenses_and_valid_category():
    root = _metainfo_root()
    assert root.findtext("metadata_license") == "CC0-1.0"
    assert root.findtext("project_license")
    categories = [node.text for node in root.findall("categories/category")]
    assert categories == ["Utility"]


def test_metainfo_keeps_screenshot_placeholders():
    text = (DATA / "io.piercingxx.XXVitals.metainfo.xml").read_text(encoding="utf-8")
    assert "Screenshot placeholders" in text
    assert _metainfo_root().find("screenshots") is None


def test_desktop_entry_is_activatable():
    text = (DATA / "io.piercingxx.XXVitals.desktop.in").read_text(encoding="utf-8")
    assert "DBusActivatable=true" in text
    assert "Exec=@bindir@/xx-vitals" in text
    assert "Categories=Utility;" in text


def test_dbus_service_file_matches_app_id():
    text = (DATA / "io.piercingxx.XXVitals.service.in").read_text(encoding="utf-8")
    assert f"Name={APP_ID}" in text
    assert "Exec=@bindir@/xx-vitals" in text


def test_meson_installs_activation_file():
    meson = (REPO / "meson.build").read_text(encoding="utf-8")
    assert "io.piercingxx.XXVitals.service.in" in meson
    assert "dbus-1" in meson


def test_appstream_validation_has_no_errors():
    appstreamcli = shutil.which("appstreamcli")
    if appstreamcli is None:
        pytest.skip("appstreamcli missing")
    completed = subprocess.run(
        [appstreamcli, "validate",
         str(DATA / "io.piercingxx.XXVitals.metainfo.xml")],
        capture_output=True, text=True, timeout=60,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    assert "\nE: " not in output, output
