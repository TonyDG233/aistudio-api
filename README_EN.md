# aistudio-api

<p align="center">
  <a href="https://github.com/TonyDG233/aistudio-api"><img src="https://img.shields.io/badge/API-Gemini%20%7C%20OpenAI%20%7C%20Claude%20%7C%20Responses-4285F4?style=flat-square&logo=google" alt="Supported Protocols"></a>
  <a href="https://deepwiki.com/TonyDG233/aistudio-api"><img src="https://img.shields.io/badge/DeepWiki-TonyDG233%2Faistudio--api-5B68E4?style=flat-square&logo=bookstack&logoColor=white" alt="DeepWiki"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/Framework-FastAPI-009688?style=flat-square&logo=fastapi" alt="FastAPI"></a>
  <a href="https://vuejs.org/"><img src="https://img.shields.io/badge/Frontend-Vue%203%20%2B%20Vite-4FC08D?style=flat-square&logo=vuedotjs" alt="Vue 3"></a>
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/Python-3.11%2B%20%7C%20uv-261230?style=flat-square&logo=python" alt="Python uv"></a>
  <a href="https://opensource.org/license/mit"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License"></a>
</p>

<p align="center">
  Google AI Studio reverse proxy service that exposes web AI Studio as four standard API protocols: <b>Google Gemini (<code>/v1beta</code>)</b>, <b>OpenAI Chat (<code>/v1/chat/completions</code>)</b>, <b>Claude Messages (<code>/v1/messages</code>)</b>, and <b>OpenAI Responses (<code>/v1/responses</code>)</b>.
</p>

<p align="center">
  <a href="./README.md">中文文档</a>
</p>

---

## Table of Contents

