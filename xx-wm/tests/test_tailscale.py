"""Tailscale shade tile: CLI backend (against a fake `tailscale` on PATH)
and the panel wiring (tile gate, state line, sign-in link)."""
import json
import os
import sys
import textwrap
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import tailscale  # noqa: E402
from test_captions_tile import quick_actions  # noqa: E402  (private fake-gi copy)

_REAL_AVAILABLE = tailscale.available


@pytest.fixture(autouse=True)
def _real_probe(monkeypatch):
    # conftest hides the host's Tailscale; these tests drive a fake CLI
    monkeypatch.setattr(tailscale, 'available', _REAL_AVAILABLE)


def _status_json(backend, host='flx1', auth=''):
    return json.dumps({'BackendState': backend, 'AuthURL': auth,
                       'Self': {'HostName': host}})


# --- parse_status -------------------------------------------------------------

def test_parse_running_shows_device_name():
    assert tailscale.parse_status(_status_json('Running')) == \
        tailscale.Status(tailscale.RUNNING, 'flx1')


def test_parse_needs_login_carries_auth_url():
    st = tailscale.parse_status(
        _status_json('NeedsLogin', auth='https://login.tailscale.com/a/x'))
    assert st.state == tailscale.NEEDS_LOGIN
    assert st.detail == tailscale.SIGN_IN
    assert st.auth_url == 'https://login.tailscale.com/a/x'


@pytest.mark.parametrize('raw', ['', 'not json', '[]',
                                 json.dumps({'BackendState': 'Weird'})])
def test_parse_garbage_is_unavailable(raw):
    assert tailscale.parse_status(raw).state == tailscale.UNAVAILABLE


def test_parse_stopped():
    assert tailscale.parse_status(_status_json('Stopped')).state == \
        tailscale.STOPPED


# --- CLI round trips with a fake tailscale ------------------------------------

@pytest.fixture()
def fake_cli(tmp_path, monkeypatch):
    """A `tailscale` stub: state lives in a file; `up` needs a login the
    first time (prints nothing, exposes AuthURL, keeps running)."""
    state = tmp_path / 'state'
    state.write_text('Stopped')
    script = tmp_path / 'bin' / 'tailscale'
    script.parent.mkdir()
    script.write_text(textwrap.dedent(f'''\
        #!/bin/sh
        S={state}
        case "$1" in
        status)
            st=$(cat "$S")
            auth=""
            [ "$st" = NeedsLogin ] && auth=https://login.tailscale.com/a/test
            printf '{{"BackendState":"%s","AuthURL":"%s","Self":{{"HostName":"flx1"}}}}' "$st" "$auth"
            [ "$st" = Running ] || exit 1 ;;
        down)
            [ -f {tmp_path}/deny ] && {{ echo "Access denied: prefs write access denied" >&2; exit 1; }}
            echo Stopped > "$S" ;;
        up)
            [ -f {tmp_path}/deny ] && {{ echo "Access denied: prefs write access denied" >&2; exit 1; }}
            if [ -f {tmp_path}/loggedin ]; then echo Running > "$S"; exit 0; fi
            echo NeedsLogin > "$S"; sleep 30 ;;
        esac
        '''))
    script.chmod(0o755)
    monkeypatch.setenv('PATH', f'{script.parent}{os.pathsep}{os.environ["PATH"]}')
    return tmp_path


def test_status_reads_json_despite_nonzero_exit(fake_cli):
    assert tailscale.status() == tailscale.Status(tailscale.STOPPED)


def test_up_when_logged_in_connects(fake_cli):
    (fake_cli / 'loggedin').touch()
    assert tailscale.up(wait_s=5) == tailscale.Status(tailscale.RUNNING, 'flx1')


def test_up_needing_login_returns_auth_url_without_waiting(fake_cli):
    st = tailscale.up(wait_s=5)
    assert st.state == tailscale.NEEDS_LOGIN
    assert st.auth_url == 'https://login.tailscale.com/a/test'


def test_access_denied_says_no_permission(fake_cli):
    (fake_cli / 'deny').touch()
    assert tailscale.up(wait_s=5).detail == tailscale.NO_PERMISSION
    assert tailscale.down().detail == tailscale.NO_PERMISSION


def test_down(fake_cli):
    (fake_cli / 'state').write_text('Running')
    assert tailscale.down().state == tailscale.STOPPED


def test_missing_cli_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv('PATH', str(tmp_path))
    assert not tailscale.available()
    assert tailscale.status().state == tailscale.UNAVAILABLE


# --- panel wiring -------------------------------------------------------------

def test_state_line_and_settings_section():
    fmt = quick_actions.format_tile_state
    assert fmt('tailscale', True, 'flx1') == 'flx1'
    assert fmt('tailscale', False, tailscale.SIGN_IN) == 'Sign in'
    assert fmt('tailscale', False, '') == 'Off'
    assert quick_actions.tile_settings_section('tailscale') == 'services'


class _Label:
    text = None

    def set_text(self, text):
        self.text = text


def _bare_panel(installed, opened=None):
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._hw_gate_cache = {'tailscale': installed, 'auto_br': False,
                            'torch': False, 'record': False,
                            'captions': False, 'hotspot': False,
                            'night': False, 'location': False}
    panel._dnd = panel._focus = None
    panel._als = panel._location = SimpleNamespace(available=lambda: False)
    panel._tile_buttons, panel._tile_state = {}, {}
    panel._tile_details = {}
    panel._state_labels = {'tailscale': _Label()}
    panel._on_open_uri = (opened.append if opened is not None else None)
    return panel


def test_tile_hidden_without_tailscale():
    keys = [t.key for t in _bare_panel(False)._tiles()]
    assert 'tailscale' not in keys


def test_tile_shown_and_bound_when_installed():
    panel = _bare_panel(True)
    tile = next(t for t in panel._tiles() if t.key == 'tailscale')
    assert tile.set_state == panel._set_tailscale


def test_sign_in_result_opens_link_and_labels_tile():
    opened = []
    panel = _bare_panel(True, opened)
    panel._apply_tailscale(tailscale.Status(
        tailscale.NEEDS_LOGIN, tailscale.SIGN_IN, 'https://login.tailscale.com/a/x'))
    assert opened == ['https://login.tailscale.com/a/x']
    assert panel._state_labels['tailscale'].text == 'Sign in'
    assert panel._tile_state['tailscale'] is False


def test_connected_result_shows_device():
    panel = _bare_panel(True, [])
    panel._apply_tailscale(tailscale.Status(tailscale.RUNNING, 'flx1'))
    assert panel._state_labels['tailscale'].text == 'flx1'
    assert panel._tile_state['tailscale'] is True
