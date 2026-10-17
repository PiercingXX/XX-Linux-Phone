from __future__ import annotations

import pytest

from xx_captions import audio


def _node(node_id: int, name: str, media_class: str,
           state: str = "running") -> dict:
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {
            "state": state,
            "props": {"node.name": name, "media.class": media_class},
        },
    }


def _graph() -> list[dict]:
    return [
        _node(10, "alsa_output.platform-sound.HiFi__hw__0__sink",
              "Audio/Sink"),
        _node(20, "alsa_input.platform-sound.HiFi__hw__0__source",
              "Audio/Source"),
    ]


def test_pw_record_argv_targets_sink_with_capture_sink() -> None:
    import json

    argv = audio.pw_record_argv("alsa_output.x")
    assert argv[:3] == ["pw-record", "--target", "alsa_output.x"]
    assert "--rate" in argv and "16000" in argv
    assert argv[-1] == "-"
    props = json.loads(argv[argv.index("-P") + 1])
    assert props["stream.capture.sink"] is True
    with pytest.raises(ValueError):
        audio.pw_record_argv("  ")


def test_pipewiresrc_requests_capture_role() -> None:
    props = audio.pipewiresrc_caps("alsa_output.x")
    assert props["target-object"] == "alsa_output.x"
    assert props["stream.capture.sink"] is True


def test_playback_resolves_to_sink_itself() -> None:
    plan = audio.resolve_capture(
        _graph(), "alsa_output.platform-sound.HiFi__hw__0__sink",
        in_call=False)
    assert set(plan) == {"playback"}
    assert plan["playback"].node == \
        "alsa_output.platform-sound.HiFi__hw__0__sink"


def test_call_resolves_downlink_and_uplink() -> None:
    plan = audio.resolve_capture(
        _graph(), "alsa_output.platform-sound.HiFi__hw__0__sink",
        in_call=True)
    assert set(plan) == {"downlink", "uplink"}
    assert plan["downlink"].node == \
        "alsa_output.platform-sound.HiFi__hw__0__sink"
    assert plan["uplink"].node == "alsa_input.platform-sound.HiFi__hw__0__source"


def test_no_plan_names_a_dot_monitor_node() -> None:
    nodes = _graph() + [_node(
        30, "alsa_output.platform-sound.HiFi__hw__0__sink.monitor",
        "Audio/Source")]
    for in_call in (False, True):
        plan = audio.resolve_capture(
            nodes, "alsa_output.platform-sound.HiFi__hw__0__sink",
            in_call=in_call)
        assert all(not target.node.endswith(".monitor")
                   for target in plan.values())


def test_missing_nodes_yield_empty_plan() -> None:
    assert audio.resolve_capture([], "no-such-sink", in_call=False) == {}
    assert audio.resolve_capture([], "no-such-sink", in_call=True) == {}
    nodes = [_node(1, "some-sink", "Audio/Sink", state="suspended")]
    assert audio.resolve_capture(nodes, "some-sink", in_call=False) == {}
    nodes = [_node(1, "some-source", "Audio/Source")]
    assert audio.resolve_capture(nodes, "some-source", in_call=False) == {}


def test_default_source_skips_suspended() -> None:
    nodes = [_node(1, "dead-mic", "Audio/Source", state="suspended"),
             _node(2, "live-mic", "Audio/Source")]
    assert audio.default_source(nodes) == "live-mic"
    assert audio.default_source([]) is None
