"""Tests for gesture_bindings.py — generated lisgd system-level bindings.

The default-generated invocation is pinned byte-for-byte against the
bindings launcher/data/xx-wm.in hardcoded before they became generated:
same commands, same order.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Add launcher/src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import gesture_config
from gesture_bindings import DEFAULT_VERBS, generate_bindings
from gesture_config import GestureConfig

# Literal bindings formerly hardcoded in data/xx-wm.in, with @bindir@
# resolved to /usr/bin. Order matters: it is lisgd's argument order.
LEGACY_BINDINGS = [
    '1,DU,B,S,R,/usr/bin/xx-wm-ipc gesture.keyboard',
    '1,DU,B,L,R,/usr/bin/xx-wm-ipc gesture.home',
    '1,UD,T,*,R,/usr/bin/xx-wm-ipc gesture.shade',
    '1,LR,L,*,R,/usr/bin/xx-wm-ipc gesture.back',
    '1,RL,R,*,R,/usr/bin/xx-wm-ipc gesture.back',
]


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    monkeypatch.setattr(gesture_config, '_config_path',
                        lambda: tmp_path / 'gestures.json')


class TestDefaultByteEquivalence:
    def test_defaults_match_former_hardcoded_bindings(self):
        assert generate_bindings('/usr/bin') == LEGACY_BINDINGS

    def test_bindir_is_substituted(self):
        out = generate_bindings('/usr/local/bin')
        assert out[0] == '1,DU,B,S,R,/usr/local/bin/xx-wm-ipc gesture.keyboard'
        assert len(out) == len(LEGACY_BINDINGS)

    def test_default_verbs_cover_every_slot(self):
        from gesture_bindings import _LISGD_GEOMETRY
        assert set(DEFAULT_VERBS) == set(_LISGD_GEOMETRY)


class TestRebinding:
    def test_verb_override_rebinds_slot_in_place(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'gesture.switcher',
            'swipe_left_edge': 'gesture.home',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin') == [
            '1,DU,B,S,R,/usr/bin/xx-wm-ipc gesture.switcher',
            '1,DU,B,L,R,/usr/bin/xx-wm-ipc gesture.home',
            '1,UD,T,*,R,/usr/bin/xx-wm-ipc gesture.shade',
            '1,LR,L,*,R,/usr/bin/xx-wm-ipc gesture.home',
            '1,RL,R,*,R,/usr/bin/xx-wm-ipc gesture.home',
        ]

    def test_invalid_values_fall_back_silently(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'explode',
            'swipe_up_long': 'launch:org.some.App.desktop',
            'swipe_down_top': 'camera',
            'swipe_left_edge': 'none',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin') == LEGACY_BINDINGS

    def test_non_system_slots_are_ignored(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(
            '{"squeeze": "gesture.home", "double_tap_home": "gesture.shade"}',
            encoding='utf-8',
        )
        assert generate_bindings('/usr/bin') == LEGACY_BINDINGS

    def test_corrupt_config_file_falls_back(self, tmp_path):
        (tmp_path / 'gestures.json').write_text('{not json', encoding='utf-8')
        assert generate_bindings('/usr/bin') == LEGACY_BINDINGS


class TestGestureConfigVerbValues:
    def test_verb_accepted_for_lisgd_slot_and_persisted(self, tmp_path):
        gc = GestureConfig()
        gc.set('swipe_up_short', 'gesture.switcher')
        assert GestureConfig().get('swipe_up_short') == 'gesture.switcher'

    def test_verb_rejected_for_non_lisgd_slot(self):
        with pytest.raises(ValueError):
            GestureConfig().set('squeeze', 'gesture.home')

    def test_unknown_verb_rejected(self):
        with pytest.raises(ValueError):
            GestureConfig().set('swipe_up_short', 'gesture.explode')

    def test_load_ignores_verb_for_non_lisgd_slot(self, tmp_path):
        (tmp_path / 'gestures.json').write_text('{"squeeze": "gesture.home"}',
                                                encoding='utf-8')
        assert GestureConfig().get('squeeze') == 'assistant'


class TestCli:
    def test_stdout_matches_defaults_with_empty_home(self, tmp_path):
        env = dict(os.environ, HOME=str(tmp_path))
        script = Path(__file__).parent.parent / 'launcher' / 'src' / 'gesture_bindings.py'
        proc = subprocess.run(
            [sys.executable, str(script), '--bindir', '/usr/bin'],
            capture_output=True, text=True, env=env, timeout=30, check=True)
        assert proc.stdout.splitlines() == LEGACY_BINDINGS


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
