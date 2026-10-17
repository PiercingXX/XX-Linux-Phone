"""TrackMath parity: BookTimeline, PlayerCommands, resume, auto-rewind.

Mirrors playback/TrackMath.kt, PlayerCommands.kt and BrowseTree.resumeMs/
AutoRewind, including the 1 s normalize rule and the chapter/track maps.
"""

import math

from xx_audiobook import timeline as timeline_module
from xx_audiobook.timeline import (
    BookTimeline,
    Chapter,
    Position,
    Track,
)


def _book() -> BookTimeline:
    tracks = [Track("a", "uri-a", 0, 3600000, "audio/mp4"),
              Track("b", "uri-b", 3600000, 3600000, "audio/mp4")]
    chapters = [Chapter(0, "One", 0, 3600000),
                Chapter(1, "Two", 3600000, 7200000)]
    return BookTimeline(tracks, chapters)


def test_seconds_round_halves_up():
    assert timeline_module.seconds_to_ms(2.3465) == 2347
    assert timeline_module.seconds_to_ms(0.0) == 0
    assert timeline_module.seconds_to_ms(3600.0) == 3600000


def test_normalize_trusts_offsets_within_one_second():
    tracks = [Track("a", "u", 0, 3600000),
              Track("b", "u", 3599500, 3600000)]
    assert BookTimeline(tracks, []).tracks[1].start_ms == 3599500


def test_normalize_relays_tracks_when_offsets_drift():
    tracks = [Track("a", "u", 0, 3600000),
              Track("b", "u", 5000, 3600000)]
    laid = BookTimeline(tracks, []).tracks
    assert [t.start_ms for t in laid] == [0, 3600000]


def test_normalize_boundary_is_trusted_at_exactly_one_second():
    tracks = [Track("a", "u", 0, 3600000),
              Track("b", "u", 3601000, 1000)]
    assert BookTimeline(tracks, []).tracks[1].start_ms == 3601000
    tracks = [Track("a", "u", 0, 3600000),
              Track("b", "u", 3601001, 1000)]
    assert BookTimeline(tracks, []).tracks[1].start_ms == 3600000


def test_locate_clamps_and_maps_boundaries():
    book = _book()
    assert book.locate(-5) == Position(0, 0)
    assert book.locate(0) == Position(0, 0)
    assert book.locate(3599999) == Position(0, 3599999)
    assert book.locate(3600000) == Position(1, 0)
    assert book.locate(7200000) == Position(1, 3600000)
    assert book.locate(99999999) == Position(1, 3600000)
    assert BookTimeline([], []).locate(10) == Position(0, 0)


def test_global_of_clamps_and_rejects_bad_tracks():
    book = _book()
    assert book.global_of(0, 1000) == 1000
    assert book.global_of(1, 1000) == 3601000
    assert book.global_of(1, 99999999) == 7200000
    assert book.global_of(0, -3) == 0
    assert book.global_of(7, 0) == 0
    assert book.global_of(-1, 0) == 0


def test_chapter_maps():
    book = _book()
    assert book.chapter_at(0) == 0
    assert book.chapter_at(3600000) == 1
    assert book.chapter_at(99999999) == 1
    assert book.chapter_start(1) == Position(1, 0)
    assert book.chapter_start(9) is None
    assert book.chapter_end_at(100) == 3600000
    assert book.chapter_end_at(7200000) == 7200000
    assert BookTimeline(book.tracks, []).chapter_at(5) == -1
    assert BookTimeline(book.tracks, []).chapter_end_at(5) == 7200000


def test_chapters_sorted_and_end_capped_by_duration():
    book = BookTimeline(
        [Track("a", "u", 0, 1000)],
        [Chapter(1, "B", 500, 99999), Chapter(0, "A", 0, 500)])
    assert [c.index for c in book.chapters] == [0, 1]
    assert book.chapter_end_at(600) == 1000


