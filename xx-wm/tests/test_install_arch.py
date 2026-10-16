"""Arch/pacman support, runtime deps, and fonts in scripts/install.sh.

The smoke tablet is Arch; the installer must know pacman, pull
python-pywayland (empty switcher without it), and install fonts via
distro packages (never failing the install on a font).
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
SESSION_IN = REPO / 'launcher' / 'data' / 'xx-wm-session.in'


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


def _function_body(script, name):
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return m.group(1) if m else ''


def test_pacman_branch_exists(install_script):
    assert 'PKG=pacman' in install_script
    assert 'pacman' in install_script
    assert 'need apk, apt, or pacman' in install_script


def test_pkg_install_pacman(install_script):
    body = _function_body(install_script, 'pkg_install')
    assert 'pacman -S --needed --noconfirm' in body


def test_whiptail_bootstrap_libnewt(install_script):
    assert 'pkg_install libnewt' in install_script
    assert 'pkg_install newt' in install_script
    assert 'pkg_install whiptail' in install_script
    for line in install_script.splitlines():
        if 'pkg_install' in line and 'pacman' in line:
            assert 'whiptail' not in line


def test_install_deps_pacman_list(install_script):
    body = _function_body(install_script, 'install_deps')
    for pkg in ('python-gobject', 'gtk4-layer-shell', 'python-pywayland',
                'geoclue', 'networkmanager', 'phoc', 'squeekboard',
                'brightnessctl'):
        assert pkg in body, f'pacman deps miss {pkg}'


def test_install_deps_apk_apt_brightness(install_script):
    body = _function_body(install_script, 'install_deps')
    assert 'brightnessctl' in body
    assert 'py3-wayland' in install_script
    assert 'py3-pywayland' not in install_script
    assert 'python3-pywayland' in install_script


def test_install_deps_pacman_no_lisgd_package(install_script):
    body = _function_body(install_script, 'install_deps')
    for line in body.splitlines():
        if 'pacman' in line:
            assert 'lisgd' not in line
    assert '~mil/lisgd' in install_script
    assert 'SKIP' in install_script


def test_install_fonts_defined_and_wired(install_script):
    body = _function_body(install_script, 'install_fonts')
    assert body, 'install_fonts not defined'
    assert 'ttf-jetbrains-mono-nerd' in body
    assert 'ttf-space-mono-nerd' in body
    assert 'fc-cache' in body
    assert 'install_fonts' in _function_body(install_script, 'build_install')


def test_install_fonts_never_fails(install_script):
    body = _function_body(install_script, 'install_fonts')
    assert 'return 1' not in body


def test_elogind_dropin_installed_when_present(install_script):
    body = _function_body(install_script, 'install_logind_dropin')
    assert body, 'install_logind_dropin not defined'
    assert '/etc/elogind/logind.conf.d' in body
    assert 'install_logind_dropin' in _function_body(install_script, 'build_install')


def test_session_has_no_fontconfig_path():
    text = SESSION_IN.read_text()
    assert 'FONTCONFIG_PATH' not in text


def test_script_syntax_parses(install_script):
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
