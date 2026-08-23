#!/bin/sh
# XX-WM installer — whiptail TUI, cached sudo, network check up front.
# POSIX sh; runs on postmarketOS/Alpine (apk) and Debian/Mobian (apt).
# GitHub.com/PiercingXX

set -u

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

# --- privilege helper (postmarketOS ships doas) -----------------------------
if command -v doas >/dev/null 2>&1; then
    SUDO=doas
elif command -v sudo >/dev/null 2>&1; then
    SUDO=sudo
else
    echo "Need doas or sudo." >&2
    exit 1
fi

# --- network check up front -------------------------------------------------
net_ok() {
    if command -v nmcli >/dev/null 2>&1; then
        [ "$(nmcli -t -f STATE g 2>/dev/null)" = "connected" ] && return 0
    fi
    ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1
}
if ! net_ok; then
    echo "Network connectivity is required to continue." >&2
    exit 1
fi

# --- package manager --------------------------------------------------------
if command -v apk >/dev/null 2>&1; then
    PKG=apk
elif command -v apt >/dev/null 2>&1; then
    PKG=apt
else
    echo "Unsupported distro: need apk or apt." >&2
    exit 1
fi

pkg_install() {
    # Never hard-fail the menu on one missing package
    if [ "$PKG" = apk ]; then
        $SUDO apk add "$@" || echo "warn: some packages failed: $*" >&2
    else
        $SUDO apt install -y "$@" || echo "warn: some packages failed: $*" >&2
    fi
}

# --- whiptail ---------------------------------------------------------------
if ! command -v whiptail >/dev/null 2>&1; then
    echo "Installing whiptail..."
    if [ "$PKG" = apk ]; then pkg_install newt; else pkg_install whiptail; fi
fi

# --- cached sudo (doas has no timestamp cache; skip there) ------------------
if [ "$SUDO" = sudo ]; then
    echo "Caching sudo credentials..."
    sudo -v
    ( while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null & )
fi

msg_box() {
    whiptail --msgbox "$1" 0 0 0
}

install_deps() {
    echo "Installing build and runtime dependencies..."
    if [ "$PKG" = apk ]; then
        pkg_install py3-gobject3 gtk4.0 libadwaita gtk4-layer-shell \
            meson ninja rsync git squeekboard phoc lisgd wl-clipboard
    else
        pkg_install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
            libgtk4-layer-shell0 meson ninja-build rsync git squeekboard \
            phoc lisgd wl-clipboard
    fi
}

build_install() {
    echo "Building XX-WM..."
    cd "$REPO_DIR/launcher" || return 1
    meson setup build --prefix=/usr --reconfigure || meson setup build --prefix=/usr || return 1
    $SUDO meson install -C build || return 1
    cd "$REPO_DIR" || return 1

    # squeekboard only reads the user path — symlink the installed layouts
    mkdir -p "$HOME/.local/share/squeekboard"
    ln -sfn /usr/share/xx-wm/squeekboard \
        "$HOME/.local/share/squeekboard/keyboards"

    # piercing-dots phone profile (parallel task — tolerate its loud failure)
    sh "$REPO_DIR/scripts/bootstrap-dots.sh" || \
        echo "warn: piercing-dots phone profile not applied yet" >&2

    select_phoc_scale
    enable_service
    add_input_group
}

# --- per-device phoc.ini scale (20.5) --------------------------------------
# The tablet's panel reports as DSI-1 (wanting scale 1.5) and collides with
# the FP5's DSI-1 (scale 2.5), so a single phoc.ini cannot serve both. Prompt
# for the device and copy its fragment over the meson-installed default.
# Cancelling the prompt keeps the default (FP5+FLX1) file.
select_phoc_scale() {
    dev=$(whiptail --backtitle "GitHub.com/PiercingXX" --title "Device" \
        --menu "Which device is this? (sets phoc.ini scale)" 0 0 0 \
        "fairphone-5"    "FP5 — DSI-1, scale 2.5" \
        "furiphone-flx1" "FLX1 — HWCOMPOSER-1, scale 3" \
        "librem-5"       "Librem 5 — DSI-1, scale 2" \
        "tablet"         "x86 tablet — DSI-1, scale 1.5" \
        3>&1 1>&2 2>&3) || return 0
    frag="$REPO_DIR/launcher/data/phoc/$dev.ini"
    if [ ! -f "$frag" ]; then
        echo "warn: no phoc.ini fragment for '$dev' — keeping default" >&2
        return 0
    fi
    $SUDO cp "$frag" /usr/share/xx-wm/phoc.ini || \
        echo "warn: could not install phoc.ini fragment for '$dev'" >&2
}

# --- input group (20.3) -----------------------------------------------------
# lisgd (touchscreen) and DisplayManager (power/volume keys) read evdev
# directly; without membership in the `input` group they silently do nothing.
# usermod -aG appends and is idempotent; we skip the call entirely when the
# user is already a member so the "re-login" note only appears when it matters.
add_input_group() {
    user=$(id -un)
    if id -nG "$user" 2>/dev/null | grep -qw input; then
        return 0
    fi
    $SUDO usermod -aG input "$user" 2>/dev/null || {
        echo "warn: could not add '$user' to the input group" >&2
        return 1
    }
    echo "Added '$user' to the input group — re-login (or reboot) for it to take effect."
}

