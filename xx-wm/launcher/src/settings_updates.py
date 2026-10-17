"""Software-update flow helpers: reboot notice, update log, dots row.

Pure file reads only (no subprocess, no D-Bus): the blocking package
commands live in system_settings (worker threads) and the privileged
apk work in the root helper. ``home``/``sysfs`` paths are injectable so
tests never touch the real machine.
"""
from __future__ import annotations

import os

LOG_TAIL_LINES = 15


def log_dir(home: str | None = None) -> str:
    """The shell's update-log directory (matches update-system.sh's
    ``$PX_STATE_DIR/logs`` layout for one shared history)."""
    base = home if home is not None else os.path.expanduser('~')
    return os.path.join(base, '.local', 'state', 'piercingxx', 'logs')


def latest_update_log(home: str | None = None,
                      directory: str | None = None,
                      tail_lines: int = LOG_TAIL_LINES,
                      ) -> tuple[str, str] | None:
    """(filename, tail) of the newest ``update-*.log``, or None when no
    log exists yet. Only the tail is read: logs can be megabytes."""
    folder = directory if directory is not None else log_dir(home)
    try:
        names = sorted(name for name in os.listdir(folder)
                       if name.startswith('update-') and name.endswith('.log'))
    except OSError:
        return None
    if not names:
        return None
    path = os.path.join(folder, names[-1])
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    tail = [line for line in lines if line.strip()][-tail_lines:]
    return names[-1], '\n'.join(tail) if tail else '(empty log)'


def new_log_path(home: str | None = None) -> str:
    """Fresh timestamped log path for an update run starting now; the
    parent directory is created (mode 0700) like every other XDG state
    dir the shell owns."""
    import time
    folder = log_dir(home)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime())
    return os.path.join(folder, 'update-%s.log' % stamp)


def append_log(path: str, text: str) -> None:
    """Append one update step's output to the run log; never raises."""
    try:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(text)
            if not text.endswith('\n'):
                handle.write('\n')
    except OSError:
        pass


def reboot_required(uname_release: str | None = None,
                    lib_modules: str = '/lib/modules',
                    usr_lib_modules: str = '/usr/lib/modules',
                    flag_file: str = '/var/run/reboot-required',
                    ) -> str | None:
    """Why a restart is needed to finish updates, or None.

    Mirrors update-system.sh: the running kernel was replaced (its
    module directory is gone) or a package left a reboot-required flag.
    """
    release = uname_release
    if release is None:
        release = os.uname().release
    gone = (not os.path.isdir(os.path.join(lib_modules, release))
            and not os.path.isdir(os.path.join(usr_lib_modules, release)))
    if gone:
        return 'Running kernel %s was replaced.' % (release,)
    if os.path.isfile(flag_file):
        return 'Installed packages requested a restart.'
    return None


def dots_bootstrap_available(home: str | None = None) -> bool:
    """True only when scripts/bootstrap-dots.sh can actually run: a
    piercing-dots clone whose install.sh is executable and takes a
    --profile flag (the phone profile). Otherwise the row stays hidden."""
    base = home if home is not None else os.path.expanduser('~')
    for dest in (os.path.join(base, 'piercing-dots'),
                 os.path.join(base, '.cache', 'piercing-dots')):
        installer = os.path.join(dest, 'install.sh')
        if not (os.path.isfile(installer) and os.access(installer, os.X_OK)):
            continue
        try:
            with open(installer, encoding='utf-8', errors='replace') as handle:
                head = handle.read(65536)
        except OSError:
            continue
        if '--profile' in head:
            return True
    return False


def bootstrap_script() -> str | None:
    """Filesystem path of scripts/bootstrap-dots.sh in a repo checkout,
    else None (installed trees do not ship it)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, '..', '..', 'scripts', 'bootstrap-dots.sh')
    candidate = os.path.normpath(candidate)
    if os.path.isfile(candidate):
        return candidate
    return None


def format_count(repo: int | None, flatpak: int | None) -> str:
    """One status line for a check result: unknown stays unknown, never
    a false 'up to date'."""
    if repo is None and flatpak is None:
        return 'Update status unknown — package index is stale.'
    total = (repo or 0) + (flatpak or 0)
    if total == 0:
        return 'System is up to date.'
    plural = 's' if total != 1 else ''
    bits = []
    if repo:
        bits.append('%d package%s' % (repo, 's' if repo != 1 else ''))
    if flatpak:
        bits.append('%d Flatpak%s' % (flatpak, 's' if flatpak != 1 else ''))
    return '%d update%s available (%s).' % (total, plural, ', '.join(bits))
