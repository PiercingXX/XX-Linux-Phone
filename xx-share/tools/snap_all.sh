#!/bin/sh
# Render every xx-share screen in every theme and montage one labeled
# contact sheet per theme:
#   xx-share/tools/snap_all.sh OUTDIR
# Exits non-zero on any snap WARNING (phone-fit violation) or snap
# failure. Python: $PYTHON, else ./.venv, else xx-wm/.venv, else
# python3 (mirrors scripts/check-all.sh).
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
pick_python() {
    for py in "${PYTHON:-}" "$ROOT/.venv/bin/python" "$ROOT/xx-wm/.venv/bin/python"; do
        [ -n "$py" ] && [ -x "$py" ] && { echo "$py"; return; }
    done
    echo python3
}
PY=$(pick_python)
sweep_stale() {
    "$PY" - <<'EOF'
import os, socket
runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
try:
    names = os.listdir(runtime)
except OSError:
    names = []
for name in names:
    if not (name.startswith("broadway") and name.endswith(".socket")):
        continue
    path = os.path.join(runtime, name)
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(path)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass
    finally:
        probe.close()
EOF
}
THEMES="amoled paper forest aura"
SCREENS="share history favorites settings receive sending"
WORK=$(mktemp -d)
echo "snap_all: work $WORK" >&2
failed=""
for theme in $THEMES; do
    HOME_DIR="$WORK/home-$theme"
    "$PY" "$ROOT/xx-share/tools/demo_seed.py" "$HOME_DIR" --theme "$theme" \
        || { echo "snap_all: seed failed for $theme"; exit 1; }
    for screen in $SCREENS; do
        err="$WORK/snap-$theme-$screen.err"
        snap_once() {
            sweep_stale
            HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" \
                SCREEN="$screen" \
                "$PY" "$ROOT/scripts/snap.py" --out "$WORK/$theme-$screen.png" \
                --delay 1.5 --path "$ROOT/xx-share/src" --path "$ROOT/common" \
                --script "$ROOT/xx-share/tools/snap_screen.py" \
                -- "$ROOT/xx-share/src/main.py" \
                --send "$HOME_DIR/demo-files/IMG_20240613_091522.jpg" \
                       "$HOME_DIR/demo-files/concert-clip.mp4" \
                2>"$err"
            rc=$?
            echo "snap_all: snap_once rc=$rc: $theme $screen" >&2
            return $rc
        }
        ok=""
        attempt=0
        while [ "$attempt" -lt 3 ] && [ -z "$ok" ]; do
            attempt=$((attempt + 1))
            echo "snap_all: try $attempt: $theme $screen" >&2
            # NOTE: snap.py can exit 0 with no window when the Broadway
            # daemon is still starting (activation raises, app.run quits
            # silently), so a zero-size/missing PNG retries like a failure.
            if snap_once && [ -s "$WORK/$theme-$screen.png" ]; then
                ok=1
            else
                echo "snap_all: try $attempt rc=$?: $theme $screen" >&2
            fi
            sleep 2
        done
        if [ -z "$ok" ]; then
            echo "snap_all: snap failed: $theme $screen"
            tail -n 5 "$err"
            failed="$failed $theme/$screen(rc)"
            continue
        fi
        if grep -q 'snap: WARNING' "$err"; then
            echo "snap_all: $theme $screen does not fit 360x720"
            grep 'snap: WARNING' "$err"
            failed="$failed $theme/$screen(size)"
        fi
        if [ ! -s "$WORK/$theme-$screen.png" ]; then
            echo "snap_all: missing output: $theme $screen"
            tail -n 5 "$err"
            failed="$failed $theme/$screen(missing)"
        fi
    done
    args=""
    for screen in $SCREENS; do
        args="$args -label $screen $WORK/$theme-$screen.png"
    done
    # shellcheck disable=SC2086
    montage $args -tile 3x2 -geometry +4+4 -background black \
        -fill white -pointsize 24 "$OUTDIR/sheet-$theme.png" \
        || { echo "snap_all: montage failed for $theme"; exit 1; }
done
if [ -n "$failed" ]; then
    echo "snap_all FAILED:$failed (work kept in $WORK)"
    exit 1
fi
rm -rf "$WORK"
echo "wrote $OUTDIR/sheet-{amoled,paper,forest,aura}.png"
