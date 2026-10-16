"""Ported JVM unit tests for the pure domain layer.

Ports DomainLogicTest, FtsEscaperTest, LabelCsvTest, RecipientsTest,
SafePathsTest, RemoteImagePolicyTest, NewMailDetectorTest,
OutboxFilesTest, OutboxSendTest, SendCancelPolicyTest,
SendRetryPolicyTest, MessagePartsTest, ThreadAggregationTest (and the
folders half of the mailbox contract) to pytest over xx_email.domain.
No GI imports, no network, no home directory: tmp dirs only.
"""

import sys
from datetime import datetime, timezone

from xx_email.domain import (
    INBOX_TABS,
    AddressUtils,
    FoundAttachment,
    FtsEscaper,
    LabelCsv,
    MailboxFolder,
    MessageParts,
    NewMailDetector,
    OutboxFiles,
    OutboxKind,
    OutboxSend,
    OutboxState,
    Recipients,
    RemoteImagePolicy,
    SendCancelPolicy,
    SendOutcome,
    SendRetryPolicy,
    SnoozePresets,
    SafePaths,
    ThreadAggregation,
    server_label_filter,
)
from xx_email.gmail_client import (
    GmailThread,
    Message,
    MessageHeader,
    MessagePart,
    MessagePartBody,
)


def test_domain_imports_no_gi():
    assert "gi" not in sys.modules


def utc(hour, day=25, month=8, year=2026):
    return datetime(year, month, day, hour, 0, 0, 0, timezone.utc)


# ------------------------------------------------------- snooze presets


def test_later_today_caps_at_6pm():
    assert SnoozePresets.later_today(utc(15)) == utc(18)


def test_later_today_rolls_to_tomorrow_when_too_close_to_6pm():
    assert SnoozePresets.later_today(utc(17)) == \
        datetime(2026, 8, 26, 8, 0, 0, 0, timezone.utc)


def test_later_today_is_plus_four_hours_in_the_morning():
    assert SnoozePresets.later_today(utc(9)) == utc(13)


def test_later_today_late_evening_rolls_to_tomorrow():
    assert SnoozePresets.later_today(utc(21)) == \
        datetime(2026, 8, 26, 8, 0, 0, 0, timezone.utc)


def test_next_week_lands_on_monday_8am():
    wednesday = datetime(2026, 8, 26, 12, 0, 0, 0, timezone.utc)
    assert SnoozePresets.next_week(wednesday) == \
        datetime(2026, 8, 31, 8, 0, 0, 0, timezone.utc)


def test_next_week_from_sunday_still_lands_on_monday():
    sunday = datetime(2026, 8, 30, 12, 0, 0, 0, timezone.utc)
    assert SnoozePresets.next_week(sunday) == \
        datetime(2026, 8, 31, 8, 0, 0, 0, timezone.utc)


def test_weekend_lands_on_saturday_8am():
    assert SnoozePresets.weekend(utc(10)) == \
        datetime(2026, 8, 29, 8, 0, 0, 0, timezone.utc)


def test_weekend_from_saturday_moves_a_week_out():
    saturday = datetime(2026, 8, 29, 12, 0, 0, 0, timezone.utc)
    assert SnoozePresets.weekend(saturday) == \
        datetime(2026, 9, 5, 8, 0, 0, 0, timezone.utc)


# ------------------------------------------------- folders and address


def test_inbox_tabs_cover_the_five_categories():
    assert INBOX_TABS == [
        MailboxFolder.PRIMARY,
        MailboxFolder.SOCIAL,
        MailboxFolder.PROMOTIONS,
        MailboxFolder.UPDATES,
        MailboxFolder.FORUMS,
    ]
    assert MailboxFolder.PRIMARY.category == "CATEGORY_PERSONAL"
    assert MailboxFolder.PRIMARY.include_empty_primary is True
    assert MailboxFolder.SOCIAL.include_empty_primary is False
    assert MailboxFolder.SENT.label_id == "SENT"
    assert MailboxFolder.DRAFTS.label_id == "DRAFT"
    assert MailboxFolder.SPAM.label_id == "SPAM"
    assert MailboxFolder.TRASH.label_id == "TRASH"
    assert MailboxFolder.OUTBOX.label_id is None