enable_service() {
    init_comm=$(ps -p 1 -o comm= 2>/dev/null || echo unknown)
    if [ "$init_comm" = systemd ]; then
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user enable xx-wm 2>/dev/null || \
            echo "warn: enable the service after first login: systemctl --user enable xx-wm" >&2
    else
        # postmarketOS default images are OpenRC
        $SUDO rc-update add xx-wm default 2>/dev/null || \
            echo "warn: rc-update add xx-wm default failed" >&2
    fi
}

# --- GDM Colemak OSK (24) ---------------------------------------------------
# The GDM greeter runs GNOME Shell's own on-screen keyboard, which reads JSON
# layouts from /usr/share/gnome-shell/osk-layouts/ — squeekboard layouts do
# not apply there, so the login screen types QWERTY unless the stock US
# layout is overwritten. Only offered when GDM is actually wired up as the
# display manager — enabled, or at least shipped and selected by the distro
# config; a bare binary with nothing pointing at it makes the entry inert.
# pmOS/FuriOS phones don't run GDM anyway. Backs up the original once;
# restoring is a plain cp.
gdm_detected() {
    if ! command -v systemctl >/dev/null 2>&1; then
        # No systemd (postmarketOS/OpenRC): keep the old binary-only probe.
        command -v gdm >/dev/null 2>&1 || command -v gdm3 >/dev/null 2>&1
        return
    fi
    if ! command -v gdm >/dev/null 2>&1 && ! command -v gdm3 >/dev/null 2>&1; then
        return 1
    fi
    if systemctl -q is-enabled gdm 2>/dev/null || systemctl -q is-enabled gdm3 2>/dev/null; then
        return 0
    fi
    if systemctl cat gdm >/dev/null 2>&1 || systemctl cat gdm3 >/dev/null 2>&1; then
        return 0
    fi
    grep -q gdm /etc/X11/default-display-manager 2>/dev/null && return 0
    readlink /etc/systemd/system/display-manager.service 2>/dev/null | grep -q gdm
}

install_gdm_osk() {
    src=/usr/share/xx-wm/gnome-osk/us.json
    dst=/usr/share/gnome-shell/osk-layouts/us.json
    bak="$dst.xx-wm-backup"
    if [ ! -f "$src" ]; then
        echo "warn: $src missing — run Install or Update first" >&2
        return 1
    fi
    if ! whiptail --backtitle "GitHub.com/PiercingXX" \
        --title "GDM Colemak keyboard" --yesno \
        "This OVERWRITES the stock GNOME Shell US on-screen keyboard with the Colemak layout.\n\nThe original is backed up once to:\n  $bak\n\nRestore QWERTY later with:\n  sudo cp $bak $dst\n\nContinue?" 0 0; then
        return 0
    fi
    if [ ! -f "$bak" ]; then
        $SUDO cp "$dst" "$bak" || {
            echo "warn: could not back up $dst" >&2
            return 1
        }
    fi
    $SUDO cp "$src" "$dst" || {
        echo "warn: could not install the Colemak GDM OSK layout" >&2
        return 1
    }
    msg_box "GDM now types Colemak on the login screen.\nRestore QWERTY with:\n  sudo cp $bak $dst"
}

do_install() {
    install_deps
    if build_install; then
        msg_box "XX-WM installed. Select the PiercingOS session at next login, or reboot."
    else
        msg_box "Install hit an error — check the terminal output."
    fi
}

do_update() {
    echo "Updating repo..."
    git -C "$REPO_DIR" pull --ff-only || echo "warn: git pull failed" >&2
    if build_install; then
        msg_box "XX-WM updated."
    else
        msg_box "Update hit an error — check the terminal output."
    fi
}

menu() {
    set -- \
        "Install"             "Install XX-WM (deps, build, service)" \
        "Update"              "Pull latest and reinstall" \
        "Install phone apps"  "Browser, calculator, Tailscale, Skippy, Waydroid..."
    if gdm_detected; then
        set -- "$@" \
            "GDM Colemak OSK"    "Colemak on the GDM login screen (overwrites stock US)"
    fi
    set -- "$@" \
        "Reboot"              "Reboot the system" \
        "Exit"                "Exit the installer"
    whiptail --backtitle "GitHub.com/PiercingXX" --title "XX-WM" \
        --menu "Run options in order:" 0 0 0 "$@" 3>&1 1>&2 2>&3
}

while true; do
    clear
    choice=$(menu) || exit 0
    case $choice in
        "Install")            do_install ;;
        "Update")             do_update ;;
        "Install phone apps") sh "$REPO_DIR/scripts/apps.sh" "$PKG" "$SUDO" || true ;;
        "GDM Colemak OSK")    install_gdm_osk ;;
        "Reboot")             $SUDO reboot ;;
        "Exit")               exit 0 ;;
        *)                    exit 0 ;;
    esac
done
