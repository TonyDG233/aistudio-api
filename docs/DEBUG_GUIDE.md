# aistudio-api 调试指南

本文档面向"下一个接手排障的 agent/开发者"，沉淀 verify-account（扫码/设备验证）排障中**已被真实证据验证过**的方法论：取证顺序、证据分层、环境变量开关、隔离调试姿势与取证文件读法。文档以当前 HEAD 代码为事实标准；出现分歧时以代码为准，并同步回本文档。

> 行号基于 2.1.6 发布点，后续以符号名为准。所有实现引用统一采用「符号名（`文件:行号`）」形式；行号漂移时先按符号名定位。

本文只讲"怎么定位"，不重复架构决策；架构与决策口径见 `docs/ARCHITECTURE.md`、`docs/dev/rebuild/06-decision-log.md` 与 `docs/dev/rebuild/08-09-decision-record.md`。

---

## 目录

- [1. 适用范围与红线](#1-适用范围与红线)
- [2. 核心原则（每条都有反面案例）](#2-核心原则每条都有反面案例)
- [3. 四层证据法](#3-四层证据法)
- [4. 环境变量速查表](#4-环境变量速查表)
- [5. 本地隔离调试的标准姿势](#5-本地隔离调试的标准姿势)
- [6. 取证文件怎么读](#6-取证文件怎么读)
- [7. 错误文案与归因对照](#7-错误文案与归因对照)
- [8. 验收口径](#8-验收口径)
- [9. 禁令清单](#9-禁令清单)

---

## 1. 适用范围与红线

- 适用：verify-account 扫码墙排障、验证会话状态异常、上游 RPC 判定与本地观测不一致、需要判断"到底卡在浏览器环境 / 页面检测 / 服务端授权"的场合。
- 不适用：常规生成链路的 429/403 路由问题，请先看 `docs/ARCHITECTURE.md` 与 `docs/DEBUG_GUIDE.md` 第 3 节的证据分层，再结合路由文档定位。
- 红线（本仓库既有硬约束，不因本文档而放宽）：
  - 严禁 Worker / 子 Agent 私自启动真实服务（`main.py server`）或拉起真实浏览器进程；获批的实机测试只允许由主控或用户在**单次常驻进程**内完成，严禁反复重启（BotGuard 探针风险，见 `AGENT.md` §4.6）。
  - 默认离线：不访问真实 Google / 真实账号 / 真实 IP，除非用户单独授权本次实测。
  - 实测前必须先告知用户；需要用户扫码时必须直接、明确地请求，并告知是哪个账号、第几次。
  - 全量取证会落盘明文凭据，只能在**本机隔离目录**开启，严禁在共享部署上使用（见 §6）。

---

## 2. 核心原则（每条都有反面案例）

### 2.1 先取证，再假设

任何归因都必须站在一条**可复验的证据**上（日志行、抓包事件、上游状态码），而不是"看起来像"。

反面案例：本轮前期把"二维码没生成"归因于"页面检测/风控"，实际是主框架指标 `qr_canvas` / `verify_button`（`_DESCRIBE_PAGE_JS`，`src/aistudio_api/application/verify_account_service.py:344-363`，由 `_describe_page_snapshot` 在 `:3296-3316` 执行；`_PAGE_SNAPSHOT_JS` 在 `:330-339` 只返回 url/text）在量**错误的框架**——二维码由 reCAPTCHA 的 `bframe` 里的 `canvas.recaptchaJavascriptChallengeQrCanvas` 生成，必须由独立的 bframe 读取逻辑（`_read_bframe_qr`，同文件 `:2898-2984`，筛选条件 `bframe`/`recaptcha` 在 `:2917-2926`）去拿。主框架指标一直是"不存在"，于是整整 24 小时都在错误方向上加深搜索。

教训：当"预期出现的元素不存在"时，第一反应应当是**换一个观测点复验**，而不是给这个不存在的元素编一个原因。

### 2.2 禁止把"纸面路径/单测/模拟"当实机闭环

单测只能证明代码分支按设计走；模拟只能证明我们自己的假设自洽。它们都不能证明"上游真的给了二维码、真的回调了、真的判了通过"。

反面案例：交接文档曾把 verify-account 扫码闭环写成"可行"，但从未实机验证过 QR 生成与扫码后的回调；真实情况是 QR 生成阶段会 `qr_timeout`，扫码后 verify 按钮消失而 `upstream_status` 仍为 1。把未跑通的路径当作可行交付，会让下一个接手的人从错误的前提出发。

正确表述方式：明确区分"代码路径存在""单测通过""实机单次观测到 X"三种结论，并各自附证据。

### 2.3 错误文案先分类，再归因

同一堵墙上的文案属于不同层级，混为一谈会导致反复在错误方向加深搜索。至少先分清三类：

1. **浏览器环境级**：`Something went wrong while verifying your device … browser extensions`。含义是验证浏览器本身的启动参数/扩展/被掐断的通道出了问题，与账号和设备无关（`_apply_verify_compat_args` 的实机证据注释：`src/aistudio_api/application/verify_account_service.py:3241-3259`）。
2. **设备级**：`We couldn't verify your account with this device`、`try a different mobile device` 等，指向手机侧额度/安全策略（失败文案清单：`_FAILURE_MARKERS`，同文件 `:206-212`）。
3. **通过**：`your account was verified`、`no verification needed` 等（成功文案清单：`_SUCCESS_MARKERS`，`:200-205`）。

反面案例：把第 1 类（浏览器环境级）和第 2 类（设备级）当成同一件事，于是既改启动参数又换手机，两个变量同时动，导致任何一次结果都无法归因。

### 2.4 区分"我们的请求"与"对方的判定"

请求形状（headers、Cookie、`X-Goog-Api-Key`、`Authorization: SAPISIDHASH`、`X-AIStudio-Visit-Id` 等，`confirm_verified` 构造于 `src/aistudio_api/application/verify_account_service.py:2753-2766`）完全正确时，结论要落到**服务端授权/设备证明**，而不是继续改 header。

反面案例：本次排障中 `VerifyUser` 的请求形状已与真实浏览器一致，仍被 403 拒绝；继续微调 header 无收益，真正的变量在出口 IP 与设备证明（令牌来路）这一侧。判定必须来自上游响应，而不是"我们觉得请求已经很像了"。

### 2.5 一次只改一个变量

本轮有效的推进顺序是：**直连 → 有头 → 真实 UA/GPU（去掉低配机专属参数）**；其后的 **系统浏览器 → 最小参数** 两步属调查期的**历史尝试记录**——"最小参数"档由 `AISTUDIO_VERIFY_MINIMAL_FLAGS` 驱动，该开关已在 2.1.6 移除，无法复现（系统浏览器仍可经 `AISTUDIO_VERIFY_CHROME_PATH` 复现，见下）。每一步只动一个维度，并记录该步的页面状态、网络记录与上游状态。

- 直连：`AISTUDIO_VERIFY_PROXY` 留空时浏览器与 RPC 都直连（`CdpVerifyBrowserDriver.__init__` 读取代理：`:1934-1938`；`_build_verify_chrome_command` 无显式代理时剔除 `--proxy-server=`：`:3228-3229`；`confirm_verified` 的 httpx `proxy=`：`:2771`）。
- 有头：**桌面平台默认有头**；`AISTUDIO_VERIFY_HEADED=0/false/no` 可回退无头（该变量已废弃，仅保留作未来研究开关）。非桌面平台默认无头（`_verify_headed_enabled`）。
- 真实 UA/GPU：有头模式会剔除 `--disable-gpu`、`--use-gl=`、`--use-angle=`、`--in-process-gpu`、`--js-flags=`、`--fingerprint-timezone=` 等低配机/指纹参数（`_build_verify_chrome_command`，`:3201-3224`）；**两种模式都不注入固定 UA**，使用内核原生 UA 与一致的 client hints（`:3186-3188`）；同时无条件剔除破坏 reCAPTCHA 的 `--disable-background-networking`、`--disable-component-update`、`--disable-features=` 等（`_apply_verify_compat_args`，`:3241-3271`）。
- 系统浏览器：`AISTUDIO_VERIFY_CHROME_PATH` 指向宿主已安装的浏览器可执行文件（`CdpVerifyBrowserDriver.__init__`，`:1929-1933`；`_resolve_binary`，`:1972-1988`）。
- 最小参数：这一档此前由 `AISTUDIO_VERIFY_MINIMAL_FLAGS` 驱动，**该变量已在 2.1.6 移除**，不要再依赖；最小参数仅作为方法论记录保留，需要时手工构造启动命令并在实测前告知用户。

注意：一次实测只允许一个进程、一个变量。任何"同时换三个参数试试"的动作都会让结论不可用。

---

## 3. 四层证据法

按成本从低到高排列。前两层几乎零成本，应该先跑；后两层带副作用（性能/凭据），只在必要时开启。

| 层 | 证据 | 能回答 | 不能回答 | 开关 |
|---|---|---|---|---|
| 1 | 页面状态（URL / 标题 / 正文 / 控件存在性） | 页面到没到 `/verify-account`、有没有被重定向到登录或 region 拦截、主框架上是否有 verify 按钮 | 二维码是否在 **bframe** 内生成；上游是否认可本次验证 | 默认注册，但逐轮页面状态走 `_log_diagnostic`：默认 DEBUG，`AISTUDIO_VERIFY_DIAGNOSTICS=1` 时 INFO（`_log_page_state`，`src/aistudio_api/application/verify_account_service.py:2074-2100`）；也可通过 `describe_page` 只读读取（`:2070-2072`） |
| 2 | 网络记录（请求/响应元数据，白名单字段） | 页面有没有真的发出 `GenerateAccessToken` / `VerifyUser`，响应状态是什么，帧导航去了哪里 | 响应体内容；Cookie 是否随请求带出 | 默认注册监听（`_register_net_trace`，`:2134-2156`；`NET_TRACE_MAXLEN=300`，`:114`），但 `_dump_net_trace`（`:2640-2718`）默认 DEBUG，`AISTUDIO_VERIFY_DIAGNOSTICS=1` 时 INFO |
| 3 | 响应体（关键 RPC 的 body） | 403/401 的具体错误体，`GetUserVerificationStatus` 返回的 `[2]`/`[1]`，`Waa/Create` 是否放行手机交接 | 其它非白名单请求的 body；完整请求头与 Cookie | 抓取默认对白名单 token 开启（`NET_BODY_URL_TOKENS`，`:146-154`；每会话上限 `NET_BODY_MAX_PER_SESSION=20`，`:155`）；body 预览默认 DEBUG，`AISTUDIO_VERIFY_DIAGNOSTICS=1` 时 INFO（非 2xx `VerifyUser` 例外，恒 INFO） |
| 4 | 全量抓包（含 Cookie / Authorization / postData / Set-Cookie / 响应体 / 控制台 / WS） | 逐字节复现"Google 到底拒绝了什么"，排查出口 IP 与令牌来路 | 不解释业务含义，只给原始事实；且含明文凭据 | `AISTUDIO_VERIFY_FULL_CAPTURE=1`（`_full_capture_enabled`，`:1816-1818`；`_start_full_capture`，`:2196-2306`），目录可由 `AISTUDIO_VERIFY_CAPTURE_DIR` 覆盖（`:1851-1856`） |

使用顺序建议：

1. 先看第 1 层日志，确认没有停留在登录页 / region 拦截 / 空白页。
2. 再看第 2 层，确认 `VerifyUser` 是否真的发出、状态码是多少、扫码后有没有 `frame_navigated`。
3. 若 403 但请求形状正常，直接看第 3 层的响应体，把结论从"我们的请求"转到"对方的判定"。
4. 只有当第 3 层仍不足以区分（例如怀疑 Cookie 未随请求带出、被浏览器拦截、或需要看完整 postData）时，才开第 4 层；并严格遵守 §6 的隔离要求。

补充：第 1 层的 `qr_canvas` / `verify_button` 只描述**主框架**。二维码在 bframe 内，主框架指标恒为否并不代表二维码没生成——这正是 §2.1 的反面案例。

---

## 4. 环境变量速查表

以下默认值与读取位置均以当前代码为准。读取时机分三类，**不要混用**：

- **逐行热读取**（进程内改 `os.environ` 立即生效）：`AISTUDIO_VERIFY_DIAGNOSTICS`——`_log_diagnostic` 每条诊断行都重读 `os.environ`（`src/aistudio_api/application/verify_account_service.py:263-268`）。
- **下一次验证会话生效**（无需重启进程，但已经构造好的 driver/会话不会重读）：`AISTUDIO_VERIFY_HEADED`、`AISTUDIO_VERIFY_PROXY`、`AISTUDIO_VERIFY_CHROME_PATH`、`AISTUDIO_VERIFY_FULL_CAPTURE`、`AISTUDIO_VERIFY_CAPTURE_DIR`。
- **必须重启服务**（只在启动期或配置归一化时读取，进程内改无效）：`AISTUDIO_ALLOW_EMPTY_INSTANCES`、`AISTUDIO_SKIP_CONTROL_WARMUP`、`AISTUDIO_UI_DEBUG`、`AISTUDIO_EXIT_MODE`，以及路径/端口类 `AISTUDIO_CONFIG_PATH`、`AISTUDIO_DATA_DIR`、`AISTUDIO_ACCOUNTS_DIR`、`AISTUDIO_STATS_FILE`、`AISTUDIO_ROTATOR_STATE_FILE`、`AISTUDIO_PORT`。

逐项核对（证据均为当前工作树）：

| 变量 | 读取时机 | 生效方式 | 证据 |
|---|---|---|---|
| `AISTUDIO_VERIFY_DIAGNOSTICS` | 每条诊断日志调用时 | 热读取，立即生效 | `_verify_diagnostics_enabled`（`:251-260`）、`_log_diagnostic`（`:263-268`） |
| `AISTUDIO_VERIFY_HEADED` | 每次构造验证浏览器启动命令时 | 需新验证会话，不需重启 | `_verify_headed_enabled`（`:3158-3163`）、调用点 `_build_verify_chrome_command`（`:3185`） |
| `AISTUDIO_VERIFY_PROXY` | `CdpVerifyBrowserDriver` 构造时 | 需新验证会话，不需重启 | `CdpVerifyBrowserDriver.__init__`（`:1934-1938`） |
| `AISTUDIO_VERIFY_CHROME_PATH` | `CdpVerifyBrowserDriver` 构造时 | 需新验证会话，不需重启 | `CdpVerifyBrowserDriver.__init__`（`:1929-1933`） |
| `AISTUDIO_VERIFY_FULL_CAPTURE` | 验证会话启动时（`_start_full_capture`） | 需新验证会话，不需重启 | `_full_capture_enabled`（`:1816-1818`）、调用点（`:2203`） |
| `AISTUDIO_VERIFY_CAPTURE_DIR` | `_FullCaptureSink` 构造时 | 需新验证会话（且须同时开启 `FULL_CAPTURE`），不需重启 | `_FullCaptureSink.__init__`（`:1851-1856`） |
| `AISTUDIO_ALLOW_EMPTY_INSTANCES` | 启动期配置归一化（`get_configured_instance_specs` → `_normalize_instance_specs`） | **必须重启** | `model_defaults.py:415-420`、`:556`；调用链 `api/app.py:439` |
| `AISTUDIO_SKIP_CONTROL_WARMUP` | 启动期 `_start_control_warmup` | **必须重启** | `api/app.py:114-123`、调用点 `:458` |
| `AISTUDIO_UI_DEBUG` | 启动期 `resolve_ui_debug()` | **必须重启**；且优先级高于配置 `diagnostics.ui_debug` | `config.py:311-334`、调用点 `api/app.py:298` |
| `AISTUDIO_EXIT_MODE` | 启动期首次 `get_exit_resolver()`（进程级缓存） | **必须重启** | `exit_pool/config.py:109-125`、`exit_pool/resolver.py:877-890`、`api/app.py:536` |

各变量的默认值与作用：

| 变量 | 真实默认值 | 作用 | 证据 |
|---|---|---|---|
| `AISTUDIO_VERIFY_HEADED` | 桌面平台未设置 = **有头**；非桌面平台未设置 = 无头；`0`/`false`/`no` 强制无头 | 覆盖验证浏览器的有头/无头（已废弃，保留给未来 recaptcha 研究） | `_verify_headed_enabled` |\n| `AISTUDIO_VERIFY_FORCE` | 空 = 按平台判断（Windows/macOS 支持，Linux 不支持）；`1` 强制支持；`0` 强制不支持 | 能力门禁：不支持的平台隐藏入口并让 `verify/start` 返回 501 | `verify_capability` |
| `AISTUDIO_VERIFY_DIAGNOSTICS` | 未设置 = `false`；`1`/`true`/`yes` 开启 | 诊断行级别：关时只保留出码就绪/会话就绪/最终结果/`VerifyUser` 非 2xx/清锁等关键行，其余降为 DEBUG；开时恢复全部诊断行 | `_verify_diagnostics_enabled`（`:251-260`）、`_log_diagnostic`（`:263-268`）、会话启动打印（`:916-919`） |
| `AISTUDIO_VERIFY_CHROME_PATH` | 空 = 走 `find_chromium_executable()`（仓库 `.cloakbrowser/**/chrome` 或 Termux wrapper） | 指定验证浏览器可执行文件；显式设置但文件不存在会抛 `VerifyBrowserUnavailable` | `CdpVerifyBrowserDriver.__init__`（`:1929-1933`）、`_resolve_binary`（`:1972-1988`） |
| `AISTUDIO_VERIFY_PROXY` | 空 = **直连**；浏览器与 `confirm_verified` 的 httpx 走同一出口 | 验证会话出口代理 | `CdpVerifyBrowserDriver.__init__`（`:1934-1938`）、`_build_verify_chrome_command`（`:3228-3229`）、`confirm_verified`（`:2771`） |
| `AISTUDIO_VERIFY_FULL_CAPTURE` | 未设置 = 关闭；`1`/`true`/`yes` 开启 | 全量 JSONL 取证（明文，勿在共享部署开启） | `_full_capture_enabled`（`:1816-1818`）、`_start_full_capture`（`:2196-2306`） |
| `AISTUDIO_VERIFY_CAPTURE_DIR` | 空 = 项目数据根下 `verify-capture/`（`resolve_data_dir()`，即 `<AISTUDIO_DATA_DIR>` 或 `<repo>/data`）；相对路径按 cwd 解析为绝对路径 | 覆盖全量取证目录 | `_resolve_capture_data_root`（`:1821-1837`，优先 `resolve_data_dir()`）、调用点（`:2206`）；`_FullCaptureSink.__init__`（`:1851-1856`）只处理覆盖值与相对路径 |
| `AISTUDIO_ALLOW_EMPTY_INSTANCES` | 未设置 = `false` | 仅当 `instances` 原始列表为空时允许通过校验（不会创建请求实例）；**读取于启动期配置归一化，改后必须重启** | `model_defaults.py:415-420`、`:555-561` |
| `AISTUDIO_SKIP_CONTROL_WARMUP` | 未设置 = 不跳过 | 跳过控制面预热；**只在 `pool_enabled=False` 分支被读取，且读取于启动期，改后必须重启**，有实例时是空转 | `_start_control_warmup`（`app.py:114-123`，调用点 `:458`） |
| `AISTUDIO_DATA_DIR` | 空 = `<repo>/data` | 数据根目录（stats/rotator/模型缓存/取证默认目录等） | `config.py:72`、`:145-153` |
| `AISTUDIO_ACCOUNTS_DIR` | 空 = 自动发现 `<root>/data/accounts` | 账号目录（registry.json / meta.json / presets.json） | `config.py:417`、`account_store.py:293-303` |
| `AISTUDIO_STATS_FILE` | 空 = `<data_dir>/stats.json` | 调用量/Token 统计持久化文件 | `config.py:156-163` |
| `AISTUDIO_ROTATOR_STATE_FILE` | 空 = `<data_dir>/rotator_state.json` | 调度与 429 冷却状态持久化文件 | `config.py:166-173` |
| `AISTUDIO_EXIT_MODE` | 缺失/空 = 关闭；仅 `external` 生效，其它值告警并保持关闭 | 外部出口池特殊模式 | `exit_pool/config.py:108-125` |
| `AISTUDIO_VERIFY_MINIMAL_FLAGS` | **已在 2.1.6 移除**，不要再推荐 | 曾用于有头模式的最小启动参数档位 | 原实现见 2.1.6 之前的提交历史（`git log -S AISTUDIO_VERIFY_MINIMAL_FLAGS`） |
| `AISTUDIO_IMPORT_KEEP_OSID` | **已在 2.1.6 移除**，不要再推荐 | 曾用于导入时保留 `OSID` 类 Cookie | 原实现见 2.1.6 之前的提交历史（`git log -S AISTUDIO_IMPORT_KEEP_OSID`） |

相关的非验证专用变量（本地调试同样会用到）：

- `AISTUDIO_PORT`：默认 `8080`（`src/aistudio_api/config.py:393`）。
- `AISTUDIO_WEB_PASSWORD`：空 = 管理页免密（`src/aistudio_api/config.py:49`）。**服务端 uvicorn 固定 `host="0.0.0.0"`（`src/aistudio_api/api/app.py:1005`），并不只绑本机**，因此本地调试要么设置临时密码，要么用主机防火墙限制访问，不能假设"只有本机能连"。设了密码后，账号/实例/保活等管理 API 由 `require_web_auth` 保护（挂载见 `src/aistudio_api/api/app.py:612-621`）；日志路由同样受它保护，挂载在 `:627-630`。令牌可从 `?key=`、`x-goog-api-key`、`x-api-key` 或 `Authorization: Bearer <password>` 任一位置提供（`_extract_request_token`，`src/aistudio_api/api/dependencies.py:159-176`；校验与 401 分支在 `require_web_auth` `:199-212`），四者皆缺失或不匹配才返回 401。

---

## 5. 本地隔离调试的标准姿势

目标：在不碰生产账号目录、不创建请求实例、不触发实例池/控制面预热的前提下，只跑 verify 这一条链路。以下命令以 Windows PowerShell 为例，**整个流程必须由主控/用户在一次常驻进程内完成**（见 §1 红线）。

### 5.1 关键前提：先隔离配置，再隔离数据

本机 `config.yaml` 通常含实例配置；若沿用默认 `config.yaml`，`instances` 非空 → `pool_enabled=True`（`src/aistudio_api/api/app.py:439-440`）→ 启动时 `InstanceManager.startup()`（`_startup_instances`，同文件 `:126-142`）会拉起真实 Chromium 并跑 BotGuard。

- `AISTUDIO_ALLOW_EMPTY_INSTANCES` 只在 `instances` 原始列表为空时生效（`model_defaults.py:555-561`）；`AISTUDIO_SKIP_CONTROL_WARMUP` 只在无池分支被读取（`app.py:109-123`）。
- 因此 **只设 `AISTUDIO_DATA_DIR`/`AISTUDIO_ACCOUNTS_DIR` 而不设 `AISTUDIO_CONFIG_PATH` 是错的**：默认 `config.yaml` 里的实例会照常拉起真实浏览器。必须先准备一份 `instances: []` 的独立配置，并用 `AISTUDIO_CONFIG_PATH` 指向它（`resolve_config_file`，`src/aistudio_api/config.py:110-126`）。
- 注意：目标配置文件**必须在启动前已存在**。若 `AISTUDIO_CONFIG_PATH` 指向的路径不存在，`resolve_config_file` 会自动从 `defaults/config.yaml` 复制模板（`config.py:128-142`），而模板里带实例——等于隔离失败。

独立配置 `data/local-verify/config.yaml`（或任一独立路径；下文步骤写入 `$debugRoot\cfg\config.yaml`）最小内容：

```yaml
instances: []
engine:
  mode: browser
diagnostics:
  ui_debug: false
features:
  routing:
    proactive_switch_mode: "off"
  keepalive:
    auth_update: false
    metadata_refresh: false
    metadata_refresh_on_startup: false
```

只有配置确实为空实例时，`AISTUDIO_ALLOW_EMPTY_INSTANCES=1`（放行空实例校验）与 `AISTUDIO_SKIP_CONTROL_WARMUP=1`（跳过控制面预热）才会生效；两者都在**启动期**读取（`model_defaults.py:556`、`app.py:114-123`），进程内改动无效，**必须重启服务**。

### 5.2 步骤

```powershell
# 1) 准备隔离根目录：配置 + 数据 + 账号副本（副本含明文凭据，结束整目录删除）
$debugRoot = "$env:TEMP\aistudio-verify-debug"
New-Item -ItemType Directory -Force -Path "$debugRoot\data" | Out-Null
New-Item -ItemType Directory -Force -Path "$debugRoot\cfg" | Out-Null

# 1.1 写隔离配置（instances 必须为空，否则会拉起实例真实浏览器）
@'
instances: []
engine:
  mode: browser
diagnostics:
  ui_debug: false
features:
  routing:
    proactive_switch_mode: "off"
  keepalive:
    auth_update: false
    metadata_refresh: false
    metadata_refresh_on_startup: false
'@ | Set-Content -Encoding utf8 "$debugRoot\cfg\config.yaml"

# 1.2 账号副本（不要直接指向生产 data/accounts）
if (Test-Path ".\data\accounts") {
    Copy-Item -Recurse -Force ".\data\accounts" "$debugRoot\data\accounts"
} else {
    Write-Host "本机没有 data\accounts，请先把待验证账号导入副本目录再继续"
}

# 1.3 先清掉从父 shell / IDE 继承的 AISTUDIO_*（否则会悄悄覆盖下面的隔离设置）
#     最危险的是 AISTUDIO_UI_DEBUG：它的优先级高于 diagnostics.ui_debug: false
#     （config.py:311-334），会让服务进入纯离线沙盒，verify 判定全是模拟结果。
Remove-Item Env:AISTUDIO_UI_DEBUG           -ErrorAction SilentlyContinue
Remove-Item Env:AISTUDIO_VERIFY_PROXY       -ErrorAction SilentlyContinue
Remove-Item Env:AISTUDIO_VERIFY_HEADED      -ErrorAction SilentlyContinue
Remove-Item Env:AISTUDIO_VERIFY_CHROME_PATH -ErrorAction SilentlyContinue
Remove-Item Env:AISTUDIO_VERIFY_FULL_CAPTURE -ErrorAction SilentlyContinue
Remove-Item Env:AISTUDIO_VERIFY_DIAGNOSTICS -ErrorAction SilentlyContinue
Remove-Item Env:AISTUDIO_VERIFY_CAPTURE_DIR -ErrorAction SilentlyContinue
Remove-Item Env:AISTUDIO_EXIT_MODE          -ErrorAction SilentlyContinue

# 2) 指向隔离配置与数据（只设 DATA_DIR/ACCOUNTS_DIR 不够，必须设 CONFIG_PATH）
$env:AISTUDIO_CONFIG_PATH        = "$debugRoot\cfg\config.yaml"
$env:AISTUDIO_DATA_DIR           = "$debugRoot\data"
$env:AISTUDIO_ACCOUNTS_DIR       = "$debugRoot\data\accounts"
$env:AISTUDIO_STATS_FILE         = "$debugRoot\data\stats.json"
$env:AISTUDIO_ROTATOR_STATE_FILE = "$debugRoot\data\rotator_state.json"

# 3) 允许空实例 + 跳过控制面预热（两者都以“实例为空”为前提，且都在启动期读取）
$env:AISTUDIO_ALLOW_EMPTY_INSTANCES = "1"
$env:AISTUDIO_SKIP_CONTROL_WARMUP   = "1"

# 4) 因为是 0.0.0.0 监听，设一个临时管理密码，别留空
$env:AISTUDIO_WEB_PASSWORD = "<临时密码>"

# 5) 另开临时端口，避免与生产实例冲突
uv run python main.py server --port 18080

# 6) 结束后清理：停进程、确认端口释放、删副本/配置/抓包
# Stop-Process -Id <pid>
# Get-NetTCPConnection -LocalPort 18080   # 应无结果
# Remove-Item -Recurse -Force $debugRoot
```

为什么必须先清空（§1.3）：这些变量来自父 shell / IDE，效果和本次实验变量完全混在一起，直接破坏 §2.5"一次只改一个变量"的前提——例如继承的 `AISTUDIO_VERIFY_PROXY` 会让"直连"看起来没生效，继承的 `AISTUDIO_UI_DEBUG=1` 会把验证判定换成沙盒模拟。清理后再按需设**一个**变量，才能把观测归因到它。

验证会话必须通过**后端接口**驱动，而不是要求用户去页面点。设了 `AISTUDIO_WEB_PASSWORD` 后，管理 API 受 `require_web_auth` 保护（`src/aistudio_api/api/dependencies.py:199-212`；账号/保活等挂载见 `src/aistudio_api/api/app.py:612-621`，日志路由 `:627-630`），调用须带上管理密码令牌——`?key=`、`x-goog-api-key`、`x-api-key` 与 `Authorization: Bearer <临时密码>` 任一形式均可（`_extract_request_token`，`dependencies.py:159-176`），全部缺失或不匹配才返回 401：

1. `POST /accounts/{account_id}/verify/start` → 返回 `session_id`（`src/aistudio_api/api/routes_accounts.py:1254-1290`）。
2. `GET /accounts/{account_id}/verify/status?session_id=...&force=true` → 查询状态；`force=true` 表示手动检查，即使页面尚未 success 也立即查上游（同文件 `:1293-1321`）。

全局同一时间只允许一个验证会话（冲突返回 409，`:1267-1274`）。

### 5.3 为什么这是必要组合

- `instances: []`：让 `pool_specs` 为空、`pool_enabled=False`（`app.py:439-453`），从根源上不创建请求实例、不走 `InstanceManager.startup()`。
- `AISTUDIO_ALLOW_EMPTY_INSTANCES=1`：空实例默认在配置校验阶段直接失败（`model_defaults.py:555-561`）；该开关只放行校验，**不会创建任何请求实例**（同处告警文案即为此意）。
- `AISTUDIO_SKIP_CONTROL_WARMUP=1`：空实例时启动会走控制面预热（`app.py:452-458` → `_start_control_warmup`，`:105-123`）。控制面预热会拿账号打开控制页、拉起真实 Chromium 并跑 BotGuard——这与"只调试 verify"的目标相悖，也会带来风控风险。该开关把这条路径关掉（`:111-122` 的注释写明）；**有实例时它不被读取**，所以不能替代清空实例。
- `engine.mode: browser`：verify 链路本身依赖真实 Chromium + CDP（bframe 读取、`Network.*` 事件、有头模式都建立在 CDP 之上，`CdpVerifyBrowserDriver.start`，`verify_account_service.py:1996-2068`）。分发默认值就是 `browser`（`defaults/config.yaml:22`），保持默认即可。

### 5.4 为什么不用 `browserless` 引擎做这类调试

- `browserless` 依赖本地 `waa-signer` 子进程（`defaults/config.yaml:28-38`，`binary_path: waa-signer/bin/waa-signer`）。
- 本机该二进制当前并不存在（`waa-signer/bin/waa-signer` 缺失），切到 browserless 会在引擎启动阶段失败，而不是进入验证流程，白白浪费一次冷启动。
- 更重要的是：verify 排障需要 bframe / CDP 级观测，browserless 没有 Page 与 CDP 世界，天然无法取证。

### 5.5 端口与免密控制台

- 服务固定监听 `0.0.0.0`（`app.py:1005`），不能只绑 `127.0.0.1`；调试时请换一个非默认端口，并设置临时 `AISTUDIO_WEB_PASSWORD` 或断网/防火墙隔离。
- `--ui-debug` 沙盒（`src/aistudio_api/main.py:22-24`、`:33-39`）会用**沙盒 fake 的 verify 服务**（`src/aistudio_api/api/routes_accounts.py:120-137`）替代真实控制器。它可以用来验证 UI 与状态机，但**不能**用来做真实扫码排障——沙盒里的一切判定都是模拟的（§2.2）。

---

## 6. 取证文件怎么读

### 6.1 文件位置与结构

- 默认目录：未设 `AISTUDIO_VERIFY_CAPTURE_DIR` 时，优先落在项目数据根下 `verify-capture/`（`_resolve_capture_data_root` 优先 `resolve_data_dir()`，即 `<AISTUDIO_DATA_DIR>` 或 `<repo>/data`，`verify_account_service.py:1821-1837`；`_start_full_capture` 在 `:2206` 调用）；可用 `AISTUDIO_VERIFY_CAPTURE_DIR` 覆盖，且相对路径按 cwd 解析为绝对路径（`_FullCaptureSink.__init__`，`:1851-1856`）。
- 文件名：`verify-<account_label>-<YYYYmmddTHHMMSS>-<6位hex>.jsonl`（`_FullCaptureSink.__init__`，`:1858-1861`）。
- 一行一个事件，字段 `t`（会话内秒数）+ `kind` + 该事件的原始/白名单字段（`_FullCaptureSink.record`，`:1868-1885`）。
- 单会话上限：`FULL_CAPTURE_MAX_ENTRIES=20000`、`FULL_CAPTURE_MAX_BODIES=600`、单 body 截断 `FULL_CAPTURE_BODY_LIMIT=16384`（`:192-194`）。

`kind` 取值（注册处：`request` `:2225-2230`；`response` `:2241`、`:2314`；`loading_failed` `:2252-2257`；`request_extra`/`response_extra`/`frame_navigated`/`console`/`log`/`ws_*` `:2274-2282`；`body`/`body_error` `:2350`、`:2361`）：

| `kind` | 来源 | 关键字段 |
|---|---|---|
| `request` | `Network.requestWillBeSent`（白名单包装） | `request_id`、`url`、`method`、`headers`、`post_data` |
| `request_extra` | `Network.requestWillBeSentExtraInfo` | `requestId`、`headers`（**含 Cookie**）、`associatedCookies` |
| `response` | `Network.responseReceived`（白名单包装） | `request_id`、`url`、`status`、`headers`、`mime_type` |
| `response_extra` | `Network.responseReceivedExtraInfo` | `requestId`、`statusCode`、`headers`（**含 Set-Cookie**） |
| `body` | `Network.getResponseBody` 异步补抓 | `request_id`、`url`、`length`、`truncated`、`body` |
| `body_error` | 取 body 失败 | `request_id`、`url`、`error_type` |
| `loading_failed` | `Network.loadingFailed` | `requestId`、`type`、`errorText` 等 |
| `frame_navigated` | `Page.frameNavigated` | `frame`、`type` |
| `console` | `Runtime.consoleAPICalled` | `type`、`args` 等 |
| `log` | `Log.entryAdded` | `entry` |
| `ws_sent` / `ws_recv` | `Network.webSocketFrameSent/Received` | `requestId`、`response` |

### 6.2 关键技巧：Cookie 在 `request_extra`，不在 `request`

- `request` 的 `headers` 来自 `Network.requestWillBeSent`，**不含** `Cookie`；`Cookie` 由网络栈在 `Network.requestWillBeSentExtraInfo` 里给出，落盘为 `request_extra.headers`（大小写不固定，按不区分大小写查找）。
- `request_extra.associatedCookies` 里每条 Cookie 都带 `blockedReasons`：为空表示"带了"，非空表示"被浏览器拦了（SameSite/安全策略等）"。这能精确区分**没带**与**被拦**，是"请求形状正确但上游不认"时最省时间的一步。
- 关联键不同：白名单包装后的 `request`/`response`/`body` 用 `request_id`；原始 CDP 事件（`request_extra`/`response_extra`/`ws_*`/`loading_failed`）用 `requestId`。脚本里两个都要兼容。

### 6.3 摘要脚本（可直接用）

保存为 `$env:TEMP\verify_summary.py`，用法：`uv run python "$env:TEMP\verify_summary.py" <capture_dir>`（不传参则用 `data/verify-capture`）。

```python
import json
import sys
from pathlib import Path

CAPTURE_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "data/verify-capture")
KEYS = (
    "VerifyUser",
    "GenerateAccessToken",
    "GetUserVerificationStatus",
    "Waa/Create",
    "recaptcha/enterprise",
)


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue  # 半行/坏行（进程被 kill 时会留下）直接跳过
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def header_value(headers: object, name: str) -> str:
    if not isinstance(headers, dict):
        return ""
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value)
    return ""


def main() -> int:
    if not CAPTURE_DIR.is_dir():
        print(f"目录不存在: {CAPTURE_DIR}")
        return 2
    files = sorted(
        CAPTURE_DIR.glob("verify-*.jsonl"), key=lambda p: p.stat().st_mtime
    )
    if not files:
        print(f"未找到抓包文件: {CAPTURE_DIR}")
        return 2
    path = files[-1]
    print(f"# 最新抓包: {path} ({path.stat().st_size} bytes)")

    merged: dict[str, dict[str, dict]] = {}
    for row in load_rows(path):
        rid = row.get("request_id") or row.get("requestId")
        kind = row.get("kind")
        if not rid or not kind:
            continue
        merged.setdefault(str(rid), {})[str(kind)] = row

    hits = 0
    for rid, entry in merged.items():
        req = entry.get("request") or {}
        resp = entry.get("response") or {}
        extra = entry.get("request_extra") or {}
        url = str(req.get("url") or resp.get("url") or "")
        if not any(token in url for token in KEYS):
            continue
        hits += 1
        cookie = header_value(extra.get("headers"), "cookie")
        blocked = []
        for item in extra.get("associatedCookies") or []:
            if isinstance(item, dict) and item.get("blockedReasons"):
                blocked.append(
                    f"{item.get('cookie', {}).get('name')}={item['blockedReasons']}"
                )
        body_row = entry.get("body") or entry.get("body_error") or {}
        body = str(body_row.get("body") or body_row.get("error_type") or "")
        print(f"\n[{rid}] {req.get('method', '?')} {url}")
        print(f"  status={resp.get('status', '?')} mime={resp.get('mime_type', '?')}")
        print(f"  cookie_header={'yes' if cookie else 'NO'}"
              f" cookie_len={len(cookie)}")
        if blocked:
            print(f"  blocked_cookies={'; '.join(blocked)}")
        if body:
            preview = body.replace("\n", " ")[:300]
            print(f"  body[{body_row.get('length', len(body))}]: {preview}")

    if not hits:
        print("\n未命中关键 RPC：检查抓包是否来自验证会话、是否被 20000 条上限截断。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

脚本行为说明（避免误读输出）：

- 只读最新一个 `verify-*.jsonl`，按 mtime 排序；多次会话请手动指定目录或文件名。
- 半行/坏行/非 JSON 行一律跳过，不会中断。
- `cookie_header=NO` 且没有 `blocked_cookies` → Cookie 确实没被带出；`cookie_header=NO` 但有 `blocked_cookies` → 被浏览器拦了。二者处置完全不同。
- `status` 来自 `response`，`body` 来自异步补抓；body 可能因上限缺失，缺失不代表没有响应体。

---

## 7. 错误文案与归因对照

| 观测 | 层级 | 说明 | 下一步 |
|---|---|---|---|
| `Something went wrong while verifying your device … browser extensions` | 浏览器环境级 | 启动参数/扩展/被掐断的推送与组件通道；与账号、手机无关 | 检查验证浏览器的启动参数是否包含 `--disable-background-networking`、`--disable-component-update`、`--disable-features=`、Windows 下的 `--no-sandbox` 等（这些在验证浏览器中已被无条件剔除，`_apply_verify_compat_args`，`:3241-3271`） |
| `device already used` / `doesn't meet device security requirements` | 设备级 | 与真实 stock browser 一致的状态（`:3244-3248`），说明浏览器环境已不再是变量 | 转向手机侧条件与出口 IP，而不是继续改浏览器 |
| `We couldn't verify your account with this device` / `try a different mobile device` | 设备级 | 手机侧额度/安全策略；安卓扫码失败属已知上游误杀（社区资料） | 用 iOS 16.4+ App Clip 路径复验（`AGENT.md` §4.6.1），不要据此判定服务不可用 |
| `VerifyUser` 403 且请求形状已与真实浏览器一致 | 服务端判定 | 令牌来路/出口不被认可（本轮实测记录，未固化到代码） | 核对出口 IP 与 Cookie 来源；不要再微调 header（§2.4） |
| `GetUserVerificationStatus` 返回 `200 [2]` / `200 [3]` | 通过 | `2=verified`、`3=notRequired`，两者都置 `passed`（模块文档 `:11-12`；独立检查 `:1367-1375`；会话内 `:1640-1647`） | 清锁；不要再用页面文案覆盖这个结论 |
| `GetUserVerificationStatus` 返回 `200 [1]` / 超时 / 非 200 | 未通过（pending） | `1=pending`；超时与未命中显式标记的 401/403 及解析失败保持 pending 并重试（`confirm_verified`，`:2740-2743`、`:2782-2794`；会话内 `:1648-1658`） | 继续等或重试；不要写成"失败"或"已验证" |

判定口径只有一条：**以上游 `GetUserVerificationStatus` 的状态为准**。页面 DOM 文案与 probe 成功标记只作辅助证据（§2.3、§8）。

---

## 8. 验收口径

- 通过：`confirm_verified` 调 `GetUserVerificationStatus` 得到 `200 [2]`（verified）或 `200 [3]`（notRequired），此时才清 `verify_required` 锁（模块文档：`src/aistudio_api/application/verify_account_service.py:11-12`；独立检查映射：`:1367-1375`；会话内清锁：`:1640-1647`）。
- 未通过：`[1]`、`None`、超时、以及没有显式上游标记的 401/403（`VerifySessionInvalid`/`VerifyRegionBlocked` 只在命中标记时才是终态），都保持 pending（`confirm_verified`，`:2740-2743`、`:2782-2794`；会话内 `_refresh`，`:1648-1658`）。
- 禁止用以下任一替代验收：页面出现"已通过"文案、`qr_canvas` 存在、单测通过、沙盒 `simulate` 结果、请求形状看起来正确。

---

## 9. 禁令清单

1. 不得把未验证路径写成"已实现/可行"。只有实机单次观测到的事实才能描述为"已跑通"，且要写清账号、次数与证据位置。
2. 不得用页面 DOM 文案代替上游状态做结论。验收只认 `GetUserVerificationStatus` 的 `[2]`/`[3]`。
3. 不得在共享部署上开全量取证。`AISTUDIO_VERIFY_FULL_CAPTURE=1` 的产物含 Cookie / Authorization / Set-Cookie 明文（`_FullCaptureSink` 文档字符串，`:1840-1848`），只能落在本机隔离目录，用完即删。
4. 不得要求用户通过页面操作来验证后端功能。verify 会话用 `POST /accounts/{id}/verify/start` 与 `GET /accounts/{id}/verify/status` 直接驱动；后端问题直接读接口与日志。
5. 实测前必须告知用户，并在同一进程内完成；禁止反复重启服务或反复拉起浏览器（BotGuard 探针风险，`AGENT.md` §4.6）。
6. 不得把 `AISTUDIO_VERIFY_MINIMAL_FLAGS`、`AISTUDIO_IMPORT_KEEP_OSID` 作为可用开关写进方案或脚本——两者已移除。
7. 不得在同一个实测里同时改多个变量（§2.5）；也不得用单测/模拟结果替代实机证据（§2.2）。

