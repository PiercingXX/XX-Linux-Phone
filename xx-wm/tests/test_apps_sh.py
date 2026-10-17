"""scripts/apps.sh — pacman browser, Tailscale, RAM-gated Waydroid."""
from pathlib import Path
import re
import subprocess

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'apps.sh'


def test_script_syntax_parses():
    result = subprocess.run(
        ['sh', '-n', str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_pacman_browser_is_waterfox_or_firefox_not_esr():
    text = SCRIPT.read_text(encoding='utf-8')
    start = text.find('# --- 17.1 Browser')
    assert start != -1
    chunk = text[start:text.find('# --- 17.2')]
    pacman_at = chunk.find('[ "$PKG" = pacman ]')
    firefox_dt = chunk.find('BROWSER_DESKTOP=firefox.desktop')
    assert pacman_at != -1
    assert firefox_dt != -1
    pacman_arm = chunk[pacman_at:firefox_dt]
    assert 'pacman -Si waterfox' in pacman_arm
    assert 'pacman -Si waterfox-bin' in pacman_arm
    assert 'firefox-esr' not in pacman_arm
    assert re.search(r'pkg_install firefox$', chunk, re.M)
    assert 'BROWSER_DESKTOP=firefox-esr.desktop' in chunk
    assert not re.search(r'^\s*yay\b', chunk, re.M)
    assert 'yay -' not in chunk
    assert 'pkg_install firefox-esr' in chunk


def test_xdg_settings_uses_chosen_desktop():
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'xdg-settings set default-web-browser "$BROWSER_DESKTOP"' in text


def test_tailscale_per_distro_then_static_fallback():
    text = SCRIPT.read_text(encoding='utf-8')
    chunk = text[text.find('# --- 17.3 Tailscale'):text.find('# --- 17.4')]
    # apk picks the service subpackage for the running init (pmOS is systemd)
    assert 'pkg_install tailscale tailscale-systemd' in chunk
    assert 'pkg_install tailscale tailscale-openrc' in chunk
    assert 'pacman) pkg_install tailscale' in chunk
    # Debian does not package tailscale: Tailscale's own apt repo
    assert 'pkgs.tailscale.com/stable/$_ts_dist/$_ts_code' in chunk
    assert 'TarballsVersion' in chunk          # static build, newest version
    assert 'systemctl enable --now tailscaled' in chunk
    assert 'rc-service tailscale start' in chunk
    # the shade tile toggles without root
    assert 'tailscale set --operator="$(id -un)"' in chunk
    assert chunk.find('ts_apt_repo ;;') < chunk.find('|| ts_static')


def test_waydroid_skipped_under_3gib():
    text = SCRIPT.read_text(encoding='utf-8')
    assert '3145728' in text
    assert 'MemTotal' in text
    assert 'SKIP: Waydroid' in text


def test_pwa_desktop_escapes_percent_and_quotes_url():
    text = SCRIPT.read_text(encoding='utf-8')
    assert "s/%/%%/g" in text
    assert 'Exec=$_browser --new-window "$_url"' in text
    assert 'pwa_desktop skippy "Skippy"' in text
    assert 'http://${SKIPPY_HOST}:8282/mobile/' in text
