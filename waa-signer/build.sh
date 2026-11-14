#!/usr/bin/env bash
set -euo pipefail

# 一键跨平台静态编译 WAA Signer 服务
# 支持环境: Android (Termux arm64), Linux (x86_64 / aarch64), macOS (arm64 / amd64)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TARGET="${TARGET:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --target)
      if [ $# -lt 2 ]; then
        echo "错误: --target 缺少参数值" >&2
        exit 1
      fi
      TARGET="$2"
      shift 2
      ;;
    --target=*)
      TARGET="${1#*=}"
      shift
      ;;
    *)
      echo "错误: 未知参数: $1" >&2
      exit 1
      ;;
  esac
done

SM_VERSION="v152.0.4-aistudio2api.1"

if [ "$TARGET" = "android-arm64" ]; then
  GO_ARCH="arm64"
  TARGET_GOOS="linux"
  OS_PREFIX="linux"
  OUT_PATH="${SCRIPT_DIR}/../android/app/src/main/jniLibs/arm64-v8a/libwaa_signer.so"
elif [ -z "$TARGET" ]; then
  ARCH="$(uname -m)"
  HOST_OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

  case "$ARCH" in
    x86_64|amd64)
      GO_ARCH="amd64"
      ;;
    aarch64|arm64)
      GO_ARCH="arm64"
      ;;
    *)
      echo "不支持的架构: $ARCH" >&2
      exit 1
      ;;
  esac

  TARGET_OS="${GOOS:-$HOST_OS}"
  case "$TARGET_OS" in
    linux|android)
      TARGET_GOOS="linux"
      OS_PREFIX="linux"
      ;;
    darwin)
      TARGET_GOOS="darwin"
      OS_PREFIX="darwin"
      ;;
    windows|mingw*|msys*|cygwin*)
      TARGET_GOOS="windows"
      OS_PREFIX="windows"
      ;;
    *)
      echo "不支持的操作系统: $TARGET_OS" >&2
      exit 1
      ;;
  esac

  BIN_NAME="waa-signer"
  if [ "$TARGET_GOOS" = "windows" ]; then
    BIN_NAME="waa-signer.exe"
  fi
  OUT_PATH="${SCRIPT_DIR}/bin/${BIN_NAME}"
else
  echo "错误: 不支持的目标平台: ${TARGET} (支持: android-arm64)" >&2
  exit 1
fi

SM_ARCH="${OS_PREFIX}-${GO_ARCH}"
DEPS_DIR="${SCRIPT_DIR}/.deps/spidermonkey/${SM_ARCH}"
mkdir -p "$DEPS_DIR"

if [ ! -f "$DEPS_DIR/lib/libjs_static.a" ]; then
  TARBALL="spidermonkey-${SM_VERSION}-${SM_ARCH}.tar.gz"
  OFFICIAL_URL="https://github.com/Mag1cFall/firefox/releases/download/${SM_VERSION}/${TARBALL}"
  PROXY="${AISTUDIO_PROXY:-${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}}}"
  CURL_ARGS=(-fL --retry 2 --connect-timeout 10 --max-time 180 -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
  if [ -n "$PROXY" ]; then
    CURL_ARGS+=(-x "$PROXY")
  fi

  TEMP_TAR="${SCRIPT_DIR}/.deps/${TARBALL}"
  mkdir -p "${SCRIPT_DIR}/.deps"

  # 1. 优先检查本地候选路径
  HAS_LOCAL=0
  for CANDIDATE in "$TEMP_TAR" "${SCRIPT_DIR}/${TARBALL}" "${HOME}/Downloads/${TARBALL}"; do
    if [ -f "$CANDIDATE" ] && [ "$(wc -c < "$CANDIDATE")" -gt 10000000 ]; then
      echo ">>> 检测到本地已存在静态依赖包: ${CANDIDATE}"
      if [ "$CANDIDATE" != "$TEMP_TAR" ]; then
        cp "$CANDIDATE" "$TEMP_TAR"
      fi
      HAS_LOCAL=1
      break
    fi
  done

  # 2. 多源镜像重试下载
  if [ "$HAS_LOCAL" -ne 1 ]; then
    DOWNLOAD_URLS=(
      "$OFFICIAL_URL"
      "https://ghproxy.net/${OFFICIAL_URL}"
      "https://gh-proxy.com/${OFFICIAL_URL}"
      "https://mirror.ghproxy.com/${OFFICIAL_URL}"
    )
    DOWNLOAD_SUCCESS=0
    for URL in "${DOWNLOAD_URLS[@]}"; do
      echo ">>> 正在下载 SpiderMonkey 静态依赖库: ${URL}"
      rm -f "$TEMP_TAR"
      if curl "${CURL_ARGS[@]}" -o "$TEMP_TAR" "$URL" && [ -f "$TEMP_TAR" ] && [ "$(wc -c < "$TEMP_TAR")" -gt 10000000 ]; then
        DOWNLOAD_SUCCESS=1
        break
      else
        rm -f "$TEMP_TAR"
      fi
    done
    if [ "$DOWNLOAD_SUCCESS" -ne 1 ]; then
      echo "" >&2
      echo "==================================================================" >&2
      echo "错误: 下载 SpiderMonkey 依赖包失败。您可以直接在浏览器中下载:" >&2
      echo "  ${OFFICIAL_URL}" >&2
      echo "并将其放置于 ${SCRIPT_DIR}/.deps/ 目录下后重新运行。" >&2
      echo "==================================================================" >&2
      echo "" >&2
      exit 1
    fi
  fi

  tar -xz -f "$TEMP_TAR" -C "$DEPS_DIR"
  rm -f "$TEMP_TAR"
  echo ">>> SpiderMonkey 解压完成: ${DEPS_DIR}"
fi

# 检查编译器
if command -v zig >/dev/null 2>&1; then
  case "$TARGET_GOOS" in
    linux)   ZIG_TARGET="${GO_ARCH}-linux-musl" ;;
    darwin)  ZIG_TARGET="${GO_ARCH}-macos-none" ;;
    windows) ZIG_TARGET="${GO_ARCH}-windows-gnu" ;;
  esac
  CC_CMD="zig cc -target ${ZIG_TARGET}"
  CXX_CMD="zig c++ -target ${ZIG_TARGET}"
  EXTLDFLAGS="-static -Wl,--strip-all"
