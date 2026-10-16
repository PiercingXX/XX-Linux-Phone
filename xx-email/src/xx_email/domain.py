"""Pure domain ports from the Android app (stdlib only, no GI/GTK).

Ports dev.xxemail.domain (Folders, Recipients, Fts, LabelCsv,
SafePaths, RemoteImagePolicy), dev.xxemail.sync (NewMailDetector,
OutboxSend, OutboxFiles, SendRetryPolicy, SendCancelPolicy) and
dev.xxemail.data.api (MessageParts, ThreadAggregation) from
ref/xx-email. Every symbol here is JVM-unit-testable on Android and
stays pytest-testable here: no network, no disk outside the passed
directories, no imports beyond the standard library.
"""

from __future__ import annotations

import email.utils
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path


class OutboxKind(str, Enum):
    """Send flavors. Values match the stored row strings."""

    SEND = "SEND"
    SCHEDULED_SEND = "SCHEDULED_SEND"
    SNOOZE_WAKE = "SNOOZE_WAKE"


class OutboxState(str, Enum):
    """Outbox row lifecycle. Values match the stored row strings."""

    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    WOKEN = "WOKEN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OutboxFiles:
    """File-backed storage for outbox RFC822 payloads.

    Bytes live under ``files_dir/outbox/{id}.eml`` so rows never carry
    megabytes of payload. Stored paths stay relative to ``files_dir``
    and are validated on every read.
    """

    DIR_NAME = "outbox"
    EXT = ".eml"
    TMP_EXT = ".eml.tmp"

    @staticmethod
    def relative_path(entry_id: int) -> str:
        return f"{OutboxFiles.DIR_NAME}/{entry_id}{OutboxFiles.EXT}"

    @staticmethod
    def write_new(files_dir: str | os.PathLike, entry_id: int,
                  payload: bytes) -> str:
        """Durably write the payload (tmp plus rename)."""
        outbox_dir = Path(files_dir) / OutboxFiles.DIR_NAME
        outbox_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(outbox_dir, 0o700)
        except OSError:
            pass
        target = outbox_dir / f"{entry_id}{OutboxFiles.EXT}"
        if not SafePaths.is_inside(outbox_dir, target):
            raise ValueError("Refusing to write outside "
                             f"{OutboxFiles.DIR_NAME}")
        tmp = outbox_dir / f"{entry_id}{OutboxFiles.TMP_EXT}"
        try:
            tmp.write_bytes(bytes(payload))
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()
        return OutboxFiles.relative_path(entry_id)

    @staticmethod
    def resolve(files_dir: str | os.PathLike,
                stored_path: str | None) -> Path | None:
        """Stored relative path to an existing file inside outbox."""
        if not stored_path or not stored_path.strip():
            return None
        if ".." in stored_path or stored_path.startswith("/"):
            return None
        outbox_dir = Path(files_dir) / OutboxFiles.DIR_NAME
        candidate = Path(files_dir) / stored_path
        if not SafePaths.is_inside(outbox_dir, candidate):
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def delete_payload_file(files_dir: str | os.PathLike,
                            stored_path: str | None,
                            entry_id: int) -> None:
        """Best-effort delete of a row's payload file and stale tmp."""
        resolved = OutboxFiles.resolve(
            files_dir, stored_path or OutboxFiles.relative_path(
                entry_id))
        if resolved is not None:
            resolved.unlink(missing_ok=True)
        stale = Path(files_dir) / OutboxFiles.DIR_NAME / \
            f"{entry_id}{OutboxFiles.TMP_EXT}"
        stale.unlink(missing_ok=True)


@dataclass
class OutboxSendPlan:
    """Enqueue math: kind, fire time, and worker delay."""

    kind: OutboxKind
    target_at: int
    initial_delay_ms: int
    requires_connected_network: bool = True
    state: OutboxState = OutboxState.QUEUED


@dataclass
class OutboxDraft:
    """Values for a new outbox row (payload written separately)."""

    account_email: str
    kind: str
    thread_id: str | None
    subject: str
    target_at: int
    state: str
    created_at: int
    rfc822_base64: str | None = None


