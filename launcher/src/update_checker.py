from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from shell_log import get_logger

_log = get_logger('update')

CHECK_INTERVAL = 24 * 3600
SNOOZE_INTERVAL = 7 * 24 * 3600

# First match wins; each entry maps a terminal binary to the argv that runs a
# shell command string inside it.
_TERMINALS: list[tuple[str, Callable[[str, str], list[str]]]] = [
    ('kitty', lambda sh, cmd: ['kitty', sh, '-c', cmd]),
    ('foot', lambda sh, cmd: ['foot', sh, '-c', cmd]),
    ('alacritty', lambda sh, cmd: ['alacritty', '-e', sh, '-c', cmd]),
    ('gnome-terminal', lambda sh, cmd: ['gnome-terminal', '--', sh, '-c', cmd]),
    ('xterm', lambda sh, cmd: ['xterm', '-e', sh, '-c', cmd]),
]


def run_update_script(script_path: str) -> tuple[bool, str]:
    """Open the update script in the first available terminal emulator."""
    path = Path(script_path).expanduser()
    if not path.exists():
        return False, f'Update script not found: {path}'

    quoted = shlex.quote(str(path))
    if shutil.which('bash'):
        # read -rsn1 exits on any single key — including the Escape the shell's
        # back gesture synthesizes — so an edge swipe closes the terminal.
        shell = 'bash'
        cmd = f'{quoted}; echo; echo "Done — swipe from the screen edge to close."; read -rsn1 _'
    else:
        shell = 'sh'
        cmd = f'{quoted}; echo; echo "Done — press Enter to close."; read _'
    for binary, argv in _TERMINALS:
        if shutil.which(binary):
            try:
                subprocess.Popen(argv(shell, cmd), close_fds=True)
                return True, binary
            except OSError as e:
                _log.warning('failed to launch %s: %s', binary, e)
    return False, 'No terminal emulator found (kitty, foot, alacritty, ...).'


def count_updates() -> int:
    """Best-effort count of upgradable packages across the distros we target."""
    checks: list[tuple[str, list[str], Callable[[str], int]]] = [
        ('apk', ['apk', 'list', '--upgradable'],
         lambda out: sum(1 for line in out.splitlines() if line.strip())),
        ('checkupdates', ['checkupdates'],
         lambda out: sum(1 for line in out.splitlines() if line.strip())),
        ('apt', ['apt', 'list', '--upgradable'],
         lambda out: sum(1 for line in out.splitlines()[1:] if line.strip())),
        ('pacman', ['pacman', '-Qu'],
         lambda out: sum(1 for line in out.splitlines() if line.strip())),
    ]
    for binary, argv, parse in checks:
        if not shutil.which(binary):
            continue
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=120, check=False,
            )
            return parse(result.stdout)
        except (OSError, subprocess.TimeoutExpired) as e:
            _log.warning('update count via %s failed: %s', binary, e)
            return 0
    return 0


class UpdateChecker:
    """Once-a-day update availability check with a one-week snooze.

    on_updates_available(count) fires on the main loop when a scheduled check
    finds updates. check_now() bypasses the daily/snooze gates and always
    reports through on_result(count).
    """

    def __init__(
        self,
        config: object,
        on_updates_available: Callable[[int], None],
    ) -> None:
        self._config = config
        self._on_updates_available = on_updates_available
        self._checking = False
        GLib.timeout_add_seconds(90, self._initial_check)
        GLib.timeout_add_seconds(3600, self._tick)

    def _initial_check(self) -> bool:
        self.maybe_check()
        return False

    def _tick(self) -> bool:
        self.maybe_check()
        return True

    def maybe_check(self) -> None:
        now = time.time()
        if now < self._config.update_snooze_until:
            return
        if now - self._config.update_last_check < CHECK_INTERVAL:
            return
        self._start_check(self._report_scheduled)

    def check_now(self, on_result: Callable[[int], None]) -> None:
        self._start_check(on_result)

    def _start_check(self, report: Callable[[int], None]) -> None:
        if self._checking:
            return
        self._checking = True

        def worker() -> None:
            count = count_updates()
            GLib.idle_add(self._finish_check, count, report)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_check(self, count: int, report: Callable[[int], None]) -> bool:
        self._checking = False
        self._config.set_update_last_check(time.time())
        _log.info('update check: %d packages upgradable', count)
        report(count)
        return False

    def _report_scheduled(self, count: int) -> None:
        if count > 0:
            self._on_updates_available(count)

    def snooze(self) -> None:
        self._config.set_update_snooze_until(time.time() + SNOOZE_INTERVAL)
