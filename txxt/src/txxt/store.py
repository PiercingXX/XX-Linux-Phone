"""SQLite data layer.

Ports the Android Room stack (``data/*Dao``, ``*Entity``, ``InboundStore``,
``OutboundStore``, ``QuarantineStore``; ``RestoreService`` upsert semantics
arrive with the L2 backup lane) onto stdlib ``sqlite3``:

- Tables per PORT_SPEC §3 mirror Room schema v5 1:1 for ``conversations``
  and ``messages`` (cites: ``app/schemas/.../TxxTDatabase/5.json``).
  ``blocklist``/``starred``/``settings``/``meta`` are Linux additions
  replacing Android SharedPreferences (``SettingsBlockingStore``,
  ``SettingsStore``, ``ThemeStore``); the spec's ``blocklist(kind)`` gains a
  ``phrase`` kind next to ``sender``/``keyword`` so the ported keyword
  (whole-word) vs phrase (substring) distinction survives storage.
- WAL mode, ``busy_timeout=5000``, foreign keys on.
- One :class:`Store` connection guarded by a single writer lock: every
  write runs through :meth:`Store.write`, so find-then-insert sequences
  (find-or-create, wall-clock id bumps) cannot race each other the way two
  concurrent deliver receivers could.
- Migrations framework: :data:`SCHEMA_VERSION` with incremental
  ``MIGRATION_<n>_<m>`` statement lists applied off ``PRAGMA user_version``;
  the 1->5 chain reproduces the Room column history exactly, then the
  Linux-only 5->6 step adds the modem delete-after-import flag.
- Every query is parameterized (``?`` placeholders only).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from enum import Enum

from txxt import conversations_core as core
from txxt import phone_numbers

#: Current schema version. Room v5 plus the Linux ``pending_delete`` flag
#: (schema 6): the modem store keeps an SMS until TxxT deletes it, so an
#: import marks its row first and clears the mark only after the modem
#: ``Delete`` succeeds — a crash between the two restarts with the row
#: still flagged instead of a modem object deleted before its archive row.
SCHEMA_VERSION = 7

ADDRESS_DELIMITER = "\u0001"
BUSY_TIMEOUT_MS = 5000

BLOCKLIST_KINDS = ("sender", "keyword", "phrase")


@dataclass
class ConversationRow:
    id: int
    participant_addresses: str = ""
    dialable_address: str = ""
    is_starred: bool = False
    is_pinned: bool = False
    is_archived: bool = False
    is_muted: bool = False
    is_quarantined: bool = False
    muted_until_millis: int = 0
    sort_order: str = "PINNED_FIRST"

    def flags(self) -> core.ConversationFlags:
        return core.ConversationFlags(
            is_pinned=self.is_pinned,
            is_archived=self.is_archived,
            is_muted=self.is_muted,
            is_quarantined=self.is_quarantined,
            muted_until_millis=self.muted_until_millis,
            sort_order=core.ConversationSortOrder(self.sort_order),
        )

    def with_flags(self, flags: core.ConversationFlags) -> "ConversationRow":
        row = ConversationRow(
            id=self.id,
            participant_addresses=self.participant_addresses,
            dialable_address=self.dialable_address,
            is_starred=self.is_starred,
            is_pinned=flags.is_pinned,
            is_archived=flags.is_archived,
            is_muted=flags.is_muted,
            is_quarantined=flags.is_quarantined,
            muted_until_millis=flags.muted_until_millis,
            sort_order=flags.sort_order.value,
        )
        return row

    def addresses(self) -> list[str]:
        if not self.participant_addresses:
            return []
        return self.participant_addresses.split(ADDRESS_DELIMITER)

    def dialable_list(self) -> list[str]:
        if self.dialable_address and self.dialable_address.strip():
            return [
                part
                for part in self.dialable_address.split(ADDRESS_DELIMITER)
                if part.strip()
            ]
        return [part for part in self.addresses() if part.strip()]

    def primary_dialable(self) -> str:
        for part in self.dialable_list():
            if part.strip():
                return part.strip()
        for part in self.addresses():
            if part.strip():
                return part.strip()
        return ""


@dataclass
class MessageRow:
    id: int
    conversation_id: int
    direction: str = "INCOMING"
    transport: str = "SMS"
    body: str = ""
    timestamp_millis: int = 0
    sender_address: str | None = None
    is_read: bool = False
    sent: bool = True
    content_location: str | None = None
    media_path: str | None = None
    pending_delete: bool = False


class PinResult(Enum):
    PINNED = "PINNED"
    UNPINNED = "UNPINNED"
    CAP_REACHED = "CAP_REACHED"
    MISSING = "MISSING"


_BASE_V1 = [
    """CREATE TABLE IF NOT EXISTS conversations (
         id INTEGER NOT NULL PRIMARY KEY,
         participantAddresses TEXT NOT NULL,
         isStarred INTEGER NOT NULL DEFAULT 0,
         isPinned INTEGER NOT NULL DEFAULT 0,
         isArchived INTEGER NOT NULL DEFAULT 0,
         sortOrder TEXT NOT NULL DEFAULT 'PINNED_FIRST'
       )""",
    """CREATE TABLE IF NOT EXISTS messages (
         id INTEGER NOT NULL PRIMARY KEY,
         conversationId INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
         direction TEXT NOT NULL,
         transport TEXT NOT NULL,
         body TEXT NOT NULL,
         timestampMillis INTEGER NOT NULL,
         senderAddress TEXT,
         isRead INTEGER NOT NULL DEFAULT 0
       )""",
    "CREATE INDEX IF NOT EXISTS index_messages_conversationId ON messages (conversationId)",
    """CREATE TABLE IF NOT EXISTS blocklist (
         kind TEXT NOT NULL,
         value TEXT NOT NULL,
         PRIMARY KEY (kind, value)
       )""",
    """CREATE TABLE IF NOT EXISTS starred (
         address TEXT NOT NULL PRIMARY KEY
       )""",
    """CREATE TABLE IF NOT EXISTS settings (
         key TEXT NOT NULL PRIMARY KEY,
         value TEXT NOT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS meta (
         key TEXT NOT NULL PRIMARY KEY,
         value TEXT NOT NULL
       )""",
]

MIGRATION_1_2 = ["ALTER TABLE messages ADD COLUMN sent INTEGER NOT NULL DEFAULT 1"]
MIGRATION_2_3 = ["ALTER TABLE messages ADD COLUMN contentLocation TEXT"]
MIGRATION_3_4 = [
    "ALTER TABLE conversations ADD COLUMN isMuted INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE messages ADD COLUMN mediaPath TEXT",
]
MIGRATION_4_5 = [
    "ALTER TABLE conversations ADD COLUMN isQuarantined INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE conversations ADD COLUMN mutedUntilMillis INTEGER NOT NULL DEFAULT 0",
]

MIGRATION_5_6 = [
    "ALTER TABLE messages ADD COLUMN pendingDelete INTEGER NOT NULL DEFAULT 0",
]

MIGRATION_6_7 = [
    "ALTER TABLE conversations ADD COLUMN dialableAddress TEXT NOT NULL DEFAULT ''",
]

MIGRATIONS: dict[tuple[int, int], list[str]] = {
    (1, 2): MIGRATION_1_2,
    (2, 3): MIGRATION_2_3,
    (3, 4): MIGRATION_3_4,
    (4, 5): MIGRATION_4_5,
    (5, 6): MIGRATION_5_6,
    (6, 7): MIGRATION_6_7,
}


def _backfill_dialable_6_7(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT id, participantAddresses FROM conversations"):
        cid = row["id"]
        key = row["participantAddresses"] or ""
        newest = conn.execute(
            "SELECT senderAddress FROM messages WHERE conversationId = ?"
            " AND senderAddress IS NOT NULL AND TRIM(senderAddress) != ''"
            " ORDER BY timestampMillis DESC, id DESC LIMIT 1",
            (cid,),
        ).fetchone()
        raw = newest["senderAddress"] if newest is not None else None
        if raw is not None and str(raw).strip():
            dialable = phone_numbers.canonical_dialable_configured(str(raw))
        else:
            best = phone_numbers.canonical_dialable_configured(key) if key else ""
            dialable = best if best else key
        if not dialable:
            dialable = key
        conn.execute(
            "UPDATE conversations SET dialableAddress = ? WHERE id = ?",
            (dialable, cid),
        )


def _apply_migrations(conn: sqlite3.Connection, from_version: int) -> int:
    version = from_version
    while version < SCHEMA_VERSION:
        steps = MIGRATIONS.get((version, version + 1))
        if not steps:
            raise sqlite3.DatabaseError(f"no migration path from schema {version}")
        for sql in steps:
            conn.execute(sql)
        if version == 6:
            _backfill_dialable_6_7(conn)
        version += 1
        conn.execute(f"PRAGMA user_version = {version:d}")
    return version


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    # PRAGMAs take no placeholders; the value is an internal int constant.
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS:d}")
    conn.execute("PRAGMA foreign_keys = ON")


def _row_to_conversation(row: sqlite3.Row) -> ConversationRow:
    keys = row.keys()
    return ConversationRow(
        id=row["id"],
        participant_addresses=row["participantAddresses"],
        dialable_address=row["dialableAddress"] if "dialableAddress" in keys else "",
        is_starred=bool(row["isStarred"]),
        is_pinned=bool(row["isPinned"]),
        is_archived=bool(row["isArchived"]),
        is_muted=bool(row["isMuted"]) if "isMuted" in row.keys() else False,
        is_quarantined=bool(row["isQuarantined"]) if "isQuarantined" in row.keys() else False,
        muted_until_millis=row["mutedUntilMillis"] if "mutedUntilMillis" in row.keys() else 0,
        sort_order=row["sortOrder"],
    )


def _row_to_message(row: sqlite3.Row) -> MessageRow:
    keys = row.keys()
    return MessageRow(
        id=row["id"],
        conversation_id=row["conversationId"],
        direction=row["direction"],
        transport=row["transport"],
        body=row["body"],
        timestamp_millis=row["timestampMillis"],
        sender_address=row["senderAddress"],
        is_read=bool(row["isRead"]),
        sent=bool(row["sent"]) if "sent" in keys else True,
        content_location=row["contentLocation"] if "contentLocation" in keys else None,
        media_path=row["mediaPath"] if "mediaPath" in keys else None,
        pending_delete=bool(row["pendingDelete"]) if "pendingDelete" in keys else False,
    )


class Store:
    """Single-connection SQLite store. One instance per process; all writes
    are serialized through an internal lock (:meth:`write`)."""

    def __init__(self, path: str | None = None, conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._conn = conn
            self._owns_conn = False
        else:
            self._conn = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
            self._owns_conn = True
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            _configure(self._conn)
            version = self._conn.execute("PRAGMA user_version").fetchone()[0] or 0
            if version == 0:
                for sql in _BASE_V1:
                    self._conn.execute(sql)
                self._conn.execute("PRAGMA user_version = 1")
                version = 1
            _apply_migrations(self._conn, version)
            self._conn.commit()

    @classmethod
    def memory(cls) -> "Store":
        return cls(conn=sqlite3.connect(":memory:", check_same_thread=False))

    @property
    def schema_version(self) -> int:
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            if self._owns_conn:
                self._conn.close()

    def write(self, fn):
        """Run ``fn(conn)`` under the single-writer lock and commit."""
        with self._lock:
            result = fn(self._conn)
            self._conn.commit()
            return result

    def read(self, fn):
        with self._lock:
            return fn(self._conn)

    # ---- conversations ----

    def upsert_conversation(self, row: ConversationRow) -> None:
        dialable = row.dialable_address.strip() or phone_numbers.canonical_dialable_configured(
            row.participant_addresses
        )
        self.write(
            lambda c, _d=dialable: c.execute(
                "REPLACE INTO conversations (id, participantAddresses, dialableAddress,"
                " isStarred, isPinned, isArchived, isMuted, isQuarantined,"
                " mutedUntilMillis, sortOrder)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.id,
                    row.participant_addresses,
                    _d,
                    int(row.is_starred),
                    int(row.is_pinned),
                    int(row.is_archived),
                    int(row.is_muted),
                    int(row.is_quarantined),
                    row.muted_until_millis,
                    row.sort_order,
                ),
            )
        )

    def update_conversation(self, row: ConversationRow) -> None:
        self.upsert_conversation(row)

    def get_conversation(self, conversation_id: int) -> ConversationRow | None:
        def _get(c):
            found = c.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            return _row_to_conversation(found) if found is not None else None

        return self.read(_get)

    def get_conversation_by_participants(self, joined: str) -> ConversationRow | None:
        def _get(c):
            found = c.execute(
                "SELECT * FROM conversations WHERE participantAddresses = ? LIMIT 1", (joined,)
            ).fetchone()
            return _row_to_conversation(found) if found is not None else None

        return self.read(_get)

    def get_all_conversations(self) -> list[ConversationRow]:
        return self.read(
            lambda c: [_row_to_conversation(r) for r in c.execute("SELECT * FROM conversations")]
        )

    def delete_conversation(self, conversation_id: int) -> None:
        def _delete(c):
            c.execute("DELETE FROM messages WHERE conversationId = ?", (conversation_id,))
            c.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

        self.write(_delete)

    def unarchive(self, conversation_id: int) -> None:
        self.write(
            lambda c: c.execute(
                "UPDATE conversations SET isArchived = 0 WHERE id = ? AND isArchived = 1",
                (conversation_id,),
            )
        )

    def toggle_pin(self, conversation_id: int) -> PinResult:
        """Toggle a conversation's pin flag, refusing a new pin at the cap of
        :data:`txxt.conversations_core.MAX_PINNED` without writing."""

        def _toggle(c):
            found = c.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if found is None:
                return PinResult.MISSING
            row = _row_to_conversation(found)
            if row.is_pinned:
                c.execute("UPDATE conversations SET isPinned = 0 WHERE id = ?", (conversation_id,))
                return PinResult.UNPINNED
            count = c.execute(
                "SELECT COUNT(*) FROM conversations WHERE isPinned = 1"
            ).fetchone()[0]
            if not core.can_pin(count, already_pinned=False):
                return PinResult.CAP_REACHED
            c.execute("UPDATE conversations SET isPinned = 1 WHERE id = ?", (conversation_id,))
            return PinResult.PINNED

        return self.write(_toggle)

    def set_flags(self, conversation_id: int, flags: core.ConversationFlags) -> bool:
        def _set(c):
            found = c.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if found is None:
                return False
            c.execute(
                "UPDATE conversations SET isPinned = ?, isArchived = ?, isMuted = ?,"
                " isQuarantined = ?, mutedUntilMillis = ?, sortOrder = ? WHERE id = ?",
                (
                    int(flags.is_pinned),
                    int(flags.is_archived),
                    int(flags.is_muted),
                    int(flags.is_quarantined),
                    flags.muted_until_millis,
                    flags.sort_order.value,
                    conversation_id,
                ),
            )
            return True

        return self.write(_set)

    def is_muted(self, address: str, now_millis: int) -> bool:
        """Whether any conversation holding ``address`` suppresses
        notifications at ``now_millis`` (format-tolerant match)."""
        for row in self.get_all_conversations():
            if not core.is_mute_active(row.is_muted, row.muted_until_millis, now_millis):
                continue
            if any(
                phone_numbers.matches(part, address)
                for part in row.addresses()
                if part.strip()
            ):
                return True
        return False

    # ---- messages ----

    def upsert_message(self, row: MessageRow) -> None:
        self.write(
            lambda c: c.execute(
                "REPLACE INTO messages (id, conversationId, direction, transport, body,"
                " timestampMillis, senderAddress, isRead, sent, contentLocation, mediaPath,"
                " pendingDelete)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.id,
                    row.conversation_id,
                    row.direction,
                    row.transport,
                    row.body,
                    row.timestamp_millis,
                    row.sender_address,
                    int(row.is_read),
                    int(row.sent),
                    row.content_location,
                    row.media_path,
                    int(row.pending_delete),
                ),
            )
        )

    def get_message(self, message_id: int) -> MessageRow | None:
        def _get(c):
            found = c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            return _row_to_message(found) if found is not None else None

        return self.read(_get)

    def get_messages_for_conversation(self, conversation_id: int) -> list[MessageRow]:
        return self.read(
            lambda c: [
                _row_to_message(r)
                for r in c.execute(
                    "SELECT * FROM messages WHERE conversationId = ? ORDER BY timestampMillis ASC",
                    (conversation_id,),
                )
            ]
        )

    def mark_conversation_read(self, conversation_id: int) -> None:
        self.write(
            lambda c: c.execute(
                "UPDATE messages SET isRead = 1 WHERE conversationId = ?"
                " AND direction = 'INCOMING' AND isRead = 0",
                (conversation_id,),
            )
        )

    def mark_conversation_unread(self, conversation_id: int) -> None:
        """Mark every inbound message in one thread unread again."""
        self.write(
            lambda c: c.execute(
                "UPDATE messages SET isRead = 0 WHERE conversationId = ?"
                " AND direction = 'INCOMING'",
                (conversation_id,),
            )
        )

    def mark_sent(self, message_id: int) -> None:
        self.write(lambda c: c.execute("UPDATE messages SET sent = 1 WHERE id = ?", (message_id,)))

    def mark_message_read(self, message_id: int) -> None:
        self.write(
            lambda c: c.execute("UPDATE messages SET isRead = 1 WHERE id = ?", (message_id,))
        )

    def pending_outgoing_sms(self) -> list[MessageRow]:
        """Outgoing SMS rows never confirmed sent (retry/reconcile seeds)."""
        return self.read(
            lambda c: [
                _row_to_message(r)
                for r in c.execute(
                    "SELECT * FROM messages WHERE direction = 'OUTGOING'"
                    " AND transport = 'SMS' AND sent = 0 ORDER BY timestampMillis ASC"
                )
            ]
        )

    def pending_outgoing_mms(self) -> list[MessageRow]:
        """Outgoing MMS rows never confirmed sent (retry seeds)."""
        return self.read(
            lambda c: [
                _row_to_message(r)
                for r in c.execute(
                    "SELECT * FROM messages WHERE direction = 'OUTGOING'"
                    " AND transport = 'MMS' AND sent = 0 ORDER BY timestampMillis ASC"
                )
            ]
        )

    def set_media_path(self, message_id: int, media_path: str | None) -> None:
        """Record the on-disk photo for one MMS row (tap-to-fetch fill)."""
        self.write(
            lambda c: c.execute(
                "UPDATE messages SET mediaPath = ? WHERE id = ?",
                (media_path, message_id),
            )
        )

    def mark_pending_delete(self, message_id: int) -> None:
        """Flag a row whose modem copy still needs deleting (crash-safe)."""
        self.write(
            lambda c: c.execute(
                "UPDATE messages SET pendingDelete = 1 WHERE id = ?", (message_id,)
            )
        )

    def clear_pending_delete(self, message_id: int) -> None:
        """Clear the flag once the modem ``Delete`` has succeeded."""
        self.write(
            lambda c: c.execute(
                "UPDATE messages SET pendingDelete = 0 WHERE id = ?", (message_id,)
            )
        )

    def pending_delete_rows(self) -> list[MessageRow]:
        """Rows imported but whose modem copy may not be deleted yet."""
        return self.read(
            lambda c: [
                _row_to_message(r)
                for r in c.execute("SELECT * FROM messages WHERE pendingDelete = 1")
            ]
        )

    def delete_message(self, message_id: int) -> None:
        self.write(lambda c: c.execute("DELETE FROM messages WHERE id = ?", (message_id,)))

    def delete_messages_for_conversation(self, conversation_id: int) -> None:
        self.write(
            lambda c: c.execute("DELETE FROM messages WHERE conversationId = ?", (conversation_id,))
        )

    # ---- find-or-create + inbound/outbound/quarantine ----

    def find_or_create_conversation(self, address: str) -> int:
        """Return the 1:1 thread for ``address``, creating it when absent.

        Lookup key is the normalized conversation key; on an exact miss
        existing rows are scanned format-tolerantly so one correspondent
        keeps one thread. New ids are wall-clock millis bumped past any
        existing row so two deliveries in the same millisecond never
        REPLACE-overwrite each other.
        """

        def _find(c):
            return self._find_or_create_locked(c, address)

        return self.write(_find)

    def persist_inbound_sms(
        self, address: str, body: str, date_millis: int, quarantined: bool = False
    ) -> int:
        """Persist an inbound SMS as unread; returns the message id."""

        def _persist(c):
            conversation_id = self._find_or_create_locked(c, address)
            if quarantined:
                c.execute(
                    "UPDATE conversations SET isQuarantined = 1 WHERE id = ?", (conversation_id,)
                )
            else:
                self._surface_locked(c, conversation_id)
            message_id = _fresh_id(c, "messages")
            c.execute(
                "INSERT INTO messages (id, conversationId, direction, transport, body,"
                " timestampMillis, senderAddress, isRead, sent)"
                " VALUES (?, ?, 'INCOMING', 'SMS', ?, ?, ?, 0, 1)",
                (message_id, conversation_id, body, date_millis, address),
            )
            return message_id

        return self.write(_persist)

    def persist_inbound_mms_metadata(
        self,
        address: str,
        date_millis: int,
        content_location: str | None = None,
        quarantined: bool = False,
    ) -> int:
        """Persist an inbound MMS metadata row (``[Photo]`` until tapped)."""

        def _persist(c):
            conversation_id = self._find_or_create_locked(c, address)
            if quarantined:
                c.execute(
                    "UPDATE conversations SET isQuarantined = 1 WHERE id = ?", (conversation_id,)
                )
            else:
                self._surface_locked(c, conversation_id)
            message_id = _fresh_id(c, "messages")
            c.execute(
                "INSERT INTO messages (id, conversationId, direction, transport, body,"
                " timestampMillis, senderAddress, isRead, sent, contentLocation)"
                " VALUES (?, ?, 'INCOMING', 'MMS', ?, ?, ?, 0, 1, ?)",
                (
                    message_id,
                    conversation_id,
                    core.COLLAPSED_PHOTO_PLACEHOLDER,
                    date_millis,
                    address,
                    content_location,
                ),
            )
            return message_id

        return self.write(_persist)

    def persist_outgoing_sms(self, address: str, body: str, now_millis: int | None = None) -> int:
        """Persist an outgoing SMS as read but not-yet-sent (``sent = 0``);
        the caller marks it sent only after transmit succeeds."""

        def _persist(c):
            conversation_id = self._find_or_create_locked(c, address)
            self._surface_locked(c, conversation_id)
            message_id = _fresh_id(c, "messages")
            stamp = now_millis if now_millis is not None else _now_millis()
            c.execute(
                "INSERT INTO messages (id, conversationId, direction, transport, body,"
                " timestampMillis, senderAddress, isRead, sent)"
                " VALUES (?, ?, 'OUTGOING', 'SMS', ?, ?, NULL, 1, 0)",
                (message_id, conversation_id, body, stamp),
            )
            return message_id

        return self.write(_persist)

    def persist_outgoing_mms(
        self,
        address: str,
        body: str = core.COLLAPSED_PHOTO_PLACEHOLDER,
        media_path: str | None = None,
        now_millis: int | None = None,
    ) -> int:
        """Persist an outgoing photo row (MMS transport, pending send)."""

        def _persist(c):
            conversation_id = self._find_or_create_locked(c, address)
            self._surface_locked(c, conversation_id)
            message_id = _fresh_id(c, "messages")
            stamp = now_millis if now_millis is not None else _now_millis()
            c.execute(
                "INSERT INTO messages (id, conversationId, direction, transport, body,"
                " timestampMillis, senderAddress, isRead, sent, mediaPath)"
                " VALUES (?, ?, 'OUTGOING', 'MMS', ?, ?, NULL, 1, 0, ?)",
                (message_id, conversation_id, body, stamp, media_path),
            )
            return message_id

        return self.write(_persist)

    def quarantine_deliver(self, conversation_id: int) -> None:
        """Release a held thread into the inbox (messages stay unread)."""

        def _deliver(c):
            c.execute(
                "UPDATE conversations SET isArchived = 0, isQuarantined = 0 WHERE id = ?",
                (conversation_id,),
            )

        self.write(_deliver)

    def quarantine_delete(self, conversation_id: int) -> None:
        """Delete a held thread and every message in it."""
        self.delete_conversation(conversation_id)

    def quarantine_sender(self, conversation_id: int) -> str | None:
        row = self.get_conversation(conversation_id)
        if row is None:
            return None
        dialable = row.primary_dialable()
        if dialable:
            return dialable
        for part in row.addresses():
            if part.strip():
                return part
        return None

    def _find_or_create_locked(self, c: sqlite3.Connection, address: str) -> int:
        key = phone_numbers.conversation_key(address)
        dialable = phone_numbers.canonical_dialable_configured(address)
        if key:
            found = c.execute(
                "SELECT * FROM conversations WHERE participantAddresses = ? LIMIT 1", (key,)
            ).fetchone()
            if found is not None:
                self._maybe_upgrade_dialable_locked(c, found["id"], dialable)
                return found["id"]
        for row in c.execute("SELECT * FROM conversations"):
            parts = [p for p in row["participantAddresses"].split(ADDRESS_DELIMITER) if p.strip()]
            if any(phone_numbers.matches(p, address) for p in parts):
                self._maybe_upgrade_dialable_locked(c, row["id"], dialable)
                return row["id"]
        stored = key if key else address.strip()
        dialable_stored = dialable if dialable else stored
        new_id = _fresh_id(c, "conversations")
        c.execute(
            "INSERT INTO conversations (id, participantAddresses, dialableAddress)"
            " VALUES (?, ?, ?)",
            (new_id, stored, dialable_stored),
        )
        return new_id

    @staticmethod
    def _maybe_upgrade_dialable_locked(
        c: sqlite3.Connection, conversation_id: int, candidate: str
    ) -> None:
        if not (candidate or "").strip():
            return
        try:
            current = c.execute(
                "SELECT dialableAddress FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return
        if current is None:
            return
        stored = current["dialableAddress"] if "dialableAddress" in current.keys() else ""
        if phone_numbers.should_replace_dialable(stored or "", candidate):
            c.execute(
                "UPDATE conversations SET dialableAddress = ? WHERE id = ?",
                (candidate.strip(), conversation_id),
            )

    @staticmethod
    def _surface_locked(c: sqlite3.Connection, conversation_id: int) -> None:
        c.execute(
            "UPDATE conversations SET isArchived = 0, isQuarantined = 0"
            " WHERE id = ? AND (isArchived = 1 OR isQuarantined = 1)",
            (conversation_id,),
        )

    # ---- blocklist / starred ----

    def blocklist_add(self, kind: str, value: str) -> None:
        if kind not in BLOCKLIST_KINDS:
            raise ValueError(f"unknown blocklist kind: {kind!r}")
        trimmed = value.strip()
        if not trimmed:
            return
        self.write(
            lambda c: c.execute(
                "REPLACE INTO blocklist (kind, value) VALUES (?, ?)", (kind, trimmed)
            )
        )

    def blocklist_remove(self, kind: str, value: str) -> None:
        self.write(
            lambda c: c.execute("DELETE FROM blocklist WHERE kind = ? AND value = ?", (kind, value))
        )

    def blocklist_list(self, kind: str | None = None) -> list[tuple[str, str]]:
        def _list(c):
            if kind is None:
                rows = c.execute("SELECT kind, value FROM blocklist")
            else:
                rows = c.execute("SELECT kind, value FROM blocklist WHERE kind = ?", (kind,))
            return [(r["kind"], r["value"]) for r in rows]

        return self.read(_list)

    def is_address_blocked(self, address: str) -> bool:
        senders = [value for _, value in self.blocklist_list("sender")]
        return any(phone_numbers.matches(address, s) for s in senders)

    def star(self, address: str) -> None:
        trimmed = address.strip()
        if not trimmed:
            return
        self.write(lambda c: c.execute("REPLACE INTO starred (address) VALUES (?)", (trimmed,)))

    def unstar(self, address: str) -> None:
        """Remove every stored variant matching ``address``; idempotent."""

        def _unstar(c):
            rows = [r["address"] for r in c.execute("SELECT address FROM starred")]
            for stored in rows:
                if phone_numbers.matches(address, stored):
                    c.execute("DELETE FROM starred WHERE address = ?", (stored,))

        self.write(_unstar)

    def is_starred(self, address: str) -> bool:
        return any(phone_numbers.matches(address, s) for s in self.starred_list())

    def starred_list(self) -> list[str]:
        return self.read(lambda c: [r["address"] for r in c.execute("SELECT address FROM starred")])

    # ---- settings / meta ----

    def get_setting(self, key: str, default: object = None) -> object:
        def _get(c):
            found = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if found is None:
                return default
            return json.loads(found["value"])

        return self.read(_get)

    def set_setting(self, key: str, value: object) -> None:
        payload = json.dumps(value)
        self.write(
            lambda c: c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, payload))
        )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        def _get(c):
            found = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return found["value"] if found is not None else default

        return self.read(_get)

    def set_meta(self, key: str, value: str) -> None:
        self.write(lambda c: c.execute("REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)))


def _fresh_id(c: sqlite3.Connection, table: str) -> int:
    if table == "conversations":
        probe = "SELECT 1 FROM conversations WHERE id = ?"
    elif table == "messages":
        probe = "SELECT 1 FROM messages WHERE id = ?"
    else:  # pragma: no cover - internal tables only
        raise ValueError(f"unknown table: {table!r}")
    new_id = _now_millis()
    while c.execute(probe, (new_id,)).fetchone() is not None:
        new_id += 1
    return new_id


def _now_millis() -> int:
    return int(time.time() * 1000)

