#!/bin/sh
# Render every xx-captions screen in every theme and montage one labeled
# contact sheet per theme into OUTDIR. Exits non-zero on any snap WARNING
# (a window that does not fit the 360x720 phone canvas).
#
# Usage: snap_all.sh OUTDIR
set -u

OUTDIR=${1:?usage: snap_all.sh OUTDIR}
HERE=$(cd "$(dirname "$0")" && pwd)
APP=$(cd "$HERE/.." && pwd)
ROOT=$(cd "$APP/.." && pwd)
SNAP="$ROOT/scripts/snap.py"
SEED="$HERE/demo_seed.py"
TOOLS="$HERE"

mkdir -p "$OUTDIR"
fail=0

snap() {
    # snap <home> <out> [--script nav] -- <app args...>
    # (prefixed names: plain sh has no locals, and $home/$out must survive)
    snap_home=$1; snap_out=$2; snap_script=$3; shift 3
    snap_attempt=0
    while [ "$snap_attempt" -lt 3 ]; do
        snap_attempt=$((snap_attempt + 1))
        err=$(HOME="$snap_home" XDG_CONFIG_HOME="$snap_home/.config" \
            XX_CAPTIONS_LAYER_PRELOADED=1 \
            python3 "$SNAP" --out "$snap_out" \
            --path "$APP/src" --path "$ROOT/common" \
            ${snap_script:+--script "$TOOLS/$snap_script"} -- "$@" 2>&1)
        [ -f "$snap_out" ] && break
        sleep 2
    done
    sleep 1
    echo "$err" | grep -v "Gdk-WARNING\|CRITICAL" | tail -3
    if echo "$err" | grep -q "snap: WARNING"; then
        echo "FAIL: $snap_out has a snap WARNING" >&2
        fail=1
    fi
    [ -f "$snap_out" ] || { echo "FAIL: $snap_out missing" >&2; fail=1; }
}

for theme in amoled paper forest aura; do
    echo "=== $theme"
    work=$(mktemp -d)
    home="$work/home"; bare="$work/bare"
    mkdir -p "$home" "$bare/.config/xx-wm"
    printf '{"theme": "%s"}\n' "$theme" > "$bare/.config/xx-wm/config.json"
    python3 "$SEED" "$home" --theme "$theme" >/dev/null
    dir="$OUTDIR/$theme"
    mkdir -p "$dir"

    snap "$home" "$dir/overlay.png" nav_overlay.py -m xx_captions --overlay
    snap "$bare" "$dir/overlay-firstrun.png" "" -m xx_captions --overlay
    snap "$home" "$dir/settings.png" "" -m xx_captions --settings
    snap "$home" "$dir/detail.png" nav_detail.py -m xx_captions --settings
    snap "$bare" "$dir/settings-bare.png" "" -m xx_captions --settings

    if [ -f "$dir/overlay.png" ] && [ -f "$dir/overlay-firstrun.png" ] \
        && [ -f "$dir/settings.png" ] && [ -f "$dir/detail.png" ] \
        && [ -f "$dir/settings-bare.png" ]; then
        montage "$dir/overlay.png" "$dir/overlay-firstrun.png" \
            "$dir/settings.png" "$dir/detail.png" \
            "$dir/settings-bare.png" \
            -label 'overlay · captions' -label 'overlay · first run' \
            -label 'settings' -label 'transcript detail' \
            -label 'settings · first run' \
            -geometry +8+8 -tile 5x1 -background '#222222' -fill white \
            "$OUTDIR/sheet-$theme.png"
        echo "wrote $OUTDIR/sheet-$theme.png"
    else
        echo "FAIL: $theme sheet incomplete, skipping montage" >&2
        fail=1
    fi
    rm -rf "$work"
done

exit $fail
