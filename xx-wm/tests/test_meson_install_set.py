"""Meson ships every top-level launcher/src/*.py module.

A meson install once omitted hud.py, lock_lines.py, and
toplevel_manager.py, crashing the installed shell on start. This pins the
meson install_data Python set against the tree so it cannot regress.

Subpackages (e.g. launcher/src/wayland_proto/) are excluded from the
comparison: they install via their own meson stanza and are owned by a
concurrent runtime-bindings task. Leave those meson lines untouched.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
MESON_BUILD = REPO / 'launcher' / 'meson.build'
SRC_DIR = REPO / 'launcher' / 'src'


def _meson_top_level_py() -> set[str]:
    text = MESON_BUILD.read_text(encoding='utf-8')
    return set(re.findall(r"'(src/[^'/]+\.py)'", text))


def _tree_top_level_py() -> set[str]:
    return {f'src/{p.name}' for p in SRC_DIR.glob('*.py') if p.is_file()}


def test_meson_set_equals_tree_top_level():
    meson_set = _meson_top_level_py()
    tree_set = _tree_top_level_py()
    missing = sorted(tree_set - meson_set)
    extra = sorted(meson_set - tree_set)
    assert meson_set == tree_set, (
        f'missing from meson: {missing}; extra in meson: {extra}'
    )


def test_crash_modules_all_listed():
    meson_set = _meson_top_level_py()
    assert 'src/hud.py' in meson_set
    assert 'src/lock_lines.py' in meson_set
    assert 'src/toplevel_manager.py' in meson_set


def test_subpackages_excluded_from_comparison():
    meson_set = _meson_top_level_py()
    assert not any(p.startswith('src/wayland_proto/') for p in meson_set)
    assert all(p.count('/') == 1 for p in meson_set)


@pytest.mark.skipif(
    not (shutil.which('meson') and shutil.which('ninja')),
    reason='needs meson and ninja',
)
def test_destdir_install_contains_crash_modules(tmp_path):
    build_dir = tmp_path / 'build'
    destdir = tmp_path / 'destdir'
    subprocess.run(
        ['meson', 'setup', str(build_dir), str(REPO / 'launcher'), '--prefix=/usr'],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ['meson', 'install', '-C', str(build_dir)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, 'DESTDIR': str(destdir)},
    )
    for name in ('hud.py', 'lock_lines.py', 'toplevel_manager.py'):
        assert (destdir / 'usr' / 'share' / 'xx-wm' / name).is_file()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
