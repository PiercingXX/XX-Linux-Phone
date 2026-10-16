"""Battery-use settings section: honest per-app estimates.

Linux has no per-app power meter, so this combines what is measurable:
foreground screen time (usage_stats) and CPU time from the cgroup of the
shell's own per-app systemd scope (app_standby tracks every shell launch
in app-xxwm-<app>-<pid>.scope, so CPU time is attributable without any
launch-path change). Shares are labeled estimated everywhere.
"""
from __future__ import annotations

import settings_page

from app_names import fallback_app_name, friendly_app_name  # noqa: F401

import os
import re

# fallback_app_name/friendly_app_name live in app_names now; the names
# stay importable here for existing users (settings_screentime, tests).
__all__ = ['fallback_app_name', 'friendly_app_name']

CGROUP_ROOT = '/sys/fs/cgroup'

_SCOPE_CLEAN_RE = re.compile(r'[^A-Za-z0-9_.-]')


def sanitize_app_id(app_id: str) -> str:
    """Scope-name fragment, mirroring app_standby.sanitize_app_id (a test
    pins the two together so scope scans match the scopes we place)."""
    return _SCOPE_CLEAN_RE.sub('_', (app_id or '').strip()) or 'unknown'


def parse_cpu_stat(text: str) -> int | None:
    """usage_usec from a cpu.stat body; None when absent or malformed."""
    for line in (text or '').splitlines():
        key, sep, value = line.partition(' ')
        if sep and key.strip() == 'usage_usec':
            try:
                usec = int(value.strip())
            except (TypeError, ValueError):
                return None
            return usec if usec >= 0 else None
    return None


def app_slice_dir(cgroup_root: str = CGROUP_ROOT, uid: int | None = None) -> str:
    """app.slice holding this user's scopes (systemd user manager layout)."""
    if uid is None:
        try:
            uid = os.getuid()
        except Exception:
            return ''
    return os.path.join(
        cgroup_root, 'user.slice', f'user-{uid}.slice',
        f'user@{uid}.service', 'app.slice')


def scope_dirs_for(app_id: str, directory: str) -> list[str]:
    """Our standby scopes for one app inside an app.slice dir."""
    fragment = sanitize_app_id(app_id)
    prefix = f'app-xxwm-{fragment}-'
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names
            if name.startswith(prefix) and name.endswith('.scope')]


def cpu_time_for_app(app_id: str, directory: str | None = None) -> int | None:
    """Summed usage_usec across this app's scopes; None when unattributable
    (no scope, unreadable files) so the UI falls back to screen time."""
    if directory is None:
        directory = app_slice_dir()
    if not directory:
        return None
    total = 0
    seen = False
    for scope in scope_dirs_for(app_id, directory):
        try:
            with open(os.path.join(scope, 'cpu.stat'),
                      encoding='utf-8') as handle:
                text = handle.read()
        except OSError:
            continue
        usec = parse_cpu_stat(text)
        if usec is None:
            continue
        total += usec
        seen = True
    return total if seen else None


def cgroup_cpu_capable(cgroup_root: str = CGROUP_ROOT) -> bool:
    """Read-only capability check: our app.slice must be listable."""
    directory = app_slice_dir(cgroup_root)
    return bool(directory) and os.path.isdir(directory)


def estimate_shares(screen_secs: dict, cpu_usecs: dict) -> list:
    """Ranked [(app_id, share_pct, screen_s, cpu_s)]: score is screen
    seconds plus CPU core-seconds, so a silent background burner and a
    long-foreground reader both surface. Shares sum to 100 (or the list
    is empty when nothing is measurable)."""
    apps = set(screen_secs) | set(cpu_usecs)
    scored = []
    for app_id in apps:
        try:
            screen = max(0, int(screen_secs.get(app_id, 0)))
        except (TypeError, ValueError):
            screen = 0
        try:
            cpu = max(0, int(cpu_usecs.get(app_id, 0)))
        except (TypeError, ValueError):
            cpu = 0
        score = screen + cpu / 1_000_000
        if score > 0:
            scored.append((app_id, score, screen, cpu))
    total = sum(score for _app, score, _s, _c in scored)
    if total <= 0:
        return []
    ranked = sorted(scored, key=lambda row: row[1], reverse=True)
    return [(app_id, 100.0 * score / total, screen, cpu)
            for app_id, score, screen, cpu in ranked]


