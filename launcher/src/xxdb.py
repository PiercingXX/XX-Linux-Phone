"""xxdb core: pairing, trust store, RPC framing, path rules, interface
selection, and idle auto-off. Pure stdlib, no GTK — headless-testable.

The pairing binds the short code to both certificates: the host proves
knowledge of the 6-digit code with an HMAC over (phone cert fingerprint,
host public cert), so a man-in-the-middle cannot substitute either cert
without the code.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import re
import secrets
from pathlib import Path

PORT_MIN = 37000
PORT_MAX = 37999
PAIR_CODE_TTL_S = 120
PAIR_MAX_TRIES = 3
IDLE_TIMEOUT_S = 3600
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_LINE_BYTES = 768 * 1024 * 1024
SYSTEM_CONFIRM_TTL_S = 300

XXDB_DIRNAME = 'xxdb'
KEY_FILE = 'key.pem'
CERT_FILE = 'cert.pem'
TRUST_FILE = 'trusted.json'

PUBLIC_COMMANDS = frozenset({'pair'})


def data_dir(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / '.local' / 'share' / 'xx-wm' / XXDB_DIRNAME


def pick_port(rng: random.Random | None = None) -> int:
    source = rng if rng is not None else random.SystemRandom()
    return source.randint(PORT_MIN, PORT_MAX)


def new_pairing_code(randbelow=None) -> str:
    pick = randbelow if randbelow is not None else secrets.randbelow
    return f'{pick(1_000_000):06d}'


def cert_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def pairing_mac(code: str, phone_fp_hex: str, host_cert_der: bytes) -> str:
    msg = phone_fp_hex.encode('ascii') + b'|' + bytes(host_cert_der)
    return hmac.new(code.encode('ascii'), msg, hashlib.sha256).hexdigest()


def verify_pairing_mac(guess: str, phone_fp_hex: str,
                       host_cert_der: bytes, mac_hex: str) -> bool:
    try:
        expected = pairing_mac(guess, phone_fp_hex, host_cert_der)
    except (ValueError, UnicodeEncodeError):
        return False
    return hmac.compare_digest(expected, mac_hex)


class PairingSession:
    """One single-use 6-digit code: valid 2 minutes, burned after 3 misses."""

    def __init__(self, clock=None) -> None:
        self._clock = clock if clock is not None else __import__('time').time
        self.code: str | None = None
        self.expires_at: float = 0.0
        self.tries: int = 0
        self.used = False

    def issue(self, now: float | None = None) -> tuple[str, float]:
        now = self._clock() if now is None else now
        self.code = new_pairing_code()
        self.expires_at = now + PAIR_CODE_TTL_S
        self.tries = 0
        self.used = False
        return self.code, self.expires_at

    def attempt(self, code_guess: str, mac_hex: str, phone_fp_hex: str,
                host_cert_der: bytes,
                now: float | None = None) -> tuple[bool, str]:
        now = self._clock() if now is None else now
        if self.code is None or self.used:
            return False, 'no active pairing code'
        if now > self.expires_at:
            self.code = None
            return False, 'pairing code expired'
        if self.tries >= PAIR_MAX_TRIES:
            self.code = None
            return False, 'pairing code burned'
        code_ok = hmac.compare_digest(str(code_guess), self.code)
        mac_ok = verify_pairing_mac(self.code, phone_fp_hex,
                                    host_cert_der, mac_hex)
        if code_ok and mac_ok:
            self.used = True
            shown = self.code
            self.code = None
            return True, shown
        self.tries += 1
        if self.tries >= PAIR_MAX_TRIES:
            self.code = None
            return False, 'pairing code burned'
        return False, 'wrong pairing code'


class TrustStore:
    """Trusted host certificates, keyed by cert fingerprint. File is 0600."""

    def __init__(self, path: Path | None = None,
                 home: Path | None = None) -> None:
        self.path = path if path is not None else data_dir(home) / TRUST_FILE
        self._hosts: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and isinstance(raw.get('hosts'), list):
            for entry in raw['hosts']:
                if (isinstance(entry, dict)
                        and re.fullmatch(r'[0-9a-f]{64}',
                                         str(entry.get('fingerprint') or ''))):
                    self._hosts[str(entry['fingerprint'])] = {
                        'fingerprint': str(entry['fingerprint']),
                        'name': str(entry.get('name', '?')),
                        'cert_pem': str(entry.get('cert_pem', '')),
                        'added': float(entry.get('added', 0.0)),
                    }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump({'hosts': list(self._hosts.values())},
                          handle, indent=2)
                handle.write('\n')
        finally:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def add(self, fingerprint: str, name: str, cert_pem: str,
            now: float) -> None:
        self._hosts[fingerprint] = {
            'fingerprint': fingerprint,
            'name': name,
            'cert_pem': cert_pem,
            'added': float(now),
        }
        self.save()

    def remove(self, fingerprint: str) -> bool:
        if fingerprint in self._hosts:
            del self._hosts[fingerprint]
            self.save()
            return True
        return False

    def contains(self, fingerprint: str) -> bool:
        return fingerprint in self._hosts

    def name_of(self, fingerprint: str) -> str | None:
        entry = self._hosts.get(fingerprint)
        return entry['name'] if entry else None

    def list(self) -> list[dict]:
        return sorted(self._hosts.values(), key=lambda e: e['added'])


class FrameError(ValueError):
    pass


def encode_message(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(',', ':')) + '\n').encode('utf-8')


class FrameReader:
    """Splits a TLS byte stream into JSON objects, one per line."""

    def __init__(self, max_line: int = MAX_LINE_BYTES) -> None:
        self._buf = bytearray()
        self._max_line = max_line

    def feed(self, data: bytes) -> list[dict]:
        self._buf.extend(data)
        messages = []
        while True:
            newline = self._buf.find(b'\n')
            if newline < 0:
                if len(self._buf) > self._max_line:
                    raise FrameError('line too long')
                return messages
            raw = bytes(self._buf[:newline])
            del self._buf[:newline + 1]
            if not raw.strip():
                continue
            if len(raw) > self._max_line:
                raise FrameError('line too long')
            try:
                obj = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as error:
                raise FrameError(f'bad frame: {error}') from error
            if not isinstance(obj, dict):
                raise FrameError('frame must be a JSON object')
            messages.append(obj)


def check_inside_home(path_str: str, home_dir: str | Path) -> Path | None:
    """Absolute normalized path when `path_str` stays under $HOME, else None."""
    home = Path(home_dir).resolve() if isinstance(home_dir, str) else home_dir
    candidate = Path(os.path.expanduser(path_str))
    if not candidate.is_absolute():
        candidate = home / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = Path(os.path.normpath(os.path.join(home, candidate)))
    if resolved == home or str(resolved).startswith(str(home) + os.sep):
        return resolved
    return None


class SystemAccess:
    """User-confirmed escapes hatch for paths outside $HOME (--system).

    The host asks, the phone user confirms in the Developer section, then
    the host retries the same command carrying the token.
    """

    def __init__(self, clock=None) -> None:
        self._clock = clock if clock is not None else __import__('time').time
        self._pending: dict[str, dict] = {}

    def request(self, label: str, now: float | None = None) -> str:
        now = self._clock() if now is None else now
        self._expire(now)
        token = secrets.token_hex(8)
        self._pending[token] = {'label': label, 'allowed': False,
                                'expires': now + SYSTEM_CONFIRM_TTL_S}
        return token

    def confirm(self, token: str, allow: bool,
                now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        self._expire(now)
        entry = self._pending.get(token)
        if entry is None:
            return False
        entry['allowed'] = bool(allow)
        if not allow:
            del self._pending[token]
        return True

    def consume(self, token: str | None,
                now: float | None = None) -> str | None:
        now = self._clock() if now is None else now
        self._expire(now)
        if not token:
            return None
        entry = self._pending.get(token)
        if entry is None or not entry['allowed']:
            return None
        del self._pending[token]
        return str(entry['label'])

    def pending(self, now: float | None = None) -> list[tuple[str, str]]:
        now = self._clock() if now is None else now
        self._expire(now)
        return [(token, info['label']) for token, info in self._pending.items()
                if not info['allowed']]

    def _expire(self, now: float) -> None:
        for token in [t for t, info in self._pending.items()
                      if info['expires'] <= now]:
            del self._pending[token]


_CELLULAR_PREFIXES = ('wwan', 'rmnet', 'ccmni', 'qmi', 'qmap', 'pdp_ip',
                      'ccinet', 'usb', 'rndis', 'ncm')
_WIFI_PREFIXES = ('wlan', 'wlp', 'wifi', 'mlan', 'wl')


def classify_interface(name: str, wireless: bool = False) -> str:
    lowered = name.lower()
    if lowered == 'lo' or lowered.startswith('lo'):
        return 'loopback'
    for prefix in _CELLULAR_PREFIXES:
        if lowered.startswith(prefix):
            return 'cellular'
    if wireless or lowered.startswith(_WIFI_PREFIXES):
        return 'wifi'
    return 'other'


def select_bind_addresses(entries: list[tuple[str, bool, list[str], bool]],
                           allow_loopback: bool = False) -> list[str]:
    """Pick bind addresses from (name, wireless, addrs, up) rows.

    Only Wi-Fi interfaces; never cellular. Loopback joins only through the
    test-only override so the headless loopback test can run without Wi-Fi.
    """
    addrs: list[str] = []
    for name, wireless, candidates, up in entries:
        if not up:
            continue
        kind = classify_interface(name, wireless)
        if kind == 'wifi':
            addrs.extend(candidates)
        elif kind == 'loopback' and allow_loopback:
            addrs.extend(candidates)
    seen = set()
    unique = []
    for addr in addrs:
        if addr and addr not in seen:
            seen.add(addr)
            unique.append(addr)
    return unique


def parse_ip_addr(text: str) -> list[tuple[str, bool, list[str], bool]]:
    """Parse `ip -o -4 addr show` into (name, wireless, addrs, up) rows.

    Wireless detection is filled in by `interface_entries()` via sysfs;
    this parser leaves it False.
    """
    entries: dict[str, dict] = {}
    for line in text.splitlines():
        match = re.match(r'\d+:\s+(\S+?)(?:@\S+)?:\s+<([^>]*)>.*?\s+inet\s+(\S+)',
                         line)
        if not match:
            continue
        name, flags, cidr = match.groups()
        addr = cidr.split('/')[0]
        row = entries.setdefault(name, {'up': False, 'addrs': []})
        if 'UP' in [flag.strip() for flag in flags.split(',')]:
            row['up'] = True
        if addr and addr not in row['addrs']:
            row['addrs'].append(addr)
    return [(name, False, row['addrs'], row['up'])
            for name, row in entries.items()]


def interface_entries(run=None) -> list[tuple[str, bool, list[str], bool]]:
    import subprocess
    runner = run if run is not None else subprocess.run
    try:
        result = runner(['ip', '-o', '-4', 'addr', 'show'],
                        capture_output=True, text=True, timeout=10)
    except (OSError, TypeError):
        return []
    if getattr(result, 'returncode', 1) != 0:
        return []
    rows = parse_ip_addr(getattr(result, 'stdout', '') or '')
    enriched = []
    for name, _wireless, addrs, up in rows:
        wireless = os.path.isdir(f'/sys/class/net/{name}/wireless')
        enriched.append((name, wireless, addrs, up))
    return enriched


def idle_expired(enabled: bool, active_clients: int,
                 last_activity: float, now: float) -> bool:
    return (enabled and active_clients == 0
            and now - last_activity >= IDLE_TIMEOUT_S)
