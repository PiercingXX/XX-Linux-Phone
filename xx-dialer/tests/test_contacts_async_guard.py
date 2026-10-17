"""Missing contacts service must never delay the UI.

Every contacts_client D-Bus call is async with a short timeout, so
ServiceUnknown or a timeout surfaces through the error callback
instead of blocking the main loop — including on the finding-27
always-call path taken while the name is unowned.
"""
from pathlib import Path

from xx_dialer import contacts_client as client


def test_contacts_client_never_blocks_on_the_bus():
    text = Path(client.__file__).read_text(encoding="utf-8")
    assert "call_sync" not in text


def test_contacts_call_timeout_is_short():
    assert 0 < client._CALL_TIMEOUT_MS <= 5000
