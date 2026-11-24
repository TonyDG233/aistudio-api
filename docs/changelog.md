# Changelog

本文件记录用户可见与架构级变更，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
阶段审计细节、提交对照与逐项核对记录见 `docs/dev/rebuild/domains/`，不在此重复。

## [2.1.7] - 2026-11-24

在 2.1.6 基础上修复出入口 region 403 的误归因、canary 污染的永久停摆与死槽抱死 503，并把出口隔离/轮换的权责收敛到宿主脚本。

### Fixed

- **region 403 不再被当作“账号需扫码”**：新增内部归因 `region_suspect`，事件按 `other_403` + `canary=account_bad` 上报；不再写 `verify_required`，也不再触发同一出口的 verify 连坐烧毁。只有上游返回真实 verify-account 报文时才写入扫码锁。
- **canary 污染不再让进程永久停摆**：改为按出口 600s TTL 失效，TTL 到期自动恢复探测，无需重启进程。
- **INVALID 实例槽位不再永久 503**：新增 30/120/600s 有界自愈（最多 3 次）后再判定不可用。
- **generation 通道代码级拒绝应急直连**：生成路径不接受 `direct_fallback`，维护与探针通道行为不变。
- **出口管理器不再把 `fanout reports suspect=true` 当作出口损坏**：改为记录事件并限频告警，出口好坏交由 L1 探针判定，避免健康出口被反复误隔离。

### Added

- **出口隔离自动收敛**：`health.quarantine_ttl_s=1800`，隔离到期进入探针（锁外执行、独立 10s 超时），连续 3 次失败才置 `disabled`；`/v1/status` 巡检本身不再触发恢复。
- **`GET /v1/exits` 逐出口只读事实**：只暴露出口状态与预算等事实，不含 `socks_url`、凭据与账号明文；事件按 `kind`/`exit_id`/`canary`/HMAC 前缀结构化持久化。
- **宿主运维脚本（`data/tools/vps-exit-scripts/`，不入库）**：按实例 WARP 健康检查与轮换（quiet-gap、单实例 30min 冷却、全局每小时 ≤3 次、`flock` 互斥、默认 dry-run）；`warp-provision-instance.sh` 补位；`fanout-guard.sh` 以端口为判据、每小时最多重启 3 次。

### Changed

- **轮换权责收敛**：轮换只由宿主脚本负责，管理器 `rotate_on_auto_recover=false`、`rotate_on_recover=false`，自身只做状态机与探针。

### Notes

- **补池（replenish）仍未实现**：三条命令为空时如实输出 `replenish: disabled (no command)`，不再暗示会自动补齐。

## [2.1.6] - 2026-11-24

在 2.1.5 基础上收口 verify-account 扫码设备验证、Cookie 注入属性、出口探针误判与扫码链路可观测性，并补齐 2.1.5 遗留的版本号欠账。

### Added

- **扫码链路全量网络取证（默认关闭）**：设置 `AISTUDIO_VERIFY_FULL_CAPTURE=1` 后，验证会话把整页网络交互按 JSONL 落盘（请求头、Cookie、Authorization、postData、响应头、Set-Cookie、响应体、帧导航、控制台与 WebSocket 帧），用于复现“Google 到底拒了什么”；目录可用 `AISTUDIO_VERIFY_CAPTURE_DIR` 覆盖，未设时优先落在项目数据根下 `verify-capture/`（`resolve_data_dir()`，即 `<AISTUDIO_DATA_DIR>` 或 `<repo>/data`，相对路径按 cwd 解析为绝对路径）。产物含明文凭据，仅限本机隔离目录使用；单会话有条数与响应体上限，未设置该变量时不注册任何额外监听。
- **`AISTUDIO_VERIFY_DIAGNOSTICS`（诊断日志开关，默认关闭）**：默认只保留出码就绪、会话就绪、最终结果、非 2xx `VerifyUser` 响应体与清锁等关键行；网络记录、`GenerateAccessToken` 响应体、帧诊断与逐轮页面状态等降为 DEBUG。设为 `1`/`true`/`yes` 时恢复全部诊断行。`GenerateAccessToken` 的 401 响应体只在开启时出现，不作为成败判据。
- **独立账号状态查询 `POST /api/accounts/{id}/verify/check`**：不依赖二维码与浏览器，直接调用 `GetUserVerificationStatus`；`2/3` 清锁并返回 `passed`，`1` 或 `None` 返回 `pending`。凭据文件缺失、Cookie 不可读、缺少 SAPISID、上游会话失效（`session_invalid`）与 region 拦截等返回 `error`，不是 pending。管理台账号验证弹窗新增「查询账号状态」按钮（`web/src/components/modals/VerifyAccountModal.vue:884-900`，由账号列表页 `web/src/views/AccountsView.vue:479` 打开），并区分“扫码会话状态”与“账号真实状态”。
- **本地调试新增 `AISTUDIO_ALLOW_EMPTY_INSTANCES`**：仅当配置 `instances` 原始列表为空时放行校验，用于只跑 verify 的本地调试；不会创建任何请求实例。
- **本地调试新增 `AISTUDIO_SKIP_CONTROL_WARMUP`**：在无实例（`pool_enabled=False`）分支跳过控制面预热，避免拉起控制页 Chromium 与 BotGuard；有实例时该开关不被读取。
- **`docs/DEBUG_GUIDE.md` 与 `docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`**：前者沉淀四层证据法、取证文件读法与本地隔离调试姿势；后者记录 verify-account 状态机、错误文案归因、启动参数影响与验收口径。

