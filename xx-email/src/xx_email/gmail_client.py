"""Gmail REST client over stdlib urllib + json (no GTK, no network in tests).

Ports the GmailApi.kt surface (one thin function per endpoint, each
taking an access token and returning plain dataclasses) plus the
GmailApiFactory 401 authenticator logic exactly: one retry with a
fresh token, then surface re-auth as ReauthNeeded; a second 401 on the
retried request never loops. historyId stays an opaque string - never
arithmetic (uint64 overflow) - and unknown JSON keys are ignored, so
new Google fields never crash parsing.

The HTTP transport is injectable: pass any callable
(method, url, headers, body) -> HttpResponse. Tests use a fake; the
default transport uses urllib with the factory's timeouts (20 s
connect, 60 s read - urllib takes a single timeout, so the read value
bounds the whole call). 429/5xx responses retry with exponential
backoff plus jitter, honoring Retry-After; the sleep and jitter
sources are injectable so tests never wait on the wall clock.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from xx_email.auth import ReauthNeeded

BASE_URL = "https://gmail.googleapis.com/"
UPLOAD_BASE_URL = "https://gmail.googleapis.com/upload/gmail/v1/users/me/"
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 60

# Rate-limit/server-error retries: 5 attempts total, mirroring the
# SendRetryPolicy cap of 5. Only 429/5xx retry; other 4xx fail fast.
DEFAULT_MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.0

BOUNDARY = "xxemail-send-boundary"


class GmailTransportError(Exception):
    """Network failure or a non-401 error status from Google."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class HttpResponse:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)


@dataclass
class Profile:
    email_address: str
    history_id: str | None = None
    messages_total: int | None = None
    threads_total: int | None = None


@dataclass
class GmailLabel:
    id: str
    name: str
    type: str | None = None
    color_bg: str | None = None
    color_text: str | None = None


@dataclass
class MessageRef:
    id: str
    thread_id: str | None = None


@dataclass
class ThreadRef:
    id: str
    snippet: str | None = None
    history_id: str | None = None


@dataclass
class ThreadListPage:
    threads: list = field(default_factory=list)
    next_page_token: str | None = None
    result_size_estimate: int | None = None


@dataclass
class MessageHeader:
    name: str
    value: str


@dataclass
class MessagePartBody:
    size: int = 0
    data: str | None = None
    attachment_id: str | None = None


@dataclass
class MessagePart:
    part_id: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    headers: list = field(default_factory=list)
    body: MessagePartBody | None = None
    parts: list = field(default_factory=list)


@dataclass
class Message:
    id: str
    thread_id: str | None = None
    label_ids: list = field(default_factory=list)
    snippet: str | None = None
    history_id: str | None = None
    internal_date: str | None = None
    payload: MessagePart | None = None
    size_estimate: int | None = None


@dataclass
class GmailThread:
    id: str
    snippet: str | None = None
    history_id: str | None = None
    messages: list = field(default_factory=list)


@dataclass
class Attachment:
    attachment_id: str | None = None
    filename: str | None = None
    size: int = 0
    data: str | None = None


@dataclass
class HistoryItem:
    id: str
    messages: list = field(default_factory=list)
    messages_added: list = field(default_factory=list)
    messages_deleted: list = field(default_factory=list)
    labels_added: list = field(default_factory=list)
    labels_removed: list = field(default_factory=list)


@dataclass
class HistoryPage:
    history: list = field(default_factory=list)
    history_id: str | None = None
    next_page_token: str | None = None


@dataclass
class VacationSettings:
    enable_auto_reply: bool = False
    end_time: int | None = None
    start_time: int | None = None
    response_subject: str | None = None
    response_body_html: str | None = None


@dataclass
class SendAsEntry:
    send_as_email: str
    display_name: str | None = None
    is_default: bool = False
    is_primary: bool = False


def _uint64(value: object) -> str | None:
    """Coerce a Gmail uint64 (quoted string, or a bare number from a proxy)."""
    if value is None:
        return None
    return str(value)


def _str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _parse_part(raw: dict) -> MessagePart:
    body_raw = raw.get("body") or {}
    body = MessagePartBody(
        size=body_raw.get("size", 0) if isinstance(
            body_raw.get("size"), int) else 0,
        data=body_raw.get("data"),
        attachment_id=body_raw.get("attachmentId"),
    )
    return MessagePart(
        part_id=raw.get("partId"),
        mime_type=raw.get("mimeType"),
        filename=raw.get("filename"),
        headers=[MessageHeader(name=_str(h.get("name")),
                               value=_str(h.get("value")))
                 for h in raw.get("headers", []) if isinstance(h, dict)],
        body=body,
        parts=[_parse_part(p) for p in raw.get("parts", [])
               if isinstance(p, dict)],
    )


