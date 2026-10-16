"""Canonical backup export/import.

Pure-Python port of the Android backup stack onto the L1 SQLite store:

- ``core/BackupData.kt`` + ``core/BackupSerializer.kt`` -> the canonical
  JSON shape ``{version, messages, settings, blocklist, starred}`` with its
  version gate (only :data:`BACKUP_VERSION` imports) and range gate
  (ids/dates must be non-negative integers). Parsing is strict: malformed
  JSON, trailing garbage and future versions raise :class:`BackupError`
  and never produce a partial model.
- ``backup/SmsBackupModels.kt`` + ``backup/SmsBackupCodec.kt`` ->
  :class:`SmsBackupPayload` and friends (the suite ``export/sms.json``
  shape) with strict decode, plus the ``mms/<id>/`` sibling-file
  convention shared with the Linux export.
- ``backup/SmsExportBudget.kt`` -> :func:`select_attachments` (newest
  first; once the budget breaks, that attachment and every older one is
  dropped, never a smaller-but-older gap fill).
- ``data/ConversationExporter.kt`` -> :func:`build_backup` (live rows
  onto the canonical payload, honest photo count, ISO-8601 stamp).
- ``data/RestoreService.kt`` + ``backup/PlatformSmsImport.kt`` ->
  :func:`plan_restore` / :func:`import_backup`: an idempotent restore
  matching on conversation key + body + 5-minute window. Backup ids are
  reused only when free; on collision a fresh live id is minted, so a
  restore never overwrites a live row and a re-import never duplicates.
  The whole restore runs inside one SQLite transaction with rollback, so
  refused input never half-lands.
- SMS Backup & Restore XML (``smses/sms``, ``mms/parts/part``) ->
  :func:`import_sbr_xml` through the same upsert path; MMS photo bytes
  come from embedded base64 ``data`` or a sibling directory.

No GTK, no D-Bus: stdlib plus the L1 :mod:`txxt.store` and
:mod:`txxt.phone_numbers` modules.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from txxt import phone_numbers
from txxt import store as store_mod

#: The schema version this build can import. Bump only on a breaking change.
BACKUP_VERSION = 1

#: Duplicate-match window: conversation key + body + this timestamp drift.
#: Ports ``PlatformSmsImport.MATCH_WINDOW_MS``.
MATCH_WINDOW_MS = 5 * 60 * 1000

#: A row whose id sits this far past its message time was imported or
#: restored, not delivered live. Ports ``IMPORTED_ID_GAP_MS``.
IMPORTED_ID_GAP_MS = 60 * 60 * 1000

#: Suite per-app plaintext export budget. Ports ``DEFAULT_BUDGET_BYTES``.
DEFAULT_BUDGET_BYTES = 2 * 1024 * 1024 * 1024

#: Raw ``Telephony.Mms.DATE`` values below this are seconds, above are
#: already milliseconds. Ports ``SECONDS_MILLIS_THRESHOLD``.
SECONDS_MILLIS_THRESHOLD = 10_000_000_000

#: SMS Backup & Restore ``type`` values (mirrors ``Telephony.Sms``).
SMS_TYPE_INBOX = 1
SMS_TYPE_SENT = 2

#: SMS Backup & Restore ``msg_box`` values for MMS (mirrors ``Telephony.Mms``).
MMS_BOX_INBOX = 1
MMS_BOX_SENT = 2

#: Extension key carrying the kind-preserving blocklist next to the flat
#: Android-compatible ``blocklist`` list. Android readers ignore unknown
#: top-level keys, so interchange of the text sections is unaffected.
BLOCKLIST_DETAILED_KEY = "blocklistDetailed"

#: Per-message key listing that message's exported photo siblings,
#: relative to the backup directory (``mms/<id>/<file>``). Android readers
#: ignore unknown message fields, so text interchange is unaffected.
PHOTOS_KEY = "photos"


class BackupError(ValueError):
    """Refused backup input: garbage, future version, or range violation."""


def _reject_constant(value: str) -> object:
    raise BackupError(f"invalid JSON literal: {value!r}")


def _parse_json(text: str, what: str) -> object:
    """Strict-parse ``text``, refusing malformed input and trailing garbage."""
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except BackupError:
        raise
    except ValueError as err:
        message = str(err)
        if "Extra data" in message:
            raise BackupError(f"trailing characters after JSON value: {message}") from err
        raise BackupError(f"invalid {what}: {message}") from err


def _require_non_negative_number(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BackupError(f'field {field!r} must be a number')
    if value < 0:
        raise BackupError(f'field {field!r} must be non-negative')
    return value


def serialize(data: dict) -> str:
    """Render ``data`` to the canonical backup JSON string.

    Canonical-first and legacy-tolerant: canonical keys keep their shape
    so Android and Linux backups interchange, while the L1 ``conversations``
    section and ``thread_id`` spelling still round-trip.
    """
    if not isinstance(data, dict):
        raise BackupError("backup payload must be an object")
    payload: dict = {"version": BACKUP_VERSION}
    for key in ("conversations", "messages", "settings", "blocklist", "starred",
                BLOCKLIST_DETAILED_KEY):
        if key in data:
            payload[key] = data[key]
    payload.setdefault("messages", [])
    payload.setdefault("settings", {})
    payload.setdefault("blocklist", [])
    payload.setdefault("starred", [])
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def deserialize(text: str) -> dict:
    """Parse and validate a backup JSON string.

    Enforces the version gate (only :data:`BACKUP_VERSION`), the range gate
    (message ids, thread ids and dates are non-negative integers) and strict
    JSON (no malformed input, no trailing garbage). Raises
    :class:`BackupError` on any violation; never returns a partial model.
    """
    tree = _parse_json(text, "JSON")
    if not isinstance(tree, dict):
        raise BackupError('backup root must be an object')
    version = tree.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise BackupError("backup is missing a numeric version")
    if version != BACKUP_VERSION:
        raise BackupError(f"backup version {version} is not supported (expected {BACKUP_VERSION})")
    raw_messages = tree.get("messages")
    if not isinstance(raw_messages, list):
        raise BackupError("backup is missing a messages array")
    messages = []
    for entry in raw_messages:
        if not isinstance(entry, dict):
            raise BackupError('message entries must be objects')
        _require_non_negative_number(entry.get("id"), "message.id")
        _require_non_negative_number(
            entry.get("threadId", entry.get("thread_id")), "message.threadId"
        )
        _require_non_negative_number(entry.get("date"), "message.date")
        if not isinstance(entry.get("body"), str):
            raise BackupError("message.body must be a string")
        if not isinstance(entry.get("address"), str):
            raise BackupError("message.address must be a string")
        messages.append(entry)
    raw_conversations = tree.get("conversations", [])
    if not isinstance(raw_conversations, list):
        raise BackupError("conversations must be a list")
    for conv in raw_conversations:
        if not isinstance(conv, dict):
            raise BackupError('conversation entries must be objects')
        _require_non_negative_number(conv.get('id', 0), 'conversations[].id')
    settings = tree.get("settings", {})
    if not isinstance(settings, dict):
        raise BackupError("backup is missing a settings object")
    for key, value in settings.items():
        if not isinstance(value, str):
            raise BackupError(f"setting {key!r} must be a string")
    blocklist = tree.get("blocklist", [])
    if isinstance(blocklist, list):
        for entry in blocklist:
            if not isinstance(entry, str):
                raise BackupError("blocklist entries must be strings")
    elif not isinstance(blocklist, dict):
        raise BackupError("backup is missing a blocklist array")
    starred = tree.get("starred", [])
    if not isinstance(starred, list) or any(not isinstance(s, str) for s in starred):
        raise BackupError("backup is missing a starred array")
    detailed = tree.get(BLOCKLIST_DETAILED_KEY)
    if detailed is not None and not isinstance(detailed, list):
        raise BackupError("blocklistDetailed must be an array")
    result = {
        'version': BACKUP_VERSION,
        'messages': messages,
        'settings': settings,
        'blocklist': blocklist,
        'starred': starred,
    }
    if "conversations" in tree:
        result['conversations'] = raw_conversations
    if detailed is not None:
        result[BLOCKLIST_DETAILED_KEY] = detailed
    return result


def message_key(address: str, date_millis: int, body: str) -> tuple:
    return (address, int(date_millis), body)


def deduplicate(messages: list, known_keys: set) -> list:
    fresh = []
    for msg in messages:
        key = message_key(
            str(msg.get('address', '')),
            int(msg.get('date', 0)),
            str(msg.get('body', '')),
        )
        if key in known_keys:
            continue
        known_keys.add(key)
        fresh.append(msg)
    return fresh


@dataclass(frozen=True)
class SmsRecord:
    """One ``content://sms`` row in the ``export/sms.json`` shape."""

    address: str
    date: int
    date_sent: int
    type: int
    read: bool
    body: str
    thread_id: int


