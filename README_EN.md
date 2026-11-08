# aistudio-api

<p align="center">
  <a href="https://github.com/TonyDG233/aistudio-api"><img src="https://img.shields.io/badge/API-Gemini%20v1beta-4285F4?style=flat-square&logo=google" alt="Gemini API"></a>
  <a href="https://deepwiki.com/TonyDG233/aistudio-api"><img src="https://img.shields.io/badge/DeepWiki-TonyDG233%2Faistudio--api-5B68E4?style=flat-square&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACwAAAAyCAYAAAAnWDnqAAAAAXNSR0IArs4c6QAAA05JREFUaEPtmUtyEzEQhtWTQyQLHNak2AB7ZnyXZMEjXMGeK/AIi+QuHrMnbChYY7MIh8g01fJoopFb0uhhEqqcbWTp06/uv1saEDv4O3n3dV60RfP947Mm9/SQc0ICFQgzfc4CYZoTPAswgSJCCUJUnAAoRHOAUOcATwbmVLWdGoH//PB8mnKqScAhsD0kYP3j/Yt5LPQe2KvcXmGvRHcDnpxfL2zOYJ1mFwrryWTz0advv1Ut4CJgf5uhDuDj5eUcAUoahrdY/56ebRWeraTjMt/00Sh3UDtjgHtQNHwcRGOC98BJEAEymycmYcWwOprTgcB6VZ5JK5TAJ+fXGLBm3FDAmn6oPPjR4rKCAoJCal2eAiQp2x0vxTPB3ALO2CRkwmDy5WohzBDwSEFKRwPbknEggCPB/imwrycgxX2NzoMCHhPkDwqYMr9tRcP5qNrMZHkVnOjRMWwLCcr8ohBVb1OMjxLwGCvjTikrsBOiA6fNyCrm8V1rP93iVPpwaE+gO0SsWmPiXB+jikdf6SizrT5qKasx5j8ABbHpFTx+vFXp9EnYQmLx02h1QTTrl6eDqxLnGjporxl3NL3agEvXdT0WmEost648sQOYAeJS9Q7bfUVoMGnjo4AZdUMQku50McDcMWcBPvr0SzbTAFDfvJqwLzgxwATnCgnp4wDl6Aa+Ax283gghmj+vj7feE2KBBRMW3FzOpLOADl0Isb5587h/U4gGvkt5v60Z1VLG8BhYjbzRwyQZemwAd6cCR5/XFWLYZRIMpX39AR0tjaGGiGzLVyhse5C9RKC6ai42ppWPKiBagOvaYk8lO7DajerabOZP46Lby5wKjw1HCRx7p9sVMOWGzb/vA1hwiWc6jm3MvQDTogQkiqIhJV0nBQBTU+3okKCFDy9WwferkHjtxib7t3xIUQtHxnIwtx4mpg26/HfwVNVDb4oI9RHmx5WGelRVlrtiw43zboCLaxv46AZeB3IlTkwouebTr1y2NjSpHz68WNFjHvupy3q8TFn3Hos2IAk4Ju5dCo8B3wP7VPr/FGaKiG+T+v+TQqIrOqMTL1VdWV1DdmcbO8KXBz6esmYWYKPwDL5b5FA1a0hwapHiom0r/cKaoqr+27/XcrS5UwSMbQAAAABJRU5ErkJggg==" alt="DeepWiki"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/Framework-FastAPI-009688?style=flat-square&logo=fastapi" alt="FastAPI"></a>
  <a href="https://vuejs.org/"><img src="https://img.shields.io/badge/Frontend-Vue%203%20%2B%20Vite-4FC08D?style=flat-square&logo=vuedotjs" alt="Vue 3"></a>
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/Python-3.11%2B%20%7C%20uv-261230?style=flat-square&logo=python" alt="Python uv"></a>
  <a href="https://opensource.org/license/mit"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License"></a>
</p>

