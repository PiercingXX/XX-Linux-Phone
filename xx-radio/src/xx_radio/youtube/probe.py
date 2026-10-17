"""Which YouTube Music engine pieces are on this phone, and do they work?

Pure parsing helpers plus one ``probe()`` that looks at installed Python
distributions and JavaScript runtimes. It never touches the network. It
does start ``<runtime> --version`` subprocesses, so call it from a worker
thread only.

The pieces (PORT_SPEC.md "Engine"):
  catalog          ytmusicapi: search, browse, library, lyrics
  resolver         yt-dlp: turns a song into an audio stream URL
  solver           yt-dlp-ejs + a JS runtime: YouTube's player challenges
  token helper     bgutil PO-token plugin + its generator: without it
                   YouTube has listed no audio formats since 2026-09-21
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable

from xx_music.engine import Component, ComponentState, EngineStatus

#: Oldest runtime each JS engine may be. Node 22 is the floor proven in the
#: field for both yt-dlp's solver and the token generator (Node 20 was
#: rejected silently); Deno 2.0 is the floor both projects document.
RUNTIME_MINIMUM: dict[str, tuple[int, ...]] = {
    "deno": (2, 0, 0),
    "node": (22, 0, 0),
    "bun": (1, 0, 31),
    "qjs": (2023, 12, 9),
}
#: Search order. Only deno and node can also run the token generator.
RUNTIME_ORDER = ("deno", "node", "bun", "qjs")
TOKEN_RUNTIMES = frozenset({"deno", "node"})
#: yt-dlp versions are dates; older than this and YouTube has usually
#: changed something it does not know about yet.
RESOLVER_MAX_AGE_DAYS = 90

_VERSION_RE = re.compile(r"(\d+)[.\-](\d+)[.\-](\d+)")


@dataclass(frozen=True)
class Runtime:
    name: str
    path: str
    version: tuple[int, ...] | None

    @property
    def usable(self) -> bool:
        return (self.version is not None
                and self.version >= RUNTIME_MINIMUM[self.name])

    @property
    def version_text(self) -> str:
        if self.version is None:
            return ""
        sep = "-" if self.name == "qjs" else "."
        return sep.join(str(part) for part in self.version)


def parse_runtime_version(text: str) -> tuple[int, ...] | None:
    """First x.y.z (or YYYY-MM-DD) in ``--version`` output."""
    match = _VERSION_RE.search(text or "")
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def resolver_age_days(version: str, today: _dt.date) -> int | None:
    """Days since a date-style yt-dlp version (2026.08.19[.n])."""
    match = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})", version or "")
    if match is None:
        return None
    try:
        released = _dt.date(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    return (today - released).days


def _run_version(path: str) -> str:
    args = [path, "--version"]
    if Path(path).name == "qjs":
        args = [path, "-h"]  # QuickJS prints its version in the help text
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "") + (done.stderr or "")


def find_runtimes(which: Callable[[str], str | None] = shutil.which,
                  run: Callable[[str], str] = _run_version,
                  extra_dirs: tuple[Path, ...] = ()) -> list[Runtime]:
    found: list[Runtime] = []
    for name in RUNTIME_ORDER:
        path = None
        for directory in extra_dirs:
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                path = str(candidate)
                break
        path = path or which(name)
        if path:
            found.append(Runtime(name, path, parse_runtime_version(run(path))))
    return found


def distribution_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


@dataclass(frozen=True)
class Installed:
    """Everything probe() looked at, so the decision logic is testable."""
    catalog: str = ""          # ytmusicapi version
    resolver: str = ""         # yt-dlp version
    ejs: str = ""              # yt-dlp-ejs version
    token_plugin: str = ""     # bgutil plugin version
    token_generator: bool = False
    runtimes: tuple[Runtime, ...] = ()


def components(found: Installed, today: _dt.date) -> tuple[Component, ...]:
    catalog = Component(
        "catalog", "Catalog",
        ComponentState.OK if found.catalog else ComponentState.MISSING,
        f"ytmusicapi {found.catalog}" if found.catalog else "",
        needed_for="Search and browse")

    age = resolver_age_days(found.resolver, today)
    if not found.resolver:
        resolver_state = ComponentState.MISSING
    elif age is not None and age > RESOLVER_MAX_AGE_DAYS:
        resolver_state = ComponentState.OUTDATED
    else:
        resolver_state = ComponentState.OK
    resolver = Component(
        "resolver", "Stream resolver", resolver_state,
        f"yt-dlp {found.resolver}" if found.resolver else "",
        needed_for="Playback")

    usable = [r for r in found.runtimes if r.usable]
    too_old = [r for r in found.runtimes if not r.usable]
    if found.ejs and usable:
        solver_state = ComponentState.OK
        solver_version = f"{usable[0].name} {usable[0].version_text}"
    elif too_old and found.ejs:
        solver_state = ComponentState.OUTDATED
        solver_version = f"{too_old[0].name} {too_old[0].version_text}"
    else:
        solver_state = ComponentState.MISSING
        solver_version = ""
    solver = Component("solver", "Challenge solver", solver_state,
                       solver_version, needed_for="Playback")

    token_runtime = any(r.name in TOKEN_RUNTIMES for r in usable)
    if found.token_plugin and found.token_generator and token_runtime:
        token_state = ComponentState.OK
    else:
        token_state = ComponentState.MISSING
    token = Component(
        "token", "Token helper", token_state,
        f"bgutil {found.token_plugin}" if found.token_plugin else "",
        needed_for="Playback")
    return (catalog, resolver, solver, token)


def status_from(found: Installed, today: _dt.date) -> EngineStatus:
    parts = components(found, today)
    by_key = {part.key: part for part in parts}
    playable = (ComponentState.OK, ComponentState.OUTDATED)
    return EngineStatus(
        can_browse=by_key["catalog"].state == ComponentState.OK,
        can_play=all(by_key[k].state in playable
                     for k in ("resolver", "solver", "token")),
        components=parts)


def generator_present(engine_dir: Path) -> bool:
    server = engine_dir / "bgutil" / "server"
    return ((server / "build" / "generate_once.js").is_file()
            or ((server / "src" / "generate_once.ts").is_file()
                and (server / "node_modules").is_dir()))


def probe(engine_dir: Path) -> Installed:
    return Installed(
        catalog=distribution_version("ytmusicapi"),
        resolver=distribution_version("yt-dlp"),
        ejs=distribution_version("yt-dlp-ejs"),
        token_plugin=distribution_version("bgutil-ytdlp-pot-provider"),
        token_generator=generator_present(engine_dir),
        runtimes=tuple(find_runtimes(
            extra_dirs=(engine_dir / "bin",))),
    )