### Removed

- **移除调查期开关 `AISTUDIO_VERIFY_MINIMAL_FLAGS`**：有头模式的“最小参数档”不再受该环境变量控制；如未来需要极简参数，应新增独立开关，不要复活旧名。
- **移除 `AISTUDIO_IMPORT_KEEP_OSID`**：导入不再提供“保留 OSID 类 Cookie”的开关，相关陈旧设备凭据一律按既有过滤规则剔除。

### Changed

- **扫码取码预算 `20s` → `45s`**：弱机实测二维码在 `+22~24s` 才出现（bframe 200 与 reload 均晚于原预算），20s 会在出码前抛假 `qr_timeout`；45s 仍在 300s 会话 TTL 内。
- **扫码浏览器统一使用内核原生 UA**：有头与无头模式都不再注入固定 `Chrome/133` UA（内核为 146，`sec-ch-ua` 自相矛盾），统一使用内核原生 UA 与一致的 client hints；有头模式同时剔除软件渲染、单 renderer、128MB V8、低端设备模式与时区伪造等低配机专属或指纹不一致参数。
- **版本一致性**：`pyproject.toml` 由 `2.1.4` 补齐到 `2.1.6`（tag `2.1.5` 发布时漏更的历史欠账），`web/package.json` 与 waa-signer `/health` 服务版本同步为 `2.1.6`；`AGENT.md` 明确打 tag 必须同步 `pyproject.toml` 版本，禁止 tag 与包版本不一致。README 中的 `AISTUDIO_IMAGE_TAG=2.1.4` 是示例镜像 tag，待镜像发布后更新。

### Fixed

- **verify-account：启动参数破坏 reCAPTCHA 设备验证**：验证浏览器此前携带 `--disable-background-networking`、`--disable-component-update`、`--disable-features=`、Windows 下 `--no-sandbox` 等参数，直接掐死 reCAPTCHA 后台通道与 Google 组件，页面报 `Something went wrong…`（含浏览器扩展相关提示）；现已无条件剔除这些参数并恢复沙箱，页面错误回到与真实浏览器同类的设备级提示，出码正常。证据等级：**本仓实测 iOS App Clip 通过一次、安卓被拒；社区资料为安卓大面积误杀**，不代表本服务不可用。
- **Cookie 被 Chromium 拒收导致账号侧会话不成立**：`SameSite=None` 未配 `Secure` 的 Cookie（如 `SID`）会被 Chromium 整条拒收，验证会话注入后仅剩部分 Cookie，伴随 `GenerateAccessToken` 401 与 `VerifyUser` 403（401 只是伴随症状，不是判据）。`set_cookies` 现按固定顺序合法化（`__Secure-` 强制 `Secure`、`__Host-` 强制 `Secure` 与 `path=/`、`None` + 非 `Secure` 强制 `Secure`），并在注入后与 `Network.getCookies` 比对输出缺失名称 `WARNING`；池化会话共用同一注入路径，一并受益。
- **页面未导航时不再误报 Cookie 缺失**：`_log_missing_cookie_names`（`src/aistudio_api/infrastructure/browser/cdp_client.py:729-755`）在 `Network.getCookies` 返回空（页面尚未导航，如 `about:blank`）时只打一行 DEBUG，不再按“Cookie 注入后缺失 N 项”刷 WARNING；仅在页面已导航且确有名项缺失时保留 WARNING。
- **裸 httpx 探针的 region 403 不再写入假 `verify_required`**：`external` 出口模式下，无池化会话的 tier / 邮箱 / RotateCookies 探针走裸 httpx 经租约出口，请求形状与 signer 不一致被 Google 回 region 403，而 canary 用 signer 形状在同一出口回 200，导致误判 `account_bad` 并回写 `verify_required`（假标清理后再次回弹的根因）。现在探针按实际传输标记上报，裸传输的 `probe_exit_id` 一律为空，仲裁对空值早退、不跑 canary、不报事件、不写 verify。
- **保活 `auth_update` 裸轮换的 region 403 不再写入 verify**：`auth_update` 因签发者通道不可用而回落到裸 httpx `RotateCookies` 时，其 region 403 无法证明出口匹配；现在按 `inconclusive` 处理，不跑 canary 归因、不写 `verify_required`，避免把出口噪声误记成账号需要验证。
- **验证响应体预览的脱敏增加 ≥32 连续令牌兜底**：已知键先按原规则掩码，再对任意 ≥32 字符的凭据字母表连续片段统一折叠为 `…`，使未注册字段或裸置的长令牌也不会进入 INFO 行；截断发生在掩码之后。

## [2.1.4] - 2026-11-15

在 2.1.3 基础上补齐主动轮换、切号退避、真 LRU 选号与防风控超时口径，并完成文档体系与 Docker 开箱即用分发重塑。

### Added

