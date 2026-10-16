"""L3 fix-up tests: pointer row targeting and atomic tag updates."""

import threading
from pathlib import Path

import pytest

from xx_contacts.list_ui import resolve_row_uid
from xx_contacts.store import (
    ContactData,
    ContactStore,
    NotFoundError,
    PhoneEntry,
)

SRC = Path(__file__).parent.parent / "src" / "xx_contacts"


class _Fake:
    def __init__(self, parent=None, **attrs):
        self._parent = parent
        for key, value in attrs.items():
            setattr(self, key, value)

    def get_parent(self):
        return self._parent


def _contact(name="Race Case"):
    return ContactData(
        display_name=name,
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100")],
    )


def test_resolve_row_uid_walks_up_to_row_box():
    row_box = _Fake(parent=None, _xx_uid="uid-1")
    label = _Fake(parent=row_box)
    assert resolve_row_uid(label) == "uid-1"


def test_resolve_row_uid_supports_uid_attribute():
    row_box = _Fake(parent=None)
    row_box.uid = "uid-2"
    label = _Fake(parent=row_box)
    assert resolve_row_uid(label) == "uid-2"


def test_resolve_row_uid_skips_empty_and_keeps_walking():
    outer = _Fake(parent=None, _xx_uid="outer-uid")
    inner = _Fake(parent=outer, _xx_uid="")
    leaf = _Fake(parent=inner)
    assert resolve_row_uid(leaf) == "outer-uid"


def test_resolve_row_uid_returns_none_without_uid():
    top = _Fake(parent=None)
    leaf = _Fake(parent=top)
    assert resolve_row_uid(leaf) is None
    assert resolve_row_uid(None) is None


def test_list_resolves_row_under_pointer():
    src = (SRC / "list_ui.py").read_text(encoding="utf-8")
    assert "self._list.pick(" in src
    assert "Gtk.PickFlags.DEFAULT" in src
    assert "get_parent()" in src
    assert "GestureLongPress" in src
    assert "get_selected_item" not in src
    assert "_on_item_unbind" in src
    assert "update_tags" in src


def test_update_tags_add_remove_semantics():
    store = ContactStore(":memory:")
    try:
        uid = store.create(_contact())
        before = store.get_revision()
        store.update_tags(uid, add=("starred", "business"))
        assert store.get_tags(uid) == ["starred", "business"]
        assert store.get_revision() == before + 1
        store.update_tags(uid, add=("blocked",), remove=("starred",))
        assert store.get_tags(uid) == ["business", "blocked"]
        store.update_tags(uid, remove=("business", "blocked"))
        assert store.get_tags(uid) == []
    finally:
        store.close()


def test_update_tags_ignores_unknown_tags():
    store = ContactStore(":memory:")
    try:
        uid = store.create(_contact())
        store.update_tags(uid, add=("starred", "bogus"))
        assert store.get_tags(uid) == ["starred"]
        store.update_tags(uid, remove=("bogus",))
        assert store.get_tags(uid) == ["starred"]
    finally:
        store.close()


def test_update_tags_unknown_uid_raises():
    store = ContactStore(":memory:")
    try:
        with pytest.raises(NotFoundError):
            store.update_tags("00000000-0000-4000-8000-000000000000",
                              add=("starred",))
    finally:
        store.close()


def test_update_tags_concurrent_different_tags_both_survive():
    store = ContactStore(":memory:")
    try:
        for round_no in range(10):
            uid = store.create(_contact(f"Race {round_no}"))
            barrier = threading.Barrier(2)
            errors: list = []

            def add_starred():
                try:
                    barrier.wait(timeout=5)
                    store.update_tags(uid, add=("starred",))
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            def add_blocked():
                try:
                    barrier.wait(timeout=5)
                    store.update_tags(uid, add=("blocked",))
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            first = threading.Thread(target=add_starred)
            second = threading.Thread(target=add_blocked)
            first.start()
            second.start()
            first.join(timeout=10)
            second.join(timeout=10)
            assert not errors
            assert sorted(store.get_tags(uid)) == ["blocked", "starred"]
    finally:
        store.close()
