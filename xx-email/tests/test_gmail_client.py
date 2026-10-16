"""Gmail client tests: golden JSON parsing plus endpoint/401 behavior.

Ports GmailJsonTest (uint64-as-string, ignore-unknown-keys) and
GmailUploadsTest (media vs multipart bodies), and covers the
GmailApiFactory 401 authenticator port: one retry with a fresh token,
no loop on a second 401, no retry when the token did not rotate.
"""

import json

import pytest

from xx_email import gmail_client
from xx_email.auth import ReauthNeeded
from xx_email.gmail_client import (
    BOUNDARY,
    GmailClient,
    GmailTransportError,
    HttpResponse,
    media_body,
    multipart_body,
    parse_history,
    parse_profile,
    parse_thread_list,
    thread_metadata_json,
)


class FakeTransport:
    """Scripted (status, payload) responses; records every call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url,
                           "headers": headers, "body": body})
        status, payload = self.responses.pop(0)
        raw = json.dumps(payload).encode() if isinstance(
            payload, dict) else payload
        return HttpResponse(status=status, body=raw)


def ok(payload):
    return (200, payload)


def client_for(*responses, get_token=None, refresh_token=None):
    transport = FakeTransport(*responses)
    client = GmailClient(transport=transport,
                         get_token=get_token or (lambda: "tok-old"),
                         refresh_token=refresh_token)
    return client, transport


def test_profile_decodes_quoted_uint64_as_string():
    profile = parse_profile({
        "emailAddress": "piercingxx@example.com",
        "messagesTotal": 10934,
        "threadsTotal": 1822,
        "historyId": "9223372036854775808",
        "labelTotals": {"unread": 42},
    })
    assert profile.email_address == "piercingxx@example.com"
    assert profile.history_id == "9223372036854775808"
    assert profile.messages_total == 10934
    assert profile.threads_total == 1822


def test_thread_list_decodes_history_ids_as_strings():
    page = parse_thread_list({
        "threads": [
            {"id": "17fadf0d3b0a1c2b", "snippet": "Hello there",
             "historyId": "9223372036854775999"},
            {"id": "17fadf0e00000001", "snippet": "Second",
             "historyId": "9223372036854776000"},
        ],
        "nextPageToken": "page-2",
        "resultSizeEstimate": 2,
    })
    assert len(page.threads) == 2
    assert page.threads[0].history_id == "9223372036854775999"
    assert page.threads[1].history_id == "9223372036854776000"
    assert page.next_page_token == "page-2"


def test_thread_get_decodes_message_history_ids():
    client, _ = client_for(ok({
        "id": "17fadf0d3b0a1c2b",
        "snippet": "Lunch tomorrow?",
        "historyId": "9223372036854780000",
        "messages": [{
            "id": "17fadf0d3b0a1c2b.1",
            "threadId": "17fadf0d3b0a1c2b",
            "labelIds": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
            "snippet": "Lunch tomorrow?",
            "historyId": "9223372036854779000",
            "internalDate": "1771891200000",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [
                    {"name": "From",
                     "value": "Jane Doe <jane@example.com>"},
                    {"name": "Subject", "value": "Lunch tomorrow?"},
                ],
            },
            "sizeEstimate": 2048,
        }],
    }))
    thread = client.get_thread("17fadf0d3b0a1c2b")
    assert thread.history_id == "9223372036854780000"
    message = thread.messages[0]
    assert message.history_id == "9223372036854779000"
    assert message.internal_date == "1771891200000"
    assert message.label_ids == ["INBOX", "UNREAD", "CATEGORY_PERSONAL"]


def test_history_list_decodes_item_ids_as_strings():
    page = parse_history({
        "history": [
            {"id": "9223372036854776001",
             "messages": [{"id": "msg-a", "threadId": "thread-a"}],
             "messagesAdded": [{"message": {"id": "msg-b",
                                            "threadId": "thread-b",
                                            "labelIds": ["INBOX"]}}]},
            {"id": "9223372036854776002",
             "labelsRemoved": [{"labelIds": ["UNREAD"],
                                "message": {"id": "msg-a",
                                            "threadId": "thread-a"}}]},
        ],
        "historyId": "9223372036854776100",
    })
    assert page.history[0].id == "9223372036854776001"
    assert page.history[1].id == "9223372036854776002"
    assert page.history_id == "9223372036854776100"
    assert page.history[0].messages_added[0].id == "msg-b"


def test_send_response_decodes_quoted_history_id():
    client, _ = client_for(ok({
        "id": "17fadf10000000aa",
        "threadId": "17fadf0d3b0a1c2b",
        "labelIds": ["SENT"],
        "historyId": "9223372036854781111",
    }))
    sent = client.send_raw(b"raw")
    assert sent.id == "17fadf10000000aa"
    assert sent.thread_id == "17fadf0d3b0a1c2b"
    assert sent.label_ids == ["SENT"]
    assert sent.history_id == "9223372036854781111"


def test_uint64_beyond_long_range_survive_as_exact_strings():
    for raw in ("9223372036854775808", "18446744073709551615"):
        page = parse_thread_list({"threads": [{"id": "t",
                                               "historyId": raw}]})
        assert page.threads[0].history_id == raw


def test_numeric_history_id_coerced_to_string():
    page = parse_thread_list({"threads": [{"id": "t", "historyId": 123456}]})
    assert page.threads[0].history_id == "123456"


def test_endpoints_hit_right_method_path_and_auth():
    bodies = {
        "profile": {"emailAddress": "me@example.com"},
        "labels": {"labels": [{"id": "INBOX", "name": "Inbox",
                               "type": "system",
                               "color": {"background": "#000",
                                         "text": "#fff"}}]},
        "threads": {"threads": []},
        "attach": {"attachmentId": "a", "size": 10, "data": "eA=="},
        "history": {"history": []},
        "vacation": {"enableAutoReply": True},
        "sendAs": {"sendAs": [{"sendAsEmail": "me@example.com",
                               "isPrimary": True}]},
    }
    client, transport = client_for(*[ok(bodies[key]) for key in
                                     ("profile", "labels", "threads",
                                      "threads", "threads", "threads",
                                      "threads", "threads", "threads",
                                      "attach", "history", "vacation",
                                      "sendAs")])
    assert client.get_profile().email_address == "me@example.com"
    label = client.list_labels()[0]
    assert (label.id, label.color_bg) == ("INBOX", "#000")
    client.list_threads(query="from:x", label_ids=["INBOX"], page_token="p",
                        include_spam_trash=True)
    client.get_thread("t1")
    client.modify_thread("t1", add=["STARRED"], remove=["UNREAD"])
    client.trash_thread("t1")
    client.untrash_thread("t1")
    client.get_message("m1")
    client.modify_message("m1", add=["STARRED"])
    client.get_attachment("m1", "a")
    client.list_history("123")
    assert client.get_vacation().enable_auto_reply is True
    assert client.list_send_as()[0].is_primary is True
    for call in transport.calls:
        assert call["headers"]["Authorization"] == "Bearer tok-old"
    methods_urls = [(c["method"], c["url"]) for c in transport.calls]
    assert ("GET", gmail_client.BASE_URL + "gmail/v1/users/me/profile") in \
        methods_urls
    list_call = transport.calls[2]
    assert "q=from%3Ax" in list_call["url"]
    assert "labelIds=INBOX" in list_call["url"]
    assert "pageToken=p" in list_call["url"]
    modify_body = json.loads(transport.calls[4]["body"].decode())
    assert modify_body == {"addLabelIds": ["STARRED"],
                           "removeLabelIds": ["UNREAD"]}
    assert transport.calls[5]["method"] == "POST"
    assert transport.calls[5]["url"].endswith("/threads/t1/trash")


def test_401_retries_once_with_fresh_token():
    client, transport = client_for(
        (401, b""), ok({"emailAddress": "me@example.com"}),
        refresh_token=lambda failed: "tok-new")
    assert client.get_profile().email_address == "me@example.com"
    assert len(transport.calls) == 2
    assert transport.calls[0]["headers"]["Authorization"] == \
        "Bearer tok-old"
    assert transport.calls[1]["headers"]["Authorization"] == \
        "Bearer tok-new"


def test_second_401_does_not_loop():
    client, transport = client_for(
        (401, b""), (401, b""),
        refresh_token=lambda failed: "tok-new")
    with pytest.raises(ReauthNeeded):
        client.get_profile()
    assert len(transport.calls) == 2


def test_same_token_after_refresh_does_not_retry():
    client, transport = client_for(
        (401, b""), refresh_token=lambda failed: failed)
    with pytest.raises(ReauthNeeded):
        client.get_profile()
    assert len(transport.calls) == 1


def test_401_without_refresh_configured_surfaces_reauth():
    client, transport = client_for((401, b""))
    with pytest.raises(ReauthNeeded):
        client.get_profile()
    assert len(transport.calls) == 1


def test_refresh_failure_propagates_as_reauth():
    def refresh(failed):
        raise ReauthNeeded("revoked")

    client, _ = client_for((401, b""), refresh_token=refresh)
    with pytest.raises(ReauthNeeded):
        client.get_profile()


def test_error_status_raises_transport_error_with_status():
    client, _ = client_for((403, {"error": "forbidden"}))
    with pytest.raises(GmailTransportError) as caught:
        client.get_profile()
    assert caught.value.status == 403


def test_network_failure_raises_transport_error():
    def broken(method, url, headers, body):
        raise OSError("offline")

    client = GmailClient(transport=broken)
    with pytest.raises(GmailTransportError):
        client.get_profile()


def test_media_body_is_raw_rfc822_octets():
    content_type, data = media_body(b"\x01\x02\x03")
    assert content_type == "message/rfc822"
    assert data == b"\x01\x02\x03"


def test_multipart_body_carries_thread_metadata_and_raw_octets():
    rfc822 = b"From: me@example.com\r\n\r\nHi"
    content_type, data = multipart_body(rfc822, "thread-123")
    text = data.decode("ascii")
    assert content_type == f'multipart/related; boundary="{BOUNDARY}"'
    assert text.count(f"--{BOUNDARY}") == 3
    assert "Content-Type: application/json; charset=UTF-8" in text
    assert '{"threadId":"thread-123"}' in text
    assert "Content-Type: message/rfc822" in text
    assert "From: me@example.com" in text
    assert "Content-Transfer-Encoding: base64" not in text
    assert text.endswith(f"--{BOUNDARY}--")


def test_thread_metadata_json_escapes_quotes():
    assert thread_metadata_json("17fadf0d3b0a1c2b") == \
        '{"threadId":"17fadf0d3b0a1c2b"}'
    assert thread_metadata_json('a"b\\c') == '{"threadId":"a\\"b\\\\c"}'


def test_send_chooses_transport_by_thread_id():
    client, transport = client_for(
        ok({"id": "fresh"}), ok({"id": "reply", "threadId": "t"}))
    assert client.send(b"raw").id == "fresh"
    assert client.send(b"raw", "t").thread_id == "t"
    assert transport.calls[0]["headers"]["Content-Type"] == "message/rfc822"
    assert transport.calls[1]["headers"]["Content-Type"].startswith(
        "multipart/related")
    assert transport.calls[0]["url"].endswith("uploadType=media")
    assert transport.calls[1]["url"].endswith("uploadType=multipart")
