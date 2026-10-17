"""Subprocess driver for the Undo toast regression test.

Importing xx_email.main pulls in gi, which would poison the
no-gi-import guards elsewhere in the suite, so this scenario runs in
its own interpreter. Exits 0 printing UNDO_OK when tapping Undo runs
the toast callback; any failure raises (nonzero exit).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xx_email import main as main_mod


def check() -> None:
    app = main_mod.EmailApplication()
    try:
        ran = []
        app.toast("Queued \u2014 undo within 10 s",
                  lambda: ran.append(True))
        app._activate_undo()
        assert ran == [True], \
            "tapping Undo must run the toast callback"
        assert app._pending_undo is None
    finally:
        app._executor.shutdown(wait=True)
    print("UNDO_OK")


if __name__ == "__main__":
    check()
