#!/bin/sh
# XX-WM dev-loop deploy: rsync launcher/src/ to the phone's installed
# datadir and restart the running shell.
# Usage:
#   PIERCING_DEVICE=192.168.1.20 [XX_WM_USER=dr3k] scripts/deploy.sh [--dry-run]
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
STAGE_DIR="/tmp/xx-wm-deploy"
DEST_DIR="/usr/share/xx-wm"

# Restart the running shell: the systemd user unit only when it is the
# active shell (restarting an inactive unit under GDM would start a
# second shell). Otherwise SIGUSR1 the unique python child; the
# /usr/bin/xx-wm supervisor respawns it with fresh FDs.
# shellcheck disable=SC2016  # expands remotely, not here
RESTART_CMD='
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
if command -v systemctl >/dev/null 2>&1 \
   && [ "$(ps -p 1 -o comm=)" = systemd ] \
   && systemctl --user is-active --quiet xx-wm; then
    systemctl --user restart xx-wm
else
    # [p]ython3 so this pgrep cmdline cannot match itself (the controller).
    pid=$(pgrep -u "$(id -un)" -f '"'"'[p]ython3 /usr/share/xx-wm/main.py'"'"' || true)
    if [ -n "$pid" ] && [ "$(echo "$pid" | wc -l)" -eq 1 ]; then
        kill -USR1 "$pid"
    else
        echo "no unique python3 /usr/share/xx-wm/main.py to restart (got: $pid)" >&2
        exit 1
    fi
fi'

if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] rsync -az --delete --exclude '__pycache__' $REPO_DIR/launcher/src/ $TARGET:$STAGE_DIR/"
    echo "[dry-run] rsync -az $REPO_DIR/launcher/data/{wayland,wlr-foreign-toplevel-management-unstable-v1,wlr-output-power-management-unstable-v1,virtual-pointer-unstable-v1}.xml $TARGET:$STAGE_DIR/"
    echo "[dry-run] ssh $TARGET sudo rsync -a --chown=root:root $STAGE_DIR/ $DEST_DIR/"
    echo "[dry-run] restart: systemctl --user restart xx-wm if active, else SIGUSR1 unique '[p]ython3 /usr/share/xx-wm/main.py' (wrapper respawns)"
    exit 0
fi

if ! ssh "$TARGET" 'sudo -n true' 2>/dev/null; then
    echo "passwordless sudo is required on $TARGET — run scripts/grant-tablet-sudo.sh" >&2
    exit 1
fi

echo "Deploying launcher/src/ -> $TARGET:$DEST_DIR/ (via $STAGE_DIR)"
rsync -az --delete --exclude '__pycache__' \
    "$REPO_DIR/launcher/src/" "${TARGET}:$STAGE_DIR/" || exit 1
# The protocol XMLs live next to the modules on the device; the switcher
# regenerates its bindings when they change, so ship them with the code.
rsync -az "$REPO_DIR/launcher/data/wayland.xml" \
    "$REPO_DIR/launcher/data/wlr-foreign-toplevel-management-unstable-v1.xml" \
    "$REPO_DIR/launcher/data/wlr-output-power-management-unstable-v1.xml" \
    "$REPO_DIR/launcher/data/virtual-pointer-unstable-v1.xml" \
    "${TARGET}:$STAGE_DIR/" || exit 1

echo "Installing into $DEST_DIR on ${DEVICE}..."
# shellcheck disable=SC2029  # client-side expansion is intended
ssh "$TARGET" "sudo rsync -a --chown=root:root $STAGE_DIR/ $DEST_DIR/" || exit 1

echo "Restarting xx-wm on ${DEVICE}..."
# shellcheck disable=SC2029  # client-side expansion is intended
ssh "$TARGET" "$RESTART_CMD"
