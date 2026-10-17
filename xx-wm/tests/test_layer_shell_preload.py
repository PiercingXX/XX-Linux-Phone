"""The gtk4-layer-shell LD_PRELOAD reaches the shell only, never the apps it
launches: Firefox and GTK3 apps abort when they inherit a GTK4 library
(found when the Tailscale tile's sign-in link crashed Firefox)."""
import ast
import os
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parent.parent / 'launcher'


def _drop():
    """drop_layer_shell_preload from main.py, without importing GTK."""
    tree = ast.parse((LAUNCHER / 'src' / 'main.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == 'drop_layer_shell_preload')
    ns = {'os': os}
    exec(compile(ast.Module([fn], []), 'main.py', 'exec'), ns)
    return ns['drop_layer_shell_preload']


def test_only_layer_shell_is_removed():
    env = {'LD_PRELOAD': '/usr/lib/libfoo.so:/usr/lib/libgtk4-layer-shell.so.0'}
    _drop()(env)
    assert env == {'LD_PRELOAD': '/usr/lib/libfoo.so'}


def test_variable_dropped_when_nothing_else_preloaded():
    env = {'LD_PRELOAD': '/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so.0'}
    _drop()(env)
    assert 'LD_PRELOAD' not in env


def test_main_runs_it_at_import():
    src = (LAUNCHER / 'src' / 'main.py').read_text()
    assert '\ndrop_layer_shell_preload()\n' in src


def test_wrapper_preloads_the_shell_command_only():
    src = (LAUNCHER / 'data' / 'xx-wm.in').read_text()
    assert 'export LD_PRELOAD' not in src
    assert 'LD_PRELOAD="$_xx_preload" @python@ @shell_dir@/main.py' in src
