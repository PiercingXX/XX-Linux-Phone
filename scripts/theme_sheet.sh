#!/bin/sh
# Render the gallery for every theme and montage one column per theme:
#   scripts/theme_sheet.sh OUTDIR
# Writes OUTDIR/theme-sheet-pageN.png (N = 1..3), labeled per theme.
# HOME/XDG_CONFIG_HOME point at temp dirs so the real config is untouched.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUTDIR=${1:?usage: theme_sheet.sh OUTDIR}
mkdir -p "$OUTDIR"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

THEMES=$("$PY" -c "import sys; sys.path.insert(0, '$ROOT/common'); from xx_ui import preset_keys; print(' '.join(preset_keys()))")
WORK=$(mktemp -d)

for theme in $THEMES custom; do
    HOME_DIR="$WORK/home-$theme"
    mkdir -p "$HOME_DIR/.config/xx-wm"
    if [ "$theme" = custom ]; then
        printf '{"theme": "custom", "custom_background": "#3a2a10"}' \
            > "$HOME_DIR/.config/xx-wm/config.json"
    else
        printf '{"theme": "%s"}' "$theme" > "$HOME_DIR/.config/xx-wm/config.json"
    fi
    for page in 1 2 3; do
        err="$WORK/snap-$theme-p$page.err"
        HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" \
            XX_GALLERY_PAGE="$page" \
            "$PY" "$ROOT/scripts/snap.py" --out "$WORK/$theme-p$page.png" \
            --delay 0.8 --path "$ROOT/common" -- "$ROOT/common/tools/gallery.py" \
            2>"$err" \
            || { echo "snap failed: $theme page $page"; cat "$err"; exit 1; }
        if grep -q 'snap: WARNING' "$err"; then
            echo "theme_sheet: $theme page $page does not fit 360x720"
            cat "$err"
            exit 1
        fi
    done
done

for page in 1 2 3; do
    args=""
    for theme in $THEMES custom; do
        args="$args -label $theme $WORK/$theme-p$page.png"
    done
    # shellcheck disable=SC2086
    montage $args -tile 9x1 -geometry +4+4 -background black \
        -fill white -pointsize 30 "$OUTDIR/theme-sheet-page$page.png" \
        || { echo "montage failed: page $page"; exit 1; }
done
rm -rf "$WORK"
echo "wrote $OUTDIR/theme-sheet-page{1,2,3}.png"