def test_server_label_filter_only_for_label_folders():
    assert server_label_filter(MailboxFolder.SPAM) == ["SPAM"]
    assert server_label_filter(MailboxFolder.SENT) == ["SENT"]
    assert server_label_filter(MailboxFolder.PRIMARY) is None
    assert server_label_filter(MailboxFolder.ALL_MAIL) is None


def test_address_splitting_handles_display_names():
    name, address = AddressUtils.split("Jane Doe <jane@example.com>")
    assert (name, address) == ("Jane Doe", "jane@example.com")


def test_address_splitting_falls_back_to_bare_address():
    name, address = AddressUtils.split("bob@example.com")
    assert (name, address) == ("bob@example.com", "bob@example.com")


def test_initials_derive_from_display_name():
    assert AddressUtils.initials("Jane Doe") == "JD"
    assert AddressUtils.initials("alice@example.com") == "AL"
    assert AddressUtils.initials("") == "?"
    assert AddressUtils.initials("Madonna") == "MA"


# ------------------------------------------------------------ recipients


def test_reply_all_includes_from_plus_to_and_cc():
    to, cc = Recipients.reply_all(
        from_header="From <from@example.com>",
        to_header="a@example.com, b@example.com",
        cc_header="c@example.com",
        account="me@example.com",
    )
    assert to == ["from@example.com", "a@example.com", "b@example.com"]
    assert cc == ["c@example.com"]


def test_reply_all_excludes_self_and_never_duplicates():
    to, cc = Recipients.reply_all(
        from_header="Me <me@example.com>",
        to_header="Me <me@example.com>, B@Example.com, b@example.com",
        cc_header="me@example.com, c@example.com",
        account="me@example.com",
    )
    assert to == ["B@Example.com"]
    assert cc == ["c@example.com"]


def test_reply_all_keeps_original_to_when_from_is_self():
    to, _cc = Recipients.reply_all(
        from_header="me@example.com",
        to_header="a@example.com",
        cc_header="",
        account="me@example.com",
    )
    assert to == ["a@example.com"]


def test_reply_uses_original_from_unless_it_is_self():
    assert Recipients.reply_to("Jane Doe <jane@example.com>",
                               "a@example.com",
                               account="me@example.com") == \
        ["jane@example.com"]
    assert Recipients.reply_to("me@example.com",
                               "a@example.com, b@example.com",
                               account="me@example.com") == \
        ["a@example.com", "b@example.com"]


def test_quoted_display_name_with_comma_stays_one_address():
    assert Recipients.parse('"Doe, Jane" <jane@x.com>, bob@y.com') == \
        ["jane@x.com", "bob@y.com"]


def test_fully_quoted_address_string_is_one_entry():
    result = Recipients.parse('"Doe, Jane <jane@x.com>"')
    assert len(result) == 1
    assert "jane@x.com" in result[0]


def test_garbage_input_keeps_valid_addresses():
    result = Recipients.parse_validated(
        "<<<<>>, , total garbage, jane@x.com")
    assert "jane@x.com" in result
    assert all("@" in addr for addr in result)
    assert Recipients.parse("") == []
    assert Recipients.parse_validated("   ") == []


# ------------------------------------------------------------ FTS escape


def test_plain_terms_become_quoted_prefix_phrases():
    assert FtsEscaper.escape_prefix_query("hello world") == \
        '"hello"* "world"*'


def test_boolean_operators_neutralized_as_literals():
    assert FtsEscaper.escape_prefix_query("AND") == '"AND"*'
    assert FtsEscaper.escape_prefix_query("OR NOT") == '"OR"* "NOT"*'


