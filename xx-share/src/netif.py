"""Network interface filtering: serve and announce on non-cellular only.

Interface names come from sysfs, addresses via SIOCGIFADDR ioctl. Both
the sysfs root and the ioctl call are injectable so headless tests can
use a fake tree instead of the real radios.
"""
from __future__ import annotations

import array
import fcntl
import socket
import struct
from typing import Callable

CELLULAR_PREFIXES = ("wwan", "rmnet", "ccmni", "pdp", "wdm", "qmap", "wwan")

LOOPBACK_NAMES = ("lo", "lo0")


def is_cellular(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(CELLULAR_PREFIXES)


def _read_flags(sysfs: str, name: str) -> int | None:
    try:
        with open(f"{sysfs}/{name}/flags", encoding="utf-8") as handle:
            return int(handle.read().strip(), 0)
    except (OSError, ValueError):
        return None


def _ioctl_ipv4(name: str, ioctl: Callable | None = None) -> str | None:
    call = ioctl or fcntl.ioctl
    request = array.array("B", b"\0" * 40)
    struct.pack_into("16s", request, 0, name.encode("utf-8")[:15])
    try:
        call(socket.socket(socket.AF_INET, socket.SOCK_DGRAM).fileno(),
             0x8915, request)
    except OSError:
        return None
    addr = bytes(request[20:24])
    return socket.inet_ntoa(addr)


def interface_names(sysfs: str = "/sys/class/net") -> list[str]:
    import os

    try:
        return sorted(os.listdir(sysfs))
    except OSError:
        return []


def allowed_bind_addrs(
    sysfs: str = "/sys/class/net",
    include_loopback: bool = False,
    ioctl: Callable | None = None,
) -> list[str]:
    """IPv4 addresses of usable non-cellular interfaces.

    Requires IFF_UP; loopback is included only when tests (or an explicit
    loopback-only setup) ask for it via include_loopback.
    """
    addrs: list[str] = []
    for name in interface_names(sysfs):
        if is_cellular(name):
            continue
        flags = _read_flags(sysfs, name)
        if flags is None or not flags & 0x1:
            continue
        addr = _ioctl_ipv4(name, ioctl)
        if not addr:
            continue
        is_loopback = addr.startswith("127.") or name in LOOPBACK_NAMES
        if is_loopback and not include_loopback:
            continue
        addrs.append(addr)
    return addrs
