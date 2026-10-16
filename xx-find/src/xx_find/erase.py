"""Lane 5 erase: double-gated user-data wipe with dry-run planning.

The agent already double-gates erase (`Agent._erase_confirmed`: the
`erase_enabled` setting AND a typed confirmation repeating the device id
verbatim). This module answers "erase WHAT": the explicit, owner-reviewable
wipe list. Only user data is ever listed: XX-suite app dirs, browser
profiles when present, login keyring items owned by the user, and queued
xx-find credentials. The OS, bootloader, modem firmware, other users, and
removable media are never listed and are refused by the guards even if a
caller adds them.

Keyring choice: per-item Secret Service `Delete` on the default
collection's items (plus the xx-find accounts through the `keystore`
seam). Deleting `~/.local/share/keyrings` files out from under the
daemon races its open handles, bypasses its lock, and removes unrelated
collections; per-item `Delete` goes through the daemon, so locking stays
valid, each removal is scoped and auditable, and a locked or missing
daemon fails closed instead of corrupting files.

Only dry-run planning is exercised by tests and docs: `plan_erase` lists
what WOULD be deleted without deleting anything. Real deletion needs an
explicit `dry_run=False` call, which nothing in this repo makes.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

LOG = logging.getLogger("xx-find.erase")

XX_NAMES = ("xx-wm", "xx-find")
BROWSER_REL_DIRS = (".mozilla/firefox", ".config/chromium",
                    ".config/google-chrome", ".config/epiphany")
KEYRINGS_REL_DIR = ".local/share/keyrings"

TXXT_REL_DIRS = (".config/txxt", ".local/share/txxt", ".cache/txxt")
XX_EMAIL_REL_DIRS = (".config/xx-email", ".local/share/xx-email",
                     ".local/state/xx-email", ".cache/xx-email")
XX_VITALS_REL_DIRS = (".config/xx-vitals", ".local/share/xx-vitals",
                      ".local/state/xx-vitals", ".cache/xx-vitals")
UNVERIFIED_SUITE_APPS = ("xx-contacts", "xx-dialer", "xx-calendar",
                         "xx-share", "xx-captions")
UNVERIFIED_XDG_ROOTS = (".config", ".local/share", ".local/state", ".cache")

EMAIL_KEYRING_SCHEMA = {"application": "io.piercingxx.XXEmail",
                        "account": "<email>"}
VITALS_KEYRING_SCHEMA = {"application": "io.piercingxx.XXVitals",
                         "host": "<nas-host>"}
EMAIL_KEYRING_LABEL = ("xx-email OAuth tokens "
                       "(application=io.piercingxx.XXEmail, "
                       "account=<email>)")
VITALS_KEYRING_LABEL = ("xx-vitals NAS token "
                        "(application=io.piercingxx.XXVitals, "
                        "host=<nas-host>)")

FORBIDDEN_PREFIXES = ("/", "/usr", "/etc", "/boot", "/opt", "/media",
                      "/mnt", "/run/media", "/run/mount", "/dev",
                      "/proc", "/sys")


def validate_erase_confirmation(device_id: object,
                                typed: object) -> bool:
    """True only when the typed text repeats the device id verbatim."""
    try:
        wanted = str(device_id)
        given = str(typed)
    except Exception:
        return False
    return bool(wanted) and given == wanted


def home_path(home: Path | str | None = None) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def xx_suite_dirs(home: Path | str | None = None) -> list[Path]:
    """XX-suite user data dirs (config/share/state/cache). Missing XDG
    overrides fall back to the home-relative defaults."""
    base = home_path(home)
    config_base = Path(os.environ.get("XDG_CONFIG_HOME") or
                       (base / ".config")) \
        if home is None else base / ".config"
    share_base = Path(os.environ.get("XDG_DATA_HOME") or
                      (base / ".local" / "share")) \
        if home is None else base / ".local" / "share"
    state_base = Path(os.environ.get("XDG_STATE_HOME") or
                      (base / ".local" / "state")) \
        if home is None else base / ".local" / "state"
    cache_base = Path(os.environ.get("XDG_CACHE_HOME") or
                      (base / ".cache")) \
        if home is None else base / ".cache"
    return [config_base / "xx-wm", config_base / "xx-find",
            share_base / "xx-wm", share_base / "xx-find",
            state_base / "xx-find", cache_base / "xx-wm",
            cache_base / "xx-find"]


def _home_rel_dirs(home: Path | str | None,
                    rels: tuple[str, ...]) -> list[Path]:
    base = home_path(home)
    return [base / rel for rel in rels]


def suite_app_dirs(home: Path | str | None = None) -> list[Path]:
    """Every suite app's user-data dirs: txxt messages + MMS store,
    xx-email DB + drafts + outbox, xx-vitals metrics, plus the
    conventional XDG leaves for the suite apps with no source in this
    copy (xx-contacts, xx-dialer, xx-calendar, xx-share, xx-captions):
    those leaves are flagged owner-reviewable in the dry-run plan."""
    base = home_path(home)
    rels: list[str] = [*TXXT_REL_DIRS, *XX_EMAIL_REL_DIRS,
                       *XX_VITALS_REL_DIRS]
    for app in UNVERIFIED_SUITE_APPS:
        rels.extend("%s/%s" % (root, app) for root in UNVERIFIED_XDG_ROOTS)
    return _home_rel_dirs(base, tuple(rels))


def suite_app_detail(path: str) -> str:
    """Owner-facing note for one suite-app dir: what data it holds."""
    text = str(path)
    if "/txxt" in text:
        return "txxt messages DB + MMS store"
    if "/xx-email" in text:
        return "xx-email mail DB + drafts + outbox"
    if "/xx-vitals" in text:
        return "xx-vitals metrics DB + logs"
    if "/xx-contacts" in text:
        return "xx-contacts DB + photos"
    if "/xx-dialer" in text:
        return "xx-dialer call history + settings"
    if "/xx-calendar" in text:
        return "xx-calendar DB + settings"
    if "/xx-share" in text:
        return "xx-share history + temp files"
    if "/xx-captions" in text:
        return "xx-captions transcripts"
    return "suite app user data"


def suite_review_notes() -> list[dict]:
    """Owner-reviewable lines for the suite entries no source pins down.

    The five apps without a reference tree in this copy use conventional
    XDG leaves; their exact dirs and any Secret Service schemas still
    need owner confirmation against the app sources. Rendered as TODO
    lines by `format_plan`, never as code comments."""
    notes = []
    for app in UNVERIFIED_SUITE_APPS:
        notes.append({
            "kind": "todo",
            "detail": "TODO owner-review: confirm %s XDG leaves "
                      "and Secret Service schemas against the %s "
                      "source" % (app, app),
        })
    return notes


def browser_profile_dirs(home: Path | str | None = None) -> list[Path]:
    base = home_path(home)
    return [base / rel for rel in BROWSER_REL_DIRS]


def keyrings_dir(home: Path | str | None = None) -> Path:
    return home_path(home) / KEYRINGS_REL_DIR


def _resolved(path: Path) -> Path | None:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def is_safe_target(path: Path | str,
                   home: Path | str | None = None) -> bool:
    """True only for a deletable user-data path: inside $HOME, not $HOME
    itself, and never an OS or removable-media root. Symlinks that
    resolve outside $HOME are refused."""
    base = _resolved(home_path(home))
    target = _resolved(Path(path))
    if base is None or target is None:
        return False
    if target == base:
        return False
    try:
        target.relative_to(base)
    except ValueError:
        return False
    text = str(target)
    for prefix in FORBIDDEN_PREFIXES[1:]:
        if text == prefix or text.startswith(prefix + "/"):
            return False
    try:
        if Path(path).expanduser().is_symlink():
            return False
    except OSError:
        return False
    return True


def plan_erase(*, home: Path | str | None = None,
               keyring_accounts: list[str] | None = None) -> list[dict]:
    """Dry-run plan: what WOULD be deleted. Never deletes anything.

    Returns one dict per target: `kind` (`dir`, `keystore-account`,
    `keyring-item`, or `todo`), `path`/`account` display text, `exists`,
    and a `detail` note. The keyrings directory itself is listed once
    with `action: keep` to document that files there are purged per-item
    through the daemon, never removed as files. `todo` entries are
    owner-reviewable confirmations, not deletions.
    """
    base = home_path(home)
    targets: list[dict] = []
    suite = set(map(str, xx_suite_dirs(base)))
    app_dirs = {str(p): suite_app_detail(str(p))
                for p in suite_app_dirs(base)}
    for candidate in (*xx_suite_dirs(base), *suite_app_dirs(base),
                      *browser_profile_dirs(base)):
        if not is_safe_target(candidate, base):
            continue
        try:
            exists = Path(candidate).exists() or Path(candidate).is_symlink()
        except OSError:
            exists = False
        if str(candidate) in suite:
            detail = "XX-suite user data"
        elif str(candidate) in app_dirs:
            detail = app_dirs[str(candidate)]
        else:
            detail = "browser profile if present"
        targets.append({"kind": "dir", "path": str(candidate),
                        "exists": bool(exists),
                        "detail": detail})
    for account in (keyring_accounts or
                    ["xx-find/device-token",
                     "xx-find/owner-verify-key"]):
        targets.append({"kind": "keystore-account", "account": account,
                        "exists": None,
                        "detail": "Secret Service item, per-item Delete"})
    targets.append({"kind": "keyring-item",
                    "account": EMAIL_KEYRING_LABEL,
                    "exists": None,
                    "detail": "Secret Service per-account Delete, "
                              "attributes %s" % EMAIL_KEYRING_SCHEMA})
    targets.append({"kind": "keyring-item",
                    "account": VITALS_KEYRING_LABEL,
                    "exists": None,
                    "detail": "Secret Service per-host Delete, "
                              "attributes %s" % VITALS_KEYRING_SCHEMA})
    targets.append({"kind": "keyring-item",
                    "account": "login keyring items owned by the user",
                    "exists": None,
                    "detail": "default collection, per-item Delete; "
                              "never rm %s as files" % KEYRINGS_REL_DIR})
    targets.extend(suite_review_notes())
    return targets


def format_plan(targets: list[dict]) -> str:
    """Human-readable dry-run listing for the owner review and docs."""
    lines = []
    for target in targets or []:
        kind = target.get("kind")
        if kind == "dir":
            state = "present" if target.get("exists") else "absent"
            lines.append("dir %s (%s)" % (target.get("path"), state))
        elif kind == "keystore-account":
            lines.append("keyring %s (per-item Delete)"
                         % target.get("account"))
        elif kind == "keyring-item":
            lines.append("keyring %s (%s)"
                         % (target.get("account"), target.get("detail")))
        elif kind == "todo":
            lines.append(str(target.get("detail")))
    return "\n".join(lines)


def _delete_dir_guarded(path: Path | str,
                        home: Path | str | None = None) -> bool:
    base = home_path(home)
    candidate = Path(path)
    if not is_safe_target(candidate, base):
        raise ValueError("refusing to delete outside the wipe list: %s"
                         % candidate)
    try:
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()
            return True
        if candidate.is_dir():
            shutil.rmtree(candidate)
            return True
    except FileNotFoundError:
        return False
    return False


def build_erase_action(*, home_factory=None, keystore=None,
                       collection_factory=None,
                       keyring_accounts: list[str] | None = None,
                       dry_run: bool = True):
    """Zero-arg `erase_user_data` for `AgentDeps`. Dry-run by default:
    plans and logs what WOULD be deleted and removes nothing. Pass
    `dry_run=False` explicitly for a real wipe (no caller in this repo
    does); even then only `is_safe_target` paths are removed, keyrings
    go through per-item `Delete`, and failures raise so the agent
    reports them."""

    def _erase() -> list[dict]:
        base = Path(home_factory()) if home_factory is not None \
            else home_path()
        targets = plan_erase(home=base,
                             keyring_accounts=keyring_accounts)
        if dry_run:
            LOG.info("erase dry-run, would delete:\n%s",
                     format_plan(targets))
            return targets
        for target in targets:
            if target.get("kind") != "dir" or not target.get("exists"):
                continue
            _delete_dir_guarded(target["path"], base)
        if keystore is not None:
            for account in (keyring_accounts or
                            ["xx-find/device-token",
                             "xx-find/owner-verify-key"]):
                try:
                    keystore.delete(account)
                except Exception as exc:
                    LOG.debug("keystore delete %r failed: %s", account, exc)
                    raise
        if collection_factory is not None:
            try:
                collection = collection_factory()
            except Exception as exc:
                raise RuntimeError("keyring unavailable: %s" % exc) from exc
            try:
                items = list(collection.search_items({})) \
                    if hasattr(collection, "search_items") else []
            except Exception as exc:
                raise RuntimeError("keyring read failed: %s" % exc) from exc
            for item in items:
                try:
                    item.delete()
                except Exception as exc:
                    raise RuntimeError(
                        "keyring delete failed: %s" % exc) from exc
        return targets

    return _erase
