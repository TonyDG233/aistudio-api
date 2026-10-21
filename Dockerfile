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
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    && rm -rf /var/lib/apt/lists/*

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

# Copy application code and default configuration template
COPY src/ src/
COPY main.py .
COPY defaults/config.yaml /app/defaults/config.yaml
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# Create necessary directories
RUN mkdir -p /app/data /tmp

# Set permissions
RUN chmod +x /app/main.py /app/docker-entrypoint.sh

# Expose ports
# 8080: API server
# 9222: Browser debug port
EXPOSE 8080 9222

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/v1beta/models || exit 1

# Container entrypoint and default command
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python3", "main.py", "server", "--port", "8080", "--browser-port", "9222"]