- **预防性主动轮换与范围框选（`proactive_switch`）**：槽位单账号连续成功调用达到阈值（默认 `15` 次）且当前空闲（`active_requests == 0`）时，自动平缓换绑至池中休息最久（LRU）的健康账号；支持 `关闭（off）`、`仅 Free 池（free_only）`、`仅 Pro+ 池（paid_only）`、`全部池（all）` 与 `指定账号分组（groups）` 五种模式，并可在前端设置页可视化配置参与轮转的账号范围。
- **切换账号退避等待（`switch_backoff_sec`）**：新增 429 / 风控切号前的退避等待配置（默认 `4.0` 秒，叠加 `±25%` 随机抖动即 `3~5` 秒），并在设置页替换原未生效的 `auth_error` 冷却输入框，采用移动端单列防挤压响应式布局。
- **Android `arm64-v8a` 静态签名器交叉编译与守护基建**：`waa-signer/build.ps1` 与 `waa-signer/build.sh` 新增 `android-arm64` 构建目标；Go 运行时缺失 `/etc/resolv.conf` 时启用自定义 DNS Resolver，并自动探测 Android 14+ 系统根证书目录；Android 端新增 `WaaSignerSupervisor.kt` 进程守护。
- **二维码 / ADB 验证辅助工具（`tools/verify_qr_adb.py`）**：支持剪贴板与图片离线二维码解码、无线 ADB `--host` 配对（可经 VPN 穿透连接）、`inspect-ids`、`setup-island`、`reset-island`、`update-gms` 子命令、`help` 中文速查手册，以及 GMS `DeepLinkActivity` 验证完成后自动退出。

### Changed

- **真 LRU 全池大循环选号**：`AccountRotator.select_rebind_account` 与 `InstanceManager._pick_account` 将 `last_used`（最久未使用时间戳）提为第一排序优先级，并在触发限流 / 错误时同步刷新 `last_used`，彻底消除多账号池仅在固定两个账号间乒乓切换的问题。
- **429 首击直接退避切号（移除同账号二次撞墙）**：遇到 `429 (UsageLimitExceeded)` 不再执行同账号二次重试，首击立即记录模型级冷却并经退避等待切换至下一健康 LRU 账号；前端「调度重试次数上限」问号提示同步标注该防风控特例。
- **长思考流式超时对齐 `300s` 并禁止超时 / 瞬态错误乱切号**：`request_timeout_s` 与 `read_timeout_s` 默认值统一提升至 `300` 秒且前后端保存同步生效；生成超时（`SignerTimeoutError` / `session_hang`）与上游瞬态错误（`204/404/408/5xx`）仅允许原账号重试 1 次，不再触发切号踢走健康 Pro / Free 账号。
- **文档体系与 Docker 开箱即用分发重塑**：对齐上游清爽结构重写 `README.md` 与 `README_EN.md`（精简内部架构章节，补齐快速开始、油猴 / Cookie-Editor 导号与 API Key 引导、四协议客户端对接表），同步更新 `docs/` 下 6 份核心规范文档；重构 `docker-compose.yml` / `docker-compose.deploy.yml` / `Dockerfile` / `.dockerignore` 实现零前置步骤开箱即用——首次启动自动播种 `./data/config.yaml`，从旧版根目录 `./config.yaml` 升级需在启动前执行 `mkdir -p data && mv config.yaml data/config.yaml`（若自定义 compose 仍将旧配置挂载到 `/app/config.yaml`，入口脚本亦会自动迁移至 `/app/data/config.yaml`），可选 `./.env` 通过 `env_file` 注入（`required: false`，缺失不报错），移除硬编码 `AISTUDIO_CONFIG_PATH` 与 `TZ=Asia/Shanghai`（改用 `AISTUDIO_TIMEZONE` 透传以保持指纹时区一致），`docker-compose.yml` 与 `docker-compose.deploy.yml` 健康检查均改用 `$${AISTUDIO_PORT:-8080}` 变量展开，`docker-compose.yml` 默认仅拉取预构建镜像（本地源码构建需手动取消 `build: .` 注释），CI 后端路径过滤补齐 Docker 相关文件。

### Fixed

- **死槽抱死 `503` 自动自愈**：`RoutingPipeline.begin()` 新增 `_heal_unavailable_instances`（配合 `InstanceManager.reassign(require_idle=True)` 的并发安全保护），当槽位因前序请求耗尽切号预算而停留在不可用账号时，新请求准入自动原位换绑健康账号。
- **`verify-account` 独立隔离与保活指纹对齐**：`verification_required` 事件独立隔离单账号（不连坐同源 `u/0..u/N` 兄弟账号），后台探活与 Cookie 轮换自动跳过已锁定账号，防止被 `GetAiStudioBenefitTier=200` 误洗白；`account_info_refresher` 与 `model_discovery` 请求头全面对齐 `Firefox/152.0`。

## [2.1.3] - 2026-11-13

### Added

- **ATG 输入/输出双独立开关与公开可编辑提示词模板**：`features.atg` 新增 `wrap_output`（默认 `true`）、`control_template` 与 `input_context_instruction`，完整支持仅输出、仅输入（原生流式）、双封装与双关闭四组合，以及自定义模板缺 `{tool_name}` 自动补齐函数名与空白回退；设置页 `ProtocolAtgCard.vue` 将输出侧、输入侧与严格模式开关调整为 `grid-cols-1 xl:grid-cols-3` 防拥挤布局，并在高级参数展开区公开两个全宽模板 `<textarea>` 与「恢复默认模板」按钮。
- **ATG 输入侧伪装工具调用与图文双轨分流（`wrap_input`）**：新增 `features.atg.wrap_input`（默认 `false`）与 `application/atg/input_wrapper.py`，100% 继承 `resolve_atg_mode(cfg, api_format)` 各协议独立策略（策略为 `off` 时静默直通，仅在 `prefer`/`required` 且 `wrap_input=true` 时生效）。开启后将非工具链对话输入封装为 `atg_ctx_*` 3-Turn 伪装工具交换（Turn 0 锚点 `"Initialize context retrieval."` → Turn 1 `functionCall` + PG-01 `thoughtSignature` → Turn 2 `functionResponse` 文本 Struct 与原生 `inlineData`/`fileData` 媒体 Part 同轮并置分流），保留完整多模态看图能力；`detect_client_synthetic_transport` 先于封装执行避免误判，`purge_synthetic_history` 自动清理历史 `atg_ctx_*` 与锚点防止多轮嵌套；设置页 `ProtocolAtgCard.vue` 在「各协议独立策略」正下方并排提供「严格模式」与「输入侧伪装封装（图文双轨）」开关及规范字典 Tooltip。
- **思维链截断显式告警**：非流式与流式生成在模型仅返回 `thinking`、未产出正文/工具调用/图片时输出 `[generation] 思维链截断告警`（WARNING，含 format/model/instance/finish_reason/thinking_len）；该场景仍视为非空响应，**不触发空响应重试或切号**，避免额外消耗上游配额。

