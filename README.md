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
  Google AI Studio 反向代理服务，将 Web 端 AI Studio 转换为标准 Google Gemini API 协议接口。
</p>

<p align="center">
  <a href="./README_EN.md">English Documentation</a>
</p>

---

## 目录

- [核心特性](#核心特性)
- [技术架构与文档](#技术架构与文档)
- [快速开始](#快速开始)
  - [Android (Termux)](#android-termux)
  - [Windows](#windows)
  - [Linux / macOS](#linux--macos)
  - [Docker 部署](#docker-部署)
- [配置与环境变量](#配置与环境变量)
- [API 调用示例](#api-调用示例)
  - [cURL 调用](#curl-调用)
  - [Python SDK (google-genai)](#python-sdk-google-genai)
  - [工具调用 (Function Calling)](#工具调用-function-calling)
- [Web 管理控制台](#web-管理控制台)
- [开发与测试](#开发与测试)
- [本仓库的定位（与上游差异）](#本仓库的定位与上游差异)
- [致谢](#致谢)
- [开源协议](#开源协议)

---

## 核心特性

| 功能模块 | 说明 |
|---|---|
| **Gemini 协议兼容** | 支持 `/v1beta/...` 接口规范，包括 Thinking 思维链、多模态、Function Calling 工具调用及图片生成 |
| **OpenAI Chat Completions 兼容** | 提供 `/v1/chat/completions` 与 `/v1/models`，支持流式、工具调用、图片与 `reasoning_content`；默认开启 |
| **OpenAI Responses 兼容** | 提供 `/v1/responses`，支持流式事件、工具调用、reasoning 与图片；usage 透出 input/output/reasoning/cache 明细；默认开启 |
| **Anthropic Messages 兼容** | 提供 `/v1/messages` 与 `/v1/messages/count_tokens`，支持流式事件、工具调用、thinking、图片与真实上游计数 |
| **动态模型发现** | 自动向上游同步可用模型列表与 Paid/Pro-only 元数据，支持 `gemini-3.7-flash`、`gemini-3.8-flash` 等最新模型；快照持久化 + 启动预热，刷新失败回退上次快照 |
| **多账号自动轮换** | 支持多账号管理与故障自动切换，按模型独立记录 429 配额，每日美西午夜自动重置 |
| **实例与渠道（WP5）** | 实例 = 账号 + 渠道的独立用量窗口；同一账号的 PG/Build 在总实例上限允许时是两个实例、共享一个浏览器上下文；实例总数由 `max_contexts` 限制，默认单实例顺序切换，`max_contexts>=3` 时 2c3p（3 实例）也成立；渠道切换替换单页，不新增 context |
| **Build 双路径** | 原生 9998 客户端为默认路径；原生不可用时由顶层 signer 直连兜底；两条路径独立健康状态，signer 失败不阻塞原生渠道 |
| **账号双保活（WP10）** | 定时切号（长周期均衡 + 真实页面活动）与自动刷新 Auth（cookies/origins 回写 `auth.json`）并存；另有 24h tier/email/过期元数据刷新，三者独立开关 |
| **运行状态持久化** | Token 统计 v2 (`stats.json`：按模型/日聚合，client/attempt 两层计数，cache/reasoning/count_tokens 分离，保留 90 天日桶) 与账号 429 冷却 (`rotator_state.json`) 自动落盘，重启不丢状态 |
| **下游参数容错与丢弃** | 支持配置 `drop_unsupported_params` 自动过滤下游传入的非标准安全分类（如 `HARM_CATEGORY_CIVIC_INTEGRITY`）与不兼容工具，避免 400 阻断 |
| **批量子账号探活** | 导入单个包含多个 Google 身份的 Cookie 后，自动递归探活子账号（`u/0`, `u/1`...）并分别建档 |
| **内置搜索与工具** | 支持 Google Search 联网搜索、Google Maps、代码执行沙箱等官方扩展能力 |
| **轻量 CDP 驱动** | 基于纯 Python 异步 WebSocket 直连 Chrome DevTools Protocol，无需 Node.js、Playwright 或 Selenium |
| **Web 管理面板** | 提供 Web 管理控制台，支持账号管理（文本与 JSON 批量导入）、独立用量统计页 `/stats`（Token/缓存/趋势/账号调度状态）、在线编辑与热重载 `config.yaml` |
| **Android 伴侣端** | 配套 Android 原生应用（`android/`），采用 Jetpack Compose 构建，支持多 Profile 隔离登录、Bitwarden 自动填充与一键导出凭据包 |
> [!NOTE]
> **内存占用参考**：纯 Python 后端常驻约 80 MB Working Set。实测 2 个实例（其中一个为 Build 渠道，Private Working Set 求和）平均约 454 MB、峰值约 523 MB；同批 WorkingSet 求和约 908 MB，仅作对比，不作为主指标。Android Termux 环境建议空闲 RAM ≥ 1 GB。

## 技术架构与文档

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

详细的设计与协议分析文档：

- [系统架构设计 (ARCHITECTURE.md)](./docs/ARCHITECTURE.md)：分层架构、请求生命周期、Wire Codec 编解码与并发控制模型。
- [Wire 协议逆向技术规范 (WIRE_SPECIFICATION.md)](./docs/WIRE_SPECIFICATION.md)：Protobuf-JSON 数组映射、字段索引、多模态 Part 结构与 Schema 编码。
- [BotGuard 验证链路机制 (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md)：WAA 挑战握手、Wasm 动态签名、内容哈希与服务端校验流程。
- [智能体与协作规范 (AGENT.md)](./AGENT.md)：类型系统要求、代码质量检查与开发指南。

---

## 快速开始

### Android (Termux)

> [!TIP]
> **下载提示**：请务必从 [F-Droid](https://f-droid.org/packages/com.termux/) 或 [GitHub Releases](https://github.com/termux/termux-app/releases) 安装 Termux。**切勿使用 Google Play 商店版本**（Play 商店版本已停止维护，更新环境会报错）。

打开 Termux，依次复制执行以下命令：

```bash
# 步骤 1：更新 Termux 基础环境并安装 git
pkg update -y && pkg install -y git

# 步骤 2：克隆代码仓库并进入目录
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# 步骤 3：初始化依赖环境（自动配置手机专属加速源，无需本地编译）
bash scripts/setup-env.sh
uv sync

# 步骤 4：安装并配置受控浏览器（仅首次需要，耗时约 1~2 分钟）
bash scripts/setup-browser.sh

# 步骤 5：启动服务
uv run python main.py server --port 8080
```

启动成功后：
1. 打开手机自带浏览器，访问 **`http://localhost:8080`** 即可进入 Web 管理控制台；
2. 点击 **“账号管理”** 页面，粘贴你的 Google 账号 Cookie 或导入凭据包即可开始调用；
3. **后台保活提示**：下拉手机通知栏，点击 Termux 通知项上的 `Acquire Wakelock`，并在手机系统设置中为 Termux 开启“无限制后台运行/电池优化白名单”，防止锁屏休眠被系统误杀。

---

### Windows

前置要求：电脑已安装 [Python 3.11+](https://www.python.org/downloads/)（安装时勾选 `Add python.exe to PATH`）。

打开 PowerShell 或终端，依次执行：

```bat
# 步骤 1：安装 uv 包管理工具
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 步骤 2：克隆代码仓库并进入目录
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# 步骤 3：同步安装项目依赖
uv sync

# 步骤 4：安装 CloakBrowser 浏览器（仅首次需要）
scripts\install-browser.bat

# 步骤 5：启动服务
uv run python main.py server --port 8080
```

服务启动后，浏览器访问 **`http://localhost:8080`** 进入管理控制台。

---

### Linux / macOS

打开终端，依次执行：

```bash
# 步骤 1：安装 uv 包管理工具
curl -LsSf https://astral.sh/uv/install.sh | sh

# 步骤 2：克隆代码仓库并进入目录
git clone https://github.com/DeconstructedCube/aistudio-api.git
cd aistudio-api

# 步骤 3：同步安装项目依赖
uv sync

# 步骤 4：安装 CloakBrowser 浏览器（仅首次需要）
bash scripts/setup-browser.sh

# 步骤 5：启动服务
uv run python3 main.py server --port 8080
```

服务启动后，浏览器访问 **`http://localhost:8080`** 进入管理控制台。

---

### Docker 部署

镜像已内置 CloakBrowser 运行时，支持开箱即用：

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/deconstructedcube/aistudio-api:latest
```

使用 Docker Compose 启动：

```bash
docker compose up -d
```

启动后访问 **`http://localhost:8080`**（或服务器 IP 端口）进入管理控制台。

---

## 配置与环境变量

服务启动后可访问 `http://localhost:8080` 进入 Web 控制台完成可视化管理。

### 环境变量支持

| 变量名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | API 服务监听端口 |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | Web 控制台访问密码（亦可设置 `AISTUDIO_ADMIN_PASSWORD`） |
| `AISTUDIO_PROXY` | string | `""` | 出口代理地址，支持 `http://`、`https://` 或 `socks5://` |
| `AISTUDIO_DATA_DIR` | string | `data/` | 主数据持久化根目录（Docker 下为 `/app/data`） |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | 账号持久化凭据目录 |
| `AISTUDIO_STATS_FILE` | string | `data/stats.json` | 模型调用与 Token 使用量统计持久化文件（schema v2） |
| `AISTUDIO_MODEL_CACHE_FILE` | string | `data/models_cache.json` | 模型发现快照（含 Paid 元数据与 fetched_at），用于启动恢复和预热失败兜底 |
| `AISTUDIO_ROTATOR_STATE_FILE` | string | `data/rotator_state.json` | 账号调度与 429 冷却状态持久化文件 |
| `AISTUDIO_CONFIG_FILE` | string | `config.yaml` | 模型规则与默认工具配置文件路径 |
| `AISTUDIO_PERSIST_STATS` | bool | `true` | 是否持久化模型调用与 Token 统计（1=开启，0=关闭） |
| `AISTUDIO_PERSIST_ROTATOR` | bool | `true` | 是否持久化账号 429 冷却与调度状态（1=开启，0=关闭） |
| `AISTUDIO_BROWSER_EXECUTABLE` | string | 自动探测 | 自定义 CloakBrowser 可执行文件路径（Docker 内为 `/opt/cloakbrowser/chrome`） |
| `AISTUDIO_BROWSER_PORT` | int | `9222` | Chromium 远程调试端口 (CDP) |
| `AISTUDIO_BROWSER_HEADLESS` | bool | `true` | 是否以无头模式运行 Chromium |
| `AISTUDIO_BROWSER_IDLE_TIMEOUT` | int | `900` / `1800` | 浏览器空闲自动回收超时秒数（Termux 下默认 900s，桌面端 1800s，0 为禁用） |
| `AISTUDIO_PROOT_NAME` | string | `aistudio-api` | Termux 环境下专用的 proot-distro 容器名称 |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | 默认文本模型 |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | string | `gemini-3.1-flash-image-preview` | 默认图像生成模型 |
| `AISTUDIO_TIMEZONE` | string | 自动探测 | 受控浏览器指纹与 Wire 协议交互时区（默认 `America/Los_Angeles`） |
| `AISTUDIO_LOCALE` | string | `en-US` | 受控浏览器区域语言 |
| `AISTUDIO_DUMP_REQUESTS` | bool | `false` | 是否在终端转储每次 API 交互报文详情（支持 `DEBUG=1` 触发） |
| `AISTUDIO_DUMP_FILE` | bool | `false` | 是否将请求与响应报文详情落盘保存 |
| `AISTUDIO_DUMP_DIR` | string | `dumps/` | 报文转储保存目录 |
| `AISTUDIO_LOG_DIR` | string | `data/logs/` | 按天归档日志目录（`aistudio-YYYYMMDD.log`，自动保留最近 7 天） |
| `AISTUDIO_BUILD_SURFACE` | bool | `false` | Build 页面直连面总开关（free 档账号走 Build 直连通道） |
| `AISTUDIO_APP_URL` | string | 空 | Build 应用 URL/App ID 回退；`channels.build.app_url` 为空时使用 |
| `AISTUDIO_BUILD_TRANSPORT` | enum | `native` | Build 传输偏好：`native`（默认）/`signer`；signer 不可用时回退原生 |
| `AISTUDIO_ATG_MODE` | string | `off` | ATG 防截断模式：`off` / `prefer` / `required` |
| `AISTUDIO_ACCOUNT_REFRESH_CONCURRENCY` | int | `3` | 批量账号信息刷新并发工作池大小 |
| `AISTUDIO_ROUTE_BY_TIER` | bool | `false` | 是否按账号档位（tier）路由到不同的面 |
| `AISTUDIO_AUTO_SWITCH` | bool | `false` | 定时自动切换账号总开关 |
| `AISTUDIO_AUTO_SWITCH_INTERVAL_HOURS` | float | `3.0` | 定时自动切换周期间隔（小时，最小 `0.1`） |
| `AISTUDIO_KEEPALIVE_AUTH_UPDATE` | bool | `true` | 自动刷新 Auth：把 cookies/origins 回写 `auth.json` |
| `AISTUDIO_KEEPALIVE_AUTH_UPDATE_HOURS` | float | `24.0` | 凭证回写周期（小时，最小 `0.1`） |
| `AISTUDIO_KEEPALIVE_METADATA_REFRESH` | bool | `true` | 自动刷新 tier/email/过期元数据到 `meta.json` |
| `AISTUDIO_KEEPALIVE_METADATA_REFRESH_HOURS` | float | `24.0` | 元数据刷新周期（小时，最小 `0.1`） |
| `AISTUDIO_KEEPALIVE_METADATA_REFRESH_ON_STARTUP` | bool | `true` | 启动时补刷缺失/过期元数据 |
| `AISTUDIO_KEEPALIVE_ACTIVE_ACTIVITY` | bool | `false` | 活跃页轻量活动（开启后浏览器不会被空闲回收） |
| `AISTUDIO_KEEPALIVE_ACTIVE_ACTIVITY_SECONDS` | float | `60.0` | 活跃页活动间隔（秒，最小 `10`） |

### 功能开关（features）

> [!IMPORTANT]
> **两处开关的关系与优先级**：`config.yaml` 顶层的 `features:` 段是**运行时权威配置**（可在 Web 控制台可视化编辑并经 `PUT /config/yaml` **热重载**，无需重启）；上表中的 `AISTUDIO_*` 环境变量仅是**启动时的基线兜底**（进程启动读取一次）。
> 优先级为：**`config.yaml features.*` > `AISTUDIO_*` 环境变量 > 内置默认值**。即：`features` 段缺省某项时使用环境变量值，环境变量也未设置时使用内置默认。编排/fallback/活动类开关默认关闭（`fallback` 组默认全关，遵循「零自动回落」决策）；WP10 凭证与账号信息保活默认开启，与 Ark 的 `ENABLE_AUTH_UPDATE` 对齐。

`config.yaml` `features:` 段字段一览（Web 控制台「模型规则与安全策略」页面提供自解释可视化编辑）：

| 字段 | 类型 | 默认值 | 说明（开=什么 / 关=什么 / 何时用 / 失败怎么办） |
|---|---|---|---|
| `features.build_surface` | bool | `false` | 开=free 档账号走 Build 宿主页直连通道；关=仅聊天面（零行为变化）。何时开：需要免费档走 Build 额度。失败：面内失败显式报错，绝不改道。 |
| `features.channels.build.app_url` | string | 空 | Build 应用 URL 或 App ID；优先于 `AISTUDIO_APP_URL`；为空回退内置默认 |
| `features.routing.max_contexts` | int | `1` | 用户可见总实例上限（1-16）；总实例数 = 该值，默认单实例；`max_contexts=1` 时 PG/Build 顺序切换，不能并行开两个 page |
| `features.routing.global_max_pages` | int | `2` | 兼容保留；实际全局页面预算 = `max_contexts`，实例切换不得突破 |
| `features.route_by_tier` | bool | `false` | 开=按账号档位分发到允许的面；关=始终聊天面默认路由。何时开：WP5 v3 分组编排已落地，需要区分 pro/free 的面或额度池时。失败：无匹配组按默认策略并记录决策链。 |
| `features.account_refresh_concurrency` | int | `3` | 批量刷新 email/tier/过期的并发数（范围 1-16）。越大越快但越易限流。失败：单账号失败不影响其余。 |
| `features.auto_switch` | bool | `false` | 开=按间隔周期轮换账号；关=仅按用量/失败/429 触发。失败：无可切换账号时保持当前账号并告警。 |
| `features.auto_switch_interval_hours` | float | `3.0` | 自动切换间隔（小时，最小 0.1，与 Ark 一致）。非法值回退 3.0。 |
| `features.keepalive_auth_update` | bool | `true` | 开=按周期/切号/初始化/重连/关闭前把浏览器 cookies/origins 原子合并写回 `auth.json`；关=仅保留自然回写。此开关不写 meta。 |
| `features.keepalive_auth_update_hours` | float | `24.0` | 凭证回写周期（小时，最小 0.1）。仅作用于当前活跃账号；非活跃账号在 `auto_switch` 轮转到它时回写。 |
| `features.keepalive_metadata_refresh` | bool | `true` | 开=自动刷新 tier/email/expired 到 `registry.json`/`meta.json`；与手动刷新共用同一刷新器和 409 互斥。 |
| `features.keepalive_metadata_refresh_hours` | float | `24.0` | 账号信息刷新周期（小时，最小 0.1）。 |
| `features.keepalive_metadata_refresh_on_startup` | bool | `true` | 启动时只补刷缺失/过期账号，不阻塞启动。 |
| `features.keepalive_active_activity` | bool | `false` | 开=周期滚动页面并刷新空闲计时器；代价是浏览器不会被空闲回收。凭证保活不依赖此开关。 |
| `features.keepalive_active_activity_seconds` | float | `60.0` | 活跃页活动间隔（秒，最小 10）。 |
| `features.formats.openai` | bool | `true` | 开=提供 `/v1/chat/completions` 与 `/v1/models`；关=对应路由显式 403。默认开启（对齐 Ark 无条件端点注册）。 |
| `features.formats.claude` | bool | `true` | 开=提供 `/v1/messages` 与 `/v1/messages/count_tokens`；关=对应路由显式 403。 |
| `features.formats.responses` | bool | `true` | 开=提供 `/v1/responses` 与 `/v1/responses/input_tokens`；关=对应路由显式 403。 |
| `features.formats.media.allow_private_urls` | bool | `false` | 开=允许代理下载私网/回环图片 URL；关=拒绝，防 SSRF。`data:` URL 不受影响。 |
| `features.atg.mode_by_format` | map | `inherit` | 按协议面覆盖 ATG：`gemini/openai/claude/responses` 分别取 `inherit`（跟随 `features.atg.mode`）或 `off/prefer/required`。 |
| `features.atg.mode` | select | `off` | `off`=禁用（零改动）/ `prefer`=尽力合成、忽略则回退纯文本 / `required`=缺失合成调用即显式失败。 |
| `features.atg.tool_prefix` | string | `atg_emit_` | 合成工具名前缀（须稳定以利过滤与历史清理）。留空回退默认。 |
| `features.atg.control_position` | select | `tail` | 控制提示注入位置：`tail`=对话末尾（推荐）/ `head`=开头。非法值回退 `tail`。 |
| `features.atg.strict` | bool | `false` | 开=合成参数异常即判失败；关=允许宽松解析（默认）。 |
| `features.atg.empty_retries` | int | `2` | 空/塌陷响应最大重试（0-5）。越大兜底越强但耗额度。达上限仍失败则按 recovery 处理。 |
| `features.atg.recovery` | select | `repair` | `repair`=确定性修复后解包（默认）/ `fail`=直接显式失败。 |
| `features.atg.side_buffer_bytes` | int | `0` | 流式旁路缓冲字节（0=立即透传，最大 16MB）。超限即透传。 |
| `features.atg.client_synthetic_guard` | bool | `true` | 开=客户端已自带「经工具回正文」机制时跳过本地包装，防双通道叠加（默认）；关=无条件包装。 |
| `features.atg.text_model_pattern` | string | `""` | 文本模型识别正则；留空用内置默认，非法正则自动回退。 |
| `features.atg.non_text_model_pattern` | string | `""` | 非文本模型识别正则（优先判定，命中即跳过 ATG）；留空用内置默认。 |
| `features.fallback.on_quota` | bool | `false` | 开=配额耗尽允许自动回落改道；关=显式报错（默认）。 |
| `features.fallback.on_failure` | bool | `false` | 开=请求失败允许自动回落；关=显式报错（默认）。 |
| `features.fallback.cross_surface` | bool | `false` | 开=允许 chat↔build 跨面回落；关=面内失败不改道（默认）。 |

### 账号信息刷新

`POST /api/accounts/refresh`（别名 `POST /accounts/refresh`，需管理端鉴权）：无浏览器直连探测账号档位（tier）与登录态，并回填邮箱（对齐上游 Ark 的 `AccountInfoRefresher`，零模型额度）。

- 请求体：`{"account_ids": ["acc_xxx"], "all": false, "refresh_email": true}`；`account_ids` 与 `all=true` 二选一，均缺省 → `400`
- 响应：`{results: [{id, ok, tier, expired, email, error, checked_at}], refreshed_count, expired_count}`
  - `tier`：`free` / `pro` / `ultra` / `plus` / `unknown-N`
  - `expired`：`true`（401 已标记过期）/ `false`（有效）/ `null`（非权威失败，如 5xx 或网络故障——账号状态未被改动）
- 并发：同一时间仅允许一次刷新（重复触发 → `409`）；工作池大小由 `features.account_refresh_concurrency`（默认 3）控制
- 被标记 `expired` 的账号会被自动排除出账号调度，直至刷新成功或重新导入 cookies；Web 账号页提供 tier/过期徽章与单个、批量刷新按钮

### 账号保活（WP10，纯后端）

三条独立机制，可分别开关：

1. **定时切号** `features.auto_switch`：按间隔把 active 账号轮换到 `auth_saved_at` 最久未回写的可用账号；切号前保存旧账号 Auth，切后保存新账号 Auth；仅 chat + build 均无在途请求时触发。
2. **自动刷新 Auth** `features.keepalive_auth_update`：等价于 Ark `ENABLE_AUTH_UPDATE`；在切号前、初始化、重连、周期到期、关闭前，把浏览器 cookies/origins 原子合并写回 `auth.json`。**不写 meta**。
3. **自动更新账号信息** `features.keepalive_metadata_refresh`：周期把 tier/email/expired/tier_checked_at 写回 `registry.json`/`meta.json`；与 `POST /api/accounts/refresh` 共用同一刷新器，重叠时返回 `409`。

运维接口（需管理端鉴权）：

- `GET /api/keepalive`：返回开关、间隔、下次运行、最近一次 refresh/auth_update/auto_switch 结果、chat/build 在途数。
- `POST /api/keepalive/run`：手动触发 `metadata_refresh` / `auth_update` / `auto_switch` / `active_activity`，用于验收与排障；仍受互斥和在途检查约束。
- `PUT /config/features`：结构化更新 `features` 并原子落盘、热重载，供后续 WP7 UI 使用。
- `GET /logs/stream`：SSE 实时日志流（`level`、`last_id` / `Last-Event-ID`，先回放最近 100 条，15s 心跳，最多 8 个订阅者）。
- `GET /logs/export`：导出最近 7 天日志归档；`start`/`end`（ISO8601）或 `days`，单日为 `.log`，跨天为 `.zip`。

> 配置双写：本地运行配置是根 `config.yaml`（不被 git 追踪）；分发默认配置是 `defaults/config.yaml`。修改 features 时必须两处同步，提交以 `defaults/config.yaml` 为准。

### ATG 防截断（synthetic transport）

`features.atg.mode` 控制 ATG（Anti-Truncation Gateway）抗截断通道：`off`=零行为变化；`prefer`=尽力经合成工具回传完整正文，模型忽略指令时原样返回；`required`=必须经合成通道，上游缺失合成调用即**显式失败**（绝不静默）。请求侧自动注入合成工具声明与控制指令（客户端已自带同类机制时自动跳过，见 `client_synthetic_guard`）；响应侧自动解包——合成工具调用还原为正文（含参数修复与流式增量累积），思考链保留、真实工具调用不受影响。

- 观测：运行日志 grep `[ATG]`（注入/跳过原因 + 解包 stats：`syntheticHit` / `syntheticCount` / `realToolCount` / `repaired` / `conflict`）
- 合成参数修复上限：`features.atg.max_response_bytes`（默认 16MB，超限判失败）
- 空/塌陷响应自动重发至多 `features.atg.empty_retries` 次（默认 2，不换号）

---

## API 调用示例

支持以下鉴权方式：
- Query 参数：`?key=YOUR_API_KEY`
- 请求头：`x-goog-api-key: YOUR_API_KEY`、`x-api-key: YOUR_API_KEY` 或 `Authorization: Bearer YOUR_API_KEY`

### cURL 调用

<details>
<summary><b>1. 查询模型列表</b></summary>

```bash
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-api-key"
```
</details>

<details>
<summary><b>2. 文本生成 (Non-streaming)</b></summary>

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "用简短的一句话介绍量子计算。"}]
      }
    ]
  }'
```
</details>

<details>
<summary><b>3. 流式生成 (Server-Sent Events)</b></summary>

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "介绍一下 Python 协程的工作原理。"}]
      }
    ]
  }'
```
</details>

### OpenAI Chat Completions

Base URL 使用 `http://localhost:8080/v1`，模型名从 `GET /v1/models` 获取。

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "messages": [{"role": "user", "content": "用一句话介绍量子计算。"}],
    "max_tokens": 256
  }'
```

OpenAI Python SDK：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="your-api-key",
)
resp = client.chat.completions.create(
    model="gemini-3.7-flash",
    messages=[{"role": "user", "content": "用一句话介绍量子计算。"}],
    max_tokens=256,
)
```

### OpenAI Responses

Base URL 使用 `http://localhost:8080/v1`，完整端点为 `/v1/responses`。

```bash
curl http://localhost:8080/v1/responses \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "input": "用一句话介绍量子计算。",
    "max_output_tokens": 256
  }'
```

OpenAI Python SDK：

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="your-api-key")
resp = client.responses.create(
    model="gemini-3.7-flash",
    input="用一句话介绍量子计算。",
    max_output_tokens=256,
)
```

预检计数：

```bash
curl http://localhost:8080/v1/responses/input_tokens \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gemini-3.7-flash", "input": "用一句话介绍量子计算。"}'
```

### Anthropic Messages

Base URL 使用 `http://localhost:8080`（Anthropic SDK 会自行拼接 `/v1/messages`）。

```bash
curl http://localhost:8080/v1/messages \
  -H "x-api-key: your-api-key" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "用一句话介绍量子计算。"}]
  }'
```

Anthropic Python SDK：

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://localhost:8080",
    api_key="your-api-key",
)
msg = client.messages.create(
    model="gemini-3.7-flash",
    max_tokens=256,
    messages=[{"role": "user", "content": "用一句话介绍量子计算。"}],
)
```

> [!NOTE]
> Gemini / OpenAI Chat / OpenAI Responses / Anthropic 四种格式默认开启；如被运维关闭，会返回 403 并在错误信息中提示对应 `features.formats.*` 配置键。
>
> 计数端点使用上游真实 tokenizer，但当前实现会把 system/tools/对话打包成聚合 prompt 后调用 MakerSuite `CountTokens`；与原生结构化请求相比约有 +2.8%~+8.6% 的误差，适合 agent 上下文长度参考，不作为逐 token 精确值。`/v1/messages/count_tokens`、`/v1/responses/input_tokens`、Gemini `:countTokens` 均已实现。

---

### Python SDK (google-genai)

使用 Google 官方 `google-genai` SDK 直连本代理服务：

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:8080",
    },
)

# 1. 流式响应
response = client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="解释一下什么是反应式编程？",
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 2. 非流式响应
result = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="用 Python 写一个快速排序算法。",
)
print(result.text)
```

---

### 工具调用 (Function Calling)

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

# 定义工具声明
weather_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_current_weather",
            description="获取指定地点的实时天气状况",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "location": types.Schema(
                        type="STRING",
                        description="城市或地区名称，例如 'Beijing' 或 'Tokyo'",
                    ),
                    "unit": types.Schema(
                        type="STRING",
                        enum=["celsius", "fahrenheit"],
                        description="温度单位",
                    ),
                },
                required=["location"],
            ),
        )
    ]
)

response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="今天杭州天气怎么样？",
    config=types.GenerateContentConfig(tools=[weather_tool]),
)

if response.function_calls:
    for call in response.function_calls:
        print(f"触发工具调用: {call.name}")
        print(f"调用参数: {call.args}")
```

---

## Web 管理控制台

系统内置静态打包的 Web 前端管理控制台（访问 `http://localhost:8080`）：

- **仪表盘 (Dashboard)**：实时展示服务健康状况、各模型请求量、成功率及 429 冷却指标。
- **用量统计 (Stats, `/stats`)**：模型调用（上游 attempts，含重试/换号，不含 count_tokens）为主指标；客户端请求为次级；Token 计数独立展示且不计入模型调用。支持按时间/模型查看输入、输出、推理、缓存读写与命中率，以及账号调度状态与 Paid/Pro-only 标记。数据接口为 `GET /api/stats`、`GET /api/stats/timeseries`。
- **账号管理 (Accounts)**：支持 Cookie 单条与凭据包批量导入、自动探测多账号、手动切换当前活跃账号或重置冷却状态。
- **系统设置 (Settings)**：在线编辑 `config.yaml`（支持配置 profiles 与 models 默认规则），保存后服务端即时热重载。
- **访问鉴权**：设置环境变量 `AISTUDIO_WEB_PASSWORD` 后自动开启登录鉴权守卫。

---

## 开发与测试

前端源码位于 `web/`，采用 Vue 3 + TypeScript + Vite + Tailwind CSS：

```bash
# 启动前端开发调试服务
cd web
bun install
bun run dev          # 启动开发服务（localhost:3000，代理至 8080）
bun run type-check   # 执行 TypeScript 类型校验
bun run lint         # 执行 ESLint 代码规范检查
bun run build        # 生产构建，产物输出至 src/aistudio_api/static
```

Python 端质量检查与测试：

```bash
# 执行代码风格与 Lint 检查
uv run ruff check .
# 执行静态类型检查
bun x pyright

# 执行单元测试套件
uv run pytest
```

Android 伴侣端代码质量检查：

```bash
# 执行 Kotlin 代码规范检查 (已配置于 ~/.zshrc)
ktlint "android/app/src/main/java/**/*.kt"
```

---

## 本仓库的定位（与上游差异）

本仓库是 [`DeconstructedCube/aistudio-api`](https://github.com/DeconstructedCube/aistudio-api) 的下游分支，面向低内存长期运行（连续十几天）与多实例调度做了深度改造。相对上游，主要差异如下：

| 方向 | 本仓库实现 |
|---|---|
| **多实例化** | 以「实例 = 账号 + 渠道」作为调度与用量单位：同一账号的 PG/Build 在总实例上限允许时是两个实例、各自独立用量窗口，但共享同一个浏览器 context（`max_instances = max_contexts`，2c1p/1c2p 以及 `max_contexts>=3` 时的 2c3p 都只是实例组合，不能超过总上限） |
| **渠道生命周期** | 支持按渠道单独关闭/回收；仅当最后一个实例退出时才回收 context，避免误伤另一渠道的会话 |
| **Build 通道** | 原生 9998 直连为主；页内 signer（本地签名绕行）作为 Build 失灵时的冗余方案，signer 失败不阻塞原生通道。signer 采用结构探测（`kvc` / `snapshot` / `content`）与 `tokenLike`（getMetadata / H / A.snapshot）定位签名材料 |
| **Web 控制台重构** | 实例切换面板、实例总览与账号矩阵、账号多模式管理与批量导入、日志 SSE + 7 天归档导出、独立统计页与分区配置页；UI/API 不暴露 context/page/slot 等实现概念 |
| **统计口径** | 以模型调用（requests/attempts，含重试与换号）为主指标，客户端请求为次级；`count_*` 独立展示且不计入模型调用；用量按渠道/实例分别统计 |
| **长期运行约束** | 默认单实例；总页面/实例预算由 `max_contexts`（1-16）控制；静态资源 `no-cache, must-revalidate`；按需预热，优先保证低内存 VPS 上的稳定性 |

## 致谢

本仓库的技术方案建立在以下项目之上，特此致谢：

- [`DeconstructedCube/aistudio-api`](https://github.com/DeconstructedCube/aistudio-api)：本仓库的**上游源方案**。现有的多协议面（Gemini / OpenAI / Anthropic / Responses）、BotGuard 验证链、动态模型发现、账号 tier/邮箱探测与会话重导航等能力均来自该仓库及其维护者，本仓库持续合并其更新。
- [`iBUHub/AIStudioToAPI`](https://github.com/iBUHub/AIStudioToAPI)：**Build 通道方案的提供者**。本仓库的 Build 面（App 宿主、wire 帧协议、任务提交与结果回收）参考了其公开实现，并在此基础上增加了本地 signer 绕行与原生直连的冗余路径。
- 谱系与自有研究资料：`chrysoljq/aistudio-api`（原仓）→ `DeconstructedCube/aistudio-api` → 本仓库；Build 通道、直连签名器与 bundle 考古的移交文档见 [`docs/ark-reference/`](./docs/ark-reference/README.md)（已脱敏）。

以上项目的代码与文档版权归各自作者所有；本仓库仅作引用与致谢，不复述任何凭证或密钥材料。

---

## 开源协议

本项目基于 [MIT License](./LICENSE) 许可协议开源。