def test_fts_syntax_characters_defused():
    assert FtsEscaper.escape_prefix_query("from:alice@example.com") == \
        '"from:alice@example.com"*'
    assert FtsEscaper.escape_prefix_query('a"b') == '"ab"*'
    assert FtsEscaper.escape_prefix_query("(paren)") == '"(paren)"*'
    assert FtsEscaper.escape_prefix_query("a-b c*d") == '"a-b"* "c*d"*'


def test_whitespace_runs_collapse_and_empties_vanish():
    assert FtsEscaper.escape_prefix_query("  a   b  ") == '"a"* "b"*'
    assert FtsEscaper.escape_prefix_query("   ") == ""
    assert FtsEscaper.escape_prefix_query("") == ""
    assert FtsEscaper.escape_prefix_query('" " ab') == '"ab"*'


# --------------------------------------------------------------- label CSV


def test_exact_token_match_sent_never_matches_consent():
    assert LabelCsv.contains("CONSENT", "SENT") is False
    assert LabelCsv.contains("INBOX,CONSENT", "SENT") is False


def test_trash_does_not_match_substring_labels():
    assert LabelCsv.contains("MYTRASHFOLDER", "TRASH") is False
    assert LabelCsv.contains("MYTRASHFOLDER", "MYTRASHFOLDER") is True


def test_real_labels_match_exactly():
    assert LabelCsv.contains("INBOX,TRASH,UNREAD", "TRASH") is True
    assert LabelCsv.contains("TRASH", "TRASH") is True
    assert LabelCsv.contains("INBOX,CATEGORY_PERSONAL",
                             "CATEGORY_PERSON") is False
    assert LabelCsv.contains("INBOX", "") is False
    assert LabelCsv.contains("", "INBOX") is False


def test_add_appends_and_dedupes():
    assert LabelCsv.add("INBOX", "TRASH") == "INBOX,TRASH"
    assert LabelCsv.add("INBOX,TRASH", "TRASH") == "INBOX,TRASH"
    assert LabelCsv.add("", "TRASH") == "TRASH"
    assert LabelCsv.add("INBOX", "") == "INBOX"


def test_remove_drops_only_the_exact_token():
    assert LabelCsv.remove("INBOX,TRASH", "TRASH") == "INBOX"
    assert LabelCsv.remove("MYTRASHFOLDER,TRASH",
                           "MYTRASHFOLDER") == "TRASH"
    assert LabelCsv.remove("TRASH", "TRASH") == ""
    assert LabelCsv.remove("INBOX", "MISSING") == "INBOX"


def test_add_remove_roundtrip_restores_csv():
    csv = "INBOX,CATEGORY_PROMOTIONS,UNREAD"
    assert LabelCsv.remove(LabelCsv.add(csv, "TEMP"), "TEMP") == csv


# -------------------------------------------------------------- safe paths


def test_clean_filename_passes_through():
    assert SafePaths.child_name("report.pdf") == "report.pdf"
    assert SafePaths.child_name("attachment-ANGtJd-_abc123") == \
        "attachment-ANGtJd-_abc123"


def test_path_traversal_collapses_to_last_segment():
    assert SafePaths.child_name("../../files/authstates.bin") == \
        "authstates.bin"
    assert SafePaths.child_name("../../secret") == "secret"


def test_dot_and_dotdot_rejected():
    assert SafePaths.child_name(".") is None
    assert SafePaths.child_name("..") is None


def test_dots_only_and_trailing_dots_rejected():
    assert SafePaths.child_name("...") is None
    assert SafePaths.child_name("../..") is None


def test_blank_and_none_rejected():
    assert SafePaths.child_name(None) is None
    assert SafePaths.child_name("") is None
    assert SafePaths.child_name("   ") is None


