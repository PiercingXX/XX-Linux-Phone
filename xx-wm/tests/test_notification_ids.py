"""Shell-internal notification ids must be unique across modules: two
features sharing an id replace each other's notification (capture and
wireless debugging both claimed 999902 once)."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'launcher' / 'src'
_ID_RE = re.compile(r'^([A-Z_]*NOTIF_ID)\s*=\s*(\d+)', re.M)


def _ids():
    found = []
    for path in sorted(SRC.glob('*.py')):
        for name, value in _ID_RE.findall(path.read_text()):
            found.append((int(value), f'{path.name}:{name}'))
    return found


def test_shell_notification_ids_are_unique():
    seen = {}
    dupes = []
    for value, where in _ids():
        if value in seen:
            dupes.append(f'{value}: {seen[value]} and {where}')
        seen[value] = where
    assert dupes == []


def test_internal_id_sets_cover_every_shell_id():
    ids = {value for value, _ in _ids() if value >= 999900}
    for name in ('notification_shade.py', 'banner.py'):
        text = (SRC / name).read_text()
        match = re.search(r'_SHELL_INTERNAL_IDS = frozenset\(\{([^}]*)\}\)', text)
        assert match, name
        declared = {int(x) for x in re.findall(r'\d+', match.group(1))}
        assert ids <= declared, (name, sorted(ids - declared))
