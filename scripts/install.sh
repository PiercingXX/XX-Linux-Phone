#!/bin/sh
# XX-WM installer — whiptail TUI, cached sudo, network check up front.
# POSIX sh; runs on postmarketOS/Alpine (apk), Debian/Mobian (apt), and Arch (pacman).
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
elif command -v pacman >/dev/null 2>&1; then
    PKG=pacman
else
    echo "Unsupported distro: need apk, apt, or pacman." >&2
    exit 1
fi

pkg_install() {
    # Never hard-fail the menu on one missing package
    if [ "$PKG" = apk ]; then
        $SUDO apk add "$@" || echo "warn: some packages failed: $*" >&2
    elif [ "$PKG" = pacman ]; then
        $SUDO pacman -S --needed --noconfirm "$@" || echo "warn: some packages failed: $*" >&2
    else
        $SUDO apt install -y "$@" || echo "warn: some packages failed: $*" >&2
    fi
}

# --- whiptail ---------------------------------------------------------------
if ! command -v whiptail >/dev/null 2>&1; then
    echo "Installing whiptail..."
    if [ "$PKG" = apk ]; then
        pkg_install newt
    elif [ "$PKG" = pacman ]; then
        pkg_install libnewt
    else
        pkg_install whiptail
    fi
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
        pkg_install py3-pywayland
        pkg_install geoclue
        pkg_install networkmanager
        pkg_install brightnessctl
    elif [ "$PKG" = pacman ]; then
        pkg_install python-gobject gtk4 libadwaita gtk4-layer-shell meson ninja \
            rsync git squeekboard phoc python-pywayland wl-clipboard geoclue \
            networkmanager brightnessctl
        if command -v lisgd >/dev/null 2>&1; then
            echo "lisgd already present: $(command -v lisgd)"
        else
            echo "SKIP: lisgd is not in Arch repos. Build from https://git.sr.ht/~mil/lisgd (libevdev + libinput). This tablet already has /usr/bin/lisgd."
        fi
    else
        pkg_install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
            libgtk4-layer-shell0 meson ninja-build rsync git squeekboard \
            phoc lisgd wl-clipboard
        pkg_install python3-pywayland
        pkg_install geoclue-2.0
        pkg_install network-manager
        pkg_install brightnessctl
    fi
}

install_fonts() {
    # One package per invocation: pacman aborts the whole transaction if any
    # name is unknown. Missing fonts are a warn, never a failed install.
    echo "Installing fonts (optional)..."
    if [ "$PKG" = pacman ]; then
        pkg_install ttf-jetbrains-mono-nerd
        pkg_install ttf-jetbrains-mono
        pkg_install ttf-space-mono-nerd
        pkg_install ttf-space-mono
    elif [ "$PKG" = apk ]; then
        pkg_install nerd-fonts-jetbrains-mono
        pkg_install font-jetbrains-mono
        pkg_install font-space-mono
    else
        pkg_install fonts-jetbrains-mono
        echo "warn: no Space Mono package on apt — continuing" >&2
    fi
    $SUDO fc-cache -f >/dev/null 2>&1 || true
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

    install_fonts
    select_phoc_scale || return 1
    enable_service
    add_input_group
}

# --- per-device phoc.ini scale (20.5) --------------------------------------
# The tablet's panel reports as DSI-1 (wanting scale 1.5) and collides with
# the FP5's DSI-1 (scale 2.5), so a single phoc.ini cannot serve both. Prompt
# for the device and copy its fragment over the meson-installed default.
# Cancelling without a detection must not keep the Fairphone 5 default.
detect_phoc_device() {
    for modes in /sys/class/drm/card*-DSI-1/modes; do
        [ -r "$modes" ] || continue
        if grep -q '1200x1920' "$modes" 2>/dev/null || \
           grep -q '1920x1200' "$modes" 2>/dev/null; then
            echo tablet
            return 0
        fi
        if grep -q '2340x1080' "$modes" 2>/dev/null || \
           grep -q '1080x2340' "$modes" 2>/dev/null; then
            echo fairphone-5
            return 0
        fi
        if grep -q '720x1440' "$modes" 2>/dev/null || \
           grep -q '1440x720' "$modes" 2>/dev/null; then
            echo librem-5
            return 0
        fi
    done
    for modes in /sys/class/drm/card*-HWCOMPOSER-1/modes; do
        [ -r "$modes" ] || continue
        echo furiphone-flx1
        return 0
    done
    return 1
}

_install_phoc_fragment() {
    _dev=$1
    _frag="$REPO_DIR/launcher/data/phoc/${_dev}.ini"
    if [ ! -f "$_frag" ]; then
        _frag="/usr/share/xx-wm/phoc/${_dev}.ini"
    fi
    if [ ! -f "$_frag" ]; then
        echo "warn: no phoc.ini fragment for '$_dev' — keeping default" >&2
        return 1
    fi
    $SUDO cp "$_frag" /usr/share/xx-wm/phoc.ini || {
        echo "warn: could not install phoc.ini fragment for '$_dev'" >&2
        return 1
    }
}

select_phoc_scale() {
    detected=$(detect_phoc_device) || true

    if [ ! -t 0 ]; then
        if [ -n "$detected" ]; then
            echo "using detected device: $detected"
            _install_phoc_fragment "$detected"
            return
        fi
        echo "A device is required so phoc.ini scale is not the Fairphone 5 default (need a TTY or DRM sysfs)." >&2
        return 1
    fi

    warned=0
    while :; do
        dev=$(whiptail --backtitle "GitHub.com/PiercingXX" --title "Device" \
            --menu "Which device is this? (sets phoc.ini scale)" 0 0 0 \
            "fairphone-5"    "FP5 — DSI-1, scale 2.5" \
            "furiphone-flx1" "FLX1 — HWCOMPOSER-1, scale 3" \
            "librem-5"       "Librem 5 — DSI-1, scale 2" \
            "tablet"         "x86 tablet — DSI-1, scale 1.5" \
            3>&1 1>&2 2>&3) || dev=
        if [ -n "$dev" ]; then
            _install_phoc_fragment "$dev"
            return
        fi
        if [ -n "$detected" ]; then
            echo "using detected device: $detected"
            _install_phoc_fragment "$detected"
            return
        fi
        if [ "$warned" -eq 0 ]; then
            msg_box "A device is required so phoc.ini scale is not the Fairphone 5 default."
            warned=1
        fi
    done
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
        if [ -f /usr/share/wayland-sessions/xx-wm.desktop ]; then
            # Idempotent: a previous install.sh always-enabled the unit.
            # --now stops a running duplicate if we are somehow inside a session.
            systemctl --user disable --now xx-wm 2>/dev/null || true
            echo "wayland-session installed; systemd --user xx-wm disabled (would double-start under GDM)."
            echo "Opt-in only from a session that is NOT already xx-wm-session:"
            echo "  systemctl --user start xx-wm"
            echo "Do not 'enable --now' under GDM: WantedBy=graphical-session.target would start a second Python shell."
        else
            systemctl --user enable xx-wm 2>/dev/null || \
                echo "warn: enable the service after first login: systemctl --user enable xx-wm" >&2
        fi
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
        msg_box "XX-WM installed. Select the XX-WM session at next login (not PiercingXX), or reboot."
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
