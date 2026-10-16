"""First-run setup orchestration for XX Email (stdlib only, no GTK).

Ports the SetupScreen/AuthRepository completion side
(ref/xx-email ui/setup + data/auth/): with the loopback flow from
xx_email.auth, this turns an authorized code into a stored grant, a
registered account row, and persisted settings — then the caller runs
its first sync. Sign-out mirrors AccountRepository.remove: best-effort
server revoke while the grant still exists, then token, payload files
and every local row for the account go; server mail is untouched.

Threading: every function here may touch the network, the Secret
Service backend, or sqlite. Call it on a worker thread, never on the
GTK main loop. First-run directories are created mode 0700 before
anything is opened in them (a clean device has none of them).
"""

from __future__ import annotations

import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from xx_email import auth as auth_mod
from xx_email import config as config_mod
from xx_email import db as db_mod
from xx_email.domain import OutboxFiles

SETUP_GUIDE_DOC = "docs/oauth-setup.md"
KEYRING_LOCKED_MESSAGE = "Unlock your keyring to continue"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/permissions"
SIGN_IN_TIMEOUT = 300.0
REVOKE_TIMEOUT = 20


class KeyringLocked(Exception):
    """The credential store is locked: unlock it and retry the step."""


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def ensure_app_dirs() -> dict[str, Path]:
    """Create every XDG directory the app uses (mode 0700). Idempotent."""
    config_dir = _private_dir(config_mod.config_path().parent)
    data_dir = _private_dir(config_mod.data_dir())
    cache_dir = _private_dir(config_mod.cache_dir())
    state_dir = _private_dir(config_mod.state_dir())
    _private_dir(data_dir / OutboxFiles.DIR_NAME)
    _private_dir(cache_dir / "attachments")
    _private_dir(cache_dir / "images")
    return {"config": config_dir, "data": data_dir,
            "cache": cache_dir, "state": state_dir}


def describe_setup_state(accounts: list[str],
                         keyring_unreadable: bool) -> str:
    """Route helper: "locked", "setup", or "ready".

    A locked store is reported on its own — it must never read as
    "no accounts".
    """
    if keyring_unreadable:
        return "locked"
    if accounts:
        return "ready"
    return "setup"


def finish_sign_in(client_id: str, code: str, redirect_uri: str,
                   verifier: str, *, token_transport, store, conn,
                   config_path: Path | None = None,
                   now_ms=None) -> str:
    """Complete a browser sign-in: exchange, store, register, configure.

    Returns the signed-in account email. Raises AuthFlowError for a
    bad client ID, a missing email claim, or a grant without a refresh
    token (the stored record must stay refreshable); KeyringLocked
    when the credential store cannot be written while locked.
    Existing accounts keep their sync checkpoint — only the grant is
    replaced.
    """
    candidate = (client_id or "").strip()
    if not auth_mod.is_valid_client_id(candidate):
        raise auth_mod.AuthFlowError(
            "That client ID does not look like a Google client ID — "
            "it should end in apps.googleusercontent.com.")
    if store.unreadable:
        raise KeyringLocked(KEYRING_LOCKED_MESSAGE)
    response = auth_mod.exchange_code(
        candidate, redirect_uri, code, verifier,
        transport=token_transport)
    if not response.refresh_token:
        raise auth_mod.AuthFlowError(
            "Google did not return a refresh token — run the consent "
            "step again so the app can stay signed in.")
    email = auth_mod.email_from_id_token(response.id_token)
    if not email:
        raise auth_mod.AuthFlowError(
            "Google did not return an email address — sign in again.")
    clock = now_ms or (lambda: int(time.time() * 1000))
    from xx_email.token_store import TokenRecord
    store.save(email, TokenRecord(
        refresh_token=response.refresh_token, client_id=candidate,
        updated_at=clock()))
    existing = db_mod.get_account(conn, email)
    if existing is None:
        db_mod.upsert_account(conn, db_mod.Account(
            email=email, display_name=email))
    elif not existing.display_name:
        db_mod.upsert_account(conn, db_mod.Account(
            email=email, display_name=email,
            history_id=existing.history_id,
            last_sync_at=existing.last_sync_at))
    current = config_mod.load(config_path)
    current.client_id = candidate
    current.last_account = email
    config_mod.save(current, config_path)
    return email


def revoke_grant(token: str, opener=None) -> bool:
    """Best-effort server revoke of one refresh token. Never raises."""
    if not token:
        return False
    body = urllib.parse.urlencode({"token": token}).encode("ascii")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        if opener is not None:
            status = int(opener(REVOKE_ENDPOINT, body, headers))
        else:
            request = urllib.request.Request(
                REVOKE_ENDPOINT, data=body, headers=headers,
                method="POST")
            with urllib.request.urlopen(
                    request, timeout=REVOKE_TIMEOUT) as reply:
                status = int(reply.status)
    except Exception:
        return False
    return 200 <= status < 300


def sign_out(conn, store, account: str, *, data_dir=None,
             revoke=None, restore=None) -> dict:
    """Remove one account from this device; server mail untouched.

    revoke(client_id, refresh_token), when given, runs first while the
    grant still exists; its outcome is best-effort and reported in the
    returned dict alongside any warnings. Still-snoozed threads are
    restored to the inbox server-side before the grant drops:
    restore(account, thread_id), when given, runs per snoozed thread
    while the local wake rows still exist; without it the wakes are
    named in the warnings so stranded mail can be found in All Mail.
    Payload files are deleted before their rows, then every local row
    for the account goes.
    """
    from xx_email import snooze as snooze_mod

    warnings: list[str] = []
    revoked = False
    try:
        pending = list(db_mod.snooze_wakes_for_account(conn, account))
    except Exception:
        pending = []
    if pending and restore is not None:
        for wake in pending:
            try:
                restore(wake.account_email, wake.thread_id)
            except Exception:
                warnings.append(
                    f"Could not unsnooze {wake.thread_id} on the "
                    "server — find it in All Mail.")
    if pending:
        warnings.append(snooze_mod.UNINSTALL_WARNING)
    try:
        record = store.load(account)
    except Exception:
        record = None
    if revoke is not None and record is not None \
            and record.refresh_token:
        try:
            revoked = bool(revoke(record.client_id,
                                  record.refresh_token))
            if not revoked:
                warnings.append(
                    "Google did not confirm revocation — the grant "
                    "may still work until it expires.")
        except Exception:
            warnings.append(
                "Could not reach Google to revoke access — the local "
                "grant is removed, revoke it there if needed.")
    try:
        store.remove(account)
    except Exception:
        warnings.append("Could not clear the stored grant.")
    files_dir = Path(data_dir) if data_dir is not None \
        else config_mod.data_dir()
    try:
        rows = db_mod.outbox_for_account(conn, account)
    except Exception:
        rows = []
    for row in rows:
        try:
            OutboxFiles.delete_payload_file(
                files_dir, row.path, row.id)
        except Exception:
            warnings.append("Could not delete a queued-send file.")
            break
    db_mod.delete_account(conn, account)
    return {"revoked": revoked, "warnings": warnings}
