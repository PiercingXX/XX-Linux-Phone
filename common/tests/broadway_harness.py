"""Spawn a private gtk4-broadwayd and run a driver script against it.

Shared by the Broadway tests; skips cleanly when the daemon binary is
missing or no display is free.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

COMMON = str(Path(__file__).parent.parent)


def socket_path(display: str) -> str:
    runtime = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    return os.path.join(runtime, f'broadway{int(display[1:]) + 1}.socket')


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _display_live(display: str) -> bool:
    path = socket_path(display)
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(path)
    except OSError:
        return False
    return True


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / 'home'
    config = home / '.config' / 'xx-wm' / 'config.json'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'theme': 'amoled'}), encoding='utf-8')
    return home


def run_driver(name: str, home: Path, timeout: int = 60):
    """Run tests/<name> under a private broadway display."""
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('gtk4-broadwayd is missing')
    for number in range(53, 70):
        display = f':{number}'
        if _display_live(display):
            continue
        try:
            os.unlink(socket_path(display))
        except OSError:
            pass
        proc = subprocess.Popen(
            ['gtk4-broadwayd', '--port', str(_free_tcp_port()), display],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(200):
                if os.path.exists(socket_path(display)):
                    break
                time.sleep(0.05)
            else:
                continue
            env = dict(os.environ)
            env['GDK_BACKEND'] = 'broadway'
            env['BROADWAY_DISPLAY'] = display
            env['GSK_RENDERER'] = 'cairo'
            env['HOME'] = str(home)
            env['XDG_CONFIG_HOME'] = str(home / '.config')
            env['PYTHONPATH'] = COMMON + os.pathsep + \
                env.get('PYTHONPATH', '')
            done = subprocess.run(
                [sys.executable, str(Path(__file__).parent / name)],
                env=env, capture_output=True, text=True, timeout=timeout,
            )
            return done
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    pytest.skip('no free broadway display')
