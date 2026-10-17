"""Lane bug_shell home/shell findings: 5, 11, 14.

5: startup must not permanently delete home slots when the app index is
   empty (transient environment), only compact against a real index.
11: shade Settings deep links must use the public open_settings (section
    validation + deferred scroll + section refresh), not _open_settings.
14: thumbnail capture must stand down while blanked or on a live call.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

SRC = Path(__file__).parent.parent / 'launcher' / 'src'


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Leave no imported shell module behind: sibling files import window
    and friends against their own gi seams (test_folders idiom)."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


class FakeConfig:
    def __init__(self, slots):
        self.home_slots = list(slots)
        self.writes: list = []

    def set_home_slots(self, slots):
        self.home_slots = list(slots)
        self.writes.append(list(slots))


def _slots():
    return [
        {'type': 'app', 'label': 'Notes', 'app_id': 'org.gnome.Notes'},
        {'type': 'app', 'label': 'Calc',
         'app_id': 'org.gnome.Calculator.desktop'},
    ]


class TestCompactEmptyIndex:
    def test_empty_index_keeps_slots(self) -> None:
        from default_layout import compact_unresolved_home
        config = FakeConfig(_slots())
        index = SimpleNamespace(entries=[])
        assert compact_unresolved_home(config, index) is False
        assert config.home_slots == _slots()
        assert config.writes == []

    def test_populated_index_still_compacts(self) -> None:
        from default_layout import compact_unresolved_home
        config = FakeConfig(_slots())
        index = SimpleNamespace(entries=[
            SimpleNamespace(app_id='org.gnome.Notes'),
        ])
        assert compact_unresolved_home(config, index) is True
        assert config.home_slots == [
            {'type': 'app', 'label': 'Notes', 'app_id': 'org.gnome.Notes'},
        ]


class TestShadeSettingsWiring:
    def test_shade_wired_to_public_open_settings(self) -> None:
        body = (SRC / 'window.py').read_text()
        start = body.find('self._shade = NotificationShade(')
        assert start != -1
        block = body[start:start + 800]
        assert 'on_open_settings=self.open_settings' in block


class TestCaptureStandsDown:
    def _shell(self, **overrides):
        import window
        shell = SimpleNamespace(
            _layer_is_top=False,
            _switcher=None,
            _shade=None,
            _lock_screen=None,
            _power_menu=None,
            _blanked=False,
            _modem_monitor=SimpleNamespace(calls=lambda: []),
        )
        for key, value in overrides.items():
            setattr(shell, key, value)
        shell._call_live = (
            lambda: window.ShellWindow._call_live(shell))
        return shell

    def test_blanked_blocks_capture(self) -> None:
        import window
        shell = self._shell(_blanked=True)
        assert window.ShellWindow._overlays_block_capture(shell) is True

    def test_live_call_blocks_capture(self) -> None:
        import window
        import modem_monitor
        live = SimpleNamespace(state=modem_monitor._STATE_ACTIVE)
        shell = self._shell(
            _modem_monitor=SimpleNamespace(calls=lambda: [live]))
        assert window.ShellWindow._overlays_block_capture(shell) is True

    def test_idle_home_allows_capture(self) -> None:
        import window
        shell = self._shell()
        assert window.ShellWindow._overlays_block_capture(shell) is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
