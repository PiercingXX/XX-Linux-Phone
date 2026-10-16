"""PipeWire capture target resolution (pure helpers, no PipeWire calls).

Live captions capture the default sink's monitor without touching
routing; call transcription captures the voice-call sink monitor
(downlink) plus the default source (uplink). The two graphs stay
read-only: capture streams never move or re-link existing nodes.
"""
from __future__ import annotations

from dataclasses import dataclass

MONITOR_SUFFIX = ".monitor"
ENGINE_RATE_HZ = 16000


@dataclass(frozen=True)
class CaptureTarget:
    node: str
    label: str


def monitor_name(sink: str) -> str:
    """Monitor node for a sink; already-a-monitor names pass through."""
    sink = (sink or "").strip()
    if sink.endswith(MONITOR_SUFFIX):
        return sink
    return sink + MONITOR_SUFFIX if sink else ""


def pw_record_argv(target: str, rate: int = ENGINE_RATE_HZ) -> list[str]:
    """pw-record argv capturing exactly one node at engine sample rate."""
    if not target or not target.strip():
        raise ValueError("capture target must be a non-empty node name")
    return ["pw-record", "--target", target.strip(), "--rate",
            str(int(rate)), "--format", "s16", "--channels", "1", "-"]


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
    """Monitor node name for a sink, or None when the graph has none.

    Only `running` nodes count, matching the shell privacy monitor's
    classification of live streams.
    """
    if not sink:
        return None
    node = find_node_by_name(nodes, monitor_name(sink))
    if node is None:
        return None
    info = node.get("info")
    if not isinstance(info, dict):
        return None
    if str(info.get("state", "")).lower() != "running":
        return None
    return monitor_name(sink)


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