class OutboxSend:
    """Offline-compose enqueue math. Nothing here touches Gmail."""

    @staticmethod
    def plan(now_ms: int, undo_seconds: int,
             scheduled_at: int | None) -> OutboxSendPlan:
        kind = OutboxKind.SCHEDULED_SEND if scheduled_at is not None \
            else OutboxKind.SEND
        target_at = scheduled_at if scheduled_at is not None else \
            now_ms + max(undo_seconds, 0) * 1000
        return OutboxSendPlan(
            kind=kind,
            target_at=target_at,
            initial_delay_ms=max(target_at - now_ms, 0),
        )

    @staticmethod
    def entity(account_email: str, thread_id: str | None,
               subject: str, plan: OutboxSendPlan,
               now_ms: int) -> OutboxDraft:
        return OutboxDraft(
            account_email=account_email,
            kind=plan.kind.value,
            thread_id=thread_id,
            subject=subject,
            target_at=plan.target_at,
            state=OutboxState.QUEUED.value,
            created_at=now_ms,
        )

    @staticmethod
    def write_payload(files_dir: str | os.PathLike, entry_id: int,
                      payload: bytes) -> tuple[str, int]:
        """Write the RFC822 bytes; returns stored path and size."""
        data = bytes(payload)
        path = OutboxFiles.write_new(files_dir, entry_id, data)
        return (path, len(data))

    @staticmethod
    def queued_label(scheduled: bool) -> str:
        """Enqueue is success, even with no network."""
        return "Send scheduled" if scheduled else "Queued"

    @staticmethod
    def can_retry(state: str | None) -> bool:
        return state == OutboxState.FAILED.value

    @staticmethod
    def can_discard(state: str | None) -> bool:
        return state in (OutboxState.FAILED.value,
                         OutboxState.QUEUED.value)

    @staticmethod
    def is_open(state: str | None) -> bool:
        return state in (OutboxState.QUEUED.value,
                         OutboxState.FAILED.value,
                         OutboxState.SENDING.value)


class SendOutcome(str, Enum):
    """Outbox send retry decision."""

    MARK_SENT = "MARK_SENT"
    MARK_FAILED = "MARK_FAILED"
    RETRY = "RETRY"


class SendRetryPolicy:
    """Pure decision table for outbox send retries.

    A send that reached Gmail with HTTP 2xx must never be retried,
    even when the response body fails to decode.
    """

    MAX_ATTEMPTS = 5

    @staticmethod
    def decide(http_code: int | None,
               transport_error: bool) -> SendOutcome:
        if http_code is not None and 200 <= http_code <= 299:
            return SendOutcome.MARK_SENT
        if transport_error:
            return SendOutcome.RETRY
        if http_code == 429:
            return SendOutcome.RETRY
        if http_code is not None and 500 <= http_code <= 599:
            return SendOutcome.RETRY
        if http_code is not None and 400 <= http_code <= 499:
            return SendOutcome.MARK_FAILED
        return SendOutcome.RETRY


class SendCancelPolicy:
    """Undo gate for the claim-vs-undo race.

    The QUEUED to SENDING flip is a two-sided atomic compare on the
    row: whichever side wins, the other deterministically loses.
    """

    @staticmethod
    def can_undo(state: str | None) -> bool:
        """Only a still-queued row may be cancelled."""
        return state == OutboxState.QUEUED.value


class NewMailDetector:
    """Pure new-mail decision for notifications.

    Only a pass that resumed from an existing checkpoint may notify.
    First syncs and forced rebuilds see the whole mailbox and must
    never fire a wall of alerts.
    """

    @staticmethod
    def new_arrivals(known_inbox_ids_before: set[str] | None,
                     current_unread_inbox: list) -> list:
        """Currently-unread inbox threads absent from the snapshot."""
        if known_inbox_ids_before is None:
            return []
        known = set(known_inbox_ids_before)
        return [thread for thread in current_unread_inbox
                if getattr(thread, "id", None) not in known]


