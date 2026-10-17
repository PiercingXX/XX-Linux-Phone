#!/bin/sh
# Render every xx-email screen in every theme and montage one labeled
# contact sheet per theme into OUTDIR.
#
#   xx-email/tools/snap_all.sh OUTDIR
#
# BEFORE=1 renders only the screens the pre-polish code can show
# (amoled still loops all four themes; pick per invocation).
# THEMES="amoled" BEFORE=1 .../snap_all.sh lane_shots/before
#
# Exits non-zero on any snap WARNING (window not exactly 360x720).
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES:-"amoled paper forest aura"}
if [ "${BEFORE:-0}" = "1" ]; then
    SCREENS="setup inbox thread compose search"
else
    SCREENS="setup inbox thread compose folders search settings"
fi
mkdir -p "$OUTDIR"
PY=/media/Working-Storage/GitHub/Phone-Projects/linux/xx-wm/.venv/bin/python
[ -x "$PY" ] || PY=python3
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
failed=""

for theme in $THEMES; do
    for screen in $SCREENS; do
        hd="$WORK/home-$theme-$screen"
        mkdir -p "$hd/run"
        if [ "$screen" = "setup" ]; then
            "$PY" "$ROOT/xx-email/tools/demo_seed.py" "$hd" --empty \
                >/dev/null || failed="$failed seed-$theme-$screen"
        else
            "$PY" "$ROOT/xx-email/tools/demo_seed.py" "$hd" \
                >/dev/null || failed="$failed seed-$theme-$screen"
        fi
        "$PY" -c "
import json
path = '$hd/.config/xx-wm/config.json'
with open(path, encoding='utf-8') as fh:
    data = json.load(fh)
data['theme'] = '$theme'
with open(path, 'w', encoding='utf-8') as fh:
    json.dump(data, fh)
"
        err="$WORK/snap-$theme-$screen.err"
        # Success is a fresh PNG on disk: the runner can exit 0
        # while the app died at Gtk init (broadway boot race), so
        # retry on a missing shot, not on the exit status.
        rm -f "$WORK/$theme-$screen.png"
        shot_ok=0
        for attempt in 1 2 3 4 5; do
            HOME="$hd" XDG_CONFIG_HOME="$hd/.config" \
                XDG_DATA_HOME="$hd/.local/share" \
                XDG_CACHE_HOME="$hd/.cache" \
                XDG_STATE_HOME="$hd/.local/state" \
                XDG_RUNTIME_DIR="$hd/run" \
                "$PY" "$ROOT/scripts/snap.py" \
                --out "$WORK/$theme-$screen.png" --delay 4.0 \
                --path "$ROOT/common" --path "$ROOT/xx-email/src" \
                --script "$ROOT/xx-email/tools/snap_nav/$screen.py" \
                -- -m xx_email 2>"$err" || true
            if [ -s "$WORK/$theme-$screen.png" ]; then
                shot_ok=1
                break
            fi
            echo "snap_all: retrying $theme/$screen" \
                "attempt $attempt"
            sleep 2
        done
        [ "$shot_ok" = "1" ] || failed="$failed snap-$theme-$screen"
        if grep -q 'snap: WARNING' "$err" 2>/dev/null; then
            echo "snap_all: $theme/$screen does not fit 360x720"
            cat "$err"
            failed="$failed size-$theme-$screen"
        fi
    done
done

for theme in $THEMES; do
    args=""
    for screen in $SCREENS; do
        args="$args -label $screen $WORK/$theme-$screen.png"
    done
    # shellcheck disable=SC2086
    montage $args -tile 4x2 -geometry +4+4 -background black \
        -fill white -pointsize 24 "$OUTDIR/xx-email-$theme.png" \
        || failed="$failed montage-$theme"
    echo "wrote $OUTDIR/xx-email-$theme.png"
done

if [ -n "$failed" ]; then
    echo "FAILED:$failed"
    exit 1
fi
