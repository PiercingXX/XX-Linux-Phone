"""xx_music: the reusable, source-agnostic music player core.

Engine interface, models, app spec (extension points), paths, worker
helpers, and (from later stages) the player service, queue, cache and core
UI. Nothing in this package may import a music source library or the
xx_radio app; tests/test_isolation.py enforces that.
"""

__version__ = "0.1.0"
#: Bumped whenever the Engine contract changes incompatibly.
ENGINE_API = 1
