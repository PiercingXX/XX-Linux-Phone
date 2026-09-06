"""Session enable policy and naming (workstream 1.3 / 1.6).

GDM starts xx-wm-session → phoc → xx-wm. Enabling the systemd user unit
(ExecStart=xx-wm, the Python shell) double-starts under GDM. When the
wayland-session file is installed, the user unit stays opt-in.
"""
from pathlib import Path
import re
import subprocess

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
OPENRC = REPO / 'launcher' / 'data' / 'openrc' / 'xx-wm'
MESON = REPO / 'launcher' / 'meson.build'
UNIT = REPO / 'launcher' / 'data' / 'systemd' / 'xx-wm.service'
DESKTOP = REPO / 'launcher' / 'data' / 'xx-wm.desktop.in'


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


def _function_body(script, name):
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return m.group(1) if m else ''


def test_enable_service_disables_user_unit_when_session_file_exists(
        install_script):
    body = _function_body(install_script, 'enable_service')
    assert 'wayland-sessions/xx-wm.desktop' in body
    assert 'disable --now xx-wm' in body
    assert 'enable --now xx-wm' not in body
    desktop_at = body.find('wayland-sessions/xx-wm.desktop')
    disable_at = body.find('disable --now xx-wm')
    enable_at = body.find('systemctl --user enable xx-wm')
    assert desktop_at != -1
    assert disable_at != -1
    assert enable_at != -1
    assert desktop_at < disable_at
    assert desktop_at < enable_at
    assert disable_at < enable_at


def test_opt_in_is_start_not_enable(install_script):
    body = _function_body(install_script, 'enable_service')
    assert 'start xx-wm' in body
    assert 'NOT already xx-wm-session' in body


def test_openrc_uses_meson_libexecdir():
    text = OPENRC.read_text(encoding='utf-8')
    assert '@libexecdir@/xx-wm-session' in text
    assert '/usr/libexec/xx-wm-session' not in text
    meson = MESON.read_text(encoding='utf-8')
    assert "input: 'data/openrc/xx-wm'" in meson
    assert 'configure_file' in meson


def test_installer_names_the_xx_wm_session(install_script):
    assert 'PiercingOS' not in install_script
    assert 'Select the XX-WM session' in install_script


def test_unit_description_is_xx_wm():
    text = UNIT.read_text(encoding='utf-8')
    assert 'Description=XX-WM Shell' in text
    desktop = DESKTOP.read_text(encoding='utf-8')
    assert 'Name=XX-WM' in desktop


def test_script_syntax_parses(install_script):
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