### Changed

- **流式首字输出（TTFT）加速优化**：`capture.py` 引入 `_SNAPSHOT_SENTINEL` 对大请求体仅执行单次 `build_body` 序列化并原位安全替换（含哨兵碰撞回退）；`browserless_session.py` 与 Go 侧 `runtime.go` `fillPrompt` 在保持完整请求 `digest` 的同时将页面虚拟输入框截断至末尾 256 个 Unicode 码点并省略冗余 `wireContents` 传输；`waa-signer` `transport_browser.go` 将 CORS `OPTIONS` 预检缺省缓存延长至 `600s`。
- **`[toolshape]` 诊断日志降级**：`chat_service._emit_toolshape()` 由 `logger.info` 降为 `logger.debug`，完整 Schema 摘要不再刷屏占满常规日志与上下文，仍保留脱敏摘要供 DEBUG 排障与 PG-04 断言使用。
- **管理台极简审美对齐与 375px 移动端防挤压**：
  - `AccountRow.vue` 账号行左侧改为响应式双层布局（`flex-col sm:flex-row`），`375px` 窄屏下账号邮箱独占整行展示，不再被状态开关、索引与分组徽章挤压截断；常规 `u/x` 徽章同步收口为中性灰细边框风格。
  - 统一 `AccountRow.vue`（恢复 / 换槽 / 激活）与 `InstancePoolCard.vue`（换绑 / 重启 / 唤醒 / 绑定账号 / 关闭）全部槽位操作按钮尺寸、圆角与 Lucide 线性图标规范。
  - `InstancePoolCard.vue` 与 `DashboardView.vue` 的引擎标签统一为中性灰胶囊并允许换行，移除实例池每行重复的引擎标签。
  - `CookieImportModal.vue`、`CookieGroupBlock.vue`、`AccountsView.vue`、`DashboardView.vue`、`SettingsView.vue`、`ProtocolAtgCard.vue`、`SecurityBrowserCard.vue`、`LogConfigCard.vue`、`SchedulingCard.vue`、`ModelStatsTable.vue`、`QuickApiExamples.vue` 与 `YamlSourceEditor.vue` 全站去除彩色 Emoji 与高饱和琥珀/蓝紫色块，统一设置页全卡片 `<Button variant="primary" size="sm">` + `<Save />` 保存按钮规范，回归白底细灰框、翡翠绿主色、CSS 状态圆点与 Lucide 线性图标（并同步更新 `src/aistudio_api/static/` 构建产物）。
- **`domains/` 双层治理重构与四位审查标号制度固化**：归并 `B-docs-and-audit`→`docs-and-audit` 与重复 `P` 域（`P3-browser`/`P7-logs`/`P10-tests-evidence`），清理空壳 `_template`，新增 `P10-android-apk` 规划域与 `docs/dev/rebuild/domains/INDEX.md` 总索引；在 `AGENT.md` 与 `INDEX.md` 固化「三段式发布版本号（`X.Y.Z`）+ 隐藏第四位审查文档标号（`REV-X.Y.Z.W`）」制度，将本周期审查报告统一收口为 `REV-2.1.3.1` ~ `REV-2.1.3.6`。

### Fixed

- **`gemini-4-hydrogen` / `gemini-3.1-pro-preview` 准入与 Playground 每日配额矩阵对齐**：`defaults/config.yaml` 将 `paid_model_rules.pro_required` 收窄为 `["argon", "imagen"]`，校准 `Free`、`Plus`、`Pro`、`Ultra`、`Ultra_20X` 各档 `tier_quotas` 以对齐 Playground 每日配额矩阵（`Free.flash_models` 软限额调整为 `50`，各档 `flash_models` 含 `"hydrogen"` 且置于 `argon_models` 之前），对齐 `gemini-4-hydrogen` 与 `gemini-3.1-pro-preview` 准入规则；`model_discovery.py` 内置 `_FALLBACK_MODELS` 收录 `gemini-4-hydrogen` 并在 `get_required_tiers` 与 `routing_eligibility.py` `resolve_required_tiers` 增加 `hydrogen` 免付费防线，防止冷启动或旧配置误将其归入 Pro 队列。
- **WAA 502「隔离登录态失效」判定为终态**：`_map_signer_auth_error` 在 401 之外，将 reason 命中 `隔离登录态失效` / `登录态失效` / `session expired` / `authentication required` / `servicelogin` / `url=https://accounts.google.com` 的 `SignerAuthError`（含 Go Worker 冷启动 502）统一映射为 `SessionExpiredError`，不再先耗时走同账号 403 重签路径；`refresh_auth_state()` 重新 mint 命中该错误时置 `_session_expired=True` 并返回 False，由路由直接换绑切号；`browserless_session._EXPIRED_REASON_MARKERS` 补齐 `缺少登录 cookie` / `需要重新登录` / `请重新登录`，与 `signer_client._AUTH_FAILURE_MARKERS` 对齐，并明确 docstring 边界。
- **同源多登账号整组隔离、滚动凭据同步与防回灌**：账号根登录态确认失效时，`AccountStore.mark_session_expired_group()` / `get_sibling_account_ids()` 按 SAPISID/SID 会话锚点把 `u/0..u/N` 兄弟账号一并标记 `expired`（迭代 `list(registry.accounts)` 快照消除并发增删竞态，`_mark_root_session_expired()` 异常日志提升为 `warning`）；`account_orchestrator` 在 `session_expired` 事件中同步记录兄弟账号失效，`account_rotator.record_auth_error` 在 store 已判过期时将本次 403 提升为终态事件，`get_account_stats` 将 store 过期态镜像到统计视图；`BrowserlessSession.save_current_cookies()` 落盘后调用 `sync_sibling_storage_state_files()` 将 `__Secure-1PSIDTS` / `SIDCC` 等滚动 Cookie 原子同步到同锚点兄弟 `auth.json`（双方均带有效数字 `expires` 且来源更早时跳过覆盖以防回灌），`_reload_if_changed()` 在 `st_mtime_ns` 变化时自动重载快照并叠加待落盘 Set-Cookie。

