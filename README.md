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
  Google AI Studio 反向代理服务，将 Web 端 AI Studio 统一转换为 <b>Google Gemini (<code>/v1beta</code>)</b>、<b>OpenAI Chat (<code>/v1/chat/completions</code>)</b>、<b>Claude Messages (<code>/v1/messages</code>)</b> 与 <b>OpenAI Responses (<code>/v1/responses</code>)</b> 四种标准 API 协议接口。
</p>

<p align="center">
  <a href="./README_EN.md">English Documentation</a>
</p>

---

## 目录

- [核心特性](#核心特性)
- [技术架构与文档](#技术架构与文档)
- [快速开始](#快速开始)
  - [Docker 部署](#docker-部署)
  - [Android (Termux)](#android-termux)
  - [Windows](#windows)
  - [Linux / macOS](#linux--macos)
- [账号凭据 (Cookie) 获取与导入指引](#账号凭据-cookie-获取与导入指引)
- [第三方客户端配置指引 (SillyTavern 酒馆 / Cherry Studio / Claude Code / NextChat 等)](#第三方客户端配置指引-sillytavern-酒馆--cherry-studio--claude-code--nextchat-等)
- [配置与环境变量](#配置与环境变量)
- [API 调用示例](#api-调用示例)
  - [cURL 调用](#curl-调用)
  - [Python SDK (google-genai)](#python-sdk-google-genai)
  - [工具调用 (Function Calling)](#工具调用-function-calling)
- [Web 管理控制台](#web-管理控制台)
- [开发与测试](#开发与测试)
- [借物表与致谢 (Credits & Acknowledgments)](#借物表与致谢-credits--acknowledgments)
- [开源协议](#开源协议)

---

## 核心特性

| 功能模块 | 说明 |
|---|---|
| **四协议原生兼容** | 同时支持 Gemini `/v1beta`、OpenAI `/v1/chat/completions`、Claude `/v1/messages` 与 OpenAI Responses `/v1/responses`，支持 Thinking 思维链、多模态图文、Function Calling 工具调用与 `countTokens`。 |
| **双引擎热切换（无浏览器 WAA / 受控浏览器）** | 支持轻量化无浏览器 `browserless` 引擎（内置 `waa-signer` SpiderMonkey 152 + Firefox 152 uTLS 指纹，常驻内存仅约 80~150 MB）与 `browser`（CloakBrowser Chromium）引擎在线一键切换。 |
| **Free / Pro 分层实例池与智能防风控轮换** | Free 池与 Pro+ 付费池物理隔离（绝不让免费模型消耗 Pro 账号配额）；内置真实 LRU 全池大循环、**预防性主动轮换（跑满 N 次趁空闲平缓换号，支持按仅 Free / 仅 Pro+ / 全部 / 指定分组框选）**、429 首击退避切号（不做同号二次硬撞）与 `verify-account` 自动熔断隔离。 |
| **长效 Cookie 自动续期与油猴一键提取** | 内置油猴脚本一键抓取含 `LSID` 的跨域长效凭据并导出 `.json`；支持 `/RotateCookies` 自动滚动续期、多登子账号（`u/0..u/N`）自动探测与同源凭据同步。 |
| **ATG 防截断与酒馆深度兼容** | 内置可选的输入/输出双独立开关防截断网关（`wrap_input` 图文双轨分流 + `wrap_output` 合成工具流式解包，支持在管理台自定义提示词模板），自动修复酒馆预设常见的工具历史格式问题与思维签名缓存。 |
| **状态持久化与 Web 管理面板** | 账号、Token 统计、429 冷却、模型缓存自动落盘；Web 控制台提供仪表盘统计趋势、账号与实例池管理、可视化配置热重载与实时 SSE 日志流。 |
| **动态模型发现与参数过滤** | 自动向上游同步可用模型列表，支持按模型规则匹配默认安全设置与过滤不兼容参数，避免下游请求触发 400 报错。 |
| **SSE 首包心跳防超时** | 流式响应内置首包预读窗口与定时心跳保活，防止长思考场景被 Cloudflare 等反向代理网关超时断开（524）。 |
| **确定性指纹与独立鉴权** | 按账号生成稳定的桌面环境指纹，支持独立 API Key 鉴权与管理台访问密码隔离保护。 |
| **Android 伴侣端** | `android/` 提供 Jetpack Compose 原生伴侣应用，支持多 Profile 隔离登录、Bitwarden 自动填充与凭据包导出。 |

> [!NOTE]
> **内存占用参考**：使用 `browserless` 无浏览器引擎时，服务常驻物理内存仅约 80 - 150 MB，冷启动极快；使用 `browser` 受控 Chromium 引擎时，单实例总物理内存约 350 - 450 MB（Linux 宿主约 250 - 350 MB）。

## 技术架构与文档

```text
┌────────────────────────────────────────────────────────┐
│  Client (Gemini / OpenAI / Claude / Responses / 酒馆)   │
└───────────────────────────┬────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼────────────────────────────┐
│              FastAPI HTTP Gateway (Port 8080)          │
├───────────────────────────┬────────────────────────────┤
│  多实例池 & LRU 防风控路由 │  四协议转换 / ATG / Wire   │
└───────────────────────────┼────────────────────────────┘
                            │ 引擎全局热切换
        ┌───────────────────┴───────────────────┐
        │ browserless (WAA)                     │ browser (CloakBrowser)
┌───────▼───────────────────────┐ ┌─────────────▼──────────────────────┐
│ waa-signer (SpiderMonkey 152) │ │ 受控 Chromium + CDP + XHR Replay   │
└───────┬───────────────────────┘ └─────────────┬──────────────────────┘
        └───────────────────┬───────────────────┘
                            │ HTTPS / RPC
┌───────────────────────────▼────────────────────────────┐
│         Google AI Studio (alkalimakersuite-pa)         │
└────────────────────────────────────────────────────────┘
```

详细设计与技术参考文档：

- [系统架构设计 (ARCHITECTURE.md)](./docs/ARCHITECTURE.md)
- [实例池与路由规范 (INSTANCE_POOL_AND_ROUTING_SPEC.md)](./docs/INSTANCE_POOL_AND_ROUTING_SPEC.md)
- [协议适配与工具调用规范 (PROTOCOL_AND_TOOLCALL_SPEC.md)](./docs/PROTOCOL_AND_TOOLCALL_SPEC.md)
- [保活与日志规范 (KEEPALIVE_AND_LOGGING_SPEC.md)](./docs/KEEPALIVE_AND_LOGGING_SPEC.md)
- [Wire 协议逆向技术规范 (WIRE_SPECIFICATION.md)](./docs/WIRE_SPECIFICATION.md)
- [BotGuard 验证链路机制 (BOTGUARD_VERIFICATION_CHAIN.md)](./docs/BOTGUARD_VERIFICATION_CHAIN.md)
- [WAA 虚拟机内部架构 (WAA_VM_ARCHITECTURE.md)](./docs/WAA_VM_ARCHITECTURE.md)
- [更新日志 (changelog.md)](./docs/changelog.md)
- [智能体协作与开发规范 (AGENT.md)](./AGENT.md)

---

## 快速开始

### Docker 部署

> [!NOTE]
> 官方 Docker 镜像已同时内置编译好的 `waa-signer`（无浏览器引擎）与 `CloakBrowser`（受控浏览器引擎），**开箱即用**，无需任何额外构建步骤，并可在管理台在线一键切换引擎。如需在国内环境或通过宿主机代理访问 Google，请追加 `-e AISTUDIO_PROXY=http://host.docker.internal:7890`（Windows/macOS）或宿主机代理地址。

使用 `docker run` 一键启动：

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  -e AISTUDIO_WEB_PASSWORD=your-console-password \
  ghcr.io/tonydg233/aistudio-api:latest
```

使用 Docker Compose 启动（无需任何前置准备，直接一键启动；建议使用 `Docker Compose v2.24+`）：

```bash
# 1) 默认部署：首次启动自动在 ./data/config.yaml 生成默认配置
docker compose up -d

# 2) 生产独立部署（使用 GHCR 预构建镜像，默认标签 latest，可固定版本）
AISTUDIO_IMAGE_TAG=2.1.4 docker compose -f docker-compose.deploy.yml up -d

# 3) 基于本地源码重新构建镜像后启动（需先取消 docker-compose.yml 中 build: . 的注释）
docker compose up -d --build
```

> [!NOTE]
> - **从旧版升级**：若从旧版仓库根目录的 `./config.yaml` 升级，请在启动前执行 `mkdir -p data && mv config.yaml data/config.yaml`；若自定义 compose 仍将旧配置挂载到 `/app/config.yaml`，`docker-entrypoint.sh` 亦会自动将其迁移至 `/app/data/config.yaml`。
> - **可选环境变量文件**：在仓库根目录创建 `.env` 后，其中的全部环境变量（如 `AISTUDIO_SSE_KEEPALIVE_SEC`、`AISTUDIO_DEFAULT_TEXT_MODEL`）会自动注入容器；不存在 `.env` 时不报错、也不会创建空目录。

---

### Android (Termux)

> [!TIP]
> 请务必从 [F-Droid](https://f-droid.org/packages/com.termux/) 或 [GitHub Releases](https://github.com/termux/termux-app/releases) 安装 Termux，**切勿使用 Google Play 版本**。

打开 Termux，依次执行以下命令：

```bash
# 步骤 1：更新基础环境并安装 git
pkg update -y && pkg install -y git

# 步骤 2：克隆代码仓库并进入目录
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api

# 步骤 3：初始化依赖环境并同步安装
bash scripts/setup-env.sh
uv sync

# 步骤 4：启动服务
uv run python main.py server --port 8080
```

<details>
<summary><b>【进阶可选】安装受控浏览器或编译本地原生签名服务 (waa-signer)</b></summary>

- 若使用默认 `browser`（受控 Chromium）引擎，首次需安装受控浏览器（约 1-2 分钟）：
  ```bash
  bash scripts/setup-browser.sh
  ```
- 若切换至轻量级 `browserless` 无浏览器引擎，推荐编译本地 `waa-signer`（详见 [waa-signer 编译说明](./waa-signer/README.md)）：
  ```bash
  pkg install -y golang zig
  bash waa-signer/build.sh
  ```
</details>

> [!TIP]
> **后台保活提示**：下拉手机通知栏点击 Termux 的 `Acquire Wakelock`，并在系统电池设置中允许 Termux 无限制后台运行，防止锁屏被杀后台。

---

### Windows

前置要求：已安装 [Git](https://git-scm.com/)。打开 PowerShell，依次执行：

```powershell
# 步骤 1：安装 uv 包管理工具
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 步骤 2：克隆代码仓库并进入目录
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api

# 步骤 3：同步安装项目依赖（uv 会自动管理 Python 环境）
uv sync

# 步骤 4：启动服务
uv run python main.py server --port 8080
```

<details>
<summary><b>【进阶可选】安装受控浏览器或一键编译本地签名服务 (waa-signer.exe)</b></summary>

- 若使用 `browser`（受控 Chromium）引擎，首次运行安装脚本：
  ```bat
  scripts\install-browser.bat
  ```
- 若切换至 `browserless` 无浏览器引擎，编译本地 `waa-signer.exe`：
  ```powershell
  winget install GoLang.Go zig.zig
  .\waa-signer\build.bat
  ```
</details>

---

### Linux / macOS

打开终端，依次执行：

```bash
# 步骤 1：安装 uv 包管理工具
curl -LsSf https://astral.sh/uv/install.sh | sh

# 步骤 2：克隆代码仓库并进入目录
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api

# 步骤 3：同步安装项目依赖
uv sync

# 步骤 4：启动服务
uv run python3 main.py server --port 8080
```

<details>
<summary><b>【进阶可选】安装受控浏览器或编译本地签名服务 (waa-signer)</b></summary>

- 安装受控浏览器（`browser` 模式）：
  ```bash
  bash scripts/setup-browser.sh
  ```
- 编译本地原生签名服务（`browserless` 模式，需 Go 1.27+ 与 `zig` / `clang` / `gcc`）：
  ```bash
  bash waa-signer/build.sh
  ```
</details>

> [!IMPORTANT]
> **服务启动后访问 `http://localhost:8080` 进入管理台，按下方指引①生成 API Key、②导入 Google 账号凭据即可开始使用。**

---

## 账号凭据 (Cookie) 获取与导入指引

本服务不保存 Google 密码，仅通过 Web 会话 Cookie 发起调用，并内置自动滚动续期机制。

### 方式一（推荐，长效防掉线）：使用内置油猴脚本导出 `.json` 凭据包

1. **安装脚本**：浏览器安装 [Tampermonkey（篡改猴）](https://www.tampermonkey.net/) 扩展后，打开本服务管理台 **「账号管理 → + 导入凭据」** 点击安装抓取脚本（或直接访问 `http://localhost:8080/api/tools/aistudio-cookie-grabber.user.js`）。
2. **无痕提取**：在电脑浏览器打开一个**全新的无痕/隐私窗口**并登录 [Google AI Studio](https://aistudio.google.com/)，点击页面右下角脚本面板的 **「导出 JSON 凭据包」**（自动打包 `accounts.google.com` 等跨域长效根凭据）。
3. **拖拽导入**：回到管理台 **「账号管理 → + 导入凭据」**，将 `.json` 文件直接拖入弹窗完成导入。

> [!WARNING]
> 提取完成后**直接关闭无痕窗口即可，切勿点击 Google 网页内的“退出登录”按钮**，否则已导出的根 Cookie 会立即作废。

### 方式二：使用 Cookie-Editor 扩展导出导入

1. 在浏览器中登录 [Google AI Studio](https://aistudio.google.com/)，确保能够正常打开对话工作台；
2. 安装浏览器扩展 **Cookie-Editor**（支持 Chrome / Edge / Firefox）；
3. 在 `aistudio.google.com` 标签页下点击 Cookie-Editor 图标，选择 **Export → Export as JSON**（或 Export as Header）；
4. 回到管理台 **「账号管理 → + 导入凭据」**，将剪贴板内容粘贴并确认导入，系统会自动校验连通性并探测多登子账号（`u/0`, `u/1`...）。

### 步骤 3：在管理台配置对外 API Key

本服务默认开启 API 鉴权保护（未配置 Key 时会拒绝外部 API 请求）。首次使用请进入管理台 **「系统与模型配置 → API 密钥管理」**，添加至少一个自定义 API Key（如 `sk-my-secret-key`）并保存。

---

## 第三方客户端配置指引 (SillyTavern 酒馆 / Cherry Studio / Claude Code / NextChat 等)

本服务原生兼容四种主流协议，请根据客户端类型选择对应配置：

| 协议模式 | 适用客户端示例 | Base URL / 接口地址 | API Key |
|---|---|---|---|
| **OpenAI 兼容模式** | SillyTavern 酒馆 (OpenAI 源)、Cherry Studio、Cursor、NextChat | Base URL：`http://localhost:8080/v1`<br/>接口路径：`/v1/chat/completions` | 管理台「API 密钥管理」中设置的 Key |
| **Google Gemini 原生模式** | SillyTavern 酒馆 (Gemini 源)、Cherry Studio (Gemini 源)、`google-genai` SDK | Base URL：`http://localhost:8080`<br/>（若客户端要求带版本则填 `http://localhost:8080/v1beta`） | 管理台「API 密钥管理」中设置的 Key |
| **Anthropic Claude 原生模式** | SillyTavern 酒馆 (Claude 源)、Claude Code | Base URL：`http://localhost:8080`<br/>接口路径：`/v1/messages` | 管理台「API 密钥管理」中设置的 Key |
| **OpenAI Responses 模式** | Codex CLI、OpenAI Responses 客户端 | Base URL：`http://localhost:8080/v1`<br/>接口路径：`/v1/responses` | 管理台「API 密钥管理」中设置的 Key |

**常用模型名称参考**：
- 免费账号可用：`gemini-4-hydrogen`、`gemini-3.8-flash`
- 需 Pro 及以上账号：`gemini-4-argon`、`gemini-3.1-pro-preview`
- 需 Ultra 账号：`gemini-4-carbon`
- 图像生成：`gemini-3.1-flash-image-preview`

> [!TIP]
> **进阶优化建议（均可在「系统与模型配置」页面可视化开启）**：
> - **预防性主动轮换**：推荐将主动轮换范围设为 **「仅 Free 池」**，单号连续成功调用满 N 次后趁空闲自动换号，降低高频连续请求触发风控的概率。
> - **ATG 防截断**：SillyTavern 酒馆长文输出容易被上游中断时，可按协议按需开启 ATG（支持独立控制 `wrap_input` / `wrap_output` 与提示词模板）。

---

## 配置与环境变量

配置文件加载优先级：`AISTUDIO_CONFIG_PATH` > `AISTUDIO_CONFIG_FILE` > 项目根目录 `config.yaml`（缺失时自动从 `defaults/config.yaml` 复制）。大部分设置均可在 Web 控制台修改并热重载。

### 常用环境变量

| 变量名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | API 服务监听端口 |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | Web 控制台访问密码（留空则免密，与 API Key 完全独立） |
| `AISTUDIO_PROXY` | string | 自动探测 | 出口代理地址，支持 `http://`、`https://`、`socks5://` |
| `AISTUDIO_DATA_DIR` | string | `data/` | 主数据持久化根目录（Docker 下为 `/app/data`） |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | 账号凭据存储目录 |
| `AISTUDIO_CONFIG_PATH` | string | `config.yaml` | 配置文件路径 |
| `AISTUDIO_STATS_FILE` | string | `data/stats.json` | 模型调用与 Token 统计文件 |
| `AISTUDIO_ROTATOR_STATE_FILE` | string | `data/rotator_state.json` | 账号 429 冷却与调度状态文件 |
| `AISTUDIO_MODEL_CACHE_FILE` | string | `data/models_cache.json` | 模型目录缓存文件 |
| `AISTUDIO_PERSIST_STATS` | bool | `true` | 是否持久化调用与 Token 统计 |
| `AISTUDIO_PERSIST_ROTATOR` | bool | `true` | 是否持久化 429 冷却与调度状态 |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | 默认文本模型 |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | string | `gemini-3.1-flash-image-preview` | 默认图像模型 |
| `AISTUDIO_TIMEZONE` | string | 自动探测 | 浏览器与协议交互时区（回退 `America/Los_Angeles`） |
| `AISTUDIO_SSE_KEEPALIVE_SEC` | float | `15` | 流式 SSE 心跳包发送间隔（秒） |
| `AISTUDIO_RETRY_SAME_DELAY_SEC` | float | `2.0` | 上游瞬态错误（204/404/408/5xx）同账号重试前退避秒数 |
| `AISTUDIO_SWITCH_BACKOFF_SECONDS` | float | `4.0` | 429/风控切号前退避基准秒数（实际带 ±25% 随机抖动，默认 3~5 秒） |

---

## API 调用示例

支持以下任一鉴权方式：
- Query 参数：`?key=YOUR_API_KEY`
- 请求头：`x-goog-api-key: YOUR_API_KEY`、`x-api-key: YOUR_API_KEY` 或 `Authorization: Bearer YOUR_API_KEY`

### cURL 调用

<details>
<summary><b>1. 查询模型列表 (Gemini / OpenAI)</b></summary>

```bash
# Gemini 模型列表
curl http://localhost:8080/v1beta/models \
  -H "x-goog-api-key: your-api-key"

# OpenAI 模型列表
curl http://localhost:8080/v1/models \
  -H "Authorization: Bearer your-api-key"
```
</details>

<details>
<summary><b>2. Gemini 非流式 / 流式生成</b></summary>

```bash
# 非流式
curl http://localhost:8080/v1beta/models/gemini-3.8-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "用一句话介绍量子计算。"}]}]
  }'

# 流式 (SSE)
curl "http://localhost:8080/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse" \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "介绍一下 Python 协程。"}]}]
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
    "messages": [{"role": "user", "content": "你好"}],
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
    "messages": [{"role": "user", "content": "你好"}]
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
    "input": "你好"
  }'
```
</details>

<details>
<summary><b>6. Token 计数 (countTokens)</b></summary>

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

# 1. 流式响应
response = client.models.generate_content_stream(
    model="gemini-3.8-flash",
    contents="解释一下什么是反应式编程？",
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 2. 非流式响应
result = client.models.generate_content(
    model="gemini-3.8-flash",
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
    model="gemini-3.8-flash",
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

系统内置静态打包的 Web 管理控制台（访问 `http://localhost:8080`）：

- **控制面板 (Dashboard)**：实时展示实例池运行状态、在途请求、账号与额度概览、7 / 30 / 90 天请求与 Token 趋势图表以及快捷测试工具。
- **账号管理 (Accounts)**：支持 Cookie 文本与油猴 `.json` 凭据包批量导入、多登子账号自动探测、实例槽位管理、分组与预设方案、429 状态与保活手动触发。
- **系统与模型配置 (Settings)**：API 密钥管理、请求引擎热切换（`browser` / `browserless`）、主动轮换策略、协议开关与 ATG 配置、模型规则与 YAML 源文编辑，保存即热重载。
- **日志 (Logs)**：实时 SSE 日志流、级别过滤、关键词搜索、暂停与 1 / 3 / 7 天日志导出。
- **访问鉴权**：设置环境变量 `AISTUDIO_WEB_PASSWORD` 即可开启控制台登录保护；与对外 API Key 完全独立。

---

## 开发与测试

前端源码位于 `web/`（Vue 3 + TypeScript + Vite + Tailwind CSS）：

```bash
cd web
bun install
bun run dev          # 启动开发服务（localhost:3000，代理至 8080）
bun run type-check   # 执行 TypeScript 类型校验
bun run lint         # 执行 ESLint 检查
bun run build        # 生产构建，产物输出至 src/aistudio_api/static
```

Python 端质量检查与测试（统一使用 `uv`）：

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pyright src tests
uv run pytest
```

Android 伴侣端检查：

```bash
cd android
./gradlew :app:lintDebug :app:ktlintCheck
```

---

## 借物表与致谢 (Credits & Acknowledgments)

本项目在基础架构、逆向工程、签名算法与协议实现过程中，参考并借鉴了以下优秀的开源项目与社区成果，在此致以诚挚的感谢：

- **[DeconstructedCube/aistudio-api](https://github.com/DeconstructedCube/aistudio-api)**：提供了项目基础架构、轻量 CDP 通信层、浏览器内 XHR Replay 与 Wire 编解码核心实现。
- **[Mag1cFall/AIStudio2API](https://github.com/Mag1cFall/AIStudio2API)**：提供了嵌入式 SpiderMonkey 152 运行时与 Firefox 152 DOM / WebIDL 宿主现场实现，以及基于 uTLS 的网络栈设计，`waa-signer` 服务基于其核心逻辑适配抽离。
- **[Lianues/aistudio-pg-bridge](https://github.com/Lianues/aistudio-pg-bridge)**：开创了脱离本地浏览器的轻量化 WAA 证明签名服务思路，并确立了 `/v1/proof/mint` 接口规范。
- **[spinsnow](https://waa.spinsnow.fun)**：提供了公开稳定的 Node.js VM WAA 签名中继服务与环境参考。
- **[Camoufox](https://github.com/daijro/camoufox)** 与 **[go-browserforge](https://github.com/brainplusplus/go-browserforge)**：提供了真实浏览器环境指纹数据与反指纹追踪生成算法。

---

## 开源协议

本项目基于 [MIT License](https://opensource.org/license/mit) 许可协议开源。
