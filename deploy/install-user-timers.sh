#!/usr/bin/env bash
# Copies this repo's canonical systemd --user unit files (deploy/systemd/)
# into ~/.config/systemd/user/ and reloads the user systemd daemon.
#
# This script ONLY installs/updates the unit files. It never enables or
# starts a timer - that stays a separate, explicit step you run yourself:
#
#   systemctl --user enable --now event-radar-<name>.timer
#
# Editing files under deploy/systemd/ does NOT change the installed units
# in ~/.config/systemd/user/ - re-run this script to pick up edits.
#
# Usage:
#   deploy/install-user-timers.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC_DIR="$SCRIPT_DIR/systemd"
UNIT_DEST_DIR="$HOME/.config/systemd/user"

mkdir -p "$UNIT_DEST_DIR"

shopt -s nullglob
units=("$UNIT_SRC_DIR"/event-radar-*.service "$UNIT_SRC_DIR"/event-radar-*.timer)
shopt -u nullglob

if [ ${#units[@]} -eq 0 ]; then
    echo "No event-radar-*.service/.timer files found in $UNIT_SRC_DIR" >&2
    exit 1
fi

for unit in "${units[@]}"; do
    cp -v "$unit" "$UNIT_DEST_DIR/"
done

systemctl --user daemon-reload

echo
echo "Installed $((${#units[@]})) unit file(s) to $UNIT_DEST_DIR and reloaded the user daemon."
echo "Nothing has been enabled or started."
echo "To enable a timer:   systemctl --user enable --now <unit>.timer"
echo "To list timers:      systemctl --user list-timers --all"
