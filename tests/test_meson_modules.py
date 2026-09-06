"""Meson install_data must ship every runtime Python module.

A meson install of main used to omit hud.py, lock_lines.py, and
toplevel_manager.py, so the first login crashed in _show_shell.
"""
from pathlib import Path
import re

LAUNCHER = Path(__file__).parent.parent / 'launcher'
MESON = LAUNCHER / 'meson.build'
SRC = LAUNCHER / 'src'


def _meson_top_level_py() -> set[str]:
    """src/foo.py entries only — not src/wayland_proto/foo.py."""
    text = MESON.read_text(encoding='utf-8')
    return {name for name in re.findall(r"'src/([^/']+\.py)'", text)}


def test_install_data_covers_every_runtime_module():
    shipped = _meson_top_level_py()
    on_disk = {p.name for p in SRC.glob('*.py')}
    missing = on_disk - shipped
    extra = shipped - on_disk
    assert not missing, f'meson.build omits runtime modules: {sorted(missing)}'
    assert not extra, f'meson.build lists missing files: {sorted(extra)}'


def test_wayland_proto_package_is_installed():
    text = MESON.read_text(encoding='utf-8')
    proto = {p.name for p in (SRC / 'wayland_proto').glob('*.py')}
    assert proto, 'wayland_proto package is empty'
    for name in proto:
        assert f'src/wayland_proto/{name}' in text, (
            f'meson.build must install wayland_proto/{name}'
        )


def test_critical_shell_modules_are_listed():
    shipped = _meson_top_level_py()
    for name in ('hud.py', 'lock_lines.py', 'toplevel_manager.py', 'main.py'):
        assert name in shipped, f'{name} must be in meson install_data'