def test_nested_separators_collapse_to_final_segment():
    assert SafePaths.child_name("a/b/c") == "c"
    assert SafePaths.child_name("/a/b/c/deep.txt") == "deep.txt"


def test_absolute_paths_keep_only_the_file_part():
    assert SafePaths.child_name("/etc/passwd") == "passwd"


def test_windows_separators_are_separators():
    assert SafePaths.child_name("..\\..\\x.bin") == "x.bin"
    assert SafePaths.child_name("C:\\Users\\victim\\notes") == "notes"


def test_null_bytes_rejected():
    assert SafePaths.child_name("bad\x00name") is None
    assert SafePaths.child_name("\x00") is None


def test_overly_long_names_truncated():
    long = "x" * 10000 + ".pdf"
    safe = SafePaths.child_name(long)
    assert safe is not None
    assert len(safe) == SafePaths.MAX_NAME_LENGTH
    assert "/" not in safe


def test_child_name_or_falls_back_to_last_resort(tmp_path):
    assert SafePaths.child_name_or("a/ok.txt", "seed", "last") == \
        "ok.txt"
    assert SafePaths.child_name_or(None, "seed", "last") == "seed"
    assert SafePaths.child_name_or("../..", "../seed", "last") == "seed"
    assert SafePaths.child_name_or("", "", "last") == "last"


def test_file_directly_in_dir_is_inside(tmp_path):
    assert SafePaths.is_inside(tmp_path, tmp_path / "child.txt") is True


def test_parent_escape_is_not_inside(tmp_path):
    assert SafePaths.is_inside(tmp_path,
                               tmp_path / ".." / "escape.txt") is False
    assert SafePaths.is_inside(tmp_path, tmp_path.parent) is False
    assert SafePaths.is_inside(tmp_path, "/etc/passwd") is False


# -------------------------------------------------------- remote images


def test_https_urls_with_a_host_allowed():
    assert RemoteImagePolicy.is_https_url(
        "https://images.example.com/px.gif") is True
    assert RemoteImagePolicy.is_https_url(
        "HTTPS://Example.COM/a.png") is True


def test_non_https_and_empty_sources_rejected():
    assert RemoteImagePolicy.is_https_url(
        "http://images.example.com/px.gif") is False
    assert RemoteImagePolicy.is_https_url(
        "file:///data/local/tmp/x") is False
    assert RemoteImagePolicy.is_https_url("javascript:alert(1)") is False
    assert RemoteImagePolicy.is_https_url("https://") is False
    assert RemoteImagePolicy.is_https_url("") is False
    assert RemoteImagePolicy.is_https_url("not a url") is False


# ------------------------------------------------------- new-mail detector


class _Row:
    def __init__(self, row_id, unread=1):
        self.id = row_id
        self.unread_count = unread


def test_first_sync_notifies_nothing():
    current = [_Row("1"), _Row("2"), _Row("3")]
    assert NewMailDetector.new_arrivals(None, current) == []


def test_old_unread_threads_not_renotified():
    current = [_Row("1"), _Row("2")]
    assert NewMailDetector.new_arrivals({"1", "2"}, current) == []


def test_newly_arrived_unread_threads_detected():
    current = [_Row("1"), _Row("9")]
    arrivals = NewMailDetector.new_arrivals({"1", "2"}, current)
    assert [row.id for row in arrivals] == ["9"]


def test_empty_prior_inbox_means_everything_is_new():
    arrivals = NewMailDetector.new_arrivals(set(), [_Row("1")])
    assert [row.id for row in arrivals] == ["1"]


def test_burst_of_arrivals_fully_reported():
    known = {str(n) for n in range(1, 61)}
    current = [_Row(str(n)) for n in range(61, 131)]
    assert len(NewMailDetector.new_arrivals(known, current)) == 70


# ------------------------------------------------------------ outbox files


