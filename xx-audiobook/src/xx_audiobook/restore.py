"""Cold-start mini-player restore (M2).

Mirrors playback/PlayerRestore.kt: the last book and position become a
paused PlayerUi with the cached title/author before anything is loaded
into the player. State only, always paused. Pure; the GUI calls this on
cold start and plays through the player service.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from xx_audiobook import timeline as timeline_module


@dataclass
class PlayerUi:
    item_id: str
    title: str = "Last book"
    author: str = ""
    cover_url: str | None = None
    is_playing: bool = False
    position_ms: int = 0
    duration_ms: int = 0
    restored: bool = True
    chapters: list = field(default_factory=list)


def restore(last_item_id: str | None, last_position_s: float,
            cached_detail, progress_row) -> PlayerUi | None:
    """Newer of the last-played pointer and the progress row wins."""
    if not last_item_id:
        return None
    if progress_row is not None and progress_row.is_finished:
        return None
    position_s = last_position_s
    if progress_row is not None and progress_row.position_s > 0:
        position_s = progress_row.position_s
    duration_s = 0.0
    detail_duration = getattr(cached_detail, "duration_s", 0.0) or 0.0
    if detail_duration > 0:
        duration_s = detail_duration
    elif progress_row is not None and (progress_row.duration_s or 0) > 0:
        duration_s = progress_row.duration_s
    chapters = []
    detail_chapters = getattr(cached_detail, "chapters", None) or []
    if detail_chapters:
        chapters = timeline_module.chapters_of(detail_chapters)
        chapters.sort(key=lambda c: c.start_ms)
    title = getattr(cached_detail, "title", None) or "Last book"
    authors = getattr(cached_detail, "authors", None) or []
    return PlayerUi(
        item_id=last_item_id, title=title,
        author=", ".join(authors),
        cover_url=getattr(cached_detail, "cover_url", None),
        is_playing=False,
        position_ms=timeline_module.seconds_to_ms(position_s),
        duration_ms=timeline_module.seconds_to_ms(duration_s),
        restored=True, chapters=chapters)
