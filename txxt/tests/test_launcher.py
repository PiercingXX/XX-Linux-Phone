"""The installed launcher must be directly executable."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_launcher_has_a_shebang():
    # Without it, exec of /usr/bin/txxt fails with "Exec format error"
    # and the app never starts from its desktop entry.
    first = (ROOT / "data" / "txxt.in").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!") and "python3" in first
