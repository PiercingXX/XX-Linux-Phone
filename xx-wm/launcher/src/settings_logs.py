"""Extended log viewer backends (lane sm_device, ref log-viewer.sh).

The About → Logs page shows the shell-log tail plus journal, kernel and
system-log tabs. journalctl/dmesg run unprivileged on worker threads;
files under /var/log go through the helper's fixed-allowlist ReadLog,
and dmesg falls back to it when the kernel restricts unprivileged
reads (dmesg_restrict=1). Output is capped and PINs/PSKs are scrubbed
before anything reaches the UI.
"""
from __future__ import annotations

import re
import subprocess

MAX_LOG_BYTES = 128 * 1024
MAX_LOG_LINES = 1000

TABS = (
    ('shell', 'Shell'),
    ('errors-boot', 'Errors'),
    ('errors-prev', 'Last boot'),
    ('user', 'User'),
    ('kernel', 'Kernel'),
    ('system', 'System'),
)

_SECRET_RE = re.compile(
    r'(?i)\b(psk|password|passwd|pin)\s*[:=]\s*\S+')


def _run(argv: list[str], timeout: int = 30):
    """Sole subprocess chokepoint; callers run on worker threads only."""
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, check=False)


def journal_argv(tab: str) -> list[str] | None:
    """journalctl argv for a tab, None when the tab is not a journal."""
    if tab == 'errors-boot':
        return ['journalctl', '-b', '-p', 'err..alert', '-n', '500',
                '--no-pager']
    if tab == 'errors-prev':
        return ['journalctl', '-b', '-1', '-p', 'err..alert', '-n', '500',
                '--no-pager']
    if tab == 'user':
        return ['journalctl', '--user', '-b', '-n', '500', '--no-pager']
    return None


def cap_text(text: str, max_bytes: int = MAX_LOG_BYTES,
             max_lines: int = MAX_LOG_LINES) -> str:
    """Tail-cap output so a huge log never reaches the UI."""
    if not text:
        return ''
    data = text.encode('utf-8', errors='replace')
    if len(data) > max_bytes:
        data = data[-max_bytes:]
        text = data.decode('utf-8', errors='replace')
        cut = text.find('\n')
        text = text[cut + 1:] if cut != -1 else text
    lines = text.splitlines(keepends=True)
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return ''.join(lines)


def scrub_secrets(text: str) -> str:
    """Redact `psk:`/`password=`/`pin:` values; nothing else changes."""

    def _hide(match) -> str:
        key = match.group(0)
        sep = ':' if ':' in key else '='
        name = key.split(sep)[0]
        return '%s%s%s' % (name, sep, ' •••')

    return _SECRET_RE.sub(_hide, text or '')


def fetch_journal(tab: str, run=_run) -> str:
    """Unprivileged journal/dmesg read for one tab (worker only)."""
    argv = journal_argv(tab)
    if argv is not None:
        try:
            result = run(argv)
        except (OSError, subprocess.TimeoutExpired) as err:
            return 'Log unavailable: %s' % err
        if result.returncode != 0:
            detail = ((result.stderr or '') + (result.stdout or '')).strip()
            hint = detail.splitlines()[-1] if detail else 'exit %d' % (
                result.returncode)
            return ('Log unavailable (%s). Journal reads need the '
                    'systemd-journal or wheel group.' % hint)
        return scrub_secrets(cap_text(result.stdout or 'No entries.'))
    if tab == 'kernel':
        try:
            result = run(['dmesg', '--level=err,warn'], timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return 'dmesg unavailable.'
        if result.returncode != 0:
            return 'dmesg restricted (dmesg_restrict=1) — reading as service.'
        return scrub_secrets(cap_text(result.stdout or 'No entries.'))
    return ''


def helper_log_name(tab: str) -> str | None:
    """Helper ReadLog name for tabs that need privilege, else None."""
    if tab == 'system':
        return 'messages'
    if tab == 'kernel-helper':
        return 'dmesg'
    return None
