"""Mini-player restore: paused state only, newer pointer wins."""

from fake_server import DETAIL_1

from xx_audiobook import api as api_module
from xx_audiobook import restore as restore_module
from xx_audiobook import store as store_module


def _detail():
    return api_module.ItemDetail.from_dict(dict(DETAIL_1))


def test_no_last_book_restores_nothing():
    assert restore_module.restore(None, 0.0, _detail(), None) is None
    assert restore_module.restore("", 0.0, _detail(), None) is None


def test_finished_book_restores_nothing():
    row = store_module.Progress("it-1", 100.0, 7200.0, True, 99)
    assert restore_module.restore("it-1", 100.0, _detail(), row) is None


def test_progress_row_wins_over_older_pointer():
    row = store_module.Progress("it-1", 4100.0, 7200.0, False, 99)
    state = restore_module.restore("it-1", 1200.0, _detail(), row)
    assert state.position_ms == 4100000
    assert state.duration_ms == 7200000
    assert state.title == "The Long Way"
    assert state.author == "Becky Chambers"
    assert state.is_playing is False
    assert state.restored is True
    assert [c.title for c in state.chapters] == ["One", "Two"]


def test_pointer_fills_in_without_a_row():
    state = restore_module.restore("it-1", 1200.0, _detail(), None)
    assert state.position_ms == 1200000
    assert state.duration_ms == 7200000


def test_missing_detail_falls_back_to_names_and_row_duration():
    row = store_module.Progress("it-9", 60.0, 3600.0, False, 99)
    state = restore_module.restore("it-9", 60.0, None, row)
    assert state.title == "Last book"
    assert state.author == ""
    assert state.duration_ms == 3600000
    assert state.cover_url is None
