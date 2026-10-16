"""Cleartext guard: HTTPS always; cleartext only to configured hosts.

Port of sync/CleartextGuardInterceptor.kt. Pure decision logic, no I/O.
"""

from __future__ import annotations

from urllib.parse import urlparse


def _split_host_port(entry: str) -> tuple[str, int | None]:
    idx = entry.rfind(":")
    if idx <= 0:
        return entry, None
    try:
        return entry[:idx], int(entry[idx + 1:])
    except ValueError:
        return entry, None


def _entry_matches(entry: str, host: str, port: int) -> bool:
    trimmed = entry.strip()
    if not trimmed:
        return False
    if "://" in trimmed:
        try:
            parsed = urlparse(trimmed)
        except ValueError:
            return False
        parsed_host = parsed.hostname or ""
        parsed_port = parsed.port
        if parsed_port is None:
            parsed_port = 443 if parsed.scheme == "https" else 80
        return parsed_host.lower() == host.lower() and parsed_port == port
    entry_host, entry_port = _split_host_port(trimmed)
    return entry_host.lower() == host.lower() and (entry_port is None or entry_port == port)


def is_cleartext_allowed(host: str, port: int, configured: set[str]) -> bool:
    return any(_entry_matches(entry, host, port) for entry in configured)


def is_https_allowed(url: str) -> bool:
    return urlparse(url).scheme == "https"


def check_url(url: str, configured: set[str]) -> bool:
    if is_https_allowed(url):
        return True
    parsed = urlparse(url)
    port = parsed.port or 80
    return is_cleartext_allowed(parsed.hostname or "", port, configured)


def guard_url(url: str, configured: set[str]) -> None:
    if not check_url(url, configured):
        parsed = urlparse(url)
        raise OSError(
            f"cleartext to {parsed.hostname}:{parsed.port or 80} denied by LAN-only policy"
            f" (allowed: {configured}); use HTTPS or configure this host"
        )
