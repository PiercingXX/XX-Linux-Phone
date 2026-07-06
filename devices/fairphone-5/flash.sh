#!/usr/bin/env bash
# Flash postmarketOS onto Fairphone 5 (fairphone-fp5)
# Pre-built phosh image from images.postmarketos.org
#
# Requirements:
#   - android-tools (provides fastboot)
#   - Device in fastboot mode (power + vol-down, or: adb reboot bootloader)
#   - Bootloader already unlocked (fastboot flashing unlock)
#
# Usage: ./devices/fairphone-5/flash.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOADS="$SCRIPT_DIR/downloads"

# --- locate images ---
BOOT_XZ=$(ls "$DOWNLOADS"/*-boot.img.xz 2>/dev/null | head -1)
ROOTFS_XZ=$(ls "$DOWNLOADS"/*-fairphone-fp5.img.xz 2>/dev/null | grep -v boot | head -1)

if [[ -z "$BOOT_XZ" || -z "$ROOTFS_XZ" ]]; then
    echo "ERROR: images not found in $DOWNLOADS"
    echo "Expected: *-boot.img.xz and *-fairphone-fp5.img.xz"
    exit 1
fi

BOOT_IMG="${BOOT_XZ%.xz}"
ROOTFS_IMG="${ROOTFS_XZ%.xz}"

echo "=== PiercingOS / postmarketOS FP5 flash ==="
echo "Boot:   $(basename "$BOOT_XZ")"
echo "Rootfs: $(basename "$ROOTFS_XZ")"
echo ""

# --- check fastboot ---
if ! command -v fastboot &>/dev/null; then
    echo "ERROR: fastboot not found. Install android-tools (pacman -S android-tools)"
    exit 1
fi

if ! fastboot devices | grep -q .; then
    echo "ERROR: no device in fastboot mode."
    echo "  Put FP5 into fastboot: power off, then hold Power + Vol-Down"
    echo "  Or from adb: adb reboot bootloader"
    exit 1
fi

echo "Device detected:"
fastboot devices
echo ""

# --- show partition layout for reference ---
echo "--- Slot info ---"
fastboot getvar current-slot 2>&1 || true
fastboot getvar slot-count  2>&1 || true
echo ""

# --- decompress ---
if [[ ! -f "$BOOT_IMG" ]]; then
    echo "Decompressing boot image..."
    xz -dk "$BOOT_XZ"
fi

if [[ ! -f "$ROOTFS_IMG" ]]; then
    echo "Decompressing rootfs image (~750MB, may take a minute)..."
    xz -dk "$ROOTFS_XZ"
fi

echo ""
echo "=== Flashing ==="

# Flash boot to both A/B slots
echo "[1/3] Flashing boot_a..."
fastboot flash boot_a "$BOOT_IMG"

echo "[2/3] Flashing boot_b..."
fastboot flash boot_b "$BOOT_IMG"

# rootfs goes to userdata (largest accessible partition on A/B Android devices)
echo "[3/3] Flashing userdata (rootfs ~750MB, takes ~2 min)..."
fastboot flash userdata "$ROOTFS_IMG"

echo ""
echo "=== Done. Rebooting... ==="
fastboot reboot

echo ""
echo "First boot takes ~2-3 minutes. Watch the screen."
echo "Once Phosh loads, find the IP: tap the wifi icon or check your router."
echo "SSH: ssh user@<device-ip>"
