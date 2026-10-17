#!/bin/sh
# Render every TxxT screen in four themes and montage one labeled contact
# sheet per theme into OUTDIR. Exits non-zero on any snap WARNING
# (a 360x720 phone-screen misfit) or any failed render.
set -u

OUTDIR=${1:?usage: snap_all.sh OUTDIR}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
LANE_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
TX="$LANE_ROOT/txxt"
export PYTHONPATH="$TX/src$([ -n "${PYTHONPATH:-}" ] && printf ':%s' "$PYTHONPATH")"

THEMES="amoled paper forest aura"
mkdir -p "$OUTDIR"
failed=""

for theme in $THEMES; do
    SEED_HOME=$(mktemp -d "${TMPDIR:-/tmp}/txxt-seed-XXXXXX")
    NAV=$(mktemp -d "${TMPDIR:-/tmp}/txxt-nav-XXXXXX")
    export SEED_HOME
    python3 "$TX/tools/demo_seed.py" "$SEED_HOME" >/dev/null || {
        echo "seed failed for $theme" >&2; failed="$failed $theme-seed"; continue; }
    mkdir -p "$SEED_HOME/.config/xx-wm"
    printf '{"theme": "%s"}' "$theme" > "$SEED_HOME/.config/xx-wm/config.json"
    export HOME="$SEED_HOME" \
        XDG_DATA_HOME="$SEED_HOME/.local/share" \
        XDG_CONFIG_HOME="$SEED_HOME/.config" \
        XDG_CACHE_HOME="$SEED_HOME/.cache"

    cat > "$NAV/list.py" <<'EOF'
EOF
    cat > "$NAV/thread.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['mara'])
EOF
    cat > "$NAV/mms.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['juniper'])
EOF
    cat > "$NAV/search.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['mara'])
page = win._thread_pages[-1]
page._search_bar.set_search_mode(True)
page._search_entry.set_text('the')
EOF
    cat > "$NAV/new.py" <<'EOF'
win.open_new_conversation()
EOF
    cat > "$NAV/settings.py" <<'EOF'
win.open_settings()
EOF
    cat > "$NAV/blocking.py" <<'EOF'
win.open_blocking()
EOF
    cat > "$NAV/quarantine.py" <<'EOF'
win.open_quarantine()
EOF
    cat > "$NAV/rowmenu.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win._list_page._show_row_menu(_ids['mara'])
EOF
    cat > "$NAV/overflow.py" <<'EOF'
win._list_page._on_overflow_clicked()
EOF
    cat > "$NAV/attach.py" <<'EOF'
import json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['mara'])
win._thread_pages[-1]._on_attach_clicked()
EOF
    cat > "$NAV/staged.py" <<'EOF'
import glob, json, os
_ids = json.load(open(os.path.join(os.environ['SEED_HOME'], 'ids.json')))
win.open_thread(_ids['juniper'])
src = sorted(glob.glob(os.path.join(
    os.environ['SEED_HOME'], '.local', 'share', 'txxt', 'media', '*')))[0]
win._thread_pages[-1]._stage_photo(src)
EOF

    shots=""
    snap() {
        name=$1; script=$2
        out="$NAV/shot-$name.png"
        # The headless rig is racy starting broadwayd back-to-back, so a
        # hard failure (no PNG at all) retries; a WARNING means the
        # content truly overflowed and fails the gate immediately.
        attempt=0
        while [ $attempt -lt 3 ]; do
            attempt=$((attempt + 1))
            rm -f "$out"
            if log=$(python3 "$LANE_ROOT/scripts/snap.py" --out "$out" \
                    --delay 2.0 --path "$TX/src" --path "$LANE_ROOT/common" \
                    ${script:+--script "$NAV/$script.py"} -- -m txxt 2>&1) \
                    && [ -f "$out" ]; then
                break
            fi
            echo "snap $name ($theme) attempt $attempt failed; retrying" >&2
            sleep 2
        done
        [ -f "$out" ] || { echo "snap $name ($theme): no PNG after 3 tries" >&2
            failed="$failed $theme/$name"; return; }
        case "$log" in
            *"snap: WARNING"*)
                echo "snap $name ($theme): $log" >&2
                failed="$failed $theme/$name"
                cp "$out" "$OUTDIR/failed-$theme-$name.png" ;;
        esac
        shots="$shots -label $name $out"
    }

    snap list ""
    snap thread thread
    snap mms mms
    snap search search
    snap new new
    snap settings settings
    snap blocking blocking
    snap quarantine quarantine
    snap rowmenu rowmenu
    snap overflow overflow
    snap attach attach
    snap staged staged

    # shellcheck disable=SC2086
    montage $shots -tile 4x3 -geometry 360x720+4+4 -background black \
        -fill white "$OUTDIR/sheet-$theme.png" || failed="$failed $theme-montage"
    rm -rf "$SEED_HOME" "$NAV"
done

if [ -n "$failed" ]; then
    echo "FAILED:$failed" >&2
    exit 1
fi
echo "sheets in $OUTDIR"
