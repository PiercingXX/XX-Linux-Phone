"""Tests for app_index.py search matching."""
from pathlib import Path

import pytest

import sys
import types
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

gi = pytest.importorskip('gi')

import app_index
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


class _FakeDesktopInfo:
    """Minimal Gio.DesktopAppInfo double for refresh() tests."""

    def __init__(self, app_id: str) -> None:
        self._app_id = app_id

    def get_id(self):
        return self._app_id

    def should_show(self):
        return True

    def get_nodisplay(self):
        return False

    def get_display_name(self):
        return self._app_id

    def get_name(self):
        return self._app_id

    def get_description(self):
        return ''

    def get_generic_name(self):
        return ''

    def get_executable(self):
        return ''

    def get_keywords(self):
        return []


def _refresh_with(monkeypatch, app_ids: list) -> AppIndex:
    infos = [_FakeDesktopInfo(app_id) for app_id in app_ids]
    fake_gio = types.SimpleNamespace(
        AppInfo=types.SimpleNamespace(get_all=lambda: list(infos)),
        DesktopAppInfo=_FakeDesktopInfo,
    )
    monkeypatch.setattr(app_index, 'Gio', fake_gio)
    index = AppIndex()
    index.refresh()
    return index


class TestSupersededStockApps:
    def test_stock_contacts_hidden_when_xx_contacts_installed(
            self, monkeypatch) -> None:
        index = _refresh_with(monkeypatch, [
            'org.gnome.Contacts.desktop', 'io.piercingxx.XXContacts.desktop'])
        assert [e.app_id for e in index.entries] == [
            'io.piercingxx.XXContacts.desktop']

    def test_stock_contacts_kept_without_replacement(
            self, monkeypatch) -> None:
        index = _refresh_with(monkeypatch, ['org.gnome.Contacts.desktop'])
        assert [e.app_id for e in index.entries] == [
            'org.gnome.Contacts.desktop']

    def test_calls_and_chatty_need_their_own_replacement(
            self, monkeypatch) -> None:
        index = _refresh_with(monkeypatch, [
            'org.gnome.Calls.desktop', 'sm.puri.Chatty.desktop',
            'io.piercingxx.XXContacts.desktop'])
        assert {e.app_id for e in index.entries} == {
            'org.gnome.Calls.desktop', 'sm.puri.Chatty.desktop',
            'io.piercingxx.XXContacts.desktop'}

    def test_calls_hidden_when_dialer_installed(self, monkeypatch) -> None:
        index = _refresh_with(monkeypatch, [
            'org.gnome.Calls.desktop', 'io.piercingxx.XXDialer.desktop'])
        assert [e.app_id for e in index.entries] == [
            'io.piercingxx.XXDialer.desktop']

    def test_chatty_hidden_when_txxt_installed(self, monkeypatch) -> None:
        index = _refresh_with(monkeypatch, [
            'sm.puri.Chatty.desktop', 'io.piercingxx.TxxT.desktop'])
        assert [e.app_id for e in index.entries] == [
            'io.piercingxx.TxxT.desktop']

    def test_superseded_stock_absent_from_search(
            self, monkeypatch) -> None:
        index = _refresh_with(monkeypatch, [
            'org.gnome.Contacts.desktop', 'io.piercingxx.XXContacts.desktop'])
        assert [e.app_id for e in index.search('contacts')] == [
            'io.piercingxx.XXContacts.desktop']

    def test_replacement_for_names_the_xx_successor(
            self, monkeypatch) -> None:
        index = _refresh_with(monkeypatch, [
            'org.gnome.Contacts.desktop', 'io.piercingxx.XXContacts.desktop'])
        assert index.replacement_for(
            'org.gnome.Contacts.desktop') == 'io.piercingxx.XXContacts.desktop'
        assert index.replacement_for(
            'org.gnome.Contacts') == 'io.piercingxx.XXContacts.desktop'
        assert index.replacement_for('io.piercingxx.XXContacts.desktop') is None

    def test_replacement_for_empty_without_successor(
            self, monkeypatch) -> None:
        index = _refresh_with(monkeypatch, ['org.gnome.Contacts.desktop'])
        assert index.replacement_for('org.gnome.Contacts.desktop') is None
        assert index.replacement_for('org.gnome.Calculator.desktop') is None
