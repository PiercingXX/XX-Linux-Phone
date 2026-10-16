"""L2 list-model tests: sections, jump strip, debounce, filters, URIs."""

from xx_contacts.list_model import (
    JUMP_MIN_TOUCH_PX,
    SEARCH_DEBOUNCE_MS,
    ListFilter,
    ListRow,
    SearchDebouncer,
    action_uri,
    apply_filters,
    build_sections,
    call_uri,
    email_uri,
    fetch_rows,
    flatten_sections,
    jump_labels,
    message_uri,
    plan_refresh,
    record_to_row,
    row_menu,
    section_key,
)
from xx_contacts.store import ContactData, ContactStore, EmailEntry, PhoneEntry


def _row(name, **kwargs):
    kwargs.setdefault("uid", name.lower().replace(" ", "-"))
    return ListRow(display_name=name, **kwargs)


def test_section_key_buckets():
    assert section_key("Ada") == "A"
    assert section_key("  grace") == "G"
    assert section_key("Émile") == "É"
    assert section_key("123 Corp") == "#"
    assert section_key("") == "#"
    assert section_key(None) == "#"


def test_starred_section_comes_first_then_letters():
    rows = [_row("Zed"), _row("Ada", starred=True),
            _row("bob"), _row("1st Bank"), _row("Amy", starred=True)]
    sections = build_sections(rows)
    assert [s.title for s in sections] == ["Starred", "B", "Z", "#"]
    assert [r.display_name for r in sections[0].rows] == ["Ada", "Amy"]
    assert [s.key for s in sections] == ["starred", "B", "Z", "#"]


def test_jump_strip_labels_and_touch_target():
    assert JUMP_MIN_TOUCH_PX >= 40
    sections = build_sections([_row("Zed"), _row("Ada", starred=True),
                               _row("bob")])
    assert jump_labels(sections) == ["*", "B", "Z"]


def test_flatten_sections_interleaves_headers():
    sections = build_sections([_row("Ada", starred=True), _row("bob")])
    flat = flatten_sections(sections)
    assert [getattr(item, "title", None) for item in flat[:1]] == ["Starred"]
    assert flat[1].display_name == "Ada"
    assert getattr(flat[2], "title", None) == "B"


def test_debounce_interval_and_release():
    assert SEARCH_DEBOUNCE_MS == 150
    debouncer = SearchDebouncer()
    debouncer.edit("a", 1000)
    assert debouncer.due(1000 + SEARCH_DEBOUNCE_MS - 1) is None
    assert debouncer.due(1000 + SEARCH_DEBOUNCE_MS) == "a"
    assert debouncer.due(1000 + SEARCH_DEBOUNCE_MS + 50) is None
    debouncer.edit("ad", 2000)
    assert debouncer.has_unsent
    assert debouncer.due(2000 + SEARCH_DEBOUNCE_MS) == "ad"


def test_debounce_typing_resets_the_clock():
    debouncer = SearchDebouncer()
    debouncer.edit("a", 0)
    debouncer.edit("ad", 100)
    assert debouncer.due(149) is None
    assert debouncer.due(250) == "ad"


def test_filters_hide_blocked_without_query_and_has_phone():
    blocked = _row("Mallory", blocked=True)
    plain = _row("Ada")
    assert blocked not in apply_filters([blocked, plain], None)
    assert blocked in apply_filters([blocked, plain], "mal")
    assert blocked in apply_filters([blocked, plain], "  mal  ")
    no_phone = _row("No Phone")
    with_phone = _row("Has Phone", has_phone=True,
                      primary_number="+1 555 0100")
    only = ListFilter(has_phone_only=True)
    assert apply_filters([no_phone, with_phone], None, only) == [with_phone]


def test_row_uris():
    assert call_uri("+1 555 0100") == "tel:+15550100"
    assert message_uri("+1 555 0100") == "sms:+15550100"
    assert email_uri("Ada@Example.ORG ") == "mailto:Ada@Example.ORG"
    assert call_uri("") == "" and message_uri("  ") == ""
    assert email_uri("") == ""


def test_row_menu_depends_on_contact_data():
    full = _row("Ada", primary_number="+1 555 0100",
                primary_email="ada@example.org")
    assert row_menu(full) == ["call", "message", "email", "star", "block",
                              "share", "delete"]
    bare = _row("Nope")
    assert row_menu(bare) == ["star", "block", "share", "delete"]
    starred_menu = row_menu(_row("S", starred=True, primary_number="1"))
    assert "unstar" in starred_menu and "star" not in starred_menu
    blocked_menu = row_menu(_row("B", blocked=True))
    assert "unblock" in blocked_menu and "block" not in blocked_menu
    assert action_uri("call", full) == "tel:+15550100"
    assert action_uri("message", full) == "sms:+15550100"
    assert action_uri("email", full) == "mailto:ada@example.org"
    assert action_uri("star", full) == ""


def test_plan_refresh_ignores_stale_notices():
    assert plan_refresh(5, 5, ["u1"], ["u1"]).refresh is False
    assert plan_refresh(6, 4, ["u1"], ["u1"]).refresh is False
    plan = plan_refresh(4, 5, ["u1", "u9"], ["u1", "u2"])
    assert plan.refresh is True
    assert plan.interesting == ("u1",)
    assert plan_refresh(0, 1, ["new"], []).refresh is True


def _seeded_store(path):
    store = ContactStore(path)
    store.create(ContactData(
        display_name="Ada Lovelace",
        phones=[PhoneEntry(number_raw="+1 555 0100",
                            number_digits="15550100")],
        emails=[EmailEntry(address="ada@example.org")],
        starred=True))
    store.create(ContactData(display_name="Grace Hopper",
                             phones=[PhoneEntry(number_raw="+1 555 0202",
                                                 number_digits="15550202")]))
    blocked = ContactData(display_name="Mallory",
                          phones=[PhoneEntry(number_raw="+1 555 0303",
                                              number_digits="15550303")],
                          blocked=True)
    store.create(blocked)
    store.create(ContactData(display_name="No Phone"))
    return store


def test_fetch_rows_lists_filters_and_searches(tmp_path):
    store = _seeded_store(str(tmp_path / "contacts.db"))
    try:
        listed = fetch_rows(store, "")
        assert sorted(r.display_name for r in listed) == ["Ada Lovelace",
                                                          "Grace Hopper",
                                                          "No Phone"]
        ada = next(r for r in listed if r.display_name == "Ada Lovelace")
        assert (ada.starred, ada.has_phone,
                ada.primary_number) == (True, True, "+1 555 0100")
        hits = fetch_rows(store, "0202")
        assert [r.display_name for r in hits] == ["Grace Hopper"]
        assert [r.display_name
                for r in fetch_rows(store, "mallory")] == ["Mallory"]
        phone_only = fetch_rows(store, "", list_filter=ListFilter(True))
        assert sorted(r.display_name
                      for r in phone_only) == ["Ada Lovelace", "Grace Hopper"]
        sections = build_sections(listed)
        assert sections[0].title == "Starred"
        assert jump_labels(sections) == ["*", "G", "N"]
    finally:
        store.close()


def test_record_to_row_falls_back_without_a_name(tmp_path):
    store = ContactStore(str(tmp_path / "contacts.db"))
    try:
        uid = store.create(ContactData(
            phones=[PhoneEntry(number_raw="+1 555 0100",
                                number_digits="15550100")]))
        row = record_to_row(store.get(uid))
        assert row.display_name == "+1 555 0100"
        assert row.has_phone is True
    finally:
        store.close()
