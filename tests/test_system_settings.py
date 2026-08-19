"""Tests for system_settings.py parsers (pure logic, injected output)."""
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from system_settings import parse_nmcli_wifi, parse_pactl_sinks

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