class MailboxFolder(Enum):
    """Mailbox views. Inbox tabs map to Gmail CATEGORY_* labels."""

    PRIMARY = ("Primary", "CATEGORY_PERSONAL", True, None)
    SOCIAL = ("Social", "CATEGORY_SOCIAL", False, None)
    PROMOTIONS = ("Promotions", "CATEGORY_PROMOTIONS", False, None)
    UPDATES = ("Updates", "CATEGORY_UPDATES", False, None)
    FORUMS = ("Forums", "CATEGORY_FORUMS", False, None)
    STARRED = ("Starred", None, False, None)
    SNOOZED = ("Snoozed", None, False, None)
    SENT = ("Sent", None, False, "SENT")
    OUTBOX = ("Outbox", None, False, None)
    DRAFTS = ("Drafts", None, False, "DRAFT")
    SPAM = ("Spam", None, False, "SPAM")
    TRASH = ("Trash", None, False, "TRASH")
    ALL_MAIL = ("All mail", None, False, None)

    def __init__(self, title: str, category: str | None,
                 include_empty_primary: bool, label_id: str | None,
                 ) -> None:
        self.title = title
        self.category = category
        self.include_empty_primary = include_empty_primary
        self.label_id = label_id


INBOX_TABS = [
    MailboxFolder.PRIMARY,
    MailboxFolder.SOCIAL,
    MailboxFolder.PROMOTIONS,
    MailboxFolder.UPDATES,
    MailboxFolder.FORUMS,
]


def server_label_filter(folder: MailboxFolder) -> list[str] | None:
    """Label filter used when hydrating a non-inbox folder."""
    if folder.label_id is None:
        return None
    return [folder.label_id]


class SnoozePresets:
    """Snooze preset math mirroring Gmail's UX constants."""

    @staticmethod
    def later_today(now: datetime) -> datetime:
        plus_four = now + timedelta(hours=4)
        six_pm = now.replace(hour=18, minute=0, second=0,
                             microsecond=0)
        chosen = min(plus_four, six_pm)
        if not chosen > now + timedelta(hours=2):
            return SnoozePresets.tomorrow_morning(now)
        return chosen

    @staticmethod
    def tomorrow_morning(now: datetime) -> datetime:
        return (now + timedelta(days=1)).replace(
            hour=8, minute=0, second=0, microsecond=0)

    @staticmethod
    def next_week(now: datetime) -> datetime:
        day = (now + timedelta(days=1)).date()
        while day.weekday() != 0:
            day += timedelta(days=1)
        return datetime(day.year, day.month, day.day, 8, 0,
                        tzinfo=now.tzinfo)

    @staticmethod
    def weekend(now: datetime) -> datetime:
        day = (now + timedelta(days=1)).date()
        while day.weekday() != 5:
            day += timedelta(days=1)
        return datetime(day.year, day.month, day.day, 8, 0,
                        tzinfo=now.tzinfo)


class AddressUtils:
    """Display name / address splitting for RFC headers."""

    @staticmethod
    def split(header_value: str) -> tuple[str, str]:
        """'Jane Doe <jane@x>' -> ('Jane Doe', 'jane@x')."""
        text = header_value or ""
        open_at = text.rfind("<")
        close_at = text.rfind(">")
        if 1 <= open_at < close_at:
            return (text[:open_at].strip().strip('"'),
                    text[open_at + 1:close_at].strip())
        return (text.strip(), text.strip())

    @staticmethod
    def initials(name_or_address: str) -> str:
        trimmed = (name_or_address or "").strip()
        if not trimmed:
            return "?"
        if "@" in trimmed:
            local = trimmed.split("@", 1)[0]
            words = [w for w in re.split(r"[._\-+]+", local) if w]
            if len(words) >= 2:
                result = "".join(w[0].upper() for w in words[:2])
            else:
                result = local[:2].upper()
        else:
            words = [w for w in re.split(r"\s+", trimmed) if w]
            if len(words) >= 2:
                result = "".join(w[0].upper() for w in words[:2])
            else:
                result = words[0][:2].upper()
        return result or "?"


