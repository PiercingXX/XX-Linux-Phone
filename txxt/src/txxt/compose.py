"""Compose send gate and outgoing validation.

Ports ``ui/SendGate.kt`` (one send at a time: the decision lives in
state, not in the button) plus the small pure slice of the send path
the thread page needs before it touches D-Bus: empty address/body is
rejected locally so a blank tap never reaches the daemon.

No GTK/D-Bus imports: headless-testable.
"""

from __future__ import annotations


class SendGate:
    """One send at a time.

    ``begin`` admits the first caller and turns every later one away
    until ``end``. ``end`` belongs in a ``finally`` so a failed send
    never wedges the compose bar shut.
    """

    def __init__(self) -> None:
        self._in_flight = False

    @property
    def is_sending(self) -> bool:
        return self._in_flight

    def begin(self) -> bool:
        if self._in_flight:
            return False
        self._in_flight = True
        return True

    def end(self) -> None:
        self._in_flight = False


def validate_outgoing(address: str, body: str) -> str | None:
    """Error text for an unsendable compose, None when it may be sent."""
    if not (address or "").strip():
        return "Enter a recipient first."
    if not (body or "").strip():
        return "Type a message first."
    return None


def validate_mms_outgoing(
    address: str, body: str, file_paths=None,
) -> str | None:
    """Error text for an unsendable MMS compose, None when it may send.

    A photo MMS may carry an empty caption (the thread shows ``[Photo]``);
    a text-only MMS still needs words. An unscrubbable photo is refused
    at the daemon, never here — this gate only checks presence.
    """
    if not (address or "").strip():
        return "Enter a recipient first."
    if (body or "").strip():
        return None
    paths = [p for p in (file_paths or []) if str(p).strip()]
    if paths:
        return None
    return "Attach a photo or type a message first."
