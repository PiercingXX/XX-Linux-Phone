#!/bin/sh
# XX-WM — default app set (todo.md Workstream 17).
# Called from install.sh: apps.sh <pkg:apk|apt|pacman> <sudo:doas|sudo>
# Every item is guarded — one missing package never hard-fails the menu.
# GitHub.com/PiercingXX

set -u

PKG=${1:-}
SUDO=${2:-}
if [ -z "$PKG" ] || [ -z "$SUDO" ]; then
    if command -v apk >/dev/null 2>&1; then
        PKG=apk
    elif command -v apt >/dev/null 2>&1; then
        PKG=apt
    elif command -v pacman >/dev/null 2>&1; then
        PKG=pacman
    else
        PKG=apt
    fi
    if command -v doas >/dev/null 2>&1; then SUDO=doas; else SUDO=sudo; fi
fi

APPDIR="$HOME/.local/share/applications"
mkdir -p "$APPDIR"

pkg_install() {
    if [ "$PKG" = apk ]; then
        $SUDO apk add "$@" || echo "warn: install failed: $*" >&2
    elif [ "$PKG" = pacman ]; then
        $SUDO pacman -S --needed --noconfirm "$@" || echo "warn: install failed: $*" >&2
    else
        $SUDO apt install -y "$@" || echo "warn: install failed: $*" >&2
    fi
}

find_browser() {
    for b in waterfox firefox firefox-esr; do
        if command -v "$b" >/dev/null 2>&1; then
            echo "$b"
            return 0
        fi
    done
    return 1
}

ask() {
    # ask "<prompt>" "<default>"
    if command -v whiptail >/dev/null 2>&1; then
        whiptail --inputbox "$1" 0 0 "$2" 3>&1 1>&2 2>&3
    else
        printf '%s [%s]: ' "$1" "$2" >&2
        read -r _ans
        echo "${_ans:-$2}"
    fi
}

pwa_desktop() {
    # pwa_desktop <id> <name> <url>
    _browser=$(find_browser) || { echo "warn: no browser for $2 PWA" >&2; return 1; }
    # Encode the URL for a double-quoted Exec argument (Desktop Entry Spec):
    # %% for field codes, then escape " ` $ \ with a backslash. Every
    # backslash is doubled once more because the KeyFile layer consumes one
    # level of \\ escapes before the Exec line is parsed; the same doubling
    # keeps the unquoted heredoc below from expanding $ and ` as shell syntax.
    _url=$(printf '%s' "$3" | sed \
        -e 's/%/%%/g' \
        -e 's/\\/\\\\\\\\/g' \
        -e 's/"/\\\\"/g' \
        -e 's/`/\\\\`/g' \
        -e 's/\$/\\\\$/g')
    cat > "$APPDIR/$1.desktop" << EOF
[Desktop Entry]
Type=Application
Name=$2
Exec=$_browser --new-window "$_url"
Terminal=false
Categories=Network;
EOF
    echo "$2 PWA entry written."
}

# --- 17.1 Browser -----------------------------------------------------------
# Waterfox has no arm64 Linux build (checked 2026-07; x86-centric) → Firefox
# ESR + mobile-config-firefox for phone ergonomics.
echo "Installing browser..."
if [ "$PKG" = apk ]; then
    pkg_install firefox-esr mobile-config-firefox
else
    pkg_install firefox-esr mobile-config-firefox
fi
if command -v firefox-esr >/dev/null 2>&1 && command -v xdg-settings >/dev/null 2>&1; then
    xdg-settings set default-web-browser firefox-esr.desktop 2>/dev/null || true
fi

# --- 17.2 Calculator --------------------------------------------------------
pkg_install gnome-calculator

# --- 17.3 Tailscale ---------------------------------------------------------
if command -v tailscale >/dev/null 2>&1; then
    echo "Tailscale already installed."
elif [ "$PKG" = apk ] && $SUDO apk add tailscale 2>/dev/null; then
    $SUDO rc-update add tailscale default 2>/dev/null || true
    $SUDO rc-service tailscale start 2>/dev/null || true
    echo "Tailscale installed — run: $SUDO tailscale up"
