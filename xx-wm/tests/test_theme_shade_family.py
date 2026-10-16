"""Theme-sweep contract for the shade family surfaces.

notification_shade / quick_actions / power_menu must expose a pure
``theme_css(preset)`` that interpolates every themeable color from the
preset — no hardcoded grays left behind, for any shipped theme.
"""
import functools
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import DANGER_RED, THEME_PRESETS

from _theme_contract import assert_sheets_differ_by_preset, hardcoded_leftover

SHEETS = {
    'notification_shade': 'notification_shade',
    'quick_actions': 'quick_actions',
    'power_menu': 'power_menu',
}

FIELDS = ('background', 'foreground', 'surface', 'surface_alt', 'muted', 'accent')


@functools.lru_cache(maxsize=1)
def _sheets():
    # Run-time import: sibling tests stub sys.modules['gi'] during pytest
    # collection; conftest restores the real modules before tests run.
    import notification_shade
    import power_menu
    import quick_actions

    return {
        'notification_shade': notification_shade.theme_css,
        'quick_actions': quick_actions.theme_css,
        'power_menu': power_menu.theme_css,
    }


def test_every_preset_interpolates_all_fields():
    for key, preset in THEME_PRESETS.items():
        for name, theme_css in _sheets().items():
            css = theme_css(preset)
            for field in FIELDS:
                value = getattr(preset, field)
                assert value in css, f'{name}/{key}: missing {field}={value}'


def test_paper_differs_from_amoled():
    assert_sheets_differ_by_preset(
        _sheets(), THEME_PRESETS['paper'], THEME_PRESETS['amoled'],
    )


def test_no_banned_literals_outside_preset_values():
    for key, preset in THEME_PRESETS.items():
        for name, theme_css in _sheets().items():
            leftover = hardcoded_leftover(theme_css(preset), preset)
            assert not leftover, f'{name}/{key}: hardcoded {leftover}'


def test_hardcoded_leftover_catches_hardcoding():
    # Pins the scanner itself: a deliberately hardcoded legacy literal must
    # be reported, so the sweep cannot silently rot into a no-op.
    assert hardcoded_leftover('.x { color: #f4f4f4; }', THEME_PRESETS['paper']) == {'#f4f4f4'}


def test_danger_red_in_shade_sheet():
    for key in THEME_PRESETS:
        assert DANGER_RED in _sheets()['notification_shade'](THEME_PRESETS[key]), key


def test_theme_css_is_pure_and_total():
    first = {name: theme_css(THEME_PRESETS['ocean']) for name, theme_css in _sheets().items()}
    second = {name: theme_css(THEME_PRESETS['ocean']) for name, theme_css in _sheets().items()}
    assert first == second
