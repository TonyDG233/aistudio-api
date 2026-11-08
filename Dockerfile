# syntax=docker/dockerfile:1

# ============================================================================
# Stage 1: waa-signer (browserless engine signing daemon)
#
# Builds the Go signer with the Go 1.27 toolchain required by waa-signer/go.mod
# (toolchain go1.27.0) and links the static SpiderMonkey 152 library with Zig.
# The resulting binary is statically linked, so no mozjs shared library is
# needed in the runtime image. BuildKit caches the module download, Zig download
# and SpiderMonkey download layers independently from the source copy.
# ============================================================================
FROM --platform=$BUILDPLATFORM golang:1.27-bookworm AS waa-signer-build

ARG TARGETARCH
ARG ZIG_VERSION=0.14.1
ARG SPIDERMONKEY_VERSION=v152.0.4-aistudio2api.1

ENV GOTOOLCHAIN=local \
    CGO_ENABLED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Zig for the build host architecture. Zig is used as the C/C++
# cross-compiler so Go can cross-compile CGO for the target architecture.
RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
        amd64) ZIG_ARCH="x86_64" ;; \
        arm64) ZIG_ARCH="aarch64" ;; \
        *) echo "Unsupported build architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -fSL --retry 3 -o /tmp/zig.tar.xz \
        "https://ziglang.org/download/${ZIG_VERSION}/zig-linux-${ZIG_ARCH}-${ZIG_VERSION}.tar.xz"; \
    mkdir -p /opt/zig; \
    tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1; \
    rm -f /tmp/zig.tar.xz; \
    /opt/zig/zig version
ENV PATH="/opt/zig:${PATH}"

WORKDIR /src/waa-signer

# Cache Go module downloads before copying the source tree.
COPY waa-signer/go.mod waa-signer/go.sum ./
RUN go mod download

# Download the matching static SpiderMonkey library before copying the source
# tree so source changes do not re-download it.
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) SM_ARCH="linux-amd64" ;; \
        arm64) SM_ARCH="linux-arm64" ;; \
        *) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    DEPS_DIR="/src/waa-signer/.deps/spidermonkey/${SM_ARCH}"; \
    mkdir -p "${DEPS_DIR}"; \
    TARBALL="spidermonkey-${SPIDERMONKEY_VERSION}-${SM_ARCH}.tar.gz"; \
    curl -fSL --retry 3 -o "/tmp/${TARBALL}" \
        "https://github.com/Mag1cFall/firefox/releases/download/${SPIDERMONKEY_VERSION}/${TARBALL}"; \
    tar -xzf "/tmp/${TARBALL}" -C "${DEPS_DIR}"; \
    rm -f "/tmp/${TARBALL}"; \
    test -f "${DEPS_DIR}/lib/libjs_static.a"

COPY waa-signer/ ./

# Build a fully static Linux binary for the requested target architecture.
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) GO_ARCH="amd64"; SM_ARCH="linux-amd64"; ZIG_TARGET="x86_64-linux-musl" ;; \
        arm64) GO_ARCH="arm64"; SM_ARCH="linux-arm64"; ZIG_TARGET="aarch64-linux-musl" ;; \
        *) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    DEPS_DIR="/src/waa-signer/.deps/spidermonkey/${SM_ARCH}"; \
    mkdir -p /out; \
    CGO_ENABLED=1 GOOS=linux GOARCH="${GO_ARCH}" \
        CC="zig cc -target ${ZIG_TARGET}" \
        CXX="zig c++ -target ${ZIG_TARGET}" \
        CGO_CXXFLAGS="-I${DEPS_DIR}/include" \
        CGO_LDFLAGS="-L${DEPS_DIR}/lib" \
        go build -trimpath -tags netgo,osusergo \
            -ldflags="-s -w -linkmode external -extldflags '-static -Wl,--strip-all'" \
            -o /out/waa-signer ./cmd/waa-signer; \
    chmod +x /out/waa-signer

