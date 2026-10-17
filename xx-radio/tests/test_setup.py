"""Engine setup: fake uv/pip/npm/git, resumable steps, untouched priors."""

import hashlib
import io
import os
import stat
import tarfile

import pytest

from xx_radio import setup
from xx_radio.setup import RunResult, SetupError


def _tarball(payload=b"fake-uv-binary"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("uv-9.9/uv")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _fetch_for(data):
    calls = []

    def fetch(url):
        calls.append(url)
        return data

    fetch.calls = calls
    fetch.data = data
    return fetch


class FakeRunner:
    """Records calls; each added rule maps an argv prefix to a behaviour."""

    def __init__(self):
        self.calls = []
        self.rules = []

    def when(self, *prefix, result=None, side=None):
        self.rules.append((tuple(prefix), result, side))
        return self

    def __call__(self, args, *, cwd=None):
        self.calls.append((list(args), cwd))
        for prefix, result, side in self.rules:
            if tuple(args[:len(prefix)]) == prefix:
                if side is not None:
                    side(args, cwd)
                return result or RunResult(tuple(args))
        raise AssertionError(f"unexpected command: {args}")


def _fake_venv(path):
    python = path / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\n")
    os.chmod(python, 0o755)
    (path / "pyvenv.cfg").write_text("home = /usr\n")


def _wire_uv_venv_rules(engine, runner, versions="ytmusicapi 1.12.3\n"
                                                 "yt-dlp 2026.08.19\n"):
    def make_venv(args, cwd):
        _fake_venv(setup.new_venv_dir(engine))

    runner.when(str(setup.uv_path(engine)), "venv", side=make_venv)
    runner.when(str(setup.uv_path(engine)), "pip")
    runner.when(str(setup.venv_python(setup.new_venv_dir(engine))), "-c",
                result=RunResult((), 0, versions, ""))
    return runner


# -- uv ---------------------------------------------------------------------

def test_ensure_uv_downloads_verifies_and_resumes(tmp_path):
    engine = tmp_path / "engine"
    data = _tarball()
    digest = hashlib.sha256(data).hexdigest()
    fetch = _fetch_for(data)
    plat = setup.platform_tag()
    binary = setup.ensure_uv(engine, fetch=fetch,
                             checksums={plat: digest})
    assert binary.read_bytes() == b"fake-uv-binary"
    assert stat.S_IMODE(binary.stat().st_mode) == 0o700
    assert fetch.calls and fetch.calls[0].endswith(f"uv-{plat}.tar.gz")
    setup.ensure_uv(engine, fetch=fetch, checksums={plat: digest})
    assert len(fetch.calls) == 1  # already there: no re-download


def test_ensure_uv_rejects_tampered_downloads(tmp_path):
    engine = tmp_path / "engine"
    fetch = _fetch_for(_tarball(b"evil"))
    with pytest.raises(SetupError):
        setup.ensure_uv(engine, fetch=fetch,
                        checksums={setup.platform_tag(): "0" * 64})
    with pytest.raises(SetupError):
        setup.ensure_uv(engine, fetch=fetch,
                        checksums={setup.platform_tag(): "f" * 64})
    assert not setup.uv_path(engine).exists()


def test_ensure_uv_refuses_without_a_checksum(tmp_path):
    engine = tmp_path / "engine"
    fetch = _fetch_for(_tarball())
    with pytest.raises(SetupError):
        setup.ensure_uv(engine, fetch=fetch, checksums={})
    assert fetch.calls == []


# -- venv lifecycle ------------------------------------------------------------

def _checksums(data):
    return {setup.platform_tag(): hashlib.sha256(data).hexdigest()}


def _update(engine, runner, fetch, **kwargs):
    return setup.update_engine(engine, runner=runner, fetch=fetch,
                               uv_checksums=_checksums(fetch.data),
                               **kwargs)


def test_update_promotes_and_keeps_versions(tmp_path):
    engine = tmp_path / "engine"
    runner = FakeRunner()
    _wire_uv_venv_rules(engine, runner)
    fetch = _fetch_for(_tarball())
    versions = _update(engine, runner, fetch)
    assert versions == {"ytmusicapi": "1.12.3", "yt-dlp": "2026.08.19"}
    assert setup.venv_ok(setup.venv_dir(engine))
    assert not setup.new_venv_dir(engine).exists()
    pip_calls = [args for args, _ in runner.calls
                 if args[1:3] == ["pip", "install"]]
    assert pip_calls and "ytmusicapi" in pip_calls[0] \
        and "yt-dlp" in pip_calls[0]
    assert "--system-site-packages" in [
        arg for args, _ in runner.calls for arg in args]


def test_failed_install_leaves_live_venv_untouched(tmp_path):
    engine = tmp_path / "engine"
    _fake_venv(engine / "venv")
    (engine / "venv" / "marker.txt").write_text("live")
    runner = FakeRunner()
    _wire_uv_venv_rules(engine, runner)
    runner.rules = [rule for rule in runner.rules
                    if rule[0][:2] != (str(setup.uv_path(engine)), "pip")]
    runner.when(str(setup.uv_path(engine)), "pip",
                result=RunResult((), 1, "", "index unreachable"))
    with pytest.raises(SetupError):
        _update(engine, runner, _fetch_for(_tarball()))
    assert (engine / "venv" / "marker.txt").read_text() == "live"
    assert not setup.prev_venv_dir(engine).exists()


def test_failed_self_check_leaves_live_venv_untouched(tmp_path):
    engine = tmp_path / "engine"
    _fake_venv(engine / "venv")
    runner = FakeRunner()
    _wire_uv_venv_rules(engine, runner, versions="nothing useful\n")
    with pytest.raises(SetupError):
        _update(engine, runner, _fetch_for(_tarball()))
    assert setup.venv_ok(setup.venv_dir(engine))
    assert not setup.prev_venv_dir(engine).exists()


def test_online_check_failure_blocks_promote(tmp_path):
    engine = tmp_path / "engine"
    runner = FakeRunner()
    _wire_uv_venv_rules(engine, runner)

    def offline(python):
        raise SetupError("no route")

    with pytest.raises(SetupError):
        _update(engine, runner, _fetch_for(_tarball()),
                online_check=offline)
    assert not setup.venv_dir(engine).exists()
    assert setup.new_venv_dir(engine).exists()  # kept for the retry


def test_promote_undo_and_drop(tmp_path):
    engine = tmp_path / "engine"
    with pytest.raises(SetupError):
        setup.promote(engine)
    with pytest.raises(SetupError):
        setup.undo(engine)
    _fake_venv(engine / "venv")
    _fake_venv(engine / "venv.new")
    setup.promote(engine)
    assert setup.venv_ok(engine / "venv")
    assert setup.prev_venv_dir(engine).is_dir()
    assert not setup.new_venv_dir(engine).exists()
    setup.undo(engine)
    assert setup.venv_ok(engine / "venv")
    setup.drop_to_system(engine)
    assert not setup.venv_dir(engine).exists()
    assert not setup.prev_venv_dir(engine).exists()


# -- token helper ---------------------------------------------------------------

NODE_24 = RunResult(("node", "--version"), 0, "v24.15.0\n", "")


def _wire_token_rules(engine, runner):
    def clone(args, cwd):
        server = setup.new_bgutil_dir(engine) / "server"
        server.mkdir(parents=True, exist_ok=True)
        (server / "package.json").write_text("{}")

    def build(args, cwd):
        out = setup.new_bgutil_dir(engine) / "server" / "build"
        out.mkdir(parents=True, exist_ok=True)
        (out / "generate_once.js").write_text("// generator")

    runner.when("node", "--version", result=NODE_24)
    runner.when("git", "clone", side=clone)
    runner.when("npm", "ci")
    runner.when("npx", "tsc", side=build)
    runner.when(str(setup.uv_path(engine)), "pip")
    return runner


def test_token_helper_runs_each_step_once_then_resumes(tmp_path):
    engine = tmp_path / "engine"
    _fake_venv(engine / "venv")
    (engine / "bin").mkdir(parents=True)
    setup.uv_path(engine).write_text("uv")
    os.chmod(setup.uv_path(engine), 0o700)
    runner = FakeRunner()
    _wire_token_rules(engine, runner)
    home = setup.setup_token_helper(engine, runner=runner)
    assert setup.generator_present(engine)
    sequence = [args[0] for args, _ in runner.calls]
    assert sequence == ["node", "git", "npm", "npx",
                        str(setup.uv_path(engine))]
    clone = [args for args, _ in runner.calls if args[0] == "git"][0]
    assert setup.BGUTIL_TAG in clone and clone[-1] == str(
        setup.new_bgutil_dir(engine))
    npm = [call for call in runner.calls if call[0][0] == "npm"][0]
    assert npm[1].endswith("bgutil.new/server")
    assert not setup.new_bgutil_dir(engine).exists()
    assert home == setup.bgutil_dir(engine)
    runner.calls.clear()
    setup.setup_token_helper(engine, runner=runner)
    assert [args[0] for args, _ in runner.calls] == ["node"]


def test_token_helper_needs_node_22(tmp_path):
    engine = tmp_path / "engine"
    runner = FakeRunner()
    runner.when("node", "--version",
                result=RunResult(("node",), 0, "v20.19.2\n", ""))
    runner.when("git", "clone")
    with pytest.raises(SetupError):
        setup.setup_token_helper(engine, runner=runner)
    assert [args[0] for args, _ in runner.calls] == ["node"]
    assert not setup.bgutil_dir(engine).exists()


def test_token_helper_failure_keeps_prior_helper(tmp_path):
    engine = tmp_path / "engine"
    _fake_venv(engine / "venv")
    (engine / "bin").mkdir(parents=True)
    setup.uv_path(engine).write_text("uv")
    os.chmod(setup.uv_path(engine), 0o700)
    old = setup.bgutil_dir(engine) / "server"
    old.mkdir(parents=True)
    (old / "package.json").write_text("{}")
    (setup.bgutil_dir(engine) / "sentinel.txt").write_text("prior")
    runner = FakeRunner()
    _wire_token_rules(engine, runner)
    runner.rules = [rule for rule in runner.rules
                    if rule[0][:1] != ("npm",)]
    runner.when("npm", "ci", result=RunResult(("npm",), 1, "", "no network"))
    with pytest.raises(SetupError):
        setup.setup_token_helper(engine, runner=runner,
                                 repo="https://example.invalid/x.git")
    assert (setup.bgutil_dir(engine) / "sentinel.txt").read_text() == "prior"
    assert not (setup.bgutil_dir(engine) / "server" / "build").exists()
    assert setup.new_bgutil_dir(engine).exists()  # kept for the retry


def test_setup_never_escalates_or_shells_out():
    text = (setup.__file__ and open(setup.__file__).read()) or ""
    for banned in ("sudo", "curl", "wget",
                   "--break-system-packages", "shell=True",
                   "EXTERNALLY-MANAGED"):
        assert banned not in text
    worker_text = open(str(setup.__file__).replace("setup.py",
                                                   "youtube/resolver.py")).read()
    assert "shell=True" not in worker_text
