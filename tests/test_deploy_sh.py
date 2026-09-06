"""scripts/deploy.sh must update /usr/share/xx-wm and the running shell."""
import os
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'deploy.sh'


def test_default_user_is_dr3k():
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'XX_WM_USER:-dr3k' in text
    assert 'XX_WM_USER:-user}' not in text


def test_rsyncs_to_usr_share_via_staging():
    text = SCRIPT.read_text(encoding='utf-8')
    assert '/usr/share/xx-wm' in text
    assert '/tmp/xx-wm-deploy' in text
    assert '~/xx-wm/src' not in text
    assert ':xx-wm/src/' not in text
    assert '--chown=root:root' in text


def test_datadir_rsync_does_not_delete():
    text = SCRIPT.read_text(encoding='utf-8')
    # Staging may --delete; the datadir copy must not.
    assert 'sudo rsync -a --chown=root:root' in text
    assert 'sudo rsync -a --delete' not in text
    assert 'sudo rsync -a --chown=root:root /tmp/xx-wm-deploy/ /usr/share/xx-wm/' in text \
        or 'sudo rsync -a --chown=root:root ${STAGE}/ ${DEST}/' in text


def test_restart_sigusr1s_unique_python_child():
    text = SCRIPT.read_text(encoding='utf-8')
    assert '/usr/share/xx-wm/main.py' in text
    assert 'comm=' in text
    assert '[ "$c" = python3 ]' in text
    assert 'kill -USR1' in text
    assert 'kill -TERM' not in text
    assert 'kill -15' not in text
    assert 'kill -HUP' not in text
    assert 'DBUS_SESSION_BUS_ADDRESS' in text
    assert 'is-active' in text


def test_dry_run_prints_real_paths():
    text = SCRIPT.read_text(encoding='utf-8')
    assert '--dry-run' in text
    assert '[dry-run]' in text
    env = os.environ.copy()
    env['PIERCING_DEVICE'] = '192.168.1.129'
    result = subprocess.run(
        ['sh', str(SCRIPT), '--dry-run'],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert '/usr/share/xx-wm/' in out
    assert '/tmp/xx-wm-deploy/' in out
    assert 'xx-wm/src/' not in out
    assert 'SIGUSR1' in out
    assert '/usr/share/xx-wm/main.py' in out


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
