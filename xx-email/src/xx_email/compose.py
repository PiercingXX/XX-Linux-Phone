"""Compose helpers: prefill, MIME, attachments, drafts (stdlib only).

Ports ``ComposeViewModel``/``ComposeScreen`` prefill, ``MimeComposer``
composition, ``MailRepository.guessMime``/``enqueueSend`` staging and the
schedule presets from ``ref/xx-email``. Every function here is
unit-testable: callers pass directories, message snapshots and clocks
in; GTK pages and worker threads call in from off-loop threads.

Threading headers follow the Android app exactly: ``In-Reply-To`` is
the quoted message's ``Message-ID`` and ``References`` is the prior
chain plus that parent, space-separated with duplicates dropped.
Delivery stays REST-only; SMTP is never used.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email import policy
from email.message import EmailMessage
from pathlib import Path

from xx_email.domain import Recipients, SafePaths

MAX_TOTAL_ATTACHMENT_BYTES = 18 * 1024 * 1024
MAX_UPLOAD_BYTES = MAX_TOTAL_ATTACHMENT_BYTES
COPY_BUFFER_BYTES = 64 * 1024
DRAFTS_DIR_NAME = "drafts"
UPLOADS_DIR_NAME = "uploads"
_STORE_SEQ = itertools.count(1)


@dataclass
class StagedAttachment:
    """One file staged for sending: safe name, MIME type, raw bytes."""

    name: str
    mime_type: str
    data: bytes = field(repr=False)


@dataclass
class ComposeRequest:
    """Validated send request (mirrors the Android ComposeRequest)."""

    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    thread_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    attachments: list[StagedAttachment] = field(default_factory=list)


@dataclass
class OriginalMessage:
    """Quoted-message snapshot the prefill reads (store row + full fetch)."""

    id: str = ""
    thread_id: str = ""
    subject: str = ""
    from_address: str = ""
    to_csv: str = ""
    cc_csv: str = ""
    date: int = 0
    snippet: str = ""
    body_plain: str | None = None
    message_id_header: str | None = None
    references_header: str | None = None
    has_attachments: bool = False


@dataclass
class Prefill:
    """Reply/reply-all/forward field values plus threading headers."""

    to: str = ""
    cc: str = ""
    subject: str = ""
    body: str = ""
    thread_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    offer_attachments: bool = False


def prefill_key(account: str, initial_to: str, initial_cc: str,
                initial_subject: str, thread_id: str,
                quote_message_id: str, mode: str) -> str:
    """Identity of one prefill argument set.

    The page keeps the last key: identical args (a config change or a
    re-push of the same route) keep in-progress edits, while fresh
    args reset the draft so bodies never leak between drafts.
    """
    return "|".join((account, initial_to, initial_cc, initial_subject,
                     thread_id, quote_message_id, mode))


def build_references(prior: str | None,
                     parent_message_id: str | None) -> str | None:
    """Prior References chain plus the parent Message-ID, deduped.

    Tokens split on any whitespace so a folded prior chain (CRLF + WSP)
    never repeats the parent. Returns None when both parts are blank.
    """
    tokens: list[str] = []
    for part in (prior, parent_message_id):
        if part is None:
            continue
        for token in part.split():
            if token and token not in tokens:
                tokens.append(token)
    return " ".join(tokens) if tokens else None


def full_date(epoch_ms: int, now: datetime | None = None) -> str:
    """Long date line for the quote header ("Jan 5, 2026 3:04 PM").

    Mirrors Android ``fullDate`` (``MMM d, yyyy h:mm a``) in the local
    timezone; blank for a missing date.
    """
    if not epoch_ms or epoch_ms <= 0:
        return ""
    moment = datetime.fromtimestamp(epoch_ms / 1000).astimezone()
    _ = now
    hour = moment.hour % 12 or 12
    meridiem = "PM" if moment.hour >= 12 else "AM"
    return (f"{moment.strftime('%b')} {moment.day}, "
            f"{moment.year} {hour}:{moment.minute:02d} {meridiem}")


def quote_body(plain: str, from_address: str, date_ms: int,
               mode: str) -> str:
    """Quoted body exactly like the Android app.

    Replies prefix every line with "> "; forwards inline the original
    unquoted after the same "On <date>, <sender> wrote:" header.
    """
    wrote = (f"On {full_date(date_ms) or 'an unknown date'}, "
             f"{from_address or 'an unknown sender'} wrote:")
    if mode == "forward":
        return f"\n\n{wrote}\n\n{plain}\n"
    quoted = "\n".join(f"> {line}" for line in plain.splitlines())
    return f"\n\n{wrote}\n\n{quoted}\n"


def prefill_for_mode(account: str, mode: str, original: OriginalMessage,
                     initial_to: str = "", initial_cc: str = "",
                     initial_subject: str = "",
                     thread_id_arg: str = "") -> Prefill:
    """Field values for reply / reply-all / forward (pure prefill core).

    ``initial_*`` seed a fresh compose; the quoted original then
    overrides them per mode, exactly like ``ComposeViewModel.prefill``:
    reply recomputes To from the sender, reply-all recomputes To + Cc,
    forward clears recipients and offers the original attachments.
    """
    thread_id = thread_id_arg.strip() or None
    if thread_id is None and original.thread_id.strip():
        thread_id = original.thread_id.strip()
    if not original.id:
        return Prefill(to=initial_to, cc=initial_cc,
                       subject=initial_subject, thread_id=thread_id)
    to = initial_to
    cc = initial_cc
    subject = initial_subject
    if mode == "reply":
        to = ", ".join(Recipients.reply_to(
            original.from_address, original.to_csv, account))
        cc = ""
    elif mode == "reply_all":
        to_set, cc_set = Recipients.reply_all(
            original.from_address, original.to_csv, original.cc_csv,
            account)
        to = ", ".join(to_set)
        cc = ", ".join(cc_set)
    elif mode == "forward":
        to = ""
        cc = ""
        subject = subject if subject.startswith("Fwd:") \
            else f"Fwd: {original.subject}"
    if mode != "forward" and not subject.strip():
        subject = f"Re: {original.subject}"
    plain = original.body_plain if original.body_plain is not None \
        else original.snippet
    body = quote_body(plain or "", original.from_address, original.date,
                      mode)
    in_reply_to = (original.message_id_header or "").strip() or None
    references = build_references(original.references_header,
                                  in_reply_to)
    return Prefill(to=to, cc=cc, subject=subject, body=body,
                   thread_id=thread_id, in_reply_to=in_reply_to,
                   references=references,
                   offer_attachments=bool(
                       mode == "forward" and original.has_attachments))


def guess_mime(name: str) -> str:
    """Extension MIME guess (Android MailRepository.guessMime port).

    The picker/portal-provided type always wins; this is only the
    fallback for staged files whose type is unknown.
    """
    lowered = (name or "").lower()
    if lowered.endswith(".pdf"):
        return "application/pdf"
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".gif"):
        return "image/gif"
    if lowered.endswith(".txt"):
        return "text/plain"
    if lowered.endswith(".html"):
        return "text/html"
    if lowered.endswith(".zip"):
        return "application/zip"
    return "application/octet-stream"


def split_mime(content_type: str) -> tuple[str, str]:
    """Split a content type into (maintype, subtype), tolerating junk."""
    text = (content_type or "").split(";", 1)[0].strip().lower()
    if "/" in text:
        main, _, sub = text.partition("/")
        if main.strip() and sub.strip():
            return (main.strip(), sub.strip())
    return ("application", "octet-stream")


def validated_request(to_raw: str, cc_raw: str, bcc_raw: str,
                      subject: str, body: str,
                      thread_id: str | None = None,
                      in_reply_to: str | None = None,
                      references: str | None = None,
                      attachments: list[StagedAttachment] | None = None,
                      ) -> ComposeRequest:
    """Parse + validate send fields; raises ValueError with no recipient."""
    request = ComposeRequest(
        to=Recipients.parse_validated(to_raw),
        cc=Recipients.parse_validated(cc_raw),
        bcc=Recipients.parse_validated(bcc_raw),
        subject=subject.strip() or "(no subject)",
        body_text=body,
        thread_id=(thread_id or "").strip() or None,
        in_reply_to=(in_reply_to or "").strip() or None,
        references=(references or "").strip() or None,
        attachments=list(attachments or []),
    )
    if not request.to and not request.cc and not request.bcc:
        raise ValueError("No valid recipients")
    return request


def compose_message(account_email: str, request: ComposeRequest) -> bytes:
    """Build the RFC822 payload (multipart/mixed when attaching).

    UTF-8 subject, ``In-Reply-To``/``References`` chaining, explicit
    attachment types, and the 18 MB total cap accounting for base64
    inflation (the cap applies to raw bytes so the encoded form stays
    under Gmail's 25 MB limit). Raises ValueError on misuse.
    """
    if not request.to and not request.cc and not request.bcc:
        raise ValueError("No recipients")
    total = sum(len(att.data) for att in request.attachments)
    if total > MAX_TOTAL_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachments total {total // 1024 // 1024} MB exceeds the "
            f"{MAX_TOTAL_ATTACHMENT_BYTES // 1024 // 1024} MB limit")
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = account_email
    if request.to:
        message["To"] = ", ".join(request.to)
    if request.cc:
        message["Cc"] = ", ".join(request.cc)
    if request.bcc:
        message["Bcc"] = ", ".join(request.bcc)
    message["Subject"] = request.subject
    if request.in_reply_to:
        message["In-Reply-To"] = request.in_reply_to
    built = build_references(request.references, request.in_reply_to)
    if built:
        message["References"] = built
    if not request.attachments:
        message.set_content(request.body_text or "", charset="utf-8")
    else:
        message.set_content(request.body_text or "", charset="utf-8")
        message.make_mixed()
        for attachment in request.attachments:
            main, sub = split_mime(attachment.mime_type
                                   or guess_mime(attachment.name))
            message.add_attachment(
                attachment.data, maintype=main, subtype=sub,
                filename=attachment.name)
    return bytes(message.as_bytes())


def raw_base64url(payload: bytes) -> str:
    """Gmail ``raw`` form: base64url without padding (parity helper)."""
    return base64.urlsafe_b64encode(bytes(payload)).rstrip(b"=").decode(
        "ascii")


def schedule_presets(now: datetime) -> list[tuple[str, int]]:
    """Schedule-send presets mirroring Gmail's UX constants.

    Takes an aware ``now``; returns (label, epoch-ms) pairs the worker
    waits for. Times are local wall-clock, like the Android presets.
    """
    tomorrow = (now + timedelta(days=1)).date()
    morning = datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                       8, 0, tzinfo=now.tzinfo)
    afternoon = datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                         13, 0, tzinfo=now.tzinfo)
    monday = (now + timedelta(days=1)).date()
    while monday.weekday() != 0:
        monday += timedelta(days=1)
    monday8 = datetime(monday.year, monday.month, monday.day, 8, 0,
                       tzinfo=now.tzinfo)
    return [
        ("Tomorrow 8:00 AM", int(morning.timestamp() * 1000)),
        ("Tomorrow 1:00 PM", int(afternoon.timestamp() * 1000)),
        ("Monday 8:00 AM", int(monday8.timestamp() * 1000)),
        ("In 1 hour", int((now + timedelta(hours=1)).timestamp()
                          * 1000)),
    ]


def stage_upload(uploads_dir: str | os.PathLike, display_name: str,
                 source: str | os.PathLike,
                 mime_type: str | None = None) -> StagedAttachment:
    """Copy a picked file into the uploads dir under a safe name.

    Provider display names are reduced to a safe single segment, the
    copy is capped at 18 MB so a huge file cannot exhaust disk, and
    the portal MIME type rides along (extension guess is the fallback
    at send time). Raises ValueError when nothing safe remains or the
    file exceeds the cap.
    """
    target_dir = Path(uploads_dir)
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(target_dir, 0o700)
    except OSError:
        pass
    raw = display_name or Path(str(source)).name or "attachment"
    name = SafePaths.child_name(raw)
    if name is None:
        raise ValueError("Could not use that file name")
    chunks: list[bytes] = []
    total = 0
    with open(source, "rb") as handle:
        while True:
            piece = handle.read(COPY_BUFFER_BYTES)
            if not piece:
                break
            total += len(piece)
            if total > MAX_UPLOAD_BYTES:
                raise ValueError(
                    f"Attachment exceeds the "
                    f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB limit")
            chunks.append(piece)
    data = b"".join(chunks)
    return StagedAttachment(name=name,
                            mime_type=(mime_type or "").strip()
                            or guess_mime(name), data=data)


def store_upload(uploads_dir: str | os.PathLike,
                 display_name: str, source: str | os.PathLike,
                 prefix: str = "pick") -> tuple[Path, str, int]:
    """Stream a picked file into the uploads dir under a safe name.

    Same SafePaths + 18 MB cap rules as stage_upload, but the bytes
    stay on disk: returns (stored path, safe name, size) so compose
    sessions never hold megabytes in memory. Raises ValueError when
    the name is unusable or the file exceeds the cap.
    """
    target_dir = Path(uploads_dir)
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(target_dir, 0o700)
    except OSError:
        pass
    name = SafePaths.child_name(display_name) or "attachment"
    stamp = f"{prefix}-{os.getpid()}-{next(_STORE_SEQ)}-{name}"
    target = target_dir / stamp[-180:]
    if not SafePaths.is_inside(target_dir, target):
        raise ValueError("Resolved upload path escapes its directory")
    total = 0
    with open(source, "rb") as handle_in, open(target, "wb") as handle_out:
        while True:
            piece = handle_in.read(COPY_BUFFER_BYTES)
            if not piece:
                break
            total += len(piece)
            if total > MAX_UPLOAD_BYTES:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise ValueError(
                    f"Attachment exceeds the "
                    f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB limit")
            handle_out.write(piece)
    return (target, name, total)


def copy_staged_to_uploads(uploads_dir: str | os.PathLike,
                           staged: StagedAttachment) -> Path:
    """Persist staged bytes for the compose session (SafePaths-checked)."""
    target_dir = Path(uploads_dir)
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = SafePaths.child_name(staged.name) or "attachment"
    stamp = f"{os.getpid()}-{id(staged) % 100000:d}-{name}"
    target = target_dir / stamp
    if not SafePaths.is_inside(target_dir, target):
        raise ValueError("Resolved upload path escapes its directory")
    target.write_bytes(bytes(staged.data))
    return target


def draft_key(account: str, thread_id: str, quote_id: str,
              mode: str) -> str:
    """Stable file-safe identity for one compose draft."""
    parts = []
    for raw in (account, thread_id, quote_id, mode):
        cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".")
                          else "_" for ch in (raw or "").strip())
        parts.append(cleaned[:80] or "none")
    return "__".join(parts) + ".json"


def draft_path(data_dir: str | os.PathLike, account: str,
               thread_id: str = "", quote_id: str = "",
               mode: str = "new") -> Path:
    """Draft file for one compose identity (never the payload itself)."""
    return Path(data_dir) / DRAFTS_DIR_NAME / draft_key(
        account, thread_id, quote_id, mode)


def save_draft(data_dir: str | os.PathLike, account: str, fields: dict,
               thread_id: str = "", quote_id: str = "",
               mode: str = "new") -> Path:
    """Atomically autosave a draft (tmp file + rename, mode 0700 dirs)."""
    drafts_dir = Path(data_dir) / DRAFTS_DIR_NAME
    drafts_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(drafts_dir, 0o700)
    except OSError:
        pass
    target = drafts_dir / draft_key(account, thread_id, quote_id, mode)
    payload = json.dumps({
        "account": account,
        "thread_id": thread_id,
        "quote_id": quote_id,
        "mode": mode,
        "to": fields.get("to", ""),
        "cc": fields.get("cc", ""),
        "bcc": fields.get("bcc", ""),
        "subject": fields.get("subject", ""),
        "body": fields.get("body", ""),
    }, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(drafts_dir), prefix=".draft-",
                                    suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        Path(tmp_name).replace(target)
    except BaseException:
        try:
            Path(tmp_name).unlink()
        except OSError:
            pass
        raise
    return target


def load_draft(data_dir: str | os.PathLike, account: str,
               thread_id: str = "", quote_id: str = "",
               mode: str = "new") -> dict | None:
    """Read back an autosaved draft; corrupt rows read as missing."""
    try:
        raw = draft_path(data_dir, account, thread_id, quote_id,
                         mode).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return {key: payload.get(key, "") for key in
            ("to", "cc", "bcc", "subject", "body")}


def clear_draft(data_dir: str | os.PathLike, account: str,
                thread_id: str = "", quote_id: str = "",
                mode: str = "new") -> None:
    """Drop an autosaved draft after send or discard."""
    try:
        draft_path(data_dir, account, thread_id, quote_id,
                   mode).unlink(missing_ok=True)
    except OSError:
        pass


def list_drafts(data_dir: str | os.PathLike,
                account: str) -> list[dict]:
    """Autosaved local drafts for one account, newest first.

    Used to seed the Drafts folder alongside server DRAFT threads:
    each row carries the compose identity needed to reopen it.
    """
    drafts_dir = Path(data_dir) / DRAFTS_DIR_NAME
    try:
        files = sorted(drafts_dir.glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    rows = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("account") != account:
            continue
        rows.append({
            "account": account,
            "thread_id": payload.get("thread_id", ""),
            "quote_id": payload.get("quote_id", ""),
            "mode": payload.get("mode", "new") or "new",
            "subject": payload.get("subject", "") or "(no subject)",
            "to": payload.get("to", ""),
            "updated_at": int(path.stat().st_mtime * 1000),
        })
    return rows


def outbox_status_line(kind: str, state: str, target_at: int,
                       error: str | None = None,
                       now_ms: int = 0) -> str:
    """One-line Outbox row note: queued, scheduled, sending, failed.

    Scheduled rows show their local send time so the folder doubles
    as the schedule list; failed rows name the error when one exists.
    """
    if state == "FAILED":
        return f"Failed — {error}" if error else "Failed"
    if state == "SENDING":
        return "Sending…"
    if state == "SENT":
        return "Sent"
    if state == "CANCELLED":
        return "Cancelled"
    if kind == "SCHEDULED_SEND":
        if target_at and now_ms and target_at > now_ms:
            moment = datetime.fromtimestamp(
                target_at / 1000).astimezone()
            stamp = (f"{moment.strftime('%b')} {moment.day} "
                     f"{moment.hour:02d}:{moment.minute:02d}")
            return f"Scheduled — {stamp}"
        return "Scheduled"
    return "Queued"


def describe_undo_window(undo_seconds: int) -> str:
    """Toast note naming the undo window after enqueue."""
    return f"Queued — undo within {int(undo_seconds):d} s"
