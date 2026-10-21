# aistudio-api

<p align="center">
  <a href="https://github.com/DeconstructedCube/aistudio-api"><img src="https://img.shields.io/badge/API-Gemini%20v1beta-4285F4?style=flat-square&logo=google" alt="Gemini API"></a>
  <a href="https://hub.docker.com/r/bedrockblock/aistudio-api"><img src="https://img.shields.io/badge/Docker-bedrockblock%2Faistudio--api-2496ED?style=flat-square&logo=docker" alt="Docker Hub"></a>
  <a href="https://deepwiki.com/DeconstructedCube/aistudio-api"><img src="https://img.shields.io/badge/DeepWiki-DeconstructedCube%2Faistudio--api-5B68E4?style=flat-square&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACwAAAAyCAYAAAAnWDnqAAAAAXNSR0IArs4c6QAAA05JREFUaEPtmUtyEzEQhtWTQyQLHNak2AB7ZnyXZMEjXMGeK/AIi+QuHrMnbChYY7MIh8g01fJoopFb0uhhEqqcbWTp06/uv1saEDv4O3n3dV60RfP947Mm9/SQc0ICFQgzfc4CYZoTPAswgSJCCUJUnAAoRHOAUOcATwbmVLWdGoH//PB8mnKqScAhsD0kYP3j/Yt5LPQe2KvcXmGvRHcDnpxfL2zOYJ1mFwrryWTz0advv1Ut4CJgf5uhDuDj5eUcAUoahrdY/56ebRWeraTjMt/00Sh3UDtjgHtQNHwcRGOC98BJEAEymycmYcWwOprTgcB6VZ5JK5TAJ+fXGLBm3FDAmn6oPPjR4rKCAoJCal2eAiQp2x0vxTPB3ALO2CRkwmDy5WohzBDwSEFKRwPbknEggCPB/imwrycgxX2NzoMCHhPkDwqYMr9tRcP5qNrMZHkVnOjRMWwLCcr8ohBVb1OMjxLwGCvjTikrsBOiA6fNyCrm8V1rP93iVPpwaE+gO0SsWmPiXB+jikdf6SizrT5qKasx5j8ABbHpFTx+vFXp9EnYQmLx02h1QTTrl6eDqxLnGjporxl3NL3agEvXdT0WmEost648sQOYAeJS9Q7bfUVoMGnjo4AZdUMQku50McDcMWcBPvr0SzbTAFDfvJqwLzgxwATnCgnp4wDl6Aa+Ax283gghmj+vj7feE2KBBRMW3FzOpLOADl0Isb5587h/U4gGvkt5v60Z1VLG8BhYjbzRwyQZemwAd6cCR5/XFWLYZRIMpX39AR0tjaGGiGzLVyhse5C9RKC6ai42ppWPKiBagOvaYk8lO7DajerabOZP46Lby5wKjw1HCRx7p9sVMOWGzb/vA1hwiWc6jm3MvQDTogQkiqIhJV0nBQBTU+3okKCFDy9WwferkHjtxib7t3xIUQtHxnIwtx4mpg26/HfwVNVDb4oI9RHmx5WGelRVlrtiw43zboCLaxv46AZeB3IlTkwouebTr1y2NjSpHz68WNFjHvupy3q8TFn3Hos2IAk4Ju5dCo8B3wP7VPr/FGaKiG+T+v+TQqIrOqMTL1VdWV1DdmcbO8KXBz6esmYWYKPwDL5b5FA1a0hwapHiom0r/cKaoqr+27/XcrS5UwSMbQAAAABJRU5ErkJggg==" alt="DeepWiki"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/Framework-FastAPI-009688?style=flat-square&logo=fastapi" alt="FastAPI"></a>
  <a href="https://vuejs.org/"><img src="https://img.shields.io/badge/Frontend-Vue%203%20%2B%20Vite-4FC08D?style=flat-square&logo=vuedotjs" alt="Vue 3"></a>
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/Python-3.11%2B%20%7C%20uv-261230?style=flat-square&logo=python" alt="Python uv"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License"></a>
</p>

