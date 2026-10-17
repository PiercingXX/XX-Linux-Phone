"""Run blocking work off the GTK main loop and hand results back to it.

Engine calls, image fetches and disk scans all go through ``Worker.run``:
the function runs on a small thread pool; ``on_done`` / ``on_error`` run
later on the main loop via GLib.idle_add. A ``Generation`` lets a page drop
answers to questions it no longer asks (a newer search, a page left).
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class Generation:
    """A counter a page bumps when its old in-flight answers go stale."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._value += 1
            return self._value

    def current(self) -> int:
        with self._lock:
            return self._value


def _idle_add(callback: Callable[[], None]) -> None:
    from gi.repository import GLib

    def once() -> bool:
        callback()
        return False

    GLib.idle_add(once)


class Worker:
    def __init__(self, max_workers: int = 3, name: str = "xx-music",
                 post: Callable[[Callable[[], None]], None] | None = None
                 ) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix=name)
        self._post = post or _idle_add

    def run(self, fn: Callable[[], Any],
            on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[BaseException], None] | None = None,
            generation: Generation | None = None) -> Future:
        """Run ``fn`` on the pool; deliver its outcome on the main loop.

        With ``generation``, the outcome is dropped when the generation
        moved on before it arrived.
        """
        ticket = generation.next() if generation is not None else None

        def deliver(future: Future) -> None:
            def on_main() -> None:
                if generation is not None and generation.current() != ticket:
                    return
                error = future.exception()
                if error is not None:
                    if on_error is not None:
                        on_error(error)
                elif on_done is not None:
                    on_done(future.result())
            self._post(on_main)

        future = self._pool.submit(fn)
        future.add_done_callback(deliver)
        return future

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
