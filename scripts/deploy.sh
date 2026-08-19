#!/bin/sh
# XX-WM dev-loop deploy: rsync launcher/src/ to the phone and restart
# the shell service. Usage:
#   PIERCING_DEVICE=192.168.1.20 [XX_WM_USER=user] scripts/deploy.sh [--dry-run]
# GitHub.com/PiercingXX

set -u

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

DEVICE=${PIERCING_DEVICE:-}
USER_NAME=${XX_WM_USER:-user}
DRY_RUN=0

for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "Usage: PIERCING_DEVICE=<host> [XX_WM_USER=user] $0 [--dry-run]"
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
RSYNC_FLAGS="-az --delete"

# Restart the shell service — systemd user unit or OpenRC, whichever runs
# shellcheck disable=SC2016  # expands remotely, not here
RESTART_CMD='
if command -v systemctl >/dev/null 2>&1 && [ "$(ps -p 1 -o comm=)" = systemd ]; then
    systemctl --user restart xx-wm
elif command -v rc-service >/dev/null 2>&1; then
    doas rc-service xx-wm restart 2>/dev/null || sudo rc-service xx-wm restart
else
    echo "no known service manager — restart the shell manually" >&2
    exit 1
fi'

if [ "$DRY_RUN" = 1 ]; then
    # Echo mode: show exactly what would run, touch nothing
    echo "[dry-run] rsync $RSYNC_FLAGS $REPO_DIR/launcher/src/ ${TARGET}:xx-wm/src/"
    echo "[dry-run] ssh $TARGET '<restart xx-wm via systemd-user or OpenRC>'"
    exit 0
fi

echo "Deploying launcher/src/ -> ${TARGET}:~/xx-wm/src/"
# shellcheck disable=SC2086  # RSYNC_FLAGS is intentionally word-split
rsync $RSYNC_FLAGS "$REPO_DIR/launcher/src/" "${TARGET}:xx-wm/src/" || exit 1

echo "Restarting xx-wm on ${DEVICE}..."
# shellcheck disable=SC2029  # client-side expansion is intended
ssh "$TARGET" "$RESTART_CMD"
