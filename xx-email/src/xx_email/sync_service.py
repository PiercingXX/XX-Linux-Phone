"""Gmail sync engine: full plus delta sync over an injectable transport.

Ports MailRepository.sync/deltaSync/initialSync/hydrateThreads and the
SyncWorker one-pass-per-account shape (ref/xx-email data/repo/ and
sync/). Blocking by contract: run it on a worker thread. The systemd
unit is a separate process; manual kicks use sync_async, which hops
threads and posts the outcome back with GLib.idle_add (direct call
when GLib is unavailable, e.g. in tests).

Token access never touches the GTK main loop: production wiring
reaches the synchronous TokenStore only through TokenStoreWorker
(review note 4), and refresh network I/O runs on this same worker.
historyId stays an opaque string throughout, and the sync checkpoint
never advances past work that did not fully land. Full sync pages
through the listing with a per-page checkpoint in folder_pages, so a
killed pass resumes where it stopped instead of starting over.
"""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field

from xx_email import db
from xx_email import outbox as outbox_mod
from xx_email.auth import ReauthNeeded
from xx_email.domain import LabelCsv, NewMailDetector, ThreadAggregation
from xx_email.gmail_client import (
    BASE_URL,
    DEFAULT_MAX_RETRIES,
    GmailClient,
    GmailTransportError,
    HttpResponse,
)
from xx_email.token_store import TokenRecord, TokenStoreWorker

MAX_THREADS_PER_DELTA = 60
PAGE_SIZE = 50
LIST_MAX_RESULTS = 50
HISTORY_MAX_RESULTS = 100
INITIAL_PAGE_COUNT = 4
INBOX_PAGE_KEY = "INBOX"
# Progress cursor for an in-progress full sync. Kept apart from the
# INBOX load-more cursor so a stale load-more position can never make
# a fresh full sync skip the newest mail; deleted when the pass ends.
FULL_SYNC_PROGRESS_KEY = "INBOX_FULL_SYNC"


@dataclass
class SyncOutcome:
    """One account pass. Failures are values, never exceptions."""

    email: str
    ok: bool
    full: bool = False
    new_ids: list[str] = field(default_factory=list)
    checkpoint: str | None = None
    error: str | None = None
    offline: bool = False
    reauth: bool = False
    follow_up: bool = False


def production_refresh(store_worker: TokenStoreWorker,
                       clock=None):
    """Refresh callable wiring the worker-wrapped TokenStore.

    Loads the stored grant off the token worker thread, exchanges it
    at the token endpoint, and persists rotation. invalid_grant
    surfaces as ReauthNeeded for the sign-in-again banner.
    """
    from xx_email import auth as auth_module

    now_ms = clock or (lambda: int(time.time() * 1000))

    def refresh(email: str, _rejected_token: str) -> str:
        record = store_worker.load(email)
        if record is None or not record.refresh_token:
            raise ReauthNeeded(
                f"No stored grant for {email}; sign in again.")
        response = auth_module.refresh_access_token(
            record.client_id, record.refresh_token)
        updated = TokenRecord(
            refresh_token=response.refresh_token or
            record.refresh_token,
            client_id=record.client_id,
            updated_at=now_ms(),
            extra=record.extra,
        )
        store_worker.save(email, updated)
        return response.access_token

    return refresh