def test_write_new_stores_relative_path_and_readable_file(tmp_path):
    rel = OutboxFiles.write_new(tmp_path, 42, b"hello")
    assert rel == "outbox/42.eml"
    resolved = OutboxFiles.resolve(tmp_path, rel)
    assert resolved is not None
    assert resolved.read_bytes() == b"hello"
    assert resolved.is_file()


def test_resolve_rejects_traversal_and_absolute_paths(tmp_path):
    assert OutboxFiles.resolve(tmp_path, "../escape.eml") is None
    assert OutboxFiles.resolve(tmp_path, "/etc/passwd") is None
    assert OutboxFiles.resolve(tmp_path, "") is None
    assert OutboxFiles.resolve(tmp_path, None) is None
    assert OutboxFiles.resolve(tmp_path, "outbox/missing.eml") is None


def test_delete_payload_file_removes_eml_and_stale_tmp(tmp_path):
    rel = OutboxFiles.write_new(tmp_path, 7, b"x")
    stale = tmp_path / "outbox" / "7.eml.tmp"
    stale.write_bytes(b"1")
    OutboxFiles.delete_payload_file(tmp_path, rel, 7)
    assert OutboxFiles.resolve(tmp_path, rel) is None
    assert not stale.exists()
    OutboxFiles.delete_payload_file(tmp_path, None, 7)


# ------------------------------------------------------------ outbox send


def test_immediate_send_queued_with_undo_window():
    now = 1700000000000
    plan = OutboxSend.plan(now_ms=now, undo_seconds=10,
                           scheduled_at=None)
    assert plan.kind == OutboxKind.SEND
    assert plan.state == OutboxState.QUEUED
    assert plan.target_at == now + 10000
    assert plan.initial_delay_ms == 10000
    assert plan.requires_connected_network is True


def test_scheduled_send_waits_until_target():
    now = 1700000000000
    at = now + 3600000
    plan = OutboxSend.plan(now_ms=now, undo_seconds=10,
                           scheduled_at=at)
    assert plan.kind == OutboxKind.SCHEDULED_SEND
    assert plan.target_at == at
    assert plan.initial_delay_ms == 3600000
    assert plan.requires_connected_network is True


def test_enqueue_offline_persists_queued_payload(tmp_path):
    now = 1700000000000
    plan = OutboxSend.plan(now_ms=now, undo_seconds=5,
                           scheduled_at=None)
    draft = OutboxSend.entity("me@dev.xxemail", "thread-1",
                              "hello from airplane", plan, now)
    assert draft.state == OutboxState.QUEUED.value
    assert draft.kind == OutboxKind.SEND.value
    assert draft.subject == "hello from airplane"
    assert draft.thread_id == "thread-1"
    assert draft.rfc822_base64 is None
    payload = (b"From: me@dev.xxemail\r\nTo: you@example.com\r\n"
               b"Subject: hello from airplane\r\n\r\nbody\r\n")
    path, size = OutboxSend.write_payload(tmp_path, 42, payload)
    assert path == "outbox/42.eml"
    assert size == len(payload)
    assert (tmp_path / path).read_bytes() == payload
    assert OutboxFiles.resolve(tmp_path, path).is_file()


def test_compose_snackbar_says_queued():
    assert OutboxSend.queued_label(scheduled=False) == "Queued"
    assert OutboxSend.queued_label(scheduled=True) == "Send scheduled"


def test_failed_rows_retry_or_discard():
    assert OutboxSend.can_retry(OutboxState.FAILED.value) is True
    assert OutboxSend.can_discard(OutboxState.FAILED.value) is True
    assert OutboxSend.can_retry(OutboxState.QUEUED.value) is False
    assert OutboxSend.can_discard(OutboxState.QUEUED.value) is True
    assert OutboxSend.can_retry(OutboxState.SENDING.value) is False
    assert OutboxSend.can_discard(OutboxState.SENDING.value) is False
    assert OutboxSend.can_discard(OutboxState.SENT.value) is False
    assert OutboxSend.is_open(OutboxState.QUEUED.value) is True
    assert OutboxSend.is_open(OutboxState.FAILED.value) is True
    assert OutboxSend.is_open(OutboxState.SENDING.value) is True
    assert OutboxSend.is_open(OutboxState.SENT.value) is False


