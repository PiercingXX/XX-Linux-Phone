"""Tests for system_settings.py parsers (pure logic, injected output)."""
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from system_settings import connect_wifi, parse_nmcli_wifi, parse_pactl_sinks

NMCLI_OUTPUT = """\
*:HomeNet:87:WPA2
 :HomeNet:45:WPA2
 :CoffeeShop:62:
 :Neighbor\\:5G:31:WPA3
 ::99:WPA2
 :Weak:notanumber:WPA2
"""


class TestWifiParsing:
    def test_dedupes_keeps_strongest_and_in_use_first(self):
        nets = parse_nmcli_wifi(NMCLI_OUTPUT)
        ssids = [n.ssid for n in nets]
        assert ssids[0] == 'HomeNet'
        assert nets[0].in_use
        assert nets[0].signal == 87
        assert ssids.count('HomeNet') == 1

    def test_open_network_and_hidden_ssid(self):
        nets = parse_nmcli_wifi(NMCLI_OUTPUT)
        coffee = next(n for n in nets if n.ssid == 'CoffeeShop')
        assert coffee.security == 'open'
        assert all(n.ssid for n in nets)

    def test_escaped_colon_in_ssid(self):
        nets = parse_nmcli_wifi(NMCLI_OUTPUT)
        neighbor = next(n for n in nets if n.ssid == 'Neighbor:5G')
        assert neighbor.signal == 31
        assert neighbor.security == 'WPA3'

    def test_bad_signal_defaults_zero(self):
        nets = parse_nmcli_wifi(NMCLI_OUTPUT)
        weak = next(n for n in nets if n.ssid == 'Weak')
        assert weak.signal == 0

    def test_empty_output(self):
        assert parse_nmcli_wifi('') == []


PACTL_JSON = '''[
  {"name": "alsa_output.usb", "description": "USB Headset"},
  {"name": "alsa_output.internal", "description": "Speakers"},
  {"description": "nameless"}
]'''


class TestSinkParsing:
    def test_marks_default(self):
        sinks = parse_pactl_sinks(PACTL_JSON, 'alsa_output.internal')
        assert [s.description for s in sinks] == ['USB Headset', 'Speakers']
        assert [s.is_default for s in sinks] == [False, True]

    def test_invalid_json(self):
        assert parse_pactl_sinks('nope', 'x') == []

    def test_non_list_json(self):
        assert parse_pactl_sinks('{"a": 1}', 'x') == []


def _completed(cmd: list[str], code: int = 0,
               stderr: str = '') -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, code, stdout='', stderr=stderr)


def _passwd_paths(calls: list[tuple[list[str], int]]) -> list[str]:
    return [cmd[cmd.index('passwd-file') + 1]
            for cmd, _timeout in calls if 'passwd-file' in cmd]


class TestConnectWifiOpenNetwork:
    def test_open_network_single_call_no_secrets(self):
        calls: list[list[str]] = []

        def run(cmd, timeout=15):
            calls.append(list(cmd))
            return _completed(cmd)

        ok, msg = connect_wifi('CoffeeShop', run=run)
        assert ok
        assert msg == 'Connected to CoffeeShop'
        assert calls == [['nmcli', 'dev', 'wifi', 'connect', 'CoffeeShop']]


class TestConnectWifiPassword:
    """The PSK must travel via nmcli's passwd-file channel (nmcli(1):
    `connection up ... [passwd-file file]`), never on the command line."""

    @staticmethod
    def _recording_run(up_code: int = 0, up_stderr: str = ''):
        calls: list[tuple[list[str], int]] = []
        modes_at_up: dict[str, int] = {}
        contents_at_up: dict[str, str] = {}

        def run(cmd, timeout=15):
            calls.append((list(cmd), timeout))
            if 'passwd-file' in cmd:
                path = cmd[cmd.index('passwd-file') + 1]
                modes_at_up[path] = stat.S_IMODE(os.stat(path).st_mode)
                contents_at_up[path] = Path(path).read_text()
                return _completed(cmd, up_code, up_stderr)
            return _completed(cmd)

        return run, calls, modes_at_up, contents_at_up

    def test_password_never_in_argv_secret_via_passwd_file(self):
        run, calls, _modes, contents = self._recording_run()
        ok, msg = connect_wifi('HomeNet', 'hunter2', run=run)
        assert ok
        assert msg == 'Connected to HomeNet'
        argv_words = [word for cmd, _t in calls for word in cmd]
        assert 'hunter2' not in argv_words
        add_cmd = calls[0][0]
        assert add_cmd[:6] == ['nmcli', 'connection', 'add', 'type',
                               'wifi', 'con-name']
        assert add_cmd[add_cmd.index('ssid') + 1] == 'HomeNet'
        up_cmd = calls[1][0]
        assert up_cmd[:4] == ['nmcli', 'connection', 'up', 'HomeNet']
        assert up_cmd[4] == 'passwd-file'
        path = up_cmd[5]
        assert contents[path] == '802-11-wireless-security.psk:hunter2\n'
        assert not Path(path).exists()

    def test_temp_file_is_0600_while_nmcli_runs(self):
        run, _calls, modes, _contents = self._recording_run()
        connect_wifi('HomeNet', 'hunter2', run=run)
        assert list(modes.values()) == [0o600]

    def test_activation_gets_45s_timeout_add_keeps_default(self):
        run, calls, _modes, _contents = self._recording_run()
        connect_wifi('HomeNet', 'hunter2', run=run)
        assert calls[0][1] == 15
        assert calls[1][1] == 45

    def test_file_removed_when_activation_fails(self):
        run, calls, _modes, _contents = self._recording_run(
            up_code=4, up_stderr='Error: Connection activation failed')
        ok, msg = connect_wifi('HomeNet', 'hunter2', run=run)
        assert not ok
        assert msg == 'Error: Connection activation failed'
        paths = _passwd_paths(calls)
        assert len(paths) == 1
        assert not Path(paths[0]).exists()

    def test_file_removed_when_nmcli_missing(self):
        calls: list[list[str]] = []

        def run(cmd, timeout=15):
            calls.append(list(cmd))
            if 'passwd-file' in cmd:
                raise OSError('nmcli vanished')
            return _completed(cmd)

        ok, msg = connect_wifi('HomeNet', 'hunter2', run=run)
        assert not ok
        assert msg == 'NetworkManager unavailable'
        paths = _passwd_paths([(c, 0) for c in calls])
        assert len(paths) == 1
        assert not Path(paths[0]).exists()

    def test_existing_profile_add_failure_is_tolerated(self):
        calls: list[list[str]] = []

        def run(cmd, timeout=15):
            calls.append(list(cmd))
            if 'add' in cmd:
                return _completed(
                    cmd, 10,
                    'Error: Failed to add a new connection: profile exists')
            return _completed(cmd)

        ok, msg = connect_wifi('HomeNet', 'hunter2', run=run)
        assert ok
        assert msg == 'Connected to HomeNet'
        paths = _passwd_paths([(c, 0) for c in calls])
        assert len(paths) == 1
        assert not Path(paths[0]).exists()

    def test_open_network_unavailable_message(self):
        def run(cmd, timeout=15):
            raise OSError('no nmcli')

        ok, msg = connect_wifi('CoffeeShop', run=run)
        assert not ok
        assert msg == 'NetworkManager unavailable'
