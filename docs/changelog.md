# Changelog

本文件记录用户可见与架构级变更，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
阶段审计细节、提交对照与逐项核对记录见 `docs/dev/rebuild/domains/`，不在此重复。

## [2.0-rebuild] - 2026-10-31

重建版本：在保留上游协议能力与前端界面的前提下，将运行时重构为“多实例池 + 请求期路由”架构，
并补齐账号、保活、日志、模型发现与安全链路。

### Added

- **多实例池**：`instance_pool.py` 以“1 实例 = 1 账号 + 1 浏览器上下文 + 1 页面 + 1 surface”为单位管理并发，
  `active_requests` 是唯一并发原语；Free 与付费账号分 lane 隔离，容量按配置计算，不自动扩容。
- **请求期路由**：`routing_pipeline.py` 解析 `chat/`、`build/` 模型前缀，按 tier 准入与负载均衡选择实例；
  排序依据为在途请求数、账号最低满足 tier、总/模型请求数、最近使用与实例编号。
- **统一生成主链**：`generation_service.py` 统一负责路由获取、同账号空响应重试、账号/实例切换与 usage/finish 提取；
  OpenAI Chat Completions、`/v1/models` 与 Gemini 原生 `generateContent`、`streamGenerateContent`、`countTokens` 共用该链路。
- **工具调用与 ATG**：签名缓存、流式截断 JSON 修复与 Struct 形状归一；ATG（Anti-Truncation Gateway）支持
  `off/prefer/required`，默认关闭。
- **账号体系**：Cookie 导入与跨域刷新、多子账号递归探测、账号预设与分组、上游原生 tier 与 Paid 识别；
  账号文件使用临时文件加原子替换写入。
- **模型发现**：通过池化会话直接调用上游 ListModels，运行时解析 `access_tier` 与 `accessModes`，
  以 live 结果覆盖静态规则中的 Free/Paid 判定。
- **保活调度**：`account_keepalive` 提供元数据刷新、Cookie 回写与可选的活跃活动任务，默认 24 小时周期，
  单全局锁串行执行，忙碌时跳过且不推进下次执行时间。
- **日志子系统**：stderr、内存环形缓冲（SSE 实时推送）与按日归档三路 sink；日志页支持级别过滤、搜索、
  暂停与 1/3/7 天导出。
- **配置与鉴权**：`AISTUDIO_CONFIG_PATH` 指定配置路径，文件缺失时从 `defaults/config.yaml` 播种；
  支持深合并、热重载与原子写回；对外 API 默认要求 API Key。
- **旧版配置自愈**：启动校验时自动移除废弃的 `groups`、`channel_registry`、`channels` 键，
  将 legacy `min_tier: Free` 归一为 `only_tier: Free`，并用默认模板深合并补齐新配置块；
  仅在内容实际变化时原子写回，不覆盖当前干净配置，也不改写受版本控制的默认模板。
- **Web 控制台**：账号、实例、设置、日志与仪表盘页面；静态前端产物随仓库发布。
- **容器与 CI**：Dockerfile 内置默认配置与静态前端；GHCR 镜像自动构建，支持 `rebuild-upstream`、`main`、`master`
  分支与手动触发。
- **应用层请求契约**：新增 `application/contracts/`（Gemini/OpenAI HTTP 请求 DTO）与
  `application/finish_reasons.py`，使应用层无需依赖 FastAPI 或 API 模块。

### Changed

- 运行时从“控制面单页会话 + 后台自动切换”改为“请求实例池 + 请求期换绑”；控制面只承担管理操作，不占用请求实例容量。
- 流式输出不再受请求级总超时限制，支持无限时长输出；仅保留工具调用的短空闲终止窗口。
- 请求按上游 BotGuard 快照就绪状态启动，并按请求固定 `SessionContext`，避免跨请求串扰。
- 路由候选顺序直接使用账号 tier 秩，实例选择结果可重复、可解释。
- 配置以 `defaults/config.yaml` 为唯一受版本控制的默认值；根 `config.yaml` 仅作本地运行态，不纳入版本控制。
  运行时默认配置直接读取该模板（含诊断开关、路由退避、完整模型画像与冷却参数），模板缺失时启动明确报错，
  不再维护可能与模板漂移的内嵌默认副本。
- Claude 与 Responses 协议保留配置开关但默认关闭；Build surface 在对应阶段前显式拒绝，而非静默切换 surface。
- 吸收上游对内存占用、readiness watchdog、`Ultra_20X` tier 归一与 Web 配置编辑器的修复。
- 收紧分层边界：Application 不再导入 API/FastAPI 或读取全局 `runtime_state`，请求统计改为构造注入回调；
  Gemini 协议渲染与错误映射归位 API 层，Tier/Paid 共享规则归位 Domain，Infrastructure 不再反向依赖 Application。
- 兼容性：旧 `pg/` 前缀已移除；生成接口接受显式 `chat/` 前缀，但模型元数据目录不输出也不接受该前缀。
- 配置启动迁移写回的是已应用非关键回退的有效配置（例如非法 `logging.level` 会持久化为 `INFO`），
  `rotator.cooldowns.auth_error` 默认值调整为 `0`，403 认证失败不再产生固定冷却锁。

### Fixed

- 修复 403 级联切号：首次同账号重新签名重试，第二次才切号；付费 tier 不足的 403 不触发切号。
- 修复 429 处理：同账号重试一次后池内切号，预算耗尽返回 503。
- 修复流式截断与意外终止：补齐不完整 JSON 分块，finish-only 与工具流可正确收尾，`CONTINUING` 不再被当作终止码。
- 修复工具调用参数与响应的 Struct 形状；缺少签名时从缓存取回或使用上游可接受的占位签名。
- 修复实例池重复预热与双 BotGuard 探针；池化启用时跳过控制面预热。
- 修复故障槽请求期换绑、路由均衡与实例 tier 选择器（`only_tier`/`min_tier`/`max_tier`）。
- 修复日志导出与归档写入的脱敏缺口，历史日志不再原样泄漏密钥或 Cookie。
- 修复只读配置目录下的启动失败：迁移写回失败时记录警告并继续以内存中已校验的配置启动，
  同时清理可能残留的临时文件。

### Removed

- 移除请求级全局超时 watchdog 与后台自动切换/再平衡，相关行为改由路由层显式重试与换绑承担。
- 移除蓝绿发布与就绪探测的旧调度语义，以及 lease/reservation/pinned/defer 等预留机制。
- 移除旧的全账号兜底切换路径，避免绕过实例池直接操作浏览器会话。
- 移除仓库中的历史批次台账与失败草稿，仅保留归档记录。
- 移除越层的 `application/api_service_gemini.py`、`api/schemas.py` 与 `api/schemas_openai.py`，由新契约与 API 模块取代。

### Security

- 对外接口默认拒绝匿名访问；仅显式设置 `security.api_allow_anonymous: true` 时放开。
- 日志在写入环形缓冲与归档文件前统一遮蔽 Authorization、Cookie、Set-Cookie、Google API Key、Bearer 与 `sk-*` 令牌；
  导出时对历史行再次脱敏，形成纵深防御。
- 请求与响应 dump 只保留白名单字段，禁止明文记录请求头、Cookie、令牌与完整响应体。
- API Key 的创建、删除与更新日志固定输出 `***`，不记录 Key 或前缀。
- Cookie 仅保留核心认证项并剔除易失效项；账号与配置写回使用原子替换，避免半写状态。

## [0.1.0] - 2026-05-16

- 初始开源版本：提供 Google AI Studio 的 OpenAI 兼容接口、账号 Cookie 管理与基础请求转发能力。