# -------------------------------------------------- retry/cancel tables


def test_undo_only_while_still_queued():
    assert SendCancelPolicy.can_undo(OutboxState.QUEUED.value) is True
    assert SendCancelPolicy.can_undo(OutboxState.SENDING.value) is False
    assert SendCancelPolicy.can_undo(OutboxState.SENT.value) is False
    assert SendCancelPolicy.can_undo(None) is False


def test_2xx_with_garbage_body_marked_sent():
    assert SendRetryPolicy.decide(http_code=200,
                                  transport_error=False) == \
        SendOutcome.MARK_SENT


def test_2xx_valid_response_marked_sent():
    assert SendRetryPolicy.decide(200, False) == SendOutcome.MARK_SENT
    assert SendRetryPolicy.decide(201, False) == SendOutcome.MARK_SENT
    assert SendRetryPolicy.decide(204, False) == SendOutcome.MARK_SENT


def test_5xx_retried():
    assert SendRetryPolicy.decide(500, False) == SendOutcome.RETRY
    assert SendRetryPolicy.decide(503, False) == SendOutcome.RETRY


def test_4xx_marked_failed_without_retry():
    assert SendRetryPolicy.decide(401, False) == SendOutcome.MARK_FAILED
    assert SendRetryPolicy.decide(400, False) == SendOutcome.MARK_FAILED
    assert SendRetryPolicy.decide(403, False) == SendOutcome.MARK_FAILED


def test_429_retried():
    assert SendRetryPolicy.decide(429, False) == SendOutcome.RETRY


def test_transport_error_retried():
    assert SendRetryPolicy.decide(None, True) == SendOutcome.RETRY


def test_unknown_outcomes_retried_conservatively():
    assert SendRetryPolicy.decide(None, False) == SendOutcome.RETRY
    assert SendRetryPolicy.decide(302, False) == SendOutcome.RETRY


def test_retry_budget_is_five_attempts():
    assert SendRetryPolicy.MAX_ATTEMPTS == 5


# --------------------------------------------- message parts/aggregation


def _leaf(mime, filename=None, attachment_id=None):
    if attachment_id is not None:
        body = MessagePartBody(attachment_id=attachment_id)
    else:
        body = MessagePartBody(data="eA")
    return MessagePart(mime_type=mime, filename=filename, body=body)


def _nested_thread():
    return MessagePart(
        mime_type="multipart/mixed",
        parts=[
            MessagePart(
                mime_type="multipart/alternative",
                parts=[_leaf("text/plain"), _leaf("text/html")],
            ),
            _leaf("application/pdf", filename="report.pdf",
                  attachment_id="att-1"),
            MessagePart(
                mime_type="multipart/mixed",
                parts=[_leaf("image/png", filename="pic.png",
                             attachment_id="att-2")],
            ),
        ],
    )


def test_has_attachment_recurses_nested_multiparts():
    assert MessageParts.has_attachment_in(_nested_thread()) is True
    assert MessageParts.has_attachment(
        Message(id="m", payload=_nested_thread())) is True


def test_plain_body_has_no_attachments():
    assert MessageParts.has_attachment_in(_leaf("text/plain")) is False
    assert MessageParts.has_attachment(Message(id="m")) is False


def test_attachments_collects_every_depth_with_metadata():
    found = MessageParts.attachments("msg-1", _nested_thread())
    assert [item.attachment_id for item in found] == ["att-1", "att-2"]
    assert [item.filename for item in found] == ["report.pdf", "pic.png"]
    assert all(item.message_id == "msg-1" for item in found)
    assert found[0].mime_type == "application/pdf"
    assert isinstance(found[0], FoundAttachment)


