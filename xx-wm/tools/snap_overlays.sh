#!/bin/sh
# Render the shell overlay surfaces (recents, shade, call UI, HUD, power
# menu, banner, back arrow) for every theme.
#   xx-wm/tools/snap_overlays.sh OUTDIR [theme ...]
# Sibling of snap_shell.sh (part 1): same seeded temp HOME, first boot
# done, same hermetic Broadway rig. Full-screen surfaces must be exactly
# 360x720 with zero `snap: WARNING`s or the run fails; the HUD/banner/back
# arrow are small overlay windows captured at their natural size (their
# expected sizes are asserted instead). One labeled sheet per surface.
set -u
REPO=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_overlays.sh OUTDIR [theme ...]}
shift || true
if [ "$#" -gt 0 ]; then
    THEMES="$*"
else
    THEMES="amoled paper forest aura burgundy"
fi
mkdir -p "$OUTDIR"
WORK=$(mktemp -d)
if [ "${KEEP_WORK:-0}" = 1 ]; then
    trap 'echo "work kept: $WORK"' EXIT INT TERM
else
    trap 'rm -rf "$WORK"' EXIT INT TERM
fi
export PATH="$HOME/.local/bin:$PATH"

APPS="Phone:phone Messages:messages Contacts:contacts Calendar:calendar Camera:camera Maps:maps Notes:notes Clocks:clocks Files:files Settings:settings Weather:weather TxxT:txxt"

