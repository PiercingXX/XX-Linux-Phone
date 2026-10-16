#!/bin/sh
# Grant passwordless sudo on the XX-WM smoke tablet, from this laptop.
#
# Run ON THIS COMPUTER (not on the tablet). You will be prompted once for
# dr3k's *tablet* sudo password. After that, SSH as dr3k can `sudo -n`
# without a prompt.
#
#   sh scripts/grant-tablet-sudo.sh
#   sudo sh scripts/grant-tablet-sudo.sh
#
# Local sudo is optional: if you use it, the script re-execs as $SUDO_USER
# so your SSH keys still apply. Defaults match the smoke tablet:
#   PIERCING_DEVICE=192.168.1.129  XX_WM_USER=dr3k
#
# Revoke later:
#   sh scripts/grant-tablet-sudo.sh --revoke
# GitHub.com/PiercingXX

set -u

# Local root would SSH as root and miss this user's keys. Drop back.
if [ "$(id -u)" -eq 0 ]; then
    if [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = root ]; then
        echo "Do not run as root. Use: sh $0" >&2
        echo "Or: sudo -u <your-user> $0" >&2
        exit 1
    fi
    exec sudo -u "$SUDO_USER" -- "$0" "$@"
fi

DEVICE=${PIERCING_DEVICE:-192.168.1.129}
USER_NAME=${XX_WM_USER:-dr3k}
REVOKE=0
DRY_RUN=0
DROPIN=xx-wm-smoke

for arg in "$@"; do
    case $arg in
        --revoke) REVOKE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "Usage: [PIERCING_DEVICE=host] [XX_WM_USER=user] $0 [--revoke] [--dry-run]"
            echo "Installs /etc/sudoers.d/${DROPIN} on the tablet: NOPASSWD ALL for XX_WM_USER."
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

# Interpolated into a remote sh -c string — keep it a unix username.
case $USER_NAME in
    ''|*[!A-Za-z0-9._-]*)
        echo "Refusing XX_WM_USER='$USER_NAME' (must be a unix username)." >&2
        exit 1
        ;;
esac

TARGET="${USER_NAME}@${DEVICE}"

if [ "$REVOKE" = 1 ]; then
    echo "Remove /etc/sudoers.d/${DROPIN} on ${TARGET}."
    echo "Type ${USER_NAME}'s sudo password on the tablet if prompted."
    if [ "$DRY_RUN" = 1 ]; then
        echo "[dry-run] ssh -t $TARGET sudo rm -f /etc/sudoers.d/${DROPIN}"
        exit 0
    fi
    ssh -t "$TARGET" "sudo rm -f /etc/sudoers.d/${DROPIN} && sudo visudo -c"
    echo "Revoked. ${USER_NAME} needs a password for sudo again (except reboot/shutdown)."
    exit 0
fi

echo "Install /etc/sudoers.d/${DROPIN} on ${TARGET}:"
echo "  ${USER_NAME} ALL=(ALL:ALL) NOPASSWD: ALL"
echo "Type ${USER_NAME}'s sudo password on the tablet if prompted."

if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] ssh -t $TARGET sudo sh -c '<write drop-in + visudo -c>'"
    exit 0
fi

# File contents live in the remote command (not stdin) so `ssh -t` can use
# the tty for the sudo password. sudoers.d filenames must not contain '.' .
# visudo -c (no -f) checks the whole sudoers tree after install.
ssh -t "$TARGET" "sudo sh -c '
set -eu
dest=/etc/sudoers.d/${DROPIN}
tmp=\$(mktemp)
trap \"rm -f \$tmp\" EXIT
printf \"%s\\n\" \
  \"# XX-WM smoke tablet — passwordless sudo. Delete this file when smoke is done.\" \
  \"${USER_NAME} ALL=(ALL:ALL) NOPASSWD: ALL\" > \"\$tmp\"
chmod 0440 \"\$tmp\"
visudo -c -f \"\$tmp\"
install -o root -g root -m 0440 \"\$tmp\" \"\$dest\"
visudo -c
printf \"installed %s\\n\" \"\$dest\"
'"

echo "Checking passwordless sudo..."
if ssh -o BatchMode=yes -o ConnectTimeout=8 "$TARGET" "sudo -n true"; then
    echo "OK: ${TARGET} can sudo without a password."
else
    echo "Drop-in written but sudo -n still failed. Check /etc/sudoers.d/${DROPIN} on the tablet." >&2
    exit 1
fi
