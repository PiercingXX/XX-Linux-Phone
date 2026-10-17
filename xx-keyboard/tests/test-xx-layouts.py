#!/usr/bin/python3
"""xx-keyboard layout checks: valid JSON plus the Piercing letter contract.

Run from meson: test-xx-layouts.py --layouts <src/layouts dir>.
Every layout must parse and carry the Stevia shape (name/locale/levels).
piercing-num.json must hold the owner's touch letters exactly, with a
number/symbol layer like the Android keyboard's; us+colemak.json must
still ship (Stevia covers Colemak through its xkb layouts).
"""

import argparse
import json
import sys
from pathlib import Path

# Touch letters from the owner's layout README (identical in both variants).
PIERCING_ROWS = [
    list('qrfwg;lpyz'),
    list('asetdhnuio'),
    [',', 'c', 'x', 'v', '/', 'k', 'm', 'b', 'j', '.'],
]

PIERCING_SHIFT_ROWS = [
    list('QRFWG:LPYZ'),
    list('ASETDHNUIO'),
    ['<', 'C', 'X', 'V', '?', 'K', 'M', 'B', 'J', '>'],
]


def fail(message: str) -> int:
    print(f'FAIL: {message}')
    return 1


def first_symbols(layout: dict, level: str):
    for entry in layout['levels']:
        if entry['level'] == level:
            return [[key[0] for key in row] for row in entry['rows']]
    raise AssertionError(f'level {level!r} missing')


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--layouts', required=True)
    args = parser.parse_args(argv[1:])

    layouts_dir = Path(args.layouts)
    files = sorted(layouts_dir.glob('*.json'))
    if not files:
        return fail(f'no layouts in {layouts_dir}')
    names = [path.name for path in files]

    if 'us+colemak.json' not in names:
        return fail('us+colemak.json missing (Colemak must keep shipping)')
    if 'piercing-num.json' not in names:
        return fail('piercing-num.json missing')

    parsed = {}
    for path in files:
        try:
            layout = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            return fail(f'{path.name}: invalid JSON ({error})')
        if not isinstance(layout, dict):
            return fail(f'{path.name}: root is not an object')
        for key in ('name', 'locale', 'levels'):
            if key not in layout:
                return fail(f'{path.name}: missing {key!r}')
        if not layout['levels']:
            return fail(f'{path.name}: empty levels')
        parsed[path.name] = layout

    piercing = parsed['piercing-num.json']
    if piercing['locale'] != 'en':
        return fail('piercing-num.json: locale must be "en"')

    try:
        default = first_symbols(piercing, '')
        shift = first_symbols(piercing, 'shift')
        opt = first_symbols(piercing, 'opt')
    except AssertionError as error:
        return fail(f'piercing-num.json: {error}')

    for got, want in zip(default[:3], PIERCING_ROWS):
        if got != want:
            return fail(f'piercing-num.json default row {got} != {want}')
    for got, want in zip(shift[:3], PIERCING_SHIFT_ROWS):
        if got != want:
            return fail(f'piercing-num.json shift row {got} != {want}')

    digits = [key for row in opt for key in row]
    for digit in '1234567890':
        if digit not in digits:
            return fail(f'piercing-num.json opt layer lacks {digit}')

    print(f'OK: {len(files)} layouts, Piercing letters exact')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
