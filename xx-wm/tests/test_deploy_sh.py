"""deploy.sh must update the installed shell, not a stale home copy.

Meson installs to /usr/share/xx-wm/ and the wrapper execs that path, so
rsyncing to ~/xx-wm/src/ is a no-op on an installed device. Restart must
not start a second shell under GDM: only restart the user unit when it is
active, else SIGUSR1 the unique python child (the wrapper respawns it).
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
DEPLOY_SH = REPO / 'scripts' / 'deploy.sh'


@pytest.fixture(scope='module')
def deploy_script():
    assert DEPLOY_SH.is_file(), f'missing {DEPLOY_SH}'
    return DEPLOY_SH.read_text()


def test_default_user_is_dr3k(deploy_script):
    assert 'XX_WM_USER:-dr3k' in deploy_script


def test_targets_installed_datadir(deploy_script):
    assert '/usr/share/xx-wm/' in deploy_script
    assert '/tmp/xx-wm-deploy' in deploy_script
    assert '~/xx-wm/src' not in deploy_script
    assert ':xx-wm/src/' not in deploy_script


def test_no_delete_on_datadir(deploy_script):
    assert '--chown=root:root' in deploy_script
    for line in deploy_script.splitlines():
        if '/usr/share/xx-wm' in line and 'rsync' in line:
            assert '--delete' not in line


def test_tight_pgrep_pattern(deploy_script):
    assert '[p]ython3 /usr/share/xx-wm/main.py' in deploy_script


def test_sigusr1_not_term_hup(deploy_script):
    assert 'kill -USR1' in deploy_script
    assert 'kill -TERM' not in deploy_script
    assert 'kill -15' not in deploy_script
    assert 'kill -HUP' not in deploy_script


def test_dbus_address_exported(deploy_script):
    assert 'DBUS_SESSION_BUS_ADDRESS' in deploy_script


def test_dry_run_exists(deploy_script):
    assert '--dry-run' in deploy_script


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(DEPLOY_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
