"""Arch/pacman path in scripts/install.sh (workstream 1.2).

The smoke tablet is Arch. The installer used to exit
``Unsupported distro: need apk or apt.`` These tests pin the pacman
branch, non-fatal pkg_install, lisgd skip, and font install by reading
the script text (same idiom as test_install_input_group.py).
"""
from pathlib import Path
import re
import subprocess

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
APPS_SH = REPO / 'scripts' / 'apps.sh'


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


def _function_body(script, name):
    m = re.search(rf'^{name}\(\) \{{(.*?)^\}}', script, re.M | re.S)
    return m.group(1) if m else ''


def test_pkg_detects_pacman(install_script):
    assert 'PKG=pacman' in install_script
    assert 'need apk, apt, or pacman' in install_script
    assert 'need apk or apt.' not in install_script


def test_pkg_install_pacman_is_nonfatal(install_script):
    body = _function_body(install_script, 'pkg_install')
    assert 'pacman -S --needed --noconfirm' in body
    assert 'warn: some packages failed' in body


def test_whiptail_bootstrap_uses_libnewt_on_pacman(install_script):
    assert 'pkg_install libnewt' in install_script
    assert 'pkg_install newt' in install_script
    assert 'pkg_install whiptail' in install_script
    start = install_script.find('Installing whiptail')
    assert start != -1
    chunk = install_script[start:]
    chunk = chunk[:chunk.find('\nfi') + 3]
    pacman_arm = chunk.split('[ "$PKG" = pacman ]')[1].split('else')[0]
    assert 'pkg_install libnewt' in pacman_arm
    assert 'pkg_install whiptail' not in pacman_arm


def _pacman_install_deps(script):
    body = _function_body(script, 'install_deps')
    start = body.find('[ "$PKG" = pacman ]')
    assert start != -1, 'install_deps missing pacman branch'
    rest = body[start:]
    end = rest.find('\n    else')
    return rest if end == -1 else rest[:end]


def test_pacman_deps_include_runtime_stack(install_script):
    block = _pacman_install_deps(install_script)
    for name in ('python-gobject', 'gtk4-layer-shell', 'python-pywayland',
                 'geoclue', 'networkmanager', 'phoc', 'squeekboard',
                 'brightnessctl'):
        assert name in block, f'pacman install_deps missing {name}'


def test_apk_and_apt_install_brightnessctl(install_script):
    body = _function_body(install_script, 'install_deps')
    assert 'brightnessctl' in body
    assert body.count('pkg_install brightnessctl') >= 2


def test_pacman_does_not_pacman_s_lisgd(install_script):
    block = _pacman_install_deps(install_script)
    pkg_lines = '\n'.join(
        line for line in block.splitlines() if 'pkg_install' in line)
    assert 'lisgd' not in pkg_lines
    assert '~mil/lisgd' in install_script
    assert 'SKIP:' in install_script


def test_install_fonts_is_wired_and_nonfatal(install_script):
    body = _function_body(install_script, 'install_fonts')
    assert body, 'install_fonts is missing'
    assert 'ttf-jetbrains-mono-nerd' in body
    assert 'ttf-space-mono-nerd' in body
    assert 'install_fonts' in _function_body(install_script, 'build_install')
    # Never abort the install because a font package is missing.
    assert 'return 1' not in body


def test_apps_sh_understands_pacman():
    text = APPS_SH.read_text(encoding='utf-8')
    assert 'PKG=pacman' in text
    assert 'pacman -S --needed --noconfirm' in text


def test_script_syntax_parses(install_script):
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
