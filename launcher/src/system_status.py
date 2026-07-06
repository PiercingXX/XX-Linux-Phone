from __future__ import annotations

import subprocess
from pathlib import Path


def battery_pct() -> int | None:
    """Read battery level from sysfs. Returns 0–100 or None if unavailable."""
    for path in Path('/sys/class/power_supply').glob('*/capacity'):
        try:
            return int(path.read_text())
        except (OSError, ValueError):
            pass
    return None


def battery_charging() -> bool:
    """True if any power supply reports Charging or Full status."""
    for path in Path('/sys/class/power_supply').glob('*/status'):
        try:
            status = path.read_text().strip()
            if status in ('Charging', 'Full'):
                return True
        except OSError:
            pass
    return False


def battery_label() -> str:
    pct = battery_pct()
    if pct is None:
        return ''
    charging = battery_charging()
    marker = '+' if charging else ''
    return f'{pct}%{marker}'


def wifi_ssid() -> str | None:
    """Return connected WiFi SSID, or None if not connected."""
    try:
        out = subprocess.check_output(
            ['iwgetid', '-r'], text=True, timeout=1,
        ).strip()
        return out or None
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    # Fallback: nmcli
    try:
        out = subprocess.check_output(
            ['nmcli', '-t', '-f', 'ACTIVE,SSID', 'dev', 'wifi'],
            text=True, timeout=2,
        )
        for line in out.splitlines():
            if line.startswith('yes:'):
                return line[4:] or None
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    return None


def mobile_signal() -> str | None:
    """Return mobile signal strength label (bars) or None if no modem."""
    try:
        out = subprocess.check_output(
            ['mmcli', '-m', '0', '--output-keyvalue'],
            text=True, timeout=2,
        )
        for line in out.splitlines():
            if 'signal-quality.value' in line:
                pct = int(line.split(':')[1].strip())
                bars = min(4, pct // 25)
                return '▂▄▆█'[:bars] if bars > 0 else '·'
    except Exception:
        pass
    return None


def status_line() -> str:
    """Single-line status string for the home screen header."""
    parts: list[str] = []

    ssid = wifi_ssid()
    if ssid:
        parts.append(ssid)
    else:
        sig = mobile_signal()
        if sig:
            parts.append(sig)

    bat = battery_label()
    if bat:
        parts.append(bat)

    return '  ·  '.join(parts)