def test_filename_without_attachment_id_not_downloadable():
    part = MessagePart(
        mime_type="multipart/mixed",
        parts=[_leaf("application/octet-stream",
                     filename="weird.bin")],
    )
    assert MessageParts.has_attachment_in(part) is True
    assert MessageParts.attachments("m", part) == []


def test_find_body_walks_nested_multiparts():
    assert MessageParts.find_body(_nested_thread(),
                                  "text/html") is not None
    assert MessageParts.find_body(_nested_thread(),
                                  "text/calendar") is None
    assert MessageParts.find_body(None, "text/html") is None


def _api_message(mid, labels, internal_date, subject=None):
    headers = [MessageHeader(name="Subject",
                             value=subject or ("s" + mid)),
               MessageHeader(name="From",
                             value="Jane Doe <jane@example.com>")]
    return Message(id=mid, thread_id="t1", label_ids=list(labels),
                   snippet="snip" + mid, internal_date=internal_date,
                   payload=MessagePart(headers=headers))


def _api_thread(*messages):
    return GmailThread(id="t1", snippet="thread snippet",
                       history_id="100",
                       messages=list(messages))


def test_reply_with_sent_only_latest_stays_in_inbox():
    thread = _api_thread(
        _api_message("old", ["INBOX", "UNREAD"], "1000"),
        _api_message("latest", ["SENT"], "2000"),
    )
    assert ThreadAggregation.build("a@x.com", thread).in_inbox is True


def test_sent_only_thread_not_in_inbox():
    thread = _api_thread(_api_message("m1", ["SENT"], "1000"))
    assert ThreadAggregation.build("a@x.com", thread).in_inbox is False


def test_categories_come_from_union_of_all_messages():
    thread = _api_thread(
        _api_message("old", ["INBOX", "CATEGORY_PROMOTIONS"], "1000"),
        _api_message("latest", ["CATEGORY_UPDATES", "CATEGORY_FORUMS"],
                     "2000"),
    )
    entity = ThreadAggregation.build("a@x.com", thread)
    assert "CATEGORY_PROMOTIONS" in entity.categories
    assert "CATEGORY_UPDATES" in entity.categories
    assert "CATEGORY_FORUMS" in entity.categories


def test_starred_and_unread_union_based():
    thread = _api_thread(
        _api_message("old", ["INBOX", "STARRED", "UNREAD"], "1000"),
        _api_message("latest", ["SENT", "UNREAD"], "2000"),
    )
    entity = ThreadAggregation.build("a@x.com", thread)
    assert entity.starred is True
    assert entity.unread_count == 2
    assert entity.message_count == 2


def test_aggregate_fields_mirror_newest_message():
    thread = _api_thread(
        _api_message("old", ["INBOX"], "1000", subject="older"),
        _api_message("latest", ["INBOX"], "2000", subject="newer"),
    )
    entity = ThreadAggregation.build("a@x.com", thread)
    assert entity.subject == "newer"
    assert entity.from_name == "Jane Doe"
    assert entity.snippet == "thread snippet"
    assert entity.date == 2000


def test_empty_thread_falls_back_to_placeholder():
    entity = ThreadAggregation.build("a@x.com", GmailThread(id="t1"))
    assert entity.subject == "(no subject)"
    assert entity.in_inbox is False
    assert entity.date == 0


def test_snoozed_until_passes_through():
    entity = ThreadAggregation.build("a@x.com", GmailThread(id="t1"),
                                     snoozed_until=42)
    assert entity.snoozed_until == 42


def test_build_message_maps_row_fields():
    message = _api_message("m1", ["INBOX", "UNREAD"], "1500")
    row = ThreadAggregation.build_message("a@x.com", message, "t1")
    assert row.thread_id == "t1"
    assert row.subject == "sm1"
    assert row.read is False
    assert row.labels_csv == "INBOX,UNREAD"
    assert row.message_id_header is None