## [2.1.2] - 2026-11-11

### Fixed

- **Ultra-only 模型准入信号优先级**：提权 `ListModels` 的 `item[82]`（`accessModes`）Ultra-only 信号优先于静态 `pro_required` 子串规则，`gemini-4-carbon` 不再被误判为 Pro 可用；同时将 `carbon` 纳入 `paid_model_rules.ultra_required`（`defaults/config.yaml` 与前端 `schema.ts` 同步）与内置模型表。
- **实例换绑弹窗 tier 显示与过滤**：修复 `InstanceSpec.from_mapping`、`InstanceManager._view` 与前端 `instances.ts` 对 `min_tier` 槽位 `max_tier`（`Ultra_20X`）的归一化与兼容计算，解决弹窗误显示“仅限 Pro”及可用 Pro 账号被过滤的问题。
- **Browserless 403 同号刷新**：`BrowserlessSession.refresh_auth_state()` 接入 `rotate_google_cookies`（`/RotateCookies`）主动续期并立即落盘 `auth.json`，刷新前先持久化内存中已收到的 Cookie；强制释放 Go 侧驻留热 Worker，使下一次 `mint_proof` 用最新凭据重建 Cookie 罐；waa-signer 热复用路径检测滚动凭据差异后按最新 storage state 重建 Worker，不再继续发送过期 Cookie。
- **`verify-account` 移动设备验证自动熔断隔离**：新增 `is_account_verification_required_message()` 与 `AuthError.verification_required`，识别 `verify-account` / `verify?` 验证 URL、`must be verified before it can continue using the Gemini API`、`account verification required to restore access`、`free_tier_verification_required` 等上游账号验证信号；`routing_pipeline` 命中后跳过 `tier_allows` 误判与同账号重签重试，直接按 `session_expired` 永久隔离该账号并在请求期换绑切号，后续 `is_account_available` 与 `select_rebind_account` 均排除该账号；管理台重新导入凭据或点击清除锁定后恢复可用。

## [2.1.1] - 2026-11-10

### Changed

- **Responses 协议转换器日志降级**：`/v1/responses` 协议转换器将常规非核心忽略字段（`store`、`include`、`reasoning.summary`、`parallel_tool_calls`、`truncation`、`metadata`、`user`、`service_tier`）日志级别从 `WARNING` 降为 `DEBUG`，仅保留 `seed` 的 `WARNING`（遵守 E-04）。

### Added

- **油猴长效抓取助手与安装路由**：恢复并增强油猴多账号长效抓取脚本 `src/aistudio_api/tools/aistudio-cookie-grabber.user.js` 及在线安装路由 `GET /api/tools/aistudio-cookie-grabber.user.js`（保留结构化多域名 `cookieObjects`、新增下载 `.json` 凭据包按钮、桌面端自动唤起 Tampermonkey 安装界面、移动端浏览器自动阻断并提示）。
- **Google /RotateCookies 凭据主动轮换与同源兄弟账号同步**：合入 Google `/RotateCookies` 主动滚动凭据轮换（`rotate_google_cookies`）、`POST /api/accounts/{id}/rotate-cookies` 端点及同源多登兄弟账号同步（Sibling Sync）；各子账号独立持有完整 `auth.json`，删除任意重复/同源子账号不影响其余账号保活。

### Fixed

- **批量导入解析与长效根凭据识别**：修复 `/api/accounts/import-bundle` 与前端导入弹窗对单账号 Playwright `storageState`（`auth-*.json`）及 Cookie-Editor 数组的解析断层；新增「账号管理」全页面与弹窗内 `.json` 拖拽导入、多文件批量合并及 `accounts.google.com` 长效根凭据（`LSID` / `__Host-1PLSID`）识别提示。

## [2.1.0] - 2026-11-08

2.1.0 在 2.0.0 多实例池基础上引入 browser 与 browserless 双引擎，补齐 WAA 独立签名器、设置页事务热切换与 Docker 单镜像双引擎。

### Added

