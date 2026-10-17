#!/bin/sh
# Render every xx-calendar screen in four themes and montage one labeled
# contact sheet per theme into OUTDIR. Exits non-zero on any snap WARNING.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LANE=$(cd "$ROOT/.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY="/media/Working-Storage/GitHub/Phone-Projects/linux/xx-wm/.venv/bin/python"
[ -x "$PY" ] || PY=python3
THEMES="amoled paper forest aura"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
SAMPLE_ICS="$WORK/sample.ics"
printf 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//X//EN\r\nBEGIN:VEVENT\r\nUID:sample-1\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20260820T100000Z\r\nDTEND:20260820T110000Z\r\nSUMMARY:Sample import\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n' > "$SAMPLE_ICS"

snap_one() {
    home=$1; name=$2; script=$3
    out="$WORK/$name.png"
    err="$WORK/$name.err"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --delay 1.2 --path "$ROOT/src" \
            --path "$LANE/common" \
            ${script:+--script "$script"} -- -m xx_calendar \
            2>"$err" || true
        if grep -q 'snap: WARNING' "$err"; then
            echo "snap WARNING: $name (attempt $attempt)"
            cat "$err"
            return 1
        fi
        if [ -f "$out" ]; then
            sleep 1
            return 0
        fi
        echo "snap retry: $name (attempt $attempt)"
        sleep 2
    done
    echo "snap failed: $name"
    cat "$err"
    return 1
}

for theme in $THEMES; do
    HOME_DIR="$WORK/home-$theme"
    mkdir -p "$HOME_DIR/.config/xx-wm"
    printf '{"theme": "%s"}' "$theme" > "$HOME_DIR/.config/xx-wm/config.json"
    PYTHONPATH="$ROOT/src" "$PY" "$ROOT/tools/demo_seed.py" "$HOME_DIR" >/dev/null
    EMPTY_HOME="$WORK/empty-$theme"
    mkdir -p "$EMPTY_HOME/.config/xx-wm" "$EMPTY_HOME/.local/share/xx-calendar"
    printf '{"theme": "%s"}' "$theme" > "$EMPTY_HOME/.config/xx-wm/config.json"
    cat > "$WORK/nav-schedule.py" <<'PY'
import datetime
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("schedule")
PY
    cat > "$WORK/nav-month.py" <<'PY'
import datetime
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("month")
PY
    cat > "$WORK/nav-day.py" <<'PY'
import datetime
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("day")
PY
    cat > "$WORK/nav-detail.py" <<'PY'
import datetime
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("schedule")
win.open_detail("dentist-1", 0)
PY
    cat > "$WORK/nav-editor.py" <<'PY'
import datetime
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("schedule")
win.open_editor(day=datetime.date(2026, 8, 15))
PY
    cat > "$WORK/nav-edit.py" <<'PY'
import datetime
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("schedule")
win.open_editor(uid="dentist-1")
PY
    cat > "$WORK/nav-calendars.py" <<'PY'
win.show_view("calendars")
PY
    cat > "$WORK/nav-search.py" <<'PY'
import datetime
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("search")
win._search_entry.set_text("standup")
PY
    cat > "$WORK/nav-settings.py" <<'PY'
win.show_view("settings")
PY
    cat > "$WORK/nav-import-tmpl.py" <<'PY'
import datetime
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("schedule")
win.import_ics_uri("SAMPLE_ICS_PLACEHOLDER")
PY
    sed "s|SAMPLE_ICS_PLACEHOLDER|$SAMPLE_ICS|" "$WORK/nav-import-tmpl.py" > "$WORK/nav-import.py"
    cat > "$WORK/nav-export.py" <<'PY'
win.open_export_dialog()
PY
    cat > "$WORK/nav-empty.py" <<'PY'
win.show_view("schedule")
PY
    failed=""
    snap_one "$HOME_DIR" "$theme-schedule" "$WORK/nav-schedule.py" || failed=1
    snap_one "$HOME_DIR" "$theme-month" "$WORK/nav-month.py" || failed=1
    snap_one "$HOME_DIR" "$theme-day" "$WORK/nav-day.py" || failed=1
    snap_one "$HOME_DIR" "$theme-detail" "$WORK/nav-detail.py" || failed=1
    snap_one "$HOME_DIR" "$theme-editor" "$WORK/nav-editor.py" || failed=1
    snap_one "$HOME_DIR" "$theme-edit" "$WORK/nav-edit.py" || failed=1
    snap_one "$HOME_DIR" "$theme-calendars" "$WORK/nav-calendars.py" || failed=1
    snap_one "$HOME_DIR" "$theme-search" "$WORK/nav-search.py" || failed=1
    snap_one "$HOME_DIR" "$theme-settings" "$WORK/nav-settings.py" || failed=1
    snap_one "$HOME_DIR" "$theme-import" "$WORK/nav-import.py" || failed=1
    snap_one "$HOME_DIR" "$theme-export" "$WORK/nav-export.py" || failed=1
    snap_one "$EMPTY_HOME" "$theme-empty" "$WORK/nav-empty.py" || failed=1
    if [ -n "$failed" ]; then
        echo "snap_all: $theme failed"
        exit 1
    fi
    # shellcheck disable=SC2086
    montage -label 'schedule' "$WORK/$theme-schedule.png" \
        -label 'month' "$WORK/$theme-month.png" \
        -label 'day' "$WORK/$theme-day.png" \
        -label 'detail' "$WORK/$theme-detail.png" \
        -label 'editor-new' "$WORK/$theme-editor.png" \
        -label 'editor-edit' "$WORK/$theme-edit.png" \
        -label 'calendars' "$WORK/$theme-calendars.png" \
        -label 'search' "$WORK/$theme-search.png" \
        -label 'settings' "$WORK/$theme-settings.png" \
        -label 'import' "$WORK/$theme-import.png" \
        -label 'export' "$WORK/$theme-export.png" \
        -label 'empty' "$WORK/$theme-empty.png" \
        -tile 3x4 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/calendar-$theme.png" \
        || { echo "montage failed: $theme"; exit 1; }
    echo "wrote $OUTDIR/calendar-$theme.png"
done
echo "all sheets ok"
