"""Tests for custom font install (config.install_custom_font)."""
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig, install_custom_font


class _FakeResult:
    def __init__(self, stdout: str = '') -> None:
        self.stdout = stdout


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    return ShellConfig()


def _fake_runner(family: str):
    calls = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        if cmd[0] == 'fc-scan':
            return _FakeResult(family)
        return _FakeResult()

    run.calls = calls
    return run


class TestInstallCustomFont:
    def test_installs_and_switches_font(self, config, tmp_path):
        src = tmp_path / 'MyFont.ttf'
        src.write_bytes(b'\x00\x01fake')
        ok, family = install_custom_font(config, str(src), run=_fake_runner('My Font'))
        assert ok
        assert family == 'My Font'
        assert config.data['font'] == 'custom'
        assert config.font_family == 'My Font'
        assert (Path.home() / '.local' / 'share' / 'fonts' / 'MyFont.ttf').exists()

    def test_family_falls_back_to_stem(self, config, tmp_path):
        src = tmp_path / 'Stemmy.otf'
        src.write_bytes(b'\x00')

        def broken_runner(cmd, **_kwargs):
            raise OSError('no fontconfig')

        ok, family = install_custom_font(config, str(src), run=broken_runner)
        assert ok
        assert family == 'Stemmy'

    def test_rejects_wrong_extension(self, config, tmp_path):
        src = tmp_path / 'notes.txt'
        src.write_text('hi')
        ok, msg = install_custom_font(config, str(src), run=_fake_runner(''))
        assert not ok
        assert config.data['font'] != 'custom'

    def test_rejects_missing_file(self, config, tmp_path):
        ok, _msg = install_custom_font(config, str(tmp_path / 'nope.ttf'),
                                       run=_fake_runner(''))
        assert not ok

    def test_set_font_custom_requires_family(self, config):
        config.set_font('custom')
        assert config.data['font'] != 'custom'
        config.data['custom_font_family'] = 'Foo'
        config.set_font('custom')
        assert config.data['font'] == 'custom'
