"""Inbound MMS receive policy (port of ``service/ReceivePolicy``).

Two decisions, both from the Android privacy contract:

- audio-MMS drop: an inbound MMS whose attachment is audio is never
  downloaded and never stored — dropped at the inbox boundary;
- auto-reply gate: the optional auto-reply SMS telling the sender voice
  messages are not accepted is off by default and per-contact
  overridable.

Pure module: no GTK, no D-Bus, headless-testable.
"""

from __future__ import annotations

from enum import Enum

#: Body of the optional auto-reply SMS.
AUTO_REPLY_BODY = "Voice messages aren't accepted. Send text or a photo."


class AttachmentDecision(Enum):
    DROP_UNSTORED = "DROP_UNSTORED"
    STORE = "STORE"


class AutoReplyOverride(Enum):
    ON = "ON"
    OFF = "OFF"
    UNSET = "UNSET"


def decide_attachment(content_type: str | None) -> AttachmentDecision:
    """Store or drop one inbound MMS attachment by MIME type."""
    if content_type is not None and content_type.lower().startswith("audio/"):
        return AttachmentDecision.DROP_UNSTORED
    return AttachmentDecision.STORE


def parse_override(value: object) -> AutoReplyOverride:
    """Lenient override parse; unknown values read as UNSET."""
    if isinstance(value, AutoReplyOverride):
        return value
    try:
        return AutoReplyOverride(str(value).strip().upper())
    except (ValueError, AttributeError):
        return AutoReplyOverride.UNSET


def should_auto_reply(
    enabled: bool,
    sender: str,
    overrides: dict | None = None,
) -> bool:
    """Whether the voice-drop auto-reply SMS goes to ``sender``.

    Off by default: a false ``enabled`` never fires regardless of
    overrides. When on, a per-contact OFF entry suppresses that sender;
    ON/UNSET (or no entry) keep the global-on behaviour.
    """
    if not enabled:
        return False
    if not overrides:
        return True
    for key, value in overrides.items():
        try:
            match = str(key).strip() == (sender or "").strip()
        except Exception:
            continue
        if match:
            return parse_override(value) is not AutoReplyOverride.OFF
    return True
