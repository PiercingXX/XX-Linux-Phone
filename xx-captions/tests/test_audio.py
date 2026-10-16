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
        _node(11, "alsa_output.platform-sound.HiFi__hw__0__sink.monitor",
              "Audio/Source"),
        _node(20, "alsa_input.platform-sound.HiFi__hw__0__source",
              "Audio/Source"),
    ]


def test_monitor_name_appends_suffix() -> None:
    assert audio.monitor_name("alsa_output.x") == "alsa_output.x.monitor"


def test_monitor_name_passes_monitors_through() -> None:
    assert audio.monitor_name("alsa_output.x.monitor") == "alsa_output.x.monitor"


def test_pw_record_argv_targets_node() -> None:
    argv = audio.pw_record_argv("alsa_output.x.monitor")
    assert argv[:3] == ["pw-record", "--target", "alsa_output.x.monitor"]
    assert "--rate" in argv and "16000" in argv
    with pytest.raises(ValueError):
        audio.pw_record_argv("  ")


def test_pipewiresrc_requests_capture_role() -> None:
    props = audio.pipewiresrc_caps("alsa_output.x.monitor")
    assert props["target-object"] == "alsa_output.x.monitor"
    assert props["stream.capture.sink"] is True


def test_playback_resolves_to_sink_monitor() -> None:
    plan = audio.resolve_capture(
        _graph(), "alsa_output.platform-sound.HiFi__hw__0__sink",
        in_call=False)
    assert set(plan) == {"playback"}
    assert plan["playback"].node.endswith(".monitor")


def test_call_resolves_downlink_and_uplink() -> None:
    plan = audio.resolve_capture(
        _graph(), "alsa_output.platform-sound.HiFi__hw__0__sink",
        in_call=True)
    assert set(plan) == {"downlink", "uplink"}
    assert plan["downlink"].node.endswith(".monitor")
    assert plan["uplink"].node == "alsa_input.platform-sound.HiFi__hw__0__source"


def test_missing_nodes_yield_empty_plan() -> None:
    assert audio.resolve_capture([], "no-such-sink", in_call=False) == {}
    assert audio.resolve_capture([], "no-such-sink", in_call=True) == {}
    nodes = [_node(1, "some-sink", "Audio/Sink"),
             _node(2, "some-sink.monitor", "Audio/Source",
                   state="suspended")]
    assert audio.resolve_capture(nodes, "some-sink", in_call=False) == {}


def test_default_source_skips_suspended() -> None:
    nodes = [_node(1, "dead-mic", "Audio/Source", state="suspended"),
             _node(2, "live-mic", "Audio/Source")]
    assert audio.default_source(nodes) == "live-mic"
    assert audio.default_source([]) is None
