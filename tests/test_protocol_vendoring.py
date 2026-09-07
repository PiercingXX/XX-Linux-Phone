"""Tests for the vendored Wayland protocol XML files under launcher/data/.

Each XML file must exist, parse as well-formed XML, and declare the expected
protocol interfaces. These files are used by the shell's pywayland client to
bind the foreign-toplevel and core Wayland globals, so a malformed or missing
file breaks the toplevel listing at runtime.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

LAUNCHER_DATA = Path(__file__).parent.parent / 'launcher' / 'data'

WLR_XML = LAUNCHER_DATA / 'wlr-foreign-toplevel-management-unstable-v1.xml'
WLR_SCREENCOPY_XML = LAUNCHER_DATA / 'wlr-screencopy-unstable-v1.xml'
WAYLAND_XML = LAUNCHER_DATA / 'wayland.xml'

# All core Wayland interfaces the vendored wayland.xml must declare.
CORE_INTERFACES = [
    'wl_display',
    'wl_registry',
    'wl_callback',
    'wl_compositor',
    'wl_shm_pool',
    'wl_shm',
    'wl_buffer',
    'wl_data_offer',
    'wl_data_source',
    'wl_data_device',
    'wl_data_device_manager',
    'wl_shell',
    'wl_shell_surface',
    'wl_surface',
    'wl_seat',
    'wl_pointer',
    'wl_keyboard',
    'wl_touch',
    'wl_output',
    'wl_region',
    'wl_subcompositor',
    'wl_subsurface',
]


def _interface_names(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    return {iface.get('name') for iface in root.findall('interface')}


@pytest.mark.parametrize('path', [WLR_XML, WLR_SCREENCOPY_XML, WAYLAND_XML])
def test_xml_file_exists(path: Path) -> None:
    assert path.is_file(), f'vendored protocol XML missing: {path}'


@pytest.mark.parametrize('path', [WLR_XML, WLR_SCREENCOPY_XML, WAYLAND_XML])
def test_xml_is_well_formed(path: Path) -> None:
    # Raises ET.ParseError if the file is not well-formed XML.
    ET.parse(path)


def test_wlr_xml_declares_both_toplevel_interfaces() -> None:
    names = _interface_names(WLR_XML)
    assert 'zwlr_foreign_toplevel_manager_v1' in names
    assert 'zwlr_foreign_toplevel_handle_v1' in names


def test_screencopy_xml_declares_manager_and_frame() -> None:
    names = _interface_names(WLR_SCREENCOPY_XML)
    assert 'zwlr_screencopy_manager_v1' in names
    assert 'zwlr_screencopy_frame_v1' in names


def test_wayland_xml_declares_all_core_interfaces() -> None:
    names = _interface_names(WAYLAND_XML)
    missing = [name for name in CORE_INTERFACES if name not in names]
    assert not missing, f'wayland.xml missing core interfaces: {missing}'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])