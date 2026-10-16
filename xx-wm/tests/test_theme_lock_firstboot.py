"""Theme sweep: lock screen + first-boot wizard sheets are pure functions of
their ThemePreset with no hardcoded legacy colors. Headless — only touches
the pure theme_css() builders, never GTK widgets."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import config

from _theme_contract import hardcoded_leftover

PRESETS = config.THEME_PRESETS

THEMEABLE_FIELDS = ('background', 'foreground', 'surface', 'surface_alt', 'muted')


def _surfaces():
    # Imported lazily: sibling test modules stub sys.modules['gi'] during
    # collection; conftest restores the real bindings before tests run.
    import first_boot
    import lock_screen
    return lock_screen, first_boot


def _sheets():
    lock_screen, first_boot = _surfaces()
    for key, preset in PRESETS.items():
        yield key, preset, lock_screen.theme_css(preset), first_boot.theme_css(preset)


def test_modules_import_headlessly():
    lock_screen, first_boot = _surfaces()
    assert callable(lock_screen.theme_css)
    assert callable(first_boot.theme_css)


def test_every_preset_field_appears_in_lock_sheet():
    for key, preset, css, _wizard in _sheets():
        for field in THEMEABLE_FIELDS:
            assert getattr(preset, field) in css, f'{key}: {field} missing from lock sheet'


def test_every_preset_field_appears_in_wizard_sheet():
    for key, preset, _lock, css in _sheets():
        for field in THEMEABLE_FIELDS:
            assert getattr(preset, field) in css, f'{key}: {field} missing from wizard sheet'


def test_paper_and_amoled_sheets_differ():
    paper = PRESETS['paper']
    amoled = PRESETS['amoled']
    lock_screen, first_boot = _surfaces()
    assert lock_screen.theme_css(paper) != lock_screen.theme_css(amoled)
    assert first_boot.theme_css(paper) != first_boot.theme_css(amoled)


def test_no_legacy_literals_outside_preset_fields_and_semantics():
    for key, preset, lock_sheet, wizard_sheet in _sheets():
        for label, sheet in (('lock', lock_sheet), ('wizard', wizard_sheet)):
            drifted = hardcoded_leftover(sheet, preset)
            assert not drifted, f'{key} ({label}): hardcoded legacy colors {sorted(drifted)}'


def test_lock_sheet_keeps_semantic_colors():
    lock_screen, _first_boot = _surfaces()
    css = lock_screen.theme_css(PRESETS['amoled'])
    assert config.DANGER_RED in css
    assert config.WARNING_ORANGE in css


def test_wizard_sheet_styles_pin_error_with_danger_red():
    _lock_screen, first_boot = _surfaces()
    css = first_boot.theme_css(PRESETS['amoled'])
    assert '.pin-dots.error' in css
    assert config.DANGER_RED in css


def test_themeable_colors_come_from_the_preset_not_constants():
    paper = PRESETS['paper']
    amoled = PRESETS['amoled']
    for module in _surfaces():
        for field in THEMEABLE_FIELDS:
            light_value = getattr(paper, field)
            dark_value = getattr(amoled, field)
            if light_value == dark_value:
                continue
            assert light_value in module.theme_css(paper)
            assert dark_value not in module.theme_css(paper)