- **双引擎**：新增 `engine.mode` 全局二选一。`browser` 保持 CloakBrowser/CDP 单页实例；`browserless` 为每个槽位独立持有 BrowserlessSession（Cookie、确定性指纹、proof 铸造器与传输器），不启动 Chromium。
- **WAA 签名器**：新增 Go + SpiderMonkey 152 独立签名服务，提供 `POST /v1/proof/mint`、`POST /v1/rpc/send`、`POST /v1/worker/release` 与 `GET /health`；支持 `local_subprocess` 与 `remote_http` 两种接入方式。
- **引擎热切换**：设置页保存后按 Validate → Preflight → Drain → Destroy → Rebuild → Commit 事务切换，失败自动回滚；切换期间新请求返回 503 并带 `Retry-After`。
- **引擎状态与探活**：`/api/system/status`、`/api/instances` 暴露引擎与切换阶段，新增 `/api/engine/health-check`（兼容 `/config/waa/health-check`）。
- **Docker 单镜像双引擎**：主镜像新增 waa-signer 多阶段构建，静态二进制落在 `/app/waa-signer/bin/waa-signer`，与默认 `engine.browserless.signer.binary_path` 一致；镜像同时内置 CloakBrowser 与 waa-signer。
- **测试**：补充双引擎切换生命周期、切换重入、池化会话回归与 waa-signer 单元测试。

### Changed

- `engine` 配置真实接线：`rollback_on_partial_failure`、`read_timeout_s`、`request_timeout_s` 接入引擎切换与签名传输；移除未实现的 `keep_control_plane`、`health_interval_s`、`http3` 等空配置项。
- browserless 每个槽位独立持有会话状态，Cookie 回写通过 `/v1/rpc/send` 与 `/v1/proof/mint` 响应及 `X-WAA-Set-Cookies` 头回传，不再依赖全局 `last_headers`。
- Mint/RPC 请求显式传递稳定 `accountKey`（优先账号 ID，缺失时回退 Cookie 哈希），Cookie 轮换前后 Worker 键保持稳定，并与 `/v1/worker/release` 对齐。
- Docker 运行时补充时区与 locale 数据；waa-signer 使用 Go 内嵌 `time/tzdata`，无需外部时区库。

### Fixed

- 修复 Cookie 轮换导致 Worker 键漂移、proof 与 RPC 落到不同 Worker 的问题。
- 修复引擎切换期间旧引擎未完全关闭，或新引擎失败后未回滚的问题。
- 修复 `X-WAA-Set-Cookies` 在日志中可能泄漏 Cookie 的问题，统一掩码并记录截断告警。

### Security

- waa-signer `/v1/rpc/send` 限制请求体 32 MiB，仅允许 `https://` 且 host 命中 Google 官方域名白名单，拒绝 userinfo、非 443 端口、IP 字面量与第三方 URL（SSRF 防护）。
- 签名器日志仅记录 Cookie 数量，不记录 Cookie、Authorization 或 API Key 内容；响应头 Cookie 摘要超限时截断并显式标记。

## [2.0.0] - 2026-11-08

2.0.0 重建基准：在保留上游协议能力与前端界面的前提下，将运行时重构为“多实例池 + 请求期路由”架构，
补齐账号、保活、日志、模型发现与安全链路，并完成四协议接入、确定性浏览器指纹、SSE 首包保活与双口径统计台账。

### Added

- **多实例池**：`instance_pool.py` 以“1 实例 = 1 账号 + 1 浏览器上下文 + 1 页面 + 1 surface”为单位管理并发，
  `active_requests` 是唯一并发原语；Free 与付费账号分 lane 隔离，容量按配置计算，不自动扩容。
- **请求期路由**：`routing_pipeline.py` 解析 `chat/`、`build/` 模型前缀，按 tier 准入与负载均衡选择实例；
  排序依据为在途请求数、账号最低满足 tier、总/模型请求数、最近使用与实例编号。
- **统一生成主链**：`generation_service.py` 统一负责路由获取、同账号空响应重试、账号/实例切换与 usage/finish 提取；
  Gemini、OpenAI Chat Completions、Claude Messages 与 OpenAI Responses 共用该链路。
- **四协议默认接入（C3）**：Gemini 核心协议常驻；`/v1/chat/completions`、`/v1/messages`、`/v1/messages/count_tokens`、
  `/v1/responses`、`/v1/responses/input_tokens` 全部接入，`features.formats.openai/claude/responses` 默认 `true`，
  可显式置 `false` 关闭。Claude 契约与转换位于 `application/contracts/claude.py`、
  `application/format_converters/claude_{request,response}.py`；Responses 对应 `contracts/responses.py`、
  `format_converters/responses_{request,response}.py`；路由分别位于 `api/routes_claude.py`、`api/routes_responses.py`。
- **确定性桌面指纹（C1）**：`infrastructure/browser/fingerprint.py` 按账号标识（或 auth 文件目录+文件名）SHA-256
  派生固定的 Windows 桌面指纹（NVIDIA/Intel/AMD ANGLE/D3D11 GPU、屏幕分辨率/DPR、CPU/内存、UA/平台/语言），
  导航前通过 `fingerprint_spoof.js` 注入；脚本注册 fail-closed，屏幕指标覆盖 best-effort。
- **Tier 单源与同步（C1b）**：`domain/model_access.py` 统一 `normalize_tier` / `tier_header_value` /
  `resolve_tier_selector` / lane 判定；`InstanceManager.sync_account_tier` 与 `InstancePool.sync_account_tier`
  在手动探测到 tier 变化时原位同步同 lane 槽位，跨 lane 时退役旧绑定并在新 lane 重新绑定。
