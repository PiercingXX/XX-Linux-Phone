"""Theme behavior through the shared design system.

TxxT owns no theme reader or CSS of its own: the window installs
``xx_ui`` once at startup (config-driven, live-reloading) and
re-installs only for the in-app manual pick. These tests pin the
manual-pick mapping and the no-white guarantee over every preset.
"""

import re

import xx_ui
from txxt.main import MANUAL_FONTS, manual_theme
from txxt.settings import Settings


def test_all_eight_shell_presets_known():
    assert set(xx_ui.theme.preset_keys()) == {
        'amoled', 'graphite', 'forest', 'ocean',
        'paper', 'mist', 'aura', 'burgundy',
    }


def test_unknown_preset_falls_back_to_amoled():
    assert manual_theme(Settings(theme_preset='nope')).key == 'amoled'


def test_manual_preset_maps_to_shared_preset():
    active = manual_theme(Settings(theme_preset='forest'))
    assert active.key == 'forest'
    assert active.background == xx_ui.theme.PRESETS['forest'].background


def test_manual_font_mapping():
    assert manual_theme(Settings(font_mode='space-mono')).font_family.startswith('Space Mono')
    assert manual_theme(Settings(font_mode='jetbrains-mono')).font_family.startswith('JetBrains Mono')
    assert manual_theme(Settings(font_mode='system')).font_family == 'Sans Light'
    assert manual_theme(Settings(font_mode='bogus')).font_family.startswith('Space Mono')


def test_manual_font_keys_cover_settings_modes():
    assert set(MANUAL_FONTS) == {'space-mono', 'jetbrains-mono', 'system'}


def test_every_preset_renders_without_white():
    for key in xx_ui.theme.preset_keys():
        active = manual_theme(Settings(theme_preset=key))
        css = xx_ui.css.render(active).lower()
        assert 'listview' in css, key
        assert 'listview row' in css, key
        assert 'row:selected' in css, key
        assert 'entry' in css, key
        assert 'textview' in css, key
        # 3-digit white is never a surface; #ffffff survives only as the
        # destructive-confirm text token the design names explicitly.
        assert re.search(r'#fff(?![0-9a-f])', css) is None, key
        assert 'white' not in css, key


def test_every_preset_renders_without_placeholders():
    for key in xx_ui.theme.preset_keys():
        active = manual_theme(Settings(theme_preset=key))
        assert '${' not in xx_ui.css.render(active), key
