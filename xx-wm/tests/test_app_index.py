"""Tests for app_index.py search matching."""
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

gi = pytest.importorskip('gi')

from app_index import AppEntry, AppIndex


def _entry(app_id: str, name: str, description: str = '', keywords: str = '') -> AppEntry:
    search_text = ' '.join(p for p in [name, description, keywords, app_id] if p).casefold()
    return AppEntry(app_id=app_id, name=name, description=description,
                    executable='', search_text=search_text, desktop_app=None)


@pytest.fixture
def index():
    idx = AppIndex()
    idx.entries = sorted([
        _entry('cam.desktop', 'Camera'),
        _entry('calc.desktop', 'Calculator', 'Do math'),
        _entry('files.desktop', 'Files', 'Browse documents'),
        _entry('fractal.desktop', 'Fractal', 'Matrix chat client', 'messaging'),
        _entry('term.desktop', 'Terminal'),
    ], key=lambda e: e.name.casefold())
    return idx


class TestSearch:
    def test_empty_query_returns_all_sorted(self, index):
        results = index.search('')
        assert [e.name for e in results] == ['Calculator', 'Camera', 'Files', 'Fractal', 'Terminal']

    def test_prefix_matches_rank_before_contains(self, index):
        results = index.search('ca')
        names = [e.name for e in results]
        assert names[:2] == ['Calculator', 'Camera']

    def test_contains_matches_description_and_keywords(self, index):
        assert [e.name for e in index.search('math')] == ['Calculator']
        assert [e.name for e in index.search('messaging')] == ['Fractal']

    def test_case_insensitive(self, index):
        assert [e.name for e in index.search('FRACTAL')] == ['Fractal']

    def test_no_match(self, index):
        assert index.search('zzz') == []

    def test_resolve_keeps_order_and_drops_unknown(self, index):
        resolved = index.resolve(['term.desktop', 'missing.desktop', 'cam.desktop'])
        assert [e.name for e in resolved] == ['Terminal', 'Camera']
