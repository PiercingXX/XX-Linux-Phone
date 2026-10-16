"""Headless-safe imports: the pure modules must load without gi."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def store(tmp_path):
    from xx_dialer.store import Store

    handle = Store(tmp_path / "calls.db")
    yield handle
    handle.close()