@dataclass(frozen=True)
class MmsTextPart:
    """An inline text part of an MMS, stored verbatim in the JSON."""

    content_type: str
    text: str


@dataclass(frozen=True)
class MmsPartRef:
    """A binary MMS part exported alongside the JSON."""

    part_id: int
    ext: str
    content_type: str

    def export_path(self, mms_id: int) -> str:
        """The ``export/``-relative path of this part."""
        return f"mms/{mms_id}/{self.part_id}.{self.ext}"


@dataclass(frozen=True)
class MmsRecord:
    """One ``content://mms`` row plus its addr/part sub-tables."""

    mms_id: int
    addresses: tuple[str, ...] = ()
    date: int = 0
    read: bool = True
    message_box: int = MMS_BOX_INBOX
    thread_id: int = 0
    text_parts: tuple[MmsTextPart, ...] = ()
    parts: tuple[MmsPartRef, ...] = ()


@dataclass(frozen=True)
class SmsBackupPayload:
    """The full ``export/sms.json`` payload."""

    sms: tuple[SmsRecord, ...] = ()
    mms: tuple[MmsRecord, ...] = ()
    truncated: bool = False
    note: str | None = None


def encode_sms_payload(payload: SmsBackupPayload) -> str:
    """Render ``payload`` to its canonical ``sms.json`` string."""
    tree = {
        "sms": [
            {
                "address": record.address,
                "date": record.date,
                "dateSent": record.date_sent,
                "type": record.type,
                "read": record.read,
                "body": record.body,
                "threadId": record.thread_id,
            }
            for record in payload.sms
        ],
        "mms": [
            {
                "mmsId": record.mms_id,
                "addresses": list(record.addresses),
                "date": record.date,
                "read": record.read,
                "messageBox": record.message_box,
                "threadId": record.thread_id,
                "textParts": [
                    {"contentType": part.content_type, "text": part.text}
                    for part in record.text_parts
                ],
                "parts": [
                    {
                        "partId": part.part_id,
                        "ext": part.ext,
                        "contentType": part.content_type,
                    }
                    for part in record.parts
                ],
            }
            for record in payload.mms
        ],
        "truncated": payload.truncated,
        "note": payload.note,
    }
    return json.dumps(tree, separators=(",", ":"), ensure_ascii=False)


def _sms_string(entry: dict, field: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str):
        raise BackupError(f"sms.json {field} must be a string")
    return value


def _sms_number(entry: dict, field: str) -> int:
    value = entry.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BackupError(f"sms.json {field} must be a number")
    return value


def _sms_bool(entry: dict, field: str) -> bool:
    value = entry.get(field)
    if not isinstance(value, bool):
        raise BackupError(f"sms.json {field} must be a boolean")
    return value


def decode_sms_payload(text: str) -> SmsBackupPayload:
    """Parse an ``sms.json`` string, refusing malformed input.

    Mirrors ``SmsBackupCodec.decode``: missing ``sms``/``mms`` arrays and
    mistyped fields raise :class:`BackupError`. The codec itself has no
    range gate (faithful to Android); the canonical backup path does.
    """
    tree = _parse_json(text, "sms.json")
    if not isinstance(tree, dict):
        raise BackupError("sms.json root must be an object")
    raw_sms = tree.get("sms")
    if not isinstance(raw_sms, list):
        raise BackupError("sms.json is missing an sms array")
    raw_mms = tree.get("mms")
    if not isinstance(raw_mms, list):
        raise BackupError("sms.json is missing an mms array")
    sms = []
    for entry in raw_sms:
        if not isinstance(entry, dict):
            raise BackupError("sms entry must be an object")
        sms.append(
            SmsRecord(
                address=_sms_string(entry, "address"),
                date=_sms_number(entry, "date"),
                date_sent=_sms_number(entry, "dateSent"),
                type=_sms_number(entry, "type"),
                read=_sms_bool(entry, "read"),
                body=_sms_string(entry, "body"),
                thread_id=_sms_number(entry, "threadId"),
            )
        )
    mms = []
    for entry in raw_mms:
        if not isinstance(entry, dict):
            raise BackupError("mms entry must be an object")
        raw_addresses = entry.get("addresses")
        if not isinstance(raw_addresses, list):
            raise BackupError("mms entry is missing addresses")
        addresses = []
        for item in raw_addresses:
            if not isinstance(item, str):
                raise BackupError("mms.addresses entries must be strings")
            addresses.append(item)
        text_parts = []
        raw_text = entry.get("textParts", [])
        if not isinstance(raw_text, list):
            raise BackupError("mms.textParts must be an array")
        for node in raw_text:
            if not isinstance(node, dict):
                raise BackupError("mms.textParts entry must be an object")
            text_parts.append(
                MmsTextPart(
                    content_type=_sms_string(node, "contentType"),
                    text=_sms_string(node, "text"),
                )
            )
        parts = []
        raw_parts = entry.get("parts", [])
        if not isinstance(raw_parts, list):
            raise BackupError("mms.parts must be an array")
        for node in raw_parts:
            if not isinstance(node, dict):
                raise BackupError("mms.parts entry must be an object")
            parts.append(
                MmsPartRef(
                    part_id=_sms_number(node, "partId"),
                    ext=_sms_string(node, "ext"),
                    content_type=_sms_string(node, "contentType"),
                )
            )
        mms.append(
            MmsRecord(
                mms_id=_sms_number(entry, "mmsId"),
                addresses=tuple(addresses),
                date=_sms_number(entry, "date"),
                read=_sms_bool(entry, "read"),
                message_box=_sms_number(entry, "messageBox"),
                thread_id=_sms_number(entry, "threadId"),
                text_parts=tuple(text_parts),
                parts=tuple(parts),
            )
        )
    truncated = tree.get("truncated", False)
    if not isinstance(truncated, bool):
        truncated = False
    note = tree.get("note")
    if note is not None and not isinstance(note, str):
        note = None
    return SmsBackupPayload(sms=tuple(sms), mms=tuple(mms), truncated=truncated, note=note)


