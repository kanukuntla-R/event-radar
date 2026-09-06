#!/usr/bin/env bash
# Generates the persistent EVENT_RADAR_SECRET_KEY used to sign Flask
# sessions (V0.10C), if one doesn't already exist. Never rotates an
# existing secret - Flask session cookies signed with the old key would
# all become invalid (every logged-in user gets signed out).
#
# Safe to re-run: it only creates the file if missing.
#
# Usage:
#   deploy/generate-secret.sh
set -euo pipefail

SECRET_DIR="$HOME/.config/event-radar"
SECRET_FILE="$SECRET_DIR/secret.env"

mkdir -p "$SECRET_DIR"
chmod 0700 "$SECRET_DIR"

if [ -f "$SECRET_FILE" ]; then
    echo "Secret already exists at $SECRET_FILE - not rotating it."
    exit 0
fi

SECRET_VALUE="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
# Write with a restrictive umask so the file is never briefly world/group
# readable between creation and chmod.
( umask 077; printf 'EVENT_RADAR_SECRET_KEY=%s\n' "$SECRET_VALUE" > "$SECRET_FILE" )
chmod 0600 "$SECRET_FILE"

echo "Generated a new secret at $SECRET_FILE (mode 0600)."
