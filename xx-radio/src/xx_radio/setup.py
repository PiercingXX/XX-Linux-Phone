"""The private, promotable engine venv and the token helper set-up.

FuriOS ships no ``python3-venv``, no ``pip`` and no download helper, so
everything goes through a downloaded static ``uv`` binary whose sha256 is
checked before it is ever executed. All installs are root-free under
``engine_dir``
(``~/.local/share/xx-radio/engine``); the system interpreter is never
touched. Every step is resumable (finished work is skipped) and a failed
step raises SetupError while leaving the promoted state unchanged: scratch
work happens in ``venv.new`` / ``bgutil.new`` and is only renamed into
place after its checks pass.

External commands and downloads are injected (``runner``/``fetch``) so
tests use fakes; nothing here ever downloads for real in tests.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from xx_radio.youtube.probe import (RUNTIME_MINIMUM, generator_present,
                                    parse_runtime_version)

#: Pinned uv release that creates the venv and installs packages.
UV_VERSION = "0.13.1"
UV_RELEASE_URL = ("https://github.com/astral-sh/uv/releases/download/"
                  "{version}/uv-{plat}.tar.gz")
#: sha256 of each static tarball, copied from the release's own .sha256
#: files and checked before extraction. Refresh all four when UV_VERSION
#: moves. The musl builds are fully static and the right choice for
#: musl-based phones (postmarketOS); glibc ones (FuriOS, Mobian, Arch) take gnu.
UV_SHA256 = {
    "aarch64-unknown-linux-gnu": (
        "9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14"),
    "aarch64-unknown-linux-musl": (
        "345a8308e4d1835cdfaadf692836fd3137a6d45864773f05ad7bf59442b1ba16"),
    "x86_64-unknown-linux-gnu": (
        "eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220"),
    "x86_64-unknown-linux-musl": (
        "6c86040a8f045ab9adc9e3ec4cdafa03fd00e9a988ed2bf28971c7fc2d0debd0"),
}
#: Installed into the new venv on every engine update.
ENGINE_PACKAGES = ("ytmusicapi", "yt-dlp", "yt-dlp-ejs",
                   "bgutil-ytdlp-pot-provider")
#: Token helper generator, built in script mode with Node >= 22.
BGUTIL_REPO = ("https://github.com/brainicism/"
               "bgutil-ytdlp-pot-provider.git")
BGUTIL_TAG = "v1.2.2"
BGUTIL_PLUGIN = "bgutil-ytdlp-pot-provider"
NODE_MINIMUM = RUNTIME_MINIMUM["node"]
_FETCH_TIMEOUT_S = 60
_FETCH_MAX_BYTES = 64 * 2**20


class SetupError(Exception):
    pass


@dataclass(frozen=True)
class RunResult:
    args: tuple[str, ...]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


Runner = Callable[..., RunResult]
Fetcher = Callable[[str], bytes]


def _default_runner(args: list[str], *,
                    cwd: Path | str | None = None) -> RunResult:
    done = subprocess.run(args, capture_output=True, text=True, check=False,
                          cwd=str(cwd) if cwd else None, timeout=600)
    return RunResult(tuple(args), done.returncode, done.stdout or "",
                     done.stderr or "")


def _default_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as response:
        chunks = []
        total = 0
        while True:
            piece = response.read(65536)
            if not piece:
                break
            total += len(piece)
            if total > _FETCH_MAX_BYTES:
                raise SetupError(f"download too large: {url}")
            chunks.append(piece)
    return b"".join(chunks)


# -- layout ---------------------------------------------------------------

def uv_path(engine_dir: Path | str) -> Path:
    return Path(engine_dir) / "bin" / "uv"


def venv_dir(engine_dir: Path | str) -> Path:
    return Path(engine_dir) / "venv"


def new_venv_dir(engine_dir: Path | str) -> Path:
    return Path(engine_dir) / "venv.new"


def prev_venv_dir(engine_dir: Path | str) -> Path:
    return Path(engine_dir) / "venv.previous"


def venv_python(venv: Path | str) -> Path:
    return Path(venv) / "bin" / "python"


def bgutil_dir(engine_dir: Path | str) -> Path:
    return Path(engine_dir) / "bgutil"


def new_bgutil_dir(engine_dir: Path | str) -> Path:
    return Path(engine_dir) / "bgutil.new"


def plugin_dir(engine_dir: Path | str) -> Path:
    return Path(engine_dir) / "plugins"


def venv_ok(venv: Path | str) -> bool:
    venv = Path(venv)
    python = venv_python(venv)
    return ((venv / "pyvenv.cfg").is_file() and python.is_file()
            and os.access(python, os.X_OK))


def platform_tag() -> str:
    """One of the UV_SHA256 keys, from the CPU and the C library."""
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        arch = "aarch64"
    elif machine in ("x86_64", "amd64"):
        arch = "x86_64"
    else:
        raise SetupError(f"unsupported architecture: {platform.machine()}")
    libc, _ = platform.libc_ver()
    suffix = "gnu" if libc == "glibc" else "musl"
    return f"{arch}-unknown-linux-{suffix}"


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# -- uv ---------------------------------------------------------------------

def ensure_uv(engine_dir: Path | str, *, fetch: Fetcher | None = None,
              version: str = UV_VERSION,
              checksums: dict[str, str] | None = None) -> Path:
    """Fetch the static uv binary once, verify it, and return its path."""
    target = uv_path(engine_dir)
    if target.is_file() and os.access(target, os.X_OK):
        return target
    plat = platform_tag()
    table = UV_SHA256 if checksums is None else checksums
    expected = table.get(plat, "")
    if not expected:
        raise SetupError(f"no checksum for uv on {plat}: refusing download")
    url = UV_RELEASE_URL.format(version=version, plat=plat)
    try:
        data = (fetch or _default_fetch)(url)
    except SetupError:
        raise
    except Exception as exc:
        raise SetupError(f"could not download uv: {exc}") from exc
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, expected.lower()):
        raise SetupError("uv checksum mismatch: refusing to install")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member = next(
                (info for info in tar.getmembers()
                 if info.isfile() and Path(info.name).name == "uv"), None)
            if member is None:
                raise SetupError("uv binary missing from the release")
            member.uid = os.getuid() if hasattr(os, "getuid") else 0
            extracted = tar.extractfile(member)
            if extracted is None:
                raise SetupError("uv binary missing from the release")
            binary = extracted.read()
    except tarfile.TarError as exc:
        raise SetupError(f"bad uv release: {exc}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"uv.tmp-{os.getpid()}")
    try:
        tmp.write_bytes(binary)
        os.chmod(tmp, 0o700)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


# -- engine venv --------------------------------------------------------------

def create_venv(engine_dir: Path | str, *,
                runner: Runner | None = None) -> Path:
    """Create ``venv.new`` with uv. Skipped when it already validates."""
    new = new_venv_dir(engine_dir)
    if venv_ok(new):
        return new
    _rmtree(new)
    uv = uv_path(engine_dir)
    if not (uv.is_file() and os.access(uv, os.X_OK)):
        raise SetupError("uv is not set up; run ensure_uv first")
    run = runner or _default_runner
    done = run([str(uv), "venv", "--system-site-packages", str(new)])
    if done.returncode != 0 or not venv_ok(new):
        _rmtree(new)
        tail = (done.stderr or done.stdout or "").strip()[-300:]
        raise SetupError(f"could not create the engine venv: {tail}")
    return new


def install_packages(engine_dir: Path | str, *,
                     runner: Runner | None = None,
                     packages: tuple[str, ...] = ENGINE_PACKAGES,
                     pre_release: bool = False) -> None:
    """Install/upgraded packages into ``venv.new`` (never the live venv)."""
    new = new_venv_dir(engine_dir)
    python = venv_python(new)
    if not venv_ok(new):
        raise SetupError("create the new venv first")
    uv = uv_path(engine_dir)
    args = [str(uv), "pip", "install", "--python", str(python), "--upgrade",
            *packages]
    if pre_release:
        args.append("--pre")
    run = runner or _default_runner
    done = run(args)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip()[-300:]
        raise SetupError(f"could not install the engine packages: {tail}")


_CHECK_SNIPPET = ("import importlib.metadata as m;"
                  "print('ytmusicapi', m.version('ytmusicapi'));"
                  "print('yt-dlp', m.version('yt-dlp'))")


def self_check(engine_dir: Path | str, *,
               runner: Runner | None = None) -> dict[str, str]:
    """Offline self-check of ``venv.new``: imports and versions."""
    python = venv_python(new_venv_dir(engine_dir))
    if not python.is_file():
        raise SetupError("create the new venv first")
    run = runner or _default_runner
    done = run([str(python), "-c", _CHECK_SNIPPET])
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip()[-300:]
        raise SetupError(f"the new engine failed its self-check: {tail}")
    versions: dict[str, str] = {}
    for line in done.stdout.splitlines():
        name, _, version = line.partition(" ")
        name, version = name.strip(), version.strip()
        if name and version:
            versions[name] = version
    if "ytmusicapi" not in versions or "yt-dlp" not in versions:
        raise SetupError("the new engine failed its self-check")
    return versions


def promote(engine_dir: Path | str) -> None:
    """Make ``venv.new`` live; the old venv is kept as ``venv.previous``."""
    new = new_venv_dir(engine_dir)
    if not venv_ok(new):
        raise SetupError("nothing valid to promote")
    live = venv_dir(engine_dir)
    prev = prev_venv_dir(engine_dir)
    if live.exists():
        if prev.exists():
            _rmtree(prev)
        os.rename(live, prev)
    os.rename(new, live)


def undo(engine_dir: Path | str) -> None:
    """Swap ``venv.previous`` back into place."""
    live = venv_dir(engine_dir)
    prev = prev_venv_dir(engine_dir)
    if not prev.exists():
        raise SetupError("no previous engine to undo to")
    if live.exists():
        tmp = Path(engine_dir) / "venv.swap"
        if tmp.exists():
            _rmtree(tmp)
        os.rename(live, tmp)
        os.rename(prev, live)
        os.rename(tmp, prev)
    else:
        os.rename(prev, live)


def drop_to_system(engine_dir: Path | str) -> None:
    """Remove every staged venv; the app falls back to system packages."""
    _rmtree(venv_dir(engine_dir))
    _rmtree(new_venv_dir(engine_dir))
    _rmtree(prev_venv_dir(engine_dir))


def update_engine(engine_dir: Path | str, *,
                  runner: Runner | None = None,
                  fetch: Fetcher | None = None,
                  pre_release: bool = False,
                  online_check: Callable[[Path], None] | None = None,
                  uv_checksums: dict[str, str] | None = None,
                  ) -> dict[str, str]:
    """Full update into ``venv.new`` with checks, then promote.

    ``online_check`` (search one fixed term, resolve one fixed song) runs
    before the promote; any failure leaves the live venv unchanged.
    ``uv_checksums`` overrides the pinned table (tests, mirrors).
    """
    ensure_uv(engine_dir, fetch=fetch,
              checksums=UV_SHA256 if uv_checksums is None else uv_checksums)
    create_venv(engine_dir, runner=runner)
    install_packages(engine_dir, runner=runner, pre_release=pre_release)
    versions = self_check(engine_dir, runner=runner)
    if online_check is not None:
        try:
            online_check(venv_python(new_venv_dir(engine_dir)))
        except SetupError:
            raise
        except Exception as exc:
            raise SetupError(f"the new engine failed online: {exc}") from exc
    promote(engine_dir)
    return versions


# -- token helper (bgutil, script mode) -----------------------------------------

def node_version(runner: Runner | None = None) -> tuple[int, ...] | None:
    run = runner or _default_runner
    try:
        done = run(["node", "--version"])
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return parse_runtime_version((done.stdout or "") + (done.stderr or ""))


def token_status(engine_dir: Path | str,
                 runner: Runner | None = None) -> dict[str, Any]:
    version = node_version(runner)
    return {
        "node": ".".join(str(part) for part in version) if version else "",
        "node_ok": version is not None and version >= NODE_MINIMUM,
        "generator": generator_present(Path(engine_dir)),
    }


def _install_plugin_zip(home: Path, plugins: Path) -> Path | None:
    zips = sorted(home.glob("*.zip"), key=lambda item: item.name)
    if not zips:
        zips = sorted(home.rglob("*.zip"), key=lambda item: item.name)
    if not zips:
        return None
    plugins.mkdir(parents=True, exist_ok=True)
    target = plugins / zips[-1].name
    if target.is_file():
        return target
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    try:
        shutil.copyfile(zips[-1], tmp)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def setup_token_helper(engine_dir: Path | str, *,
                       runner: Runner | None = None,
                       repo: str = BGUTIL_REPO, tag: str = BGUTIL_TAG,
                       ) -> Path:
    """Clone, build and wire the bgutil generator (script mode).

    Builds in ``bgutil.new`` and only replaces ``bgutil`` once the build
    and the plugin install succeed, so a failure leaves the prior helper
    (if any) untouched. Resumes: finished work is skipped.
    """
    run = runner or _default_runner
    version = node_version(run)
    if version is None or version < NODE_MINIMUM:
        floor = ".".join(str(part) for part in NODE_MINIMUM)
        raise SetupError(f"the token helper needs Node {floor} or newer")
    home = bgutil_dir(engine_dir)
    if generator_present(Path(engine_dir)):
        return home
    new = new_bgutil_dir(engine_dir)
    server = new / "server"
    if not (server / "package.json").is_file():
        _rmtree(new)
        done = run(["git", "clone", "--branch", tag, "--depth", "1",
                    repo, str(new)])
        if done.returncode != 0 or not (server / "package.json").is_file():
            _rmtree(new)
            tail = (done.stderr or done.stdout or "").strip()[-300:]
            raise SetupError(f"could not fetch the token helper: {tail}")
    for args in (["npm", "ci"], ["npx", "tsc"]):
        done = run(args, cwd=str(server))
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "").strip()[-300:]
            raise SetupError(
                f"could not build the token helper ({' '.join(args)}):"
                f" {tail}")
    if not (server / "build" / "generate_once.js").is_file():
        raise SetupError("the token helper build left no generator")
    live_python = venv_python(venv_dir(engine_dir))
    if not venv_ok(venv_dir(engine_dir)):
        raise SetupError("set up the playback engine before the helper")
    uv = uv_path(engine_dir)
    done = run([str(uv), "pip", "install", "--python", str(live_python),
                "--upgrade", BGUTIL_PLUGIN])
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip()[-300:]
        raise SetupError(f"could not install the token plugin: {tail}")
    if home.exists():
        _rmtree(home)
    os.rename(new, home)
    _install_plugin_zip(home, plugin_dir(engine_dir))
    return home