def mms_date_to_millis(raw: int) -> int:
    """Normalize a raw ``Telephony.Mms.DATE`` value to epoch millis."""
    return raw * 1000 if raw < SECONDS_MILLIS_THRESHOLD else raw


def millis_to_mms_date_seconds(millis: int) -> int:
    """Convert epoch millis back to the seconds the MMS store keeps."""
    return millis // 1000


def extension_for_mime(mime: str) -> str:
    """Best-effort file extension for an MMS part's content type."""
    lowered = (mime or "").lower()
    if "png" in lowered:
        return "png"
    if "gif" in lowered:
        return "gif"
    if "webp" in lowered:
        return "webp"
    if "heic" in lowered or "heif" in lowered:
        return "heic"
    if "bmp" in lowered:
        return "bmp"
    if "3gp" in lowered or "mp4" in lowered or lowered.startswith("video/"):
        return "3gp"
    if "jpeg" in lowered or "jpg" in lowered:
        return "jpg"
    return "bin"


def mime_for_extension(ext: str) -> str:
    """Best-effort content type restored from a part file's extension."""
    return {
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "heic": "image/heic",
        "heif": "image/heic",
        "bmp": "image/bmp",
        "3gp": "video/3gpp",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }.get((ext or "").lower(), "application/octet-stream")


@dataclass(frozen=True)
class AttachmentKey:
    """Identifies one binary MMS part across the export pair."""

    mms_id: int
    part_id: int


@dataclass(frozen=True)
class Attachment:
    """One candidate binary attachment: identity, capture date, size."""

    key: AttachmentKey
    date_millis: int
    size_bytes: int


@dataclass(frozen=True)
class Selection:
    """Which attachments survive the budget, and what was dropped."""

    kept: frozenset[AttachmentKey]
    truncated: bool
    dropped_count: int
    dropped_bytes: int


def select_attachments(
    base_bytes: int,
    attachments: list[Attachment],
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
) -> Selection:
    """Keep the newest attachments that fit in ``budget_bytes``.

    Ports ``SmsExportBudget.select``: newest first, accepted while the
    running total (starting from ``base_bytes``) stays within budget. The
    moment one does not fit, it and every older attachment are dropped, so
    survivors are always a newest prefix, never a gap-filling older item.
    """
    newest_first = sorted(attachments, key=lambda item: item.date_millis, reverse=True)
    kept: set[AttachmentKey] = set()
    running = base_bytes
    out_of_budget = False
    dropped_count = 0
    dropped_bytes = 0
    for attachment in newest_first:
        if out_of_budget:
            dropped_count += 1
            dropped_bytes += attachment.size_bytes
            continue
        if running + attachment.size_bytes <= budget_bytes:
            kept.add(attachment.key)
            running += attachment.size_bytes
        else:
            out_of_budget = True
            dropped_count += 1
            dropped_bytes += attachment.size_bytes
    return Selection(
        kept=frozenset(kept),
        truncated=dropped_count > 0,
        dropped_count=dropped_count,
        dropped_bytes=dropped_bytes,
    )


@dataclass(frozen=True)
class PlatformRow:
    """One platform SMS row to consider importing."""

    address: str
    date_millis: int
    type: int
    body: str
    read: bool


@dataclass(frozen=True)
class KnownMessage:
    """A stored message reduced to what the duplicate check needs."""

    participants: str
    timestamp_millis: int
    body: str


@dataclass(frozen=True)
class StoredMessage:
    """A stored SMS row reduced to what the repair check needs."""

    id: int
    conversation_id: int
    transport: str
    timestamp_millis: int
    body: str


def same_sender(participants: str, address: str) -> bool:
    """Whether ``address`` matches any participant of a ``\\u0001``-joined set."""
    return any(
        phone_numbers.matches(part, address)
        for part in participants.split(store_mod.ADDRESS_DELIMITER)
        if part.strip()
    )


def find_missing(platform: list[PlatformRow], known: list[KnownMessage]) -> list[PlatformRow]:
    """Which platform rows the store does not hold yet.

    Ports ``PlatformSmsImport.missing``: a row is held when a known message
    has the same body, a timestamp within :data:`MATCH_WINDOW_MS`, and a
    participant matching the row's address (format-tolerant, like delivery).
    """
    by_body: dict[str, list[KnownMessage]] = {}
    for item in known:
        by_body.setdefault(item.body, []).append(item)
    missing = []
    for row in platform:
        candidates = by_body.get(row.body)
        if candidates is None:
            missing.append(row)
            continue
        held = any(
            abs(item.timestamp_millis - row.date_millis) <= MATCH_WINDOW_MS
            and same_sender(item.participants, row.address)
            for item in candidates
        )
        if not held:
            missing.append(row)
    return missing


def find_duplicate_ids(
    messages: list[StoredMessage], participants_of: dict[int, str]
) -> list[int]:
    """Ids of imported SMS rows duplicating an older row.

    Ports ``PlatformSmsImport.duplicateIds``: an imported row is one whose
    id sits more than :data:`IMPORTED_ID_GAP_MS` past its message time; a
    duplicate shares body, window and sender with an older id. The older
    row always wins. MMS rows are never candidates.
    """
    by_body: dict[str, list[StoredMessage]] = {}
    for item in messages:
        if item.transport == "SMS":
            by_body.setdefault(item.body, []).append(item)
    duplicates = []
    for item in messages:
        if item.transport != "SMS":
            continue
        if item.id - item.timestamp_millis <= IMPORTED_ID_GAP_MS:
            continue
        mine = participants_of.get(item.conversation_id, "")
        older = any(
            other.id < item.id
            and abs(other.timestamp_millis - item.timestamp_millis) <= MATCH_WINDOW_MS
            and (
                other.conversation_id == item.conversation_id
                or same_sender(participants_of.get(other.conversation_id, ""), mine)
            )
            for other in by_body.get(item.body, [])
        )
        if older:
            duplicates.append(item.id)
    return duplicates


@dataclass(frozen=True)
class ExportResult:
    """An export payload plus the honest photo count and its stamp."""

    backup: dict
    photo_count: int
    exported_at: str


