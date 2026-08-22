"""Tests for the 20.5 per-device phoc.ini scale.

The tablet's panel reports as ``DSI-1`` (wanting scale 1.5) and collides with
the FP5's ``DSI-1`` (scale 2.5), so a single phoc.ini cannot serve both
devices. install.sh must therefore pick a per-device phoc.ini fragment and
copy it over the meson-installed default. This is a shell script, so the test
reads the script text and the fragment files and asserts the selection logic
is present, correct, and actually wired into the install path (not just
defined).
"""
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
INSTALL_SH = REPO / 'scripts' / 'install.sh'
PHOC_DIR = REPO / 'launcher' / 'data' / 'phoc'

# device name -> (output section, expected scale)
EXPECTED = {
    'fairphone-5': ('DSI-1', '2.5'),
    'furiphone-flx1': ('HWCOMPOSER-1', '3'),
    'librem-5': ('DSI-1', '2'),
    'tablet': ('DSI-1', '1.5'),
}


@pytest.fixture(scope='module')
def install_script():
    assert INSTALL_SH.is_file(), f'missing {INSTALL_SH}'
    return INSTALL_SH.read_text()


def _function_body(script, name):
    import re
    m = re.search(re.escape(name) + r'\(\) \{(.*?)^\}', script, re.M | re.S)
    return m.group(1) if m else ''


def test_per_device_fragments_exist():
    """Every supported device must have a phoc.ini fragment shipped."""
    for dev in EXPECTED:
        assert (PHOC_DIR / f'{dev}.ini').is_file(), f'missing {dev}.ini'


def test_fragment_scale_matches_device():
    """Each fragment must set the documented scale on the right output."""
    import configparser
    for dev, (output, scale) in EXPECTED.items():
        cfg = configparser.ConfigParser()
        cfg.read(PHOC_DIR / f'{dev}.ini')
        section = f'output:{output}'
        assert section in cfg, f'{dev}.ini missing [{section}] section'
        assert cfg[section]['scale'] == scale, \
            f'{dev}.ini scale != {scale}'


def test_fragment_disables_xwayland():
    """The fragments must keep the core hardening from the default file."""
    import configparser
    for dev in EXPECTED:
        cfg = configparser.ConfigParser()
        cfg.read(PHOC_DIR / f'{dev}.ini')
        assert cfg['core'].getboolean('xwayland') is False


def test_install_script_selects_fragment(install_script):
    """install.sh must copy the chosen device's fragment over phoc.ini."""
    body = _function_body(install_script, 'select_phoc_scale')
    assert 'whiptail' in body
    assert 'launcher/data/phoc' in body
    assert '.ini' in body
    assert 'cp' in body
    assert '/usr/share/xx-wm/phoc.ini' in body


def test_wired_into_install_path(install_script):
    """select_phoc_scale must run from build_install (shared Install/Update
    path), not just be defined — otherwise the scale is never applied."""
    assert 'select_phoc_scale' in _function_body(install_script, 'build_install')


def test_script_syntax_parses(install_script):
    """The script must still parse as valid POSIX sh (sh -n)."""
    import subprocess
    result = subprocess.run(
        ['sh', '-n', str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr