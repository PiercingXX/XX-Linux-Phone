"""Shared test setup: the xx_calendar package is importable headless."""
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