def build_backup(
    conversations: list,
    messages: list,
    settings: dict,
    blocklist: list[str],
    starred: list[str],
) -> ExportResult:
    """Map live conversation/message rows onto the canonical payload.

    Ports ``ConversationExporter.buildBackup``: a message's conversation id
    becomes its ``threadId``, its id is kept verbatim (so a restore
    overwrites the same key instead of duplicating), and its address is the
    sender's when present, else the conversation's first participant. Every
    message carrying a ``media_path`` is counted in ``photo_count`` because
    photo bytes travel as ``mms/`` siblings, never inside the JSON.
    """
    by_conversation = {row.id: row for row in conversations}
    photo_count = 0
    backup_messages = []
    for message in messages:
        media_path = getattr(message, "media_path", None)
        if isinstance(media_path, str) and media_path.strip():
            photo_count += 1
        sender = getattr(message, "sender_address", None)
        if sender is None:
            conversation = by_conversation.get(message.conversation_id)
            sender = ""
            if conversation is not None:
                primary = getattr(conversation, "primary_dialable", None)
                if callable(primary):
                    try:
                        sender = primary() or ""
                    except Exception:
                        sender = ""
                if not sender:
                    joined = getattr(conversation, "participant_addresses", "") or ""
                    for part in joined.split(store_mod.ADDRESS_DELIMITER):
                        if part.strip():
                            sender = part
                            break
        backup_messages.append(
            {
                "id": message.id,
                "threadId": message.conversation_id,
                "address": sender,
                "body": message.body,
                "date": message.timestamp_millis,
            }
        )
    return ExportResult(
        backup={
            "version": BACKUP_VERSION,
            "messages": backup_messages,
            "settings": dict(settings),
            "blocklist": list(blocklist),
            "starred": list(starred),
        },
        photo_count=photo_count,
        exported_at=datetime.now(timezone.utc).isoformat(),
    )


@dataclass(frozen=True)
class PlannedMessage:
    """One backup message normalized for the restore."""

    backup_id: int
    thread_id: int
    address: str
    body: str
    date: int


@dataclass(frozen=True)
class PlannedThread:
    """One backup thread: its live-conversation key and its messages."""

    thread_id: int
    key: str
    address: str
    messages: tuple[PlannedMessage, ...]


@dataclass(frozen=True)
class RestorePlan:
    """The deterministic import a validated payload restores to."""

    threads: tuple[PlannedThread, ...]
    messages: tuple[PlannedMessage, ...]


def _planned_id(entry: dict) -> int:
    return entry.get("id")


def _planned_thread(entry: dict) -> int:
    return entry.get("threadId", entry.get("thread_id"))


def plan_restore(data: dict) -> RestorePlan:
    """Map validated backup data onto its deterministic restore plan.

    Ports ``RestoreService.plan``: pure in its input, so applying the same
    payload twice converges on the same state. Expects :func:`deserialize`
    output (ids/dates already range-checked).
    """
    by_thread: dict[int, list[PlannedMessage]] = {}
    order: list[int] = []
    planned: list[PlannedMessage] = []
    for entry in data.get("messages", []):
        thread_id = _planned_thread(entry)
        item = PlannedMessage(
            backup_id=_planned_id(entry),
            thread_id=thread_id,
            address=entry.get("address", ""),
            body=entry.get("body", ""),
            date=entry.get("date"),
        )
        planned.append(item)
        if thread_id not in by_thread:
            by_thread[thread_id] = []
            order.append(thread_id)
        by_thread[thread_id].append(item)
    threads = tuple(
        PlannedThread(
            thread_id=thread_id,
            key=phone_numbers.conversation_key(by_thread[thread_id][0].address),
            address=by_thread[thread_id][0].address,
            messages=tuple(by_thread[thread_id]),
        )
        for thread_id in order
    )
    return RestorePlan(threads=threads, messages=tuple(planned))


@dataclass(frozen=True)
class ImportResult:
    """Outcome counts of one restore run."""

    imported: int
    skipped: int
    conversations_created: int
    photos_restored: int
    missing_photos: int
    settings_applied: int
    blocklist_entries: int
    starred_entries: int


@dataclass
class PendingMessage:
    """One message queued for the single restore transaction."""

    address: str
    body: str
    date: int
    read: bool = True
    direction: str = "INCOMING"
    backup_id: int | None = None
    thread_id: int | None = None
    photo_name: str | None = None
    photo_bytes: bytes | None = None
    photo_expected: bool = False
    photo_missing: int = 0
    photo_staged: str | None = None