_BRACKETED_RE = re.compile(r"<([^<>]+@[^<>]+)>")
_BARE_ADDRESS_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PLAUSIBLE_RE = re.compile(r"^[^<>\s,;]+@[^<>\s,;]+$")


def _salvage_addresses(text: str) -> list[str]:
    """Bracketed addresses first, then bare email-shaped tokens."""
    bracketed = [m.group(1).strip()
                 for m in _BRACKETED_RE.finditer(text or "")]
    bracketed = [addr for addr in bracketed if "@" in addr]
    if bracketed:
        return bracketed
    return [m.group(0) for m in _BARE_ADDRESS_RE.finditer(text or "")]


class Recipients:
    """Pure reply / reply-all recipient-set computation.

    Lists are parsed leniently so a quoted display name containing a
    comma ('"Doe, Jane" <jane@x>') stays one address. A wholly
    unparseable list degrades to bracket/bare-address salvage, never
    to dropping every recipient.
    """

    @staticmethod
    def parse(header_value: str) -> list[str]:
        """Bare addresses from an RFC 5322 list. Never throws."""
        if not header_value or not header_value.strip():
            return []
        try:
            pairs = email.utils.getaddresses([header_value])
        except Exception:
            pairs = []
        parsed: list[str] = []
        for name, address in pairs:
            bare = (address or "").strip()
            if bare and _PLAUSIBLE_RE.match(bare):
                parsed.append(bare)
            elif bare:
                salvaged = _salvage_addresses(bare)
                parsed.extend(salvaged if salvaged else [bare])
            else:
                parsed.extend(_salvage_addresses(name or ""))
        if parsed:
            return parsed
        return _salvage_addresses(header_value)

    @staticmethod
    def parse_validated(raw: str) -> list[str]:
        """Send-time gate: keep only plausible addresses."""
        return [addr for addr in Recipients.parse(raw) if "@" in addr]

    @staticmethod
    def _is_self(address: str, account: str) -> bool:
        return bool(account.strip()) and \
            address.lower() == account.lower()

    @staticmethod
    def reply_to(from_header: str, to_header: str,
                 account: str) -> list[str]:
        """Reply: To is the original From unless that is us."""
        sender = Recipients.parse(from_header)
        if any(Recipients._is_self(addr, account) for addr in sender):
            return Recipients.parse(to_header)
        return sender

    @staticmethod
    def reply_all(from_header: str, to_header: str,
                  cc_header: str, account: str,
                  ) -> tuple[list[str], list[str]]:
        """Reply-all: To is From plus original To, Cc minus overlaps."""
        sender = Recipients.parse(from_header)
        original_to = Recipients.parse(to_header)
        carbon = Recipients.parse(cc_header)
        to_list: list[str] = []
        seen_to: set[str] = set()
        for addr in sender + original_to:
            if Recipients._is_self(addr, account):
                continue
            if addr.lower() in seen_to:
                continue
            seen_to.add(addr.lower())
            to_list.append(addr)
        cc_list: list[str] = []
        seen_cc: set[str] = set()
        for addr in carbon:
            if Recipients._is_self(addr, account):
                continue
            if addr.lower() in seen_to or addr.lower() in seen_cc:
                continue
            seen_cc.add(addr.lower())
            cc_list.append(addr)
        return (to_list, cc_list)


class FtsEscaper:
    """Turns free text into a safe FTS MATCH prefix query.

    Raw input never reaches MATCH: every whitespace-separated token
    becomes a double-quoted literal phrase with a trailing prefix
    ``*``, so AND/OR/NOT and punctuation cannot throw
    malformed-match errors.
    """

    @staticmethod
    def escape_prefix_query(raw: str) -> str:
        parts = []
        for token in re.split(r"\s+", raw or ""):
            cleaned = token.replace('"', "")
            if cleaned:
                parts.append('"' + cleaned + '"*')
        return " ".join(parts)