class SyncEngine:
    """One engine per local store connection.

    conn is a db connection owned by the calling worker thread (one
    connection per thread). transport is
    (method, url, headers, body) -> HttpResponse; tests inject a fake
    and assert on requests. tokens seeds per-account access tokens;
    refresh(email, rejected_token) mints fresh ones.
    """

    def __init__(self, conn, transport, tokens=None,
                 refresh=None, clock=None, on_follow_up=None,
                 base_url=None, retry_sleep=None, retry_jitter=None,
                 retry_max_retries=None, files_dir=None,
                 send_and_archive=False,
                 ) -> None:
        self.conn = conn
        self._transport = transport
        self.requests: list[dict] = []
        self._tokens = dict(tokens or {})
        self._refresh = refresh
        self._now_ms = clock or (lambda: int(time.time() * 1000))
        self._on_follow_up = on_follow_up
        self._files_dir = files_dir
        self._send_and_archive = send_and_archive
        self._base_url = base_url
        self._retry_sleep = retry_sleep
        self._retry_jitter = retry_jitter
        self._retry_max_retries = retry_max_retries
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="xxemail-sync")

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    # ------------------------------------------------------------ client

    def _logging_transport(self, method: str, url: str,
                           headers: dict, body: bytes | None,
                           ) -> HttpResponse:
        self.requests.append({"method": method, "url": url})
        return self._transport(method, url, headers, body)

    def _get_token(self, email: str) -> str:
        cached = self._tokens.get(email)
        if cached:
            return cached
        if self._refresh is None:
            return ""
        fresh = self._refresh(email, "")
        self._tokens[email] = fresh
        return fresh

    def _refresh_token(self, email: str, rejected: str) -> str:
        if self._refresh is None:
            raise ReauthNeeded(
                f"Access rejected for {email}; sign in again.")
        fresh = self._refresh(email, rejected)
        self._tokens[email] = fresh
        return fresh

    def _client_for(self, email: str) -> GmailClient:
        extra = {}
        if self._base_url is not None:
            root = self._base_url.rstrip("/") + "/"
            extra["base_url"] = root
            extra["upload_base_url"] = root + "upload/gmail/v1/users/me/"
        else:
            extra["base_url"] = BASE_URL
        if self._retry_sleep is not None:
            extra["sleep"] = self._retry_sleep
        if self._retry_jitter is not None:
            extra["jitter"] = self._retry_jitter
        extra["max_retries"] = self._retry_max_retries \
            if self._retry_max_retries is not None else DEFAULT_MAX_RETRIES
        return GmailClient(
            transport=self._logging_transport,
            get_token=lambda _e=email: self._get_token(_e),
            refresh_token=lambda tok, _e=email: self._refresh_token(
                _e, tok),
            **extra,
        )

    # -------------------------------------------------------------- passes

    def sync_all(self, force_full: bool = False,
                 ) -> list[SyncOutcome]:
        return [self.sync_account(account.email,
                                  force_full=force_full)
                for account in db.all_accounts(self.conn)]

    def sync_account(self, email: str,
                     force_full: bool = False) -> SyncOutcome:
        """Run one pass. Call from a worker thread, never the UI."""
        try:
            account = db.get_account(self.conn, email)
            client = self._client_for(email)
            profile = client.get_profile()
            if account is None:
                # New row, but the history checkpoint stays empty until
                # the first full pass lands: writing profile.historyId
                # here would skip the whole window on a crash before
                # the pass completes.
                db.upsert_account(self.conn, db.Account(
                    email=email,
                    display_name=profile.email_address,
                    history_id=None))
                stored = None
            else:
                db.upsert_account(self.conn, db.Account(
                    email=email, display_name=profile.email_address,
                    history_id=account.history_id,
                    last_sync_at=account.last_sync_at))
                stored = account.history_id
            self._sync_labels(email, client)
            end = profile.history_id
            full = stored is None or force_full or end is None
            known = None
            follow_up = False
            if full:
                self._initial_sync(email, client)
                db.update_sync_point(self.conn, email, end,
                                     self._now_ms())
            else:
                known = set(db.inbox_ids(self.conn, email))
                follow_up = self._delta_sync(email, client, stored,
                                             end)
            if follow_up and self._on_follow_up is not None:
                self._on_follow_up(email)
            unread = db.unread_inbox(self.conn, email)
            fresh = NewMailDetector.new_arrivals(known, unread)
            self._drain_outbox(email, client)
            checkpoint = db.get_account(self.conn, email)
            return SyncOutcome(
                email=email, ok=True, full=full,
                new_ids=[row.id for row in fresh],
                checkpoint=checkpoint.history_id
                if checkpoint is not None else None,
                follow_up=follow_up,
            )
        except ReauthNeeded as exc:
            return SyncOutcome(email=email, ok=False,
                               error=str(exc), reauth=True)
        except GmailTransportError as exc:
            return SyncOutcome(email=email, ok=False,
                               error=str(exc),
                               offline=exc.status is None)
        except Exception as exc:  # never throw: surface it
            return SyncOutcome(email=email, ok=False,
                               error=str(exc))

    def sync_async(self, email: str, done,
                   force_full: bool = False):
        """Kick a pass on the worker pool; done(outcome) on return.

        The handed-over connection is used on a pool thread: open it
        with check_same_thread=False (and serialize passes sharing
        one connection).
        """
        future = self._executor.submit(self.sync_account, email,
                                       force_full)

        def _finish(completed) -> None:
            try:
                outcome = completed.result()
            except Exception as exc:  # defensive: sync_account
                outcome = SyncOutcome(email=email, ok=False,  # composes
                                      error=str(exc))
            self._post_back(done, outcome)

        future.add_done_callback(_finish)
        return future

    def _post_back(self, done, outcome) -> None:
        try:
            from gi.repository import GLib
        except (ImportError, ValueError):
            done(outcome)
            return
        GLib.idle_add(done, outcome)

    def _drain_outbox(self, email: str, client) -> None:
        """Send due outbox rows (undo window / schedule elapsed).

        Runs inside the one-pass worker after sync, so the systemd
        timer unit and manual kicks share the send path. Never raises:
        per-row failures land on the row for banner retry, and rows
        still inside their undo window or scheduled for the future
        stay QUEUED. Skipped when the engine has no files dir (tests).
        """
        if self._files_dir is None:
            return

        def _send(account: str, thread_id, payload: bytes):
            if account != email:
                raise ValueError(
                    f"Outbox row for {account} reached"
                    f" {email}'s sync")
            return client.send(bytes(payload), thread_id)

        def _on_sent(entry) -> None:
            if self._send_and_archive and entry.thread_id:
                self._archive_after_send(email, client,
                                         entry.thread_id)

        try:
            outbox_mod.drain_due_outbox(
                self.conn, self._files_dir, _send, self._now_ms(),
                on_sent=_on_sent, account=email)
        except Exception:
            pass

    def _archive_after_send(self, email: str, client,
                            thread_id: str) -> None:
        """Send-and-archive: SENT is already recorded, archive best-effort."""
        try:
            client.modify_thread(thread_id, remove=["INBOX"])
        except Exception:
            return
        try:
            row = db.get_thread(self.conn, email, thread_id)
            if row is not None:
                db.set_inbox_and_labels(
                    self.conn, email, thread_id, False,
                    LabelCsv.remove(row.labels_csv, "INBOX"))
        except Exception:
            pass

    # -------------------------------------------------------------- stages

    def _sync_labels(self, email: str, client: GmailClient) -> None:
        rows = [db.Label(account_email=email, id=label.id,
                         name=label.name, type=label.type,
                         color_bg=label.color_bg,
                         color_text=label.color_text)
                for label in client.list_labels()]
        if rows:
            db.upsert_labels(self.conn, rows)

    def _initial_sync(self, email: str, client: GmailClient) -> None:
        """Bounded full pull of recent INBOX pages plus hydration.

        Resumable: each listed page is hydrated before the progress
        cursor advances, so a pass killed mid-way restarts at the
        saved cursor and never re-pulls landed pages. The INBOX
        load-more cursor is written only on completion.
        """
        saved = db.get_folder_page(self.conn, email,
                                   FULL_SYNC_PROGRESS_KEY)
        page_token = saved.next_page_token \
            if saved is not None else None
        pages = 0
        seen: set[str] = set()
        while True:
            page = client.list_threads(label_ids=["INBOX"],
                                       max_results=LIST_MAX_RESULTS,
                                       page_token=page_token)
            fresh = [ref.id for ref in page.threads
                     if ref.id not in seen]
            seen.update(fresh)
            self._hydrate(email, client, fresh)
            page_token = page.next_page_token
            pages += 1
            if page_token is None or pages >= INITIAL_PAGE_COUNT:
                break
            db.upsert_folder_page(self.conn, db.FolderPage(
                account_email=email, folder_key=FULL_SYNC_PROGRESS_KEY,
                next_page_token=page_token))
        db.upsert_folder_page(self.conn, db.FolderPage(
            account_email=email, folder_key=INBOX_PAGE_KEY,
            next_page_token=page_token))
        db.delete_folder_page(self.conn, email, FULL_SYNC_PROGRESS_KEY)

    def _delta_sync(self, email: str, client: GmailClient,
                    start: str, fallback_end: str | None) -> bool:
        """History walk. Returns True when a follow-up should run."""
        touched: list[str] = []
        touched_set: set[str] = set()
        need_lookup: list[str] = []
        lookup_set: set[str] = set()
        deleted: list[str] = []
        deleted_set: set[str] = set()
        last_processed = start
        saw_any = False
        overflow = False
        page_token = None
        try:
            while True:
                page = client.list_history(
                    start_history_id=start,
                    max_results=HISTORY_MAX_RESULTS,
                    page_token=page_token)
                for item in page.history:
                    saw_any = True
                    self._collect(item, touched, touched_set,
                                  need_lookup, lookup_set, deleted,
                                  deleted_set)
                    if len(touched) + len(need_lookup) > \
                            MAX_THREADS_PER_DELTA:
                        overflow = True
                        break
                    last_processed = item.id
                page_token = page.next_page_token
                if page_token is None or overflow:
                    break
        except GmailTransportError as exc:
            if exc.status == 404:
                self._initial_sync(email, client)
                db.update_sync_point(
                    self.conn, email, fallback_end or start,
                    self._now_ms())
                return False
            raise
        lookup_failed = False
        for message_id in need_lookup:
            try:
                found = client.get_message(message_id,
                                           fmt="metadata")
            except GmailTransportError:
                lookup_failed = True
                continue
            if found.thread_id and \
                    found.thread_id not in touched_set:
                touched_set.add(found.thread_id)
                touched.append(found.thread_id)
        for message_id in deleted:
            cached = db.get_message(self.conn, email, message_id)
            if cached is None:
                continue
            db.delete_message(self.conn, email, message_id)
            if db.count_for_thread(self.conn, email,
                                   cached.thread_id) == 0:
                db.delete_threads_by_ids(self.conn, email,
                                         [cached.thread_id])
        failed = self._hydrate(email, client,
                               touched[:MAX_THREADS_PER_DELTA])
        if saw_any:
            checkpoint = last_processed
        else:
            checkpoint = fallback_end or start
        if lookup_failed or failed:
            overflow = True
            checkpoint = start
        db.update_sync_point(self.conn, email, checkpoint,
                             self._now_ms())
        return overflow

    @staticmethod
    def _collect(item, touched, touched_set, need_lookup,
                 lookup_set, deleted, deleted_set) -> None:
        for ref in list(item.messages) + list(item.messages_added):
            thread_id = getattr(ref, "thread_id", None)
            message_id = getattr(ref, "id", None)
            if thread_id:
                if thread_id not in touched_set:
                    touched_set.add(thread_id)
                    touched.append(thread_id)
            elif message_id and message_id not in lookup_set:
                lookup_set.add(message_id)
                need_lookup.append(message_id)
        raw_changes = list(item.labels_added) + \
            list(item.labels_removed)
        for _labels, raw in raw_changes:
            if not isinstance(raw, dict):
                continue
            if raw.get("threadId"):
                if raw["threadId"] not in touched_set:
                    touched_set.add(raw["threadId"])
                    touched.append(raw["threadId"])
            elif raw.get("id") and raw["id"] not in lookup_set:
                lookup_set.add(raw["id"])
                need_lookup.append(raw["id"])
        for ref in list(item.messages_deleted):
            message_id = getattr(ref, "id", None)
            if message_id and message_id not in deleted_set:
                deleted_set.add(message_id)
                deleted.append(message_id)

    def hydrate(self, email: str,
                thread_ids: list[str]) -> set[str]:
        """Rebuild aggregates plus message rows. Returns failed ids.

        Public wrapper the mailbox/search UI uses for server-driven
        fills (load-more pages, server search) on a worker thread.
        """
        return self._hydrate(email, self._client_for(email), thread_ids)

    def fetch_folder_page(self, email: str, folder_key: str,
                          label_ids: list[str] | None,
                          include_spam_trash: bool,
                          page_token: str | None,
                          ) -> tuple[list, str | None]:
        """List one server page, hydrate it, persist the new cursor.

        Returns (local thread rows in server order, next page token).
        Rows that failed to hydrate are skipped, never half-written.
        """
        client = self._client_for(email)
        page = client.list_threads(
            label_ids=label_ids, max_results=LIST_MAX_RESULTS,
            page_token=page_token, include_spam_trash=include_spam_trash)
        failed = self._hydrate(email, client,
                               [ref.id for ref in page.threads])
        db.upsert_folder_page(self.conn, db.FolderPage(
            account_email=email, folder_key=folder_key,
            next_page_token=page.next_page_token))
        rows = [db.get_thread(self.conn, email, ref.id)
                for ref in page.threads if ref.id not in failed]
        return ([row for row in rows if row is not None],
                page.next_page_token)

    def _hydrate(self, email: str, client: GmailClient,
                 thread_ids: list[str]) -> set[str]:
        """Rebuild aggregates plus message rows. Returns failed ids."""
        threads: list[db.Thread] = []
        messages: list[db.Message] = []
        failed: set[str] = set()
        for thread_id in thread_ids:
            try:
                full = client.get_thread(thread_id, fmt="metadata")
            except GmailTransportError as exc:
                if exc.status == 404:
                    db.delete_messages_for_thread(
                        self.conn, email, thread_id)
                    db.delete_threads_by_ids(
                        self.conn, email, [thread_id])
                    continue
                failed.add(thread_id)
                continue
            existing = db.get_thread(self.conn, email, thread_id)
            snoozed = existing.snoozed_until \
                if existing is not None else None
            aggregate = ThreadAggregation.build(email, full,
                                                snoozed_until=snoozed)
            threads.append(db.Thread(
                account_email=aggregate.account_email,
                id=aggregate.id, snippet=aggregate.snippet,
                subject=aggregate.subject,
                from_address=aggregate.from_address,
                from_name=aggregate.from_name, date=aggregate.date,
                message_count=aggregate.message_count,
                unread_count=aggregate.unread_count,
                has_attachments=aggregate.has_attachments,
                starred=aggregate.starred,
                in_inbox=aggregate.in_inbox,
                categories=aggregate.categories,
                labels_csv=aggregate.labels_csv,
                snoozed_until=aggregate.snoozed_until,
            ))
            for api_message in full.messages:
                row = ThreadAggregation.build_message(
                    email, api_message, full.id)
                messages.append(db.Message(
                    account_email=row.account_email, id=row.id,
                    thread_id=row.thread_id, subject=row.subject,
                    from_address=row.from_address,
                    to_csv=row.to_csv, cc_csv=row.cc_csv,
                    date=row.date, snippet=row.snippet,
                    read=row.read, starred=row.starred,
                    has_attachments=row.has_attachments,
                    labels_csv=row.labels_csv,
                    message_id_header=row.message_id_header,
                ))
        if threads:
            db.upsert_threads(self.conn, threads)
        if messages:
            db.upsert_message_metadata(self.conn, messages)
        return failed