def _settings_value_to_string(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _apply_settings_locked(conn: sqlite3.Connection, settings: dict) -> int:
    applied = 0
    for key, value in settings.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise BackupError("backup settings must be string-keyed string values")
        try:
            parsed: object = json.loads(value)
        except ValueError:
            parsed = value
        conn.execute(
            "REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(parsed, ensure_ascii=False)),
        )
        applied += 1
    return applied


def _blocklist_entries(data: dict) -> list[tuple[str, str]]:
    """Kind-preserving blocklist entries for the restore.

    Prefers the ``blocklistDetailed`` extension when present; otherwise
    falls back to the flat Android-compatible list, treating digit-bearing
    entries as senders and the rest as keywords.
    """
    detailed = data.get(BLOCKLIST_DETAILED_KEY)
    if detailed is not None:
        if not isinstance(detailed, list):
            raise BackupError("blocklistDetailed must be an array")
        entries = []
        for node in detailed:
            if not isinstance(node, dict):
                raise BackupError("blocklistDetailed entries must be objects")
            kind = node.get("kind")
            value = node.get("value")
            if kind not in store_mod.BLOCKLIST_KINDS:
                raise BackupError(f"unknown blocklist kind: {kind!r}")
            if not isinstance(value, str) or not value.strip():
                raise BackupError("blocklistDetailed values must be non-blank strings")
            entries.append((kind, value.strip()))
        return entries
    raw = data.get("blocklist", [])
    if isinstance(raw, dict):
        entries = []
        for kind, values in raw.items():
            items = values if isinstance(values, list) else [values]
            for value in items:
                if not isinstance(value, str) or not value.strip():
                    continue
                clean = value.strip()
                if kind in store_mod.BLOCKLIST_KINDS:
                    entries.append((kind, clean))
                elif any(ch.isdigit() for ch in clean):
                    entries.append(("sender", clean))
                else:
                    entries.append(("keyword", clean))
        return entries
    if not isinstance(raw, list):
        raise BackupError("backup is missing a blocklist array")
    entries = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise BackupError("blocklist entries must be strings")
        clean = value.strip()
        if any(ch.isdigit() for ch in clean):
            entries.append(("sender", clean))
        else:
            entries.append(("keyword", clean))
    return entries


def _apply_blocklist_locked(conn: sqlite3.Connection, data: dict) -> int:
    entries = _blocklist_entries(data)
    conn.execute("DELETE FROM blocklist")
    for kind, value in entries:
        conn.execute("REPLACE INTO blocklist (kind, value) VALUES (?, ?)", (kind, value))
    return len(entries)


def _apply_starred_locked(conn: sqlite3.Connection, starred: list) -> int:
    if not isinstance(starred, list) or any(not isinstance(s, str) for s in starred):
        raise BackupError("backup is missing a starred array")
    conn.execute("DELETE FROM starred")
    count = 0
    for address in starred:
        clean = address.strip()
        if not clean:
            continue
        conn.execute("REPLACE INTO starred (address) VALUES (?)", (clean,))
        count += 1
    return count


def _resolve_sibling(photo_dir: Path | str | None, rel: str) -> Path | None:
    """Resolve a backup-relative photo path without escaping ``photo_dir``."""
    if photo_dir is None:
        return None
    base = Path(photo_dir).resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        return None
    return target if target.is_file() else None


def _read_sibling(photo_dir: Path | str | None, rel: str) -> tuple[str, bytes] | None:
    """Read one backup-relative sibling, treating races as absent."""
    target = _resolve_sibling(photo_dir, rel)
    if target is None:
        return None
    try:
        return target.name, target.read_bytes()
    except OSError:
        return None


def _photo_jobs_for_backup_message(
    entry: dict, photo_dir: Path | str | None
) -> tuple[str | None, bytes | None, bool, int]:
    """Resolve one canonical message's ``photos`` siblings.

    Returns ``(name, data, expected, missing)``: the staged file name and
    bytes when the first listed sibling reads, else the honest missing
    count for every listed sibling.
    """
    rels = entry.get(PHOTOS_KEY, [])
    if not rels:
        return None, None, False, 0
    if not isinstance(rels, list) or any(not isinstance(r, str) for r in rels):
        raise BackupError("message photos must be an array of paths")
    for rel in rels:
        hit = _read_sibling(photo_dir, rel)
        if hit is not None:
            name, blob = hit
            return name, blob, True, len(rels) - 1
    return None, None, True, len(rels)


def _target_conversation_locked(
    conn: sqlite3.Connection,
    key: str,
    address: str,
    thread_id: int | None,
    used_ids: set[int],
) -> tuple[int, bool]:
    """Find the live conversation for one backup thread, creating it idle.

    An existing thread (exact key, then format-tolerant match) is reused so
    a re-import merges instead of forking. A new thread reuses the backup
    id only when it is free; on collision a fresh live id is minted, so a
    restore never overwrites a live conversation.
    """
    dialable = phone_numbers.canonical_dialable(address)
    if key:
        hit = conn.execute(
            "SELECT id FROM conversations WHERE participantAddresses = ? LIMIT 1",
            (key,),
        ).fetchone()
        if hit is not None:
            _upgrade_dialable_locked(conn, hit["id"], dialable)
            return hit["id"], False
        for row in conn.execute("SELECT id, participantAddresses FROM conversations"):
            parts = [
                part
                for part in row["participantAddresses"].split(store_mod.ADDRESS_DELIMITER)
                if part.strip()
            ]
            if any(phone_numbers.matches(part, address) for part in parts):
                _upgrade_dialable_locked(conn, row["id"], dialable)
                return row["id"], False
    if thread_id is not None and thread_id not in used_ids:
        stored = key if key else address.strip()
        dialable_stored = dialable if dialable else stored
        try:
            conn.execute(
                "INSERT INTO conversations (id, participantAddresses, dialableAddress)"
                " VALUES (?, ?, ?)",
                (thread_id, stored, dialable_stored),
            )
        except sqlite3.OperationalError:
            conn.execute(
                "INSERT INTO conversations (id, participantAddresses) VALUES (?, ?)",
                (thread_id, stored),
            )
        used_ids.add(thread_id)
        return thread_id, True
    fresh = store_mod._fresh_id(conn, "conversations")
    stored = key if key else address.strip()
    dialable_stored = dialable if dialable else stored
    try:
        conn.execute(
            "INSERT INTO conversations (id, participantAddresses, dialableAddress)"
            " VALUES (?, ?, ?)",
            (fresh, stored, dialable_stored),
        )
    except sqlite3.OperationalError:
        conn.execute(
            "INSERT INTO conversations (id, participantAddresses) VALUES (?, ?)",
            (fresh, stored),
        )
    used_ids.add(fresh)
    return fresh, True


def _upgrade_dialable_locked(
    conn: sqlite3.Connection, conversation_id: int, candidate: str
) -> None:
    if not (candidate or "").strip():
        return
    try:
        current = conn.execute(
            "SELECT dialableAddress FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return
    if current is None:
        return
    try:
        stored = current["dialableAddress"]
    except (KeyError, IndexError, TypeError):
        return
    if phone_numbers.should_replace_dialable(stored or "", candidate):
        conn.execute(
            "UPDATE conversations SET dialableAddress = ? WHERE id = ?",
            (candidate.strip(), conversation_id),
        )


def _restore_locked(
    conn: sqlite3.Connection,
    pendings: list[PendingMessage],
    settings: dict | None,
    blocklist_data: dict | None,
    starred: list | None,
    media_root: Path | str | None,
) -> tuple[ImportResult, list[tuple[int, str, bytes | None, str | None]]]:
    """Apply one restore inside the caller's transaction.

    Returns the counts plus the photo-file jobs the caller must write after
    commit (message id, file name, bytes or staged-file path).
    """
    settings_applied = _apply_settings_locked(conn, settings) if settings is not None else 0
    blocklist_entries = (
        _apply_blocklist_locked(conn, blocklist_data) if blocklist_data is not None else 0
    )
    starred_entries = _apply_starred_locked(conn, starred) if starred is not None else 0

    participants_of = {
        row["id"]: row["participantAddresses"]
        for row in conn.execute("SELECT id, participantAddresses FROM conversations")
    }
    used_conversation_ids = set(participants_of)
    used_message_ids: set[int] = set()
    known_by_body: dict[str, list[tuple[str, int]]] = {}
    for row in conn.execute("SELECT id, conversationId, body, timestampMillis FROM messages"):
        used_message_ids.add(row["id"])
        known_by_body.setdefault(row["body"], []).append(
            (participants_of.get(row["conversationId"], ""), row["timestampMillis"])
        )

    conversation_of_thread: dict[int, int] = {}
    imported = 0
    skipped = 0
    conversations_created = 0
    missing_photos = 0
    photo_jobs: list[tuple[int, str, bytes]] = []

    for pending in pendings:
        key = phone_numbers.conversation_key(pending.address)
        if not key:
            skipped += 1
            if pending.photo_expected:
                missing_photos += 1
            continue
        thread_id = pending.thread_id
        cache_key = (thread_id, key)
        if cache_key in conversation_of_thread:
            conversation_id = conversation_of_thread[cache_key]
        else:
            conversation_id, created = _target_conversation_locked(
                conn, key, pending.address, thread_id, used_conversation_ids
            )
            conversation_of_thread[cache_key] = conversation_id
            participants_of.setdefault(
                conversation_id, key if key else pending.address.strip()
            )
            if created:
                conversations_created += 1
        participants = participants_of.get(conversation_id, "")
        duplicate = any(
            abs(timestamp - pending.date) <= MATCH_WINDOW_MS
            and same_sender(known_participants, pending.address)
            for known_participants, timestamp in known_by_body.get(pending.body, [])
        )
        if duplicate:
            skipped += 1
            continue
        if pending.backup_id is not None and pending.backup_id not in used_message_ids:
            message_id = pending.backup_id
        else:
            message_id = store_mod._fresh_id(conn, "messages")
        used_message_ids.add(message_id)
        has_photo = pending.photo_bytes is not None or (
            pending.photo_staged is not None
        )
        stage_photo = (
            pending.photo_expected
            and has_photo
            and media_root is not None
            and pending.photo_name
        )
        missing_photos += pending.photo_missing
        if (
            pending.photo_expected
            and has_photo
            and pending.photo_name
            and media_root is None
        ):
            missing_photos += 1
        transport = "MMS" if stage_photo else "SMS"
        media_path = (
            str(Path(media_root) / "mms" / str(message_id) / pending.photo_name)
            if stage_photo
            else None
        )
        conn.execute(
            "INSERT INTO messages (id, conversationId, direction, transport, body,"
            " timestampMillis, senderAddress, isRead, sent, contentLocation, mediaPath)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?)",
            (
                message_id,
                conversation_id,
                pending.direction,
                transport,
                pending.body,
                pending.date,
                pending.address if pending.direction == "INCOMING" else None,
                int(pending.read),
                media_path,
            ),
        )
        known_by_body.setdefault(pending.body, []).append((participants, pending.date))
        if stage_photo:
            photo_jobs.append(
                (message_id, pending.photo_name, pending.photo_bytes, pending.photo_staged)
            )
        imported += 1

    result = ImportResult(
        imported=imported,
        skipped=skipped,
        conversations_created=conversations_created,
        photos_restored=0,
        missing_photos=missing_photos,
        settings_applied=settings_applied,
        blocklist_entries=blocklist_entries,
        starred_entries=starred_entries,
    )
    return result, photo_jobs


def _copy_staged(staged: str, target: Path) -> None:
    """Copy a staged photo file to ``target`` in chunks (never all in RAM)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(staged, "rb") as source, open(target, "wb") as dest:
        shutil.copyfileobj(source, dest, length=1024 * 1024)


def _finish_photo_jobs(
    store,
    photo_jobs: list[tuple[int, str, bytes | None, str | None]],
    media_root: Path | str | None,
) -> tuple[int, int]:
    """Write staged photo bytes; clear the media link when a write fails.

    A job carries either in-memory ``data`` or a ``staged`` file path (the
    streaming SBR import stages multi-MB base64 parts on disk); staged
    files are copied in 1 MiB chunks so the finish pass stays flat too.
    """
    restored = 0
    missing = 0
    for message_id, name, data, staged in photo_jobs:
        target = Path(media_root) / "mms" / str(message_id) / Path(name).name
        try:
            if staged is not None:
                _copy_staged(staged, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data or b"")
            restored += 1
        except OSError:
            missing += 1

            def _clear(conn: sqlite3.Connection, mid: int = message_id) -> None:
                conn.execute(
                    "UPDATE messages SET mediaPath = NULL, transport = 'SMS' WHERE id = ?",
                    (mid,),
                )

            store.write(_clear)
    return restored, missing


def _run_restore(
    store,
    pendings: list[PendingMessage],
    settings: dict | None = None,
    blocklist_data: dict | None = None,
    starred: list | None = None,
    media_root: Path | str | None = None,
) -> ImportResult:
    """Run one restore inside a single SQLite transaction with rollback."""
    box: dict = {}

    def _tx(conn: sqlite3.Connection) -> None:
        try:
            result, jobs = _restore_locked(
                conn, pendings, settings, blocklist_data, starred, media_root
            )
        except BaseException:
            conn.rollback()
            raise
        box["result"] = result
        box["jobs"] = jobs

    store.write(_tx)
    result: ImportResult = box["result"]
    restored, failed = _finish_photo_jobs(store, box["jobs"], media_root)
    return ImportResult(
        imported=result.imported,
        skipped=result.skipped,
        conversations_created=result.conversations_created,
        photos_restored=restored,
        missing_photos=result.missing_photos + failed,
        settings_applied=result.settings_applied,
        blocklist_entries=result.blocklist_entries,
        starred_entries=result.starred_entries,
    )


def import_backup(
    store,
    data: dict,
    photo_dir: Path | str | None = None,
    media_root: Path | str | None = None,
) -> ImportResult:
    """Restore validated backup ``data`` into ``store`` idempotently.

    ``data`` is :func:`deserialize` output. Messages matching a live row on
    conversation key + body + 5-minute window are skipped; new rows reuse
    their backup id only when free. Settings merge by key; the blocklist
    and starred sets are replaced. Photo siblings resolve under
    ``photo_dir`` and land under ``media_root/mms/<live-id>/``; absent
    photos are counted honestly in ``missing_photos``.
    """
    plan = plan_restore(data)
    pendings = []
    for item, entry in zip(plan.messages, data.get("messages", [])):
        name, blob, expected, missing = _photo_jobs_for_backup_message(entry, photo_dir)
        pendings.append(
            PendingMessage(
                address=item.address,
                body=item.body,
                date=item.date,
                read=True,
                direction="INCOMING",
                backup_id=item.backup_id,
                thread_id=item.thread_id,
                photo_name=name,
                photo_bytes=blob,
                photo_expected=expected,
                photo_missing=missing,
            )
        )
    return _run_restore(
        store,
        pendings,
        settings=data.get("settings"),
        blocklist_data=data,
        starred=data.get("starred"),
        media_root=media_root,
    )


def import_backup_json(
    store,
    text: str,
    photo_dir: Path | str | None = None,
    media_root: Path | str | None = None,
) -> ImportResult:
    """Strict-parse ``text`` then restore it; garbage never half-lands."""
    return import_backup(store, deserialize(text), photo_dir, media_root)


def export_backup(
    store,
    json_path: Path | str,
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
) -> dict:
    """Export the whole archive to ``json_path`` plus ``mms/`` siblings.

    Reads conversations, messages, settings, blocklist and starred from
    ``store`` (public API only), writes the canonical JSON, then copies
    each message's photo file to ``<parent>/mms/<message-id>/<file>`` and
    records it in that message's ``photos`` list. Attachments over
    ``budget_bytes`` are dropped newest-first (see
    :func:`select_attachments`); messages whose file is absent are counted
    honestly in ``missing_photos`` instead of failing the export.
    """
    target = Path(json_path)
    conversations = store.get_all_conversations()
    messages = []
    for conversation in conversations:
        messages.extend(store.get_messages_for_conversation(conversation.id))
    settings = store.read(
        lambda conn: {
            row["key"]: _settings_value_to_string(
                json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
            )
            for row in conn.execute("SELECT key, value FROM settings")
        }
    )
    flat = [value for _, value in store.blocklist_list()]
    detailed = [
        {"kind": kind, "value": value} for kind, value in store.blocklist_list()
    ]
    export = build_backup(conversations, messages, settings, flat, store.starred_list())
    payload = dict(export.backup)
    payload[BLOCKLIST_DETAILED_KEY] = detailed

    photo_dir = target.parent / "mms"
    candidates = []
    for message in messages:
        media = getattr(message, "media_path", None)
        if not (isinstance(media, str) and media.strip()):
            continue
        source = Path(media)
        if not source.is_file():
            continue
        candidates.append(
            Attachment(
                key=AttachmentKey(message.id, 0),
                date_millis=message.timestamp_millis,
                size_bytes=source.stat().st_size,
            )
        )
    base_bytes = len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    selection = select_attachments(base_bytes, candidates, budget_bytes)

    by_id = {message.id: message for message in messages}
    photo_entries: dict[int, list[str]] = {}
    photo_files = 0
    missing_photos = 0
    dropped_photos = 0
    for attachment in candidates:
        message_id = attachment.key.mms_id
        message = by_id[message_id]
        source = Path(str(message.media_path))
        if not source.is_file():
            missing_photos += 1
            continue
        if attachment.key not in selection.kept:
            dropped_photos += 1
            continue
        dest = photo_dir / str(message_id) / source.name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
        except OSError:
            missing_photos += 1
            continue
        photo_entries.setdefault(message_id, []).append(f"mms/{message_id}/{source.name}")
        photo_files += 1
    for message in messages:
        media = getattr(message, "media_path", None)
        if isinstance(media, str) and media.strip() and not Path(media).is_file():
            missing_photos += 1
    for node in payload["messages"]:
        rels = photo_entries.get(node["id"])
        if rels:
            node[PHOTOS_KEY] = rels

    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    note = None
    if selection.truncated:
        note = (
            f"dropped {selection.dropped_count} oldest attachment(s),"
            f" {selection.dropped_bytes} byte(s), over the export budget"
        )
    return {
        "path": str(target),
        "messages": len(payload["messages"]),
        "exported_at": export.exported_at,
        "photo_count": export.photo_count,
        "photo_files": photo_files,
        "missing_photos": missing_photos,
        "dropped_photos": dropped_photos,
        "truncated": selection.truncated,
        "note": note,
    }


def import_sms_json(
    store,
    text: str,
    export_dir: Path | str | None = None,
    media_root: Path | str | None = None,
) -> ImportResult:
    """Restore a suite ``export/sms.json`` payload through the upsert path.

    SMS rows map by ``type`` (inbox/sent, others skipped); MMS rows join
    their addresses as participants and their text parts as the body, with
    binary parts resolved under ``export_dir/mms/<mmsId>/<partId>.<ext>``.
    Absent parts count toward ``missing_photos``.
    """
    payload = decode_sms_payload(text)
    pendings: list[PendingMessage] = []
    for record in payload.sms:
        if not record.address.strip():
            continue
        if record.type == SMS_TYPE_INBOX:
            direction = "INCOMING"
        elif record.type == SMS_TYPE_SENT:
            direction = "OUTGOING"
        else:
            continue
        pendings.append(
            PendingMessage(
                address=record.address,
                body=record.body,
                date=record.date,
                read=record.read,
                direction=direction,
            )
        )
    for record in payload.mms:
        addresses = [
            address.strip()
            for address in record.addresses
            if address.strip() and address.strip() != "insert-address-token"
        ]
        if not addresses:
            continue
        body = "\n".join(part.text for part in record.text_parts)
        obtained: list[tuple[str, bytes]] = []
        unobtainable = 0
        for part in record.parts:
            hit = _read_sibling(export_dir, part.export_path(record.mms_id))
            if hit is not None:
                obtained.append(hit)
            else:
                unobtainable += 1
        pendings.append(
            PendingMessage(
                address=addresses[0],
                body=body,
                date=mms_date_to_millis(record.date),
                read=record.read,
                direction=(
                    "OUTGOING" if record.message_box == MMS_BOX_SENT else "INCOMING"
                ),
                photo_name=obtained[0][0] if obtained else None,
                photo_bytes=obtained[0][1] if obtained else None,
                photo_expected=bool(record.parts),
                photo_missing=unobtainable,
            )
        )
    return _run_restore(store, pendings, media_root=media_root)


def _sbr_int(value: str | None, field: str) -> int:
    if value is None:
        raise BackupError(f"sms-backup-restore {field} is missing")
    try:
        return int(value.strip())
    except ValueError:
        raise BackupError(
            f"sms-backup-restore {field} must be a number, was {value!r}"
        ) from None


def _sbr_bool(value: str | None, field: str, default: bool = True) -> bool:
    if value is None:
        return default
    text = value.strip().lower()
    if text in ("1", "true"):
        return True
    if text in ("0", "false"):
        return False
    raise BackupError(f"sms-backup-restore {field} must be 0/1, was {value!r}")


def _sbr_photo_name(part: ET.Element, content_type: str, index: int) -> str:
    """Deterministic file name for one ``mms/parts/part`` binary part."""
    ext = extension_for_mime(content_type)
    raw_name = part.get("filename") or part.get("name") or ""
    stem = Path(raw_name).name if raw_name else f"part{part.get('seq', index)}"
    stem = stem.split(".")[0] if "." in stem else stem
    return f"{stem}.{ext}"


def _sbr_stage_data(name: str, data: str, stage_dir: str, counter: int) -> str:
    """Decode one base64 ``data`` attribute straight to a staged file."""
    try:
        blob = base64.b64decode("".join(data.split()), validate=True)
    except ValueError:
        raise BackupError("sms-backup-restore part data is not valid base64") from None
    staged = str(Path(stage_dir) / f"{counter:06d}-{Path(name).name}")
    try:
        with open(staged, "wb") as dest:
            dest.write(blob)
    except OSError as err:
        raise BackupError(f"cannot stage sms-backup-restore photo: {err}") from err
    del blob
    return staged


def _sbr_stage_sibling(
    photo_dir: Path | str | None, candidate: str, stage_dir: str, counter: int
) -> tuple[str | None, str | None]:
    """Copy one sibling photo file to the staging dir, if present.

    Returns ``(staged path, display name)``; the staged file carries a
    counter prefix so sibling names can never collide there.
    """
    target = _resolve_sibling(photo_dir, Path(candidate).name)
    if target is None and Path(candidate).name != candidate:
        target = _resolve_sibling(photo_dir, candidate)
    if target is None:
        return None, None
    staged = str(Path(stage_dir) / f"{counter:06d}-{target.name}")
    try:
        with open(target, "rb") as source, open(staged, "wb") as dest:
            shutil.copyfileobj(source, dest, length=1024 * 1024)
    except OSError:
        return None, None
    return staged, target.name


def _sbr_part_photo(
    part: ET.Element,
    photo_dir: Path | str | None,
    stage_dir: str,
    counter: int,
    index: int,
) -> tuple[str | None, str | None, bool]:
    """Resolve one ``mms/parts/part`` binary part to ``(name, staged, found)``.

    Embedded base64 ``data`` is decoded straight to a file in ``stage_dir``
    (only one part's bytes are ever resident); sibling files are copied
    there in chunks. Returns the staged path, never the bytes.
    """
    content_type = part.get("ct") or "application/octet-stream"
    if content_type.lower().startswith("text/"):
        return None, None, False
    name = _sbr_photo_name(part, content_type, index)
    data = part.get("data")
    if data:
        return name, _sbr_stage_data(name, data, stage_dir, counter), True
    for attr in ("filename", "name", "file", "src", "cl"):
        candidate = part.get(attr)
        if not candidate:
            continue
        staged, display = _sbr_stage_sibling(photo_dir, candidate, stage_dir, counter)
        if staged is not None:
            return display, staged, True
    return None, None, True


def _pending_from_sms_element(node: ET.Element) -> PendingMessage | None:
    """Map one ``sms`` element onto its restore row (None skips the row)."""
    address = (node.get("address") or "").strip()
    if not address:
        return None
    kind = _sbr_int(node.get("type"), "sms/@type")
    if kind == SMS_TYPE_INBOX:
        direction = "INCOMING"
    elif kind == SMS_TYPE_SENT:
        direction = "OUTGOING"
    else:
        return None
    return PendingMessage(
        address=address,
        body=node.get("body") or "",
        date=_sbr_int(node.get("date"), "sms/@date"),
        read=_sbr_bool(node.get("read"), "sms/@read"),
        direction=direction,
    )


def _pending_from_mms_element(
    node: ET.Element,
    photo_dir: Path | str | None,
    stage_dir: str,
    staged_counter: list[int],
) -> PendingMessage | None:
    """Map one ``mms`` element onto its restore row (None skips the row)."""
    addresses = []
    addrs = node.find("addrs")
    if addrs is not None:
        for addr in addrs.findall("addr"):
            value = (addr.get("address") or "").strip()
            if value and value != "insert-address-token":
                addresses.append(value)
    fallback = (node.get("address") or "").strip()
    if not addresses and fallback:
        addresses.append(fallback)
    if not addresses:
        return None
    texts = []
    obtained: list[tuple[str, str]] = []
    unobtainable = 0
    parts = node.find("parts")
    if parts is not None:
        for index, part in enumerate(parts.findall("part")):
            content_type = part.get("ct") or "application/octet-stream"
            if content_type.lower().startswith("text/"):
                texts.append(part.get("text") or "")
                continue
            name, staged, _expected = _sbr_part_photo(
                part, photo_dir, stage_dir, staged_counter[0], index
            )
            staged_counter[0] += 1
            if staged is not None:
                obtained.append((name or Path(staged).name, staged))
            else:
                unobtainable += 1
    box = _sbr_int(node.get("msg_box", node.get("message_box", "1")), "mms/@msg_box")
    return PendingMessage(
        address=addresses[0],
        body="\n".join(texts),
        date=mms_date_to_millis(_sbr_int(node.get("date"), "mms/@date")),
        read=_sbr_bool(node.get("read"), "mms/@read"),
        direction="OUTGOING" if box == MMS_BOX_SENT else "INCOMING",
        photo_name=obtained[0][0] if obtained else None,
        photo_bytes=None,
        photo_staged=obtained[0][1] if obtained else None,
        photo_expected=bool(obtained or unobtainable),
        photo_missing=unobtainable,
    )


def _sbr_open_stream(source) -> tuple[object, bool]:
    """Open an SBR ``source`` for ``iterparse``: (binary stream, must_close).

    Accepts XML text/bytes, a path, or an already-open binary file object.
    Plain strings that name an existing file stream from disk; anything
    else is treated as inline XML so the old ``import_sbr_xml(text)``
    call shape keeps working.
    """
    if hasattr(source, "read"):
        return source, False
    if isinstance(source, (bytes, bytearray)):
        return io.BytesIO(bytes(source)), False
    if isinstance(source, Path):
        return open(source, "rb"), True
    if isinstance(source, str):
        if source.lstrip().startswith("<"):
            return io.BytesIO(source.encode("utf-8")), False
        try:
            return open(source, "rb"), True
        except OSError:
            return io.BytesIO(source.encode("utf-8")), False
    raise BackupError(f"unsupported sms-backup-restore source: {type(source).__name__}")


def _collect_sbr_pendings(stream, photo_dir, stage_dir) -> list[PendingMessage]:
    """Stream one SBR document with ``iterparse`` into restore rows.

    Each processed ``sms``/``mms`` element is cleared together with its
    subtree, so memory stays flat no matter how large the document is;
    base64 photo parts land in ``stage_dir`` during the same pass. Raises
    :class:`BackupError` before the caller writes anything.
    """
    try:
        context = ET.iterparse(stream, events=("start", "end"))
        pendings: list[PendingMessage] = []
        staged_counter = [0]
        root: ET.Element | None = None
        depth = 0
        for event, elem in context:
            if event == "start":
                if root is None:
                    root = elem
                    if elem.tag != "smses":
                        raise BackupError(
                            f"unsupported backup root: <{elem.tag}> (expected <smses>)"
                        )
                else:
                    depth += 1
                continue
            if elem is root:
                continue
            if depth == 1:
                if elem.tag == "sms":
                    pending = _pending_from_sms_element(elem)
                elif elem.tag == "mms":
                    pending = _pending_from_mms_element(
                        elem, photo_dir, stage_dir, staged_counter
                    )
                else:
                    raise BackupError(f"unsupported backup entry: <{elem.tag}>")
                if pending is not None:
                    pendings.append(pending)
                elem.clear()
            depth -= 1
    except ET.ParseError as err:
        raise BackupError(f"invalid sms-backup-restore XML: {err}") from err
    if root is None:
        raise BackupError("invalid sms-backup-restore XML: empty document")
    return pendings


def import_sbr_xml(
    store,
    text,
    photo_dir: Path | str | None = None,
    media_root: Path | str | None = None,
) -> ImportResult:
    """Restore an SMS Backup & Restore XML document through the upsert path.

    Streams ``smses/sms`` (``address``/``date``/``type``/``body``/``read``)
    and ``mms`` (addresses from ``addrs/addr``, text from
    ``parts/part``) with ``iterparse`` — each element is cleared after it
    is processed, so even hundred-MB documents stay flat in memory; photo
    bytes come from embedded base64 ``data`` decoded straight to staged
    files, or a sibling directory, and absent photos count toward
    ``missing_photos``. The store write stays a single transaction with
    rollback, and malformed XML or a non-``smses`` root raises
    :class:`BackupError` before anything is written. ``text`` may be inline
    XML, a path, or an open binary stream (see :func:`import_sbr_file`).
    """
    stage_dir = tempfile.mkdtemp(prefix="txxt-sbr-")
    try:
        stream, must_close = _sbr_open_stream(text)
        try:
            pendings = _collect_sbr_pendings(stream, photo_dir, stage_dir)
        finally:
            if must_close:
                stream.close()
        return _run_restore(store, pendings, media_root=media_root)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def import_sbr_file(
    store,
    xml_path: Path | str,
    photo_dir: Path | str | None = None,
    media_root: Path | str | None = None,
) -> ImportResult:
    """Restore an SMS Backup & Restore XML file from disk (streaming).

    Same guarantees as :func:`import_sbr_xml` but named for the large-file
    case: the document is never held whole in memory, whatever its size.
    """
    return import_sbr_xml(store, Path(xml_path), photo_dir, media_root)
