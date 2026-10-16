"""Contact display-name tests: xx-contacts first, header fallback.

Covers review note 1 — recipient/sender names come from xx-contacts'
D-Bus Search, fall back to the raw header name when it is absent.
"""

from xx_email import contacts
from xx_email.contacts import _first_name, resolve_sender_display


def test_contacts_hit_wins_over_header():
    assert resolve_sender_display("Jane Doe <jane@x>", "Janey") == "Janey"


def test_blank_contact_falls_back_to_header_name():
    assert resolve_sender_display(
        "Jane Doe <jane@x>", "  ") == "Jane Doe"
    assert resolve_sender_display(
        "Jane Doe <jane@x>", None) == "Jane Doe"


def test_bare_address_is_last_resort():
    assert resolve_sender_display("jane@x", None) == "jane@x"
    assert resolve_sender_display("", None) == "?"


def test_first_name_reads_contact_rows():
    assert _first_name([{"name": " Ada "}]) == "Ada"
    assert _first_name([{"display_name": "Bo"}]) == "Bo"
    assert _first_name([]) is None
    assert _first_name("oops-not-a-list") is None


def test_search_without_app_calls_back_none(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "gi", None)
    seen = []
    contacts.search_contacts_async("jane@x", seen.append)
    assert seen == [None]
    assert "gi" not in sys.modules or sys.modules["gi"] is None


def test_eds_lookup_needs_an_address():
    assert contacts.lookup_eds_display_name("") is None
    assert contacts.lookup_eds_display_name("not-an-address") is None