<p align="center">
  Google AI Studio reverse proxy gateway that exposes native Google Gemini API interfaces.
</p>

<p align="center">
  <a href="./README.md">中文文档</a>
</p>

---

## Table of Contents

- [Features](#features)
- [Architecture & Documentation](#architecture--documentation)
- [Quick Start](#quick-start)
  - [Android (Termux)](#android-termux)
  - [Windows](#windows)
  - [Linux / macOS](#linux--macos)
  - [Docker Deployment](#docker-deployment)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [API Usage Examples](#api-usage-examples)
  - [cURL](#curl)
  - [Python SDK (google-genai)](#python-sdk-google-genai)
  - [Function Calling](#function-calling)
- [Web Management Console](#web-management-console)
- [Development & Testing](#development--testing)
- [Relationship to Upstream](#relationship-to-upstream)
- [Acknowledgements](#acknowledgements)
- [License](#license)

---

## Features

| Feature | Description |
|---|---|
| **Gemini API Compatibility** | Supports `/v1beta/...` endpoints including Thinking, multimodal inputs, function calling, and image generation |
| **OpenAI Chat Completions Compatibility** | Exposes `/v1/chat/completions` and `/v1/models` with streaming, tool calls, images, and `reasoning_content`; enabled by default |
| **OpenAI Responses Compatibility** | Exposes `/v1/responses` with streaming events, tool calls, reasoning, and images; usage includes input/output/reasoning/cache details; enabled by default |
| **Anthropic Messages Compatibility** | Exposes `/v1/messages` and `/v1/messages/count_tokens` with streaming events, tool calls, thinking, images, and real upstream counting |
| **Dynamic Model Discovery** | Automatically synchronizes available models and Paid/Pro-only metadata from upstream Google AI Studio; snapshots are persisted and warmed on startup, with fallback to the last snapshot if refresh fails |
| **Multi-Account Rotation** | Manages multiple accounts with automatic failover, tracks 429 rate limits per model independently, and resets quotas at midnight Pacific Time |
| **Instances & Channels (WP5)** | An instance is one account+channel usage window. PG and Build for the same account are two instances sharing one browser context when the total instance limit allows it. The total instance count is bounded by `max_contexts`; the default is one instance with sequential switching, and 2c3p (3 instances) is valid when `max_contexts>=3`. Channel switching replaces a single page without adding a context. |
| **Dual Build Paths** | The native 9998 client is primary; the top-page signer direct path is a fallback when native is unavailable. Both have independent health state, and signer failure never blocks the native channel. |
| **Dual Account Keepalive (WP10)** | Scheduled account rotation (long-cycle balancing + real page activity) and automatic Auth refresh (cookies/origins written back to `auth.json`) coexist; a separate 24h tier/email/expiry metadata refresh runs on its own switch |
| **Runtime State Persistence** | Token statistics v2 (`stats.json`: per-model/day aggregation, client/attempt layers, separate cache/reasoning/count_tokens, 90-day daily buckets) and account 429 cooldown (`rotator_state.json`) are persisted across restarts |
| **Downstream Parameter Fault Tolerance** | `drop_unsupported_params` automatically drops unsupported safety categories (e.g. `HARM_CATEGORY_CIVIC_INTEGRITY`) and tools to prevent 400 errors |
| **Multi-Account Probing** | Extracts and verifies sub-accounts (`u/0`, `u/1`...) automatically when importing a multi-session Cookie |
| **Built-in Tools** | Supports official tools such as Google Search, Google Maps, and Code Execution sandbox |
| **Pure Python CDP** | Direct Chrome DevTools Protocol communication over asynchronous WebSockets without Node.js, Playwright, or Selenium |
| **Web Management Console** | Built-in web dashboard for account management (supporting text and JSON bundle file imports), a dedicated usage statistics page `/stats` (tokens/cache/trends/account scheduling status), and runtime `config.yaml` hot reloading |
| **Android Companion App** | Native Android app (`android/`) built with Jetpack Compose, featuring isolated multi-profile management, on-demand WebView, Bitwarden autofill, and one-click bundle export |
> [!NOTE]
> **Memory Footprint**: The Python backend uses ~80 MB Working Set. A measured 2-instance run (one Build channel; sum of Private Working Set) averaged ~454 MB and peaked at ~523 MB. The WorkingSet sum in the same run was ~908 MB; it double-counts shared pages and is not the primary metric. On Android Termux, ≥ 1 GB free RAM is recommended.

## Architecture & Documentation

```
┌────────────────────────────────────────────────────────┐
│               Client (cURL / Python SDK)               │
└───────────────────────────┬────────────────────────────┘
                            │ /v1beta/models/...
┌───────────────────────────▼────────────────────────────┐
│              FastAPI HTTP Gateway (Port 8080)          │
├───────────────────────────┬────────────────────────────┤
│    Account Rotator        │    Chat & Wire Codec       │
│  (Sticky & 429 Isolation) │ (Protobuf-JSON & BotGuard) │
└───────────────────────────┼────────────────────────────┘
                            │ CDP WebSocket
┌───────────────────────────▼────────────────────────────┐
│      Controlled Chromium Instance (CloakBrowser)       │
│       In-Page XHR Replay (withCredentials=true)        │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS / RPC
┌───────────────────────────▼────────────────────────────┐
│         Google AI Studio (alkalimakersuite-pa)         │
└────────────────────────────────────────────────────────┘
```

Technical reference documentation:

- [System Architecture Specification (ARCHITECTURE.md)](./docs/ARCHITECTURE.md): Layered architecture, request lifecycle, wire codec, and concurrency model.
- [Wire Protocol Reverse Engineering Specification (WIRE_SPECIFICATION.md)](./docs/WIRE_SPECIFICATION.md): Protobuf-JSON array mappings, field indices, multimodal Part structures, and Schema encoding.
- [BotGuard Verification Chain (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md): WAA challenge handshake, dynamic Wasm signing, content hashing, and upstream validation.
- [Agent & Engineering Conventions (AGENT.md)](./AGENT.md): Strict type specifications, testing standards, and development guidelines.

---

## Quick Start

### Android (Termux)

> [!TIP]
> **Download Notice**: Always install Termux from [F-Droid](https://f-droid.org/packages/com.termux/) or [GitHub Releases](https://github.com/termux/termux-app/releases). **Do NOT install from the Google Play Store** (the Play Store build is obsolete and will produce repository update errors).

Open Termux and run the following commands step by step:

```bash
# Step 1: Update Termux environment and install git
pkg update -y && pkg install -y git

# Step 2: Clone repository and enter directory
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# Step 3: Initialize Python environment & sync dependencies (auto-resolves prebuilt Android wheels)
bash scripts/setup-env.sh
uv sync

# Step 4: Setup managed CloakBrowser runtime (first run only, takes ~1-2 min)
bash scripts/setup-browser.sh

# Step 5: Start server
uv run python main.py server --port 8080
```

Once started:
1. Open your phone's browser and go to **`http://localhost:8080`** to access the Web Console;
2. Navigate to **Accounts**, paste your Google account Cookies or import a bundle to start using the API;
3. **Background Persistence Tip**: Pull down Android's notification bar, tap `Acquire Wakelock` on Termux's notification, and disable battery optimization for Termux in phone system settings to prevent sleep kills.

---

### Windows

Prerequisite: [Python 3.11+](https://www.python.org/downloads/) installed (check `Add python.exe to PATH` during setup).

Open PowerShell or Windows Terminal:

```bat
# Step 1: Install uv package manager
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Step 2: Clone repository and enter directory
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# Step 3: Sync dependencies
uv sync

# Step 4: Install CloakBrowser (first run only)
scripts\install-browser.bat

# Step 5: Start server
uv run python main.py server --port 8080
```

Open **`http://localhost:8080`** in your browser to access the Web Console.

---

### Linux / macOS

Open a terminal and run:

```bash
# Step 1: Install uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Step 2: Clone repository and enter directory
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# Step 3: Sync dependencies
uv sync

# Step 4: Install CloakBrowser (first run only)
bash scripts/setup-browser.sh

# Step 5: Start server
uv run python3 main.py server --port 8080
```

Open **`http://localhost:8080`** in your browser to access the Web Console.

---

### Docker Deployment

CloakBrowser is bundled in the container image for immediate use:

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/deconstructedcube/aistudio-api:latest
```

Or run with Docker Compose:

```bash
docker compose up -d
```

Open **`http://localhost:8080`** in your browser to access the Web Console.

---

## Configuration & Environment Variables

Open `http://localhost:8080` in your browser to access the Web Console.

### Environment Variables
| Variable | Type | Default | Description |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | HTTP service listening port |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | Web Console access password (also checks `AISTUDIO_ADMIN_PASSWORD`) |
| `AISTUDIO_PROXY` | string | `""` | Outbound proxy URL (`http://`, `https://`, or `socks5://`) |
| `AISTUDIO_DATA_DIR` | string | `data/` | Main data directory for persistent files (`/app/data` in Docker) |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | Directory for storing account credentials |
| `AISTUDIO_STATS_FILE` | string | `data/stats.json` | Persistent storage file for token usage statistics (schema v2) |
| `AISTUDIO_MODEL_CACHE_FILE` | string | `data/models_cache.json` | Model discovery snapshot (including Paid metadata and `fetched_at`) used for startup recovery and refresh-failure fallback |
| `AISTUDIO_ROTATOR_STATE_FILE` | string | `data/rotator_state.json` | Persistent storage file for account 429 cooldowns and dispatch states |
| `AISTUDIO_CONFIG_FILE` | string | `config.yaml` | Path to model rules and default tools configuration YAML |
| `AISTUDIO_PERSIST_STATS` | bool | `true` | Persist request and token usage metrics across service restarts |
| `AISTUDIO_PERSIST_ROTATOR` | bool | `true` | Persist account 429 rate limit dates & cooldowns across restarts |
| `AISTUDIO_BROWSER_EXECUTABLE` | string | Auto | Path to CloakBrowser executable (`/opt/cloakbrowser/chrome` in Docker) |
| `AISTUDIO_BROWSER_PORT` | int | `9222` | Chromium remote debugging port (CDP) |
| `AISTUDIO_BROWSER_HEADLESS` | bool | `true` | Run Chromium in headless mode |
| `AISTUDIO_BROWSER_IDLE_TIMEOUT` | int | `900` / `1800` | Browser idle reclamation timeout in seconds (900s on Termux, 1800s on Desktop; 0 disables) |
| `AISTUDIO_PROOT_NAME` | string | `aistudio-api` | Dedicated proot-distro container name for Termux |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | Default text model |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | string | `gemini-3.1-flash-image-preview` | Default image generation model |
| `AISTUDIO_TIMEZONE` | string | Auto | Browser fingerprint & Wire protocol timezone (`America/Los_Angeles` by default) |
| `AISTUDIO_LOCALE` | string | `en-US` | Browser fingerprint locale |
| `AISTUDIO_DUMP_REQUESTS` | bool | `false` | Dump API request/response exchanges to console logs (enabled via `DEBUG=1`) |
| `AISTUDIO_DUMP_FILE` | bool | `false` | Dump request/response exchanges to individual disk files |
| `AISTUDIO_DUMP_DIR` | string | `dumps/` | Target directory for dumped exchange files |
| `AISTUDIO_LOG_DIR` | string | `data/logs/` | Daily archive directory (`aistudio-YYYYMMDD.log`, 7-day retention) |
| `AISTUDIO_APP_URL` | string | empty | Build app URL/App ID fallback when `channels.build.app_url` is empty |
| `AISTUDIO_BUILD_TRANSPORT` | enum | `native` | Build transport preference: `native` (default) or `signer`; falls back to native when signer is unavailable |
| `AISTUDIO_AUTO_SWITCH` | bool | `false` | Enable scheduled account rotation |
| `AISTUDIO_AUTO_SWITCH_INTERVAL_HOURS` | float | `3.0` | Rotation interval in hours (minimum `0.1`) |
| `AISTUDIO_KEEPALIVE_AUTH_UPDATE` | bool | `true` | Refresh Auth: write cookies/origins back to `auth.json` |
| `AISTUDIO_KEEPALIVE_AUTH_UPDATE_HOURS` | float | `24.0` | Credential write-back interval in hours (minimum `0.1`) |
| `AISTUDIO_KEEPALIVE_METADATA_REFRESH` | bool | `true` | Refresh tier/email/expiry metadata into `meta.json` |
| `AISTUDIO_KEEPALIVE_METADATA_REFRESH_HOURS` | float | `24.0` | Metadata refresh interval in hours (minimum `0.1`) |
| `AISTUDIO_KEEPALIVE_METADATA_REFRESH_ON_STARTUP` | bool | `true` | Refresh missing/stale metadata on startup |
| `AISTUDIO_KEEPALIVE_ACTIVE_ACTIVITY` | bool | `false` | Light active-page activity (prevents idle browser reclamation) |
| `AISTUDIO_KEEPALIVE_ACTIVE_ACTIVITY_SECONDS` | float | `60.0` | Active-page activity interval in seconds (minimum `10`) |

### Protocol Format Faces (WP8)

| Field | Type | Default | Description |
|---|---|---|---|
| `features.formats.openai` | bool | `true` | Enables `/v1/chat/completions` and `/v1/models`; when disabled the routes return an explicit 403. Default enabled to match Ark's unconditional endpoint registration. |
| `features.formats.claude` | bool | `true` | Enables `/v1/messages` and `/v1/messages/count_tokens`; when disabled the routes return an explicit 403. |
| `features.formats.responses` | bool | `true` | Enables `/v1/responses` and `/v1/responses/input_tokens`; when disabled the routes return an explicit 403. |
| `features.formats.media.allow_private_urls` | bool | `false` | Allow the proxy to download private/loopback image URLs; keep disabled for SSRF safety. `data:` URLs are unaffected. |
| `features.atg.mode_by_format` | map | `inherit` | Per-face ATG override: `gemini` / `openai` / `claude` / `responses` each accept `inherit` (follow `features.atg.mode`) or `off` / `prefer` / `required`. |

### Account Keepalive (WP10, backend only)

Three independent mechanisms:

1. **Scheduled rotation** (`features.auto_switch`): rotates the active account to the eligible account with the oldest `auth_saved_at`; saves the previous account's Auth before switching and the new account's Auth after; only runs when both chat and build have zero in-flight requests.
2. **Automatic Auth refresh** (`features.keepalive_auth_update`, equivalent to Ark `ENABLE_AUTH_UPDATE`): atomically merges browser cookies/origins into `auth.json` before switching, after initialization/reconnect, on the 24h interval, and before shutdown. It never writes meta.
3. **Automatic account-info refresh** (`features.keepalive_metadata_refresh`): periodically writes tier/email/expired/tier_checked_at into `registry.json`/`meta.json`; shares the same refresher and 409 mutex as `POST /api/accounts/refresh`.

Operational endpoints (management auth required):

- `GET /api/keepalive`: switches, intervals, next due times, latest refresh/auth-update/rotation results, and chat/build in-flight counts.
- `POST /api/keepalive/run`: manually run `metadata_refresh` / `auth_update` / `auto_switch` / `active_activity` for validation or troubleshooting; still subject to mutex and in-flight checks.
- `PUT /config/features`: structured feature update with atomic persistence and hot reload, ready for the future WP7 UI.
- `GET /logs/stream`: SSE log stream (`level`, `last_id` / `Last-Event-ID`; replays the newest 100 entries, 15s heartbeat, max 8 subscribers).
- `GET /logs/export`: export the last 7 days of archives; `start`/`end` (ISO8601) or `days`; single day returns `.log`, multiple days return `.zip`.

> Config dual-write: the local runtime config is the root `config.yaml` (git-ignored); the distributed default is `defaults/config.yaml` (tracked). Update both, and treat `defaults/config.yaml` as the committed source of truth.

---

## API Usage Examples

Supported authentication methods:
- Query param: `?key=YOUR_API_KEY`
- Header: `x-goog-api-key: YOUR_API_KEY`, `x-api-key: YOUR_API_KEY`, or `Authorization: Bearer YOUR_API_KEY`

### cURL

<details>
<summary><b>1. List Models</b></summary>

```bash
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-api-key"
```
</details>

<details>
<summary><b>2. Generate Content (Non-streaming)</b></summary>

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "Explain quantum computing in one sentence."}]
      }
    ]
  }'
```
</details>

<details>
<summary><b>3. Stream Generate Content (SSE)</b></summary>

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "Explain how Python asyncio works."}]
      }
    ]
  }'
```
</details>

### OpenAI Chat Completions

Use `http://localhost:8080/v1` as the base URL; get model IDs from `GET /v1/models`.

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "messages": [{"role": "user", "content": "Explain quantum computing in one sentence."}],
    "max_tokens": 256
  }'
```

OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="your-api-key",
)
resp = client.chat.completions.create(
    model="gemini-3.7-flash",
    messages=[
        {"role": "user", "content": "Explain quantum computing in one sentence."}
    ],
    max_tokens=256,
)
```

### OpenAI Responses

Use `http://localhost:8080/v1` as the base URL; the full endpoint is `/v1/responses`.

```bash
curl http://localhost:8080/v1/responses \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "input": "Explain quantum computing in one sentence.",
    "max_output_tokens": 256
  }'
```

Preflight token count:

```bash
curl http://localhost:8080/v1/responses/input_tokens \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gemini-3.7-flash", "input": "Explain quantum computing in one sentence."}'
```

### Anthropic Messages

Use `http://localhost:8080` as the base URL (the Anthropic SDK appends `/v1/messages`).

```bash
curl http://localhost:8080/v1/messages \
  -H "x-api-key: your-api-key" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "Explain quantum computing in one sentence."}]
  }'
```

Anthropic Python SDK:

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://localhost:8080",
    api_key="your-api-key",
)
msg = client.messages.create(
    model="gemini-3.7-flash",
    max_tokens=256,
    messages=[
        {"role": "user", "content": "Explain quantum computing in one sentence."}
    ],
)
```

> [!NOTE]
> Gemini / OpenAI Chat / OpenAI Responses / Anthropic faces are enabled by default. If disabled operationally, requests return 403 with the relevant `features.formats.*` key in the error message.
>
> Count endpoints use the real upstream tokenizer, but the current implementation packs system/tools/conversation into an aggregate prompt before calling MakerSuite `CountTokens`. Expect roughly +2.8%~+8.6% versus the native structured request; use it as an agent context-length reference, not a per-token exact value. `/v1/messages/count_tokens`, `/v1/responses/input_tokens`, and Gemini `:countTokens` are implemented.

---

### Python SDK (google-genai)

Using the official `google-genai` Python SDK:

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 1. Streaming response
response = client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="What is reactive programming?",
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 2. Non-streaming response
result = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="Write a quicksort implementation in Python.",
)
print(result.text)
```

---

### Function Calling

```python
from google import genai
from google.genai import types

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# Tool declaration
weather_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_current_weather",
            description="Get real-time weather conditions for a given location",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "location": types.Schema(
                        type="STRING",
                        description="City name, e.g. 'San Francisco' or 'Tokyo'",
                    ),
                    "unit": types.Schema(
                        type="STRING",
                        enum=["celsius", "fahrenheit"],
                        description="Temperature unit",
                    ),
                },
                required=["location"],
            ),
        )
    ]
)

response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="What is the weather in Tokyo right now?",
    config=types.GenerateContentConfig(tools=[weather_tool]),
)

if response.function_calls:
    for call in response.function_calls:
        print(f"Tool called: {call.name}")
        print(f"Arguments: {call.args}")
```

---

## Web Management Console

The service includes a built-in static Web Console served at `http://localhost:8080`:

- **Dashboard**: Real-time view of service health, per-model request stats, success rates, and rate-limit cooldown status.
- **Usage Statistics (Stats, `/stats`)**: Model calls (upstream attempts including retries/account switches, excluding `count_tokens`) are the primary metric; client requests are secondary and Token counting is separate. Views cover input, output, reasoning, cache reads/writes and hit rate, plus account scheduling state and Paid/Pro-only markers. Data endpoints are `GET /api/stats` and `GET /api/stats/timeseries`.
- **Accounts**: Import Cookie credentials (single text or bundle files), auto-probe multiple accounts, switch the active account, or reset rate limits.
- **Settings**: Online editor for `config.yaml` with instant hot-reloading on save.
- **Access Control**: Enable authentication by setting `AISTUDIO_WEB_PASSWORD`.

---

## Development & Testing

Frontend code is located in `web/` (Vue 3 + TypeScript + Vite + Tailwind CSS):

```bash
cd web
bun install
bun run dev          # Start local Vite dev server (localhost:3000 -> 8080)
bun run type-check   # Run TypeScript type check
bun run lint         # Run ESLint check
bun run build        # Build production assets into src/aistudio_api/static
```

Python quality checks and tests:

```bash
# Run code style & lint check
uv run ruff check .
# Run static type checking
bun x pyright

# Run test suite
uv run pytest
```

Android companion app code style checks:

```bash
# Run Kotlin code style check (configured via ~/.zshrc)
ktlint "android/app/src/main/java/**/*.kt"
```

---

## Relationship to Upstream

This repository is a downstream fork of [`DeconstructedCube/aistudio-api`](https://github.com/DeconstructedCube/aistudio-api), heavily reworked for low-memory, long-running deployments (weeks of uptime) and multi-instance scheduling. The main differences:

| Area | This repository |
|---|---|
| **Multi-instance** | The scheduling and usage unit is an *instance = account + channel*: the PG and Build channels of the same account are two instances with independent usage windows that share one browser context when the total instance limit allows it. `max_instances = max_contexts`; 2c1p/1c2p and, when `max_contexts>=3`, 2c3p are valid combinations as long as the total and channel caps are respected |
| **Channel lifecycle** | Channels can be disabled or reclaimed individually; the shared context is reclaimed only when the last instance exits, so the other channel keeps its session |
| **Build channel** | Native 9998 direct connection is the primary path; an in-page signer (local signature bypass) is the redundant fallback when Build fails, and signer failures never block the native path. The signer locates signing material via structural probes (`kvc` / `snapshot` / `content`) and `tokenLike` (getMetadata / H / A.snapshot) |
| **Web console rewrite** | Instance switching panel, instance overview and account matrix, multi-mode account management with bulk import, log SSE with 7-day archive export, a dedicated stats page and a partitioned settings page; UI/API never leak context/page/slot implementation terms |
| **Statistics semantics** | Model calls (requests/attempts, including retries and account switches) are the primary metric and client requests are secondary; `count_*` is shown separately and never counted as model calls; usage is tracked per channel/instance |
| **Long-running constraints** | Single instance by default; total page/instance budget follows `max_contexts` (1-16); `no-cache, must-revalidate` for static assets; on-demand warmup for stability on low-memory VPS hosts |

## Acknowledgements

This project builds on the following work:

- [`DeconstructedCube/aistudio-api`](https://github.com/DeconstructedCube/aistudio-api): the **upstream source solution**. The multi-protocol surfaces (Gemini / OpenAI / Anthropic / Responses), BotGuard verification chain, dynamic model discovery, account tier/email probing and session re-navigation all come from this repository and its maintainer; this fork continuously merges its updates.
- [`iBUHub/AIStudioToAPI`](https://github.com/iBUHub/AIStudioToAPI): the **provider of the Build channel approach**. The Build surface here (app host, wire frame protocol, task submission and result collection) references its public implementation, extended with a local signer bypass and a redundant native direct path.
- Lineage and in-repo research notes: `chrysoljq/aistudio-api` (original) → `DeconstructedCube/aistudio-api` → this fork. The handover documents for the Build channel, direct signer and bundle archaeology live in [`docs/ark-reference/`](./docs/ark-reference/README.md) (sanitized).

All rights to the referenced code and documentation remain with their respective authors; this repository only cites and credits them and does not reproduce any credentials or key material.

---

## License

This project is licensed under the [MIT License](./LICENSE).
