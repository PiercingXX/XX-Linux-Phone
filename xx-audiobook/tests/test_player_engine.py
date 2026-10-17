"""Engine: book transport over one backend track at a time."""

from xx_audiobook import timeline as timeline_module
from xx_audiobook.player import (
    STATUS_PAUSED,
    STATUS_PLAYING,
    STATUS_STOPPED,
    BookPlayer,
    FakeBackend,
    file_uri,
)
from xx_audiobook.stream_auth import StreamAuth
from xx_audiobook.timeline import BookTimeline, Chapter, Track

BASE = "https://estate.ts.net/audiobook/"


def _book() -> timeline_module.LoadedBook:
    timeline = BookTimeline(
        [Track("a", BASE + "api/items/it/files/a", 0, 60000,
               "audio/mp4"),
         Track("b", "file:///books/it/b.m4b", 60000, 60000,
               "audio/mp4")],
        [Chapter(0, "One", 0, 60000),
         Chapter(1, "Two", 60000, 120000)])
    return timeline_module.LoadedBook(
        "it", "Title", "Author", None, "ps-1", timeline)


def _player(**kwargs):
    auth = StreamAuth()
    auth.set(BASE, "tok")
    backend = FakeBackend()
    clock = [1000000]
    player = BookPlayer(backend, auth, now_ms=lambda: clock[0],
                        **kwargs)
    return player, backend, clock


def test_open_maps_resume_and_sends_estate_bearer_only():
    player, backend, _clock = _player()
    assert player.open_book(_book(), 90000, 1.25) is True
    assert player.status == STATUS_PAUSED
    assert player.track_index == 1
    assert backend.uris == ["file:///books/it/b.m4b"]
    assert backend.headers == [{}]
    assert backend.rate == 1.25
    player2, backend2, _clock2 = _player()
    player2.open_book(_book(), 1000, 1.0)
    assert backend2.uris == [BASE + "api/items/it/files/a"]
    assert backend2.headers == [{"Authorization": "Bearer tok"}]


def test_open_rejects_books_without_tracks():
    player, _backend, _clock = _player()
    empty = timeline_module.LoadedBook("it", timeline=BookTimeline([]))
    assert player.open_book(empty, 0, 1.0) is False
    assert player.play() is False


def test_play_applies_auto_rewind_after_long_pause():
    player, _backend, clock = _player(auto_rewind_s=5)
    player.open_book(_book(), 60000, 1.0)
    assert player.play() is True
    clock[0] += 1000
    assert player.pause() is True
    clock[0] += 20000
    assert player.play() is True
    assert player.position_global_ms() == 55000


def test_play_ignores_short_pause():
    player, _backend, clock = _player(auto_rewind_s=5)
    player.open_book(_book(), 60000, 1.0)
    player.play()
    clock[0] += 1000
    player.pause()
    clock[0] += 5000
    player.play()
    assert player.position_global_ms() == 60000


def test_seek_crosses_tracks_and_clamps():
    player, backend, _clock = _player()
    player.open_book(_book(), 0, 1.0)
    player.play()
    assert player.seek_global(90000) == 90000
    assert player.track_index == 1
    assert backend.uris[-1] == "file:///books/it/b.m4b"
    assert player.seek_global(9999999) == 120000
    assert player.seek_global(-50) == 0


def test_skip_chapters_rate_volume_and_caps():
    player, backend, _clock = _player(skip_back_s=10,
                                      skip_forward_s=30)
    player.open_book(_book(), 55000, 1.0)
    assert player.can_go_next() is True
    assert player.can_go_previous() is True
    assert player.skip_forward() == 85000
    assert player.can_go_next() is False
    assert player.can_go_previous() is True
    assert player.next_chapter() is False
    assert player.skip_back() == 75000
    assert player.previous_chapter() is True
    assert player.position_global_ms() == 60000
    assert player.set_rate(9.0) == 3.0
    assert backend.rate == 3.0
    assert player.set_rate(0.1) == 0.5
    assert player.set_volume(0.25) == 0.25
    assert backend.volume == 0.25


def test_chapterless_books_step_tracks():
    timeline = BookTimeline(
        [Track("a", "u-a", 0, 60000), Track("b", "u-b", 60000, 60000)])
    book = timeline_module.LoadedBook("it", timeline=timeline)
    player, _backend, _clock = _player()
    player.open_book(book, 1000, 1.0)
    assert player.can_go_next() is True
    assert player.can_go_previous() is True
    assert player.next_chapter() is True
    assert player.position_global_ms() == 60000
    assert player.next_chapter() is False
    assert player.previous_chapter() is True
    assert player.position_global_ms() == 0


def test_eos_advances_tracks_then_ends_the_book():
    player, backend, _clock = _player()
    ended = []
    player.on_book_ended = ended.append
    player.open_book(_book(), 1000, 1.0)
    player.play()
    backend.fire_eos()
    assert player.track_index == 1
    assert player.status == STATUS_PLAYING
    assert player.position_global_ms() == 60000
    backend.position = 60000
    backend.fire_eos()
    assert player.status == STATUS_STOPPED
    assert player.position_global_ms() == 120000
    assert ended == ["it"]


def test_stop_and_snapshot():
    player, _backend, _clock = _player()
    player.open_book(_book(), 61000, 1.5)
    player.play()
    snap = player.snapshot()
    assert (snap.item_id, snap.title, snap.author) == (
        "it", "Title", "Author")
    assert snap.status == STATUS_PLAYING
    assert snap.position_ms == 61000
    assert snap.duration_ms == 120000
    assert snap.rate == 1.5
    assert snap.chapter_index == 1
    assert snap.chapter_count == 2
    assert snap.has_book is True
    assert player.stop() is True
    assert player.status == STATUS_STOPPED
    assert player.snapshot().has_book is True


def test_file_uri_quotes_paths():
    assert file_uri("/a b/c.m4b") == "file:///a%20b/c.m4b"
