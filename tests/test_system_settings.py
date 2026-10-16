"""Tests for system_settings.py parsers (pure logic, injected output)."""
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from system_settings import _key_mgmt_for, connect_wifi, parse_nmcli_wifi, parse_pactl_sinks

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


def _completed(cmd: list[str], code: int = 0, stderr: str = '',
               stdout: str = '') -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr=stderr)


def _passwd_paths(calls: list[tuple[list[str], int]]) -> list[str]:
    return [cmd[cmd.index('passwd-file') + 1]
            for cmd, _timeout in calls if 'passwd-file' in cmd]


class _FakeNm:
    """Fake nmcli with a profile store. `profiles` lists pre-existing
    connection names; the add path creates them, delete removes them."""

    def __init__(self, profiles=(), up_code: int = 0, up_stderr: str = '',
                 add_code: int = 0):
        self.calls: list[tuple[list[str], int]] = []
        self.profiles = set(profiles)
        self.up_code = up_code
        self.up_stderr = up_stderr
        self.add_code = add_code
        self.modes: dict[str, int] = {}
        self.contents: dict[str, str] = {}

    def run(self, cmd, timeout=15):
        cmd = list(cmd)
        self.calls.append((cmd, timeout))
        if cmd[:4] == ['nmcli', '-t', '-f', 'NAME']:
            stdout = ''.join(f'{name}\n' for name in sorted(self.profiles))
            return _completed(cmd, stdout=stdout)
        if 'delete' in cmd:
            self.profiles.discard(cmd[cmd.index('delete') + 1])
            return _completed(cmd)
        if 'add' in cmd:
            if self.add_code == 0:
                self.profiles.add(cmd[cmd.index('con-name') + 1])
            return _completed(cmd, self.add_code)
        if 'up' in cmd:
            path = cmd[cmd.index('passwd-file') + 1]
            self.modes[path] = stat.S_IMODE(os.stat(path).st_mode)
            self.contents[path] = Path(path).read_text()
            return _completed(cmd, self.up_code, self.up_stderr)
        return _completed(cmd)

    def verbs(self) -> list[str]:
        return [cmd[1] for cmd, _t in self.calls]


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

    def test_password_never_in_argv_secret_via_passwd_file(self):
        nm = _FakeNm()
        ok, msg = connect_wifi('HomeNet', 'hunter2', run=nm.run)
        assert ok
        assert msg == 'Connected to HomeNet'
        argv_words = [word for cmd, _t in nm.calls for word in cmd]
        assert 'hunter2' not in argv_words
        show_cmd, add_cmd, up_cmd = (cmd for cmd, _t in nm.calls)
        assert show_cmd[:4] == ['nmcli', '-t', '-f', 'NAME']
        assert add_cmd[:6] == ['nmcli', 'connection', 'add', 'type',
                               'wifi', 'con-name']
        assert add_cmd[add_cmd.index('ssid') + 1] == 'HomeNet'
        assert add_cmd[add_cmd.index('wifi-sec.key-mgmt') + 1] == 'wpa-psk'
        assert up_cmd[:4] == ['nmcli', 'connection', 'up', 'HomeNet']
        assert up_cmd[4] == 'passwd-file'
        path = up_cmd[5]
        assert nm.contents[path] == '802-11-wireless-security.psk:hunter2\n'
        assert not Path(path).exists()

    def test_temp_file_is_0600_while_nmcli_runs(self):
        nm = _FakeNm()
        connect_wifi('HomeNet', 'hunter2', run=nm.run)
        assert list(nm.modes.values()) == [0o600]

    def test_timeouts_show_add_up(self):
        nm = _FakeNm()
        connect_wifi('HomeNet', 'hunter2', run=nm.run)
        assert [timeout for _cmd, timeout in nm.calls] == [10, 15, 45]

    def test_existing_profile_brings_up_without_adding(self):
        nm = _FakeNm(profiles={'HomeNet'})
        ok, msg = connect_wifi('HomeNet', 'newpsk', run=nm.run)
        assert ok
        assert msg == 'Connected to HomeNet'
        assert nm.verbs() == ['-t', 'connection']
        up_cmd = nm.calls[1][0]
        assert up_cmd[:4] == ['nmcli', 'connection', 'up', 'HomeNet']
        assert up_cmd[4] == 'passwd-file'

    def test_race_on_add_is_tolerated(self):
        nm = _FakeNm(add_code=10)
        ok, msg = connect_wifi('HomeNet', 'hunter2', run=nm.run)
        assert ok
        assert msg == 'Connected to HomeNet'
        paths = _passwd_paths(nm.calls)
        assert len(paths) == 1
        assert not Path(paths[0]).exists()

    def test_wpa3_only_profile_uses_sae(self):
        nm = _FakeNm()
        connect_wifi('HomeNet', 'hunter2', run=nm.run, security='WPA3')
        add_cmd = nm.calls[1][0]
        assert add_cmd[add_cmd.index('wifi-sec.key-mgmt') + 1] == 'sae'

    def test_mixed_security_stays_wpa_psk(self):
        for security in (None, '', 'WPA2', 'WPA1 WPA2', 'WPA2 WPA3'):
            nm = _FakeNm()
            connect_wifi('HomeNet', 'hunter2', run=nm.run, security=security)
            add_cmd = nm.calls[1][0]
            assert add_cmd[add_cmd.index('wifi-sec.key-mgmt') + 1] == 'wpa-psk'

    def test_key_mgmt_helper(self):
        assert _key_mgmt_for('WPA3') == 'sae'
        assert _key_mgmt_for('wpa3') == 'sae'
        assert _key_mgmt_for('WPA2') == 'wpa-psk'
        assert _key_mgmt_for('WPA2 WPA3') == 'wpa-psk'
        assert _key_mgmt_for(None) == 'wpa-psk'
        assert _key_mgmt_for('open') == 'wpa-psk'

    def test_created_profile_deleted_on_bad_secrets(self):
        nm = _FakeNm(
            up_code=4,
            up_stderr='Error: Connection activation failed: '
                      'Secrets were required, but not provided.')
        ok, msg = connect_wifi('HomeNet', 'wrong', run=nm.run)
        assert not ok
        assert 'Secrets were required' in msg
        assert 'HomeNet' not in nm.profiles
        delete_cmd = nm.calls[-1][0]
        assert delete_cmd == ['nmcli', 'connection', 'delete', 'HomeNet']
        paths = _passwd_paths(nm.calls)
        assert all(not Path(path).exists() for path in paths)

    def test_preexisting_profile_kept_on_bad_secrets(self):
        nm = _FakeNm(
            profiles={'HomeNet'}, up_code=4,
            up_stderr='Error: Secrets were required, but not provided.')
        ok, _msg = connect_wifi('HomeNet', 'wrong', run=nm.run)
        assert not ok
        assert 'HomeNet' in nm.profiles
        assert nm.verbs() == ['-t', 'connection']

    def test_non_secrets_failure_keeps_created_profile(self):
        nm = _FakeNm(up_code=4, up_stderr='Error: Connection activation failed')
        ok, msg = connect_wifi('HomeNet', 'hunter2', run=nm.run)
        assert not ok
        assert msg == 'Error: Connection activation failed'
        assert 'HomeNet' in nm.profiles
        assert nm.verbs() == ['-t', 'connection', 'connection']

    def test_file_removed_when_nmcli_missing(self):
        def run(cmd, timeout=15):
            raise OSError('no nmcli')

        ok, msg = connect_wifi('HomeNet', 'hunter2', run=run)
        assert not ok
        assert msg == 'NetworkManager unavailable'

    def test_open_network_unavailable_message(self):
        def run(cmd, timeout=15):
            raise OSError('no nmcli')

        ok, msg = connect_wifi('CoffeeShop', run=run)
        assert not ok
        assert msg == 'NetworkManager unavailable'