elif [ "$PKG" = apt ] && $SUDO apt install -y tailscale 2>/dev/null; then
    $SUDO systemctl enable --now tailscaled 2>/dev/null || true
    echo "Tailscale installed — run: $SUDO tailscale up"
else
    # Static tgz fallback, exactly like the reference apps.sh (service-path sed)
    echo "Installing Tailscale from static tgz..."
    TS_VER=1.90.9
    TS_ARCH=arm64
    case "$(uname -m)" in x86_64) TS_ARCH=amd64 ;; esac
    TS_DIR="tailscale_${TS_VER}_${TS_ARCH}"
    if wget -q "https://pkgs.tailscale.com/stable/${TS_DIR}.tgz" && tar xzf "${TS_DIR}.tgz"; then
        sed -i 's|/usr/sbin/tailscaled|/usr/local/bin/tailscaled|g' "$TS_DIR/systemd/tailscaled.service"
        $SUDO mv "$TS_DIR/tailscale" /usr/local/bin/tailscale
        $SUDO mv "$TS_DIR/tailscaled" /usr/local/bin/tailscaled
        if [ "$(ps -p 1 -o comm= 2>/dev/null)" = systemd ]; then
            $SUDO mv "$TS_DIR/systemd/tailscaled.service" /etc/systemd/system/tailscaled.service
            $SUDO mv "$TS_DIR/systemd/tailscaled.defaults" /etc/default/tailscaled
            $SUDO systemctl daemon-reload
            $SUDO systemctl enable --now tailscaled.service
        else
            echo "warn: OpenRC service for static tailscaled not written — start tailscaled manually" >&2
        fi
        rm -rf "${TS_DIR}.tgz" "$TS_DIR"
        echo "Tailscale installed — run: $SUDO tailscale up"
    else
        echo "warn: Tailscale download failed" >&2
    fi
fi

# --- 17.4 Skippy (mobile PWA over Tailscale) --------------------------------
SKIPPY_HOST=$(ask "Skippy host (Tailscale IP or name)" "skippy")
if [ -n "$SKIPPY_HOST" ]; then
    pwa_desktop skippy "Skippy" "http://${SKIPPY_HOST}:8282/mobile/" || true
fi

# --- 17.5 Audiobookshelf (PWA path first — no Waydroid dependency) ----------
ABS_URL=$(ask "Audiobookshelf server URL (blank to skip)" "")
if [ -n "$ABS_URL" ]; then
    pwa_desktop audiobookshelf "Audiobook" "$ABS_URL" || true
fi

# --- 17.6 Waydroid (install only — init/microG is device work) --------------
if command -v waydroid >/dev/null 2>&1; then
    echo "Waydroid already installed."
elif [ "$PKG" = apk ] && $SUDO apk add waydroid 2>/dev/null; then
    echo "Waydroid installed via apk."
elif [ "$PKG" = apt ]; then
    if ! $SUDO apt install -y waydroid 2>/dev/null; then
        pkg_install curl ca-certificates
        curl -s https://repo.waydro.id | $SUDO bash || echo "warn: waydro.id repo setup failed" >&2
        pkg_install waydroid
    fi
else
    echo "warn: Waydroid unavailable on this distro" >&2
fi
echo "Waydroid init + microG image + Android apps are device work — see devices/ notes."

# --- 17.8 Utilities baseline ------------------------------------------------
pkg_install neovim ripgrep wl-clipboard
if [ "$PKG" = apk ]; then
    pkg_install yazi || echo "warn: yazi not in repos — install via cargo later" >&2
else
    pkg_install yazi || echo "warn: yazi not in repos — install via cargo later" >&2
fi
pkg_install ufw
if command -v ufw >/dev/null 2>&1; then
    $SUDO ufw allow SSH 2>/dev/null || true
    $SUDO ufw --force enable 2>/dev/null || true
fi

echo "Phone app set done."
