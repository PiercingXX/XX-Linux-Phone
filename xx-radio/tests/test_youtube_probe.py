"""Engine piece detection: versions, runtimes and the resulting states."""

import datetime as dt
import os

from xx_music.engine import ComponentState
from xx_radio.youtube import probe
from xx_radio.youtube.engine import YouTubeMusicEngine
from xx_radio.youtube.probe import Installed, Runtime

TODAY = dt.date(2026, 10, 17)


def test_runtime_version_parsing():
    assert probe.parse_runtime_version("v24.18.1\n") == (24, 18, 1)
    assert probe.parse_runtime_version(
        "deno 2.7.4 (stable, release, x86_64-unknown-linux-gnu)\n"
        "v8 14.0\ntypescript 5.9") == (2, 7, 4)
    assert probe.parse_runtime_version(
        "QuickJS version 2025-09-13\nusage: qjs") == (2025, 9, 13)
    assert probe.parse_runtime_version("") is None
    assert probe.parse_runtime_version("garbage") is None


def test_runtime_floors():
    assert Runtime("node", "/n", (22, 0, 0)).usable
    assert not Runtime("node", "/n", (20, 19, 2)).usable
    assert Runtime("deno", "/d", (2, 0, 0)).usable
    assert not Runtime("deno", "/d", (1, 46, 3)).usable
    assert Runtime("qjs", "/q", (2025, 4, 26)).usable
    assert not Runtime("node", "/n", None).usable
    assert Runtime("qjs", "/q", (2025, 4, 26)).version_text == "2025-4-26"


def test_find_runtimes_in_order_with_private_bin_first(tmp_path):
    private = tmp_path / "bin"
    private.mkdir()
    node = private / "node"
    node.write_text("#!/bin/sh\n")
    os.chmod(node, 0o755)
    outputs = {str(node): "v26.1.0", "/usr/bin/deno": "deno 2.7.4",
               "/usr/bin/qjs": "QuickJS version 2025-09-13"}
    which = {"deno": "/usr/bin/deno", "node": "/usr/bin/node",
             "qjs": "/usr/bin/qjs"}.get
    found = probe.find_runtimes(which=which, run=outputs.get,
                                extra_dirs=(private,))
    assert [(r.name, r.path) for r in found] == [
        ("deno", "/usr/bin/deno"), ("node", str(node)),
        ("qjs", "/usr/bin/qjs")]
    assert all(r.usable for r in found)


def test_resolver_age():
    assert probe.resolver_age_days("2026.08.19", TODAY) == 59
    assert probe.resolver_age_days("2026.10.01.234512", TODAY) == 16
    assert probe.resolver_age_days("not-a-date", TODAY) is None
    assert probe.resolver_age_days("2026.13.40", TODAY) is None


def _full(**overrides):
    base = dict(catalog="1.12.1", resolver="2026.08.19", ejs="0.8.0",
                token_plugin="2.0.0", token_generator=True,
                runtimes=(Runtime("deno", "/usr/bin/deno", (2, 7, 4)),))
    base.update(overrides)
    return Installed(**base)


def _states(found):
    return {c.key: c.state for c in probe.components(found, TODAY)}


def test_everything_present_can_browse_and_play():
    status = probe.status_from(_full(), TODAY)
    assert status.can_browse and status.can_play
    assert set(_states(_full()).values()) == {ComponentState.OK}
    names = [c.name for c in status.components]
    assert names == ["Catalog", "Stream resolver", "Challenge solver",
                     "Token helper"]


def test_missing_token_helper_still_browses_but_cannot_play():
    found = _full(token_generator=False)
    status = probe.status_from(found, TODAY)
    assert status.can_browse and not status.can_play
    assert _states(found)["token"] == ComponentState.MISSING


def test_quickjs_solves_challenges_but_cannot_mint_tokens():
    found = _full(runtimes=(Runtime("qjs", "/usr/bin/qjs", (2025, 9, 13)),))
    states = _states(found)
    assert states["solver"] == ComponentState.OK
    assert states["token"] == ComponentState.MISSING


def test_old_node_is_outdated_not_missing():
    found = _full(runtimes=(Runtime("node", "/usr/bin/node", (20, 19, 2)),))
    states = _states(found)
    assert states["solver"] == ComponentState.OUTDATED
    assert states["token"] == ComponentState.MISSING


def test_old_resolver_is_flagged_but_still_tried():
    found = _full(resolver="2026.05.01")
    assert _states(found)["resolver"] == ComponentState.OUTDATED
    assert probe.status_from(found, TODAY).can_play


def test_nothing_installed():
    status = probe.status_from(Installed(), TODAY)
    assert not status.can_browse and not status.can_play
    assert all(c.state == ComponentState.MISSING for c in status.components)


def test_generator_detection(tmp_path):
    assert not probe.generator_present(tmp_path)
    build = tmp_path / "bgutil" / "server" / "build"
    build.mkdir(parents=True)
    (build / "generate_once.js").write_text("")
    assert probe.generator_present(tmp_path)


def test_engine_status_runs_offline(tmp_path):
    status = YouTubeMusicEngine(tmp_path).status()
    assert [c.key for c in status.components] == [
        "catalog", "resolver", "solver", "token"]
    assert not status.can_play  # no generator in an empty engine dir
