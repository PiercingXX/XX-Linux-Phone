from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import xx_captions
from xx_captions import dbus_api

SRC_DIR = Path(__file__).resolve().parent.parent / "src"


def test_names_are_consistent() -> None:
    assert dbus_api.BUS_NAME == "io.piercingxx.Captions1"
    assert dbus_api.OBJECT_PATH == "/io/piercingxx/Captions1"
    assert dbus_api.INTERFACE == dbus_api.BUS_NAME


def test_introspection_xml_matches_contract() -> None:
    root = ET.fromstring(dbus_api.INTROSPECTION_XML)
    iface = root.find(f"./interface[@name='{dbus_api.INTERFACE}']")
    assert iface is not None
    methods = {m.get("name"): m for m in iface.findall("method")}
    assert set(methods) == {"Start", "Stop", "ListTranscripts", "GetTranscript"}
    start_args = methods["Start"].findall("arg")
    assert [(a.get("name"), a.get("type"), a.get("direction"))
            for a in start_args] == [("source", "s", "in")]
    out_args = methods["ListTranscripts"].findall("arg")
    assert [(a.get("name"), a.get("type")) for a in out_args] == [
        ("transcripts", "a(ssxx)")]
    get_args = methods["GetTranscript"].findall("arg")
    assert [a.get("name") for a in get_args] == [
        "id", "peer", "started", "ended", "lines"]
    signals = {s.get("name"): s for s in iface.findall("signal")}
    text_args = signals["Text"].findall("arg")
    assert [(a.get("name"), a.get("type")) for a in text_args] == [
        ("text", "s"), ("final", "b")]


def test_validate_source() -> None:
    for source in ("playback", "call", "mic"):
        assert dbus_api.validate_source(source) == source
    for bad in ("", "bluetooth", None, 42):
        with pytest.raises(dbus_api.ContractError):
            dbus_api.validate_source(bad)


def test_validate_text() -> None:
    assert dbus_api.validate_text("hello", True) == ("hello", True)
    for text, final in (("", True), (None, True), ("hi", 1), ("hi", "x")):
        with pytest.raises(dbus_api.ContractError):
            dbus_api.validate_text(text, final)


def test_unpack_single_takes_first_out_arg() -> None:
    assert dbus_api.unpack_single(({"a": 1},)) == {"a": 1}
    with pytest.raises(dbus_api.ContractError):
        dbus_api.unpack_single({"a": 1})
    with pytest.raises(dbus_api.ContractError):
        dbus_api.unpack_single(())


def test_version_entrypoint() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "xx_captions", "--version"],
        capture_output=True, text=True, timeout=30, cwd=str(SRC_DIR),
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == xx_captions.__version__