- **SSE 首包与保活（C1c）**：`api/sse_keepalive.py` 提供 `KeepaliveIterator`，四个流式协议共享
  首事件预读窗口（默认 15 秒）与 `: keepalive\n\n` 心跳；首窗内失败保留真实 HTTP 4xx/5xx，慢首包切换为 200 + 心跳。
- **快照加固（C1d）**：快照生成只把提示词末尾 256 个 Unicode 码点写入输入框；页面内先用占位 hash 预热
  `dms[snapKey]`，并要求快照以 `!` 开头且长度 `>= 1520`，低质量时强制预热重试，仍不合格则显式失败。
- **工具调用与 ATG**：签名缓存、流式截断 JSON 修复与 Struct 形状归一；ATG（Anti-Truncation Gateway）支持
  `off/prefer/required`，默认关闭，`mode_by_format` 可按四协议覆盖。
- **账号体系**：Cookie 导入与跨域刷新、多子账号递归探测、账号预设与分组、上游原生 tier 与 Paid 识别；
  账号文件使用临时文件加原子替换写入。
- **模型发现**：通过池化会话直接调用上游 ListModels，运行时解析 `access_tier` 与 `accessModes`，
  以 live 结果覆盖静态规则中的 Free/Paid 判定。
- **保活调度**：`account_keepalive` 提供元数据刷新、Cookie 回写与可选的活跃活动任务，默认 24 小时周期，
  单全局锁串行执行，忙碌时跳过且不推进下次执行时间。
- **日志子系统**：stderr、内存环形缓冲（SSE 实时推送）与按日归档三路 sink；日志页支持级别过滤、搜索、
  暂停与 1/3/7 天导出。
- **日志沙盒注入（C2c）**：`ui_debug` 启动时 `UiDebugIsolation` 在配置/日志/API Key 路由初始化前把
  `AISTUDIO_CONFIG_PATH`、`AISTUDIO_LOG_DIR`、数据目录、统计与 dump 路径重定向到临时目录，关闭时恢复并清理。
- **双口径统计台账（C4）**：Client 口径每个客户端生成请求只记 1 次终态（429 同号重试或切号成功后仍为 1 次成功、
  1 次救援），Attempt 口径每次真实上游尝试各记 1 次；`GET /stats` 返回全站汇总、`by_model` 分项与
  最近 90 个 Asia/Shanghai 自然日的 `daily`（含每日 `by_model`），支持 `?days=7|30|90`。
- **趋势可视化**：用量统计页内嵌 DeepSeek 风格双栏 SVG 趋势（请求次数渐变面积曲线 + Tokens 分项柱状图），
  支持近 7/30/90 天、全模型汇总或单模型筛选与悬停详情。
- **UI 调试沙盒（C2a）**：`diagnostics.ui_debug` / `AISTUDIO_UI_DEBUG` 启用纯离线内存沙盒，
  不启动真实浏览器、账号、实例池、keepalive 或网络；提供确定性账号/实例/模型/统计种子与模拟操作。
- **配置与鉴权**：`AISTUDIO_CONFIG_PATH` 指定配置路径，文件缺失时从 `defaults/config.yaml` 播种；
  支持深合并、热重载与原子写回；对外 API 默认要求 API Key，且 `/v1/messages*`、`/v1/responses*`、`/v1/models*`
  同时接受 `x-api-key`、`Authorization: Bearer` 与 `x-goog-api-key`。
- **旧版配置自愈**：启动校验时自动移除废弃的 `groups`、`channel_registry`、`channels` 键，
  将 legacy `min_tier: Free` 归一为 `only_tier: Free`，并用默认模板深合并补齐新配置块；
  仅在内容实际变化时原子写回，不覆盖当前干净配置，也不改写受版本控制的默认模板。
- **Web 控制台**：账号、实例、设置、日志与仪表盘页面；静态前端产物随仓库发布。
- **容器与 CI**：Dockerfile 内置默认配置与静态前端；GHCR 镜像自动构建，支持 `master`/`main` 分支、版本标签、
  每日定时与手动触发。
- **应用层请求契约**：`application/contracts/` 提供 Gemini/OpenAI/Claude/Responses HTTP 请求 DTO 与
  `application/finish_reasons.py`，使应用层无需依赖 FastAPI 或 API 模块。

### Changed

- 运行时从“控制面单页会话 + 后台自动切换”改为“请求实例池 + 请求期换绑”；控制面只承担管理操作，不占用请求实例容量。
- 四协议默认全部启用；Gemini 核心协议面常驻，无独立开关。旧版“Claude/Responses 默认关闭”的口径作废。
- 流式输出不再受请求级总超时限制，支持无限时长输出；仅保留工具调用的短空闲终止窗口。
- 请求按上游 BotGuard 快照就绪状态启动，并按请求固定 `SessionContext`，避免跨请求串扰。
- 路由候选顺序直接使用账号 tier 秩，实例选择结果可重复、可解释。
- 配置以 `defaults/config.yaml` 为唯一受版本控制的默认值；根 `config.yaml` 仅作本地运行态，不纳入版本控制。
  运行时默认配置直接读取该模板（含诊断开关、指纹开关、路由退避、完整模型画像与冷却参数），模板缺失时启动明确报错。
- 收紧分层边界：Application 不再导入 API/FastAPI 或读取全局 `runtime_state`，请求统计改为构造注入回调；
  Gemini/OpenAI/Claude/Responses 协议渲染与错误映射归位 API 层，Tier/Paid 共享规则归位 Domain，
  Infrastructure 不再反向依赖 Application。
