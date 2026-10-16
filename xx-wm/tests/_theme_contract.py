"""Shared theme-sweep contract vocabulary.

The theme-sweep test files assert that every pure ``theme_css(preset)``
sheet interpolates its ThemePreset palette instead of leaking legacy
hardcoded hexes. This module deduplicates that contract: the banned
literal set (union across all sweep files), the semantic constants, and
the scan helper implementing the corrected semantics -- strip preset
values FIRST, then search ``BANNED - SEMANTIC`` in the lowercased
remainder.
"""

BANNED = {
    '#f4f4f4', '#9a9a9a', '#111111', '#1a1a1a', '#1e1e1e', '#242424',
    '#282828', '#5a5a5a', '#c8c8c8', '#e0e0e0', '#000000', '#ffffff',
}

# Semantic colors keep their universal meanings in every theme: banned
# literals that are deliberately NOT theme-tracked.
SEMANTIC = {'#ff6b6b', '#ff9a3c', '#2a1010'}

PALETTE_FIELDS = (
    'background', 'surface', 'surface_alt', 'border',
    'foreground', 'muted', 'accent',
)


def strip_palette(css: str, preset) -> str:
    """Remove every ThemePreset palette value from css.

    A banned literal that IS the preset's own palette value arrives via
    interpolation (amoled background is literally #000000), so interpolated
    values must be stripped before scanning for hardcoding.
    """
    for field in PALETTE_FIELDS:
        css = css.replace(getattr(preset, field), '')
    return css


def hardcoded_leftover(css: str, preset) -> set[str]:
    """Return banned literals still hardcoded in css after palette stripping.

    SEMANTIC is subtracted from BANNED *before* scanning: the semantic
    constants never occur as preset fields, so subtracting them from the
    result instead would be dead code.
    """
    lowered = strip_palette(css, preset).lower()
    return {h for h in BANNED - SEMANTIC if h in lowered}


def assert_sheets_differ_by_preset(sheets, preset_a, preset_b) -> None:
    """Every sheet must render differently under two different presets."""
    for name, theme_css in sheets.items():
        assert theme_css(preset_a) != theme_css(preset_b), name
