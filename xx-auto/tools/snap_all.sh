#!/bin/sh
# Seeded screenshots of every xx-auto screen in Ink (amoled) + paper,
# portrait 360x720 and landscape 720x360:
#   xx-auto/tools/snap_all.sh OUTDIR
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-auto"
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python3

THEMES="amoled paper"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

snap_one() {
    # $1=theme $2=name $3=size $4=scenario $5=sheet
    theme=$1 name=$2 size=$3 scenario=$4 sheet=$5
    home="$WORK/home-$theme"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    land=""
    [ "$size" = "720x360" ] && land=1
    nav=""
    if [ "$sheet" != "drive" ]; then
        nav="$WORK/nav-$theme-$name.py"
        printf 'app._auto_window.open_sheet(%s)\n' "'$sheet'" > "$nav"
    fi
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        if HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XX_AUTO_SCENARIO="$scenario" XX_AUTO_SHEET="$sheet" \
            XX_AUTO_LANDSCAPE="$land" \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "$size" --delay 1.2 \
            --path "$ROOT/src" --path "$LANE/common" \
            ${nav:+--script "$nav"} \
            -- -m xx_auto 2>"$err"; then
            if grep -q 'snap: WARNING' "$err"; then
                cat "$err" >&2
                fail "$theme $name: phone-fit breach"
            fi
            if [ -s "$out" ]; then
                echo "shot $theme $name $size"
                return 0
            fi
        fi
        sleep 1
    done
    fail "snap failed: $theme $name ($(tail -n 3 "$err" 2>/dev/null))"
}

for theme in $THEMES; do
    home="$WORK/home-$theme"
    mkdir -p "$home/.config/xx-wm" "$home/.config/xx-auto"
    printf '{"theme": "%s"}' "$theme" > "$home/.config/xx-wm/config.json"
    # amoled proves always_ink forcing; paper proves live suite themes
    # with always_ink off.
    if [ "$theme" = "amoled" ]; then
        printf '{"always_ink": true}' > "$home/.config/xx-auto/settings.json"
    else
        printf '{"always_ink": false}' > "$home/.config/xx-auto/settings.json"
    fi
done

states="nothing_installed,nothing_installed,drive
nothing_playing,nothing_playing,drive
playing,audiobook,drive
radio,radio,drive
quick_pick,players,players
favourites,favourites,favourites
settings,settings,settings
licenses,settings,licenses"

for theme in $THEMES; do
    for entry in $states; do
        name=${entry%%,*}; rest=${entry#*,}
        scenario=${rest%%,*}; sheet=${rest##*,}
        snap_one "$theme" "portrait-$name" "360x720" "$scenario" "$sheet"
    done
    for entry in $states; do
        name=${entry%%,*}; rest=${entry#*,}
        scenario=${rest%%,*}; sheet=${rest##*,}
        snap_one "$theme" "landscape-$name" "720x360" "$scenario" "$sheet"
    done
done

for theme in $THEMES; do
    # shellcheck disable=SC2086
    montage -label 'nothing-installed' "$WORK/$theme-portrait-nothing_installed.png" \
        -label 'nothing-playing' "$WORK/$theme-portrait-nothing_playing.png" \
        -label 'playing' "$WORK/$theme-portrait-playing.png" \
        -label 'radio' "$WORK/$theme-portrait-radio.png" \
        -label 'quick-pick' "$WORK/$theme-portrait-quick_pick.png" \
        -label 'favourites' "$WORK/$theme-portrait-favourites.png" \
        -label 'settings' "$WORK/$theme-portrait-settings.png" \
        -label 'licenses' "$WORK/$theme-portrait-licenses.png" \
        -tile 4x2 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme-portrait.png" \
        || fail "montage failed for $theme portrait"
    montage -label 'nothing-installed' "$WORK/$theme-landscape-nothing_installed.png" \
        -label 'nothing-playing' "$WORK/$theme-landscape-nothing_playing.png" \
        -label 'playing' "$WORK/$theme-landscape-playing.png" \
        -label 'radio' "$WORK/$theme-landscape-radio.png" \
        -label 'quick-pick' "$WORK/$theme-landscape-quick_pick.png" \
        -label 'favourites' "$WORK/$theme-landscape-favourites.png" \
        -label 'settings' "$WORK/$theme-landscape-settings.png" \
        -label 'licenses' "$WORK/$theme-landscape-licenses.png" \
        -tile 4x2 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme-landscape.png" \
        || fail "montage failed for $theme landscape"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}-{portrait,landscape}.png"