def test_from_play_resolves_uris_and_chapters():
    from xx_audiobook import api as api_module

    play = api_module.PlayResponse.from_dict({
        "session_id": "s", "item_id": "it", "resume_position_s": 0.0,
        "duration_s": 20.0, "speed": 1.0,
        "tracks": [
            {"file_id": "f1", "url": "api/items/it/files/f1",
             "start_offset_s": 0.0, "duration_s": 10.0,
             "mime": "audio/mp4", "size": 1},
            {"file_id": "f2", "url": "api/items/it/files/f2",
             "start_offset_s": 10.0, "duration_s": 10.0,
             "mime": "audio/mp4", "size": 1}],
        "chapters": [
            {"index": 0, "title": "A", "start_s": 0.0, "end_s": 20.0}]})
    timeline = BookTimeline.from_play(
        play, lambda fid, url: "file:///x/" + fid)
    assert [t.uri for t in timeline.tracks] == [
        "file:///x/f1", "file:///x/f2"]
    assert timeline.duration_ms == 20000
    assert timeline.chapters[0].title == "A"


def test_media_id_and_track_index_of():
    book = _book()
    loaded = timeline_module.LoadedBook("it-1", timeline=book)
    assert loaded.media_id(1) == "it-1/b"
    assert timeline_module.track_index_of(loaded, "it-1/b") == 1
    assert timeline_module.track_index_of(loaded, "it-1/zz") == -1
    assert timeline_module.track_index_of(loaded, "other/b") == -1
    assert timeline_module.track_index_of(loaded, "it-1") == -1


def test_skip_next_previous_chapter():
    book = _book()
    assert timeline_module.skip_to(book, 1000, 30000) == 31000
    assert timeline_module.skip_to(book, 1000, -5000) == 0
    assert timeline_module.skip_to(book, 7199999, 5000) == 7200000
    assert timeline_module.next_chapter(book, 100) == 3600000
    assert timeline_module.next_chapter(book, 3600000) is None
    assert timeline_module.next_chapter(
        BookTimeline(book.tracks, []), 100) is None
    assert timeline_module.previous_chapter(book, 5000) == 0
    assert timeline_module.previous_chapter(book, 3600000 + 3001) == 3600000
    assert timeline_module.previous_chapter(book, 3600000 + 3000) == 0
    assert timeline_module.previous_chapter(book, 100) == 0
    assert timeline_module.previous_chapter(
        BookTimeline(book.tracks, []), 100) == 0


def test_speed_helpers():
    assert timeline_module.clamp_speed(float("nan")) == 1.0
    assert timeline_module.clamp_speed(9.0) == 3.0
    assert timeline_module.clamp_speed(0.1) == 0.5
    assert timeline_module.step_speed(1.0, True) == 1.1
    assert timeline_module.step_speed(1.0, False) == 0.75
    assert timeline_module.step_speed(3.0, True) == 3.0
    assert timeline_module.step_speed(0.5, False) == 0.5
    assert timeline_module.initial_speed(None, 1.25) == 1.25
    assert timeline_module.initial_speed(9.0, 1.0) == 3.0
    assert timeline_module.skip_ms(30) == 30000
    assert timeline_module.skip_ms(3) == 5000
    assert timeline_module.skip_ms(200) == 120000
    assert timeline_module.SKIP_CHOICES == (10, 15, 30)
    assert math.isclose(timeline_module.MIN_SPEED, 0.5)
    assert math.isclose(timeline_module.MAX_SPEED, 3.0)


def test_resume_ms_rules():
    from xx_audiobook import store as store_module

    finished = store_module.Progress("it", 50.0, 100.0, True, 9)
    assert timeline_module.resume_ms(finished, "it", 50.0, "it", 100000) == 0
    local = store_module.Progress("it", 50.0, 100.0, False, 9)
    assert timeline_module.resume_ms(
        local, "it", 10.0, "it", 100000) == 50000
    assert timeline_module.resume_ms(
        None, "it", 10.0, "it", 100000) == 10000
    assert timeline_module.resume_ms(
        None, "other", 10.0, "it", 100000) == 0
    assert timeline_module.resume_ms(
        local, "it", 10.0, "it", 40000) == 40000
    assert timeline_module.resume_ms(None, "it", 10.0, "it", 0) == 10000


def test_auto_rewind_rules():
    rewind = timeline_module.auto_rewind_ms
    assert rewind(0, 20000, 2) == 0
    assert rewind(1000, 5000, 2) == 0
    assert rewind(1000, 11000, 2) == 2000
    assert rewind(1000, 11000, 0) == 0
    assert rewind(1000, 11000 - 1, 2) == 0
