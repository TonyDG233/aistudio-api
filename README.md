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
  Google AI Studio 反向代理网关，将 Web 端 AI Studio 统一转换为 Gemini、OpenAI、Claude 与 OpenAI Responses 四种标准协议。
</p>

<p align="center">
  <a href="./README_EN.md">English Documentation</a>
</p>

---

## 目录

- [核心特性](#核心特性)
- [协议矩阵](#协议矩阵)
- [技术架构](#技术架构)
- [多实例池与账号调度](#多实例池与账号调度)
- [双引擎（Browser / Browserless）](#双引擎browser--browserless)
- [大上下文工具调用与 SillyTavern](#大上下文工具调用与-sillytavern)
- [浏览器指纹与保活](#浏览器指纹与保活)
- [快速开始](#快速开始)
  - [Android (Termux)](#android-termux)
  - [Windows](#windows)
  - [Linux / macOS](#linux--macos)
  - [Docker / Compose](#docker--compose)
- [配置与环境变量](#配置与环境变量)
- [Web 管理台](#web-管理台)
- [客户端接入](#客户端接入)
- [开发与测试](#开发与测试)
- [相关文档](#相关文档)
- [致谢 (Acknowledgments)](#致谢-acknowledgments)
- [开源协议](#开源协议)

---

## 核心特性

| 功能模块 | 说明 |
|---|---|
| **四协议统一网关** | 同一套协议中立生成管线对外提供 Gemini `/v1beta`、OpenAI `/v1/chat/completions`、Claude `/v1/messages` 与 OpenAI Responses `/v1/responses`。Gemini 常驻开启，OpenAI / Claude / Responses 默认开启，可在 `features.formats` 中显式关闭。 |
| **多实例账号池** | 1 实例 = 1 账号 + 1 BrowserContext + 1 Page + 1 surface（chat 或 build）。实例按 `count`、`surface` 与 tier 选择器声明，Free 与 Paid lane 相互隔离。 |
| **双引擎热切换** | `browser`（CloakBrowser/CDP）与 `browserless`（内置 waa-signer，无 Chromium）全局二选一；设置页保存后执行 Validate → Preflight → Drain → Destroy → Rebuild → Commit，失败自动回滚，切换期间新请求返回 503 + `Retry-After`。 |
| **内置 WAA 签名器** | browserless 模式由 waa-signer 以 SpiderMonkey 152 执行 BotGuard 挑战，并通过 uTLS/HTTP2/HTTP3 直接发送受保护 RPC；支持本地子进程自动拉起或显式远程 endpoint。 |
| **Tier 精准准入** | `only_tier` / `min_tier` / `max_tier` 组成闭区间；Free 模型只命中 Free 实例，付费模型只命中满足 tier 的实例。付费模型 tier 不足返回本地 403，不切号、不降级。 |
| **429 / 403 弹性调度** | 429 优先同账号短退避重试，再唤醒或切换池内实例；连续切号有上限。403 不做硬冷却，首次重新签名并原位重试，第二次才切换健康账号。 |
| **大上下文工具调用** | PG-01 思维签名缓存、PG-04 工具载荷 Struct 强制、PG-08 宽松工具历史兼容，保障长会话与工具链在 Gemini 上游稳定落地。 |
| **SillyTavern 兼容** | 对缺失或重复 tool call id、孤立工具输出、非法 JSON 参数做宽松归一，避免酒馆预设重建工具通道时触发 400。 |
| **SSE 防 524** | 首事件有界预读，超时后先返回 200 并持续发送 `: keepalive` 注释帧，避免 Cloudflare 等网关因长时间无字节返回 524。 |
| **WAA 快照预热** | 首次请求、长时间闲置、超大 payload 或快照质量不足时，先在页面内执行本地快照暖机，再获取一次性签名。 |
| **ATG 防截断** | 内置 Anti-Truncation Gateway 合成工具网关，默认关闭，可通过 `mode_by_format` 按协议启用 `prefer` / `required`；模型配置仅用于 text/non_text 过滤。 |
| **确定性桌面指纹** | 按账号确定性生成 Windows 桌面屏幕与显卡（WebGL）指纹，并在页面导航前通过 CDP 注入；同账号多次启动保持一致。 |
| **24h 保活** | 默认每 24 小时刷新一次上游鉴权与账号元数据（tier / email），并支持在管理台手动触发。 |
| **状态持久化** | 账号、统计、429 冷却、模型缓存与工具签名分别落盘；容器或服务重启不丢状态。 |
| **Web 管理台** | 控制面板、账号管理、系统与模型配置、日志四个页面；统计趋势直接融合在控制面板。 |
| **UI Debug 离线沙盒** | 设置 `AISTUDIO_UI_DEBUG=1` 后使用纯内存与临时目录沙盒，100% 阻断真实浏览器、真实账号、外网请求与真实磁盘写入。 |
| **轻量 CDP 驱动** | 纯 Python 异步 WebSocket 直连 Chrome DevTools Protocol，不依赖 Node.js、Playwright 或 Selenium。 |
| **Android 伴侣端** | `android/` 提供 Jetpack Compose 原生应用，支持多 Profile 隔离登录、Bitwarden 自动填充与一键导出凭据包。 |

> [!NOTE]
> **内存占用参考**：纯 Python 后端常驻内存约 35 - 45 MB；拉起单实例受控 Chromium 后总物理内存（PSS）约 350 - 450 MB（宿主 Linux 下约 250 - 350 MB）。Android Termux 环境建议空闲 RAM ≥ 1 GB。

---

## 协议矩阵

| 协议面 | 方法与端点 | 说明 |
|---|---|---|
| **Gemini** | `POST /v1beta/{model}:generateContent` | 非流式生成 |
| | `POST /v1beta/{model}:streamGenerateContent?alt=sse` | 流式生成 |
| | `POST /v1beta/{model}:countTokens` | 上游真实计数，返回 `totalTokens` |
| | `GET /v1beta/models`、`GET /v1beta/models/{model}` | 动态模型发现 |
| **OpenAI Chat** | `POST /v1/chat/completions` | 支持流式与非流式、工具调用、多模态与图片输出 |
| | `GET /v1/models`、`GET /v1/models/{model}` | OpenAI 风格模型列表 |
| **Claude** | `POST /v1/messages` | 支持流式与非流式、工具调用、thinking 与图片 |
| | `POST /v1/messages/count_tokens` | 返回 `input_tokens` |
| **OpenAI Responses** | `POST /v1/responses` | 支持 `input`、`instructions`、工具与流式事件 |
| | `POST /v1/responses/input_tokens` | 返回 `input_tokens` |

三种 countTokens 都不消耗账号生成配额，也不计入生成成功率与生成 token 统计。

协议开关位于 `defaults/config.yaml`：

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

## 技术架构

```text
┌──────────────────────────────────────────────────────────────┐
│  客户端：Gemini SDK / OpenAI SDK / Claude SDK / SillyTavern   │
└──────────────────────────────┬───────────────────────────────┘
                               │ HTTP / SSE（四协议）
┌──────────────────────────────▼───────────────────────────────┐
│                FastAPI API 层（默认端口 8080）                │
│  routes_gemini / routes_openai / routes_claude /              │
│  routes_responses / routes_models / routes_system             │
└──────────────────────────────┬───────────────────────────────┘
                               │ 协议转换 + 统一鉴权
┌──────────────────────────────▼───────────────────────────────┐
│              Application：GenerationService                  │
│  RoutingPipeline（候选、重试、切号、active_requests）         │
│  SignatureCache（PG-01）、ATG、FormatConverters              │
└──────────────────────────────┬───────────────────────────────┘
                               │ 调度与准入
┌──────────────────────────────▼───────────────────────────────┐
│   InstancePool：1 实例 = 1 账号 + 1 Context + 1 Page + 1 surface │
└──────────────────────────────┬───────────────────────────────┘
                               │ 引擎全局二选一
        ┌──────────────────────┴───────────────────────┐
        │ browser（默认）                               │ browserless
┌───────▼──────────────────────────────┐  ┌────────────▼──────────────────────────────┐
│ 受控 CloakBrowser（Chromium）          │  │ 内置 waa-signer（SpiderMonkey 152）        │
│ 页面内 XHR Replay + CDP                │  │ Proof 铸造 + uTLS/HTTP2/HTTP3 原生 RPC     │
└───────┬──────────────────────────────┘  └────────────┬──────────────────────────────┘
        └──────────────────────┬───────────────────────┘
                               │ HTTPS / RPC
┌──────────────────────────────▼───────────────────────────────┐
│                Google AI Studio（alkalimakersuite）           │
└──────────────────────────────────────────────────────────────┘
```

代码按四层组织：`api/`（协议与鉴权）、`application/`（生成、路由、实例、保活、ATG）、`domain/`（纯模型与错误契约）、`infrastructure/`（浏览器、CDP、Wire 编解码、账号与日志）。Application 层不导入 FastAPI，也不依赖 API 层。

---

## 多实例池与账号调度

### 实例定义

- **1 实例 = 1 账号 + 1 BrowserContext + 1 Page + 1 surface**。
- 同一账号的 chat 与 build 不会同时存活；需要切换 surface 时关闭当前实例再重建，或改用其他账号。
- 实例是调度与并发单位；Context/Page 是实现细节，不作为用户可配置项。
- 全局容量由 `max_contexts` 限制（1 - 16，缺省等于所有实例 `count` 之和，超过按 16 截断）。

### 配置示例

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

### Tier 选择器

`only_tier` / `min_tier` / `max_tier` 至少提供一个，语义为闭区间：

- `only_tier: Free` 归一为 `Free..Free`；`only_tier: Ultra` 展开为 `Ultra..Ultra_20X`；`Plus` / `Pro` 保持精确。
- 单独 `min_tier: Free` 为 `Free..Free`；单独付费 `min_tier` 为 `min..Ultra_20X`。
- 单独 `max_tier` 的付费下界默认为 `Pro`，且上界不得低于 `Pro`。
- `min_tier` + `max_tier` 要求 `min <= max`；Free 与付费 lane 不得混合。
- 未知 tier 或非字符串值会在启动时 fail-fast。

### 并发与调度

- `active_requests` 是唯一的并发原语：成功、失败、取消、断连与流关闭都在 `finally` 释放。
- **429**：先同账号等待 `retry_same_delay_sec`（默认 2 秒）后重试；仍失败则唤醒或切换池内合格实例；连续切号受 `max_switches`（默认 2）限制，单请求调度重试上限为 `max_retries`（默认 5）。
- **429 冷却**：短时限流 60 秒、持续滴流 300 秒、延长冷却 600 秒，可按配置调整。
- **403 鉴权**：不设置硬冷却；首次 403 重新签名并原位重试，第二次 403 才切换到健康账号。
- **付费模型 tier 不足（D-04）**：返回本地 403，错误体包含 `tier/model/required_tier/current_tier`，不切号、不降级到 Free、不以 503 表达。
- **路由耗尽（D-06）**：返回 503，并按当前协议面渲染对应错误结构。
- **原位换绑**：账号禁用或应用预设时，先停止新请求并有界等待 `active_requests` 归零（或超时强制关闭），再关闭旧 Page/Context 并切换账号；期间不新建并发页面。
- 已输出正文后不再切号；流式响应在正文开始后保持当前实例直至完成。

### Quota scope

`rotator.quota_scope` 支持：

- `account`（默认）：chat 与 build 共用账号额度。
- `account_surface`：chat 与 build 分开记账。

该开关只影响统计键、配额展示与 429 冷却口径，不新增实例。

---

## 双引擎（Browser / Browserless）

### 引擎边界

全局仅二选一，由 `engine.mode` 指定，默认 `browser`：

- **browser**：1 实例 = 1 账号 + 1 BrowserContext + 1 Page + 1 surface，使用受控 CloakBrowser（Chromium）与页面内 XHR Replay。
- **browserless**：1 实例 = 1 账号 + 1 BrowserlessSession + 1 surface，不启动 Chromium；每个槽位独立持有 Cookie、确定性指纹、proof 铸造器与传输器，严禁跨槽共享会话或响应头。

两种引擎共享同一套协议面、路由、账号、统计、保活与日志；切换引擎不改变实例槽位数量与 tier 选择器。

### 设置页热切换

在 Web 管理台的「系统与模型配置」页保存 `engine.mode` 后，后端按事务执行：

1. **Validate**：校验目标引擎配置与实例拓扑；非法配置返回 422。
2. **Preflight**：先用新引擎建立一次可用会话；失败返回 503，旧引擎继续服务。
3. **Drain**：有界等待在途请求结束，超时则中止并回滚。
4. **Destroy**：关闭旧槽位与旧引擎（切离 browser 时一并关闭 CloakBrowser 进程）。
5. **Rebuild**：按新引擎重建原槽位拓扑并重新绑定账号。
6. **Commit**：提交新配置与引擎 epoch；任一步失败自动回滚旧引擎与旧配置。

切换窗口内新请求返回 503 并带 `Retry-After`；`GET /api/system/status`、`GET /api/instances` 与 `POST /api/engine/health-check` 暴露引擎、切换阶段与探活结果。

### WAA 签名器：本地与远程

browserless 的证明签名由 waa-signer 完成，支持两种部署方式：

```yaml
engine:
  mode: browserless
  browserless:
    signer:
      mode: local_subprocess      # local_subprocess | remote_http
      endpoint: http://127.0.0.1:8081
      binary_path: waa-signer/bin/waa-signer
      auto_start: true
      state_dir: data/waa/workers
      max_workers: 8
      idle_ttl_s: 900
      locale: en-US
      timezone: America/Los_Angeles
```

- `local_subprocess`：`binary_path` 相对仓库根目录解析；`auto_start: true` 时由主服务拉起进程并等待 `/health` 就绪。
- `remote_http`：显式配置远程 `endpoint`，主服务不拉起本地进程；远程节点需自行运行 waa-signer。
- `state_dir` 用于 Worker 指纹与解释器缓存，相对进程工作目录解析（Docker 下为 `/app/data/waa/workers`，随数据卷持久化）。
- `max_workers` 必须覆盖实例槽位数；`locale` 与 `timezone` 应与账号声明环境保持一致。

### Docker 单镜像双引擎

官方镜像同时内置 CloakBrowser 与 waa-signer：

- waa-signer 由多阶段构建产出静态二进制，落在 `/app/waa-signer/bin/waa-signer`，与 `engine.browserless.signer.binary_path` 默认值一致。
- 默认 `local_subprocess` + `auto_start`，切换到 browserless 时由主服务自动拉起，无需额外容器或挂载。
- 需要独立部署 signer 时，将 `mode` 改为 `remote_http` 并设置 `endpoint` 即可。
- 两种引擎共用同一镜像与同一数据卷，切换引擎无需重建镜像。

---

## 大上下文工具调用与 SillyTavern

### PG-01 思维签名缓存

Gemini 要求历史 `functionCall` 携带 `thoughtSignature`。当客户端（OpenAI / Claude 适配层或酒馆预设）重建工具通道而丢失签名时，网关会从持久化缓存中按 tool call id 恢复：

- 缓存文件：`data/signature_cache.json`
- 容量：4096 条；TTL：30 天
- 读取不消费，可被后续多轮复用
- 无真实签名时使用上游接受的占位签名兜底

### PG-04 工具载荷 Struct 强制

`functionCall` 参数与 `functionResponse` 结果统一强制为 Struct 形状；非法字符串 JSON 参数会被包装为合法对象，而不是直接 400。日志仅输出脱敏的 `[toolshape]` 形状摘要（参数名、类型、长度），不记录提示词或工具内容。

### PG-08 宽松工具历史

针对酒馆预设常见形态做兼容归一：

- 缺失 tool call id 时自动合成稳定 id
- 重复 id 自动去重并重编号
- 孤立的工具输出包装为合法工具结果
- 非法 JSON 参数保留原文并包装为 Struct
- 日志前缀 `[pg08]`，不记录消息正文

### C1c SSE 首包与心跳（防 524）

- 首事件预读窗口默认 15 秒；窗口内拿到事件则正常开始流式输出。
- 超过首窗后先返回 HTTP 200，并立即发送 `: keepalive\n\n`，随后每 15 秒发送一次心跳。
- 已开始输出后上游异常以 SSE error 结束，而不是静默断开。
- 环境变量覆盖：
  - `AISTUDIO_SSE_KEEPALIVE_SEC`（默认 15）
  - `AISTUDIO_SSE_PREREAD_WINDOW_SEC`（默认跟随 keepalive 间隔）

### C1d WAA 快照预热

快照预热是页面内本地调用，不依赖切换模型。Python 侧在首次请求、距上次快照超过 600 秒、payload 或 hash prompt 达到 32 KiB，或触发强制刷新时，会在获取真实签名前先执行一次本地暖机。

暖机与正式快照都通过页面内的 `dms[snapKey](service, hash)` 完成：

- 仅将 prompt 末尾 256 个 Unicode 码点写入 composer 文本域；完整 payload 不进入 DOM，但 hash 仍覆盖完整 prompt。
- 暖机使用 64 个 `0` 的占位 hash，属于 best-effort；暖机失败不会阻断正式快照。
- 快照质量要求长度 `>=1520` 字节/字符（JS 字符串长度）且以 `!` 开头；首次不达标时补一次暖机并重试，仍不达标抛出 `low_quality_snapshot`。
- 调用通过 `aistudio.snapQueue` 串行执行，结束后在 `finally` 中清空输入框。

### ATG 防截断网关

ATG 通过合成工具调用把模型输出从工具通道取出，避免长文本被上游截断。默认关闭：

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

- `mode` 是全局默认；`mode_by_format` 按协议覆盖（gemini/openai/claude/responses，`inherit` 表示跟随全局）。
- `text_model_pattern` / `non_text_model_pattern` 只用于 text/non_text 模型匹配过滤，不存在按模型设置 mode 的开关。
- `prefer`：合成调用缺失时回退纯文本。
- `required`：合成调用缺失时明确失败。
- `client_synthetic_guard`：检测到客户端已自带同类合成工具时跳过本地包装，避免双重包裹。

---

## 浏览器指纹与保活

- **确定性指纹**：按账号 id 生成稳定的 Windows 桌面屏幕尺寸、设备像素比、WebGL vendor/renderer 等指标，并在导航前通过 CDP 注入。默认开启，设置 `browser.fingerprint_spoof_enabled: false` 可关闭。
- **低内存参数**：受控 Chromium 使用单渲染进程、进程内 GPU、V8 堆限制、ANGLE SwiftShader 与静音参数。
- **24h 保活**：默认每 24 小时执行一次鉴权刷新与元数据刷新；启动时也会执行一次元数据刷新。可在管理台手动触发。
- **429 跨天重置**：按美西自然日重置账号模型限额。
- **单页生命周期**：同一账号最多一个活跃 Page；换绑采用有界 drain，期间不新建并存页面。

---

## 快速开始

### Android (Termux)

> [!TIP]
> 请从 [F-Droid](https://f-droid.org/packages/com.termux/) 或 [GitHub Releases](https://github.com/termux/termux-app/releases) 安装 Termux，不要使用 Google Play 版本。

```bash
# 1. 更新基础环境并安装 git
pkg update -y && pkg install -y git

# 2. 克隆仓库
git clone https://github.com/TonyDG233/aistudio-api.git
cd aistudio-api

# 3. 初始化依赖环境（自动配置手机加速源）
bash scripts/setup-env.sh
uv sync

# 4. 安装受控浏览器（仅首次，约 1-2 分钟）
bash scripts/setup-browser.sh

# 5. 启动服务
uv run python main.py server --port 8080
```

启动后访问 `http://localhost:8080`，在「账号管理」导入 Cookie 或凭据包。建议在 Termux 通知栏执行 `Acquire Wakelock`，并为 Termux 关闭电池优化。

### Windows

前置要求：已安装 [Python 3.11+](https://www.python.org/downloads/)（勾选 `Add python.exe to PATH`）。

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

服务启动后访问 `http://localhost:8080` 进入管理台。

### Docker / Compose

镜像同时内置 CloakBrowser（browser 引擎）与 waa-signer（browserless 引擎），一个容器即可在两种引擎间热切换：

```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  -e AISTUDIO_WEB_PASSWORD=your-console-password \
  ghcr.io/tonydg233/aistudio-api:2.1.0
```

GHCR 同时提供固定基准版本 `ghcr.io/tonydg233/aistudio-api:2.1.0` 与滚动标签 `ghcr.io/tonydg233/aistudio-api:latest`。使用 `docker run` 时，首次启动会自动从 `/app/defaults/config.yaml` 初始化 `/app/data/config.yaml`。waa-signer 位于 `/app/waa-signer/bin/waa-signer`，默认随 browserless 引擎自动拉起，无需额外容器或挂载。

使用仓库根目录的 `docker-compose.yml` 前，必须先在宿主机准备 `config.yaml`；该文件会以 bind mount 覆盖容器内 `/app/config.yaml`。若文件不存在，Docker 会创建同名目录，入口脚本无法把模板落成 `/app/config.yaml` 文件：

```bash
cp defaults/config.yaml config.yaml
docker compose up -d
```

`docker-compose.deploy.yml` 使用命名卷与 `/app/data/config.yaml`，首次启动可自动从 `/app/defaults/config.yaml` 初始化。启动后访问 `http://localhost:8080`。

---

## 配置与环境变量

### 配置优先级

1. `AISTUDIO_CONFIG_PATH` 指定的路径
2. `AISTUDIO_CONFIG_FILE` 指定的路径（向后兼容）
3. 项目根目录 `config.yaml`
4. 文件缺失时自动从 `defaults/config.yaml` 复制

`defaults/config.yaml` 是唯一受版本控制的默认配置模板，也是默认值来源。

### 主要配置块

| 配置块 | 作用 |
|---|---|
| `api_keys` / `security` | 对外 API Key 与是否允许匿名 |
| `instances` / `max_contexts` | 实例池规格与全局上限 |
| `engine` | 请求引擎（browser / browserless）、热切换事务、WAA signer 与传输参数 |
| `rotator` | 重试、429 冷却与 quota scope |
| `features.routing` | 连续切号上限与 429 同号退避 |
| `features.keepalive` | 24h 鉴权与元数据刷新节奏 |
| `features.model_discovery` | 模型目录刷新间隔 |
| `features.formats` | OpenAI / Claude / Responses 协议开关与媒体私网访问 |
| `features.atg` | 防截断网关模式与参数 |
| `paid_model_rules` | 付费模型 tier 准入 |
| `model_defaults` | 模型 profile、默认工具、安全配置与参数过滤 |
| `tier_quotas` | 按 tier 的每日软额度（仅用于展示与调度权重） |
| `account_presets` | 账号预设方案 |
| `diagnostics` / `browser` / `logging` | 诊断、指纹与日志 |

### 环境变量

| 变量名 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `AISTUDIO_PORT` | int | `8080` | API 服务监听端口 |
| `AISTUDIO_WEB_PASSWORD` | string | `""` | 管理台密码；设置后自动开启登录鉴权 |
| `AISTUDIO_ADMIN_PASSWORD` | string | `""` | 管理台密码的兼容变量名 |
| `AISTUDIO_PROXY` | string | 自动探测 | 出口代理，支持 `http://`、`https://`、`socks5://`；也会读取 `HTTPS_PROXY` / `HTTP_PROXY` |
| `AISTUDIO_DATA_DIR` | string | `data/` | 数据根目录（Docker 下为 `/app/data`） |
| `AISTUDIO_ACCOUNTS_DIR` | string | `data/accounts` | 账号凭据目录 |
| `AISTUDIO_STATS_FILE` | string | `data/stats.json` | 调用与 Token 统计文件 |
| `AISTUDIO_ROTATOR_STATE_FILE` | string | `data/rotator_state.json` | 429 冷却与调度状态文件 |
| `AISTUDIO_MODEL_CACHE_FILE` | string | `data/models_cache.json` | 模型目录快照 |
| `AISTUDIO_CONFIG_PATH` | string | `config.yaml` | 配置文件路径（优先） |
| `AISTUDIO_CONFIG_FILE` | string | `config.yaml` | 配置文件路径（兼容） |
| `AISTUDIO_AUTH_FILE` | string | 自动发现 | 单文件认证覆盖 |
| `AISTUDIO_PERSIST_STATS` | bool | `true` | 是否持久化统计 |
| `AISTUDIO_PERSIST_ROTATOR` | bool | `true` | 是否持久化调度状态 |
| `AISTUDIO_BROWSER_EXECUTABLE` | string | 自动探测 | Chromium 可执行文件路径（Docker 内为 `/opt/cloakbrowser/chrome`） |
| `AISTUDIO_BROWSER_PORT` | int | `9222` | CDP 调试端口 |
| `AISTUDIO_BROWSER_HEADLESS` | bool | `true` | 是否无头运行 |
| `AISTUDIO_FINGERPRINT_SPOOF` / `AISTUDIO_FINGERPRINT_SPOOF_ENABLED` | bool | `true` | 浏览器指纹伪装开关 |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | string | `gemini-3.7-flash` | 默认文本模型 |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | string | `gemini-3.1-flash-image-preview` | 默认图像模型 |
| `AISTUDIO_TIMEZONE` | string | 自动探测 | 浏览器与 Wire 时区（回退 `America/Los_Angeles`） |
| `AISTUDIO_LOCALE` | string | `en-US` | 浏览器区域语言 |
| `AISTUDIO_TMP_DIR` | string | 系统临时目录 | 临时文件目录 |
| `AISTUDIO_SSE_KEEPALIVE_SEC` | float | `15` | SSE 心跳间隔（秒） |
| `AISTUDIO_SSE_PREREAD_WINDOW_SEC` | float | `15` | 首事件预读窗口（秒） |
| `AISTUDIO_RETRY_SAME_DELAY_SEC` | float | `2.0` | 429 同账号首次重试前退避（0 - 30） |
| `AISTUDIO_UI_DEBUG` | bool | `false` | 启用纯离线 UI 调试沙盒 |
| `AISTUDIO_HASH_INCLUDE_CODE` | bool | `true` | 诊断：snapshot hash 是否包含代码 part |
| `AISTUDIO_DUMP_REQUESTS` | bool | `false` | 是否将请求/响应详情输出到终端（也可用 `DEBUG=1`） |
| `AISTUDIO_DUMP_FILE` / `AISTUDIO_DUMP_TO_FILE` | bool | `false` | 是否将报文落盘 |
| `AISTUDIO_DUMP_DIR` | string | `data/dumps` | 报文转储目录 |
| `AISTUDIO_DUMP_RAW_RESPONSE` | bool | `false` | 是否导出原始底层响应 |
| `AISTUDIO_DUMP_RAW_RESPONSE_DIR` | string | `data/dumps` | 原始响应导出目录 |

---

## Web 管理台

访问 `http://localhost:8080`（设置 `AISTUDIO_WEB_PASSWORD` 后需先登录）。左侧导航包含四个页面：

1. **控制面板**：实例运行状态、在途请求、账号与额度概览、模型调用统计、按 7 / 30 / 90 天的请求与 Token 趋势图，以及快速调用示例。统计按 Asia/Shanghai 自然日保留最近 90 天，并区分客户端请求与节点尝试两种分母口径。
2. **账号管理**：Cookie 文本与凭据包批量导入、子账号探活、实例槽位创建/换绑/重启、账号启用与分组、预设方案、配额与 429 状态、保活手动触发。
3. **系统与模型配置**：API Key 管理、请求引擎（browser / browserless 热切换）、安全与浏览器、调度与重试、协议开关与 ATG、日志配置、模型规则与 YAML 源文编辑；保存后热重载，引擎变更按事务切换并支持回滚。
4. **日志**：实时 SSE 日志流、级别过滤、搜索、暂停与 1 / 3 / 7 天导出。

### UI Debug 离线沙盒

```bash
AISTUDIO_UI_DEBUG=1 uv run python main.py server --port 8080
```

开启后：

- 配置、账号、统计、模型缓存、日志与转储全部重定向到临时目录。
- 不启动真实浏览器、不访问真实账号、不发起外网请求。
- 保存配置不会写入真实 `config.yaml`。
- 控制面板与日志页提供场景切换（如 `all_states`、`mass_accounts`、`all_429`、`empty`）以及模拟请求/429 的调试菜单。

该沙盒仅用于 UI 开发与验收，绝不作用于真实浏览器或真实磁盘。

---

## 客户端接入

### 鉴权

对外 API 支持以下任一方式：

- Query：`?key=YOUR_API_KEY`
- Header：`x-goog-api-key: YOUR_API_KEY`
- Header：`x-api-key: YOUR_API_KEY`
- Header：`Authorization: Bearer YOUR_API_KEY`

`api_keys` 为空且 `security.api_allow_anonymous` 为 `false` 时，所有对外 API 请求都会被拒绝。管理台密码与 API Key 完全独立。

### SillyTavern

- OpenAI 兼容连接：Base URL 填 `http://<host>:8080/v1`，API Key 填配置中的任意 `api_keys`。
- Claude 兼容连接：Base URL 填 `http://<host>:8080`，请求端点为 `/v1/messages`。
- Gemini 兼容连接：Base URL 填 `http://<host>:8080`，API 版本 `v1beta`。

### cURL 示例

**Gemini 非流式**

```bash
curl http://localhost:8080/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "用一句话介绍量子计算。"}]}]
  }'
```

**Gemini 流式**

```bash
curl "http://localhost:8080/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse" \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "介绍 Python 协程。"}]}]
  }'
```

**OpenAI Chat**

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "messages": [{"role": "user", "content": "你好"}],
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
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

**OpenAI Responses**

```bash
curl http://localhost:8080/v1/responses \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.7-flash",
    "input": "你好"
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
    contents="解释一下什么是反应式编程。",
):
    print(chunk.text, end="", flush=True)
```

---

## 开发与测试

前端位于 `web/`，使用 Vue 3 + TypeScript + Vite + Tailwind CSS，依赖管理与构建使用 `bun`：

```bash
cd web
bun install
bun run dev          # 开发服务（默认 3000，代理到 8080）
bun run type-check
bun run lint
bun run build        # 产物输出到 src/aistudio_api/static
```

Python 工具链统一使用 `uv`：

```bash
uv sync --extra dev      # 开发/测试环境
uv run ruff check .
uv run ruff format --check .
uv run pyright src tests
uv run pytest
```

Android 代码检查：

```bash
cd android
./gradlew :app:lintDebug :app:ktlintCheck
```

---

## 相关文档

- [系统架构设计](./docs/ARCHITECTURE.md)：四层架构、请求生命周期、实例池、路由与日志。
- [Wire 协议逆向规范](./docs/WIRE_SPECIFICATION.md)：Protobuf-JSON 数组映射、字段索引、多模态 Part 与 Schema 编码。
- [BotGuard 验证链路](./docs/BOTGUARD_VERIFICATION_CHAIN.md)：WAA 挑战握手、Wasm 动态签名与内容哈希。
- [WAA Signer 独立签名服务](./waa-signer/README.md)：SpiderMonkey 152 运行时、HTTP 接口、手动编译与 Docker 内置说明。
- [实例池与路由规范](./docs/INSTANCE_POOL_AND_ROUTING_SPEC.md)：实例、tier 选择器、429 / 403 与 surface 路由。
- [保活与日志规范](./docs/KEEPALIVE_AND_LOGGING_SPEC.md)：24h 保活、日志三 Sink 与脱敏。
- [协议适配与工具调用规范](./docs/PROTOCOL_AND_TOOLCALL_SPEC.md)：四协议转换、PG-01/04/08、ATG 与 countTokens。
- [更新日志](./docs/changelog.md)。
- [智能体协作与开发规范](./AGENT.md)。

---

## 致谢 (Acknowledgments)

本项目的基础架构、CDP 通信层与逆向协议实现源自上游项目 [DeconstructedCube/aistudio-api](https://github.com/DeconstructedCube/aistudio-api)。感谢上游作者及贡献者在 Google AI Studio 协议分析、浏览器内 XHR Replay、Wire 编解码与 BotGuard 验证链路上打下的基础。

---

## 开源协议

本项目基于 [MIT License](https://opensource.org/license/mit) 许可协议开源。
