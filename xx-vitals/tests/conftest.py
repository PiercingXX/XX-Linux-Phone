"""Shared test setup: package sources importable without PYTHONPATH."""

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
