#!/bin/sh
# Piercing WM — piercing-dots phone-profile bootstrap
# Runs ON THE PHONE. Clones piercing-dots and applies its phone profile.
#
# Contract with piercing-dots (see repo handoff prompt):
#   ./install.sh --profile phone   must exist, be POSIX sh, detect apk vs apt,
#   never assume x86 / GNOME / systemd, and install only the phone subset:
#   kitty, nvim (+ daily-note entry point `piercing-note`), yazi, bash+starship,
#   and the maintenance script.
#
# Until that lands upstream this script is a stub that fails loudly.

set -eu

REPO="https://github.com/PiercingXX/piercing-dots"
DEST="${HOME}/.cache/piercing-dots"

if command -v git >/dev/null 2>&1; then :; else
    echo "git is required (apk add git / sudo apt install git)" >&2
    exit 1
fi

rm -rf "$DEST"
git clone --depth 1 "$REPO" "$DEST"

if [ -x "$DEST/install.sh" ] && grep -q -- '--profile' "$DEST/install.sh"; then
    exec sh "$DEST/install.sh" --profile phone
fi

echo "piercing-dots has no phone profile yet — waiting on the piercing-dots agent task." >&2
exit 1