elif [ "$TARGET" = "android-arm64" ]; then
  echo "错误: android-arm64 交叉编译需要 zig 编译器 (zig cc -target aarch64-linux-musl)" >&2
  exit 1
elif command -v clang >/dev/null 2>&1; then
  CC_CMD="clang"
  CXX_CMD="clang++"
  EXTLDFLAGS="-Wl,--strip-all"
elif command -v gcc >/dev/null 2>&1; then
  CC_CMD="gcc"
  CXX_CMD="g++"
  EXTLDFLAGS="-Wl,--strip-all"
else
  echo "错误: 未找到 zig, clang 或 gcc 编译器。请先安装 (例如: pkg install zig 或 apt install clang)" >&2
  exit 1
fi

# Linux/Android SpiderMonkey archives may include a compiler_builtins object
# that collides with Go's compiler-rt (___chkstk_ms). Remove that member
# before linking; no-op on archives that do not contain it.
if [ -f "$DEPS_DIR/lib/libjsrust.a" ]; then
  if [ ! -f "$DEPS_DIR/lib/libjsrust.a.orig" ]; then
    cp "$DEPS_DIR/lib/libjsrust.a" "$DEPS_DIR/lib/libjsrust.a.orig"
  fi
  if command -v zig >/dev/null 2>&1; then
    AR_CMD="zig ar"
  elif command -v ar >/dev/null 2>&1; then
    AR_CMD="ar"
  else
    AR_CMD=""
  fi
  if [ -n "$AR_CMD" ]; then
    for MEMBER in $($AR_CMD t "$DEPS_DIR/lib/libjsrust.a" | grep -E '^compiler_builtins-.*cgu\.150\.rcgu\.o$' || true); do
      echo ">>> 移除重复 compiler runtime 成员: ${MEMBER}"
      $AR_CMD d "$DEPS_DIR/lib/libjsrust.a" "$MEMBER"
    done
  fi
fi

mkdir -p "$(dirname "$OUT_PATH")"

echo ">>> 开始编译 waa-signer (CGO_ENABLED=1 GOOS=${TARGET_GOOS} GOARCH=${GO_ARCH})..."
CGO_ENABLED=1 GOOS="${TARGET_GOOS}" GOARCH="${GO_ARCH}" \
  CC="${CC_CMD}" \
  CXX="${CXX_CMD}" \
  CGO_CPPFLAGS="-I${DEPS_DIR}/include" \
  CGO_CXXFLAGS="-I${DEPS_DIR}/include" \
  CGO_LDFLAGS="-L${DEPS_DIR}/lib" \
  go build -trimpath -tags netgo,osusergo \
  -ldflags="-s -w -linkmode external -extldflags '${EXTLDFLAGS}'" \
  -o "${OUT_PATH}" ./cmd/waa-signer

echo ">>> 编译成功: ${OUT_PATH}"
ls -lh "${OUT_PATH}"
