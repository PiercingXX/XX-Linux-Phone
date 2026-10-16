"""Shared pytest setup: put src/ on sys.path so the package imports
without an install, headless by construction (no gi, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
