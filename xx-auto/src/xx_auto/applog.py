"""Process log ring with redaction (same shape as the Android AppLog).

Never log contact numbers: digit runs of 7+ are masked, along with
tokens, emails and otpauth URLs.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass

RING_SIZE = 300

_TOKEN_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?i)(password|passwd|secret|token|otp|pin)\s*[=:]\s*\S+")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}")
_OTPAUTH_RE = re.compile(r"otpauth://\S+", re.IGNORECASE)
_PHONE_RE = re.compile(r"\+?[\d][\d ()\-]{5,}\d")


def redact(message: str) -> str:
    text = str(message or "")
    text = _TOKEN_RE.sub("Bearer ***", text)
    text = _SECRET_RE.sub(r"\1=***", text)
    text = _EMAIL_RE.sub("***@***", text)
    text = _OTPAUTH_RE.sub("otpauth://***", text)

    def _mask_number(match: re.Match) -> str:
        run = match.group(0)
        digits = sum(ch.isdigit() for ch in run)
        return "***" if digits >= 7 else run

    return _PHONE_RE.sub(_mask_number, text)


@dataclass
class Line:
    ts: float
    level: str
    tag: str
    message: str


class AppLog:
    """Thread-safe ring buffer; the suite log door drains it."""

    def __init__(self, size: int = RING_SIZE) -> None:
        self._lock = threading.Lock()
        self._ring: deque[Line] = deque(maxlen=size)

    def emit(self, level: str, tag: str, message: str) -> None:
        with self._lock:
            self._ring.append(Line(time.time(), level, tag, redact(message)))

    def d(self, tag: str, message: str) -> None:
        self.emit("D", tag, message)

    def i(self, tag: str, message: str) -> None:
        self.emit("I", tag, message)

    def w(self, tag: str, message: str) -> None:
        self.emit("W", tag, message)

    def e(self, tag: str, message: str) -> None:
        self.emit("E", tag, message)

    def dump(self) -> str:
        with self._lock:
            lines = list(self._ring)
        return "\n".join(f"{line.level} {line.tag} {line.message}"
                         for line in lines)

    def __len__(self) -> int:
        with self._lock:
            return len(self._ring)


LOG = AppLog()
