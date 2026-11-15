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
LEGACY_CONFIG_FILE="/app/config.yaml"

if [ ! -f "$CONFIG_FILE" ]; then
    mkdir -p "$(dirname "$CONFIG_FILE")"
    if [ -f "$LEGACY_CONFIG_FILE" ] && [ -s "$LEGACY_CONFIG_FILE" ]; then
        cp "$LEGACY_CONFIG_FILE" "$CONFIG_FILE"
        echo "[entrypoint] Migrated legacy /app/config.yaml to $CONFIG_FILE"
    elif [ -f "$TEMPLATE_FILE" ]; then
        cp "$TEMPLATE_FILE" "$CONFIG_FILE"
        echo "[entrypoint] Initialized default config.yaml at $CONFIG_FILE from $TEMPLATE_FILE"
    else
        echo "[entrypoint] WARNING: no legacy or default config found, starting without $CONFIG_FILE" >&2
    fi
    echo "=============================================================="
    echo " AI Studio API 已完成首次配置初始化，正在启动 AI Studio API 服务..."
    echo "--------------------------------------------------------------"
    echo " 管理台 (Web Console): http://localhost:${AISTUDIO_PORT:-8080}/"
    echo " 登录密码: 通过环境变量 AISTUDIO_WEB_PASSWORD 设置 (默认为空即免密)"
    echo " 首次使用: 打开管理台创建 API Key, 并在账号页导入 Google 账号"
    echo "=============================================================="
fi

exec "$@"
