#!/usr/bin/python3
"""Generate xx-theme-presets.h from common/xx_ui/theme.py.

xx-keyboard is C/GTK3 but the phone's theme lives in Python
(common/xx_ui/theme.py, itself a copy of xx-wm's THEME_PRESETS). This
script runs at build time so the C preset table can never drift from
the shell theme. It prefers an explicit --theme-py path (the monorepo
checkout) and falls back to the installed xx_ui package.
"""

import argparse
import importlib.util
import sys
from pathlib import Path


def _exec_at(path: Path):
    spec = importlib.util.spec_from_file_location('xx_ui_theme', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_theme_module(explicit: str | None):
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f'error: theme.py not found at {path}')
        return _exec_at(path)
    try:
        from xx_ui import theme as module  # type: ignore[import]
        return module
    except ImportError:
        pass
    guess = Path(__file__).resolve().parent.parent.parent / 'common' / 'xx_ui' / 'theme.py'
    if guess.is_file():
        return _exec_at(guess)
    raise SystemExit(
        'error: cannot find theme.py (pass --theme-py or install xx_ui)')


def c_str(value: str) -> str:
    return '"%s"' % value.replace('\\', '\\\\').replace('"', '\\"')


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--theme-py', default=None,
                        help='path to common/xx_ui/theme.py')
    parser.add_argument('--out', required=True, help='output header path')
    args = parser.parse_args(argv[1:])

    theme = load_theme_module(args.theme_py)
    presets = theme.THEME_PRESETS
    fonts = theme.FONT_FAMILIES

    lines = [
        '/* Generated from common/xx_ui/theme.py by tools/gen-xx-theme.py.',
        ' * Do not edit: colours follow the xx-wm theme presets. */',
        '#pragma once',
        '',
        'typedef struct {',
        '  const char *key;',
        '  const char *name;',
        '  const char *background;',
        '  const char *surface;',
        '  const char *surface_alt;',
        '  const char *border;',
        '  const char *foreground;',
        '  const char *muted;',
        '  const char *accent;',
        '} XxThemePreset;',
        '',
        'static const XxThemePreset xx_theme_presets[] = {',
    ]
    for key in presets:
        preset = presets[key]
        fields = [preset.key, preset.name, preset.background, preset.surface,
                  preset.surface_alt, preset.border, preset.foreground,
                  preset.muted, preset.accent]
        lines.append('  { %s },' % ', '.join(c_str(field) for field in fields))
    lines += [
        '};',
        '',
        'enum { XX_THEME_N_PRESETS = %d };' % len(presets),
        '',
        'static const char xx_theme_default_preset[] = %s;' % c_str(theme.DEFAULT_PRESET),
        '',
        'typedef struct {',
        '  const char *key;',
        '  const char *family;',
        '} XxThemeFont;',
        '',
        'static const XxThemeFont xx_theme_fonts[] = {',
    ]
    for key in fonts:
        lines.append('  { %s, %s },' % (c_str(key), c_str(fonts[key])))
    lines += [
        '};',
        '',
        'enum { XX_THEME_N_FONTS = %d };' % len(fonts),
        '',
        'static const char xx_theme_default_font_key[] = %s;' % c_str(theme.DEFAULT_FONT),
        '',
    ]
    Path(args.out).write_text('\n'.join(lines), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
