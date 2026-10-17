#!/bin/sh
# Render the in-shell Settings page (every viewport) for every theme.
#   xx-wm/tools/snap_settings.sh OUTDIR [theme ...]
# Sibling of snap_shell.sh (part 1): same seeded temp HOME, first boot done,
# same 360x720 / zero-`snap: WARNING` gate. One labeled sheet per theme.
set -u
REPO=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_settings.sh OUTDIR [theme ...]}
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

APPS="Phone:phone Messages:messages Contacts:contacts Calendar:calendar Camera:camera Maps:maps Notes:notes Clocks:clocks Files:files Settings:settings Weather:weather Vitals:vitals Email:email Share:share Calculator:calculator Photos:photos Music:music Browser:browser Terminal:terminal Software:software"

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

# $1 = shot name, $2 = open_settings arg (empty = top), $3 = bottom (1 = jump to end)
shot_nav() {
    _shot=$1 _section=$2 _bottom=$3
    {
        echo "win.unfullscreen()"
        echo "win.set_default_size(360, 720)"
        echo "win.set_size_request(360, 720)"
        if [ -n "$_section" ]; then
            echo "win.open_settings('$_section')"
        else
            echo "win.open_settings(None)"
        fi
        if [ "$_bottom" = 1 ]; then
            # Deferred past layout like the in-app deep-link retries: the
            # adjustment upper reads 0 until the first frames land.
            echo "from gi.repository import GLib"
            echo "GLib.timeout_add(600, lambda: (win._settings_scroll.get_vadjustment().set_value(win._settings_scroll.get_vadjustment().get_upper()), False)[1])"
        fi
    } > "$WORK/nav-settings-$_shot.py"
}

# Viewports covering the whole page top to bottom.
shot_nav top '' 0
shot_nav wifi 'wifi' 0
shot_nav charging 'charging' 0
shot_nav screentime 'screentime' 0
shot_nav standby 'standby' 0
shot_nav privacy 'permissions' 0
shot_nav smartlock 'smart_lock' 0
shot_nav medical 'medical' 0
shot_nav extras 'recording' 0
shot_nav about 'about' 0
shot_nav bottom '' 1
SHOTS="top wifi charging screentime standby privacy smartlock medical extras about bottom"

run_one() {
    _home=$1 _out=$2 _nav=$3
    _err="$WORK/snap.err"
    _try=0
    while [ "$_try" -lt 3 ]; do
        _try=$((_try + 1))
        HOME="$_home" XDG_CONFIG_HOME="$_home/.config" \
            XDG_DATA_HOME="$_home/.local/share" \
            XDG_CACHE_HOME="$_home/.cache" \
            XDG_DATA_DIRS="$_home/.local/share" \
            python3 "$REPO/scripts/snap.py" --out "$_out" --delay 2.0 \
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
    if grep -q 'snap: WARNING' "$_err"; then
        echo "snap size mismatch: $_out"
        grep 'snap: WARNING' "$_err"
        if [ "${ALLOW_WIDE:-0}" != 1 ]; then
            return 1
        fi
    fi
}

for theme in $THEMES; do
    echo "=== $theme"
    SHOME="$WORK/home-$theme"
    seed_home "$SHOME" "$theme"
    for shot in $SHOTS; do
        run_one "$SHOME" "$WORK/$theme-settings-$shot.png" \
            "$WORK/nav-settings-$shot.py" || exit 1
    done
    # shellcheck disable=SC2086
    montage $(
        for shot in $SHOTS; do echo "-label settings-$shot $WORK/$theme-settings-$shot.png"; done
    ) -tile 4x3 -geometry 360x720+4+4 -background black \
        -fill white -pointsize 20 "$OUTDIR/sheet-$theme-settings.png" \
        || { echo "montage failed: $theme"; exit 1; }
    echo "wrote $OUTDIR/sheet-$theme-settings.png"
    if [ "${KEEP_SHOTS:-0}" = 1 ]; then
        for shot in $SHOTS; do
            cp "$WORK/$theme-settings-$shot.png" "$OUTDIR/$theme-settings-$shot.png"
        done
    fi
done