seed_home() {
    _home=$1 _theme=$2
    mkdir -p "$_home/.config/xx-wm" "$_home/.local/share/applications" "$_home/.cache"
    for pair in $APPS; do
        _name=${pair%%:*} _id=${pair##*:}
        printf '[Desktop Entry]\nName=%s\nExec=true\nType=Application\n' "$_name" \
            > "$_home/.local/share/applications/${_id}.desktop"
    done
    cat > "$_home/.config/xx-wm/config.json" <<EOF
{"theme": "$_theme",
 "pin_hash": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
 "home_slots": [
  {"type": "app", "label": "Notes", "app_id": "notes.desktop", "cmd": null, "folder": null},
  {"type": "app", "label": "Calendar", "app_id": "calendar.desktop", "cmd": null, "folder": null},
  {"type": "app", "label": "Weather", "app_id": "weather.desktop", "cmd": null, "folder": null}
]}
EOF
}

nav() {
    _name=$1
    cat > "$WORK/nav-$_name.py"
}

SIZE360="win.unfullscreen()
win.set_default_size(360, 720)
win.set_size_request(360, 720)"

nav recents-empty <<EOF
$SIZE360
win._show_switcher()
sw = win._switcher
sw.unfullscreen()
sw.set_default_size(360, 720)
sw.set_size_request(360, 720)
win.set_visible(False)
EOF

nav recents-cards <<EOF
$SIZE360
win._show_switcher()
from gi.repository import GLib
from app_switcher import AppInfo
sw = win._switcher
sw.unfullscreen()
sw.set_default_size(360, 720)
sw.set_size_request(360, 720)
# Headless only: seed after the shell's own +50/+200 ms resyncs, which
# would otherwise wipe the staged cards before capture.
GLib.timeout_add(500, lambda: (sw.refresh(
    [AppInfo('txxt.desktop', 'unread thread'),
     AppInfo('messages.desktop', '')]), False)[1])
win.set_visible(False)
EOF

nav shade <<EOF
from gi.repository import Gtk
$SIZE360
# Headless only: freeze the slide BEFORE it starts (setting the
# transition type mid-flight can strand the panel off-screen and the
# slide never completes under the snapshotter anyway).
sh = win._ensure_shade()
sh._revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
win._show_shade()
from media_controls import MediaCard, PlayerSnapshot
sh.unfullscreen()
sh.set_default_size(360, 720)
sh.set_size_request(360, 720)
sh.add_notification(21, 'Signal', 'Dinner at 7?', 'Running late, order without me')
sh.add_notification(22, 'Bank', 'Card charged', 'Coffee Bar \$4.20')
qa = sh.quick_actions
qa._apply_tile_state('wifi', True, 'HomeNet')
qa._apply_tile_state('bt', True, 'Pixel 8')
sh.media_card._on_snapshot(PlayerSnapshot(
    bus_name='org.mpris.MediaPlayer2.vlc', identity='VLC',
    desktop_entry='vlc', status='Playing', title='Night Owl',
    artist='Galimatias', album='', can_go_next=True,
    can_go_previous=True, can_play=True, can_pause=True,
    length_us=240000000, position_us=90000000))
win.set_visible(False)
EOF

nav call-incoming <<EOF
from gi.repository import Gtk
from call_ui import CallUI
ui = CallUI(config=win.config)
ui.unfullscreen()
ui.set_default_size(360, 720)
ui.set_size_request(360, 720)
# Headless only: the crossfade never completes under the snapshotter.
ui._stack.set_transition_type(Gtk.StackTransitionType.NONE)
ui.show_incoming('Ridgeline Dental', '+1 (614) 555-0177')
win.set_visible(False)
EOF

nav call-active <<EOF
import time
from gi.repository import Gtk
from call_ui import CallUI
ui = CallUI(config=win.config)
ui.unfullscreen()
ui.set_default_size(360, 720)
ui.set_size_request(360, 720)
# Headless only: the crossfade never completes under the snapshotter.
ui._stack.set_transition_type(Gtk.StackTransitionType.NONE)
ui.show_active('Mara Voss', '+1 (614) 555-0199')
# Headless only: no modem snapshot ticks the timer, so stage the
# reference 02:41 readout directly.
ui._act_timer.set_text('2:41')
ui._speaker_btn.set_active(True)
win.set_visible(False)
EOF

nav power <<EOF
$SIZE360
win._show_power_menu()
pm = win._power_menu
pm.unfullscreen()
pm.set_default_size(360, 720)
pm.set_size_request(360, 720)
win.set_visible(False)
EOF

# Small overlay windows: shown just before capture (they auto-hide) and
# captured at natural size (NATURAL_SHOTS below).
nav hud-volume <<EOF
from gi.repository import GLib
GLib.timeout_add(800, lambda: (app._hud.show_volume(68), False)[1])
win.set_visible(False)
EOF

nav hud-brightness <<EOF
from gi.repository import GLib
GLib.timeout_add(800, lambda: (app._hud.show_brightness(82), False)[1])
win.set_visible(False)
EOF

nav banner <<EOF
from gi.repository import Gtk
b = app._ensure_banner()
# Headless only: the slide-down never completes under the snapshotter.
b._revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
b.show_notification(7, 'Signal', 'Dinner at 7?', 'Running late, order without me')
win.set_visible(False)
EOF

nav back-arrow <<EOF
from gi.repository import GLib
GLib.timeout_add(800, lambda: (win._back_layer.flash_back(True), False)[1])
win.set_visible(False)
EOF

# shot -> minimum expected size (WxH). Full-screen surfaces must be exactly
# 360x720; overlay pills just need a sane non-empty window.
SHOTS="recents-empty:360x720 recents-cards:360x720 shade:360x720 call-incoming:360x720 call-active:360x720 power:360x720 hud-volume:100x40 hud-brightness:100x40 banner:200x60 back-arrow:40x40"
RECENTS_SLOW="recents-empty recents-cards"

run_one() {
    # $1 = home, $2 = out png, $3 = nav file, $4 = expected WxH,
    # $5 = snap delay (default 1.0; recents need 2.0: their idle resyncs
    # keep the first frames tearing).
    _home=$1 _out=$2 _nav=$3 _want=$4 _delay=${5:-1.0}
    _err="$WORK/snap.err"
    _try=0
    while [ "$_try" -lt 5 ]; do
        _try=$((_try + 1))
        HOME="$_home" XDG_CONFIG_HOME="$_home/.config" \
            XDG_DATA_HOME="$_home/.local/share" \
            XDG_CACHE_HOME="$_home/.cache" \
            XDG_DATA_DIRS="$_home/.local/share" \
            python3 "$REPO/scripts/snap.py" --out "$_out" --delay "$_delay" \
            --path "$REPO/common" --path "$REPO/xx-wm/launcher/src" \
            --script "$_nav" -- "$REPO/xx-wm/launcher/src/main.py" 2>"$_err" \
            && break
        echo "snap retry $_try: $_out"
        sleep 1
    done
    if [ ! -f "$_out" ]; then
        echo "snap failed: $_out"
        tail -n 20 "$_err"
        return 1
    fi
    _ww=${_want%x*} _hh=${_want#*x}
    if [ "$_ww" = 360 ] && [ "$_hh" = 720 ]; then
        if grep -q 'snap: WARNING' "$_err"; then
            if [ "${ALLOW_WARN:-0}" = 1 ]; then
                echo "note (tolerated): $_out: $(grep 'snap: WARNING' "$_err" | head -n 1)"
            else
                echo "snap size mismatch: $_out"
                grep 'snap: WARNING' "$_err"
                return 1
            fi
        fi
    else
        _got=$(grep -o 'window is [0-9]*x[0-9]*' "$_err" | head -n 1 \
            | sed 's/window is //')
        if [ -n "$_got" ]; then
            _gw=${_got%x*} _gh=${_got#*x}
            if [ "$_gw" -lt "$_ww" ] || [ "$_gh" -lt "$_hh" ]; then
                echo "snap too small ($_got < $_want): $_out"
                return 1
            fi
            echo "note: $_out natural size $_got"
        fi
    fi
}

for theme in $THEMES; do
    echo "=== $theme"
    SHOME="$WORK/home-$theme"
    seed_home "$SHOME" "$theme"
    for spec in $SHOTS; do
        shot=${spec%%:*} want=${spec##*:}
        case " $RECENTS_SLOW " in
            *" $shot "*) _shot_delay=2.0 ;;
            *) _shot_delay=1.0 ;;
        esac
        run_one "$SHOME" "$WORK/$theme-$shot.png" "$WORK/nav-$shot.py" "$want" "$_shot_delay" || exit 1
    done
    cp "$WORK/$theme"-*.png "$OUTDIR/"
done

for spec in $SHOTS; do
    shot=${spec%%:*}
    # shellcheck disable=SC2086
    montage $(
        for theme in $THEMES; do echo "-label $theme $WORK/$theme-$shot.png"; done
    ) -tile x1 -geometry +4+4 -background black \
        -fill white -pointsize 20 "$OUTDIR/sheet-$shot.png" \
        || { echo "montage failed: $shot"; exit 1; }
    echo "wrote $OUTDIR/sheet-$shot.png"
done
