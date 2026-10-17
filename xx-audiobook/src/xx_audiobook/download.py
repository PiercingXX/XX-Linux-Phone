"""Downloads + offline: manifest, Range resume, sha256, NM gating (M4).

Mirrors download/BookDownloader.kt (Range against .part files, size +
sha256 verify keeping nothing unverified, detail + cover cached for
offline), download/BookFiles.kt (books/{safeId}/{idx:03d}-{safeFileId}
plus covers/{id}.jpg), data/repo/DownloadRepository.kt (manifest
bookkeeping, signed-out park, localFile/isComplete offline gate),
data/repo/NetworkMonitor.kt (Online/Metered/Offline classify, VPN not
metering by itself) and download/DownloadWorker.kt (8 attempts, unique
per-book work, signed-out parks at once).

Linux mapping (PORT_SPEC.md S2.10): WorkManager becomes systemd user
units (data/systemd/), NetworkManager is read over D-Bus, storage-low
refuses new downloads. Blocking calls run on worker threads, never the
GTK main loop; use submit() to post results with GLib.idle_add.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from xx_audiobook import paths as paths_module

PART = ".part"
BUFFER = 64 * 1024
SAVE_EVERY = 1 << 20
MAX_ATTEMPTS = 8
SIGNED_OUT = "Sign in to resume."
WAITING_FOR_WIFI = "Waiting for Wi-Fi."
OFFLINE_MESSAGE = "No network connection."
STORAGE_LOW_MESSAGE = "Storage is low. Free some space and retry."
STORAGE_MIN_FREE_BYTES = 200 * 1024 * 1024
FILE_TIMEOUT_S = 30.0

NM_BUS = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

NM_STATE_CONNECTED_SITE = 60
NM_STATE_CONNECTED_GLOBAL = 70
NM_METERED_NO = 2
NM_METERED_GUESS_NO = 4
NM_CONNECTIVITY_NONE = 1
NM_CONNECTIVITY_FULL = 4

HISTORY_SCHEMA = 1

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def safe_id(text: str) -> str:
    """A server id as one path segment: never empty, never '.', '..' or a
    hidden name, so it cannot point outside the books/covers folders."""
    safe = _SAFE_RE.sub("_", text or "")
    return "_" + safe if not safe or safe.startswith(".") else safe


class NetState(Enum):
    Online = "Online"
    Metered = "Metered"
    Offline = "Offline"


@dataclass
class Facts:
    internet: bool = False
    validated: bool = False
    not_metered: bool = False
    vpn: bool = False


def classify(facts: Facts) -> NetState:
    if not facts.internet:
        return NetState.Offline
    if not facts.validated and not facts.vpn:
        return NetState.Offline
    if facts.not_metered:
        return NetState.Online
    return NetState.Metered


def classify_nm(state: int, metered: int, connectivity: int,
                vpn: bool = False) -> NetState:
    internet = (state >= NM_STATE_CONNECTED_SITE
                and connectivity != NM_CONNECTIVITY_NONE)
    validated = connectivity == NM_CONNECTIVITY_FULL
    not_metered = metered in (NM_METERED_NO, NM_METERED_GUESS_NO)
    return classify(Facts(internet, validated, not_metered, vpn))


def _reply_type(signature: str):
    from gi.repository import GLib

    return GLib.VariantType.new(signature)


def fetch_nm_props(connection=None) -> dict | None:
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        return None
    try:
        conn = connection
        if conn is None:
            conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = conn.call_sync(
            NM_BUS, NM_PATH, PROPS_IFACE, "GetAll",
            GLib.Variant("(s)", (NM_IFACE,)),
            _reply_type("(a{sv})"),
            Gio.DBusCallFlags.NONE, 2000, None)
    except Exception:
        return None
    if reply is None:
        return None
    try:
        (props,) = reply.unpack()
    except Exception:
        return None
    return dict(props)


def _unbox(value):
    unpack = getattr(value, "unpack", None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value


def probe_nm(connection=None) -> NetState:
    props = fetch_nm_props(connection)
    if not props:
        return NetState.Offline
    try:
        state = int(_unbox(props.get("State", 0)))
        metered = int(_unbox(props.get("Metered", 0)))
        connectivity = int(_unbox(props.get("Connectivity", 0)))
    except (TypeError, ValueError):
        return NetState.Offline
    vpn = False
    try:
        active = props.get("ActiveConnections", None)
        paths = _unbox(active) if active is not None else []
        if paths:
            vpn = _any_vpn_active(connection, list(paths))
    except Exception:
        vpn = False
    return classify_nm(state, metered, connectivity, vpn)


def _any_vpn_active(connection, paths: list) -> bool:
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        return False
    if connection is None:
        return False
    for path in paths:
        try:
            reply = connection.call_sync(
                NM_BUS, str(path), PROPS_IFACE, "GetAll",
                GLib.Variant(
                    "(s)",
                    ("org.freedesktop.NetworkManager.Connection.Active",)),
                _reply_type("(a{sv})"),
                Gio.DBusCallFlags.NONE, 2000, None)
        except Exception:
            continue
        if reply is None:
            continue
        try:
            (props,) = reply.unpack()
        except Exception:
            continue
        try:
            if bool(_unbox(dict(props).get("Vpn", False))):
                return True
        except Exception:
            continue
    return False


class NetworkMonitor:
    def __init__(self, initial: NetState = NetState.Offline) -> None:
        self._lock = threading.Lock()
        self._state = initial

    @property
    def state(self) -> NetState:
        with self._lock:
            return self._state

    @property
    def is_online(self) -> bool:
        return self.state != NetState.Offline

    def on_facts(self, facts: Facts) -> NetState:
        with self._lock:
            self._state = classify(facts)
            return self._state

    def on_lost(self) -> NetState:
        with self._lock:
            self._state = NetState.Offline
            return self._state

    def probe(self, connection=None) -> NetState:
        state = probe_nm(connection)
        with self._lock:
            self._state = state
        return state

    def submit_probe(self, on_done=None):
        from xx_audiobook import api as api_module

        return api_module.submit(lambda: self.probe(), on_done)


class BookFiles:
    def __init__(self, files_dir: Path | None = None) -> None:
        base = Path(files_dir) if files_dir is not None else (
            paths_module.data_dir())
        self.files_dir = base
        self.root = base / "books"
        self.covers = base / "covers"

    def book_dir(self, item_id: str) -> Path:
        return self.root / safe_id(item_id)

    def track_file(self, item_id: str, index: int, file_id: str) -> Path:
        return self.book_dir(item_id) / f"{index:03d}-{safe_id(file_id)}"

    def cover_file(self, item_id: str) -> Path:
        return self.covers / (safe_id(item_id) + ".jpg")

    def ensure_dirs(self, item_id: str | None = None) -> None:
        targets = [self.root, self.covers]
        if item_id is not None:
            targets.append(self.book_dir(item_id))
        for target in targets:
            target.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target, 0o700)
            except OSError:
                pass

    def delete_book(self, item_id: str) -> None:
        try:
            cover = self.cover_file(item_id)
            if cover.is_file():
                cover.unlink()
        except OSError:
            pass
        directory = self.book_dir(item_id)
        try:
            if directory.is_dir():
                shutil.rmtree(directory, ignore_errors=True)
        except OSError:
            pass

    def bytes_used(self) -> int:
        total = 0
        for top in (self.root, self.covers):
            try:
                for path in top.rglob("*"):
                    try:
                        if path.is_file():
                            total += path.stat().st_size
                    except OSError:
                        continue
            except OSError:
                continue
        return total


def disk_free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None


def is_storage_low(path: Path, needed_bytes: int = 0,
                   free_bytes: int | None = None) -> bool:
    free = free_bytes if free_bytes is not None else disk_free_bytes(path)
    if free is None:
        return False
    return free - max(needed_bytes, 0) < STORAGE_MIN_FREE_BYTES


def check_storage_for(total_bytes: int, dest: Path,
                      free_bytes: int | None = None) -> str | None:
    if is_storage_low(dest, total_bytes, free_bytes):
        return STORAGE_LOW_MESSAGE
    return None


def storage_used(files: BookFiles | None = None) -> int:
    return (files or BookFiles()).bytes_used()


def can_download(network: NetState, wifi_only: bool) -> str | None:
    if network == NetState.Offline:
        return OFFLINE_MESSAGE
    if wifi_only and network != NetState.Online:
        return WAITING_FOR_WIFI
    return None


class Outcome:
    pass


@dataclass
class Done(Outcome):
    pass


@dataclass
class Retry(Outcome):
    message: str


@dataclass
class Failed(Outcome):
    message: str


@dataclass
class Cancelled(Outcome):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(BUFFER)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class BookDownloader:
    def __init__(self, api, db, files: BookFiles | None = None,
                 now_ms=None) -> None:
        self._api = api
        self._db = db
        self._files = files or BookFiles()
        self._now_ms = now_ms or _wall_ms

    def download(self, item_id: str, progress=None) -> Outcome:
        book = self._db.downloads().get(item_id)
        if book is None or book.state == "Cancelled":
            return Cancelled()
        rows = self._db.downloads().files(item_id)
        if not rows:
            return self._fail(item_id, "Nothing to download.")
        total = sum(r.size for r in rows)
        self._db.downloads().set_state(item_id, "Running", None,
                                       self._now_ms())
        done_before = sum(r.size for r in rows if r.verified)
        ordered = sorted(rows, key=lambda r: r.idx)
        for row in ordered:
            fresh = self._file_row(item_id, row.file_id)
            target_row = fresh if fresh is not None else row
            if self._already_done(target_row):
                done_before = self._refresh_verified_total(
                    item_id, total, done_before, target_row)
                continue
            outcome = self._fetch(target_row, lambda have: (
                progress(done_before + have, total)
                if progress is not None else None))
            if isinstance(outcome, Done):
                done_before += target_row.size
            elif isinstance(outcome, Retry):
                if not self._still_wanted(item_id):
                    return Cancelled()
                self._db.downloads().set_state(item_id, "Queued",
                                               outcome.message,
                                               self._now_ms())
                return outcome
            elif isinstance(outcome, Failed):
                return self._fail(item_id, outcome.message)
            else:
                return outcome
            if not self._still_wanted(item_id):
                return Cancelled()
        if not self._still_wanted(item_id):
            return Cancelled()
        self.cache_for_offline(item_id)
        current = self._db.downloads().get(item_id)
        if current is not None and current.state != "Cancelled":
            from xx_audiobook import store as store_module

            self._db.downloads().upsert(store_module.Download(
                item_id=item_id, title=current.title, state="Done",
                bytes_done=total, bytes_total=total, error=None,
                created_at_ms=current.created_at_ms,
                updated_at_ms=self._now_ms()))
        if progress is not None:
            progress(total, total)
        return Done()

    def cache_for_offline(self, item_id: str) -> None:
        try:
            from xx_audiobook import repos as repos_module

            library = repos_module.LibraryRepository(self._api, self._db)
            library.refresh_detail(item_id)
        except Exception:
            pass
        try:
            url = self._api.cover_url(item_id, "full")
        except Exception:
            return
        if not url:
            return
        try:
            body = self._fetch_bytes(url)
        except Exception:
            return
        if not body:
            return
        try:
            self._files.ensure_dirs(item_id)
            dest = self._files.cover_file(item_id)
            tmp = dest.with_name(dest.name + PART)
            tmp.write_bytes(body)
            if tmp.stat().st_size > 0:
                if dest.exists():
                    dest.unlink()
                tmp.rename(dest)
            else:
                tmp.unlink()
        except OSError:
            pass

    def _file_row(self, item_id: str, file_id: str):
        for row in self._db.downloads().files(item_id):
            if row.file_id == file_id:
                return row
        return None

    def _already_done(self, row) -> bool:
        if not row.verified:
            return False
        try:
            found = self._files.track_file(
                row.item_id, row.idx, row.file_id)
        except Exception:
            return False
        try:
            return found.is_file() and found.stat().st_size == row.size
        except OSError:
            return False

    def _refresh_verified_total(self, item_id: str, total: int,
                                done_before: int, row) -> int:
        rows = self._db.downloads().files(item_id)
        verified = sum(r.size for r in rows if r.verified)
        if verified > done_before:
            return verified
        return done_before + 0

    def _still_wanted(self, item_id: str) -> bool:
        row = self._db.downloads().get(item_id)
        return row is not None and row.state != "Cancelled"

    def _fail(self, item_id: str, message: str) -> Failed:
        if not self._still_wanted(item_id):
            return Failed(message)
        self._db.downloads().set_state(item_id, "Failed", message,
                                       self._now_ms())
        return Failed(message)

    def _auth_headers(self, url: str) -> dict:
        try:
            bearer = self._api._bearer_for(url)
        except Exception:
            bearer = None
        if bearer:
            return {"Authorization": "Bearer " + bearer}
        return {}

    def _fetch_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(
            url, headers={"Accept": "*/*", **self._auth_headers(url)},
            method="GET")
        with urllib.request.urlopen(request,
                                    timeout=FILE_TIMEOUT_S) as response:
            return response.read()

    def _fetch(self, row, on_bytes) -> Outcome:
        from xx_audiobook import store as store_module

        target = self._files.track_file(row.item_id, row.idx, row.file_id)
        part = target.with_name(target.name + PART)
        try:
            self._files.ensure_dirs(row.item_id)
        except OSError:
            return Retry("Could not move the file into place.")
        have = 0
        try:
            if part.is_file():
                have = part.stat().st_size
        except OSError:
            have = 0
        if have > row.size:
            try:
                part.unlink()
            except OSError:
                pass
            have = 0
        if have < row.size:
            try:
                url = self._api.file_url(row.item_id, row.file_id)
            except Exception:
                url = None
            if not url:
                return Retry("Not signed in.")
            headers = dict(self._auth_headers(url))
            if have > 0:
                headers["Range"] = f"bytes={have}-"
            try:
                code, body = self._range_get(url, headers)
            except _NetworkError as exc:
                return Retry(str(exc) or "Network error")
            except _HttpStatus as exc:
                if exc.code in (401, 403):
                    return Retry(f"Not authorised ({exc.code}).")
                if exc.code == 404:
                    return Failed("The server no longer has this file.")
                if exc.code == 416:
                    pass
                else:
                    return Retry(f"The server answered {exc.code}.")
            else:
                if code == 206:
                    append = True
                elif code == 200:
                    append = False
                    have = 0
                else:
                    return Retry(f"The server answered {code}.")
                try:
                    have = self._write_body(part, body, append, have,
                                            row, on_bytes)
                except _Cancelled:
                    return Cancelled()
                except _NetworkError as exc:
                    return Retry(str(exc) or "Network error")
                except OSError:
                    return Retry("Could not move the file into place.")
                if not self._still_wanted(row.item_id):
                    return Cancelled()
        try:
            final_size = part.stat().st_size if part.is_file() else 0
        except OSError:
            final_size = 0
        if not part.is_file() or final_size != row.size:
            if final_size > row.size:
                try:
                    part.unlink()
                except OSError:
                    pass
            self._save_progress(row, min(final_size, row.size))
            return Retry(f"Incomplete file ({final_size} of "
                         f"{row.size} bytes).")
        if row.sha256.strip() and (
                sha256_file(part).lower() != row.sha256.strip().lower()):
            try:
                part.unlink()
            except OSError:
                pass
            if self._still_wanted(row.item_id):
                self._db.downloads().upsert_files(
                    [store_module.DownloadFile(
                        item_id=row.item_id, file_id=row.file_id,
                        idx=row.idx, size=row.size, sha256=row.sha256,
                        mime=row.mime, local_path=None, bytes_done=0,
                        verified=False)])
            return Failed(f"Checksum mismatch on file {row.idx + 1}.")
        if not self._still_wanted(row.item_id):
            try:
                part.unlink()
            except OSError:
                pass
            return Cancelled()
        try:
            if target.exists():
                target.unlink()
            part.rename(target)
        except OSError:
            return Retry("Could not move the file into place.")
        self._db.downloads().upsert_files(
            [store_module.DownloadFile(
                item_id=row.item_id, file_id=row.file_id, idx=row.idx,
                size=row.size, sha256=row.sha256, mime=row.mime,
                local_path=str(target), bytes_done=row.size,
                verified=True)])
        self._refresh_totals(row.item_id)
        try:
            on_bytes(row.size)
        except Exception:
            pass
        return Done()

    def _save_progress(self, row, have: int) -> None:
        from xx_audiobook import store as store_module

        try:
            if not self._still_wanted(row.item_id):
                return
            self._db.downloads().upsert_files(
                [store_module.DownloadFile(
                    item_id=row.item_id, file_id=row.file_id, idx=row.idx,
                    size=row.size, sha256=row.sha256, mime=row.mime,
                    local_path=None, bytes_done=have, verified=False)])
            self._refresh_totals(row.item_id)
        except Exception:
            pass

    def _refresh_totals(self, item_id: str) -> None:
        try:
            done = sum(r.bytes_done for r in
                       self._db.downloads().files(item_id))
            current = self._db.downloads().get(item_id)
            if current is not None:
                from xx_audiobook import store as store_module

                self._db.downloads().upsert(store_module.Download(
                    item_id=current.item_id, title=current.title,
                    state=current.state, bytes_done=done,
                    bytes_total=current.bytes_total, error=current.error,
                    created_at_ms=current.created_at_ms,
                    updated_at_ms=self._now_ms()))
        except Exception:
            pass

    def _range_get(self, url: str, headers: dict) -> tuple[int, object]:
        request = urllib.request.Request(url, headers=headers,
                                         method="GET")
        try:
            response = urllib.request.urlopen(request,
                                              timeout=FILE_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            raise _HttpStatus(exc.code)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise _NetworkError("Network error") from exc
        code = getattr(response, "status", 200)
        return (code, response)

    def _write_body(self, part: Path, body, append: bool, have: int,
                    row, on_bytes) -> int:
        mode = "ab" if append else "wb"
        if not append:
            have = 0
        since_save = 0
        try:
            with open(part, mode) as out:
                while True:
                    chunk = body.read(BUFFER)
                    if not chunk:
                        break
                    out.write(chunk)
                    have += len(chunk)
                    since_save += len(chunk)
                    if since_save >= SAVE_EVERY:
                        since_save = 0
                        if not self._still_wanted(row.item_id):
                            raise _Cancelled()
                        self._save_progress(row, have)
                        try:
                            on_bytes(have)
                        except Exception:
                            pass
        except _Cancelled:
            raise
        except (OSError, ValueError) as exc:
            raise _NetworkError("Network error") from exc
        finally:
            try:
                body.close()
            except Exception:
                pass
        return have


class _HttpStatus(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


class _NetworkError(Exception):
    pass


class _Cancelled(Exception):
    pass


def _wall_ms() -> int:
    import time

    return int(time.time() * 1000)


class RecordingScheduler:
    def __init__(self) -> None:
        self.scheduled: list = []
        self.cancelled: list = []

    def schedule(self, item_id: str, wifi_only: bool = True) -> None:
        self.scheduled.append((item_id, wifi_only))

    def cancel(self, item_id: str) -> None:
        self.cancelled.append(item_id)


def unit_instance(item_id: str) -> str:
    return safe_id(item_id) or "book"


class SystemdScheduler:
    def __init__(self, runner=None) -> None:
        self._runner = runner

    def schedule(self, item_id: str, wifi_only: bool = True) -> None:
        from xx_audiobook import api as api_module

        unit = f"xx-audiobook-download@{unit_instance(item_id)}.service"

        def _start() -> None:
            import subprocess

            subprocess.run(
                ["systemctl", "--user", "start", unit],
                check=False, capture_output=True, timeout=10)

        if self._runner is not None:
            self._runner(_start)
        else:
            api_module.submit(_start)

    def cancel(self, item_id: str) -> None:
        from xx_audiobook import api as api_module

        unit = f"xx-audiobook-download@{unit_instance(item_id)}.service"

        def _stop() -> None:
            import subprocess

            subprocess.run(
                ["systemctl", "--user", "stop", unit],
                check=False, capture_output=True, timeout=10)

        if self._runner is not None:
            self._runner(_stop)
        else:
            api_module.submit(_stop)


class DownloadRepository:
    def __init__(self, api, db, files: BookFiles | None = None,
                 scheduler=None, now_ms=None,
                 free_bytes=None) -> None:
        self._api = api
        self._db = db
        self._files = files or BookFiles()
        self._scheduler = scheduler or RecordingScheduler()
        self._now_ms = now_ms or _wall_ms
        self._free_bytes = free_bytes

    def list_all(self) -> list:
        return self._db.downloads().all()

    def get(self, item_id: str):
        return self._db.downloads().get(item_id)

    def files_for(self, item_id: str) -> list:
        return self._db.downloads().files(item_id)

    def enqueue(self, item_id: str, title: str,
                wifi_only: bool = True) -> tuple:
        from xx_audiobook import api as api_module
        from xx_audiobook import store as store_module

        try:
            manifest = self._api.manifest(item_id)
        except api_module.NotSignedIn as exc:
            return (False, str(exc) or "Not signed in.")
        except api_module.ApiError as exc:
            return (False, str(exc) or "Download failed.")
        total = sum(f.size for f in manifest.files)
        blocked = check_storage_for(total, self._files.root,
                                    self._free_bytes)
        if blocked is not None:
            now = self._now_ms()
            existing = self._db.downloads().get(item_id)
            self._db.downloads().upsert(store_module.Download(
                item_id=item_id, title=title, state="Failed",
                error=blocked, bytes_done=0, bytes_total=total,
                created_at_ms=(existing.created_at_ms
                               if existing else now),
                updated_at_ms=now))
            return (False, blocked)
        now = self._now_ms()
        existing = self._db.downloads().get(item_id)
        self._db.downloads().upsert(store_module.Download(
            item_id=item_id, title=title, state="Queued",
            bytes_done=0, bytes_total=total, error=None,
            created_at_ms=(existing.created_at_ms if existing else now),
            updated_at_ms=now))
        known = {f.file_id: f for f in
                 self._db.downloads().files(item_id)}
        rows = []
        for item in sorted(manifest.files, key=lambda f: f.index):
            prior = known.get(item.file_id)
            keep = (prior is not None and prior.verified
                    and prior.sha256 == item.sha256
                    and prior.size == item.size
                    and prior.local_path is not None
                    and Path(prior.local_path).is_file()
                    and Path(prior.local_path).stat().st_size
                    == item.size)
            rows.append(store_module.DownloadFile(
                item_id=item_id, file_id=item.file_id, idx=item.index,
                size=item.size, sha256=item.sha256, mime=item.mime,
                local_path=(prior.local_path if keep else None),
                bytes_done=(prior.bytes_done if keep else 0),
                verified=keep))
        self._db.downloads().upsert_files(rows)
        try:
            self._scheduler.schedule(item_id, wifi_only)
        except Exception:
            pass
        return (True, None)

    def cancel(self, item_id: str) -> None:
        try:
            self._scheduler.cancel(item_id)
        except Exception:
            pass
        self._db.downloads().set_state(item_id, "Cancelled", None,
                                       self._now_ms())

    def retry(self, item_id: str, wifi_only: bool = True) -> None:
        self._db.downloads().set_state(item_id, "Queued", None,
                                       self._now_ms())
        try:
            self._scheduler.schedule(item_id, wifi_only)
        except Exception:
            pass

    def park_signed_out(self, item_id: str) -> None:
        row = self._db.downloads().get(item_id)
        if row is None or row.state in ("Cancelled", "Done"):
            return
        self._db.downloads().set_state(item_id, "Failed", SIGNED_OUT,
                                       self._now_ms())

    def resume_signed_out(self, wifi_only: bool = True) -> int:
        count = 0
        for row in self._db.downloads().all():
            if row.state == "Failed" and row.error == SIGNED_OUT:
                self.retry(row.item_id, wifi_only)
                count += 1
        return count

    def delete(self, item_id: str) -> None:
        try:
            self._scheduler.cancel(item_id)
        except Exception:
            pass
        self._db.downloads().delete(item_id)
        self._files.delete_book(item_id)

    def local_file(self, item_id: str, file_id: str) -> str | None:
        for row in self._db.downloads().files(item_id):
            if row.file_id != file_id or not row.verified:
                continue
            if not row.local_path:
                return None
            try:
                path = Path(row.local_path)
            except (TypeError, ValueError):
                return None
            try:
                if path.is_file() and path.stat().st_size == row.size:
                    return str(path)
            except OSError:
                return None
            return None
        return None

    def is_complete(self, item_id: str) -> bool:
        rows = self._db.downloads().files(item_id)
        return bool(rows) and all(r.verified for r in rows)


def run_one(item_id: str, api, db, files: BookFiles | None = None,
            is_signed_in=None, network: NetState = NetState.Online,
            wifi_only: bool = True, progress=None,
            scheduler=None, now_ms=None) -> Outcome:
    repo_files = files or BookFiles()
    signed_in = True if is_signed_in is None else bool(is_signed_in())
    if not signed_in:
        DownloadRepository(api, db, repo_files,
                           scheduler or RecordingScheduler(),
                           now_ms=now_ms).park_signed_out(item_id)
        return Failed(SIGNED_OUT)
    blocked = can_download(network, wifi_only)
    if blocked is not None:
        db.downloads().set_state(item_id, "Queued", blocked,
                                 (now_ms or _wall_ms)())
        return Retry(blocked)
    downloader = BookDownloader(api, db, repo_files, now_ms=now_ms)
    return downloader.download(item_id, progress)


class DownloadsState:
    """Small state API for the Downloads and Setup screens.

    Screens call the sync getters on a worker thread and the submit_*
    variants from the GTK thread (results arrive via GLib.idle_add).
    """

    def __init__(self, api, db, prefs_store=None,
                 files: BookFiles | None = None, scheduler=None,
                 network_monitor: NetworkMonitor | None = None,
                 now_ms=None) -> None:
        from xx_audiobook import prefs as prefs_module

        self._api = api
        self._db = db
        self._prefs_store = prefs_store
        self._prefs_module = prefs_module
        self._files = files or BookFiles()
        self._scheduler = scheduler or SystemdScheduler()
        self._network = network_monitor or NetworkMonitor(NetState.Online)
        self._now_ms = now_ms or _wall_ms
        self._repo = DownloadRepository(api, db, self._files,
                                        self._scheduler,
                                        now_ms=self._now_ms)

    @property
    def repo(self) -> DownloadRepository:
        return self._repo

    @property
    def network(self) -> NetworkMonitor:
        return self._network

    def list(self) -> list:
        return self._repo.list_all()

    def status(self, item_id: str) -> dict:
        row = self._repo.get(item_id)
        if row is None:
            return {"item_id": item_id, "state": "Missing",
                    "bytes_done": 0, "bytes_total": 0, "error": None,
                    "is_complete": False}
        return {"item_id": row.item_id, "title": row.title,
                "state": row.state, "bytes_done": row.bytes_done,
                "bytes_total": row.bytes_total, "error": row.error,
                "is_complete": self._repo.is_complete(item_id)}

    def wifi_only(self) -> bool:
        try:
            if self._prefs_store is not None:
                return bool(self._prefs_store.current()
                            .download_wifi_only)
            return bool(self._prefs_module.load().download_wifi_only)
        except Exception:
            return True

    def set_wifi_only(self, value: bool) -> None:
        try:
            prefs = self._prefs_module.load()
            prefs.download_wifi_only = bool(value)
            self._prefs_module.save(prefs)
        except Exception:
            pass

    def storage_used_bytes(self) -> int:
        return self._files.bytes_used()

    def storage_state(self) -> dict:
        used = self._files.bytes_used()
        free = disk_free_bytes(self._files.root)
        return {"used_bytes": used, "free_bytes": free,
                "low": (is_storage_low(self._files.root, 0, free)
                        if free is not None else False)}

    def block_reason(self) -> str | None:
        return can_download(self._network.state, self.wifi_only())

    def enqueue(self, item_id: str, title: str) -> tuple:
        return self._repo.enqueue(item_id, title, self.wifi_only())

    def cancel(self, item_id: str) -> None:
        self._repo.cancel(item_id)

    def retry(self, item_id: str) -> None:
        self._repo.retry(item_id, self.wifi_only())

    def delete(self, item_id: str) -> None:
        self._repo.delete(item_id)

    def submit_enqueue(self, item_id: str, title: str, on_done=None):
        from xx_audiobook import api as api_module

        return api_module.submit(
            lambda: self.enqueue(item_id, title), on_done)

    def submit_probe_network(self, on_done=None):
        return self._network.submit_probe(on_done)


def export_history(db) -> str:
    entries = []
    try:
        last_played: dict[str, int] = {}
        for event in db.history().recent(500):
            if event.item_id not in last_played:
                last_played[event.item_id] = event.at_ms
    except Exception:
        last_played = {}
    try:
        rows = db.progress().all()
    except Exception:
        rows = []
    for row in rows:
        entries.append({
            "item_id": row.item_id, "position_s": row.position_s,
            "duration_s": row.duration_s,
            "finished": bool(row.is_finished),
            "last_played_at_ms": max(
                last_played.get(row.item_id, 0), row.updated_at_ms),
            "updated_at_ms": row.updated_at_ms, "speed": row.speed,
        })
    entries.sort(key=lambda e: e["item_id"])
    return json.dumps({"schema": HISTORY_SCHEMA, "entries": entries},
                      indent=2)


def import_history(db, text: str) -> int:
    try:
        doc = json.loads(text)
    except ValueError:
        return 0
    if not isinstance(doc, dict):
        return 0
    if int(doc.get("schema", HISTORY_SCHEMA)) > HISTORY_SCHEMA:
        return 0
    entries = doc.get("entries", [])
    if not isinstance(entries, list):
        return 0
    written = 0
    try:
        from xx_audiobook import store as store_module

        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("item_id"):
                continue
            try:
                updated = int(entry.get("updated_at_ms", 0) or 0)
            except (TypeError, ValueError):
                continue
            local = db.progress().get(str(entry["item_id"]))
            if local is not None and local.updated_at_ms > updated:
                continue
            try:
                position = max(float(entry.get("position_s", 0.0)), 0.0)
            except (TypeError, ValueError):
                continue
            duration = entry.get("duration_s")
            try:
                duration = (None if duration is None
                            else float(duration))
            except (TypeError, ValueError):
                duration = None
            speed = entry.get("speed")
            try:
                speed = None if speed is None else float(speed)
            except (TypeError, ValueError):
                speed = None
            db.progress().upsert(store_module.Progress(
                item_id=str(entry["item_id"]), position_s=position,
                duration_s=duration,
                is_finished=bool(entry.get("finished", False)),
                updated_at_ms=updated, speed=speed))
            written += 1
    except Exception:
        return written
    return written


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="xx-audiobook-download")
    parser.add_argument("item_id", nargs="?")
    parser.add_argument("--retry-queued", action="store_true")
    args = parser.parse_args(argv if argv is not None else [])
    paths_module.ensure_app_dirs()
    from xx_audiobook import store as store_module

    db = store_module.Database()
    try:
        if args.retry_queued:
            count = 0
            for row in db.downloads().all():
                if row.state in ("Queued", "Failed") and (
                        row.error != SIGNED_OUT):
                    count += 1
            print(f"queued: {count}")
            return 0
        if not args.item_id:
            parser.print_usage()
            return 2
        print(f"queued: {args.item_id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