- [Features](#features)
- [Architecture & Documentation](#architecture--documentation)
- [Quick Start](#quick-start)
  - [Docker Deployment](#docker-deployment)
  - [Android (Termux)](#android-termux)
  - [Windows](#windows)
  - [Linux / macOS](#linux--macos)
- [Obtaining & Importing Account Credentials (Cookies)](#obtaining--importing-account-credentials-cookies)
- [Third-Party Client Setup Guide (SillyTavern / Cherry Studio / Claude Code / NextChat)](#third-party-client-setup-guide-sillytavern--cherry-studio--claude-code--nextchat)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [API Usage Examples](#api-usage-examples)
  - [cURL](#curl)
  - [Python SDK (google-genai)](#python-sdk-google-genai)
  - [Function Calling](#function-calling)
- [Web Management Console](#web-management-console)
- [Development & Testing](#development--testing)
- [Credits & Acknowledgments](#credits--acknowledgments)
- [License](#license)

---

## Features

| Feature | Description |
|---|---|
| **Four-Protocol Native Compatibility** | Supports Gemini `/v1beta`, OpenAI `/v1/chat/completions`, Claude `/v1/messages`, and OpenAI Responses `/v1/responses` with Thinking, multimodal inputs, function calling, and `countTokens`. |
| **Dual-Engine Hot Switch (Browserless WAA / Controlled Browser)** | Switch live in the Web Console between the lightweight browserless engine (`waa-signer` with SpiderMonkey 152 + Firefox 152 uTLS fingerprint, ~80–150 MB RAM) and the `browser` engine (CloakBrowser Chromium). |
| **Free / Pro Tiered Pool & Anti-Risk Rotation** | Physically isolates Free and Pro+ pools so free models never consume Pro quota; features full-pool LRU rotation, **proactive idle rotation (rotates smoothly every N successful calls, scoped to Free-only / Paid-only / all / custom groups)**, 429 first-hit backoff switching without blind retries, and automatic `verify-account` isolation. |
| **Long-Lived Cookie Renewal & Tampermonkey Grabber** | Includes a Tampermonkey script to export cross-domain long-lived `.json` credential bundles (with `LSID`); supports automatic `/RotateCookies` rolling renewal, multi-login sub-account probing (`u/0..u/N`), and cookie sync. |
| **ATG Anti-Truncation & SillyTavern Compatibility** | Optional dual-switch Anti-Truncation Gateway (`wrap_input` text/media dual-track split + `wrap_output` synthetic tool streaming unwrap with editable templates), plus automatic tool-history repair and thought-signature caching for SillyTavern presets. |
| **Persistent State & Web Management Console** | Accounts, token stats, 429 cooldowns, and model cache persist to disk; the Web Console provides trend charts, account and slot management, visual config hot-reloading, and live SSE log streaming. |
| **Dynamic Model Discovery & Parameter Filtering** | Automatically syncs available models upstream and applies per-model safety settings and unsupported-parameter filtering to prevent 400 rejections. |
| **SSE Keepalive Anti-Timeout** | Streaming endpoints use a first-event pre-read window and periodic heartbeat frames so reverse proxies like Cloudflare do not time out (524) on long thinking turns. |
| **Deterministic Fingerprint & Separate Auth** | Generates per-account deterministic desktop fingerprints and keeps external API keys completely separate from the Web Console password. |
| **Android Companion App** | `android/` provides a native Jetpack Compose app with isolated multi-profile login, Bitwarden autofill, and credential bundle export. |

> [!NOTE]
> **Memory Footprint**: With the `browserless` engine, resident memory is only ~80–150 MB with near-instant startup. With the `browser` (CloakBrowser Chromium) engine, a single controlled browser instance uses ~350–450 MB total RAM (~250–350 MB on native Linux).

## Architecture & Documentation

```text
┌────────────────────────────────────────────────────────┐
│  Client (Gemini / OpenAI / Claude / Responses / Tavern)│
└───────────────────────────┬────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼────────────────────────────┐
│              FastAPI HTTP Gateway (Port 8080)          │
├───────────────────────────┬────────────────────────────┤
│  Instance Pool & LRU      │  4-Protocol Adapters /     │
│  Anti-Risk Routing        │  ATG / Wire Codec          │
└───────────────────────────┼────────────────────────────┘
                            │ Live Engine Switch
        ┌───────────────────┴───────────────────┐
        │ browserless (WAA)                     │ browser (CloakBrowser)
┌───────▼───────────────────────┐ ┌─────────────▼──────────────────────┐
│ waa-signer (SpiderMonkey 152) │ │ Controlled Chromium + CDP + XHR    │
└───────┬───────────────────────┘ └─────────────┬──────────────────────┘
        └───────────────────┬───────────────────┘
                            │ HTTPS / RPC
┌───────────────────────────▼────────────────────────────┐
│         Google AI Studio (alkalimakersuite-pa)         │
└────────────────────────────────────────────────────────┘
```

Technical reference documentation:

- [System Architecture Specification (ARCHITECTURE.md)](./docs/ARCHITECTURE.md)
- [Instance Pool & Routing Specification (INSTANCE_POOL_AND_ROUTING_SPEC.md)](./docs/INSTANCE_POOL_AND_ROUTING_SPEC.md)
- [Protocol & Tool-Call Specification (PROTOCOL_AND_TOOLCALL_SPEC.md)](./docs/PROTOCOL_AND_TOOLCALL_SPEC.md)
- [Keepalive & Logging Specification (KEEPALIVE_AND_LOGGING_SPEC.md)](./docs/KEEPALIVE_AND_LOGGING_SPEC.md)
- [Wire Protocol Specification (WIRE_SPECIFICATION.md)](./docs/WIRE_SPECIFICATION.md)
- [BotGuard Verification Chain (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md)
- [WAA VM Architecture (WAA_VM_ARCHITECTURE.md)](./docs/WAA_VM_ARCHITECTURE.md)
- [Changelog (changelog.md)](./docs/changelog.md)
- [Agent & Engineering Conventions (AGENT.md)](./AGENT.md)

---

## Quick Start

### Docker Deployment

> [!NOTE]
> The official Docker image comes with both `waa-signer` (browserless engine) and `CloakBrowser` (controlled browser engine) pre-built inside the container—**ready out of the box** with one-click switching in the Web Console. If you need an outbound proxy to reach Google, add `-e AISTUDIO_PROXY=http://host.docker.internal:7890` (Windows/macOS) or your host proxy URL.

Start with `docker run`:

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  -e AISTUDIO_WEB_PASSWORD=your-console-password \
  ghcr.io/tonydg233/aistudio-api:latest
```

Or start with Docker Compose:

```bash
# Prepare host config.yaml before using the root docker-compose.yml
cp defaults/config.yaml config.yaml
docker compose up -d
```

---

### Android (Termux)

> [!TIP]
> Install Termux from [F-Droid](https://f-droid.org/packages/com.termux/) or [GitHub Releases](https://github.com/termux/termux-app/releases). **Do NOT use the Google Play Store build.**

Open Termux and run the following commands step by step:

```bash
# Step 1: Update base packages and install git
pkg update -y && pkg install -y git

# Step 2: Clone repository and enter directory
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api

# Step 3: Initialize Python environment & sync dependencies
bash scripts/setup-env.sh
uv sync

# Step 4: Start the server
uv run python main.py server --port 8080
```

<details>
<summary><b>[Optional Advanced] Install controlled browser or build local waa-signer</b></summary>

- If using the default `browser` (controlled Chromium) engine, run once:
  ```bash
  bash scripts/setup-browser.sh
  ```
- If switching to the lightweight `browserless` engine, build local `waa-signer` (see [waa-signer README](./waa-signer/README.md)):
  ```bash
  pkg install -y golang zig
  bash waa-signer/build.sh
  ```
</details>

> [!TIP]
> **Background Persistence**: Pull down the Android notification shade, tap `Acquire Wakelock` on Termux, and disable battery optimization for Termux in system settings.

---

### Windows

Prerequisite: [Git](https://git-scm.com/) installed. Open PowerShell and run:

```powershell
# Step 1: Install uv package manager
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Step 2: Clone repository and enter directory
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api

# Step 3: Sync dependencies (uv manages Python automatically)
uv sync

# Step 4: Start the server
uv run python main.py server --port 8080
```

<details>
<summary><b>[Optional Advanced] Install controlled browser or build local waa-signer.exe</b></summary>

- For `browser` (controlled Chromium) mode:
  ```bat
  scripts\install-browser.bat
  ```
- For `browserless` mode, compile `waa-signer.exe`:
  ```powershell
  winget install GoLang.Go zig.zig
  .\waa-signer\build.bat
  ```
</details>

---

### Linux / macOS

Open a terminal and run:

```bash
# Step 1: Install uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Step 2: Clone repository and enter directory
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api

# Step 3: Sync dependencies
uv sync

# Step 4: Start the server
uv run python3 main.py server --port 8080
```

<details>
<summary><b>[Optional Advanced] Install controlled browser or build local waa-signer</b></summary>

- Install controlled browser (`browser` mode):
  ```bash
  bash scripts/setup-browser.sh
  ```
- Build local signer (`browserless` mode, requires Go 1.27+ and `zig` / `clang` / `gcc`):
  ```bash
  bash waa-signer/build.sh
  ```
</details>

> [!IMPORTANT]
> **After starting the service, open `http://localhost:8080` to enter the Web Console, then follow the guide below to (1) create an API Key and (2) import your Google account credentials.**

---

## Obtaining & Importing Account Credentials (Cookies)

This service never stores your Google password; it uses web session cookies and automatically rotates them in the background.

### Method 1 (Recommended, Long-Lived): Export `.json` Credential Bundle with Built-in Tampermonkey Script

1. **Install Script**: Install [Tampermonkey](https://www.tampermonkey.net/) in your desktop browser, then open **Accounts → + Import Credentials** in the Web Console to install the grabber script (or open `http://localhost:8080/api/tools/aistudio-cookie-grabber.user.js`).
2. **Export in Incognito**: Open a **fresh Incognito / Private window**, sign in to [Google AI Studio](https://aistudio.google.com/), and click **Export JSON Credential Bundle** in the bottom-right script panel (automatically includes long-lived `accounts.google.com` root cookies).
3. **Drag & Import**: Return to **Accounts → + Import Credentials** in the Web Console and drop the `.json` file into the dialog.

> [!WARNING]
> After exporting, **simply close the Incognito window—do NOT click Google's "Sign out" button**, or the exported root cookies will be invalidated immediately.

### Method 2: Export & Import via Cookie-Editor Extension

1. Sign in to [Google AI Studio](https://aistudio.google.com/) in your browser and make sure the chat workspace loads normally;
2. Install the **Cookie-Editor** extension (Chrome / Edge / Firefox);
3. On the `aistudio.google.com` tab, click Cookie-Editor and choose **Export → Export as JSON** (or Export as Header);
4. Paste the clipboard content into **Accounts → + Import Credentials** in the Web Console. The service verifies connectivity and probes multi-login sub-accounts (`u/0`, `u/1`...) automatically.

### Step 3: Create an External API Key in the Web Console

API authentication is enabled by default (requests without a valid key are rejected). Go to **Settings → API Key Management** in the Web Console, add a custom API Key (e.g. `sk-my-secret-key`), and save.

---

## Third-Party Client Setup Guide (SillyTavern / Cherry Studio / Claude Code / NextChat)

This gateway natively supports all four major protocols. Pick the matching settings for your client:

| Protocol Mode | Example Clients | Base URL / Endpoint | API Key |
|---|---|---|---|
| **OpenAI-Compatible** | SillyTavern (OpenAI source), Cherry Studio, Cursor, NextChat | Base URL: `http://localhost:8080/v1`<br/>Endpoint: `/v1/chat/completions` | Key configured in Console API Key Management |
| **Google Gemini Native** | SillyTavern (Gemini source), Cherry Studio (Gemini), `google-genai` SDK | Base URL: `http://localhost:8080`<br/>(or `http://localhost:8080/v1beta` if required) | Key configured in Console API Key Management |
| **Anthropic Claude Native** | SillyTavern (Claude source), Claude Code | Base URL: `http://localhost:8080`<br/>Endpoint: `/v1/messages` | Key configured in Console API Key Management |
| **OpenAI Responses** | Codex CLI, OpenAI Responses clients | Base URL: `http://localhost:8080/v1`<br/>Endpoint: `/v1/responses` | Key configured in Console API Key Management |

**Common Model Names**:
- Available on Free accounts: `gemini-4-hydrogen`, `gemini-3.8-flash`
- Requires Pro or higher: `gemini-4-argon`, `gemini-3.1-pro-preview`
- Requires Ultra: `gemini-4-carbon`
- Image generation: `gemini-3.1-flash-image-preview`

> [!TIP]
> **Recommended Settings (configurable visually on the Settings page)**:
> - **Proactive Rotation**: Set proactive rotation scope to **Free-only** so each account smoothly rotates when idle after N successful calls, reducing high-frequency risk flags.
> - **ATG Anti-Truncation**: For long-form roleplay in SillyTavern, enable ATG per protocol as needed (supports independent `wrap_input` / `wrap_output` toggles and custom prompt templates).

---

## Configuration & Environment Variables

Config precedence: `AISTUDIO_CONFIG_PATH` > `AISTUDIO_CONFIG_FILE` > root `config.yaml` (copied from `defaults/config.yaml` if missing). Most settings can be edited and hot-reloaded from the Web Console.

### Common Environment Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | HTTP server listening port |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | Web Console password (empty disables login; completely independent from API keys) |
| `AISTUDIO_PROXY` | string | Auto | Outbound proxy URL (`http://`, `https://`, or `socks5://`) |
| `AISTUDIO_DATA_DIR` | string | `data/` | Root data directory (`/app/data` in Docker) |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | Account credentials directory |
| `AISTUDIO_CONFIG_PATH` | string | `config.yaml` | Configuration YAML file path |
| `AISTUDIO_STATS_FILE` | string | `data/stats.json` | Model usage and token statistics file |
| `AISTUDIO_ROTATOR_STATE_FILE` | string | `data/rotator_state.json` | Account 429 cooldown and rotation state file |
| `AISTUDIO_MODEL_CACHE_FILE` | string | `data/models_cache.json` | Cached model catalog snapshot |
| `AISTUDIO_PERSIST_STATS` | bool | `true` | Persist request and token statistics |
| `AISTUDIO_PERSIST_ROTATOR` | bool | `true` | Persist account 429 cooldown and rotation state |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | Default text model |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | string | `gemini-3.1-flash-image-preview` | Default image generation model |
| `AISTUDIO_TIMEZONE` | string | Auto | Browser & wire timezone (falls back to `America/Los_Angeles`) |
| `AISTUDIO_SSE_KEEPALIVE_SEC` | float | `15` | SSE heartbeat interval (seconds) |
| `AISTUDIO_RETRY_SAME_DELAY_SEC` | float | `2.0` | Backoff seconds before same-account retry on upstream transient errors (`204`/`404`/`408`/`5xx`) |
| `AISTUDIO_SWITCH_BACKOFF_SECONDS` | float | `4.0` | Base backoff seconds before switching accounts on 429/risk events (±25% jitter, 3–5 s by default) |

---

## API Usage Examples

Supported authentication methods:
- Query param: `?key=YOUR_API_KEY`
- Header: `x-goog-api-key: YOUR_API_KEY`, `x-api-key: YOUR_API_KEY`, or `Authorization: Bearer YOUR_API_KEY`

### cURL

<details>
<summary><b>1. List Models (Gemini / OpenAI)</b></summary>

```bash
# Gemini model list
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-api-key"

# OpenAI model list
curl http://localhost:8080/v1/models \
  -H "Authorization: Bearer your-api-key"
```
</details>

<details>
<summary><b>2. Gemini Generate Content (Non-streaming / SSE)</b></summary>

```bash
# Non-streaming
curl http://localhost:8080/v1beta/models/gemini-3.8-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Explain quantum computing in one sentence."}]}]
  }'

# Streaming (SSE)
curl "http://localhost:8080/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse" \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Explain Python asyncio."}]}]
  }'
```
</details>

<details>
<summary><b>3. OpenAI Chat Completions</b></summary>

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```
</details>

<details>
<summary><b>4. Claude Messages</b></summary>

```bash
curl http://localhost:8080/v1/messages \
  -H "x-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```
</details>

<details>
<summary><b>5. OpenAI Responses</b></summary>

```bash
curl http://localhost:8080/v1/responses \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.8-flash",
    "input": "Hello"
  }'
```
</details>

<details>
<summary><b>6. Token Counting (countTokens)</b></summary>

```bash
# Gemini
curl http://localhost:8080/v1beta/models/gemini-3.8-flash:countTokens \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"parts": [{"text": "hello"}]}]}'

# Claude
curl http://localhost:8080/v1/messages/count_tokens \
  -H "x-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": "hello"}]}'

# OpenAI Responses
curl http://localhost:8080/v1/responses/input_tokens \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gemini-3.8-flash", "input": "hello"}'
```
</details>

---

### Python SDK (google-genai)

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
    model="gemini-3.8-flash",
    contents="What is reactive programming?",
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 2. Non-streaming response
result = client.models.generate_content(
    model="gemini-3.8-flash",
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
    model="gemini-3.8-flash",
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

The service includes a built-in static Web Console at `http://localhost:8080`:

- **Dashboard**: Real-time pool status, in-flight requests, account/quota overview, 7 / 30 / 90-day request and token trend charts, and quick API testers.
- **Accounts**: Import Cookie text or Tampermonkey `.json` bundles, probe multi-login sub-accounts, manage instance slots, groups, presets, 429 cooldowns, and trigger keepalive.
- **Settings**: API key management, live engine switching (`browser` / `browserless`), proactive rotation rules, protocol switches and ATG settings, model rules, and YAML editing with hot reload on save.
- **Logs**: Live SSE log stream, level filtering, keyword search, pause, and 1 / 3 / 7-day log export.
- **Access Control**: Set `AISTUDIO_WEB_PASSWORD` to require login; completely independent from external API keys.

---

## Development & Testing

Frontend source lives in `web/` (Vue 3 + TypeScript + Vite + Tailwind CSS):

```bash
cd web
bun install
bun run dev          # Start local dev server (localhost:3000 -> 8080)
bun run type-check   # TypeScript type check
bun run lint         # ESLint check
bun run build        # Build production assets into src/aistudio_api/static
```

Python checks and tests (managed via `uv`):

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pyright src tests
uv run pytest
```

Android companion checks:

```bash
cd android
./gradlew :app:lintDebug :app:ktlintCheck
```

---

## Credits & Acknowledgments

This project builds upon and draws inspiration from the following open-source projects and community efforts:

- **[DeconstructedCube/aistudio-api](https://github.com/DeconstructedCube/aistudio-api)**: Provided the foundational architecture, lightweight CDP transport layer, in-page XHR replay, and core wire codec implementation.
- **[Mag1cFall/AIStudio2API](https://github.com/Mag1cFall/AIStudio2API)**: Provided the embedded SpiderMonkey 152 runtime, Firefox 152 DOM / WebIDL host environment, and uTLS transport stack design from which `waa-signer` was extracted and adapted.
- **[Lianues/aistudio-pg-bridge](https://github.com/Lianues/aistudio-pg-bridge)**: Pioneered the lightweight browserless WAA proof signing concept and established the `/v1/proof/mint` interface specification.
- **[spinsnow](https://waa.spinsnow.fun)**: Provided a public, stable Node.js VM WAA attestation relay reference.
- **[Camoufox](https://github.com/daijro/camoufox)** & **[go-browserforge](https://github.com/brainplusplus/go-browserforge)**: Provided real-world browser fingerprint datasets and anti-fingerprinting generation algorithms.

---

## License

This project is licensed under the [MIT License](https://opensource.org/license/mit).
