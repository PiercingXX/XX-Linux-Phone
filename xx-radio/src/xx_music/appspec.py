"""What an app built on xx_music declares: identity and extension points.

xx-radio is one AppSpec. Another app reuses the core by writing its own
AppSpec with a different engine factory, extra top-level tabs and extra
player actions; nothing in xx_music changes. See PORT_SPEC.md
"Extending xx-radio".
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass, field
from typing import Callable

from xx_music.engine import Engine, Track
from xx_music.paths import AppPaths

_APP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*){2,}$")
_MPRIS_SUFFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EngineContext:
    """Handed to an engine factory: everything an engine may touch."""
    spec: AppSpec
    paths: AppPaths
    role: str = "ui"  # "ui" (the window) or "player" (the service)


EngineFactory = Callable[[EngineContext], Engine]


@dataclass(frozen=True)
class Tab:
    """An extra top-level page. ``build(context)`` returns a Gtk.Widget and
    runs on the main loop; any slow work goes through xx_music.tasks."""
    key: str
    title: str
    build: Callable[[object], object]


@dataclass(frozen=True)
class PlayerAction:
    """A custom player button, offered over io.piercingxx.XXAuto.Actions1
    (xx-auto's drive screen, the shell) and in Now Playing.

    ``handler(engine, track)`` runs in the player service on a worker
    thread. ``enabled(engine, track)`` decides whether the button is live
    for the current track; it must be cheap.
    """
    id: str
    label: str
    icon_name: str
    handler: Callable[[Engine, Track | None], None]
    enabled: Callable[[Engine, Track | None], bool] = (
        lambda _engine, track: track is not None)


@dataclass(frozen=True)
class AppSpec:
    app_id: str            # reverse-DNS GUI id, owns the window's bus name
    name: str              # shown to people
    dir_name: str          # XDG directory name
    mpris_suffix: str      # org.mpris.MediaPlayer2.<suffix>
    engine_factory: EngineFactory
    #: env var that may name another factory (dev and screenshots only)
    engine_env: str = ""
    #: named factories the env var may pick, besides "module:callable"
    engine_choices: dict[str, str] = field(default_factory=dict)
    extra_tabs: tuple[Tab, ...] = ()
    player_actions: tuple[PlayerAction, ...] = ()

    def __post_init__(self) -> None:
        if not _APP_ID_RE.match(self.app_id):
            raise ValueError(f"bad app id: {self.app_id!r}")
        if not _MPRIS_SUFFIX_RE.match(self.mpris_suffix):
            raise ValueError(f"bad MPRIS suffix: {self.mpris_suffix!r}")
        AppPaths(self.dir_name)  # raises on a bad directory name
        keys = [tab.key for tab in self.extra_tabs]
        if len(set(keys)) != len(keys) or set(keys) & set(CORE_TABS):
            raise ValueError("extra tab keys must be unique and new")
        ids = [action.id for action in self.player_actions]
        if len(set(ids)) != len(ids):
            raise ValueError("player action ids must be unique")

    # bus names and paths, all derived so derived apps never collide
    @property
    def player_bus_name(self) -> str:
        return self.app_id + ".Player"

    @property
    def player_object_path(self) -> str:
        return "/" + self.app_id.replace(".", "/") + "/Player"

    @property
    def player_interface(self) -> str:
        return self.app_id + ".Player1"

    @property
    def mpris_bus_name(self) -> str:
        return "org.mpris.MediaPlayer2." + self.mpris_suffix

    @property
    def desktop_entry(self) -> str:
        return self.app_id

    def paths(self) -> AppPaths:
        return AppPaths(self.dir_name)

    def tab_keys(self) -> list[str]:
        """Core tabs, then extras, with Settings always last."""
        return [*CORE_TABS[:-1], *(t.key for t in self.extra_tabs),
                CORE_TABS[-1]]

    def make_engine(self, role: str = "ui") -> Engine:
        factory = self.engine_factory
        override = os.environ.get(self.engine_env, "").strip() \
            if self.engine_env else ""
        if override:
            factory = load_factory(self.engine_choices.get(override, override))
        engine = factory(EngineContext(self, self.paths(), role))
        if not isinstance(engine, Engine):
            raise TypeError(f"engine factory returned {type(engine).__name__}")
        return engine


CORE_TABS = ("home", "search", "library", "settings")
CORE_TAB_TITLES = {
    "home": "Home",
    "search": "Search",
    "library": "Library",
    "settings": "Settings",
}


def load_factory(spec: str) -> EngineFactory:
    """Resolve ``"package.module:callable"`` to an engine factory."""
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"engine factory must be 'module:callable': {spec!r}")
    factory = getattr(importlib.import_module(module_name), attr)
    if not callable(factory):
        raise TypeError(f"{spec} is not callable")
    return factory
