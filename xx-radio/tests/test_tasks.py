"""Worker results come back on the posting thread; stale ones are dropped."""

import queue
import threading

from xx_music.tasks import Generation, Worker


def _collecting_worker():
    posted: queue.Queue = queue.Queue()
    return Worker(max_workers=2, post=posted.put), posted


def _drain(posted, count):
    for _ in range(count):
        posted.get(timeout=5)()


def test_result_and_error_delivered_via_post():
    worker, posted = _collecting_worker()
    got, errors = [], []
    worker.run(lambda: threading.current_thread().name, got.append)
    worker.run(lambda: 1 / 0, got.append, errors.append)
    _drain(posted, 2)
    assert len(got) == 1 and got[0].startswith("xx-music")
    assert isinstance(errors[0], ZeroDivisionError)
    worker.shutdown()


def test_stale_generation_is_dropped():
    worker, posted = _collecting_worker()
    generation = Generation()
    gate = threading.Event()
    got = []
    worker.run(lambda: (gate.wait(5), "old")[1], got.append,
               generation=generation)
    worker.run(lambda: "new", got.append, generation=generation)
    gate.set()
    _drain(posted, 2)
    assert got == ["new"]
    worker.shutdown()


def test_glib_post_reaches_the_main_context():
    from gi.repository import GLib

    worker = Worker()
    got = []
    loop = GLib.MainLoop()
    worker.run(lambda: 42, lambda value: (got.append(value), loop.quit()))
    GLib.timeout_add(5000, loop.quit)
    loop.run()
    assert got == [42]
    worker.shutdown()