- 兼容性：旧 `pg/` 前缀已移除；生成接口接受显式 `chat/` 前缀，但模型元数据目录不输出也不接受该前缀。
- 配置启动迁移写回的是已应用非关键回退的有效配置（例如非法 `logging.level` 会持久化为 `INFO`），
  `rotator.cooldowns.auth_error` 默认值保持 `0`，403 认证失败不产生固定冷却锁。
- `X-AIStudio-G1-Tier` 映射与 tier 归一集中到 `domain/model_access.py`（`tier_header_value`/`normalize_tier`），
  Cookie 解析、transport、模型发现、账号刷新与 tier 同步共用单一来源；Free 账号仍不发送该请求头。
- 429 同账号首次重试前默认退避 `2.0s`（`features.routing.retry_same_delay_sec`，可用
  `AISTUDIO_RETRY_SAME_DELAY_SEC` 覆盖，范围 0..30）。
- 403 同账号重试前强制 `refresh_page_auth_state` 重新读取 CDP Cookie；刷新后仍无 SAPISID 时不再重发过期凭据，直接进入切号/报错。

### Fixed

- 修复 403 级联切号：首次同账号刷新鉴权并重新签名重试，第二次才切号；付费 tier 不足的 403 不触发切号。
- 修复 429 处理：同账号退避重试一次后池内切号，预算耗尽返回 503。
- 修复流式截断与意外终止：补齐不完整 JSON 分块，finish-only 与工具流可正确收尾，`CONTINUING` 不再被当作终止码。
- 修复工具调用参数与响应的 Struct 形状；缺少签名时从缓存取回或使用上游可接受的占位签名。
- 修复实例池重复预热与双 BotGuard 探针；池化启用时跳过控制面预热。
- 修复故障槽请求期换绑、路由均衡与实例 tier 选择器（`only_tier`/`min_tier`/`max_tier`）。
- 修复日志导出与归档写入的脱敏缺口，历史日志不再原样泄漏密钥或 Cookie。
- 修复只读配置目录下的启动失败：迁移写回失败时记录警告并继续以内存中已校验的配置启动，同时清理残留临时文件。
- 容器 HEALTHCHECK 改为认证豁免的 `/health`；成功探针不再产生 INFO/DEBUG 访问日志或环形缓冲记录。
  只读模型列表的缺 Key 401 按客户端 IP/path 在 5 分钟窗口内去重；WARN 附带截断到 80 字符的 `User-Agent`
  与去除查询串/片段的 `Referer`，重复项降为 DEBUG。
- **C2d 实机反馈修复**：清除 `stats.json` 中 `finish_reasons` 等保留键产生的幽灵模型行；实例池只显示 `[最新]`，
  运行中实例统一绿色激活边框与 `[🟢 实例 #X 运行中]` 徽章，不再重复蓝色 `[实例 #X]` 标签；恢复 CDP、日志级别与
  Free/Plus/Pro/Ultra/Ultra_20X 等通用英文标识。
- **C4b 设置页修复**：修正 `?` Tooltip 错误显示“推荐配置: false”的根因（`FieldHelpTip` 的 `recommended` 被
  Boolean 默认值覆盖），新增推荐值格式化并与 `defaults/config.yaml` 75 个词条对齐；Tooltip 实现视口夹紧、
  移动端最大宽度、上下翻转与箭头定位，1440/1024/768/375 视口均不出界。
- **C4b 沙盒统计自洽**：`ui_debug` 种子强制 `prompt = cached + uncached`、`total = prompt + completion`、
  `attempts = success + rate_limited + errors`，`simulate_requests` / `simulate_429` 同步维护该恒等式。

### Removed

- 移除请求级全局超时 watchdog 与后台自动切换/再平衡，相关行为改由路由层显式重试与换绑承担。
- 移除蓝绿发布与就绪探测的旧调度语义，以及 lease/reservation/pinned/defer 等预留机制。
- 移除旧的全账号兜底切换路径，避免绕过实例池直接操作浏览器会话。
- 移除仓库中的历史批次台账与失败草稿，仅保留归档记录。
- 移除越层的 `application/api_service_gemini.py`、`api/schemas.py` 与 `api/schemas_openai.py`，由新契约与 API 模块取代。
- 移除“Claude/Responses 适配器未启用”“四协议默认关闭”等过时描述与相关 feature-gate 拒绝路径。

### Security

- 对外接口默认拒绝匿名访问；仅显式设置 `security.api_allow_anonymous: true` 时放开。
- `/v1/messages*`、`/v1/responses*`、`/v1/models*` 与 Gemini 生成接口统一 API Key 守卫，支持 `x-api-key`、
  `Authorization: Bearer` 与 `x-goog-api-key`。
- 浏览器指纹脚本注册 fail-closed：注册失败即中止会话启动并把实例置为 invalid，避免暴露 SwiftShader 软件渲染器。
- 日志在写入环形缓冲与归档文件前统一遮蔽 Authorization、Cookie、Set-Cookie、Google API Key、Bearer 与 `sk-*` 令牌；
  导出时对历史行再次脱敏，形成纵深防御。
- 请求与响应 dump 只保留白名单字段，禁止明文记录请求头、Cookie、令牌与完整响应体。
- API Key 的创建、删除与更新日志固定输出 `***`，不记录 Key 或前缀。
- Cookie 仅保留核心认证项并剔除易失效项；账号与配置写回使用原子替换，避免半写状态。

## [0.1.0] - 2026-05-16

- 初始开源版本：提供 Google AI Studio 的 OpenAI 兼容接口、账号 Cookie 管理与基础请求转发能力。
