#!/bin/sh
# Seeded screenshots of every xx-vitals screen in four themes:
#   xx-vitals/tools/snap_all.sh OUTDIR
# For each theme: seed a temp HOME, pin the theme in xx-wm/config.json,
# snap every screen with scripts/snap.py, montage one labeled contact
# sheet per theme. Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}

THEMES="amoled paper forest aura"
TARGETS="home journal browse detail profile"
WORK=$(mktemp -d)

fail() {
    echo "snap_all: $1" >&2
    rm -rf "$WORK"
    exit 1
}

snap_one() {
    # $1=theme $2=target $3=home $4=snap.py --out path $5=--delay
    # $6=expected artifact $7=extra env assignments, if any
    theme=$1 target=$2 HOME_DIR=$3
    err="$WORK/snap-$theme-$target.err"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        # shellcheck disable=SC2086
        if env HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" \
            SNAP_TARGET="$target" ${7:-} \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$4" --delay "$5" \
            --path "$LANE/xx-vitals/src" --path "$LANE/common" \
            --script "$LANE/xx-vitals/tools/snap_nav.py" \
            -- -m xx_vitals 2>"$err"; then
            if grep -q 'snap: WARNING' "$err"; then
                fail "$theme $target: $(cat "$err")"
            fi
            if [ -s "$6" ]; then
                return 0
            fi
        fi
        sleep 1
    done
    fail "snap failed: $theme $target ($(tail -n 3 "$err"))"
}

for theme in $THEMES; do
    HOME_DIR="$WORK/home-$theme"
    mkdir -p "$HOME_DIR/.config/xx-wm"
    printf '{"theme": "%s"}' "$theme" > "$HOME_DIR/.config/xx-wm/config.json"
    HOME="$HOME_DIR" XDG_CONFIG_HOME="$HOME_DIR/.config" \
        "$PY" "$LANE/xx-vitals/tools/demo_seed.py" "$HOME_DIR" \
        > /dev/null || fail "seed failed for $theme"
    for target in $TARGETS; do
        snap_one "$theme" "$target" "$HOME_DIR" \
            "$WORK/$theme-$target.png" 1.2 "$WORK/$theme-$target.png"
    done
    for target in add-activity add-weight menu; do
        snap_one "$theme" "$target" "$HOME_DIR" \
            "$WORK/$theme-$target-dummy.png" 2.2 "$WORK/$theme-$target.png" \
            "DIALOG_OUT=$WORK/$theme-$target.png"
    done
done

for theme in $THEMES; do
    # shellcheck disable=SC2086
    montage -label home "$WORK/$theme-home.png" \
        -label journal "$WORK/$theme-journal.png" \
        -label browse "$WORK/$theme-browse.png" \
        -label detail "$WORK/$theme-detail.png" \
        -label profile "$WORK/$theme-profile.png" \
        -label add-activity "$WORK/$theme-add-activity.png" \
        -label add-weight "$WORK/$theme-add-weight.png" \
        -label menu "$WORK/$theme-menu.png" \
        -tile 4x2 -geometry +4+4 -background black \
        -fill white -pointsize 30 "$OUTDIR/sheet-$theme.png" \
        || fail "montage failed for $theme"
done
rm -rf "$WORK"
echo "wrote $OUTDIR/sheet-{amoled,paper,forest,aura}.png"
