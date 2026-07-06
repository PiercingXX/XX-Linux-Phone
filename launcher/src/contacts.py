from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Contact:
    name: str
    numbers: list[str] = field(default_factory=list)

    def primary_number(self) -> str:
        return self.numbers[0] if self.numbers else ''


_VCF_SEARCH_PATHS = [
    Path.home() / '.local/share/gnome-contacts/system.vcf',
    Path.home() / '.local/share/contacts/contacts.vcf',
    Path('/var/lib/gnome-contacts/contacts.vcf'),
]

_LOCAL_JSON = Path.home() / '.local/share/piercing-shell/contacts.json'


class ContactBook:
    """Reads contacts from a local JSON file and/or GNOME Contacts VCF."""

    def __init__(self) -> None:
        self._contacts: list[Contact] = []
        self._reload()

    def _reload(self) -> None:
        self._contacts = []
        self._load_local_json()
        self._load_vcf()

    def _load_local_json(self) -> None:
        if not _LOCAL_JSON.exists():
            return
        try:
            raw = json.loads(_LOCAL_JSON.read_text(encoding='utf-8'))
            for entry in raw:
                name = entry.get('name', '').strip()
                nums = [str(n).strip() for n in entry.get('numbers', []) if n]
                if name and nums:
                    self._contacts.append(Contact(name=name, numbers=nums))
        except Exception:
            pass

    def _load_vcf(self) -> None:
        for path in _VCF_SEARCH_PATHS:
            if path.exists():
                try:
                    self._parse_vcf(path.read_text(encoding='utf-8', errors='replace'))
                except Exception:
                    pass
                break

    def _parse_vcf(self, text: str) -> None:
        existing_names = {c.name for c in self._contacts}
        cards = re.split(r'(?im)^BEGIN:VCARD', text)
        for card in cards:
            name = ''
            numbers: list[str] = []
            for line in card.splitlines():
                up = line.upper()
                if up.startswith('FN:'):
                    name = line[3:].strip()
                elif re.match(r'TEL', line, re.IGNORECASE):
                    # TEL;TYPE=CELL:+1234567890  or  TEL:+1234567890
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        num = re.sub(r'[^\d+]', '', parts[1].strip())
                        if num:
                            numbers.append(num)
            if name and numbers and name not in existing_names:
                self._contacts.append(Contact(name=name, numbers=numbers))
                existing_names.add(name)

    # ------------------------------------------------------------------ public

    def search(self, query: str, limit: int = 5) -> list[Contact]:
        """Return contacts matching a name prefix or number prefix/suffix."""
        if not query:
            return []
        q = query.casefold()
        stripped = re.sub(r'[^\d]', '', query)

        number_hits: list[Contact] = []
        name_prefix_hits: list[Contact] = []
        name_sub_hits: list[Contact] = []

        for c in self._contacts:
            matched_num = False
            if stripped:
                for n in c.numbers:
                    ns = re.sub(r'[^\d]', '', n)
                    if ns.startswith(stripped) or ns.endswith(stripped):
                        matched_num = True
                        break
            if matched_num:
                number_hits.append(c)
                continue
            name_cf = c.name.casefold()
            if name_cf.startswith(q):
                name_prefix_hits.append(c)
            elif q in name_cf:
                name_sub_hits.append(c)

        return (number_hits + name_prefix_hits + name_sub_hits)[:limit]

    def lookup_number(self, number: str) -> str | None:
        """Return the contact name for a phone number, or None."""
        stripped = re.sub(r'[^\d]', '', number)
        if not stripped:
            return None
        for c in self._contacts:
            for n in c.numbers:
                ns = re.sub(r'[^\d]', '', n)
                if not ns:
                    continue
                tail = min(7, len(stripped), len(ns))
                if stripped[-tail:] == ns[-tail:]:
                    return c.name
        return None

    @property
    def all(self) -> list[Contact]:
        return list(self._contacts)


# ------------------------------------------------------------------ mmcli SMS

def _strip_label(line: str, label: str) -> str | None:
    """Extract value after 'label:' in mmcli tabular output."""
    m = re.search(rf'\|\s+{re.escape(label)}:\s+(.+)', line)
    return m.group(1).strip() if m else None


def load_sms_history(modem_idx: int = 0, timeout: int = 8) -> list[dict]:
    """
    Fetch SMS history from ModemManager via mmcli.
    Returns list of dicts: {number, text, outgoing, timestamp_iso}.
    Returns empty list if mmcli is unavailable or fails.
    """
    try:
        result = subprocess.run(
            ['mmcli', '-m', str(modem_idx), '--messaging-list-sms'],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    paths = re.findall(r'(/org/freedesktop/ModemManager1/SMS/\d+)', result.stdout)
    if not paths:
        return []

    messages: list[dict] = []
    for path in paths:
        try:
            r = subprocess.run(
                ['mmcli', '-s', path],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            continue

        number = ''
        text = ''
        timestamp = ''
        pdu_type = ''

        for line in r.stdout.splitlines():
            v = _strip_label(line, 'number')
            if v:
                number = v
            v = _strip_label(line, 'text')
            if v:
                text = v
            v = _strip_label(line, 'timestamp')
            if v:
                timestamp = v
            v = _strip_label(line, 'pdu type')
            if v:
                pdu_type = v

        if not number or not text:
            continue

        outgoing = pdu_type.lower() in ('submit', 'submit-report')
        messages.append({
            'number': number,
            'text': text,
            'outgoing': outgoing,
            'timestamp_iso': timestamp,
        })

    return messages
