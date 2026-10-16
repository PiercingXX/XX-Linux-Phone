"""Agent configuration. Defaults to disabled; nothing runs until the owner
opts in via Settings AND pairs with a server (see PORT_SPEC.md)."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "xx-find"
CONFIG_FILE_NAME = "agent.conf"


def default_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(
        Path.home() / ".config")
    return Path(base) / APP_NAME


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(
        Path.home() / ".local" / "state")
    return Path(base) / APP_NAME


@dataclass
class AgentConfig:
    enabled: bool = False
    server_url: str = ""
    device_id: str = ""
    owner_public_key_hex: str = ""
    poll_interval_s: int = 900
    active_poll_interval_s: int = 360
    erase_enabled: bool = False
    last_counter: int = 0
    last_report_epoch: int = 0

    def is_paired(self) -> bool:
        return bool(self.server_url.strip()
                    and self.device_id.strip()
                    and self.owner_public_key_hex.strip())

    def is_operational(self) -> bool:
        return self.enabled and self.is_paired()


def _as_bool(value: str, fallback: bool) -> bool:
    text = value.strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return fallback


def from_dict(raw: dict) -> AgentConfig:
    section = raw.get("agent", raw) if isinstance(raw, dict) else {}
    if not isinstance(section, dict):
        section = {}
    cfg = AgentConfig()
    if isinstance(section.get("enabled"), bool):
        cfg.enabled = section["enabled"]
    elif section.get("enabled") is not None:
        cfg.enabled = _as_bool(str(section["enabled"]), False)
    for key in ("server_url", "device_id", "owner_public_key_hex"):
        value = section.get(key)
        if isinstance(value, str):
            setattr(cfg, key, value.strip())
    for key in ("poll_interval_s", "active_poll_interval_s",
                "last_counter", "last_report_epoch"):
        try:
            setattr(cfg, key, int(section[key]))
        except (KeyError, TypeError, ValueError):
            pass
    if isinstance(section.get("erase_enabled"), bool):
        cfg.erase_enabled = section["erase_enabled"]
    elif section.get("erase_enabled") is not None:
        cfg.erase_enabled = _as_bool(str(section["erase_enabled"]), False)
    return cfg


def to_dict(cfg: AgentConfig) -> dict:
    return {"agent": {
        "enabled": cfg.enabled,
        "server_url": cfg.server_url,
        "device_id": cfg.device_id,
        "owner_public_key_hex": cfg.owner_public_key_hex,
        "poll_interval_s": cfg.poll_interval_s,
        "active_poll_interval_s": cfg.active_poll_interval_s,
        "erase_enabled": cfg.erase_enabled,
        "last_counter": cfg.last_counter,
        "last_report_epoch": cfg.last_report_epoch,
    }}


def load(path: Path | None = None) -> AgentConfig:
    """Read config; missing or corrupt files yield safe defaults (disabled)."""
    target = Path(path) if path is not None else (
        default_config_dir() / CONFIG_FILE_NAME)
    parser = configparser.ConfigParser()
    try:
        with open(target, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return AgentConfig()
    raw = {section: dict(parser[section]) for section in parser.sections()}
    cfg = from_dict(raw)
    cfg.enabled = cfg.enabled and True
    return cfg


def save(cfg: AgentConfig, path: Path | None = None) -> Path:
    """Persist config atomically (tmp file + fsync + rename + dir fsync).

    Creates the parent dir mode 0700 first (first-run rule) and leaves the
    file mode 0600. Raises OSError when the counter does not reach disk:
    the agent treats that as persist failure and refuses to act, so a
    command is never executed without its counter being durably consumed.
    """
    target = Path(path) if path is not None else (
        default_config_dir() / CONFIG_FILE_NAME)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parser = configparser.ConfigParser()
    data = to_dict(cfg)["agent"]
    parser["agent"] = {k: str(v) for k, v in data.items()}
    import io as _io

    buf = _io.StringIO()
    parser.write(buf)
    text = buf.getvalue()
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    try:
        dfd = os.open(str(target.parent), os.O_DIRECTORY)
    except OSError:
        return target
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return target


@dataclass
class SecretRef:
    """Pointer to key material; the secret itself lives in libsecret
    (SecretStorage) on the phone, never in this file. Tests inject a
    memory-backed store instead of touching the real keyring."""

    account: str = ""
    present: bool = False
    store: object = field(default=None, repr=False, compare=False)
