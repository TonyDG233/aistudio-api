#!/bin/sh
set -e

DEFAULT_CONFIG_FILE="/app/data/config.yaml"
CONFIG_FILE="${AISTUDIO_CONFIG_PATH:-$DEFAULT_CONFIG_FILE}"

# Backward compatibility: an old deployment that only overrides
# AISTUDIO_CONFIG_FILE keeps working while AISTUDIO_CONFIG_PATH still wins
# whenever it is explicitly set to a non-default path.
if [ -n "$AISTUDIO_CONFIG_FILE" ] \
    && [ "$AISTUDIO_CONFIG_FILE" != "$DEFAULT_CONFIG_FILE" ] \
    && [ "$CONFIG_FILE" = "$DEFAULT_CONFIG_FILE" ]; then
    CONFIG_FILE="$AISTUDIO_CONFIG_FILE"
fi

export AISTUDIO_CONFIG_PATH="$CONFIG_FILE"
TEMPLATE_FILE="/app/defaults/config.yaml"

if [ ! -f "$CONFIG_FILE" ] && [ -f "$TEMPLATE_FILE" ]; then
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cp "$TEMPLATE_FILE" "$CONFIG_FILE"
    echo "[entrypoint] Initialized default config.yaml at $CONFIG_FILE from $TEMPLATE_FILE"
fi

exec "$@"
