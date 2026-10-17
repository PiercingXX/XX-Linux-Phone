"""PipeWire capture target resolution (pure helpers, no PipeWire calls).

Live captions capture the default sink's monitor without touching
routing; call transcription captures the voice-call sink monitor
(downlink) plus the default source (uplink). The two graphs stay
read-only: capture streams never move or re-link existing nodes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

MONITOR_SUFFIX = ".monitor"
ENGINE_RATE_HZ = 16000


@dataclass(frozen=True)
class CaptureTarget:
    node: str
    label: str


def pw_record_argv(target: str, rate: int = ENGINE_RATE_HZ,
                   properties: dict | None = None) -> list[str]:
    """pw-record argv capturing a sink's monitor at engine sample rate.

    PipeWire exposes a sink's monitor as ports on the sink node itself,
    not as a separate `<sink>.monitor` node (that name exists only in
    the PulseAudio view), so the target is the sink node and
    `stream.capture.sink` selects its monitor. Verified on PipeWire
    1.6.9: targeting `<sink>.monitor` fails with "no target node
    available", while sink + capture.sink streams.
    """
    if not target or not target.strip():
        raise ValueError("capture target must be a non-empty node name")
    props = {"stream.capture.sink": True}
    if properties:
        props.update(properties)
    return ["pw-record", "--target", target.strip(),
            "-P", json.dumps(props),
            "--rate", str(int(rate)), "--format", "s16",
            "--channels", "1", "-"]


def pipewiresrc_caps(target: str, rate: int = ENGINE_RATE_HZ) -> dict:
    """GStreamer pipewiresrc properties for in-process capture."""
    if not target or not target.strip():
        raise ValueError("capture target must be a non-empty node name")
    return {
        "target-object": target.strip(),
        "stream.capture.sink": True,
        "audio-rate": int(rate),
        "audio-format": "S16LE",
        "audio-channels": 1,
    }


def find_node_by_name(nodes: list[dict], name: str) -> dict | None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        info = node.get("info")
        props = info.get("props") if isinstance(info, dict) else None
        if isinstance(props, dict) and props.get("node.name") == name:
            return node
    return None


def find_monitor_for_sink(nodes: list[dict], sink: str) -> str | None:
    """Sink node name for monitor capture, or None when the graph has
    none. Returns the sink's own name: capture streams target it with
    `stream.capture.sink` (see pw_record_argv).

    Only `running` Audio/Sink nodes count, matching the shell privacy
    monitor's classification of live streams.
    """
    if not sink or not sink.strip():
        return None
    node = find_node_by_name(nodes, sink.strip())
    if node is None:
        return None
    info = node.get("info")
    if not isinstance(info, dict):
        return None
    if str(info.get("state", "")).lower() != "running":
        return None
    props = info.get("props")
    if not isinstance(props, dict):
        return None
    if props.get("media.class") != "Audio/Sink":
        return None
    return sink.strip()


def default_source(nodes: list[dict]) -> str | None:
    """First running Audio/Source node name (the mic), if any.

    Sink monitors also report Audio/Source class, so `.monitor` nodes
    are never eligible: a monitor must never be picked as the mic.
    """
    for node in nodes:
        if not isinstance(node, dict):
            continue
        info = node.get("info")
        if not isinstance(info, dict):
            continue
        if str(info.get("state", "")).lower() != "running":
            continue
        props = info.get("props")
        if not isinstance(props, dict):
            continue
        if props.get("media.class") == "Audio/Source":
            name = props.get("node.name")
            if (isinstance(name, str) and name
                    and not name.endswith(MONITOR_SUFFIX)):
                return name
    return None


def resolve_capture(nodes: list[dict], default_sink: str,
                    in_call: bool) -> dict[str, CaptureTarget]:
    """Pure capture plan: playback monitor, or downlink+uplink in a call.

    Returns {} when the graph cannot supply the needed node(s); the
    caller keeps the previous state instead of starting half a session.
    """
    if in_call:
        downlink = find_monitor_for_sink(nodes, default_sink)
        uplink = default_source(nodes)
        if not downlink or not uplink:
            return {}
        return {
            "downlink": CaptureTarget(downlink, "far end"),
            "uplink": CaptureTarget(uplink, "mic"),
        }
    monitor = find_monitor_for_sink(nodes, default_sink)
    if not monitor:
        return {}
    return {"playback": CaptureTarget(monitor, "system playback")}