<p align="center">
  A Google AI Studio reverse proxy gateway that exposes one unified backend through Gemini, OpenAI, Claude, and OpenAI Responses protocols.
</p>

<p align="center">
  <a href="./README.md">中文文档</a>
</p>

---

## Table of Contents

- [Features](#features)
- [Protocol Matrix](#protocol-matrix)
- [Architecture](#architecture)
- [Instance Pool and Account Scheduling](#instance-pool-and-account-scheduling)
- [Large-Context Tool Calling and SillyTavern](#large-context-tool-calling-and-sillytavern)
- [Browser Fingerprint and Keepalive](#browser-fingerprint-and-keepalive)
- [Quick Start](#quick-start)
  - [Android (Termux)](#android-termux)
  - [Windows](#windows)
  - [Linux / macOS](#linux--macos)
  - [Docker / Compose](#docker--compose)
- [Configuration and Environment Variables](#configuration-and-environment-variables)
- [Web Console](#web-console)
- [Client Integration](#client-integration)
- [Development and Testing](#development-and-testing)
- [Documentation](#documentation)
- [Acknowledgments](#acknowledgments)
- [License](#license)

---

## Features

| Feature | Description |
|---|---|
| **Four protocol faces** | One protocol-neutral generation pipeline serves Gemini `/v1beta`, OpenAI `/v1/chat/completions`, Claude `/v1/messages`, and OpenAI Responses `/v1/responses`. Gemini is always on; OpenAI / Claude / Responses are enabled by default and can be disabled in `features.formats`. |
| **Multi-instance account pool** | One instance = one account + one BrowserContext + one Page + one surface (chat or build). Instances are declared with `count`, `surface`, and tier selectors; Free and Paid lanes stay isolated. |
| **Tier admission** | `only_tier` / `min_tier` / `max_tier` form a closed interval. Free models only hit Free instances; paid models only hit instances whose account tier matches. Insufficient paid tier returns a local 403 without switching accounts or downgrading. |
| **429 / 403 scheduling** | 429 retries the same account after a short backoff, then wakes or switches eligible pooled instances, bounded by a switch budget. 403 has no hard cooldown: the first failure re-signs and retries in place, the second switches to a healthy account. |
| **Large-context tool calls** | PG-01 thought-signature cache, PG-04 Struct coercion, and PG-08 lenient tool history keep long sessions and tool chains valid against Gemini upstream. |
| **SillyTavern compatibility** | Missing or duplicate tool-call ids, orphan tool outputs, and invalid JSON arguments are normalized instead of producing a 400 when a preset rebuilds the tool channel. |
| **SSE anti-524** | The first event has a bounded pre-read window. After it expires, the server returns 200 and emits `: keepalive` comment frames so gateways such as Cloudflare do not answer 524 for a silent connection. |
| **WAA snapshot warmup** | On the first request, after long idle periods, for large payloads, or when a snapshot fails the quality check, a page-local warmup runs before the one-time signature is fetched. |
| **ATG anti-truncation** | A built-in Anti-Truncation Gateway synthetic tool transport, off by default, can enable `prefer` / `required` per protocol via `mode_by_format`; model settings only drive text/non_text filtering. |
| **Deterministic desktop fingerprint** | A per-account stable Windows desktop screen and GPU (WebGL) fingerprint is injected via CDP before navigation and stays consistent across restarts. |
| **24h keepalive** | Upstream auth and account metadata (tier / email) refresh every 24 hours by default and can be triggered manually from the console. |
| **Persistent state** | Accounts, statistics, 429 cooldowns, model cache, and tool signatures are persisted separately, so service restarts do not lose state. |
| **Web console** | Four pages: dashboard, accounts, settings, and logs. Statistics trends are embedded directly in the dashboard. |
| **UI Debug sandbox** | With `AISTUDIO_UI_DEBUG=1`, a pure in-memory and temp-directory sandbox blocks real browsers, real accounts, outbound requests, and real disk writes. |
| **Lightweight CDP** | Pure Python asynchronous WebSocket access to the Chrome DevTools Protocol, without Node.js, Playwright, or Selenium. |
| **Android companion app** | `android/` is a native Jetpack Compose app with isolated multi-profile login, Bitwarden autofill, and one-click bundle export. |

> [!NOTE]
> **Memory footprint**: The standalone Python backend uses about 35 - 45 MB RAM. With one controlled Chromium instance, total system memory (PSS) is about 350 - 450 MB (~250 - 350 MB on native Linux). At least 1 GB free RAM is recommended on Android Termux.

---

## Protocol Matrix

| Protocol | Method and Endpoint | Details |
|---|---|---|
| **Gemini** | `POST /v1beta/{model}:generateContent` | Non-streaming generation |
| | `POST /v1beta/{model}:streamGenerateContent?alt=sse` | Streaming generation |
| | `POST /v1beta/{model}:countTokens` | Real upstream count, returns `totalTokens` |
| | `GET /v1beta/models`, `GET /v1beta/models/{model}` | Dynamic model discovery |
| **OpenAI Chat** | `POST /v1/chat/completions` | Streaming and non-streaming, tool calls, multimodal input, image output |
| | `GET /v1/models`, `GET /v1/models/{model}` | OpenAI-style model list |
| **Claude** | `POST /v1/messages` | Streaming and non-streaming, tool calls, thinking, images |
| | `POST /v1/messages/count_tokens` | Returns `input_tokens` |
| **OpenAI Responses** | `POST /v1/responses` | Supports `input`, `instructions`, tools, and streaming events |
| | `POST /v1/responses/input_tokens` | Returns `input_tokens` |

None of the three countTokens endpoints consume generation quota or enter generation success-rate and token statistics.

Protocol switches live in `defaults/config.yaml`:

```yaml
features:
  formats:
    openai: true
    claude: true
    responses: true
    media:
      allow_private_urls: false
```

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│ Clients: Gemini SDK / OpenAI SDK / Claude SDK / SillyTavern    │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP / SSE (four protocols)
┌──────────────────────────────▼───────────────────────────────┐
│               FastAPI API layer (default port 8080)           │
│  routes_gemini / routes_openai / routes_claude /              │
│  routes_responses / routes_models / routes_system             │
└──────────────────────────────┬───────────────────────────────┘
                               │ conversion + unified auth
┌──────────────────────────────▼───────────────────────────────┐
│              Application: GenerationService                   │
│  RoutingPipeline (candidates, retries, switching,             │
│  active_requests), SignatureCache (PG-01), ATG, converters    │
└──────────────────────────────┬───────────────────────────────┘
                               │ scheduling and admission
┌──────────────────────────────▼───────────────────────────────┐
│ InstancePool: 1 instance = account + Context + Page + surface │
└──────────────────────────────┬───────────────────────────────┘
                               │ CDP WebSocket
┌──────────────────────────────▼───────────────────────────────┐
│      Controlled CloakBrowser (Chromium) in-page XHR Replay    │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTPS / RPC
┌──────────────────────────────▼───────────────────────────────┐
│                Google AI Studio (alkalimakersuite)            │
└──────────────────────────────────────────────────────────────┘
```

The code is organized in four layers: `api/` (protocols and auth), `application/` (generation, routing, instances, keepalive, ATG), `domain/` (pure models and error contracts), and `infrastructure/` (browser, CDP, wire codecs, accounts, logging). The application layer never imports FastAPI or the API layer.

---

## Instance Pool and Account Scheduling

### Instance definition

- **One instance = one account + one BrowserContext + one Page + one surface**.
- The chat and build surfaces of the same account never coexist. To switch surface, close the instance and rebuild it, or use another account.
- Instances are the scheduling and concurrency unit; Context/Page are implementation details and are not user-configurable.
- Global capacity is limited by `max_contexts` (1 - 16; defaults to the sum of instance `count`, capped at 16).

### Configuration example

```yaml
instances:
  - count: 1
    surface: chat
    only_tier: Free
  - count: 1
    surface: chat
    min_tier: Pro

max_contexts: 2
```

### Tier selectors

At least one of `only_tier` / `min_tier` / `max_tier` is required; semantics are a closed interval:

- `only_tier: Free` normalizes to `Free..Free`; `only_tier: Ultra` expands to `Ultra..Ultra_20X`; `Plus` / `Pro` stay exact.
- A standalone `min_tier: Free` means `Free..Free`; a standalone paid `min_tier` means `min..Ultra_20X`.
- A standalone `max_tier` uses `Pro` as the paid lower bound and must not be lower than `Pro`.
- `min_tier` + `max_tier` requires `min <= max`; Free and paid lanes cannot be mixed.
- Unknown tier names or non-string values fail fast at startup.

### Concurrency and scheduling

- `active_requests` is the only concurrency primitive: success, failure, cancellation, disconnect, and stream close all release it in `finally`.
- **429**: retry the same account after `retry_same_delay_sec` (default 2 seconds); if it still fails, wake or switch to an eligible pooled instance. Consecutive switching is bounded by `max_switches` (default 2), and per-request scheduling retries by `max_retries` (default 5).
- **429 cooldowns**: short rate limit 60 seconds, continuous drip 300 seconds, extended cooldown 600 seconds; all configurable.
- **403 auth**: no hard cooldown. The first 403 re-signs and retries in place; the second 403 switches to a healthy account.
- **Paid tier insufficient (D-04)**: returns a local 403 with `tier/model/required_tier/current_tier`, without switching, downgrading to Free, or using 503.
- **Routing exhausted (D-06)**: returns 503 rendered in the current protocol's error format.
- **In-place rebind**: when an account is disabled or a preset is applied, stop new requests and wait in a bounded way for `active_requests` to reach zero (or force-close on timeout), then close the old Page/Context and switch accounts; no concurrent page is created during the transition.
- After body bytes have been sent, the account is not switched; a streaming response stays on its instance until completion.

### Quota scope

`rotator.quota_scope` supports:

- `account` (default): chat and build share the account quota.
- `account_surface`: chat and build are accounted separately.

This only affects statistics keys, quota display, and 429 cooldown scope; it does not create new instances.

---

## Large-Context Tool Calling and SillyTavern

### PG-01 thought-signature cache

Gemini requires historical `functionCall` parts to carry a `thoughtSignature`. When a client (OpenAI / Claude adapter or a SillyTavern preset) rebuilds the tool channel and loses the signature, the gateway restores it by tool-call id:

- Cache file: `data/signature_cache.json`
- Capacity: 4096 entries; TTL: 30 days
- Reads do not consume entries and can be reused across turns
- A placeholder signature accepted by upstream is used when no real signature exists

### PG-04 Struct coercion

`functionCall` arguments and `functionResponse` results are always coerced into Struct shapes. Invalid string JSON arguments are wrapped into a legal object instead of producing a 400. Logs only emit a sanitized `[toolshape]` summary (argument names, types, lengths) and never prompt or tool content.

### PG-08 lenient tool history

Compatibility normalization for common SillyTavern shapes:

- Missing tool-call ids are synthesized deterministically
- Duplicate ids are deduplicated and renumbered
- Orphan tool outputs are wrapped as legal tool results
- Invalid JSON arguments are preserved and wrapped as Struct
- Logs use the `[pg08]` prefix and never include message bodies

### C1c SSE first packet and heartbeat (anti-524)

- The first-event pre-read window is 15 seconds by default; if an event arrives, streaming starts normally.
- After the window expires, the server returns HTTP 200 and immediately emits `: keepalive\n\n`, then one heartbeat every 15 seconds.
- Once output has started, an upstream failure ends the stream with an SSE error rather than a silent disconnect.
- Environment overrides:
  - `AISTUDIO_SSE_KEEPALIVE_SEC` (default 15)
  - `AISTUDIO_SSE_PREREAD_WINDOW_SEC` (default follows the keepalive interval)

### C1d WAA snapshot warmup

Snapshot warmup is a page-local call and does not depend on switching models. Before fetching the real signature, Python runs a local warmup on the first request, after more than 600 seconds since the last snapshot, when the payload or hash prompt reaches 32 KiB, or when a forced refresh is requested.

Both warmup and the real snapshot call `dms[snapKey](service, hash)` inside the page:

- Only the last 256 Unicode code points of the prompt are written to the composer textarea; the full payload never enters the DOM, while the hash still covers the full prompt.
- Warmup uses a 64-zero placeholder hash and is best-effort; a warmup failure never blocks the real snapshot.
- Snapshot quality requires a length `>=1520` bytes/characters (JS string length) and a leading `!`; a low-quality first result triggers one warmup and retry, and a second failure raises `low_quality_snapshot`.
- Calls are serialized through `aistudio.snapQueue`, and the textarea is cleared in `finally`.

### ATG anti-truncation gateway

ATG extracts model output from the tool channel through a synthetic tool call so long text is not truncated upstream. It is off by default:

```yaml
features:
  atg:
    mode: "off"            # off | prefer | required
    mode_by_format:
      gemini: inherit
      openai: inherit
      claude: inherit
      responses: inherit
    tool_prefix: atg_emit_
    control_position: tail
    strict: false
    empty_retries: 2
    recovery: repair
    client_synthetic_guard: true
```

- `mode` is the global default; `mode_by_format` overrides it per protocol (gemini/openai/claude/responses, where `inherit` follows the global value).
- `text_model_pattern` / `non_text_model_pattern` only drive text/non_text model filtering; there is no per-model mode switch.
- `prefer`: fall back to plain text when the synthetic call is missing.
- `required`: fail explicitly when the synthetic call is missing.
- `client_synthetic_guard`: skip local wrapping when the client already ships an equivalent synthetic transport.

---

## Browser Fingerprint and Keepalive

- **Deterministic fingerprint**: stable Windows desktop screen size, device pixel ratio, WebGL vendor/renderer, and related metrics are generated per account and injected via CDP before navigation. Enabled by default; disable with `browser.fingerprint_spoof_enabled: false`.
- **Low-memory Chromium flags**: single renderer process, in-process GPU, V8 heap limit, ANGLE SwiftShader, and muted audio.
- **24h keepalive**: auth and metadata refresh every 24 hours by default, plus a metadata refresh at startup. Manual trigger is available in the console.
- **429 daily reset**: account model limits reset on the Pacific natural day.
- **Single-page lifecycle**: at most one active Page per account; rebinding uses a bounded drain and never creates a concurrent page.

---

## Quick Start

### Android (Termux)

> [!TIP]
> Install Termux from [F-Droid](https://f-droid.org/packages/com.termux/) or [GitHub Releases](https://github.com/termux/termux-app/releases). Do not use the Google Play build.

```bash
# 1. Update the base environment and install git
pkg update -y && pkg install -y git

# 2. Clone the repository
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api

# 3. Initialize dependencies (mobile mirrors are configured automatically)
bash scripts/setup-env.sh
uv sync

# 4. Install the controlled browser (first run only, about 1-2 minutes)
bash scripts/setup-browser.sh

# 5. Start the service
uv run python main.py server --port 8080
```

Open `http://localhost:8080` and import Cookies or a credential bundle under Accounts. Also tap `Acquire Wakelock` in the Termux notification and disable battery optimization for Termux.

### Windows

Prerequisite: [Python 3.11+](https://www.python.org/downloads/) with `Add python.exe to PATH` checked.

```bat
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api
uv sync
scripts\install-browser.bat
uv run python main.py server --port 8080
```

### Linux / macOS

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api
uv sync
bash scripts/setup-browser.sh
uv run python3 main.py server --port 8080
```

Open `http://localhost:8080` after startup.

### Docker / Compose

The image bundles the CloakBrowser runtime:

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  -e AISTUDIO_WEB_PASSWORD=your-console-password \
  ghcr.io/tonydg233/aistudio-api:2.0.0
```

GHCR provides both the pinned baseline `ghcr.io/tonydg233/aistudio-api:2.0.0` and the rolling `ghcr.io/tonydg233/aistudio-api:latest` tag. With `docker run`, the container initializes `/app/data/config.yaml` from `/app/defaults/config.yaml` on first start.

Before using the repository's root `docker-compose.yml`, you must prepare `config.yaml` on the host; it is bind-mounted over `/app/config.yaml`. If the file does not exist, Docker creates a directory with that name and the entrypoint cannot materialize the template as the `/app/config.yaml` file:

```bash
cp defaults/config.yaml config.yaml
docker compose up -d
```

`docker-compose.deploy.yml` uses a named volume and `/app/data/config.yaml`, so it can initialize from `/app/defaults/config.yaml` on first start. Open `http://localhost:8080` after startup.

---

## Configuration and Environment Variables

### Configuration precedence

1. The path in `AISTUDIO_CONFIG_PATH`
2. The path in `AISTUDIO_CONFIG_FILE` (backward compatible)
3. `config.yaml` in the project root
4. If missing, the file is copied from `defaults/config.yaml`

`defaults/config.yaml` is the only tracked default template and the single source of default values.

### Main configuration blocks

| Block | Purpose |
|---|---|
| `api_keys` / `security` | Public API keys and anonymous access policy |
| `instances` / `max_contexts` | Instance specs and global capacity |
| `rotator` | Retries, 429 cooldowns, quota scope |
| `features.routing` | Consecutive switch budget and same-account 429 backoff |
| `features.keepalive` | 24h auth and metadata refresh cadence |
| `features.model_discovery` | Model catalog refresh intervals |
| `features.formats` | OpenAI / Claude / Responses switches and private media access |
| `features.atg` | Anti-truncation gateway mode and parameters |
| `paid_model_rules` | Paid model tier admission |
| `model_defaults` | Model profiles, default tools, safety settings, parameter filtering |
| `tier_quotas` | Daily soft quotas per tier (display and scheduling weight only) |
| `account_presets` | Account preset schemes |
| `diagnostics` / `browser` / `logging` | Diagnostics, fingerprint, logging |

### Environment variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | API listening port |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | Console password; setting it enables login auth |
| `AISTUDIO_ADMIN_PASSWORD` | string | `""` | Compatibility alias for the console password |
| `AISTUDIO_PROXY` | string | auto | Outbound proxy (`http://`, `https://`, `socks5://`); also reads `HTTPS_PROXY` / `HTTP_PROXY` |
| `AISTUDIO_DATA_DIR` | string | `data/` | Data root (`/app/data` in Docker) |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | Account credential directory |
| `AISTUDIO_STATS_FILE` | string | `data/stats.json` | Usage and token statistics file |
| `AISTUDIO_ROTATOR_STATE_FILE` | string | `data/rotator_state.json` | 429 cooldown and scheduling state |
| `AISTUDIO_MODEL_CACHE_FILE` | string | `data/models_cache.json` | Model catalog snapshot |
| `AISTUDIO_CONFIG_PATH` | string | `config.yaml` | Config file path (highest precedence) |
| `AISTUDIO_CONFIG_FILE` | string | `config.yaml` | Config file path (compatibility) |
| `AISTUDIO_AUTH_FILE` | string | auto | Single-file auth override |
| `AISTUDIO_PERSIST_STATS` | bool | `true` | Persist statistics |
| `AISTUDIO_PERSIST_ROTATOR` | bool | `true` | Persist scheduling state |
| `AISTUDIO_BROWSER_EXECUTABLE` | string | auto | Chromium executable (`/opt/cloakbrowser/chrome` in Docker) |
| `AISTUDIO_BROWSER_PORT` | int | `9222` | CDP debugging port |
| `AISTUDIO_BROWSER_HEADLESS` | bool | `true` | Run headless |
| `AISTUDIO_FINGERPRINT_SPOOF` / `AISTUDIO_FINGERPRINT_SPOOF_ENABLED` | bool | `true` | Browser fingerprint spoofing |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | Default text model |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | string | `gemini-3.1-flash-image-preview` | Default image model |
| `AISTUDIO_TIMEZONE` | string | auto | Browser and wire timezone (falls back to `America/Los_Angeles`) |
| `AISTUDIO_LOCALE` | string | `en-US` | Browser locale |
| `AISTUDIO_TMP_DIR` | string | system temp | Temporary directory |
| `AISTUDIO_SSE_KEEPALIVE_SEC` | float | `15` | SSE heartbeat interval (seconds) |
| `AISTUDIO_SSE_PREREAD_WINDOW_SEC` | float | `15` | First-event pre-read window (seconds) |
| `AISTUDIO_RETRY_SAME_DELAY_SEC` | float | `2.0` | 429 same-account backoff (0 - 30) |
| `AISTUDIO_UI_DEBUG` | bool | `false` | Enable the pure-offline UI debug sandbox |
| `AISTUDIO_HASH_INCLUDE_CODE` | bool | `true` | Diagnostics: include code parts in the snapshot hash |
| `AISTUDIO_DUMP_REQUESTS` | bool | `false` | Print request/response details to the console (also `DEBUG=1`) |
| `AISTUDIO_DUMP_FILE` / `AISTUDIO_DUMP_TO_FILE` | bool | `false` | Write exchanges to disk |
| `AISTUDIO_DUMP_DIR` | string | `data/dumps` | Exchange dump directory |
| `AISTUDIO_DUMP_RAW_RESPONSE` | bool | `false` | Export raw upstream responses |
| `AISTUDIO_DUMP_RAW_RESPONSE_DIR` | string | `data/dumps` | Raw response export directory |

---

## Web Console

Open `http://localhost:8080` (login is required after setting `AISTUDIO_WEB_PASSWORD`). The sidebar has four pages:

1. **Dashboard**: running instances, in-flight requests, account and quota overview, model usage statistics, 7 / 30 / 90-day request and token trend charts, and quick API examples. Statistics are archived by Asia/Shanghai natural day for the last 90 days and distinguish client-request from attempt denominators.
2. **Accounts**: Cookie text and bundle import, sub-account probing, instance slot creation/rebinding/restart, account enablement and grouping, presets, quota and 429 state, and manual keepalive.
3. **Settings**: API key management, security and browser, scheduling and retries, protocol switches and ATG, log configuration, model rules, and YAML source editing with hot reload on save.
4. **Logs**: live SSE log stream, level filtering, search, pause, and 1 / 3 / 7-day export.

### UI Debug sandbox

```bash
AISTUDIO_UI_DEBUG=1 uv run python main.py server --port 8080
```

When enabled:

- Configuration, accounts, statistics, model cache, logs, and dumps are redirected to a temporary directory.
- No real browser starts, no real account is accessed, and no outbound request is made.
- Saving configuration never writes the real `config.yaml`.
- The dashboard and log page expose scenario switches (`all_states`, `mass_accounts`, `all_429`, `empty`) and debug actions that simulate requests or 429 responses.

This sandbox is for UI development and acceptance only; it never touches a real browser or real disk.

---

## Client Integration

### Authentication

Public API requests accept any of:

- Query: `?key=YOUR_API_KEY`
- Header: `x-goog-api-key: YOUR_API_KEY`
- Header: `x-api-key: YOUR_API_KEY`
- Header: `Authorization: Bearer YOUR_API_KEY`

When `api_keys` is empty and `security.api_allow_anonymous` is `false`, all public API requests are rejected. The console password is completely independent from API keys.

### SillyTavern

- OpenAI-compatible connection: Base URL `http://<host>:8080/v1`, API key set to any configured `api_keys` entry.
- Claude-compatible connection: Base URL `http://<host>:8080`, endpoint `/v1/messages`.
- Gemini-compatible connection: Base URL `http://<host>:8080`, API version `v1beta`.

### cURL examples

**Gemini non-streaming**

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Explain quantum computing in one sentence."}]}]
  }'
```

**Gemini streaming**

```bash
curl "http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse" \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Explain Python coroutines."}]}]
  }'
```

**OpenAI Chat**

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

**Claude Messages**

```bash
curl http://localhost:8080/v1/messages \
  -H "x-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

**OpenAI Responses**

```bash
curl http://localhost:8080/v1/responses \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "input": "Hello"
  }'
```

**countTokens**

```bash
# Gemini
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:countTokens \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"parts": [{"text": "hello"}]}]}'

# Claude
curl http://localhost:8080/v1/messages/count_tokens \
  -H "x-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gemini-3.7-flash", "messages": [{"role": "user", "content": "hello"}]}'

# Responses
curl http://localhost:8080/v1/responses/input_tokens \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gemini-3.7-flash", "input": "hello"}'
```

### Python SDK (google-genai)

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={"api_version": "v1beta", "base_url": "http://localhost:8080"},
)

for chunk in client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="What is reactive programming?",
):
    print(chunk.text, end="", flush=True)
```

---

## Development and Testing

The frontend lives in `web/` and uses Vue 3 + TypeScript + Vite + Tailwind CSS with `bun`:

```bash
cd web
bun install
bun run dev          # dev server (default 3000, proxied to 8080)
bun run type-check
bun run lint
bun run build        # output to src/aistudio_api/static
```

Python tooling uses `uv`:

```bash
uv sync --extra dev      # development and test environment
uv run ruff check .
uv run ruff format --check .
uv run pyright src tests
uv run pytest
```

Android checks:

```bash
cd android
./gradlew :app:lintDebug :app:ktlintCheck
```

---

## Documentation

- [System architecture](./docs/ARCHITECTURE.md): four layers, request lifecycle, instance pool, routing, logging.
- [Wire protocol specification](./docs/WIRE_SPECIFICATION.md): Protobuf-JSON array mapping, field indices, multimodal parts, schema encoding.
- [BotGuard verification chain](./docs/BOTGUARD_VERIFICATION_CHAIN.md): WAA challenge handshake, dynamic Wasm signing, content hashing.
- [Instance pool and routing specification](./docs/INSTANCE_POOL_AND_ROUTING_SPEC.md): instances, tier selectors, 429 / 403, surface routing.
- [Keepalive and logging specification](./docs/KEEPALIVE_AND_LOGGING_SPEC.md): 24h keepalive, three log sinks, redaction.
- [Protocol and tool-call specification](./docs/PROTOCOL_AND_TOOLCALL_SPEC.md): four-protocol conversion, PG-01/04/08, ATG, countTokens.
- [Changelog](./docs/changelog.md).
- [Agent and engineering conventions](./AGENT.md).

---

## Acknowledgments

The foundational architecture, CDP communication layer, and reverse-engineered protocol implementation of this project originate from the upstream project [DeconstructedCube/aistudio-api](https://github.com/DeconstructedCube/aistudio-api). Thanks to the upstream author and contributors for their work on Google AI Studio protocol analysis, in-page XHR replay, wire codecs, and the BotGuard verification chain.

---

## License

This project is licensed under the [MIT License](https://opensource.org/license/mit).