def format_share(row) -> str:
    """One ranked row: `68% — 12m screen · 3m CPU`."""
    _app_id, pct, screen, cpu = row
    return (f'{pct:.0f}% — {format_minutes(screen)} screen · '
            f'{format_minutes(cpu // 1_000_000)} CPU')


def format_minutes(secs: int) -> str:
    try:
        total = max(0, int(secs))
    except (TypeError, ValueError):
        total = 0
    if total < 60:
        return f'{total}s'
    minutes, _rest = divmod(total, 60)
    if minutes < 60:
        return f'{minutes}m'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h {minutes:02d}m'


def usage_tracker_for(win):
    """The app-owned UsageTracker, or None (headless stubs, trackers off)."""
    try:
        app = win.get_application()
    except Exception:
        return None
    return getattr(app, '_usage_tracker', None)


class BatteryUseSection(settings_page._Section):
    """Settings section: per-app battery estimates since full charge."""

    key = 'batteryuse'
    title = 'Battery use'

    def __init__(self) -> None:
        self._list_box = None
        self._note = None

    def build(self, win) -> list:
        from settings_page import _section_card, _section_title
        from gi.repository import Gtk, Pango

        card = _section_card(spacing=8)
        note = Gtk.Label(
            label='Since full charge · estimated', xalign=0)
        note.add_css_class('dim-label')
        note.set_wrap(True)
        note.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        note.set_max_width_chars(26)
        card.append(note)
        self._list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.append(self._list_box)
        self._note = Gtk.Label(label='', xalign=0)
        self._note.add_css_class('dim-label')
        self._note.set_wrap(True)
        self._note.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._note.set_max_width_chars(26)
        card.append(self._note)
        return [_section_title(self.title), card]

    def refresh(self, win) -> None:
        from settings_page import _run_bg
        tracker = usage_tracker_for(win)
        directory = getattr(self, '_slice_override', None)
        _run_bg(lambda: self._gather(tracker, directory),
                lambda res: self._render(win, res))

    # ------------------------------------------------------------ behavior

    def _gather(self, tracker, directory: str | None):
        """Worker thread: ({app: screen_s}, {app: cpu_us}). The usage
        query is in-memory but touches day files on first load, and
        cpu.stat reads walk sysfs — both stay off the main loop."""
        import datetime
        screen: dict = {}
        if tracker is not None:
            try:
                screen = tracker.daily_totals(datetime.date.today())
            except Exception:
                screen = {}
        if directory is None:
            directory = app_slice_dir()
        else:
            directory = str(directory)
        cpu: dict = {}
        capable = (getattr(self, '_slice_override', None) is not None
                   or cgroup_cpu_capable())
        if capable:
            for app_id in screen:
                usec = cpu_time_for_app(app_id, directory)
                if usec is not None:
                    cpu[app_id] = usec
        return (screen, cpu)

    def _render(self, win, res) -> None:
        from settings_page import (_replace_box_children,
                                   _section_placeholder)
        from gi.repository import Gtk, Pango
        if self._list_box is None:
            return
        try:
            screen, cpu = res
        except (TypeError, ValueError):
            return
        rows = estimate_shares(screen, cpu)
        if not rows:
            _replace_box_children(
                self._list_box,
                [_section_placeholder('No usage yet.')])
        else:
            widgets = []
            for row in rows:
                name = self._display_name(win, row[0])
                label = Gtk.Label(label=f'{name}: {format_share(row)}',
                                  xalign=0)
                label.add_css_class('dim-label')
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_max_width_chars(26)
                widgets.append(label)
            _replace_box_children(self._list_box, widgets)
        detail = ('Screen time today + CPU time · estimated'
                  if cpu else 'Screen time today · estimated (CPU unattributed)')
        if self._note is not None:
            self._note.set_text(detail)

    def _display_name(self, win, app_id: str) -> str:
        return friendly_app_name(win, app_id)
