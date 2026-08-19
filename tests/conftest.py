"""Shared test setup: launcher sources importable, HOME never the real one
for tests that opt into the isolated_home fixture."""
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
