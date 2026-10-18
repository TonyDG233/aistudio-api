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
- [开源协议](#开源协议)

---

## 核心特性

| 功能模块 | 说明 |
|---|---|
| **Gemini 协议兼容** | 支持 `/v1beta/...` 接口规范，包括 Thinking 思维链、多模态、Function Calling 工具调用及图片生成 |
| **动态模型发现** | 自动向上游同步可用模型列表，支持 `gemini-3.7-flash`、`gemini-3.8-flash` 等最新模型 |
| **多账号自动轮换** | 支持多账号管理与故障自动切换，按模型独立记录 429 配额，每日美西午夜自动重置 |
| **运行状态持久化** | 模型调用量、Token 消耗统计 (`stats.json`) 以及账号 429 限额与冷却状态 (`rotator_state.json`) 自动落盘，容器/服务重启不丢失状态 |
| **下游参数容错与丢弃** | 支持配置 `drop_unsupported_params` 自动过滤下游传入的非标准安全分类（如 `HARM_CATEGORY_CIVIC_INTEGRITY`）与不兼容工具，避免 400 阻断 |
| **批量子账号探活** | 导入单个包含多个 Google 身份的 Cookie 后，自动递归探活子账号（`u/0`, `u/1`...）并分别建档 |
| **内置搜索与工具** | 支持 Google Search 联网搜索、Google Maps、代码执行沙箱等官方扩展能力 |
| **轻量 CDP 驱动** | 基于纯 Python 异步 WebSocket 直连 Chrome DevTools Protocol，无需 Node.js、Playwright 或 Selenium |
| **Web 管理面板** | 提供 Web 管理控制台，支持账号管理（文本与 JSON 批量导入）、调用量监控、在线编辑与热重载 `config.yaml` |
| **Android 伴侣端** | 配套 Android 原生应用（`android/`），采用 Jetpack Compose 构建，支持多 Profile 隔离登录、Bitwarden 自动填充与一键导出凭据包 |
> [!NOTE]
> **内存占用参考**：纯 Python 后端服务常驻内存约 35 - 45 MB；拉起单实例受控 Chromium 后总物理内存 (PSS) 约 350 - 450 MB（宿主 Linux 下约 250 - 350 MB）。Android Termux 环境建议空闲 RAM ≥ 1 GB。

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
| `AISTUDIO_STATS_FILE` | string | `data/stats.json` | 模型调用与 Token 使用量统计持久化文件 |
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
| `AISTUDIO_BUILD_SURFACE` | bool | `false` | Build 页面直连面总开关（free 档账号走 Build 直连通道） |
| `AISTUDIO_ATG_MODE` | string | `off` | ATG 防截断模式：`off` / `prefer` / `required` |
| `AISTUDIO_ACCOUNT_REFRESH_CONCURRENCY` | int | `3` | 批量账号信息刷新并发工作池大小 |
| `AISTUDIO_ROUTE_BY_TIER` | bool | `false` | 是否按账号档位（tier）路由到不同的面 |
| `AISTUDIO_AUTO_SWITCH` | bool | `false` | 定时自动切换账号总开关 |
| `AISTUDIO_AUTO_SWITCH_INTERVAL_HOURS` | float | `3.0` | 定时自动切换周期间隔（小时，最小 `0.1`） |

### 功能开关（features）

> [!IMPORTANT]
> **两处开关的关系与优先级**：`config.yaml` 顶层的 `features:` 段是**运行时权威配置**（可在 Web 控制台可视化编辑并经 `PUT /config/yaml` **热重载**，无需重启）；上表中的 `AISTUDIO_*` 环境变量仅是**启动时的基线兜底**（进程启动读取一次）。
> 优先级为：**`config.yaml features.*` > `AISTUDIO_*` 环境变量 > 内置默认值**。即：`features` 段缺省某项时使用环境变量值，环境变量也未设置时使用内置默认。所有开关默认保守关闭（`fallback` 组默认全关，遵循「零自动回落」决策）。

`config.yaml` `features:` 段字段一览（Web 控制台「模型规则与安全策略」页面提供自解释可视化编辑）：

| 字段 | 类型 | 默认值 | 说明（开=什么 / 关=什么 / 何时用 / 失败怎么办） |
|---|---|---|---|
| `features.build_surface` | bool | `false` | 开=free 档账号走 Build 宿主页直连通道；关=仅聊天面（零行为变化）。何时开：需要免费档走 Build 额度。失败：面内失败显式报错，绝不改道。 |
| `features.route_by_tier` | bool | `false` | 开=按账号档位分发到允许的面；关=始终聊天面默认路由。何时开：分组编排（WP5）落地后。失败：无匹配组按默认策略并记录决策链。 |
| `features.account_refresh_concurrency` | int | `3` | 批量刷新 email/tier/过期的并发数（范围 1-16）。越大越快但越易限流。失败：单账号失败不影响其余。 |
| `features.auto_switch` | bool | `false` | 开=按间隔周期轮换账号；关=仅按用量/失败/429 触发。失败：无可切换账号时保持当前账号并告警。 |
| `features.auto_switch_interval_hours` | float | `3.0` | 自动切换间隔（小时，最小 0.1，与 Ark 一致）。非法值回退 3.0。 |
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

## 开源协议

本项目基于 [MIT License](./LICENSE) 许可协议开源。