def _parse_message(raw: dict) -> Message:
    payload_raw = raw.get("payload")
    return Message(
        id=_str(raw.get("id")),
        thread_id=raw.get("threadId"),
        label_ids=[label for label in raw.get("labelIds", [])
                   if isinstance(label, str)],
        snippet=raw.get("snippet"),
        history_id=_uint64(raw.get("historyId")),
        internal_date=_uint64(raw.get("internalDate")),
        payload=_parse_part(payload_raw)
        if isinstance(payload_raw, dict) else None,
        size_estimate=raw.get("sizeEstimate")
        if isinstance(raw.get("sizeEstimate"), int) else None,
    )


def _parse_thread(raw: dict) -> GmailThread:
    return GmailThread(
        id=_str(raw.get("id")),
        snippet=raw.get("snippet"),
        history_id=_uint64(raw.get("historyId")),
        messages=[_parse_message(m) for m in raw.get("messages", [])
                  if isinstance(m, dict)],
    )


def parse_profile(payload: dict) -> Profile:
    return Profile(
        email_address=_str(payload.get("emailAddress")),
        history_id=_uint64(payload.get("historyId")),
        messages_total=payload.get("messagesTotal")
        if isinstance(payload.get("messagesTotal"), int) else None,
        threads_total=payload.get("threadsTotal")
        if isinstance(payload.get("threadsTotal"), int) else None,
    )


def parse_thread_list(payload: dict) -> ThreadListPage:
    threads = [ThreadRef(id=_str(t.get("id")), snippet=t.get("snippet"),
                         history_id=_uint64(t.get("historyId")))
               for t in payload.get("threads", []) if isinstance(t, dict)]
    estimate = payload.get("resultSizeEstimate")
    return ThreadListPage(
        threads=threads,
        next_page_token=payload.get("nextPageToken"),
        result_size_estimate=estimate if isinstance(estimate, int) else None,
    )


def parse_history(payload: dict) -> HistoryPage:
    items = []
    for raw in payload.get("history", []):
        if not isinstance(raw, dict):
            continue
        refs = lambda key: [MessageRef(  # noqa: E731
            id=_str(m.get("message", m).get("id")) if isinstance(
                m.get("message", m), dict) else "",
            thread_id=(m.get("message", m).get("threadId")
                       if isinstance(m.get("message", m), dict) else None))
            for m in raw.get(key, []) if isinstance(m, dict)]
        label_changes = lambda key: [  # noqa: E731
            (entry.get("labelIds", []), entry.get("message"))
            for entry in raw.get(key, []) if isinstance(entry, dict)]
        items.append(HistoryItem(
            id=str(raw.get("id")),
            messages=[MessageRef(id=_str(m.get("id")),
                                 thread_id=m.get("threadId"))
                      for m in raw.get("messages", [])
                      if isinstance(m, dict)],
            messages_added=refs("messagesAdded"),
            messages_deleted=refs("messagesDeleted"),
            labels_added=label_changes("labelsAdded"),
            labels_removed=label_changes("labelsRemoved"),
        ))
    return HistoryPage(history=items, history_id=_uint64(payload.get(
        "historyId")), next_page_token=payload.get("nextPageToken"))


def thread_metadata_json(thread_id: str) -> str:
    """Metadata JSON part. threadId is escaped so a quote cannot break it."""
    escaped = thread_id.replace("\\", "\\\\").replace('"', '\\"')
    return '{"threadId":"' + escaped + '"}'


def media_body(rfc822: bytes) -> tuple[str, bytes]:
    """Plain media upload body (fresh sends): raw RFC822 octets."""
    return ("message/rfc822", bytes(rfc822))


def multipart_body(rfc822: bytes,
                   thread_id: str) -> tuple[str, bytes]:
    """Multipart/related body (threaded sends): {threadId} JSON + raw octets.

    The media part stays raw RFC822 - never base64 - or Gmail delivers
    unreadable mail.
    """
    header = (
        f"--{BOUNDARY}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n"
        "\r\n"
        f"{thread_metadata_json(thread_id)}\r\n"
        f"--{BOUNDARY}\r\n"
        "Content-Type: message/rfc822\r\n"
        "\r\n"
    ).encode("ascii")
    footer = f"\r\n--{BOUNDARY}--".encode("ascii")
    return (f'multipart/related; boundary="{BOUNDARY}"',
            header + bytes(rfc822) + footer)


