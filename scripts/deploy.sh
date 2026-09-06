#!/bin/sh
# XX-WM dev-loop deploy: rsync launcher/src/ to /usr/share/xx-wm/ and
# restart the running shell. Usage:
#   PIERCING_DEVICE=192.168.1.129 [XX_WM_USER=dr3k] scripts/deploy.sh [--dry-run]
# GitHub.com/PiercingXX

set -u

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

DEVICE=${PIERCING_DEVICE:-}
USER_NAME=${XX_WM_USER:-dr3k}
DRY_RUN=0

for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "Usage: PIERCING_DEVICE=<host> [XX_WM_USER=dr3k] $0 [--dry-run]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

if [ -z "$DEVICE" ]; then
    echo "PIERCING_DEVICE is required (hostname or IP of the phone)." >&2
    exit 1
fi

TARGET="${USER_NAME}@${DEVICE}"
STAGE=/tmp/xx-wm-deploy
DEST=/usr/share/xx-wm
RSYNC_FLAGS="-az --delete --exclude __pycache__"

# Restart the running shell: user unit only if it is already active (do not
# start a second Python shell under GDM). Otherwise SIGUSR1 the unique
# python child; xx-wm.in respawns it. Gio.Application maps TERM/INT/HUP to
# quit (wait 0 → wrapper exits → phoc -E dies), so iterate never uses those.
# shellcheck disable=SC2016  # expands remotely, not here
RESTART_CMD='
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
if command -v systemctl >/dev/null 2>&1 \
   && [ "$(ps -p 1 -o comm=)" = systemd ] \
   && systemctl --user is-active --quiet xx-wm; then
    systemctl --user restart xx-wm
else
    # Match on comm=python3 so the SSH controller (bash) cannot match.
    pid=$(ps -u "$(id -un)" -o pid=,comm=,args= | while read -r p c rest; do
        [ "$c" = python3 ] || continue
        case $rest in
            */usr/share/xx-wm/main.py*) echo "$p" ;;
        esac
    done)
    if [ -n "$pid" ] && [ "$(echo "$pid" | wc -l)" -eq 1 ]; then
        kill -USR1 "$pid"
    else
        echo "no unique python3 /usr/share/xx-wm/main.py to restart (got: $pid)" >&2
        exit 1
    fi
fi'

if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] rsync $RSYNC_FLAGS $REPO_DIR/launcher/src/ ${TARGET}:${STAGE}/"
    echo "[dry-run] ssh $TARGET sudo rsync -a --chown=root:root ${STAGE}/ ${DEST}/"
    echo "[dry-run] restart: systemctl --user restart xx-wm if active, else SIGUSR1 unique python3 /usr/share/xx-wm/main.py (wrapper respawns)"
    exit 0
fi

echo "Checking passwordless sudo on ${TARGET}..."
if ! ssh "$TARGET" 'sudo -n true'; then
    echo "passwordless sudo is required to write ${DEST}. Run scripts/grant-tablet-sudo.sh" >&2
    exit 1
fi

echo "Deploying launcher/src/ -> ${TARGET}:${STAGE}/ then ${DEST}/"
# shellcheck disable=SC2086  # RSYNC_FLAGS is intentionally word-split
rsync $RSYNC_FLAGS "$REPO_DIR/launcher/src/" "${TARGET}:${STAGE}/" || exit 1

# Datadir rsync is without --delete: --delete would wipe phoc.ini, sounds,
# squeekboard, gnome-osk. --chown keeps the meson-installed tree root-owned.
# shellcheck disable=SC2029  # client-side expansion of STAGE/DEST is intended
ssh "$TARGET" "sudo rsync -a --chown=root:root ${STAGE}/ ${DEST}/" || exit 1

echo "Restarting xx-wm on ${DEVICE}..."
# shellcheck disable=SC2029  # client-side expansion is intended
ssh "$TARGET" "$RESTART_CMD"
