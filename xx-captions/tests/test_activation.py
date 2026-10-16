"""D-Bus activation file and service-name separation.

A background service must never own the same bus name as a GTK
application_id: the GUI would mistake itself for a secondary instance
and exit. The captions service name is pinned here; the future lane-3
GUI must use the bare application id instead.
"""
from __future__ import annotations

import configparser
from pathlib import Path

from xx_captions import dbus_api

ROOT = Path(__file__).resolve().parent.parent
SERVICE_FILE = ROOT / "data" / "io.piercingxx.Captions1.service.in"

FUTURE_GUI_APPLICATION_ID = "io.piercingxx.Captions"


def _service_config() -> configparser.ConfigParser:
    assert SERVICE_FILE.is_file(), "D-Bus activation file is missing"
    parser = configparser.ConfigParser()
    parser.read(SERVICE_FILE, encoding="utf-8")
    return parser


def test_activation_file_names_service() -> None:
    parser = _service_config()
    assert parser.get("D-BUS Service", "Name") == dbus_api.BUS_NAME


def test_activation_file_runs_service_module() -> None:
    parser = _service_config()
    exec_line = parser.get("D-BUS Service", "Exec")
    assert exec_line.rstrip().endswith("-m xx_captions")


def test_service_name_is_not_a_gui_application_id() -> None:
    assert dbus_api.BUS_NAME != FUTURE_GUI_APPLICATION_ID
    assert dbus_api.BUS_NAME == "io.piercingxx.Captions1"


def test_error_names_live_under_service_name() -> None:
    assert dbus_api.ERR_NOT_PERMITTED == (
        "io.piercingxx.Captions1.Error.NotPermitted")
    assert dbus_api.ERR_NOT_FOUND == (
        "io.piercingxx.Captions1.Error.NotFound")