# ============================================================================
# Stage 2: runtime image
# ============================================================================
# Use Debian 12 (bookworm) explicitly so package names stay stable.
FROM python:3.11-slim-bookworm

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AISTUDIO_DATA_DIR=/app/data \
    AISTUDIO_CONFIG_FILE=/app/data/config.yaml \
    AISTUDIO_BROWSER_EXECUTABLE=/opt/cloakbrowser/chrome

# Install system dependencies and runtime libraries for CloakBrowser headless CDP automation
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-wqy-zenhei \
    libnss3 \
    libnspr4 \
    libgbm1 \
    libasound2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxfixes3 \
    libxext6 \
    libxrender1 \
    libx11-6 \
    libxcb1 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libgtk-3-0 \
    libnotify4 \
    libpangocairo-1.0-0 \
    libcups2 \
    libdrm2 \
    libxshmfence1 \
    libglib2.0-0 \
    libdbus-1-3 \
    libfontconfig1 \
    libfreetype6 \
    libharfbuzz0b \
    libxi6 \
    libxcursor1 \
    libexpat1 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    tzdata \
    locales \
    && rm -rf /var/lib/apt/lists/*

# The waa-signer binary embeds Go's time/tzdata and resolves its locale from
# its own flags, so it does not depend on system locale files. Generate
# en_US.UTF-8 anyway so Chromium and shell tooling can reuse it when a caller
# explicitly sets LANG/LC_ALL; the default C.UTF-8 environment is unchanged.
RUN sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen

# Install CloakBrowser (stealth anti-detection Chromium) for x86_64 / aarch64
ARG CLOAKBROWSER_VERSION=146.0.7680.177.4
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    case "${ARCH}" in \
        amd64) CLOAK_ARCH="x64" ;; \
        arm64) CLOAK_ARCH="arm64" ;; \
        *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    mkdir -p /opt/cloakbrowser; \
    DOWNLOAD_URL="https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v${CLOAKBROWSER_VERSION}/cloakbrowser-linux-${CLOAK_ARCH}.tar.gz"; \
    echo "Downloading CloakBrowser from ${DOWNLOAD_URL}..."; \
    curl -fSL --retry 3 "${DOWNLOAD_URL}" | tar -xz -C /opt/cloakbrowser; \
    if [ ! -f /opt/cloakbrowser/chrome ]; then \
        INNER="$(find /opt/cloakbrowser -mindepth 1 -maxdepth 2 -type f -name chrome | head -n 1)"; \
        if [ -n "$INNER" ]; then \
            INNER_DIR="$(dirname "$INNER")"; \
            if [ "$INNER_DIR" != "/opt/cloakbrowser" ]; then \
                mv "$INNER_DIR"/* /opt/cloakbrowser/; \
                rmdir "$INNER_DIR" 2>/dev/null || true; \
            fi; \
        fi; \
    fi; \
    chmod +x /opt/cloakbrowser/chrome; \
    /opt/cloakbrowser/chrome --no-sandbox --version || true
# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (src includes the static frontend under
# src/aistudio_api/static) and the tracked default configuration template.
COPY src/ src/
COPY main.py .
COPY defaults/config.yaml /app/defaults/config.yaml
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# Bundle the statically linked waa-signer for the browserless engine. The path
# must stay identical to engine.browserless.signer.binary_path in
# defaults/config.yaml and resolves relative to /app at runtime.
COPY --from=waa-signer-build /out/waa-signer /app/waa-signer/bin/waa-signer

# Create necessary directories
RUN mkdir -p /app/data /app/waa-signer/bin /tmp

# Set permissions
RUN chmod +x /app/main.py /app/docker-entrypoint.sh /app/waa-signer/bin/waa-signer

# Expose ports
# 8080: API server
# 9222: Browser debug port
EXPOSE 8080 9222

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

# Container entrypoint and default command
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python3", "main.py", "server", "--port", "8080", "--browser-port", "9222"]