def urllib_transport(method: str, url: str, headers: dict,
                     body: bytes | None) -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method=method.upper())
    try:
        with urllib.request.urlopen(request,
                                    timeout=READ_TIMEOUT) as response:
            return HttpResponse(status=int(response.status),
                                body=response.read(),
                                headers=dict(response.headers.items()))
    except urllib.error.HTTPError as exc:
        # Error replies carry the headers too: Retry-After lives here.
        try:
            error_headers = dict(exc.headers.items())
        except AttributeError:
            error_headers = {}
        return HttpResponse(status=int(exc.code), body=exc.read(),
                            headers=error_headers)
    except OSError as exc:
        raise GmailTransportError(f"Network error: {exc}") from exc


def parse_retry_after(headers: dict) -> float | None:
    """Seconds from a Retry-After header, or None when absent/unparsable."""
    for key, value in (headers or {}).items():
        if str(key).lower() != "retry-after":
            continue
        try:
            return max(0.0, float(str(value).strip()))
        except ValueError:
            return None
    return None


class GmailClient:
    """One thin function per Gmail endpoint, Bearer [REDACTED] per request.

    get_token supplies the current access token; refresh_token takes the
    rejected token and returns a fresh one (or raises ReauthNeeded).
    When refresh_token is None, any 401 raises ReauthNeeded directly.
    """

    def __init__(self, transport=urllib_transport, get_token=None,
                 refresh_token=None,
                 base_url: str = BASE_URL,
                 upload_base_url: str = UPLOAD_BASE_URL,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 sleep=time.sleep,
                 jitter=None) -> None:
        self.transport = transport
        self.get_token = get_token or (lambda: "")
        self.refresh_token = refresh_token
        self.base_url = base_url
        self.upload_base_url = upload_base_url
        self.max_retries = max_retries
        self._sleep = sleep
        self._jitter = jitter or (lambda: random.uniform(0, 1))

    def _retry_delay(self, headers: dict, attempt: int) -> float:
        waited = parse_retry_after(headers)
        if waited is None:
            waited = BACKOFF_BASE_SECONDS * (2 ** attempt)
        return waited + self._jitter()

    @staticmethod
    def _retriable(status: int) -> bool:
        return status == 429 or 500 <= status < 600

    def _request(self, method: str, path: str,
                 params: dict | None = None,
                 json_body: dict | None = None,
                 raw_body: tuple[str, bytes] | None = None,
                 upload: bool = False,
                 max_retries: int | None = None) -> dict:
        base = self.upload_base_url if upload else \
            self.base_url + "gmail/v1/users/me/"
        url = base + path
        if params:
            cleaned = [(k, v) for k, v in params.items()
                       if v is not None]
            if cleaned:
                url += "?" + urllib.parse.urlencode(cleaned, doseq=True)
        if raw_body is not None:
            content_type, data = raw_body
        elif json_body is not None:
            content_type, data = ("application/json",
                                  json.dumps(json_body).encode("utf-8"))
        else:
            content_type, data = ("application/json", None)
        token = self.get_token()
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": content_type}
        budget = self.max_retries if max_retries is None else \
            max_retries
        authed_once = False
        rate_attempt = 0
        while True:
            payload = self._send(method, url, headers, data)
            if payload.status == 401:
                if authed_once or self.refresh_token is None:
                    raise ReauthNeeded("Access rejected; sign in again.")
                fresh = self.refresh_token(token)
                if fresh == token:
                    raise ReauthNeeded("Access rejected; sign in again.")
                token = fresh
                headers = {"Authorization": f"Bearer {fresh}",
                           "Content-Type": content_type}
                authed_once = True
                continue
            if self._retriable(payload.status):
                if rate_attempt >= budget:
                    return self._decode(payload, url)
                self._sleep(self._retry_delay(payload.headers,
                                              rate_attempt))
                rate_attempt += 1
                continue
            return self._decode(payload, url)

    def _send(self, method: str, url: str, headers: dict,
              data: bytes | None, retried: bool = False) -> HttpResponse:
        try:
            return self.transport(method, url, headers, data)
        except ReauthNeeded:
            raise
        except GmailTransportError:
            raise
        except Exception as exc:
            raise GmailTransportError(f"Network error: {exc}") from exc

    @staticmethod
    def _decode(payload: HttpResponse, url: str) -> dict:
        if 200 <= payload.status < 300:
            if not payload.body:
                return {}
            try:
                decoded = json.loads(payload.body.decode("utf-8"))
            except ValueError as exc:
                raise GmailTransportError(
                    f"Undecodable response from {url}",
                    status=payload.status) from exc
            return decoded if isinstance(decoded, dict) else {}
        raise GmailTransportError(f"Gmail request failed ({payload.status})",
                                  status=payload.status)

    def get_profile(self) -> Profile:
        return parse_profile(self._request("GET", "profile"))

    def list_labels(self) -> list[GmailLabel]:
        payload = self._request("GET", "labels")
        labels = []
        for raw in payload.get("labels", []):
            if not isinstance(raw, dict):
                continue
            color = raw.get("color") or {}
            labels.append(GmailLabel(
                id=_str(raw.get("id")), name=_str(raw.get("name")),
                type=raw.get("type"),
                color_bg=color.get("background"),
                color_text=color.get("text")))
        return labels

    def list_threads(self, query: str | None = None,
                     label_ids: list | None = None, max_results: int = 50,
                     page_token: str | None = None,
                     include_spam_trash: bool = False) -> ThreadListPage:
        return parse_thread_list(self._request(
            "GET", "threads",
            {"q": query, "labelIds": label_ids, "maxResults": max_results,
             "pageToken": page_token,
             "includeSpamTrash": str(include_spam_trash).lower()}))

    def get_thread(self, thread_id: str,
                   fmt: str = "metadata") -> GmailThread:
        return _parse_thread(self._request(
            "GET", f"threads/{urllib.parse.quote(thread_id, safe='')}",
            {"format": fmt}))

    def modify_thread(self, thread_id: str, add: list | None = None,
                      remove: list | None = None) -> GmailThread:
        return _parse_thread(self._request(
            "POST", f"threads/{urllib.parse.quote(thread_id, safe='')}/modify",
            json_body={"addLabelIds": add, "removeLabelIds": remove}))

    def trash_thread(self, thread_id: str) -> GmailThread:
        return _parse_thread(self._request(
            "POST",
            f"threads/{urllib.parse.quote(thread_id, safe='')}/trash"))

    def untrash_thread(self, thread_id: str) -> GmailThread:
        return _parse_thread(self._request(
            "POST",
            f"threads/{urllib.parse.quote(thread_id, safe='')}/untrash"))

    def get_message(self, message_id: str,
                    fmt: str = "full") -> Message:
        return _parse_message(self._request(
            "GET", f"messages/{urllib.parse.quote(message_id, safe='')}",
            {"format": fmt}))

    def modify_message(self, message_id: str, add: list | None = None,
                       remove: list | None = None) -> Message:
        return _parse_message(self._request(
            "POST",
            f"messages/{urllib.parse.quote(message_id, safe='')}/modify",
            json_body={"addLabelIds": add, "removeLabelIds": remove}))

    def send_raw(self, rfc822: bytes) -> Message:
        return _parse_message(self._request(
            "POST", "messages/send?uploadType=media",
            raw_body=media_body(rfc822), upload=True,
            max_retries=0))

    def send_multipart(self, rfc822: bytes, thread_id: str) -> Message:
        return _parse_message(self._request(
            "POST", "messages/send?uploadType=multipart",
            raw_body=multipart_body(rfc822, thread_id), upload=True,
            max_retries=0))

    def send(self, rfc822: bytes, thread_id: str | None = None) -> Message:
        """Media upload for fresh sends, multipart when threading a reply."""
        if thread_id:
            return self.send_multipart(rfc822, thread_id)
        return self.send_raw(rfc822)

    def get_attachment(self, message_id: str,
                       attachment_id: str) -> Attachment:
        payload = self._request(
            "GET",
            f"messages/{urllib.parse.quote(message_id, safe='')}"
            f"/attachments/{urllib.parse.quote(attachment_id, safe='')}")
        size = payload.get("size")
        return Attachment(attachment_id=payload.get("attachmentId"),
                          filename=payload.get("filename"),
                          size=size if isinstance(size, int) else 0,
                          data=payload.get("data"))

    def list_history(self, start_history_id: str, max_results: int = 100,
                     page_token: str | None = None) -> HistoryPage:
        return parse_history(self._request(
            "GET", "history",
            {"startHistoryId": start_history_id,
             "maxResults": max_results, "pageToken": page_token}))

    def get_vacation(self) -> VacationSettings:
        payload = self._request("GET", "settings/vacation")
        return VacationSettings(
            enable_auto_reply=bool(payload.get("enableAutoReply", False)),
            end_time=payload.get("endTime")
            if isinstance(payload.get("endTime"), int) else None,
            start_time=payload.get("startTime")
            if isinstance(payload.get("startTime"), int) else None,
            response_subject=payload.get("responseSubject"),
            response_body_html=payload.get("responseBodyHtml"))

    def list_send_as(self) -> list[SendAsEntry]:
        payload = self._request("GET", "settings/sendAs")
        return [SendAsEntry(
            send_as_email=_str(raw.get("sendAsEmail")),
            display_name=raw.get("displayName"),
            is_default=bool(raw.get("isDefault", False)),
            is_primary=bool(raw.get("isPrimary", False)))
            for raw in payload.get("sendAs", []) if isinstance(raw, dict)]