class LabelCsv:
    """Exact-token matching/editing for comma-joined label id lists."""

    @staticmethod
    def contains(csv: str, label: str) -> bool:
        if not label:
            return False
        return ("," + (csv or "").strip(",") + ",").find(
            "," + label + ",") >= 0

    @staticmethod
    def tokens(csv: str) -> list[str]:
        return [tok.strip() for tok in (csv or "").split(",")
                if tok.strip()]

    @staticmethod
    def add(csv: str, label: str) -> str:
        if not label or LabelCsv.contains(csv, label):
            return csv
        return ",".join(LabelCsv.tokens(csv) + [label])

    @staticmethod
    def remove(csv: str, label: str) -> str:
        return ",".join(tok for tok in LabelCsv.tokens(csv)
                        if tok != label)


class SafePaths:
    """Filename/path sanitization for anything saved to disk."""

    MAX_NAME_LENGTH = 200

    @staticmethod
    def child_name(raw: str | None) -> str | None:
        """Reduce input to a safe single path segment, else None."""
        if not raw:
            return None
        if "\x00" in raw:
            return None
        name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
        name = name.rstrip(".")
        if not name or name in (".", ".."):
            return None
        return name[:SafePaths.MAX_NAME_LENGTH]

    @staticmethod
    def child_name_or(raw: str | None, fallback_seed: str | None,
                      last_resort: str) -> str:
        """Sanitized raw, else sanitized seed, else last_resort."""
        return SafePaths.child_name(raw) or \
            SafePaths.child_name(fallback_seed) or last_resort

    @staticmethod
    def is_inside(directory: str | os.PathLike,
                  candidate: str | os.PathLike) -> bool:
        """True only when candidate resolves strictly inside dir."""
        dir_path = os.path.realpath(directory)
        cand_path = os.path.realpath(candidate)
        return cand_path.startswith(dir_path + os.sep)


class RemoteImagePolicy:
    """HTTPS-only gate for optional remote images in HTML bodies."""

    MAX_BYTES = 2 * 1024 * 1024

    @staticmethod
    def is_https_url(source: str) -> bool:
        if not source or not source.strip():
            return False
        try:
            from urllib.parse import urlsplit
            parts = urlsplit(source.strip())
        except ValueError:
            return False
        return parts.scheme.lower() == "https" and \
            bool(parts.hostname)


@dataclass
class FoundAttachment:
    """One downloadable part: filename plus attachment id, any depth."""

    message_id: str
    attachment_id: str
    filename: str
    mime_type: str = ""
    size: int = 0


class MessageParts:
    """Pure recursive walkers over message payloads, any nesting."""

    @staticmethod
    def has_attachment(message) -> bool:
        payload = getattr(message, "payload", None)
        return MessageParts.has_attachment_in(payload) \
            if payload is not None else False

    @staticmethod
    def has_attachment_in(part) -> bool:
        if part is None:
            return False
        if getattr(part, "filename", None):
            return True
        return any(MessageParts.has_attachment_in(child)
                   for child in (getattr(part, "parts", None) or []))

    @staticmethod
    def find_body(root, mime: str):
        """First part of type mime carrying data, depth-first."""
        if root is None:
            return None
        body = getattr(root, "body", None)
        if getattr(root, "mime_type", None) == mime and \
                body is not None and \
                getattr(body, "data", None) is not None:
            return root
        for child in (getattr(root, "parts", None) or []):
            hit = MessageParts.find_body(child, mime)
            if hit is not None:
                return hit
        return None

    @staticmethod
    def attachments(message_id: str, root) -> list[FoundAttachment]:
        """Every filename plus attachment-id part, any depth."""
        found: list[FoundAttachment] = []

        def walk(part) -> None:
            body = getattr(part, "body", None)
            filename = getattr(part, "filename", None)
            attach_id = getattr(body, "attachment_id", None) \
                if body is not None else None
            if filename and attach_id:
                found.append(FoundAttachment(
                    message_id=message_id,
                    attachment_id=attach_id,
                    filename=filename,
                    mime_type=getattr(part, "mime_type", None) or "",
                    size=getattr(body, "size", 0) or 0,
                ))
            for child in (getattr(part, "parts", None) or []):
                walk(child)

        if root is not None:
            walk(root)
        return found


