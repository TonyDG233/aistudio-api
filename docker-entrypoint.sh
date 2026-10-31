#!/bin/sh
set -e

DEFAULT_CONFIG_FILE="/app/data/config.yaml"

# Same precedence as config.resolve_config_file: non-empty AISTUDIO_CONFIG_PATH
# wins unconditionally, then non-empty AISTUDIO_CONFIG_FILE, then the default.
if [ -n "${AISTUDIO_CONFIG_PATH:-}" ]; then
    CONFIG_FILE="$AISTUDIO_CONFIG_PATH"
elif [ -n "${AISTUDIO_CONFIG_FILE:-}" ]; then
    CONFIG_FILE="$AISTUDIO_CONFIG_FILE"
else
    CONFIG_FILE="$DEFAULT_CONFIG_FILE"
fi

export AISTUDIO_CONFIG_PATH="$CONFIG_FILE"
TEMPLATE_FILE="/app/defaults/config.yaml"

if [ ! -f "$CONFIG_FILE" ] && [ -f "$TEMPLATE_FILE" ]; then
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cp "$TEMPLATE_FILE" "$CONFIG_FILE"
    echo "[entrypoint] Initialized default config.yaml at $CONFIG_FILE from $TEMPLATE_FILE"
fi

exec "$@"