def _to_long(value) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _header_of(message, name: str) -> str:
    payload = getattr(message, "payload", None)
    headers = getattr(payload, "headers", None) or []
    for header in headers:
        if str(getattr(header, "name", "")).lower() == name.lower():
            return str(getattr(header, "value", "") or "")
    return ""


@dataclass
class ThreadAggregate:
    """Denormalized conversation row rebuilt from all its messages."""

    account_email: str
    id: str
    snippet: str = ""
    subject: str = ""
    from_address: str = ""
    from_name: str = ""
    date: int = 0
    message_count: int = 0
    unread_count: int = 0
    has_attachments: bool = False
    starred: bool = False
    in_inbox: bool = True
    categories: str = ""
    labels_csv: str = ""
    snoozed_until: int | None = None


@dataclass
class MessageAggregate:
    """Per-message row fields for the messages table."""

    account_email: str
    id: str
    thread_id: str
    subject: str = ""
    from_address: str = ""
    to_csv: str = ""
    cc_csv: str = ""
    date: int = 0
    snippet: str = ""
    read: bool = False
    starred: bool = False
    has_attachments: bool = False
    labels_csv: str = ""
    message_id_header: str | None = None


class ThreadAggregation:
    """Thread to aggregate mapping used by sync hydration.

    Aggregates union ALL messages' labels, not just the newest: after
    a reply the newest message carries only SENT, but the thread stays
    in the inbox while any message keeps INBOX.
    """

    @staticmethod
    def build(account_email: str, thread,
              snoozed_until: int | None = None) -> ThreadAggregate:
        messages = list(getattr(thread, "messages", None) or [])
        if not messages:
            return ThreadAggregate(
                account_email=account_email,
                id=getattr(thread, "id", ""),
                subject="(no subject)",
                in_inbox=False,
                snoozed_until=snoozed_until,
            )
        latest = max(messages,
                     key=lambda m: _to_long(getattr(m, "internal_date",
                                                   None)))
        union: list[str] = []
        for message in messages:
            for label in (getattr(message, "label_ids", None) or []):
                if label not in union:
                    union.append(label)
        from_header = _header_of(latest, "From")
        name, _address = AddressUtils.split(from_header)
        subject = _header_of(latest, "Subject")
        snippet = getattr(thread, "snippet", None) or \
            getattr(latest, "snippet", None) or ""
        return ThreadAggregate(
            account_email=account_email,
            id=getattr(thread, "id", ""),
            snippet=snippet,
            subject=subject if subject.strip() else "(no subject)",
            from_address=from_header,
            from_name=name,
            date=_to_long(getattr(latest, "internal_date", None)),
            message_count=len(messages),
            unread_count=sum(1 for m in messages
                             if "UNREAD" in
                             (getattr(m, "label_ids", None) or [])),
            has_attachments=any(MessageParts.has_attachment(m)
                                for m in messages),
            starred="STARRED" in union,
            in_inbox="INBOX" in union,
            categories=",".join(label for label in union
                                if label.startswith("CATEGORY_")),
            labels_csv=",".join(union),
            snoozed_until=snoozed_until,
        )

    @staticmethod
    def build_message(account_email: str, message,
                      fallback_thread_id: str) -> MessageAggregate:
        labels = list(getattr(message, "label_ids", None) or [])
        subject = _header_of(message, "Subject")
        message_id = _header_of(message, "Message-ID") or \
            _header_of(message, "Message-Id") or None
        return MessageAggregate(
            account_email=account_email,
            id=getattr(message, "id", ""),
            thread_id=getattr(message, "thread_id", None) or
            fallback_thread_id,
            subject=subject if subject.strip() else "(no subject)",
            from_address=_header_of(message, "From"),
            to_csv=_header_of(message, "To"),
            cc_csv=_header_of(message, "Cc"),
            date=_to_long(getattr(message, "internal_date", None)),
            snippet=getattr(message, "snippet", None) or "",
            read="UNREAD" not in labels,
            starred="STARRED" in labels,
            has_attachments=MessageParts.has_attachment(message),
            labels_csv=",".join(labels),
            message_id_header=message_id,
        )
