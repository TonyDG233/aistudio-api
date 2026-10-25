# 06 Feature Ledger

状态：已合并 12 个 domain 批次；P1-P10 已全部决定，pending=0。
基线：`upstream/master`；开发范围：`upstream/master..master`（含 `p1-ark-merge` 95 个独有提交 + master 后 8 个修复提交）。
证据优先级：代码/日志/SSE/fixture > `docs/dev/rebuild/01-05`；02/05 只作调查线索，不作事实来源。
约束：不修代码、不合并 master、不访问真实账号/IP；每行必须有 provenance 和证据，缺失必须显式标注。全局规则：上游原生实现无必要修改则不动；UI 布局、文案、交互和细节不得无故重写或删除。
索引：`06-feature-index.csv`（`Dxx-NNN` 唯一 feature ID）；决策记录：`06-decision-log.md`。

## 1. 已验证生命周期（只作 domain 框架，不是功能清单）

| Domain | 入口 | 关键状态/行为 | 失败/清理 | 证据 |
|---|---|---|---|---|
| Boot & Config | `api/app.py` import/lifespan | import 时 `setup_logging`；lifespan 创建 client、manager、rotator、refresher、keepalive，后台 warmup 与 startup | 关闭时取消后台任务、停 keepalive、`client.close()` | `api/app.py:49-50`, `:54-267`, `:376-477` |
| Account / Auth / Paid | `AccountStore`、`AccountInfoRefresher`、`ModelDiscoveryService` | active account、tier/email、cookie auth、ListModels `paid`、paid 默认种子 | 过期/refresher 异常；paid 只影响资格/渠道，不直接建立实例 | `model_access.py:11-64`, `model_discovery.py:191`, `:230`, `:288`, `:352`, `:526-632`, `surface_orchestrator.py:215-240`, `routing_eligibility.py:183-185` |
| Browser / CDP / Context / Page | `BrowserSession`、`CDPClient` | 控制面 browser 懒启动；`Target.createBrowserContext(disposeOnDetach=True)`；page 可在 context 内创建 | CDP detach/crash 将 page 标记 closed；dispose context；idle 可关整个 browser | `session.py:564-603`, `:197-215`, `cdp_client.py:955-981`, `:375-396` |
| Instance / Pool / Lease / Capacity | `InstanceManager`、`InstancePool` | manager 账号+channel 记录；pool slot 以 Context 为单位；同账号可复用 Context 加 chat/build 页；build-only 关闭 chat 页且无 PooledSession | capacity 拒绝/等待；release 只减 reservation；close 拒绝在途；最终关页+dispose context | `instance_manager.py:117-246`, `:536-590`, `:834-903`; `instance_pool.py:202-211`, `:411-531`, `:533-606`, `:642-750`, `:832-850`, `:963-1047`, `:1214-1231` |
| Routing / Candidate / Switch / Retry | `RoutingPipeline.begin/acquire`、`handle_attempt_exception` | eligible 账号 → candidate 排序 → acquire lease；429/auth/hang 切号；瞬态错误走同账号 retry 预算 | 无候选/预算耗尽/熔断 → `SurfacePolicyError`；`retry_same` 当前未被 `record_retry` 置真 | `routing_pipeline.py:241-396`, `:419-497`, `:514-522`; `upstream_errors.py:186-346`; `routing_eligibility.py:177-190` |
| Generation / Protocol / Wire | `generate/service`、`AIStudioClient` | 非流式 capture/replay；流式 capture + StreamingGateway；build 走 REST body；ATG 包装/解包；usage/finish 事件 | 失败分类、流式已出数据不可切号；lease 在 finally 释放 | `generation_service.py:160-457`, `:506-852`; `client.py:557-628`, `:668-750` |
| Build Surface / 9998 / Signer | `BuildSurface.ensure_ready`、`stream_generate` | 页面建/复用；cookie 注入；9998 native 就绪；signer 后台探测；warmup；watchdog；native 优先、signer 备用 | 新页未就绪则关闭；BUSY/PAGE_CLOSED/TIMEOUT/NO_FRAMES；stream finally 清 task queue | `build_surface.py:975-1188`, `:1323-1360`, `:1553-1842`; `client.py:158-177`, `:589`, `:699` |
| Keepalive / Rotation / 24h | `AccountKeepaliveService` | 四类 job：metadata、auth update、auto switch、active activity；单一 job 锁；auto switch 走 InstanceSwitcher.rebalance | no page/busy/expired；auth update 走 pool 或 active session；失败记录 last_error | `account_keepalive.py:100-224`, `:311-424`; `instance_switch.py:140-216` |
| Logs / Observability | `setup_logging`、HTTP middleware、broadcaster、logs routes | root DEBUG 捕获全量；stderr/broadcast/daily file 三 sink；ring 5000；SSE replay + heartbeat；导出 7 天 | 低级别洪泛时保留 WARNING+；订阅上限 8；队列满丢弃提示 | `logger.py:264-297`; `app.py:376-477`; `log_broadcast.py:63-78`, `:136-211`; `routes_logs.py:34-153`, `:232-240` |
| Stats / Token / Usage | `RuntimeState.record/record_client/record_token_count` | 上游 attempt 与客户端 outcome 两套计数；format/surface 分桶；daily 90 天；countTokens 独立计数 | 失败/abort 分类；usage_missing；2s debounce/5s 强制落盘 | `state.py:302-582`; `routes_system.py:119-219`; `token_counter.py:274-302`; `generation_service.py:401`, `:787` |
| API / UI | FastAPI routes 与 Vue views | Gemini/OpenAI/Claude/Responses 适配；logs/stats/settings 页面 | 适配层错误映射；UI 只消费 API | `api/routes_gemini.py`, `routes_claude.py`, `routes_responses.py`; `web/src/views/LogsView.vue`, `StatsView.vue` |
| Tests / Evidence | `tests/unit` 与 `%TEMP%\kilo` | 仓库 TDD 是回归实现形状；temp 探针/SSE/日志是原始行为证据 | 不重跑真实 CDP/账号；不复制 auth/cookie | `tests/unit/*`; `%TEMP%\kilo` 见 05 |

## 2. 台账 Domain

### D01 Boot & Config

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D01 Boot & Config | FastAPI app 单例与 lifespan 挂载 | upstream/master..master + src/aistudio_api/api/app.py:54-270 | 原生 | partial | test_routes_system.py / test_format_error_routing.py 导入并请求 app；lifespan 未直接测试 | keep | keep |
| D01 Boot & Config | 进程退出清理钩子 | upstream/master..master + src/aistudio_api/api/app.py:64-68 | 原生 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | AIStudioClient 启动初始化 | upstream/master..master + src/aistudio_api/api/app.py:70-73 | 原生 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | AccountStore / AccountService 初始化 | upstream/master..master + src/aistudio_api/api/app.py:76-78 | 原生 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | InstanceManager 初始化与账号切换回调 | 79901b0 + src/aistudio_api/api/app.py:80-96 | 新增 | partial | test_instance_manager.py:300-344 覆盖 manager.startup；app 装配未验证 | port | port |
| D01 Boot & Config | 黏性 rotator 初始化 init_rotator | upstream/master..master + src/aistudio_api/api/app.py:98-100 | 原生 | legacy | test_runtime_persistence.py:46-91 覆盖 rotator 持久化；app 装配未验证 | drop | port |
| D01 Boot & Config | AccountInfoRefresher 初始化 | 1f9ba61 + src/aistudio_api/api/app.py:102-103 | 新增 | partial | test_account_info_refresh.py；app 装配未验证 | port | port |
| D01 Boot & Config | 启动浏览器预热（优先激活 active 账号，3 次重试） | 1980ada + src/aistudio_api/api/app.py:111-133 | 改造 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | 实例预启动 task（warmup 后 manager.startup） | da6ea25 + src/aistudio_api/api/app.py:135-149 | 新增 | partial | test_instance_manager.py:300-344 覆盖 startup；app task 未验证 | port | port |
| D01 Boot & Config | 模型发现周期预热 task | 1980ada + src/aistudio_api/api/app.py:151-163 | 新增 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | Keepalive 服务启动 | 1f9ba61 + src/aistudio_api/api/app.py:165-173 | 新增 | partial | test_account_keepalive.py；app 装配未验证 | port | port |
| D01 Boot & Config | 浏览器空闲回收监控与活跃工作保护 | 86391c8 + src/aistudio_api/api/app.py:175-195 | 改造 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | 启动时账号 email/tier 自动校验 | 47cec70 + src/aistudio_api/api/app.py:197-241 | 改造 | unverified | 未验证 | port | port |
| D01 Boot & Config | 关闭流程：取消后台任务、停 keepalive、client.close、清空 runtime_state | da6ea25, 1980ada, 1f9ba61 + src/aistudio_api/api/app.py:243-267 | 改造 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | 基础路由注册（system/accounts/gemini/models） | upstream/master..master + src/aistudio_api/api/app.py:270-290 | 原生 | working | test_routes_system.py, test_routes_models.py, test_format_error_routing.py | keep | keep |
| D01 Boot & Config | 管理面路由注册（logs/keepalive/instances + web auth） | eed30b6, 1f9ba61, 79901b0 + src/aistudio_api/api/app.py:273-284 | 新增 | partial | test_logs_routes.py, test_routes_keepalive.py, test_routes_instances.py | port | port |
| D01 Boot & Config | 协议面路由注册（openai/responses/claude + api key） | c0412c8, 1375646, 2b39b53 + src/aistudio_api/api/app.py:288-290 | 新增 | working | test_format_openai.py, test_format_claude.py, test_format_responses.py, test_format_error_routing.py | port | port |
| D01 Boot & Config | InstanceError 异常处理 | 79901b0 + src/aistudio_api/api/app.py:293-300 | 新增 | partial | test_routes_instances.py 间接覆盖；无 handler 专项测试 | port | port |
| D01 Boot & Config | SurfacePolicyError 映射客户端 4xx | 0cb1b82 + src/aistudio_api/api/app.py:303-308 | 新增 | working | test_app_error_handlers.py:11-21 | port | drop |
| D01 Boot & Config | BuildSurfaceError 映射 503/502 | 0cb1b82 + src/aistudio_api/api/app.py:311-334 | 新增 | working | test_app_error_handlers.py:24-28 | port | port |
| D01 Boot & Config | HTTPException 按 API 格式分派错误体 | c0412c8, dcb7592 + src/aistudio_api/api/app.py:337-353 | 改造 | working | test_format_error_routing.py:27-57 | keep | keep |
| D01 Boot & Config | RequestValidationError 按 API 格式分派错误体 | c0412c8 + src/aistudio_api/api/app.py:356-373 | 改造 | working | test_format_error_routing.py:85-106 | keep | keep |
| D01 Boot & Config | HTTP 请求生命周期日志与 dump 中间件 | upstream/master..master + src/aistudio_api/api/app.py:376-480 | 原生 | working | test_logging.py:233-245 | keep | keep |
| D01 Boot & Config | 静态资源 no-cache 挂载类 | a59c885 + src/aistudio_api/api/app.py:484-504 | 新增 | partial | test_routes_system.py:110-124 覆盖 SPA 头；static 资源未验证 | keep | keep |
| D01 Boot & Config | SPA 导航中间件与 Vary/Cache-Control | a59c885, eed30b6, c0412c8 + src/aistudio_api/api/app.py:515-566 | 改造 | working | test_routes_system.py:109-124 | keep | keep |
| D01 Boot & Config | /auth/check 认证状态端点 | upstream/master..master + src/aistudio_api/api/app.py:507-512 | 原生 | working | test_routes_system.py:100-106 | keep | keep |
| D01 Boot & Config | 根路径 / 前端兜底 | upstream/master..master + src/aistudio_api/api/app.py:569-579 | 原生 | working | test_routes_system.py:109-124 | keep | keep |
| D01 Boot & Config | CLI main 与 --port / --browser-port | upstream/master..master + src/aistudio_api/api/app.py:582-595 | 原生 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | .env 加载与默认文本/图片模型常量 | upstream/master..master + src/aistudio_api/config.py:15-22 | 原生 | working | config 模块被全部单测导入；专项未验证 | keep | keep |
| D01 Boot & Config | 布尔/整数环境变量解析与容错 | 3d01ed2, dceaa3d + src/aistudio_api/config.py:33-47 | 改造 | working | test_features_config.py:39-79 | keep | keep |
| D01 Boot & Config | 浮点/枚举环境变量解析（钳制与回退） | 3d01ed2, 1f9ba61 + src/aistudio_api/config.py:50-70 | 新增 | working | test_features_config.py:52-84 | keep | keep |
| D01 Boot & Config | 配置文件路径解析与模板初始化 | upstream/master..master + src/aistudio_api/config.py:105-130 | 原生 | working | test_runtime_persistence.py:94-122 | keep | keep |
| D01 Boot & Config | data/stats/rotator 路径解析 | upstream/master..master + src/aistudio_api/config.py:133-171 | 原生 | working | test_runtime_persistence.py:13-91 | keep | keep |
| D01 Boot & Config | 模型缓存文件路径解析 | 1980ada + src/aistudio_api/config.py:154-161 | 新增 | partial | test_model_discovery_paid.py 间接；路径解析专项未验证 | port | port |
| D01 Boot & Config | auth 文件发现（registry 活跃账号优先 + 扫描 + utf-8） | dceaa3d + src/aistudio_api/config.py:174-203 | 改造 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | 代理探测与浏览器 proxy 构造 | upstream/master..master + src/aistudio_api/config.py:206-247 | 原生 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | 时区与 locale 解析 | upstream/master..master + src/aistudio_api/config.py:250-273 | 原生 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | 浏览器空闲超时默认值改为 0 | 86391c8 + src/aistudio_api/config.py:276-283 | 改造 | unverified | 未验证 | keep | keep |
| D01 Boot & Config | Settings 基础字段（端口/浏览器/鉴权/超时/统计） | upstream/master..master + src/aistudio_api/config.py:286-323 | 原生 | working | config 导入与 test_features_config.py:87-93 | keep | keep |
| D01 Boot & Config | Settings 功能开关基线（WP1/WP10 环境变量） | 3d01ed2, 1f9ba61 + src/aistudio_api/config.py:324-359 | 新增 | working | test_features_config.py:87-93 | port | port |
| D01 Boot & Config | Settings aistudio_app_url | da6ea25 + src/aistudio_api/config.py:298 | 新增 | unverified | 未验证 | port | port |
| D01 Boot & Config | 模型默认值 YAML profiles/overrides/safety 解析与缓存 | upstream/master..master + src/aistudio_api/infrastructure/gateway/model_defaults.py:34-422 | 原生 | working | test_model_defaults.py:5-196 | keep | keep |
| D01 Boot & Config | config.yaml API key 解析与 mtime 缓存 | upstream/master..master + src/aistudio_api/infrastructure/gateway/model_defaults.py:425-483 | 原生 | working | test_format_error_routing.py 经 app 鉴权链间接覆盖 | keep | keep |
| D01 Boot & Config | logging 配置读取与写回 | upstream/master..master + src/aistudio_api/infrastructure/gateway/model_defaults.py:486-541 | 原生 | working | test_logging.py:181-230 | keep | keep |
| D01 Boot & Config | features 默认值、env 基线、YAML 深合并与归一化 | 3d01ed2 + src/aistudio_api/infrastructure/gateway/model_defaults.py:583-661,1022-1130,1174-1198 | 新增 | working | test_features_config.py:99-228 | port | port |
| D01 Boot & Config | features 热更新原子写回 | 1f9ba61 + src/aistudio_api/infrastructure/gateway/model_defaults.py:1200-1233 | 新增 | working | test_keepalive_config.py:14-39, test_features_config.py:286-323 | port | port |
| D01 Boot & Config | channels 配置归一化（别名/build legacy alias/allowed_tags） | cdb9e15, 7cd6d74 + src/aistudio_api/infrastructure/gateway/model_defaults.py:764-858 | 新增 | working | test_features_routing_config.py:24-123 | port | drop |
| D01 Boot & Config | routing 预算归一化（max_switches/global_max_pages/max_contexts/borrow/prewarm/circuit_breaker） | 8b6b52e, dd5e359 + src/aistudio_api/infrastructure/gateway/model_defaults.py:861-896 | 新增 | working | test_features_routing_config.py:126-154 | port | port |
| D01 Boot & Config | slots 配置归一化、坏项丢弃与去重 | cdb9e15 + src/aistudio_api/infrastructure/gateway/model_defaults.py:899-930 | 新增 | working | test_features_routing_config.py:157-181 | port | port |
| D01 Boot & Config | groups 配置归一化与 groups_enabled | d8bbb22, 7cd6d74 + src/aistudio_api/infrastructure/gateway/model_defaults.py:933-979,1044 | 新增 | working | test_features_groups.py, test_features_routing_config.py:184-199 | drop | drop |
| D01 Boot & Config | ATG 配置归一化（mode/mode_by_format/限额） | 792f4e1, dcb7592, 493b72f + src/aistudio_api/infrastructure/gateway/model_defaults.py:548-556,982-994,1087-1124 | 新增 | working | test_atg_config.py, test_features_config.py:99-228 | port | port |
| D01 Boot & Config | fallback 配置归一化（默认全 off） | 3d01ed2, 1f9ba61 + src/aistudio_api/infrastructure/gateway/model_defaults.py:655-660,1125-1129 | 新增 | working | test_features_config.py:128-132 | port | port |
| D01 Boot & Config | legacy features key 迁移（max_realms/global_max_instances/max_page_instances） | 8b6b52e + src/aistudio_api/infrastructure/gateway/model_defaults.py:1133-1171 | 新增 | partial | test_features_groups.py:50-92 覆盖 max_page_instances；routing 旧键未验证 | port | port |
| D01 Boot & Config | 渠道别名、已知渠道与 normalize_channel | cdb9e15 + src/aistudio_api/application/channel_registry.py:20-103 | 新增 | working | test_channel_registry.py:36-103 | port | drop |
| D01 Boot & Config | 渠道注册表构建与默认值 | cdb9e15 + src/aistudio_api/application/channel_registry.py:166-218 | 新增 | working | test_channel_registry.py:105-221 | port | drop |
| D01 Boot & Config | allowed_tags 与 max_pages=max_instances | 7cd6d74 + src/aistudio_api/application/channel_registry.py:49-70,138-150,187-217 | 改造 | working | test_channel_registry.py:156-181 | port | drop |
| D01 Boot & Config | enabled_channels/model_list_prefixes/is_multi_mode | cdb9e15 + src/aistudio_api/application/channel_registry.py:221-262 | 新增 | working | test_channel_registry.py:222-271 | port | drop |
| D01 Boot & Config | select_channel 不静默换渠道与 chat-only guard | cdb9e15, 8b6b52e + src/aistudio_api/application/channel_registry.py:344-405 | 新增 | working | test_channel_registry.py:273-406 | port | drop |
| D01 Boot & Config | setup_logging 三 sink（stderr/broadcast/daily file）与 7 天清理 | eed30b6 + src/aistudio_api/infrastructure/utils/logger.py:264-297 | 改造 | partial | test_logging.py 覆盖级别与 dump；三 sink 装配未直接断言 | port | port |
| D01 Boot & Config | 动态日志级别（root DEBUG + handler 分级） | eed30b6 + src/aistudio_api/infrastructure/utils/logger.py:234-261 | 改造 | working | test_logging.py:68-87 | keep | keep |
| D01 Boot & Config | dump 开关优先级与敏感字段掩码 | upstream/master..master + src/aistudio_api/infrastructure/utils/logger.py:72-227,311-337 | 原生 | working | test_logging.py:89-180,247-309 | keep | keep |
| D01 Boot & Config | get_logger 命名空间 | upstream/master..master + src/aistudio_api/infrastructure/utils/logger.py:300-306 | 原生 | working | test_logging.py:57-66 | keep | keep |

### D02 Account / Auth / Paid

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D02 | 账号注册表与原子持久化（AccountMeta/Registry/auth.json/meta.json/registry.json） | upstream/master..master; infrastructure/account/account_store.py:24-39,126-330 | 原生 | working | test_account_info_refresh.py::test_account_meta_backward_compatible_defaults, test_account_meta_roundtrip_with_refresh_fields | keep | keep |
| D02 | 账号元数据扩展字段（tier/tier_checked_at/expired/duplicate_of/keepalive/routing/tags） | 1f9ba61,dad9cf7,cdb9e15,fcd6f1f; account_store.py:126-194 | 改造 | working | account_store.py:549-671 落盘；test_account_info_refresh.py 字段往返用例 | keep | keep |
| D02 | 旧 `data/auth.json` 自动迁移为 `acc_migrated` 账号 | upstream/master..master; account_store.py:258-286 | 原生 | unverified | 仅代码路径，无迁移单测；未验证多账号/已存在 registry 分支 | keep | keep |
| D02 | 单份 Cookie 导入（POST /accounts/import-cookies，含 email/tier 探测） | upstream/master..master; api/routes_accounts.py:624-708; cookie_parser.py:429-442 | 原生 | working | test_cookie_import.py::test_parse_cookie_string_preserves_cross_domain_google_cookies, test_import_bundle_flow | keep | keep |
| D02 | 多格式 Cookie 解析（JSON 数组/对象、Netscape、Cookie Header） | upstream/master..master; cookie_parser.py:158-260 | 原生 | working | test_cookie_import.py::test_parse_raw_cookies_json_array_and_netscape | keep | keep |
| D02 | 跨域 Cookie 保真与域名优先级（parse_storage_state/_cookie_domain_rank） | 7cd6d74; cookie_parser.py:126-155,345-407; cookie_refresher.py:85-113 | 改造 | working | test_cookie_import.py::test_parse_cookie_string_preserves_cross_domain_google_cookies, test_cookie_injection_preserves_storage_state_domains | keep | keep |
| D02 | 瞬态/遥测 Cookie 丢弃与 `__Host-` 注入跳过 | upstream/master..master,7cd6d74; cookie_parser.py:89-108,284-342; cookie_refresher.py:31-40 | 改造 | working | test_cookie_import.py::test_parse_cookie_string_discards_transient_and_telemetry_cookies, test_domain_overrides_scopes_osid_strictly_to_aistudio | keep | keep |
| D02 | 多账号探活批量导入（POST /accounts/probe-import，u/0..u/N 无限探测） | upstream/master..master; cookie_parser.py:575-677; api/routes_accounts.py:711-761 | 原生 | working | test_cookie_import.py::test_probe_import_activates_first_account_and_registers_in_rotator | keep | keep |
| D02 | 凭据 Bundle 导入（POST /accounts/import-bundle，兼容旧项目 auth JSON） | 8ad9a63; api/routes_accounts.py:764-915 | 改造 | working | test_cookie_import.py::test_import_bundle_flow, test_import_bundle_accepts_single_old_project_auth_json | keep | keep |
| D02 | 账号删除与活跃账号兜底迁移（单删 + Cookie 组删） | upstream/master..master; api/routes_accounts.py:490-524,565-607; account_store.py:478-495 | 原生 | working | test_cookie_import.py::test_delete_cookie_group_migrates_active_account | keep | keep |
| D02 | 批量删除（POST /accounts/batch-delete，去重） | 7cd6d74; api/routes_accounts.py:527-562 | 新增 | working | test_routes_accounts.py::test_batch_delete_accounts_deduplicates_and_deletes | keep | keep |
| D02 | 账号激活（POST /{id}/activate + AccountService.activate_account，BotGuard 模板重试/失败回滚） | upstream/master..master; application/account_service.py:52-124; api/routes_accounts.py:270-290 | 原生 | partial | test_browser_session.py:283 起；test_account_orchestrator.py:169-294；回滚分支未单独断言 | keep | keep |
| D02 | 邮箱探测与回填（OGS/AI Studio HTML `oPEP7c` + data-au，按 auth_user） | upstream/master..master,0a3342b; cookie_parser.py:445-526; api/routes_accounts.py:293-379 | 改造 | partial | test_cookie_import.py::test_parse_emails_from_ogs_html_and_meta_reconciliation；detect-email 路由级未验证 | keep | keep |
| D02 | 订阅等级探测映射（GetAiStudioBenefitTier：0 Free/1 Pro/2 Ultra/3 Plus） | upstream/master..master; cookie_parser.py:22-61,529-572 | 原生 | working | test_cookie_import.py::test_parse_benefit_tier_and_update_account_tier; test_account_info_refresh.py::test_tier_mapping | keep | keep |
| D02 | AccountInfoRefresher 批量刷新（tier/expired/email、并发限流、409 进程内互斥） | dad9cf7,0a3342b; infrastructure/account/account_info_refresher.py:172-344; api/routes_accounts.py:200-267 | 新增 | working | test_account_info_refresh.py 前 13 项（含 test_batch_concurrency_bounded、test_batch_mutual_exclusion_raises） | keep | keep |
| D02 | 401 标记 expired；403 key 回收重试一次；5xx/网络失败不改状态 | dad9cf7; account_info_refresher.py:274-307,467-513 | 新增 | working | test_account_info_refresh.py::test_401_marks_expired, test_401_cleared_after_recovery, test_server_error_does_not_change_state, test_403_key_blocked_recovers_and_retries_once | keep | keep |
| D02 | expired 账号资格排除 + 24h 元数据/凭证刷新钩子（调度本体属 D08） | dad9cf7,1f9ba61; application/routing_eligibility.py:181-182; application/account_keepalive.py:54-66,265-299; config.py:343,349 | 改造 | partial | test_account_info_refresh.py::test_dispatch_excludes_expired, test_is_account_available_false_for_expired；24h 定时链路未验证 | port | keep |
| D02 | OAuth/token 过期与 refresh_token 通道 | upstream/master..master..rebuild 全仓 `.py` 无 oauth/refresh_token 实现；仅 cookie 会话过期（infrastructure/gateway/session_auth.py:495-572 登录页判定 → SessionExpiredError） | 待判定 | legacy | 全仓 grep `.py` 无 oauth/refresh_token；未验证（无对应代码/测试）；不可复跑（真实账号） | drop（当前不存在） | defer |
| D02 | 同邮箱重复账号标记（refresh_duplicate_flags：保留最早创建，其余 duplicate_of） | fcd6f1f; account_store.py:336-372,494 | 新增 | working | test_account_duplicates.py 4 项（标记/删除后清除/唯一邮箱/重载持久化） | keep | keep |
| D02 | 账号名称与自定义 tag 更新、tag 目录（自定义 + tier 派生） | upstream/master..master,cdb9e15; application/account_service.py:130-149; api/routes_accounts.py:192-197,610-621 | 改造 | working | test_routes_accounts.py::test_account_tags_catalog_endpoint, test_account_routing_get_put_delete | keep | drop |
| D02 | Paid 默认种子（`_DEFAULT_PAID_MODELS` + `_FALLBACK_MODELS.paid` + import 时 set_paid_models） | a10953d,1980ada; application/model_access.py:9-22; infrastructure/gateway/model_discovery.py:92-191 | 新增 | working | test_model_access.py::test_fallback_paid_models_allow_catalog_projection_without_discovery | keep | keep |
| D02 | ListModels paid 解析：item[77]→protobuf field 78，`paid = access_tier == 2`，保留 access_tier | a10953d; model_discovery.py:526-529,631-632 | 改造 | working | test_model_discovery_paid.py::test_parse_raw_models_marks_paid_tier; test_routes_models.py:195-214 | keep | keep |
| D02 | Discovery→paid 集合同步（升级/降级/缺失保守/快照恢复） | a10953d; model_access.py:33-56; model_discovery.py:213-233,285-289,349-363 | 新增 | working | test_model_access.py::test_discovery_updates_downgrade_upgrade_and_preserves_absent_models, test_snapshot_restores_status_and_applies_downgrade | keep | keep |
| D02 | paid map API（/api/stats 每模型 `paid` 字段，_paid_map 读 discovery） | 1adf3c2; api/routes_system.py:98-161 | 新增 | partial | test_stats_routes.py:20-25 仅 stub `_paid_map`；真实 paid 值/缓存失效未验证 | port | keep |
| D02 | Paid 模型仅 chat（is_chat_only_model / surface_capability / resolve_surface 守卫） | 1980ada,d8bbb22; application/surface_orchestrator.py:215-236,386-396,495-518 | 新增 | working | test_surface_orchestrator.py chat-only 6 项（含 build 组拒绝、强制 chat） | keep | keep |
| D02 | Paid 模型需 paid 账号 + free/pro 资格（eligible_account/account_is_paid/PAID_TIERS/channel_affinity/配额池） | 1980ada,8b6b52e,d8bbb22; surface_orchestrator.py:35-55,239-247; routing_eligibility.py:124-132,164-186 | 新增 | working | test_routing_eligibility.py::test_pro_only_model_requires_paid_account, test_tier_default_quota_pools_are_narrowable, test_expired_and_surface_pinned_accounts_are_ineligible | keep | keep |
| D02 | accessModes(Field 83) 3/4 档位过滤（is_model_allowed_for_tier） | upstream/master..master; model_discovery.py:402-417 | 原生 | working | test_routes_models.py:224-227（Free/Pro 拒、Ultra 放行） | keep | keep |
| D02 | 模型目录 paid 投影（paid 模型不进 build，chat-only 去重） | 6305377,1980ada; application/model_catalog.py:73-92 | 改造 | working | test_model_catalog.py::test_chat_only_model_is_not_duplicated_into_build | keep | keep |
| D02 | 账号身份校验与防错绑（verify_account_identity，不匹配拒绝回写） | 8e5e0cb; infrastructure/gateway/session_auth.py:235-363 | 改造 | working | test_session_lifecycle.py::test_verify_account_identity_does_not_rmtree（不匹配返回 False） | keep | keep |
| D02 | Cookie 注入页面（先清 Cookie/Cache，全量注入含 accounts/OSID） | 9786b3a; session_auth.py:121-149 | 改造 | working | test_cookie_persistence.py::test_load_cookies_keeps_account_and_osid_cookies | keep | keep |
| D02 | Cookie 回写合并（name+domain+path 键，保留未过期跨域 cookie，原子写） | 9786b3a; session_auth.py:366-437; account_store.py:45-65 | 改造 | working | test_cookie_persistence.py::test_save_cookies_merges_missing_account_cookies, test_save_cookies_preserves_cross_domain_twins, test_write_storage_state_preserves_non_cookie_fields | keep | keep |

### D03 Browser / CDP / Context / Page

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D03 | 控制面 `BrowserSession`：懒启动、`ensure_ready` 就绪门、profile dir 派生 | upstream/master(4623109); session.py:67-215,564-583 | 原生 | working | test_session_lifecycle.py:18,160；test_browser_session.py:175 | keep | keep |
| D03 | Chromium 直接子进程启动（CloakBrowser 独占、stealth/low-mem args、fingerprint key=user_data_dir） | upstream/master(4623109); browser_engine.py:61-227,1013-1071 | 原生 | working | test_browser_engine.py:34,59,141,184 | keep | keep |
| D03 | 遗留/孤儿 Chromium 清理、端口等待、Singleton/DevTools 锁文件删除 | upstream/master(4623109); browser_engine.py:471-680,1024 | 原生 | working | test_browser_engine.py:153,232 | keep | keep |
| D03 | 进程守护：pipe watchdog、进程组 SIGTERM/SIGKILL、Windows Job Object、atexit/signal hook | upstream/master(4623109); browser_engine.py:683-898,931-1010 | 原生 | working | test_browser_engine.py:169,203 | keep | keep |
| D03 | CDP WebSocket JSON-RPC：请求 future、事件分发、超时、断线失败全部 pending | upstream/master(4623109); cdp_client.py:93-308 | 原生 | working | test_cdp_client.py:38-93 | keep | keep |
| D03 | `connect_page`：复用首个 page target、关闭多余 about:blank tab、3 次重试 | 4aa35cb,0fe5821; cdp_client.py:871-918 | 原生 | working | test_cdp_client.py:235-260 | keep | keep |
| D03 | page 创建/关闭：`create_new_page`、`CDPPage.close`(Page.close+WS close)、`close_target` | f110d51a,8960de9; cdp_client.py:810-819,920-939,1038-1050 | 原生 | working | test_session_blue_green_readiness.py:82；无真实 target 关闭测试，未验证 | keep | keep |
| D03 | Inspector.detached 与 Inspector/Target.targetCrashed：标记 page closed 并立即失败 pending futures | 4aa35cb,4586271; cdp_client.py:164-198,375-396 | 原生 | working | test_cdp_client.py:200-213 | keep | keep |
| D03 | page liveness (`Runtime.evaluate 1`) + 浏览器进程 liveness checker（发送/等待命令中轮询） | 4c39542,4aa35cb; cdp_client.py:107-117,216-276,338-350 | 原生 | working | test_cdp_client.py:184-196,216-231；test_browser_session.py:175 | keep | keep |
| D03 | 资源拦截拆分：chat 面全量清单；build 面放行 monaco/codemirror/vs/mathjax/katex | 1cc634e; cdp_client.py:26-80,398-407 | 改造 | working | test_cdp_client.py:24-35 | keep | keep |
| D03 | BrowserContext 创建：`Target.createBrowserContext {disposeOnDetach:True}`，进程内串行锁 | 28c981f; cdp_client.py:941-968 | 新增 | partial | test_cdp_browser_context.py:28-44 断言参数；真实 detach 语义未验证 | keep | keep |
| D03 | `create_page_in_context`：Target.createTarget 后轮询 /json/list 取 WS，失败关 target | 28c981f; cdp_client.py:983-1036 | 新增 | unverified | 无直接单测；仅被 PooledSession 生产路径调用，未验证 | keep | keep |
| D03 | `dispose_browser_context`：忽略不存在/已释放 context | 28c981f; cdp_client.py:970-981 | 新增 | partial | test_cdp_browser_context.py:36-44 仅覆盖成功路径，未验证 | keep | keep |
| D03 | context/page 归属清理：slot 关闭先关页再 dispose context，关闭 browser 级连接 | 1201a37,b59d6f3; instance_pool.py:1214-1240; cdp_client.py:1044-1050 | 新增 | working | test_instance_pool.py:306-324,366-380,562-574 | keep | keep |
| D03 | `PooledSession`：在指定 context 内建页、注 cookie、导航、BotGuard、快照/传输 | dd5e359; pooled_session.py:51-99 | 新增 | unverified | 无 test_pooled_session*.py；pool 测试均用 FakeSession，未验证 | keep | keep |
| D03 | **当前事实：一 context 多 page**（同账号第二 channel 复用同一 context，chat+build 并存两页） | 79901b0,a59c885; instance_pool.py:533-606,642-687,1051-1094 | 新增 | partial | test_instance_pool.py:811-832,913-934；受 max_contexts/页预算约束，见冲突表 | port（需决定是否一 context 一 page） | drop |
| D03 | 蓝绿 page 切换：已有 context 时 `create_new_page`，首启 `connect_page`，新页就绪后原子换 active | 4586271; session.py:221-310 | 原生 | working | test_session_blue_green_readiness.py:82-155；test_session_lifecycle.py:18 | keep | drop |
| D03 | retiring drain：旧 context in_flight 归零或 12s 超时后关页 + close_target，不打断在途流 | 4586271; session.py:317-338; session_state.py:23-45 | 原生 | working | test_session_blue_green_readiness.py:58-78,82-155 | keep | drop |
| D03 | `SessionReadinessGate`：UNINITIALIZED/PREPARING/READY/FAILED，waiters fail-fast | 4586271; session_state.py:48-125 | 原生 | working | test_session_blue_green_readiness.py:20-54 | keep | drop |
| D03 | BotGuard bootstrap：`say '1'` 探针、hook 安装、in-page/网络模板截获、临时 chat、恢复输入框 | 4586271,1cc634e; botguard_bootstrap.py:22-323 | 原生 | working | test_browser_session.py:516-565；真实上游未跑，未验证 | keep | keep |
| D03 | BotGuard 捕获期间跳登录页：重新注入 cookie 后继续（upstream 4623109 曾移除，HEAD 恢复） | 4623109..HEAD; botguard_bootstrap.py:252-262 | 改造 | unverified | 无对应分支测试；仅代码路径，未验证 | keep | keep |
| D03 | cookie 注入：清空 browser cookies/cache 后注入 auth_file 全量 cookie（含 OSID/__Secure-OSID/accounts 域） | 9786b3a; session_auth.py:121-149 | 改造 | working | test_cookie_persistence.py:62-84 | keep | keep |
| D03 | cookie 回写：按 name+domain+path 合并、保留未过期旧 cookie、写 storage_state（不再整体覆盖） | 9786b3a; session_auth.py:366-436 | 改造 | working | test_cookie_persistence.py:86-147 | keep | keep |
| D03 | CDP `Network.setCookies` 归一化：`__Host-` 用 url、sameSite 映射、批量失败逐条重试、缓存快照 | f110d51a,ae465d68,8df49ad; cdp_client.py:660-743 | 原生 | working | test_cdp_client.py:145-181 | keep | keep |
| D03 | 控制面 cookie 导入：临时 `create_new_page` + set/get cookies + bootstrap + close_target | upstream/master(4623109); session.py:499-527 | 原生 | unverified | 无直接单测；test_cookie_import.py:251-328 未覆盖该方法，未验证 | keep | keep |
| D03 | BrowserSession 关闭前回写当前 cookie；供 keepalive/巡检读 in_flight、touch_activity | 1f9ba61; session.py:101-108,542-550,585-589 | 改造 | unverified | 无直接单测（test_account_keepalive.py:63 仅 Fake），未验证 | keep | keep |
| D03 | build 面取控制面 CDPClient：`ensure_browser_cdp_client` 复用 BrowserSession 的进程/连接 | 1cc634e; session.py:183-187; client.py:94-95 | 改造 | unverified | 无直接单测（test_build_surface 用 Fake），未验证 | keep | port |
| D03 | build-only：加载后用 bootstrap chat 页完成 BotGuard，随即关 chat 页/清 session，仅留 context+build 页 | a59c885（b59d6f3 起）; instance_pool.py:1028-1038 | 新增 | working | test_instance_pool.py:890-909 | keep | port |
| D03 | `close_channel`：只关指定 channel 页，保留 context 与另一页，可再次 `ensure_channel` 重开 | a59c885; instance_pool.py:689-719 | 新增 | working | test_instance_pool.py:835-857,912-934 | keep | drop |
| D03 | slot 重载/关闭/取消：先关页再 dispose 旧 context，generation+realm_id 自增并回调旧 realm | 1201a37,b59d6f3; instance_pool.py:963-1027,1214-1240 | 新增 | working | test_instance_pool.py:306-324,366-380,562-574 | keep | port |
| D03 | realm 服务缓存：capture/streaming/replay 按 realm_id 复用，slot retire 时 pop 清缓存 | 1201a37,6305377; client.py:75-100,128-156 | 新增 | partial | test_instance_pool.py:562-574 仅证回调触发；缓存确实删除未验证 | keep | port |
| D03 | build 面 realm WS 分桶：`?realm=` 解析，default/各 realm 连接列表隔离 | 1cc634e; build_surface.py:136-205 | 新增 | working | test_cdp_browser_context.py:47-71 | keep | port |
| D03 | 单实例单页：全局页预算=max(1,max_contexts)，chat/build 页预算由 max_instances 派生；第二页拒绝 | 7cd6d74,9786b3a; instance_pool.py:202-255,314-363 | 改造 | working | test_instance_pool.py:938-1031 | keep | port |
| D03 | 浏览器回收后 stale/pinned slot 复活：同账号复用原 slot 重载 context/page | 86391c8; instance_pool.py:919-961; test_instance_pool.py:1034-1058 | 新增 | working | test_instance_pool.py:1034-1058 | keep | port |
| D03 | 浏览器空闲回收：`browser_idle_timeout<=0` 关闭；LOADING/PREPARING 不回收；冷启动刷新时间戳 | 4aa35cb,4623109,86391c8; session.py:197-215,564-583 | 原生 | working | test_session_lifecycle.py:127-189；默认 0（86391c8 config），未验证运行时 | keep | port |

### D04 Instance / Pool / Lease / Capacity

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D04 | manager 记录（账号+channel、稳定编号）与 pool slot（Context 单位）分层 | 79901b0; instance_manager.py:78-113,738-750; instance_pool.py:49-78 | 新增 | working | test_instance_manager.py:266-283 | keep | port |
| D04 | manager 记录 runtime_ref 绑定 slot_id，孤儿 pool slot 收养为实例 | 79901b0; instance_manager.py:936-968 | 新增 | partial | test_instance_manager.py:439-467；同账号额外 slot 不收养，跨账号收养未验证 | keep | port |
| D04 | 账号+channel 唯一性，重复创建返回 already_exists | a59c885; instance_manager.py:270-273,774-783 | 新增 | working | test_instance_manager.py:352-361 | keep | drop |
| D04 | max_contexts 统一为实例上限（manager）与 Context 上限（pool） | 9786b3a; instance_manager.py:809-810; instance_pool.py:202-211 | 改造 | working | 9786b3a diff max(2)→max(1)；test_instance_manager.py:667-689 | keep | port |
| D04 | 一账号一 Context：同账号 slot 复用，不新建 Context | 1201a37; instance_pool.py:533-606,1051-1094 | 新增 | working | test_instance_pool.py:124-143,799-832 | keep | port |
| D04 | create_instance 同账号第二 channel 复用 Context，仅加 channel 页 | a59c885; instance_pool.py:546-588,642-687 | 新增 | working | test_instance_pool.py:811-832,913-934 | keep | drop |
| D04 | chat+build 两页共存于同一 Context（偏差：一 Context 两 page） | 1201a37,a59c885; instance_pool.py:642-687,1051-1094 | 新增 | working | test_instance_pool.py:204-226,811-832；与“一 context 一 page”目标冲突 | port（需拆分 Context） | drop |
| D04 | build-only：加载后关闭 bootstrap chat 页，保留 Context/cookie | b59d6f3; instance_pool.py:1028-1038 | 新增 | working | test_instance_pool.py:890-909 | keep | port |
| D04 | create_instance 无同账号 slot 时新建 slot 并 pinned | 79901b0,a59c885; instance_pool.py:533-606 | 新增 | working | test_instance_pool.py:746-759,799-807 | keep | drop |
| D04 | startup 每启用 channel 先占一槽 | da6ea25,7cd6d74; instance_manager.py:834-864 | 新增 | working | test_instance_manager.py:300-313 | keep | port |
| D04 | startup 按剩余预算补槽到 max_contexts，受 channel cap 限制 | 7cd6d74; instance_manager.py:844-864 | 改造 | working | test_instance_manager.py:623-638 | keep | port |
| D04 | startup 活跃账号优先排序 | da6ea25; instance_manager.py:127-137 | 新增 | working | test_instance_manager.py:300-313 | keep | keep |
| D04 | startup 走 eligibility/group/affinity 选号 | 7cd6d74; instance_manager.py:875-903 | 新增 | working | test_instance_manager.py:606-654 | keep | drop |
| D04 | startup 失败账号隔离，失败后少启动不整体失败 | 7cd6d74; instance_manager.py:148-225 | 新增 | working | test_instance_manager.py:316-349 | keep | keep |
| D04 | startup 同账号多 channel 共享一个 Context，产生两条实例记录（偏差） | da6ea25,a59c885; instance_manager.py:165-217 | 改造 | partial | test_instance_manager.py:316-333；记录数≠Context 数 | port（记录应绑页） | drop |
| D04 | create 前预检全局/渠道/页面预算，不产生半实例 | da6ea25; instance_manager.py:278-287,696-705; instance_pool.py:608-640 | 新增 | working | test_instance_manager.py:372-381；test_instance_pool.py:980-989 | keep | port |
| D04 | create 异步 STARTING，失败移除记录并回滚 current/latest | 79901b0; instance_manager.py:285-288,528-534,707-717 | 新增 | working | test_instance_manager.py:439-453 | keep | keep |
| D04 | close STARTING 无 runtime 时直接取消任务并删记录 | da6ea25; instance_manager.py:295-302 | 新增 | unverified | 仅代码路径，无对应测试；未验证 | keep | keep |
| D04 | close ACTIVE 转 RECLAIMING 并由后台 reclaim 关实例 | da6ea25; instance_manager.py:307-311,621-639 | 新增 | working | test_instance_manager.py:384-399 | keep | port |
| D04 | acquire 预留：reservations 计数、BUSY 状态、condition 通知 | 1201a37; instance_pool.py:411-531,1051-1094 | 新增 | working | test_instance_pool.py:124-143,585-587 | keep | drop |
| D04 | 同账号同 surface 优先复用现成 slot 与页 | 1201a37; instance_pool.py:1051-1094 | 新增 | working | test_instance_pool.py:124-143,204-226 | keep | drop |
| D04 | acquire 新建 slot，满额时 rebind 最久未用 idle slot | dd5e359; instance_pool.py:854-917 | 新增 | working | test_instance_pool.py:147-164,635-656 | keep | drop |
| D04 | 全局容量：slot 数受 max_contexts 限制，超时抛 InstanceCapacityError | 1201a37; instance_pool.py:490-502,896-917 | 新增 | working | test_instance_pool.py:184-200 | keep | port |
| D04 | 全局 page 预算=max_contexts（不再独立 global_max_pages，偏差：页数=实例数） | dd5e359,9786b3a; instance_pool.py:333-363 | 改造 | working | test_instance_pool.py:660-673,938-959 | port（页与实例解耦） | drop |
| D04 | channel page 预算由 max_instances 派生，chat/build 一页一实例 | 7cd6d74; instance_pool.py:314-331 | 改造 | working | test_instance_pool.py:591-632 | keep | drop |
| D04 | group page 预算按 group.max_pages 拒绝超额 | dd5e359,b59d6f3; instance_pool.py:269-278,355-362 | 新增 | working | test_instance_pool.py:676-701 | keep | drop |
| D04 | channel_max_instances 渠道实例硬上限（0 表示禁用） | 7cd6d74; instance_pool.py:288-301; instance_manager.py:812-818 | 改造 | working | test_instance_manager.py:623-665 | keep | drop |
| D04 | channel_enabled：build 回退 features.build_surface | 7cd6d74; instance_pool.py:303-312 | 改造 | working | test_features_routing_config.py:24-53 | keep | drop |
| D04 | pending_pages/pending_channel 占位计数，避免 build 并发双计页 | a59c885; instance_pool.py:232-255 | 改造 | working | test_instance_pool.py:229-261,529-538 | keep | drop |
| D04 | borrow：容量失败后借用其他账号 idle slot 的现成页 | 1201a37; instance_pool.py:1096-1131 | 新增 | working | test_instance_pool.py:541-559；test_routing_pipeline.py:429-446 | keep | drop |
| D04 | stale slot 复活：浏览器回收后同账号复用原 slot 重载 | 86391c8; instance_pool.py:919-961 | 新增 | working | test_instance_pool.py:1034-1058 | keep | port |
| D04 | pinned slot 不参与 rebind、borrow、idle retire | 79901b0; instance_pool.py:76,854-868,1188 | 新增 | working | test_instance_pool.py:746-759 | keep | drop |
| D04 | SlotState 状态机含 COOLDOWN，但无任何赋值点 | 1201a37; instance_pool.py:36-42 | 新增 | legacy | 引用见 instance_pool.py:239,791,867,1189；无 COOLDOWN 赋值，未验证 | drop（或补状态转换） | drop |
| D04 | _load_slot 冷启动重建 Context，最多 3 次尝试 | b59d6f3; instance_pool.py:963-1017 | 新增 | unverified | 无单测覆盖 3 次重试；未验证 | keep | keep |
| D04 | release 只减 reservation、touch last_used，不关页不 dispose | 1201a37; instance_pool.py:832-850 | 新增 | working | test_instance_pool.py:516-526 | keep | drop |
| D04 | close_channel 只关指定 channel 页，保留 Context 与另一页 | a59c885; instance_pool.py:689-719 | 新增 | working | test_instance_pool.py:835-857 | keep | drop |
| D04 | close_instance 置 RETIRING、移出池并 dispose Context | 79901b0; instance_pool.py:739-750,1214-1231 | 新增 | working | test_instance_pool.py:762-775 | keep | port |
| D04 | reassign_instance 保留 slot 身份与 pin，换账号后重载 | 79901b0; instance_pool.py:752-771 | 新增 | working | test_instance_pool.py:778-795 | keep | drop |
| D04 | rebind 空闲 slot 换账号/组，generation 自增并重建 Context | dd5e359; instance_pool.py:773-830 | 新增 | working | test_instance_pool.py:704-742 | keep | drop |
| D04 | retire_idle 空闲回收 API 存在但无 src 调用方 | 1201a37; instance_pool.py:1180-1205 | 新增 | legacy | test_instance_pool.py:562-574；src 无调用，未验证 | drop（或接线） | drop |
| D04 | slots/pages/budget snapshot 供路由与诊断 | dd5e359,86391c8; instance_pool.py:364-407 | 新增 | working | test_instance_pool.py:577-587；routing_pipeline.py:581-582 | keep | port |
| D04 | has_active_work 阻止浏览器空闲回收处于 LOADING/BUSY 的实例 | 86391c8; instance_pool.py:364-371 | 新增 | working | app.py:185-187 | keep | port |
| D04 | _runtime_occupancy 统计辅助无调用方 | 79901b0; instance_manager.py:912-919 | 新增 | legacy | 全仓无引用，未验证 | drop | drop |
| D04 | generation/realm_id 随重载自增，旧 realm 触发 retire 回调 | 1201a37; instance_pool.py:80-84,982-984,986-1023 | 新增 | working | test_instance_pool.py:147-164,562-574 | keep | keep |
| D04 | switch_to 当前实例原地切换，不动其他实例 | da6ea25; instance_manager.py:334-340 | 新增 | working | test_instance_manager.py:469-496 | keep | port |
| D04 | switch_to 复用同账号 sibling channel 实例 | a59c885; instance_manager.py:342-352 | 新增 | working | test_instance_manager.py:525-539 | keep | drop |
| D04 | switch_to 容量满时关闭原 channel 并在同 Context 开新 channel | 9786b3a; instance_manager.py:354-409 | 改造 | working | test_instance_manager.py:666-690 | keep | port |
| D04 | switch_to 换 channel 失败回滚恢复原 channel | 9786b3a; instance_manager.py:411-429 | 改造 | working | test_instance_manager.py:692-715 | keep | keep |
| D04 | switch_to 无效实例重启，容量不足时关闭 victim | da6ea25; instance_manager.py:604-619 | 新增 | working | test_instance_manager.py:557-604 | keep | port |
| D04 | switch_to 目标账号已是 active 时跳过蓝绿账号切换 | 86391c8; instance_manager.py:455-464 | 改造 | working | test_instance_manager.py:743-774 | keep | keep |
| D04 | reassign 拒绝迁移多 channel 实例之一，避免拆散共享 Context | a59c885; instance_manager.py:474-477 | 新增 | unverified | test_instance_manager.py:402-421 未覆盖 sibling 分支；未验证 | keep | drop |
| D04 | in-flight 防换号：rebind/close_instance/close_channel/manager close/reassign 拒绝 | 1201a37,79901b0,a59c885; instance_pool.py:698-699,745-746,789-790; instance_manager.py:303-306,480-483 | 新增 | working | test_instance_pool.py:728-742,762-775；test_instance_manager.py:384-399 | keep | port |
| D04 | build 在途禁止换账号/重载，抛 BUILD_BUSY | 8e5e0cb; build_surface.py:1002-1007 | 改造 | working | test_build_surface.py:528-537 | keep | port |
| D04 | build 请求身份取 lease.auth_file/auth_user，不回退全局 session | 8e5e0cb; client.py:158-178,588,698 | 改造 | working | test_build_lease_identity.py:10-39 | keep | port |
| D04 | acquire 首试 30s、切号后 15s，超时映射容量错误 | 8e5e0cb; routing_pipeline.py:430-442 | 改造 | working | test_routing_pipeline.py:429-446 | keep | drop |
| D04 | 容量错误码：chat=surface_quota_exceeded，build=surface_instance_busy，统一 503 | 8e5e0cb; routing_pipeline.py:443-487 | 改造 | working | test_generation_routing.py:196-235 | keep | port |
| D04 | 单实例 failover 限制：无空闲 slot 时不跨账号抢占，只能等待/defer/rebind | 1201a37,8e5e0cb; instance_pool.py:333-335,490-502; routing_pipeline.py:443-487 | 新增 | partial | test_instance_pool.py:938-1009；test_generation_routing.py:157-235；跨号切换需先释放 | port（一 context 一页时需重设计） | port |
| D04 | 容量满时 defer 等待后重试同候选 | 86391c8; generation_service.py:201-250; routing_safety.py:25-71 | 新增 | working | test_generation_routing.py:157-235 | keep | drop |
| D04 | InstanceSwitcher 只规划 idle slot，最受限 slot 优先，跳过 in_flight | dd5e359; instance_switch.py:71-137 | 新增 | working | test_instance_switch.py:52-96 | keep | drop |
| D04 | InstanceSwitcher 以 pool.rebind 应用迁移并带组页预算 | dd5e359; instance_switch.py:173-216 | 新增 | working | test_instance_switch.py:99-171 | keep | drop |
| D04 | 实例记录数参与容量判定，与 Context 数/页数不等价（偏差） | da6ea25; instance_manager.py:905-919; instance_pool.py:215-221,561-565 | 改造 | partial | test_instance_manager.py:316-333；2 记录共享 1 context | port（容量按页计） | port |
| D04 | 单实例默认配置 max_contexts=1，亦为启动预启动数量 | 79901b0,9786b3a; defaults/config.yaml:110-119 | 改造 | working | test_features_routing_config.py:24-53；test_instance_manager.py:667-689 | keep | port |

### D05 Routing / Candidate / Switch / Retry

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D05 | channel 别名与前缀解析（chat/pg/playground/build/official，`models/` 先剥离） | 8b6b52e,7cd6d74; channel_registry.py:20-103 | 新增 | working | test_routing_eligibility.py:218-228；`pytest` 96 passed | keep | drop |
| D05 | 显式 channel 前缀钉死，永不静默换渠道（未启用/未实现/未知前缀均显式拒绝） | 8b6b52e; channel_registry.py:344-368,269-307 | 新增 | working | test_routing_eligibility.py:218-228；test_surface_orchestrator.py:182-195 | keep | drop |
| D05 | 裸模型选 channel：单渠道直取；chat-only→chat；已挂载渠道优先；否则 build 优先 | 7cd6d74; channel_registry.py:370-405; routing_pipeline.py:216-225,260-266 | 新增 | working | test_routing_eligibility.py:193-215；test_routing_pipeline.py:183-198 | keep | drop |
| D05 | 渠道 enabled/implemented 门槛（official 强制 disabled/unimplemented，WP9 前不可用） | 8b6b52e; channel_registry.py:36-46,180-192 | 新增 | working | test_surface_orchestrator.py:195-206；official 无测试，未验证 | keep | drop |
| D05 | 渠道 `allowed_tags` 为准入闸门（旧 `quota_pool` 折算为单一 tag） | 7cd6d74; channel_registry.py:198-217; routing_eligibility.py:197-198,45-49 | 改造 | working | test_routing_eligibility.py:126-165；语义与 tier tag 耦合（见冲突表） | port（与 quota pool 解耦） | drop |
| D05 | tier→quota pool 映射（free→free，pro/ultra/plus/paid→pro+free，未知→free+pro） | 8b6b52e; surface_orchestrator.py:47-55,243-247 | 新增 | working | test_routing_eligibility.py:73-81 | keep | port |
| D05 | 账号级 quota_pools 只收窄不放大 | 8b6b52e; routing_eligibility.py:111-121; account_routing.py:41-54,146-158 | 新增 | partial | test_routing_eligibility.py:73-81；`apply_account_quota_pools` 无调用点 | port（接入筛选） | drop |
| D05 | 组 `allowed_quota_pools` 收窄 | 8b6b52e; surface_orchestrator.py:267-276,331; routing_eligibility.py:200-207 | 新增 | broken | 仅存入 GroupPolicy，资格图从未读取；test_routing_eligibility.py:168-179 靠 channel tag 偶然通过 | port（补实现） | drop |
| D05 | Paid 模型门槛：必须 paid 账号且只能走 chat | 1980ada; routing_eligibility.py:183-186; surface_orchestrator.py:215-231,239-240 | 新增 | working | test_routing_eligibility.py:84-96；test_model_access.py | keep | keep |
| D05 | chat-only / model guard：非 chat 渠道显式拒绝 | 8b6b52e,1980ada; channel_registry.py:364-365,382-388; surface_orchestrator.py:495-511 | 新增 | working | test_routing_eligibility.py:226-228；test_surface_policy_routing.py:146-165 | keep | drop |
| D05 | 分组匹配：tier/email regex、列表首个命中、组内 OR、禁用组跳过 | d8bbb22; surface_orchestrator.py:189-212,287-333 | 新增 | working | test_surface_orchestrator.py:64-109,230-242；test_surface_policy_v2.py:57-90 | keep | drop |
| D05 | 组 allowed_surfaces 只收窄；无显式组回退 tier 默认（free→build-only，未知→chat） | 7cd6d74; surface_orchestrator.py:35-45,304-351 | 改造 | working | test_surface_orchestrator.py:182-229,257-271；test_surface_policy_v2.py:77-101 | keep | drop |
| D05 | 账号级 routing 覆盖：surfaces / model_surfaces 钉面 / weight(0.1-100) | 8b6b52e; account_routing.py:88-143; routing_eligibility.py:193-195 | 新增 | working | test_account_routing.py；test_routing_eligibility.py:182-190 | keep | drop |
| D05 | 资格图顺序：expired→paid/chat→组启用与面→账号收窄→tag→pools | 8b6b52e,1980ada; routing_eligibility.py:164-207 | 新增 | working | test_routing_eligibility.py:84-190 | keep | port |
| D05 | 原生渠道亲和度（paid=chat 优先，free=build 优先，其他=2） | 8b6b52e; routing_eligibility.py:124-132,205 | 新增 | working | test_routing_pipeline.py:165-198（间接） | keep | drop |
| D05 | 资格排序 `rank_eligible`：affinity→组权重→账号权重→account_id | 7cd6d74; routing_selection.py:17-27 | 新增 | working | 无 test_routing_selection.py；仅经 pipeline 排序间接覆盖，未验证 | keep | port |
| D05 | 候选二次排序：available→affinity→weight→公平分→slot→ready→id | cd569bc,ec35c8d; routing_pipeline.py:303-355 | 新增 | working | test_routing_pipeline.py:272-391 | keep | port |
| D05 | 公平分：last_used 为主、requests/weight 为辅，避免老账号被永久饿死 | ec35c8d; routing_pipeline.py:398-412 | 新增 | working | test_routing_pipeline.py:272-347 | keep | port |
| D05 | warm slot / ready 账号仅作同分 tie-breaker，不绑架后续请求 | cd569bc; routing_pipeline.py:305-311,349-353 | 新增 | working | test_routing_pipeline.py:302-390 | keep | port |
| D05 | rotator 可用性过滤（异常时 fail-open） | ec35c8d; routing_pipeline.py:304-324 | 新增 | partial | rotator 异常→available=True，无测试；未验证 | port（fail-open 需复核） | drop |
| D05 | acquire：派发前装载候选账号，首次 30s / 后续 15s 预算，返回 lease | cd569bc; routing_pipeline.py:419-442,488-497 | 新增 | working | test_routing_pipeline.py:393-409；`pytest` 96 passed | keep | port |
| D05 | borrow 复用：主 acquire 容量失败后，仅对未试账号二次 acquire | cd569bc; routing_pipeline.py:443-487; features `routing.borrow` | 新增 | working | test_routing_pipeline.py:429-446 | keep | port |
| D05 | 容量拒绝映射：build→`surface_instance_busy`，chat→`surface_quota_exceeded`；无 borrow 直接拒绝 | 8e5e0cb; routing_pipeline.py:443-456; surface_orchestrator.py:61-69 | 改造 | working | test_routing_pipeline.py:412-426；test_generation_routing.py:196-235；真机未复跑 | keep | port |
| D05 | `features.routing.max_switches` 单请求切号预算（默认 2，非法值回落 2） | ec35c8d; routing_pipeline.py:357-362,371; routing_safety.py:24-37 | 新增 | working | test_routing_safety.py:39-49,103-125；test_generation_routing.py:275-289 | keep | port |
| D05 | defer 预算：`max_defers` 固定 2、不可配置；容量/build busy 时 sleep 1s 重试 | b59d6f3,86391c8; routing_safety.py:27,39-56; generation_service.py:193-251 | 新增 | partial | test_generation_routing.py:157-235；sleep 与上限硬编码 | port（改为可配） | drop |
| D05 | 429 限额：未出数据→切号；预算/候选耗尽→HTTP 429 显式失败 | ec35c8d,6a6afbe; upstream_errors.py:247-288 | 改造 | working | test_generation_routing.py:257-273；test_genai_sdk_compat.py:253 | keep | keep |
| D05 | 401 会话失效：清模板、记录 auth_error、未出数据→切号，否则 401 | ec35c8d; upstream_errors.py:186-219 | 改造 | working | test_api_responses.py:79-83；切号分支未单测，未验证 | keep | keep |
| D05 | 403 AuthError：清模板、记录 auth_error、未出数据→切号，否则 403 | ec35c8d; upstream_errors.py:221-245 | 改造 | working | test_api_responses.py:84-88；切号分支未单测，未验证 | keep | keep |
| D05 | 204 空响应：首次且未出数据→清模板并重试一次（语义应为同账号 retry） | 6a6afbe; upstream_errors.py:290-299 | 改造 | broken | test_api_responses.py:246-248 仅覆盖流式首块；真实 retry_same 未置真（见下） | port（先修 retry_same） | port |
| D05 | 瞬态 RPC 404（Ambiguous RPC）：未出数据且未到上限→原地重试，严禁切号 | 6a6afbe; upstream_errors.py:302-315 | 改造 | broken | test_api_responses.py:131-156；routing 路径实际会换号 | port（先修 retry_same） | port |
| D05 | 非瞬态 404 / 其他 4xx：不重试，保留上游状态码与清洗后消息 | 6a6afbe; upstream_errors.py:347-360 | 改造 | working | test_api_responses.py:158-169；test_format_error_routing.py | keep | keep |
| D05 | browser/BotGuard hang：预算内切号；否则关 session 原地重试（attempt<2） | ec35c8d; upstream_errors.py:317-346 | 改造 | partial | 无 hang 单测；关 session 分支未验证 | port | port |
| D05 | 同账号 retry（`retry_same`）：`record_retry` 只计数，从不置 True → 所有 retry 变成换号 | ec35c8d; routing_pipeline.py:514-522 vs generation_service.py:305,315,445,580,597,716 | 新增 | broken | test_generation_routing.py:64-67 的 FakePipe 自行置 True，掩盖缺陷 | port（必须修） | port |
| D05 | 流式已出数据不可切号：`has_yielded_data` 时 429/403/204/404/hang 一律不再切号 | 6a6afbe,ec35c8d; upstream_errors.py:195,230,261,290-295,326,330; generation_service.py:578,684-685 | 改造 | working | test_api_responses.py:171-180；test_surface_policy_routing.py:186 | keep | keep |
| D05 | 熔断：滚动窗口 5 次失败/600s、冷却 300s、半开保留失败 | ec35c8d; routing_safety.py:76-164; routing_pipeline.py:524-530,548-555 | 新增 | broken | test_routing_safety.py:127-270 仅测原语；`record_switch_failure` 全仓无调用点，熔断永不触发 | port（接线或删除） | drop |
| D05 | 成功闭环：清熔断失败、`retry_same=False`、释放 lease、记录 rotator success | ec35c8d,cd569bc; routing_pipeline.py:557-565; generation_service.py:390-400,773-785 | 新增 | working | test_generation_routing.py:129-155 | keep | keep |
| D05 | lease 释放：异常/取消/流关闭 finally 均释放，不泄漏在途实例 | cd569bc,b59d6f3; generation_service.py:424-457,694-726,810-852 | 新增 | working | test_generation_routing.py:292-355 | keep | keep |
| D05 | 切换耗尽：候选/预算/熔断→`surface_switch_exhausted`（HTTP 503），不误报 403 | 0cb1b82,ec35c8d; routing_pipeline.py:524-546; generation_service.py:180-190; surface_orchestrator.py:61-69 | 改造 | working | test_generation_routing.py:275-289；test_app_error_handlers.py；test_surface_policy_v2.py:154 | keep | port |
| D05 | 单实例 failover 限制：pinned slot 不可 rebind，max_contexts=1 时 build 只能等（defer），不能跨账号抢占 | 79901b0,8e5e0cb; instance_pool.py:854-917,490-502; routing_pipeline.py:444-456 | 改造 | partial | test_instance_pool.py 覆盖 rebind；跨账号 build failover 未验证 | port（需与"来消息即唤醒"对齐） | port |
| D05 | 遗留回退路径：pipeline 不可用时 `ensure_active_account` + `try_switch_account`（MAX_RETRIES=5） | 6a6afbe,ec35c8d; generation_service.py:160-177,316-317,598-601; account_orchestrator.py:13,68,202 | 原生 | working | test_account_orchestrator.py；非流式回退 surface 硬编码 chat（见冲突表） | drop（回退仅排障） | drop |
| D05 | 换号规划 InstanceSwitcher.rebalance：按最受限 slot 先分配、只动 idle、逐槽 rebind | dd5e359,b59d6f3; instance_switch.py:71-137,173-199 | 新增 | working | test_instance_switch.py；与 D08 自动切号共用，真机未复跑 | keep | drop |
| D05 | 上游错误分类：异常→(HTTP 状态, 清洗消息, Gemini status)，JSPB 嵌套消息解包 | 6a6afbe; upstream_errors.py:42-138 | 改造 | working | test_api_responses.py:77-127；`pytest` 9 passed | keep | keep |
| D05 | 强制 429 测试钩子（`AISTUDIO_TEST_HOOKS=1` + account/model/surface/count） | ec35c8d; testing_hooks.py:35-60; generation_service.py:335-340,612-617 | 新增 | working | test_testing_hooks.py:1-25 | keep | keep |

### D06 Generation / Protocol / Wire

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D06 | 非流式生成主链（normalize→generate_content→parse→GenerationOutcome） | `6a6afbe` `src/aistudio_api/application/generation_service.py:254-467,470-503`；`src/aistudio_api/application/chat_service.py` | 新增 | working | `tests/unit/test_generation_routing.py:129-155`；`tests/unit/test_atg_integration.py:100-246` | port | port |
| D06 | 流式生成主链（协议中立的 StreamEvent 事件流） | `6a6afbe` `generation_service.py:506-808,810-852` | 新增 | working | `test_generation_routing.py:336-355`；`test_atg_integration.py:252-271` | port | port |
| D06 | 协议无关管线抽取（ATG／重试／换号／空响应归 service，渲染归 API） | `6a6afbe` `generation_service.py:1-7`；`api_service_gemini.py:1-6` | 新增 | working | 同上；`test_generation_routing.py:358-370` 覆盖渲染分离 | port | port |
| D06 | Gemini 非流式入口 `:generateContent` | `4623109` `src/aistudio_api/api/routes_gemini.py:21-31`；`6a6afbe` `api_service_gemini.py:186-225` | 改造 | working | `test_atg_integration.py:100-246`；`test_api_responses.py:58-127` 覆盖 parts/usage | keep | port |
| D06 | Gemini 流式入口 `:streamGenerateContent` | `4623109` `routes_gemini.py:34-41`；`6a6afbe` `api_service_gemini.py:228-263` | 改造 | working | `test_atg_integration.py:252-287`；`test_surface_policy_routing.py:186-215` | keep | port |
| D06 | Gemini SSE 分块渲染（body/thinking/tool_calls/images/reasoning_images） | `4623109` `api_service_gemini.py:54-111`；`6a6afbe` `:166-183` | 改造 | working | `test_atg_integration.py:252-271`；`test_surface_policy_routing.py:204-215` | keep | port |
| D06 | Gemini SSE 终块（usageMetadata＋finishReason＋responseId） | `4623109` `api_service_gemini.py:114-144`；`6a6afbe` `:169-176` | 改造 | partial | 无终块字段独立断言；仅 `test_atg_integration.py:271` 断言 STOP | port（补终块用例） | port |
| D06 | Gemini SSE 错误块（HTTP 200 已出后只能塞 body） | `4623109` `api_service_gemini.py:147-163,245-257`；`6a6afbe` `api/format_errors.py:126-154` | 改造 | partial | `test_surface_policy_routing.py:186-215`：策略 403 仍为 200＋error body | port（见冲突 1） | port |
| D06 | 路由等待注释块 `: resolving-instance`（不污染 SSE data） | `6a6afbe` `generation_service.py:589-593`；`api_service_gemini.py:166-168` | 新增 | working | `test_generation_routing.py:358-370`（三面均断言） | port | port |
| D06 | 流式首事件预读以保留真实 HTTP 状态（Claude／OpenAI／Responses） | `1375646` `routes_claude.py:121-144`；`c0412c8` `routes_openai.py:102-122`；`2b39b53` `routes_responses.py:138-152` | 新增 | partial | `test_format_claude.py:793-817`；`test_format_openai.py:701-726`；`test_format_responses.py:269-287` | port（统一三面） | port |
| D06 | Claude Messages SSE 渲染与消息块序列 | `1375646` `src/aistudio_api/application/format_converters/claude_response.py:138-352`；`routes_claude.py:146-168` | 新增 | working | `test_format_claude.py:621-738,820-849` | port | port |
| D06 | OpenAI Chat Completions SSE 渲染与 tool_calls 增量 | `c0412c8` `format_converters/openai_response.py:104-220`；`routes_openai.py:124-145` | 新增 | working | `test_format_openai.py:546-602` | port | port |
| D06 | OpenAI Responses SSE 渲染与 response.failed 终态 | `2b39b53` `format_converters/responses_response.py:177-415`；`routes_responses.py:154-176` | 新增 | working | `test_format_responses.py:201-244` | port | port |
| D06 | 流式错误边界总则（200 已出后错误只能进 body；已出正文禁切号） | `6a6afbe` `generation_service.py:694-726,836-849`；`upstream_errors.py:195-343`；`api_service_gemini.py:256-257`；`format_errors.py:211-220` | 新增 | partial | `test_api_responses.py:224-271`（仅元数据事件仍可切号）；无“200 后上游 429”用例 | port | port |
| D06 | finish_reason 映射（wire int→Gemini 字符串；TOOL_CALLS→STOP） | `6a6afbe` `upstream_errors.py:74-78`；`generation_service.py:646-659`；`atg/extractor.py:36-40,131-132` | 新增 | working | `test_atg_extractor.py:129-149`；`test_atg_integration.py:252-271` | port | port |
| D06 | usage 解析（visible＋reasoning 合并、cached、total 回退） | `4623109` `src/aistudio_api/infrastructure/gateway/wire_parser.py:342-403` | 原生 | working | `test_wire_parser.py::test_parse_usage_metadata`；`test_api_responses.py::test_to_gemini_usage_metadata_uses_visible_and_reasoning_tokens` | keep | keep |
| D06 | 流式 usage 聚合与终块事件 | `6a6afbe` `generation_service.py:572-576,646-648,801-807`；`4623109` `streaming.py:86-108,154` | 新增 | partial | `test_wire_parser.py:75-78` 仅 chunk 级；无 service 级聚合断言 | port（补聚合用例） | port |
| D06 | response_id 解析与透出（chunk[7]→事件→各协议 id） | `4623109` `wire_parser.py:23,558-563`；`streaming.py:109-116`；`6a6afbe` `generation_service.py:660-661,801-807` | 新增 | partial | `test_format_openai.py:459` 提供 response_id 但未断言输出 id | port（补断言） | port |
| D06 | thought_signature 流式缓冲并附着到下一事件 | `6a6afbe` `generation_service.py:662-693`；`api_service_gemini.py:69-70,83-84` | 新增 | working | `test_api_responses.py:224-271`；`test_format_claude.py:862-915`（签名缓存） | port | port |
| D06 | 非流式 thought_signature 仅 functionCall／图片回传，纯文本签名丢失 | `4623109` `api/responses.py:58-127`；`6a6afbe` `api_service_gemini.py:205-225` | 原生 | partial | `test_function_call_parser.py:109-137` 仅 functionCall；纯文本无签名用例 | port（是否回传文本签名） | port |
| D06 | tool_calls 解析（wire part→functionCall dict，含 call_id／thought_signature） | `4623109` `wire_parser.py:29-30,164-199,227-260,406-509` | 原生 | working | `test_wire_parser.py:150-177,303-340`；`test_function_call_parser.py:6-108` | keep | keep |
| D06 | tool_calls 渲染（Gemini functionCall／OpenAI tool_calls／Claude tool_use／Responses function_call） | `6a6afbe` `api/responses.py:92-109`；`api_service_gemini.py:77-84`；`c0412c8` `openai_response.py:38-64`；`1375646` `claude_response.py:44-76`；`2b39b53` `responses_response.py:88-108` | 新增 | working | `test_format_openai.py:493-504,546-602`；`test_format_claude.py:218-299,696-738`；`test_format_responses.py:118-160` | port | port |
| D06 | images／reasoning_images 解析（inlineData＋thought 分流） | `4623109` `wire_parser.py:155-166,443-466` | 原生 | working | `test_wire_parser.py:166-177`；`test_multimodal_pipeline.py:19-88` | keep | keep |
| D06 | images 渲染（Gemini inlineData／OpenAI＋Claude markdown data URL／Responses 文本化） | `6a6afbe` `api/responses.py:69-91`；`api_service_gemini.py:85-100`；`c0412c8` `openai_response.py:27-37`；`1375646` `claude_response.py:32-43`；`2b39b53` `responses_response.py:24-34` | 新增 | working | `test_format_openai.py::test_non_stream_images_become_markdown_data_urls`；`test_format_claude.py:597-607` | port | port |
| D06 | thinking／thought 文本解析（多处 flag 兼容） | `4623109` `wire_parser.py:140-153,477-482` | 原生 | working | `test_wire_parser.py:81-149`；`test_multimodal_pipeline.py:89-126` | keep | keep |
| D06 | thinking 渲染（Gemini thought=true／OpenAI reasoning_content／Claude thinking block／Responses reasoning item） | `6a6afbe` `api_service_gemini.py:72-76`；`c0412c8` `openai_response.py:104-220`；`1375646` `claude_response.py:87-96,138-352`；`2b39b53` `responses_response.py:67-75,151-170` | 新增 | working | `test_format_openai.py:546-602`；`test_format_claude.py:621-738`；`test_format_responses.py:201-244` | port | port |
| D06 | function_response／code execution／safety_ratings 解析 | `4623109` `wire_parser.py:187-210,467-474,497-508` | 原生 | working | `test_wire_parser.py::test_parse_response_part`；code execution 无独立用例 `未验证` | keep | keep |
| D06 | capture：hook-first 模板＋每次实时生成 snapshot | `4623109` `src/aistudio_api/infrastructure/gateway/capture.py:40-134`；`client.py:453-491` | 原生 | working | `test_browser_session.py:389-443`（单次全量 payload）；`test_multimodal_pipeline.py:19-88` | keep | keep |
| D06 | capture 模板缓存失效（账号切换／context 变化／强制刷新） | `4623109` `capture.py:35-38,104-133`；`client.py:205-217,622-623` | 原生 | working | `test_browser_session.py:389-443` 覆盖 context 切换清空；账号切换路径 `未验证` | keep | keep |
| D06 | replay：捕获模板原样回放（剥离 host／content-length） | `4623109` `replay.py:17-37`；`client.py:712-750` | 原生 | working | `test_browser_session.py:83-116`；`test_generation_routing.py:89-94` 仅 fake client | keep | keep |
| D06 | XHR 非流式回放＋鉴权头刷新（SAPISIDHASH／AuthUser／Tier） | `4623109` `transport.py:32-116,181-211` | 原生 | working | `test_browser_session.py:83-116`；鉴权头分支 `未验证` | keep | keep |
| D06 | XHR 流式队列（rid 分发、status／chunk／done／error、V8 清理） | `4623109` `transport.py:119-311` | 原生 | working | `test_stream_transport.py:28-84` | keep | keep |
| D06 | 流式超时／页面关闭／abort／close 中断语义 | `4623109` `transport.py:130-147,256-311` | 原生 | working | `test_stream_transport.py:87-190` | keep | keep |
| D06 | SSE 增量 JSON 解析（XSSI 前缀＋深度状态机切 chunk） | `4623109` `stream_parser.py:14-104` | 原生 | working | `test_response_parser.py:34-62,153-168` | keep | keep |
| D06 | wire spec 常量与 JspbArray 稀疏数组容器 | `4623109` `wire_spec.py:1-123` | 原生 | working | `test_wire_codec.py:339-377` 间接覆盖 | keep | keep |
| D06 | wire_types 请求编码（content/part/functionCall/schema 结构） | `4623109` `wire_types.py:247-400` | 原生 | working | `test_gemini_request_normalization.py:136,207,273,504`；`test_wire_codec.py:359-403` | keep | keep |
| D06 | wire_codec decode／rewrite／modify_body（模板改写与工具白名单过滤） | `4623109` `wire_codec.py:150-365,488-527` | 原生 | working | `test_wire_codec.py:21-45,305-450`；`test_gemini_request_normalization.py::test_normalize_gemini_request_encodes_function_declarations_to_wire_tools` | keep | keep |
| D06 | wire_parser 非流式解析与多 chunk 合并／response_id | `4623109` `wire_parser.py:406-565` | 原生 | working | `test_wire_parser.py:47-177,303-340`；`test_api_responses.py:224-271` | keep | keep |
| D06 | build REST body 分支（非流式，surface=build） | `ec35c8d` `generation_service.py:296-300,357-358`；`client.py:691-710` | 新增 | partial | 无 body 构造单测；`test_generation_routing.py:196-235` 仅 build busy 排队 | port（补 body 用例） | defer |
| D06 | build REST body 分支（流式，capture 置空） | `ec35c8d` `generation_service.py:553-563,618-625`；`client.py:581-600` | 新增 | partial | 无流式 body 单测；`test_build_surface.py` 属 D07 | port | defer |
| D06 | build 流式事件聚合为 ModelOutput（非流式） | `ec35c8d` `client.py:630-666` | 新增 | working | `test_build_surface.py:303,689`（事件级，D07） | port | defer |
| D06 | ATG 配置与按格式 mode 解析（off／prefer／required） | `792f4e1` `src/aistudio_api/application/atg/config.py:21-58,92-170` | 新增 | working | `test_atg_config.py:1-220` | port | port |
| D06 | ATG 文本／非文本模型过滤 | `792f4e1` `atg/model_filter.py:30-75` | 新增 | working | `test_atg_config.py::test_model_filter_defaults`；`test_atg_injector.py::test_non_text_model_passthrough` | port | port |
| D06 | ATG 客户端自合成通道检测（五信号，防双包装） | `792f4e1` `atg/client_patterns.py:63-126`；`atg/injector.py:129-140` | 新增 | working | `test_atg_client_patterns.py:1-135`；`test_atg_injector.py:43-104` | port | port |
| D06 | ATG 合成历史清理（防止控制轮／tool 声明累积） | `792f4e1` `atg/history_purge.py:15-42`；`atg/__init__.py:52-73` | 新增 | working | `test_atg_injector.py::test_request_dump_validate_roundtrip_is_idempotent` | port | port |
| D06 | ATG 请求注入（合成 tool＋控制轮＋tools／toolConfig 合并） | `792f4e1` `atg/injector.py:104-216`；`atg/__init__.py:52-73` | 新增 | working | `test_atg_injector.py:43-160`；`test_atg_integration.py:100-180` | port | port |
| D06 | ATG 非流式解包（synthetic→text＋真实 tool 保留＋冲突标记） | `792f4e1` `atg/extractor.py:86-197` | 新增 | working | `test_atg_extractor.py:40-129` | port | port |
| D06 | ATG 流式解包（事件级过滤、side buffer、跨 chunk 参数累积） | `792f4e1` `atg/stream_extractor.py:38-174`；`atg/stream_decoder.py:136-326` | 新增 | working | `test_atg_stream.py:25-131` | port | port |
| D06 | ATG 合成参数修复（markdown fence／截断 JSON／扫描回退） | `792f4e1` `atg/repair.py:21-192` | 新增 | working | `test_atg_repair.py:10-88` | port | port |
| D06 | ATG 非流式空响应重试（同账号，不换号） | `6a6afbe` `generation_service.py:329-378` | 新增 | working | `test_atg_integration.py:118-150` | port | port |
| D06 | ATG 流式空响应重试（produced_content 判定，同账号） | `6a6afbe` `generation_service.py:607-609,728-749` | 新增 | working | `test_atg_integration.py:290-327` | port | port |
| D06 | ATG required 模式校验（缺失显式抛错，不静默透传） | `792f4e1` `atg/extractor.py:188-195`；`atg/stream_extractor.py:158-165`；`generation_service.py:375-378,744-748` | 新增 | partial | `test_atg_extractor.py:110-129`；`test_atg_stream.py:91-103`；`test_atg_integration.py:275-287`（错误进 200 body） | port | port |
| D06 | ATG E5：ANY＋allowedFunctionNames 白名单合并合成 tool | `792f4e1` `atg/injector.py:165-199`；`wire_codec.py:320-364` | 新增 | working | `test_atg_injector.py:123-160`；`test_atg_integration.py:197-246,329-348` | port | port |
| D06 | countTokens 打包 body（system／tools／conversation 合并进 contents） | `af3f55c` `src/aistudio_api/application/token_counter.py:49-148` | 新增 | working | `test_token_counter.py:36-142` | port | port |
| D06 | countTokens 上游 RPC（GenerateContent→CountTokens URL 改写＋[0] 解析） | `af3f55c` `client.py:498-555` | 新增 | working | `test_token_counter.py:243-299`（mock session） | port | port |
| D06 | countTokens 路由（Gemini／Claude／Responses 三面） | `af3f55c` `routes_gemini.py:44-57`；`routes_claude.py:171-195`；`routes_responses.py:62-97` | 新增 | working | `test_token_count_routes.py:41-148` | port | port |
| D06 | OpenAI Chat Completions 适配面（请求转换＋非流／流响应） | `c0412c8` `format_converters/openai_request.py:1-444`；`openai_response.py:66-220`；`routes_openai.py:70-147` | 新增 | working | `test_format_openai.py:464-602,780-873` | port | port |
| D06 | Claude Messages 适配面（thinking／tool_use／tool_result／count_tokens） | `1375646` `format_converters/claude_request.py:1-446`；`claude_response.py:77-352`；`routes_claude.py:73-195` | 新增 | working | `test_format_claude.py:122-539,540-915` | port | port |
| D06 | OpenAI Responses 适配面（instructions／function_call／reasoning item／usage） | `2b39b53` `format_converters/responses_request.py:1-385`；`responses_response.py:44-415`；`routes_responses.py:100-177` | 新增 | working | `test_format_responses.py:72-244` | port | port |
| D06 | 格式共享层（媒体下载白名单＋thought signature 缓存） | `dcb7592` `format_converters/common.py:1-195`；`media.py:1-180`；`signature_cache.py:1-153` | 新增 | working | `test_format_claude.py:174-217,862-915`；`test_format_openai.py:174-217,816-873`；`test_signature_cache.py:10-27` | port | port |
| D06 | 格式面 feature gate（features.formats.*） | `dcb7592` `api/format_errors.py:69-114`；三路由 `require_*` | 新增 | working | `test_format_error_routing.py:61-85`；`test_format_claude.py:740-751` | port | port |
| D06 | 协议错误映射（OpenAI／Claude／Gemini 状态码与 body 形状） | `6a6afbe` `format_errors.py:18-66,126-220` | 新增 | working | `test_format_error_routing.py:28-106`；`test_format_responses.py:269-287` | port | port |
| D06 | 上游错误分类与切号／同号重试决策 | `6a6afbe` `upstream_errors.py:98-392`；`generation_service.py:424-467,694-726` | 新增 | working | `test_generation_routing.py:238-306`；`test_api_responses.py:104-127` | port | port |
| D06 | 测试钩子 forced 429（仅 AISTUDIO_TEST_HOOKS=1 生效） | `b59d6f3` `application/testing_hooks.py:35-62`；`generation_service.py:335-340,612-617`；`token_counter.py:191-196` | 新增 | working | `test_testing_hooks.py:9-25` | keep | keep |
| D06 | 非流式 204 空响应同号重试 | `6a6afbe` `upstream_errors.py` retry 分支；`generation_service.py:303-315` | 新增 | working | `test_generation_routing.py:238-254` | port | port |
| D06 | 取消／断连时 lease 释放（finally＋holder） | `6a6afbe` `generation_service.py:79-91,453-457,819-852` | 新增 | working | `test_generation_routing.py:292-306,336-355` | port | port |
| D06 | 原始请求／响应落盘（dump_raw_response） | `4623109` `client.py:426-451`；`streaming.py:25-50` | 原生 | unverified | 未验证（仅 settings 开关，无测试） | keep | port |
| D06 | chat 流式网关聚合（status→chunk→finish／usage，非 200 分类） | `4623109` `streaming.py:69-155` | 原生 | unverified | `test_stream_transport.py` 仅底层；网关无单测 | keep | keep |
| D06 | 生图旧路径（size 映射＋search tool＋replay＋保存） | `4623109` `client.py:752-840`；`wire_codec.py:36-149` | 原生 | working | `test_multimodal_pipeline.py:43-88`；`test_wire_codec.py:46-133` | keep | keep |
| D06 | 官方 API key 仅用于 countTokens 完整计数（不生成、不回显、不落日志） | 用户要求；无 Git 溯源 | 新增 | unverified | 未验证；需安全存储/脱敏/权限测试 | port | port |

### D07 Build Surface / 9998 / Signer

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D07 | Build app_id 解析（config `channels.build.app_url` → `AISTUDIO_APP_URL` → settings → Ark 默认） | `1cc634e`, `da6ea25`; `build_surface.py:421-446`, `model_defaults.py` channels.build.app_url | 新增 | working | `test_build_surface.py:350-393`（env/default/config/裸 id 四例） | keep | keep |
| D07 | Build 宿主页创建（context 内建页 else 独立新页，`block_assets=True`，建页时注入 realm tag） | `1cc634e`, `28c981f`; `build_surface.py:1044-1056`; `cdp_client.create_page_in_context` | 新增（`create_page_in_context` 亦为 `28c981f` 新增） | working | `test_build_surface.py:397-430`; `test_cdp_browser_context.py:28-45` | port | port |
| D07 | Build 页面复用快路径（同 auth + 页面存活 + ready + 无 last_error；WS 断开先试原地恢复） | `d4ff844`, `8e5e0cb`; `build_surface.py:1012-1032` | 新增 | partial | 无单测；实机复用命中见 `%TEMP%\kilo\wp6-verify\reuse-run1.sse`、`reuse-run2.sse`（不可复跑） | port | port |
| D07 | Cookie 注入（`load_cookies_to_page` + 空 auth/注入失败 fail-closed `COOKIES_MISSING`） | `1cc634e`; `build_surface.py:1063-1077`；复用原生 `session_auth.load_cookies_to_page` | 新增（build 调用点）/原生（load_cookies_to_page） | working | `test_build_surface.py:554-561`；`%TEMP%\kilo\p3-build3\acceptance-run1.log` 载入 7 Cookie（不可复跑） | port | port |
| D07 | AUTH_RESPONDER 预注入（`requestAuthIndex` → `authIndex=auth_user`，否则宿主 boot 静默等待） | `1cc634e`; `build_surface.py:110-124`, `:1086-1092` | 新增 | unverified | 无单测；仅 spikeP 配方注释（`build_surface.py:109`） | port | port |
| D07 | Build 资源拦截组（chat 列表去掉编辑库组，Monaco/vs 在 build 放行） | `1cc634e`; `cdp_client.py:26-79`, `build_surface.py:449-457`, `:1059-1061` | 新增 | working | `test_build_signer_assets.py:135-172` | port | keep |
| D07 | 前台/focus 与导航 settle（`Page.bringToFront` + `Emulation.setFocusEmulationEnabled` + 180s goto + 2s 稳定） | `1cc634e`, `d4ff844`; `build_surface.py:1098-1114` | 新增 | unverified | 无单测（FakePage 吞掉 CDP send）；仅注释引用 spikeP | port | keep |
| D07 | 过场弹窗点击器（Continue to the app / Skip / Launch，精确匹配后模糊匹配） | `1cc634e`; `build_surface.py:78-98`, `:1211-1227` | 新增 | unverified | 单测以 `fast_gate` fixture 直接跳过（`test_build_surface.py:221-233`） | port | keep |
| D07 | human gate 可信输入（45 次 mouseMoved + 真实 press/release + 8 次 Escape；native 就绪后再过一次门） | `d4ff844`; `build_surface.py:100-105`, `:1361-1421`, `:1167-1168` | 新增 | unverified | 无单测；`p3-build3` 日志最终连上 9998（不可复跑，未隔离门本身） | port | keep |
| D07 | 9998 WS sink 与 realm 分桶（`?realm=` 解析、按 realm 归桶、request_id→ws+queue、断连回灌 error） | `1cc634e`, `28c981f`; `build_surface.py:129-226` | 新增 | partial | `test_cdp_browser_context.py:47-70` 仅覆盖 realm 解析与分桶；sink 本体未测；`p3-build3` 日志 `server listening on 127.0.0.1:9998`（不可复跑） | port | port |
| D07 | realm WS 标记注入（`:9998` URL 补 `?realm=`，`addScriptToEvaluateOnNewDocument`） | `28c981f`; `build_surface.py:907-934`, `:1054` | 新增 | unverified | 无单测 | port | port |
| D07 | 原生就绪判定（9998 连接即 `native_ready=True`/`ready_state=native`，signer 不作前置） | `8dd6782`; `build_surface.py:1156-1168` | 新增 | working | `test_build_surface.py:397-430`, `:432-501`, `:540-551` | port | port |
| D07 | signer 后台探测（native 就绪后 `create_task`，strict=False，不阻塞 dispatch；重建时取消旧任务） | `8e5e0cb`; `build_surface.py:1174-1176`, `:1190-1209`, `:1041-1042` | 新增 | working | `test_build_surface.py:432-501`（`_await_signer_probe`） | port | port |
| D07 | signer 状态机（installing/harvesting/warmup-captured/ready/failed；ready 需 tb + 自检通过 + 收割 key） | `1cc634e`, `8dd6782`; `build_signer.js:74-163` | 新增 | working | `test_build_signer_assets.py:47-82` | port | port |
| D07 | signer 结构发现与自检重试（snapshot+kvc+content 约束；tokenLike H/A/getMetadata；最多 3 次；warmup 复位重试） | `8dd6782`, `1cf3994`; `build_signer.js:166-275`, `:1037-1117` | 新增 | working | `test_build_signer_assets.py:60-129`；`%TEMP%\kilo\probe_signer_live.py` 只读取证快照（不可复跑） | port | port |
| D07 | XHR/fetch 收割头白名单与 warmup 本地吞没（`__warmup__` 零上游流量） | `1cc634e`, `8dd6782`; `build_signer.js:55-63`, `:1119-1259` | 新增 | working | `test_build_signer_assets.py:105-113`；`p3-build3` 日志 harvest 6 头（不可复跑） | port | port |
| D07 | warmup 规格下发（经 9998 向 app 客户端下发 `__warmup__:streamGenerateContent`） | `1cc634e`, `d4ff844`; `build_surface.py:1423-1451` | 新增 | partial | 无单测；`p3-build3` 日志“已下发 warmup 规格”后 1s signer ready（不可复跑） | port | port |
| D07 | native dispatch + SSE pump（request_id 关联 9998 会话；headers/chunk/stream_close/error 归一为 chunk/status/done/error） | `8dd6782`; `build_surface.py:819-905` | 新增 | working | `test_build_surface.py:607-634` | port | port |
| D07 | 传输选择（默认 native；`AISTUDIO_BUILD_TRANSPORT=signer/native` 显式开关，仅在目标健康时生效） | `8dd6782`; `build_surface.py:366-377` | 新增 | working | `test_build_surface.py:585-605` | port | port |
| D07 | native→signer 首帧前回退（下发失败或 error 且未出帧；已出帧不回退不重放） | `8dd6782`; `build_surface.py:1664-1679`, `:1693-1700` | 新增 | working | `test_build_surface.py:636-679` | port | port |
| D07 | native 断线重试（请求前无连接先轻量恢复；dispatch 返回 None 再恢复重发；首帧前 error 重试一次） | `1cf3994`; `build_surface.py:1577-1579`, `:1616-1646`, `:1684-1692` | 新增 | working | `test_build_surface.py:792-900` | port | port |
| D07 | 原地恢复 `_recover_warmup`（过门→等 9998→重发 warmup→可选等 signer，不重导航） | `1cf3994`; `build_surface.py:1248-1310` | 新增 | partial | 单测仅以 monkeypatch 替身验证调用次数（`test_build_surface.py:792-900`）；真实恢复路径 `未验证` | port | port |
| D07 | watchdog（5s 轮询 / 断线 10s 宽限 / 后台轻量重连；`_ensure_lock` 持有时跳过，避免并发导航） | `1cf3994`, `8e5e0cb`; `build_surface.py:1312-1359` | 新增 | unverified | 无单测、无本地日志证据；且 `:1314` 无 `_realm_id` 时直接不启动 | port（需补测） | port |
| D07 | `BUILD_BUSY`（页面有在途请求时禁止换账号或重载） | `8e5e0cb`; `build_surface.py:1004-1010` | 新增 | working | `test_build_surface.py:528-537` | port | keep |
| D07 | 流式超时（`TIMEOUT`，每次出帧重置 deadline；native 超时可回退 signer 续期一次） | `1cc634e`, `8dd6782`; `build_surface.py:1708-1723`, `:1808-1814` | 新增 | working | `test_build_surface.py:707-713` | port | port |
| D07 | `PAGE_CLOSED`（就绪轮询期、流式循环、queue 等待期三处检测） | `1cc634e`; `build_surface.py:1478-1484`, `:1711-1735` | 新增 | working | `test_build_surface.py:716-720` | port | keep |
| D07 | 就绪期错误族（`NAVIGATION_FAILED` / `APP_NOT_CONNECTED` / `SIGNER_FAILED`，均带 stage+处置建议） | `1cc634e`, `da6ea25`; `build_surface.py:1110-1121`, `:1149-1155`, `:1510-1517` | 新增 | working | `test_build_surface.py:397-501`（间接覆盖成功/失败分支）；APP_NOT_CONNECTED 无单测 | port | keep |
| D07 | 分发型错误族（`DISPATCH_FAILED` / `NATIVE_WS_MISSING` / `SIGNER_ERROR` / `NATIVE_ERROR`） | `1cc634e`, `8dd6782`; `build_surface.py:1648-1662`, `:1693-1700`, `:1783-1797` | 新增 | working | `test_build_surface.py:659-679`, `:693-704` | port | keep |
| D07 | `NO_CONVERTIBLE_FRAMES` 与丢弃帧诊断（dropped 样本进错误体/告警，不静默丢） | `1cc634e`; `build_surface.py:1815-1830`, `:1744-1756` | 新增 | working | `test_build_surface.py:723-737` | port | keep |
| D07 | rid 任务队列隔离（`state.task_queues` + binding demux + 满队列反压告警） | `1cc634e`; `build_surface.py:942-973`, `:1604-1606`, `:1831-1837` | 新增 | working | `test_build_surface.py:752-778` | port | port |
| D07 | `abort_stream(rid)` 按任务中止（写 error 事件到该 rid 队列） | `1cc634e`; `build_surface.py:1844-1854` | 新增 | partial | `test_build_surface.py:781-789`；`src` 内无调用方（仅 `transport.py` 同名方法），HTTP 断开不会触发 | drop（除非接 HTTP 断开） | port |
| D07 | Holder 解码（2–3 层嵌套数组 / 裸叶 / 单层转义字面量 → leaf 对象） | `1cc634e`; `build_surface.py:480-530` | 新增 | working | `test_build_surface.py:260-287` | port | keep |
| D07 | REST→wire 适配（candidate/part/usage/responseId 索引映射 + finishReason 名称表） | `1cc634e`; `build_surface.py:549-631`, `:235-267` | 新增 | working | `test_build_surface.py:260-320` | port | keep |
| D07 | JSPB `convChunk` 兼容分支（位置数组叶 → REST 再转 wire） | `1cc634e`; `build_surface.py:673-754` | 新增 | working | `test_build_surface.py:322-330` | port（仅兼容保留） | keep |
| D07 | 上游错误包识别（`error` dict → `UPSTREAM_ERROR`；RPC 记录 → `UPSTREAM_RPC_ERROR`） | `1cc634e`; `build_surface.py:757-790` | 新增 | working | `test_build_surface.py:331-349` | port | keep |
| D07 | Build 事件→chat 面事件映射（status/body/thinking/tool_calls/images/usage/finish_reason/response_id/thought_signature） | `1cc634e`; `build_surface.py:1738-1782`, `:1839-1842` | 新增 | working | `test_build_surface.py:569-582`, `:681-691` | port | keep |
| D07 | `BuildSurfaceError`→HTTP（可重试集合 503，其余 502，带 code/detail/advice） | `1cc634e`; `app.py:311-334` | 新增 | working | `test_app_error_handlers.py:24-29` | port | keep |
| D07 | **身份事实**：build 认证只取 lease（`_build_lease_auth`），执行期包在控制面 `self._session.request_scope()` | `8e5e0cb`; `client.py:158-177`, `:588-599`, `:698-710`; `session.py:146-160` | 新增 | working | `test_build_lease_identity.py:11-40`；`request_scope` 记的是全局 session/`active_context` 计数 | port（须显式标注） | port |
| D07 | build 面实例配额（无 lease 时按 active account/group + chat+build 活页数校验 `max_page_instances`） | `ec35c8d`, `d8bbb22`; `client.py:307-360`, `:586-587`, `:696-697` | 新增 | working | `test_rotator_surface_keys.py:237-296`（含 build 分支先配额后就绪） | port（归属 D04/D05 实现） | port |
| D07 | build-only 实例集成（bootstrap chat 页关闭、`build_ready` 需 native/signer 至少一路、并发建页只计 1、失败释放预约） | `da6ea25`, `8e5e0cb`; `instance_pool.py:110-140`, `:1028-1036`, `:1155-1171` | 新增 | working | `test_instance_pool.py:204,230,307,421,482,529,861,890` | port（归 D04 实现） | port |
| D07 | 非流式 build 收集（`_collect_build_output`：status≥400 抛错，聚合 text/thinking/tools/images/usage） | `ec35c8d`; `client.py:630-666` | 新增 | partial | 无直接单测（`未验证`）；仅流式路径有覆盖 | port | port |
| D07 | 清理路径（新页未就绪即关闭、finally 取消 pump/清 pending/task_queues、切号与 close 关 surface、slot 关页） | `1cc634e`, `8e5e0cb`; `build_surface.py:1180-1186`, `:1831-1837`; `client.py:226-235`, `:419-424`; `instance_pool.py:142-155` | 新增 | working | `test_instance_pool.py:307`; `test_build_surface.py:569-582`（in_flight 归零） | port | port |
| D07 | signer 脚本缺失降级（只启用原生 9998，记录 `signer_error`，不阻塞渠道） | `8dd6782`; `build_surface.py:1079-1085`, `:1292-1298`, `:1177-1179` | 新增 | working | `test_build_surface.py:540-551` | port | port |
| D07 | 失败现场截图 PNG + 页面 HTML dump（Build 流程失败时保存，供 debug） | 用户要求/参考项目；无 Git 溯源 | 新增 | unverified | 未验证；参考项目实现待移植 | port | port |

### D08 Keepalive / Rotation / 24h

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D08 | Keepalive 服务启停与单循环生命周期 | `1f9ba61 src/aistudio_api/application/account_keepalive.py:100-188,426-458`; `1f9ba61 src/aistudio_api/api/app.py:165-173,255-256` | 新增 | partial | `未验证`（无 start/stop 生命周期测试） | keep | port |
| D08 | 四类 job 与 feature 默认值（auth 24h / metadata 24h / auto_switch 3h / activity 60s） | `1f9ba61 account_keepalive.py:27-84`; `1f9ba61 src/aistudio_api/infrastructure/gateway/model_defaults.py:588-597` | 新增 | working | `tests/unit/test_keepalive_config.py:14-32`（本轮 20 passed） | keep | port |
| D08 | 单一 job 锁（串行、busy 立即返回） | `1f9ba61 account_keepalive.py:125,228-252` | 新增 | partial | `tests/unit/test_routes_keepalive.py:55-63` 仅 Fake busy 映射 | keep | port |
| D08 | next_due 计算与 status 暴露 | `1f9ba61 account_keepalive.py:143-152,190-217,445-447`; `1f9ba61 src/aistudio_api/api/routes_keepalive.py:20-30` | 新增 | partial | `未验证`（重启后重算、busy 仍推进 due） | keep | port |
| D08 | 失败/重试语义（仅 auth_update/no_active_page 60s 重试） | `1f9ba61 account_keepalive.py:181-188,206-217,236-244` | 新增 | partial | `未验证`（其余任务失败后等下个完整周期） | keep | port |
| D08 | metadata_refresh（启动即跑、按 tier_checked_at 判到期、排除 expired、互斥跳过） | `1f9ba61 account_keepalive.py:265-309`; `dad9cf7 src/aistudio_api/infrastructure/account/account_info_refresher.py:309-344` | 新增 | working | `tests/unit/test_account_keepalive.py:164-222` | keep | keep |
| D08 | metadata 失败只记 attempt、24h 后才再试 | `1f9ba61 account_keepalive.py:289-309`; `dad9cf7 src/aistudio_api/infrastructure/account/account_store.py:680` | 新增 | partial | `未验证`（5xx 不改状态，但 due 仍被推到下周期） | keep | port |
| D08 | auth_update（24h、pool 优先、active session 兜底、no_active_page 60s） | `1f9ba61 account_keepalive.py:311-355`; `1f9ba61 src/aistudio_api/infrastructure/gateway/session.py:542-550` | 新增 | working | `tests/unit/test_account_keepalive.py:128-160,405-428` | keep | keep |
| D08 | pool cookie 回写（逐 slot 写各自 auth.json） | `1201a37 src/aistudio_api/infrastructure/gateway/client.py:187-197`; `1f9ba61 account_keepalive.py:312-338` | 新增 | working | `tests/unit/test_account_keepalive.py:340-366` | keep | keep |
| D08 | auth_update 对 build-only 实例的覆盖 | `1201a37 client.py:192`; `1f9ba61 account_keepalive.py:320-323` | 新增 | broken | `未验证`（仅 `chat_ready()` 才回写/记账，build-only 被跳过） | port | port |
| D08 | auth_update 将 chat_ready 槽位全部标记已保存（不看逐 slot 结果） | `1f9ba61 account_keepalive.py:318-325`; `1201a37 client.py:194-196` | 新增 | partial | `未验证`（`save_pool_cookies` 吞异常只返回总数） | port | port |
| D08 | active session cookie 回写 | `1f9ba61 session.py:542-550`; `1f9ba61 client.py:183-185` | 新增 | unverified | `未验证`（keepalive 测试用 FakeClient） | keep | keep |
| D08 | session.close 关闭前保存 cookies | `4586271 session.py:585-589`; `1f9ba61 session.py:587-588` | 改造 | unverified | `未验证`（`test_session_lifecycle.py:126-141` 仅覆盖回收） | keep | keep |
| D08 | active_activity（控制面 page 滚动 + touch_activity，默认关） | `1f9ba61 account_keepalive.py:415-424`; `1f9ba61 model_defaults.py:596-597` | 新增 | unverified | `未验证`（无该 job 测试；实例化后无稳定控制页） | drop | keep |
| D08 | auto_switch job（默认关、3h、pool 存在即走 rebalance） | `1f9ba61 account_keepalive.py:357-374`; `1f9ba61 model_defaults.py:588-589` | 新增 | working | `tests/unit/test_account_keepalive.py:370-402` | keep | drop |
| D08 | InstanceSwitcher.rebalance 规划与 rebind（仅 idle、最受限优先、按 group/channel 资格） | `dd5e359 src/aistudio_api/application/instance_switch.py:71-137`; `b59d6f3 instance_switch.py:140-216` | 新增 | working | `tests/unit/test_instance_switch.py:52-171`（本轮 20 passed） | port | drop |
| D08 | auto switch 是否尊重 pinned | `79901b0 src/aistudio_api/infrastructure/gateway/instance_pool.py:76,771,866`; `dd5e359 instance_switch.py:81-87,105-136,186-194` | 新增 | broken | `未验证`；planner 不读 `pinned`，`rebind` 不拒绝 pinned 槽 | port | drop |
| D08 | auto switch 的 rotator 兜底路径与 keepalive 候选（按 auth_saved_at LRU） | `1f9ba61 account_keepalive.py:376-413`; `1f9ba61 src/aistudio_api/application/account_rotator.py:742-772` | 新增 | legacy | `tests/unit/test_account_keepalive.py:226-315` 仅 FakeClient；生产 pool 恒存在 | drop | drop |
| D08 | switch_to_account 共享切号锁与 require_idle | `1f9ba61 src/aistudio_api/application/account_orchestrator.py:32-65`; `routes_accounts.py:280-282`; `routes_system.py:378-380` | 新增 | unverified | `未验证`（路由/手动切换实际调用，无直接单测） | keep | port |
| D08 | 429 冷却分层与 drip（60s→300s→600s） | `d8bbb22 account_rotator.py:335-367`; 基线 `4623109` 已有 date-key/cooldown | 改造 | working | `tests/unit/test_account_orchestrator.py:327-344`；`test_rotator_surface_keys.py`（本轮 43 passed） | keep | keep |
| D08 | 美西午夜跨天重置与 session_expired 86400 隔离 | `9fd237d account_rotator.py:241-265`; `413496f account_rotator.py:398-415`; `d8bbb22 account_rotator.py:241-265` | 改造 | partial | `test_account_orchestrator.py:305-324`；`test_account_rotation_concurrency.py:306-309`（只验 0..86400） | keep | keep |
| D08 | idle browser 回收监控（pool 有活跃工作时 touch、否则 check_idle_timeout） | `4aa35cb api/app.py:175-195`; `86391c8 app.py:182-191`; `86391c8 src/aistudio_api/config.py:276-294`; `4623109 session.py:197-215` | 改造 | partial | `tests/unit/test_session_lifecycle.py:126-141,159-189`；app 监控循环未验证 | keep | keep |
| D08 | tier/email 启动自动校验（仅 email 缺失或 tier=Free 时探测） | `6384ca9 api/app.py:197-241`; `47cec70 app.py:208` | 改造 | unverified | `未验证`（无 `_auto_refresh_tiers` 测试） | keep | keep |
| D08 | AccountInfoRefresher 批量刷新/进程互斥/401 标过期/5xx 不改状态/邮箱回填 | `dad9cf7 account_info_refresher.py:208-344`; `0a3342b`; `1ef8ac2`; `fcd6f1f` | 新增 | working | `tests/unit/test_account_info_refresh.py`（本轮 38 passed 含 session） | keep | keep |
| D08 | Keepalive HTTP 状态与手动运行（409 busy / 400 unknown） | `1f9ba61 src/aistudio_api/api/routes_keepalive.py:1-50`; `1f9ba61 app.py:38,274-278` | 新增 | working | `tests/unit/test_routes_keepalive.py:32-76` | keep | keep |

### D09 Logs / Observability

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D09 | 统一日志格式与 TTY 颜色（`UnifiedFormatter`） | `880954f`（upstream）；`src/aistudio_api/infrastructure/utils/logger.py:26-60` | 原生 | working | `tests/unit/test_logging.py:33-54` | keep | keep |
| D09 | stderr sink + uvicorn/uvicorn.error/uvicorn.access 接入统一格式（propagate=False） | `880954f`（upstream）；`logger.py:269-295` | 原生 | working | 代码证据；`tests/unit/test_logging.py:68-86` 仅覆盖级别 | keep | keep |
| D09 | `setup_logging` 三 sink 装配（stderr + broadcast + daily file，幂等 clear） | `eed30b6`；`logger.py:264-297` | 改造 | working | 代码证据；`api/app.py:49-50` import 时执行 | keep | keep |
| D09 | root 固定 DEBUG 捕获全量，stderr 按 level 过滤 | `eed30b6`、`86391c8`；`logger.py:245-258`、`:283` | 改造 | working | 代码证据；无独立断言 | port | port |
| D09 | 动态日志级别 `set_log_level`（aistudio/uvicorn 同步） | `880954f`（upstream）；`logger.py:229-261` | 原生 | working | `tests/unit/test_logging.py:68-86` | keep | keep |
| D09 | 启动期读取 `logging.level` 并初始化 | `880954f`（upstream）；`api/app.py:49-50`；`infrastructure/gateway/model_defaults.py` | 原生 | working | 代码证据；`test_logging.py:182-193` 覆盖 /config 字段 | keep | keep |
| D09 | 运行期 `PUT /config/logging`（level/dump_requests/dump_to_file/dump_dir）持久化 | `880954f`、`d8b1c6c`（upstream）；`api/routes_system.py:494-549`；`web/src/components/settings/LogConfigCard.vue` | 原生 | working | `tests/unit/test_logging.py:195-230` | keep | keep |
| D09 | `DEBUG`/`AISTUDIO_DEBUG` 仅触发 dump、不改日志级别 | `880954f`（upstream）；`logger.py:169-217` | 原生 | working | `tests/unit/test_logging.py:89-123` | keep | keep |
| D09 | 应用日志总线（`BroadcastHandler`→ring→SSE），与请求生命周期日志分离 | `eed30b6`；`log_broadcast.py:276-298`、`:415-419` | 新增 | working | `tests/unit/test_log_broadcast.py:141-160` | keep | keep |
| D09 | ring 容量 5000 + 单调整数 id | `eed30b6`、`86391c8`（2000→5000）；`log_broadcast.py:23`、`:150-155`、`:184-193` | 新增 | working | `tests/unit/test_log_broadcast.py:24-38`、`:72-79` | keep | keep |
| D09 | WARNING 保底淘汰（DEBUG/INFO 先出队） | `86391c8`；`log_broadcast.py:26`、`:194-201` | 新增 | working | `tests/unit/test_log_broadcast.py:82-91` | keep | keep |
| D09 | `replay(level,last_id,limit)` 默认回放 5000 | `eed30b6`、`86391c8`（100→5000）；`log_broadcast.py:25`、`:239-261` | 新增 | working | `tests/unit/test_log_broadcast.py:40-79` | keep | keep |
| D09 | gap 事件（`last_id < oldest_id-1`） | `eed30b6`；`log_broadcast.py:251-254`；`api/routes_logs.py:63-65` | 新增 | working | `tests/unit/test_logs_routes.py:83-107` | keep | keep |
| D09 | 订阅上限 8，超限返回 503 | `eed30b6`；`log_broadcast.py:27`、`:213-232`；`routes_logs.py:133-134` | 新增 | working | `test_log_broadcast.py:111-119`；`test_logs_routes.py:183-196` | keep | keep |
| D09 | 订阅队列 500，满则丢最旧并累计 dropped | `eed30b6`；`log_broadcast.py:24`、`:263-273` | 新增 | working | `test_log_broadcast.py:94-108`；`routes_logs.py:92-94` | keep | keep |
| D09 | `dropped` 事件下发 | `eed30b6`；`routes_logs.py:67-68`、`:92-94` | 新增 | working | 代码证据；`test_logs_routes.py:109-125` 未断言 dropped | keep | keep |
| D09 | SSE 心跳 15s（`: ping`）+ 断连清理 | `eed30b6`；`routes_logs.py:30`、`:88-102`、`:108-109` | 新增 | working | `tests/unit/test_logs_routes.py:109-125` | keep | keep |
| D09 | `/logs/stream` 路由（level 校验、Last-Event-ID、SSE 头、web auth） | `eed30b6`；`routes_logs.py:112-153`；`api/app.py:273` | 新增 | working | `tests/unit/test_logs_routes.py:127-210` | keep | keep |
| D09 | 总线/落盘脱敏 `sanitize_log_message`（auth/cookie/sapisid/api key/bearer） | `eed30b6`；`log_broadcast.py:40-88`、`:182`、`:381` | 新增 | working | `tests/unit/test_log_broadcast.py:122-139`；`test_log_archive.py:47-61` | keep | keep |
| D09 | dump 报文头/查询参数脱敏 `_mask_headers/_mask_query_params` | `4cd38ae`（upstream）；`logger.py:311-337` | 原生 | working | `tests/unit/test_logging.py:126-178` | keep | keep |
| D09 | dump 落盘 `req_*.json` + `latest_request.json` | `d8b1c6c`（upstream）；`logger.py:339-449` | 新增 | partial | `tests/unit/test_logging.py:247-309`；落盘含原始 `headers`/`raw_body`（`logger.py:428`、`:433`） | port | port |
| D09 | dump 开关链（env/config/runtime override；`dump_to_file` 默认跟随 `dump_requests`） | `880954f`、`d8b1c6c`（upstream）；`logger.py:72-105`、`:189-227` | 原生 | working | `tests/unit/test_logging.py:89-123`、`:195-230` | keep | keep |
| D09 | 请求生命周期 access log（middleware，独立于应用总线） | `880954f`（upstream）；`api/app.py:376-480`、`:471-478` | 原生 | partial | 代码证据；`test_logging.py:233-244` 仅覆盖 dump 路径；uvicorn.access 另出同类行 | keep | keep |
| D09 | dump 模式响应体缓存回放 + SSE 不消费 body | `880954f`（upstream）；`api/app.py:438-470` | 原生 | working | 代码证据；无独立断言 | keep | keep |
| D09 | 按日归档 `aistudio-YYYYMMDD.log` + 跨天切换文件 | `eed30b6`；`log_broadcast.py:317-322`、`:356-412` | 新增 | working | `tests/unit/test_log_archive.py:41-77` | keep | keep |
| D09 | 归档 7 天保留清理（启动 + 跨天） | `eed30b6`；`log_broadcast.py:28`、`:325-353`；`logger.py:285-287` | 新增 | working | `tests/unit/test_log_archive.py:80-102` | keep | keep |
| D09 | 日志导出：单日 `.log`、多日 `.zip`、最多 7 天、web auth | `eed30b6`；`routes_logs.py:31`、`:166-260` | 新增 | partial | `tests/unit/test_logs_routes.py:212-309`；单日缺文件返回 200 空文件（`routes_logs.py:244-250`） | keep | keep |
| D09 | LogsView SSE 消费、指数退避重连、401 跳登录 | `c7bdd80`；`web/src/views/LogsView.vue:337-405`；`web/src/api/logs.ts:24-47` | 新增 | unverified | 无前端自动化测试；截图 `docs/dev/evidence/wp7-v2-20261020/p5-logs-*.png` | keep | keep |
| D09 | LogsView 级别过滤 + 多词搜索高亮 | `c7bdd80`、`2944c3c`；`LogsView.vue:116-173`、`:212-237` | 新增 | unverified | 无前端测试；代码证据 | keep | keep |
| D09 | LogsView 暂停/继续、清屏、暂停缓冲计数 | `c7bdd80`；`LogsView.vue:260-295`、`:733-739` | 新增 | unverified | 无前端测试；代码证据 | keep | keep |
| D09 | LogsView 下载当前过滤结果 | `c7bdd80`；`LogsView.vue:436-454` | 新增 | unverified | 无前端测试；客户端 Blob 导出 | keep | keep |
| D09 | LogsView 按时间导出（1/3/7 天、范围选择、ZIP） | `c7bdd80`；`LogsView.vue:456-491`；`web/src/api/logs.ts:55-94` | 新增 | unverified | 无前端测试；后端测试见导出行 | keep | keep |
| D09 | LogsView 前端 5000 条上限 + 虚拟滚动 | `c7bdd80`；`LogsView.vue:29-30`、`:175-182`、`:283-295` | 新增 | unverified | 无前端测试；代码证据 | keep | keep |
| D09 | gap/dropped 提示条（UI） | `c7bdd80`；`LogsView.vue:324-334`、`:741-755` | 新增 | partial | 服务端 gap 载荷为 `last_id/oldest_id`（`routes_logs.py:63-65`），UI 读取 `missed/dropped/gap`，只显示泛化文案；dropped 提示不清除 | port | port |
| D09 | WARP/tunnel/CDP 及三方库 DEBUG 噪音抑制 | 无实现提交；`logger.py:245-258`（root DEBUG）、`log_broadcast.py:194-201`（仅靠淘汰） | 待判定 | unverified | 未验证；未发现按 logger 名过滤（如 websockets/httpx/uvicorn.access/CDP） | port（需新增） | port |

### D10 Stats / Token / Usage

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D10 Stats/Token/Usage | 上游 attempt 总量计数（requests/success/rate_limited/errors/last_used） | `4623109` `src/aistudio_api/api/state.py:25-39,120-151`；`1adf3c2` `src/aistudio_api/api/state.py:65-133,248-263` | 原生 | working | `tests/unit/test_model_stats_v2.py:31-81`；`tests/unit/test_runtime_persistence.py:13-43` | keep | port |
| D10 Stats/Token/Usage | attempt Token 累计（prompt/completion/total） | `4623109` `src/aistudio_api/api/state.py:120-151`；`1adf3c2` `src/aistudio_api/api/state.py:216-245` | 原生 | working | `tests/unit/test_model_stats_v2.py:69-75`；`tests/unit/test_runtime_persistence.py:30-34` | keep | port |
| D10 Stats/Token/Usage | attempt 失败覆盖（实际只记 429 与服务端异常） | `4623109` `src/aistudio_api/application/api_service_gemini.py:205,281,288`；`6a6afbe` `src/aistudio_api/application/upstream_errors.py:247-392` | 原生 | partial | `src/aistudio_api/application/upstream_errors.py:186-245,290-360` 无 record 调用；完整分母未验证 | port（补齐或改名“部分错误”） | port |
| D10 Stats/Token/Usage | client 层总量（client_requests/success/errors/rate_limited/aborted） | `1adf3c2` `src/aistudio_api/api/state.py:73-77,266-278,456-470`；`src/aistudio_api/application/generation_service.py:470-503,810-852` | 新增 | partial | `tests/unit/test_model_stats_v2.py:31-81`；`tests/unit/test_stats_routes.py:39-60` | port（保留两层） | port |
| D10 Stats/Token/Usage | client × surface 分桶 | `1adf3c2` `src/aistudio_api/api/state.py:265-270`；`src/aistudio_api/application/generation_service.py:488-495,837-850` | 新增 | partial | 仅 `generation_service.py:497-502` 传 surface；失败/流式分支未传（`:488-495,837-848`） | port（修正全部调用点） | port |
| D10 Stats/Token/Usage | client 429 同时记 client_errors 与 client_rate_limited | `1adf3c2` `src/aistudio_api/api/state.py:273-275`；`tests/unit/test_model_stats_v2.py:52-67` | 新增 | working | `tests/unit/test_model_stats_v2.py:66-67` | keep 或 drop（二选一） | port |
| D10 Stats/Token/Usage | client aborted 归类（cancel/GeneratorExit/已出流异常） | `1adf3c2` `src/aistudio_api/application/generation_service.py:836-848`；`src/aistudio_api/api/state.py:271-272` | 新增 | unverified | 未验证（无 aborted 用例） | keep | port |
| D10 Stats/Token/Usage | format 分桶（api_format） | `6a6afbe` `src/aistudio_api/api/state.py:140-146`；`1adf3c2` `src/aistudio_api/api/state.py:438-443` | 改造 | working | `tests/unit/test_model_stats_v2.py:76-78` | keep | keep |
| D10 Stats/Token/Usage | surface 分桶（attempt） | `1adf3c2` `src/aistudio_api/api/state.py:444-450`；`src/aistudio_api/application/generation_service.py:401-407,787-793` | 新增 | working | `tests/unit/test_model_stats_v2.py:79-80` | keep | keep |
| D10 Stats/Token/Usage | daily 聚合 + 90 天保留裁剪 | `1adf3c2` `src/aistudio_api/api/state.py:29,314,357-367,420-424` | 新增 | partial | `tests/unit/test_model_stats_v2.py:169-188` 仅 today；prune 无测试 | port（载入时也 prune） | keep |
| D10 Stats/Token/Usage | all-time 与 daily 分离（all 读 model_stats） | `1adf3c2` `src/aistudio_api/api/state.py:530-551` | 新增 | working | `tests/unit/test_stats_routes.py:50-65` | keep | keep |
| D10 Stats/Token/Usage | schema v2 存取 + v1 合并 + 模型名归一 | `1adf3c2` `src/aistudio_api/api/state.py:28,34-40,150-213,321-355,369-391` | 改造 | working | `tests/unit/test_model_stats_v2.py:126-166`；`tests/unit/test_runtime_persistence.py:13-43` | keep | keep |
| D10 Stats/Token/Usage | 2s debounce / 5s 强制 / 失败立即落盘 | `4623109` `src/aistudio_api/api/state.py:93-118`；`1adf3c2` `src/aistudio_api/api/state.py:393-418,451-454,470` | 原生 | unverified | 未验证（无时序用例） | keep | keep |
| D10 Stats/Token/Usage | 原子写 JSON | `4623109` `src/aistudio_api/infrastructure/utils/common.py:13-31`；`1adf3c2` `src/aistudio_api/api/state.py:386` | 原生 | unverified | 未验证（无并发/断电用例） | keep | keep |
| D10 Stats/Token/Usage | usage 主统计与 total 回退 | `4623109` `src/aistudio_api/api/state.py:120-151`；`1adf3c2` `src/aistudio_api/api/state.py:216-245` | 原生 | working | `tests/unit/test_model_stats_v2.py:31-75` | keep | keep |
| D10 Stats/Token/Usage | usage_missing 显式计数 | `1adf3c2` `src/aistudio_api/api/state.py:216-236`；`src/aistudio_api/api/response_models.py:88` | 新增 | partial | `tests/unit/test_model_stats_v2.py:115-123`；UI 仅类型未消费 `web/src/types/stats.ts:25` | port（页面展示） | port |
| D10 Stats/Token/Usage | reasoning_tokens 统计 | `1adf3c2` `src/aistudio_api/api/state.py:80,228-230,243`；`4623109` `src/aistudio_api/infrastructure/gateway/wire_parser.py:365-394` | 新增 | partial | `tests/unit/test_model_stats_v2.py:71`；parser 缺省写 0（`wire_parser.py:391-394`），0/null 不可分 | port（区分 0 与未知） | port |
| D10 Stats/Token/Usage | cache_read_tokens + cache_hit_rate | `1adf3c2` `src/aistudio_api/api/state.py:81,96-100,225-227,244` | 新增 | working | `tests/unit/test_model_stats_v2.py:72-75`（0.3） | keep | keep |
| D10 Stats/Token/Usage | cache_write_tokens | `1adf3c2` `src/aistudio_api/api/state.py:82,231,245`；`4623109` `src/aistudio_api/infrastructure/gateway/wire_parser.py:381-395` 无该键 | 新增 | partial | 未验证真实上游；`web/src/components/stats/StatsOverview.vue:202` 自述无数据 | keep（预留）或 drop | keep |
| D10 Stats/Token/Usage | countTokens 独立计数（count_requests/success/errors/input_tokens） | `af3f55c` `src/aistudio_api/api/state.py:167-177`（初版计入 requests）；`1adf3c2` `src/aistudio_api/api/state.py:84-87,280-295,472-502`；`af3f55c` `src/aistudio_api/application/token_counter.py` | 新增 | working | `tests/unit/test_model_stats_v2.py:83-112`；`tests/unit/test_token_counter.py:302-319` | port | port |
| D10 Stats/Token/Usage | countTokens 不污染生成统计 | `1adf3c2` `src/aistudio_api/application/token_counter.py:274-306`；`src/aistudio_api/api/state.py:472-502` | 新增 | working | `tests/unit/test_model_stats_v2.py:99-112`；`tests/unit/test_token_counter.py:175-213` | keep | keep |
| D10 Stats/Token/Usage | range 解析（today/7d/30d/custom/all + 366 上限） | `1adf3c2` `src/aistudio_api/api/state.py:504-528` | 新增 | working | `tests/unit/test_model_stats_v2.py:169-188`（today/custom）；all/7d/30d 未验证 | keep | keep |
| D10 Stats/Token/Usage | partial / data_start 语义 | `1adf3c2` `src/aistudio_api/api/state.py:535-580`；`src/aistudio_api/api/routes_system.py:53-67` | 新增 | partial | `tests/unit/test_model_stats_v2.py:184-188`；`start` 返回请求值而非生效值（`state.py:572-580`） | port（统一 start/effective） | port |
| D10 Stats/Token/Usage | timeseries（day 桶、缺日 null） | `1adf3c2` `src/aistudio_api/api/routes_system.py:183-219` | 新增 | working | `tests/unit/test_stats_routes.py:68-88` | keep | keep |
| D10 Stats/Token/Usage | paid 标记注入 /api/stats | `1adf3c2` `src/aistudio_api/api/routes_system.py:88-133`；`a10953d`/`1980ada` `src/aistudio_api/infrastructure/gateway/model_discovery.py:526-632` | 新增 | unverified | 未验证（无测试）；发现失败返回空 map（`routes_system.py:124-126`） | port（缓存/降级） | port |
| D10 Stats/Token/Usage | StatsView 范围/自定义/10s 轮询/partial 提示 | `1adf3c2` `web/src/views/StatsView.vue`；`c7bdd80`；`2944c3c` | 新增 | partial | UI 无单测；`StatsView.vue:32-37,130-138,247-257`；rangeOptions 无 all | port（补 all） | port |
| D10 Stats/Token/Usage | StatsView attempt/client 并排展示 | `1adf3c2` `web/src/components/stats/StatsOverview.vue:46-108`；`ModelTokenTable.vue:106-109` | 新增 | partial | `StatsOverview.vue:55,103-107`；429 同时进 attempt 与 client 分母 | port（标注口径） | port |
| D10 Stats/Token/Usage | StatsView 历史字段缺失提示 | `1adf3c2` `web/src/views/StatsView.vue:51-61,252-257` | 新增 | partial | 仅 total_tokens>0 且 4 字段；未覆盖 cache_write/client_rate_limited/aborted/usage_missing | port（补字段） | port |
| D10 Stats/Token/Usage | CountTokensPanel | `1adf3c2` `web/src/components/stats/CountTokensPanel.vue:1-87`；`web/src/views/StatsView.vue:286-289` | 新增 | working | `1adf3c2` 截图 `docs/dev/evidence/wp6-20261020/` | keep | keep |
| D10 Stats/Token/Usage | totals 聚合（跨模型，含 formats/surfaces） | `1adf3c2` `src/aistudio_api/api/routes_system.py:135-168`；`src/aistudio_api/api/state.py:102-147` | 新增 | working | `tests/unit/test_stats_routes.py:64-65` | keep | keep |
| D10 Stats/Token/Usage | Paid 徽标与 “free 使用 Paid 模型” 异常 | `1adf3c2` `web/src/components/stats/AccountHealthPanel.vue:64-80,247-249`；`web/src/views/StatsView.vue:43-49` | 新增 | unverified | 未验证（无 UI 用例） | keep | keep |
| D10 Stats/Token/Usage | 成功率/错误率派生指标 | `1adf3c2` `src/aistudio_api/api/response_models.py:66-95`；`web/src/components/stats/StatsOverview.vue:67-108` | 待判定 | partial | 无 rate 字段；前端是否另算未验证 | port（补派生率）或 drop | port |

### D11 API / UI

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D11 API | Gemini generateContent 非流式端点 | `upstream/master..master`; `src/aistudio_api/api/routes_gemini.py:21-31`; `application/api_service_gemini.py:186-225` | 改造 | working | `tests/unit/test_atg_integration.py:95-284`; `test_gemini_request_normalization.py` | port | port |
| D11 API | Gemini streamGenerateContent SSE | `upstream/master..master` `af3f55c`; `routes_gemini.py:34-41`; `api_service_gemini.py:54-263` | 改造 | working | `tests/unit/test_atg_integration.py:266-307`; `test_api_responses.py:184-225` | port | port |
| D11 API | Gemini countTokens 端点 | `af3f55c`; `routes_gemini.py:44-57`; `application/token_counter.py` | 新增 | working | `tests/unit/test_token_count_routes.py:41-66` | port | port |
| D11 API | Gemini 模型列表 `/v1beta/models` | `upstream/master..master` `47cec70`,`6305377`,`0663cf5`; `routes_models.py:77-103`; `application/model_catalog.py:65-108` | 改造 | working | `tests/unit/test_routes_models.py:15-127`; `test_model_catalog.py` | port | port |
| D11 API | Gemini 单模型元数据 `/v1beta/models/{id}` | `upstream/master..master` `6305377`,`0663cf5`; `routes_models.py:106-122` | 改造 | working | `tests/unit/test_routes_models.py:33-45,115-127` | port | port |
| D11 API | OpenAI Chat Completions `/v1/chat/completions` | `c0412c8`; `routes_openai.py:70-147`; `schemas_openai.py` | 新增 | working | `tests/unit/test_format_openai.py:611-809` | port | port |
| D11 API | OpenAI 模型列表与单项 `/v1/models` | `c0412c8`,`0663cf5`; `routes_openai.py:150-185` | 新增 | working | `tests/unit/test_routes_models.py:87-101`; `test_format_openai.py` | port | port |
| D11 API | Claude Messages `/v1/messages` | `1375646`; `routes_claude.py:73-168`; `schemas_claude.py` | 新增 | working | `tests/unit/test_format_claude.py:723-850` | port | port |
| D11 API | Claude count_tokens `/v1/messages/count_tokens` | `af3f55c`; `routes_claude.py:171-195` | 新增 | working | `tests/unit/test_token_count_routes.py:68-120` | port | port |
| D11 API | OpenAI Responses `/v1/responses` | `2b39b53`; `routes_responses.py:100-178`; `schemas_openai_responses.py` | 新增 | working | `tests/unit/test_format_responses.py:258-287` | port | port |
| D11 API | Responses input_tokens `/v1/responses/input_tokens` | `af3f55c`; `routes_responses.py:62-97` | 新增 | working | `tests/unit/test_token_count_routes.py:122-148` | port | port |
| D11 API | 协议面功能开关 features.formats | `3d01ed2`,`dcb7592`,`2b39b53`; `format_errors.py:69-114`; `defaults/config.yaml:69-74` | 新增 | working | `tests/unit/test_format_error_routing.py:57-106`; `test_format_openai.py:808-815` | port | keep |
| D11 API | 协议感知错误体映射 | `dcb7592`,`2b39b53` `1375646`; `format_errors.py:16-220` | 新增 | working | `tests/unit/test_format_error_routing.py:24-106` | port | port |
| D11 API | 应用级异常到 HTTP 状态映射 | `0cb1b82`; `app.py:293-373` | 改造 | working | `tests/unit/test_app_error_handlers.py:13-33`; `test_format_error_routing.py` | port | port |
| D11 API | API Key 鉴权四种取键方式 | `upstream/master` 原生；`dependencies.py:18-115` 未被本分支改写 | 原生 | working | `tests/unit/test_api_auth.py:24-126`; `test_routes_models.py:101-115` | keep | port |
| D11 API | 本地回环免鉴权 | 无实现；`dependencies.py:69-115` 仅按 Key 判定，未见 127.0.0.1 分支 | 待判定 | broken | `未验证`（全仓检索无 loopback bypass） | drop | drop |
| D11 API | 控制台密码鉴权与 `/auth/check` | `upstream/master..master` `fa1d9d3`; `dependencies.py:47-66`; `app.py:507-512` | 原生 | working | `tests/unit/test_api_auth.py:81-126`; `test_routes_system.py:101-108` | keep | port |
| D11 API | API Key 管理 CRUD `/api-keys` | `upstream/master` 原生；`routes_system.py:570-710` 写回 config.yaml | 原生 | unverified | `未验证`（无 tests/unit 直接覆盖） | port | port |
| D11 API | 健康检查 `/health` | `upstream/master` 原生；`routes_system.py:164-167`；`9de3c8c` 仅容器探针改路径 | 原生 | working | `tests/unit/test_routes_system.py:92-99` | keep | keep |
| D11 API | SPA 导航与静态资源挂载 | `upstream/master..master` `0dd3e3e`,`4e341cf`; `app.py:484-560` | 改造 | working | `tests/unit/test_routes_system.py:110-126`; `test_routes_accounts.py:56-103` | port | port |
| D11 API | 旧轮询切号与路由快照端点 | `upstream/master..master` `28c981f`,`1a3e67a`,`da6ea25`; `routes_system.py:233-382` | 改造 | legacy | `tests/unit/test_routes_system.py:18-41,153-177`（仅 status/clear-cooldown） | drop | drop |
| D11 UI | SPA 外壳、侧栏与路由守卫 | `upstream/master..master` `fd15dbf`,`2944c3c`; `web/src/router/index.ts:10-90`; `components/layout/Sidebar.vue` | 改造 | working | `tests/unit/test_routes_system.py:110-126`; `docs/dev/evidence/wp7-v2-20261020/p1-dashboard-1440.png` | port | port |
| D11 UI | 登录页与控制台密码弹窗 | `upstream/master..master` `fa1d9d3`; `views/LoginView.vue:34-140`; `stores/auth.ts:12-75` | 改造 | working | `tests/unit/test_routes_system.py:101-108`; `test_api_auth.py:81-126` | port | port |
| D11 UI | 控制面板 Dashboard | `fd15dbf`,`2944c3c`; `views/DashboardView.vue:1-135`; `components/dashboard/InstanceOverviewCard.vue` | 改造 | working | `tests/unit/test_routes_instances.py`; 截图 `wp7-v2-20261020/p1-dashboard-1440.png` | port | port |
| D11 UI | 用量统计页 Stats | `1adf3c2`,`c7bdd80`; `views/StatsView.vue:1-300` | 新增 | working | `tests/unit/test_stats_routes.py:21-90`; 截图 `wp6-20261020/stats-desktop-1440.png` | port | port |
| D11 UI | 日志页实时 SSE 流 | `c7bdd80`; `views/LogsView.vue:337-500`; `api/logs.ts:29-53` | 新增 | working | `tests/unit/test_logs_routes.py:44-211`; 截图 `wp7-v2-20261020/p5-logs-1440.png` | port | port |
| D11 UI | 日志按时间导出面板 | `c7bdd80`,`eed30b6`; `LogsView.vue:600-780`; `api/logs.ts:55-90` | 新增 | working | `tests/unit/test_logs_routes.py:212-320` | port | port |
| D11 UI | 账号管理页导入与批量操作 | `7ac7a1a`,`7cd6d74`,`fcd6f1f`,`1282d49`; `views/AccountsView.vue:240-570` | 改造 | working | `tests/unit/test_routes_accounts.py:103-126`; 截图 `wp7-v2-20261020/p2-accounts-1440.png` | port | port |
| D11 UI | 实例切换面板 InstanceSwitchModal | `2944c3c`,`9786b3a`,`1282d49`; `components/accounts/InstanceSwitchModal.vue:36-340` | 新增 | partial | `tests/unit/test_routes_instances.py:120-185`; 截图 `wp7-v2-20261020/p2-switch-modal-390.png` | port | port |
| D11 UI | 账号实例卡片 AccountInstanceCard | `2944c3c`,`a59c885`,`1282d49`,`7cd6d74`,`fcd6f1f`; `AccountInstanceCard.vue:36-300` | 新增 | working | 截图 `wp7-v2-20261020/p2-instance-1440.png`; `test_routes_instances.py` | port | port |
| D11 UI | 实例上限设置卡 max_contexts | `2944c3c`,`9786b3a`; `components/config/InstanceLimitCard.vue:20-66` | 新增 | working | 截图 `wp7-v2-20261020/p3-settings-390-instance-limit.png` | port | port |
| D11 UI | 系统配置页运行态卡片与免密提醒 | `fe37d9b`,`7cd6d74`,`1282d49`; `views/SettingsView.vue:109-210` | 改造 | working | 截图 `wp7-v2-20261020/p3-settings-1440-top.png`; `test_routes_system.py:128-177` | port | port |
| D11 UI | 可视化配置编辑器 visual 与 yaml 双模式 | `fe37d9b`,`2944c3c`,`9786b3a`; `components/config/VisualConfigEditor.vue:49-530` | 改造 | working | 截图 `wp7-v2-20261020/p3-settings-1024-nested.png`; `test_features_config.py:270-315` | port | port |
| D11 UI | 日志与调试配置卡 | `upstream/master` 原生；`components/settings/LogConfigCard.vue:45-175`；后端 `880954f` | 原生 | working | `tests/unit/test_logging.py:197-228`; 截图 `wp7-v2-20261020/p3-settings-1440-top.png` | keep | port |
| D11 UI | API Key 管理卡 | `upstream/master` 原生；`components/settings/ApiKeyManagerCard.vue:141-340` | 原生 | unverified | `未验证`（无 UI 自动化；仅 `routes_system.py:570-710`） | port | port |
| D11 UI | 控制台密码设置弹窗 | `upstream/master` 原生；`components/modals/TokenSettingsModal.vue:51-85` | 原生 | unverified | `未验证`（无 UI 自动化） | keep | port |
| D11 UI | 四协议 API 示例 QuickApiExamples | `fd15dbf`,`2944c3c`; `components/dashboard/QuickApiExamples.vue:98-410` | 改造 | working | 截图 `wp7-v2-20261020/p1-dashboard-1440.png`; `test_format_*.py` | port | port |
| D11 UI | 控制面板模型调用概览表 | `fd15dbf`,`a59c885`; `components/dashboard/ModelStatsTable.vue:32-200` | 改造 | working | `tests/unit/test_stats_routes.py`; 截图 `wp7-v2-20261020/p1-dashboard-1440.png` | port | port |
| D11 UI | paid/free 档位与付费模型展示 | `dad9cf7`,`1adf3c2`,`a59c885`; `AccountInstanceCard.vue:109-111`; `components/stats/ModelTokenTable.vue:96-100` | 新增 | working | `tests/unit/test_stats_routes.py:21-69`; 截图 `wp6-20261020/stats-desktop-1440.png` | port | port |
| D11 UI | 手动切号 UI 入口 | `upstream/master..master` `fa1d9d3`; `stores/system.ts:140-158,188`; `api/system.ts:187-192` | 原生 | legacy | `未验证`（全 web/src 无 .vue 调用 forceNextAccount） | drop | port |
| D11 UI | 账号多模式与渠道矩阵 UI | `7ac7a1a`,`a59c885`; `AccountTable.vue`,`AccountRow.vue`,`AccountChannelMatrix.vue`,`SlotBindPopover.vue`,`ModelQuotaGrid.vue` | 改造 | legacy | `未验证`（AccountTable 未被 AccountsView 引用） | drop | drop |
| D11 UI | Token 计数面板 CountTokensPanel | `1adf3c2`; `components/stats/CountTokensPanel.vue:11-85` | 新增 | working | `tests/unit/test_token_count_routes.py`; 截图 `wp6-20261020/stats-desktop-1440.png` | port | port |

### D12 Tests / Evidence

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|
| D12 Tests/Evidence | `tests/unit` 全量回归（TDD 自证，非原始行为证明） | `4623109..HEAD`；`tests/unit/*.py`（79 文件）、`tests/conftest.py`；最后测试提交 `8e5e0cb` | 改造 | working | 2026-10-24 实测 `uv run pytest tests/unit -q` = 942 passed/35.94s；vs upstream 新增 54、修改 17；只证明“代码符合当前实现” | keep（仅回归） | keep |
| D12 Tests/Evidence | 测试钩子（强制 429，默认关闭） | `b59d6f3`；`tests/unit/test_testing_hooks.py`（2）、`tests/conftest.py` | 新增 | working | 2 例通过；`AISTUDIO_TEST_HOOKS=1` 才启用；用于离线走 429/换号路径 | port | keep |
| D12 Tests/Evidence | 协议/多模态 fixtures | `4623109`；`tests/fixtures/test_input.json`、`test_output.json`、`test_image_input.json`、`test_image_output.json` | 原生 | working | 被 format/wire/multimodal 用例引用；无独立断言 | keep | keep |
| D12 Tests/Evidence | Browser/CDP/Context/Page 单测簇 | `4623109`、`4586271`、`28c981f`、`f110d51`；`test_browser_session.py`（改造）、`test_cdp_client.py`（原生）、`test_cdp_browser_context.py`（3）、`test_browser_engine.py`、`test_session_blue_green_readiness.py`（3）、`test_browser_scripts_syntax.py` | 改造 | working | 942 内通过；全部 mock CDP，无真实浏览器/导航 | keep（单测） | keep |
| D12 Tests/Evidence | Build 面单测（38 例） | `1cc634e` 新增；`8dd6782`、`8e5e0cb` 改；`tests/unit/test_build_surface.py` | 新增 | working | 38 通过；覆盖 holder 解码、`ensure_ready` 单飞、signer/native、BUSY/PAGE_CLOSED/TIMEOUT/NO_FRAMES | keep | keep |
| D12 Tests/Evidence | Build 身份=租约（2 例） | `8e5e0cb`；`tests/unit/test_build_lease_identity.py` | 新增 | working | `:11` lease auth 优先于全局 session；`:31` 无 auth_file fail-closed 503 | keep | keep |
| D12 Tests/Evidence | Build 双并发 E2E（声称 200） | `8e5e0cb` 提交正文；`docs/dev/rebuild/03-context-and-summary.md:11`；`%TEMP%\kilo\commit-msg-build-fix.txt` | 新增 | unverified | 未验证：仅“两条 200、0 Page.navigate/ERR_ABORTED/429”文字；无 raw SSE/日志/时间戳；自述为非流式、Free、临时配置 | keep（作为验收目标） | keep |
| D12 Tests/Evidence | Capacity/租约单测簇 | `79901b0`、`1201a37`、`b59d6f3`、`8e5e0cb`；`test_instance_pool.py`（40）、`test_instance_manager.py`（27）、`test_routing_pipeline.py`（11） | 新增 | working | 942 内通过；断言 `InstanceCapacityError`、`surface_instance_busy`、503、取消/异常释放租约 | keep | keep |
| D12 Tests/Evidence | 旧 capacity 429 证据（已被 `8e5e0cb` 反转） | `b59d6f3`、`8e5e0cb`；`docs/dev/evidence/wp5v3-20261019/raw/phase-concurrent-build-free.json`、`docs/dev/evidence/wp5-20261018/f-quota-try1.out` | 改造 | legacy | 记录两组 429 语义：wp5v3“渠道 'build' 实例预算已满”；wp5“surface_quota_exceeded（配额=0）”；`8e5e0cb` 后统一 503，旧断言不可再作契约 | drop（契约） | keep |
| D12 Tests/Evidence | Paid 模型识别单测 | `a10953d`、`1980ada`；`test_model_discovery_paid.py`（2）、`test_model_access.py`（4）、`test_model_catalog.py` | 新增 | working | 942 内通过；ListModels 标 paid；模型 API 不回传 paid；paid 账号门槛独立 | keep | keep |
| D12 Tests/Evidence | Paid/前端字段逆向证据 | 无 Git 溯源；相关 `a10953d`、`1980ada`；`%TEMP%\kilo\probe_frontend_paid.py`、`analyze_paid_fields.py`、`chunk_paid_context.txt`、`paid_frontend_matches.txt`、`bundles-current/` | 新增 | partial | 真实 bundle/前端字段来源；离线可读；依赖真实账号/PRO 页面 → 不可复跑 | keep（结论）/drop（脚本） | keep |
| D12 Tests/Evidence | Routing 策略单测簇 | `cdb9e15`、`ec35c8d`、`8b6b52e`、`d8bbb22`；`test_routing_eligibility.py`（11）、`test_routing_safety.py`（18）、`test_generation_routing.py`（10）、`test_surface_orchestrator.py`（30）、`test_surface_policy_v2.py`（10） | 新增 | working | 942 内通过；策略组/资格/预算/熔断均有断言 | port（资格+候选）；drop（策略组/打分） | port |
| D12 Tests/Evidence | 单实例候选锁死（无号可切→503） | `ec35c8d`；`src/aistudio_api/application/routing_pipeline.py:251-259`；`tests/unit/test_routing_pipeline.py:212` | 新增 | broken | `02:29`；候选只留已有实例账号；用例只测“invalid 实例可路由”，未覆盖 429 无候选失败 | port（修复后） | port |
| D12 Tests/Evidence | Cookie 单测簇 | `56c1072`（upstream）、`9786b3a`、`7cd6d74`；`test_cookie_import.py`（18）、`test_cookie_persistence.py`（3）、`test_cookie_storage_state.py`（2） | 改造 | working | 942 内通过；跨域双胞胎、OSID、按 (name,domain,path) 合并回写 | keep | keep |
| D12 Tests/Evidence | Cookie/Header 真机探针 | 无 Git 溯源；相关 `9786b3a`、`7cd6d74`；`%TEMP%\kilo\capture_headers.py`、`compare_page_cookies.py`、`send_after_inject.py`、`inject_missing_cookies.py` | 改造 | partial | 真实 header/cookie 行为；含真实凭据片段，只可读不可复制 → 不可复跑 | keep（结论） | keep |
| D12 Tests/Evidence | 日志单测簇 | `880954f`（upstream，原生 `test_logging.py` 9）、`eed30b6`（`test_logs_routes.py` 14、`test_log_archive.py` 4、`test_log_broadcast.py` 9） | 改造 | working | 942 内通过；脱敏/SSE/归档有断言；订阅上限与丢弃无 E2E | keep | keep |
| D12 Tests/Evidence | 统计/Token 单测簇 | `1adf3c2`、`af3f55c`；`test_stats_routes.py`（2）、`test_model_stats_v2.py`（5）、`test_token_counter.py`（8）、`test_token_count_routes.py` | 新增 | working | 942 内通过；两套计数/format/surface 分桶/countTokens | keep | keep |
| D12 Tests/Evidence | Keepalive 单测簇（auto_switch/auth_update/metadata） | `1f9ba61`；`test_account_keepalive.py`（9）、`test_keepalive_config.py`、`test_account_store_keepalive.py` | 新增 | partial | 942 内通过；auto_switch 仅 mock/in-flight 门槛；`docs/dev/evidence/wp5v3-20261019/README.md:200-206` 明示 live 未重跑 | port（手动/显式刷新） | port |
| D12 Tests/Evidence | 自动切号真机证据（旧控制面） | `1f9ba61`；`docs/dev/evidence/wp10-20261019/README.md`；`%TEMP%\kilo\wp10_real_auth.py`、`wp6_switch.py`、`wp6-verify\switch-inflight.sse` | 新增 | legacy | 10-19 手动触发 auto_switch 成功；走旧 active/控制面路径；当前 routing 路径未验；`03:12` 称瘫痪 → 不可复跑 | drop（旧路径） | keep |
| D12 Tests/Evidence | 24h 保活证据 | `1f9ba61`；`src/aistudio_api/application/account_keepalive.py`；`docs/dev/evidence/wp10-20261019/README.md` | 新增 | unverified | 未验证：无 24h 实耗样本；`02:26`/`03:31` 指 build-only 不回写 Cookie；仅配置/单次回写用例 | port（补 24h 测试） | port |
| D12 Tests/Evidence | ATG/工具调用单测簇（93 例） | `792f4e1`；`tests/unit/test_atg_*.py`（7 文件）、`test_function_call_parser.py`、`test_response_parser.py` | 新增 | partial | 942 内通过；覆盖解析/注入/修复；无准确率指标，`03:12` 工具调用未解 | port（回归） | keep |
| D12 Tests/Evidence | 消息超时证据 | `1cc634e`、`8e5e0cb`；`tests/unit/test_build_surface.py:707` | 新增 | unverified | 未验证：仅 Build 流超时单测；无端到端消息超时/重试预算/错误码证据 | port（补 E2E） | port |
| D12 Tests/Evidence | 最终镜像 E2E | `4623109..HEAD`；无 `tests/e2e`；`docs/dev/rebuild/03-context-and-summary.md:10` | 改造 | unverified | 未验证：仅 Docker workflow `39639126777` 构建成功；镜像内未跑真实请求；本地验证非流式/Free | port（补） | port |
| D12 Tests/Evidence | 旧 WP/规划文档（PLAN/SPIKE/AUDIT/WP5-V3/V4/WP7/UI-CHANNEL/HANDOFF/WP6/INSTANCE-SPEC） | `675d958`（PLAN/SPIKE/AUDIT）、`c79f667`（WP5-V3）、`961ebc6`（WP5-V4/WP7-V2）、`50e57c6`（WP7-UI）、`98f3b91`（UI-CHANNEL）、`3ab5974`（两 HANDOFF）、`1adf3c2`（WP6）、`750dbd8`（INSTANCE-SPEC）；`docs/dev/*.md` | 新增 | legacy | `01` 矛盾表 10 项漂移；`05:140-145` 明确不再作方案；仅 SPIKE 结论可复用 | drop（除 SPIKE 结论） | drop |
| D12 Tests/Evidence | `%TEMP%` spike 系列（A/B/C/D/E/M/N/O/P/Q/R） | 无 Git 溯源；相关 `1cc634e`、`8dd6782`；`%TEMP%\kilo\spike*-evidence.json`、`spike*.py` | 新增 | partial | 原始行为证据：页面/bundle/native/signer/内存；依赖真实 CDP/账号 → 不可复跑 | keep（只读结论） | keep |
| D12 Tests/Evidence | Build 直连日志（25 份） | 无 Git 溯源；相关 `1cc634e`、`8dd6782`；`%TEMP%\kilo\direct-run*.log`、`inst-*.log` | 新增 | legacy | Build 失败路径/协议细节；旧 signer 主路径；不可复跑 | keep（失败模式） | keep |
| D12 Tests/Evidence | Bundle 逆向资产 | 无 Git 溯源；相关 `1cc634e`、`a10953d`；`%TEMP%\kilo\bundles-current\`、`chunks\`、`probe_bundle*.py`、`probe-current.cjs` | 新增 | partial | 当前 bundle 函数/字段/resource URL；离线可读；bundle 版本会漂移 | port（离线分析） | port |
| D12 Tests/Evidence | Native 9998/signer 探针 | 无 Git 溯源；相关 `8dd6782`、`8e5e0cb`；`%TEMP%\kilo\probe_signer_live.py`、`spikeQ*.py/json` | 新增 | partial | Q4 native 200/2 frames/3.01s；signer headers/tb/selfTest；真实账号 → 不可复跑 | port（native 结论）；drop（signer 默认阻塞） | port |
| D12 Tests/Evidence | 并发/预算/切号旧探针 | 无 Git 溯源；相关 `1201a37`、`b59d6f3`、`1adf3c2`；`%TEMP%\kilo\wp5_budget_race_probe.py`、`wp5_cancel_probe.py`、`wp5v3_parallel_chat.py`、`wp6_coexist.py`、`wp6-stats.json` | 新增 | legacy | 旧架构：同页 2/4 并发、强制 429 切号、统计；真实账号 → 不可复跑 | keep（旧行为 oracle） | keep |
| D12 Tests/Evidence | 内存采样 | 无 Git 溯源；相关 `79901b0`、`9786b3a`；`%TEMP%\kilo\mem-dual.csv`、`mem-private-2c.csv` | 新增 | partial | 7 个 chrome 进程；private 约 388–481MB；单点采样，非 1GB 压测 | keep（规模基线） | keep |
| D12 Tests/Evidence | mt-audit 合并快照 | 无 Git 溯源；`4623109..HEAD`；`%TEMP%\kilo\mt-audit\*.base / *.ours / *.theirs` | 改造 | working | 12 文件 ours/theirs/base 快照，可复核合并取舍 | keep | keep |
| D12 Tests/Evidence | wp7p2 账号导入夹具 | 无 Git 溯源；相关 `7cd6d74`、`2944c3c`；`%TEMP%\kilo\wp7p2\accounts.json`、`single-auth.json`、`broken.json`、`profiles.json` | 改造 | partial | UI 导入边界夹具；含真实 auth/cookie → 不可复跑、禁止复制 | drop（凭据）/port（脱敏形状） | port |
| D12 Tests/Evidence | 仓库内 evidence 快照 | `72595f4`（p2/p3）、`e008509`（wp5）、`4035d36`/`c79f667`（wp5b）、`b59d6f3`/`7cd6d74`（wp5v3）、`1adf3c2`（wp6）、`a6247e2`（wp7）、`2944c3c`（wp7-v2）、`c210d07`（wp8）、`1f9ba61`（wp10）、`2b39b53`（ui-p1/wp11-responses）、`af3f55c`（wp11-count）、`a59c885`/`9786b3a`（p5-final）；`docs/dev/evidence/**` | 新增 | partial | SSE/日志/截图/harness；多为旧架构；wp5v3 raw 含并发/切换/轮转 | port（原始证据） | keep |
| D12 Tests/Evidence | Ark build-channel 证据 | `66fa05f`；`docs/ark-reference/build-channel/evidence-20261017/*` | 新增 | working | bundle 考古：GenerateContent server-streaming、wire 字段、candidateCount 零命中 | keep | keep |
| D12 Tests/Evidence | 提交信息质量门自证 | `8e5e0cb`（942/双并发）、`9de3c8c`（919，见 `%TEMP%` handoff）、`b59d6f3`（774，见 `docs/dev/evidence/wp5v3-20261019/README.md:114`） | 改造 | unverified | 未验证：942 与本次收集一致；双并发无独立工件；自报不得作 E2E 证据 | port（仅线索） | port |
| D12 Tests/Evidence | `%TEMP%` handoff（20261021） | 无 Git 溯源；HEAD `9de3c8c`；`%TEMP%\kilo\handoff-aistudio-api-20261021-1326.md` | 改造 | partial | 记录 max_contexts=1、cookie fix、919 passed、未提交；含真实账号 ID 与路径 | keep（上下文） | keep |
| D12 | 验收后同步架构插入点、AGENT.md、两份 README、docs/ARCHITECTURE.md，并新增 docs/changelog.md | 用户要求；无 Git 溯源 | 新增 | unverified | 未验证；文档同步流程 | port | port |

## 3. 行格式

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 决定 |
|---|---|---|---|---|---|---|---|

## 4. 批次文件

- `docs/dev/rebuild/ledger-batches/D01-boot-config.md`
- `docs/dev/rebuild/ledger-batches/D02-account-auth-paid.md`
- `docs/dev/rebuild/ledger-batches/D03-browser-context-page.md`
- `docs/dev/rebuild/ledger-batches/D04-instance-pool-lease.md`
- `docs/dev/rebuild/ledger-batches/D05-routing-switch-retry.md`
- `docs/dev/rebuild/ledger-batches/D06-generation-protocol.md`
- `docs/dev/rebuild/ledger-batches/D07-build-surface.md`
- `docs/dev/rebuild/ledger-batches/D08-keepalive-rotation.md`
- `docs/dev/rebuild/ledger-batches/D09-logs-observability.md`
- `docs/dev/rebuild/ledger-batches/D10-stats-usage.md`
- `docs/dev/rebuild/ledger-batches/D11-api-ui.md`
- `docs/dev/rebuild/ledger-batches/D12-tests-evidence.md`

## 5. 合并规则

1. 每行只描述一个可辨认功能，不按 WP 编号。
2. provenance 必须可执行复核：提交 hash 或 `upstream/master..master` 范围 + 文件路径；不允许只写“旧代码”。
3. “原生/改造/新增”必须有 Git 依据；无法判定标 `待判定`，不得猜。
4. 测试/证据缺失标 `未验证`；真实账号/IP 相关证据标 `不可复跑`。
5. 跨 domain 冲突写入对应行，并在合并时单列“冲突/缺口”。
6. 最终合并后必须检查：无真实邮箱、无 auth/cookie/token、无 05 追加、domain 与生命周期一致。

## 6. 冲突/缺口汇总

### D01 Boot & Config

| # | 冲突/缺口 | 证据 | 涉及 domain |
|---|---|---|---|
| 1 | 旧路由/打分层仍由 Boot 装配（rotator 初始化、SurfacePolicyError 导入），与“不移植旧路由/打分层”决策冲突 | src/aistudio_api/api/app.py:57,99,303; src/aistudio_api/application/channel_registry.py:18 | D01, D05 |
| 2 | channels 默认 max_instances/max_pages=2，与“单实例不开两个 page”冲突 | model_defaults.py:577-581; channel_registry.py:42-44; test_features_routing_config.py:29-49 | D01, D04 |
| 3 | lifespan 启动/关闭无测试，warmup/startup/model warmup/keepalive/idle/tier/shutdown 均未验证 | tests/unit 无 lifespan 或 TestClient 用例 | D01, D08, D09 |
| 4 | AISTUDIO_BROWSER_IDLE_TIMEOUT 非数字会在 import 时 int() 抛 ValueError | src/aistudio_api/config.py:276-279 | D01 |
| 5 | accounts_router 同时以 /api 前缀和无前缀注册，路由重叠 | src/aistudio_api/api/app.py:279-285 | D01, D11 |
| 6 | features routing/groups/slots 仍保留，与“反对策略组和打分筛选”决策冲突 | model_defaults.py:619-635,933-979 | D01, D05 |
| 7 | channel_registry 依赖旧 surface_orchestrator.SurfacePolicyError，删旧路由前需解耦 | src/aistudio_api/application/channel_registry.py:18 | D01, D05 |
| 8 | channels 同时维护 max_instances 与 max_pages 语义，legacy max_pages 迁移已被移除 | model_defaults.py:827-828,1133-1171; 7cd6d74 | D01, D04 |

### D02 Account / Auth / Paid

| 冲突/缺口 | 涉及行 | 说明/证据 |
|---|---|---|
| OAuth 刷新链路缺失 | OAuth 行 | 全仓 `.py` 无 oauth/refresh_token；仅 cookie 会话过期与 24h 元数据刷新，若要求 OAuth 过期语义需新设计 |
| paid 不从模型 API 暴露 | ListModels / paid map 行 | test_model_discovery_paid.py:44-67 断言 `/v1beta/models` 不含 `paid`/`access_tier`；仅 /api/stats 有 paid |
| paid map 仅覆盖已统计模型且实际值未测 | paid map 行 | routes_system.py:119-132 以 discovery 建模名映射；test_stats_routes.py 只 stub，无真实 paid 断言 |
| 默认 paid 种子双份且不完全一致 | Paid 默认种子行 | model_access.py:11-20 与 model_discovery.py:93-189 各自维护（如 gemini-3.1-pro-preview 为 False、argon 系为 True），冷启动覆盖顺序隐式 |
| free 默认组仅 build，而 paid 模型仅 chat | Paid 仅 chat / free-pro 行 | surface_orchestrator.py:35-41 与 :394-396 组合后 free 账号默认不可用 paid 模型（可能是有意，需确认） |
| 24h 调度跨 domain | expired / 24h 行 | D02 只落 expired 状态与刷新器；定时器在 account_keepalive.py（D08），合并时需核对边界 |
| 重复账号只标记不合并 | 重复账号行 | refresh_duplicate_flags 只写 duplicate_of，删除依赖用户/批量删除 |

### D03 Browser / CDP / Context / Page

| 冲突/缺口 | 说明 | 证据 |
|---|---|---|
| 两套 page 生命周期模型 | 控制面 BrowserSession 仍用默认 browser context 的 tab（connect_page/create_new_page）；请求池用隔离 BrowserContext。蓝绿只切换控制面 tab，与池内 context 无关 | session.py:251-258; cdp_client.py:871-939; instance_pool.py:126-140 |
| `disposeOnDetach=True` 真实语义未验证 | 只断言了参数；浏览器级共享 `_browser_conn` 断开时是否连带释放所有 context、与 pool 显式 dispose 的交互无真实 CDP 证据 | cdp_client.py:941-981; test_cdp_browser_context.py:28-44; 未验证 |
| 隔离 context 内清 cookie 的作用域未验证 | `load_cookies_to_page` 每页执行 `Network.clearBrowserCookies`/`clearBrowserCache`；在多个 BrowserContext 并存时是否只影响当前 context 无真实证据 | session_auth.py:130-140; instance_pool.py:986-1003; 未验证 |
| 一 context 多 page vs 单实例单页 | max_contexts>=2 时同账号可在 1 个 context 放 chat+build 两页（耗 2 页预算）；max_contexts=1 时第二页被拒，chat/build 互斥 | instance_pool.py:333-335,546-606; test_instance_pool.py:811-832,938-1009 |
| BotGuard 登录重注入为 fork 分叉 | upstream 4623109 删除了捕获期重注入直接抛 SessionExpired；HEAD 恢复“重注入后继续”，行为与上游基线不同 | botguard_bootstrap.py:252-262; 4623109 diff |
| `create_page_in_context` 无单测 | target 出现在 /json/list 的轮询、失败关 target、WS 缺失分支均无覆盖 | cdp_client.py:1002-1036; 未验证 |
| realm 缓存淘汰无直接断言 | 仅验证 on_slot_retired 回调；client 三个 realm dict 的 pop 行为无测试 | client.py:97-100; test_instance_pool.py:562-574 |
| 真实 CDP/账号不可复跑 | 所有结论来自代码与 mock 单测；未启动真实 Chromium、未访问 AI Studio | 全局约束；未验证 |
| 与 D04 行重叠 | pool 预算/lease/slot 状态归 D04；本文件只保留 BrowserContext/Page 资源创建、关闭、隔离语义，合并时去重 | D04-instance-pool-lease.md |

### D04 Instance / Pool / Lease / Capacity

| 冲突/缺口 | 说明 | 证据 |
|---|---|---|
| 一 Context 一 Page vs 同账号双页 | 同账号 chat+build 共用一个 Context 且并存两页，直接偏离目标 | instance_pool.py:642-687; test_instance_pool.py:204-226,811-832 |
| max_contexts 语义重叠 | 同时是实例记录上限、Context 上限、全局 page 预算 | instance_manager.py:809-810; instance_pool.py:202-211,333-335 |
| 实例数≠Context 数≠Page 数 | startup 同账号双 channel 产生 2 条记录、1 个 Context；容量判定混用 | instance_manager.py:905-919; test_instance_manager.py:316-333 |
| 单实例 failover 受限 | max_contexts=1 时页预算=1，chat/build 互斥；跨账号只能 defer 或等释放 | instance_pool.py:333-335,490-502; test_instance_pool.py:938-1009 |
| 死状态/死 API | COOLDOWN 无赋值；retire_idle 无 src 调用；_runtime_occupancy 无引用 | instance_pool.py:36-42,1180-1205; instance_manager.py:912-919 |
| 在途检查非原子 | manager 先读 in_flight 再调用 pool close/rebind，中间存在竞态窗口 | instance_manager.py:303-306,367-376,480-483; instance_pool.py:698-699,745-746 |
| reassign sibling 守卫无测试 | 多 channel 实例拒绝迁移分支缺少回归证据 | instance_manager.py:474-477; test_instance_manager.py:402-421 |
| build-only bootstrap 页占预算 | 装载期 chat bootstrap 页短暂计入 chat 页预算 | instance_pool.py:1028-1038; test_instance_pool.py:270-303 |

### D05 Routing / Candidate / Switch / Retry

| 冲突/缺口 | 说明 | 涉及 |
|---|---|---|
| retry_same 从未置真 | 204/404/hang 的"同账号重试"实际释放 lease 后调用 `next_candidate` 换号；测试替身自行置 True，回归被掩盖 | routing_pipeline.py:514-522; generation_service.py:305,445,580,716; test_generation_routing.py:64-67 |
| quota pool 收窄失效 | 组 `allowed_quota_pools` 只存不判；账号 `quota_pools` 只在 `effective_account_pools` 返回、无消费者；渠道 `quota_pool` 被折算为 tier tag | routing_eligibility.py:95-121,197-207; surface_orchestrator.py:267-276; account_routing.py:146-158 |
| 熔断为死代码 | `record_switch_failure` 无调用点，`is_open()` 恒 False；文档称 process-global，实际每 pipeline 一个实例 | routing_safety.py:76-164; routing_pipeline.py:149,548-555 |
| 单实例 failover | pinned slot 不参与 rebind；build 单页 busy 只 defer 不换号，与"来消息即唤醒实例/换号重试"的旧需求冲突 | instance_pool.py:862-917; generation_service.py:213-227 |
| 回退路径 surface 不一致 | 非流式回退硬编码 `chat`，流式回退走 `_resolve_surface`（覆盖/分组），同一模型两条路径可落到不同面 | generation_service.py:265 vs 516-520; api_service_gemini.py:_resolve_surface |
| 测试缺口 | 无 `test_routing_selection.py`、无 `test_upstream_errors*.py`；排序/错误分类仅间接覆盖；`test_routing_pipeline` 不覆盖 `record_retry` | tests/unit/ |
| defer 不可配 | `max_defers=2`、`asyncio.sleep(1.0)` 硬编码，`features.routing` 只下发 max_switches | routing_safety.py:27; generation_service.py:225,244 |

### D06 Generation / Protocol / Wire

| # | 冲突/缺口 | 证据 | 影响 |
|---|---|---|---|
| 1 | Gemini 流式错误恒为 HTTP 200，错误只进 SSE body；Claude／OpenAI／Responses 仅“首事件前”可返回真实状态 | `api_service_gemini.py:228-263`（StreamingResponse 先建）；`routes_claude.py:130-144`；`generation_service.py:589-593` 首事件是 routing_wait | 同一上游 429 在不同协议面状态码不一致；Gemini 侧客户端无法靠状态码重试 |
| 2 | HTTP 200 已出后错误只能塞 body；已出正文后 `has_yielded_data` 禁止切号，但 thought_signature 等元数据不计入 | `upstream_errors.py:195-343`；`generation_service.py:694-726,836-849`；`test_api_responses.py:224-271` | 正文中途 429／断流不可换号；仅元数据阶段仍可换号 |
| 3 | countTokens 把 system／tools／conversation 拼成单条 user 文本再计数，非原生结构化计数 | `token_counter.py:83-148`；`test_token_counter.py:36-142` | 与上游真实 structured count 存在偏差，客户端计费/配额预估可能不准 |
| 4 | build 面走 REST body 分支，chat 面走 capture／replay JSPB 模板；两套请求形态并存 | `generation_service.py:296-300,553-563`；`client.py:581-600,691-710` | 同一 ATG／normalize 结果未必在两面等价；build body 无单测 |
| 5 | build 流式首帧后错误只能进事件流；D06 只覆盖分支，不覆盖 BuildSurface 内部重试 | `generation_service.py:618-625`；`client.py:596-600`；`test_build_surface.py` 属 D07 | 200 已出后 build 错误无法用状态码表达，需 D07 共同定边界 |
| 6 | ATG side_buffer 默认 0，late synthetic call 到达时已 flush 的正文无法收回，只标 conflict | `atg/stream_extractor.py:12-18,86-94,121-126`；`atg/config.py:100` | prefer 模式下可能同时输出前言与合成答案 |
| 7 | ATG 空响应判定只看 text／thinking／function_calls，忽略 images／code_output | `generation_service.py:103-108` | 纯图片／纯代码结果会被误判为空并触发重发；ATG 仅文本模型时影响有限 |
| 8 | 非流式 Gemini 纯文本 part 不回传 candidate.thought_signature；仅 functionCall／图片带签名 | `api/responses.py:80-91,108-109`；`api_service_gemini.py:205-225` | 文本多轮 thinking 签名可能丢失，影响后续轮次一致性 |
| 9 | Gemini SSE 终块 usage／finish／responseId 无独立断言；response_id 在各协议渲染处也未断言 | `api_service_gemini.py:114-144`；`test_format_openai.py:459` 仅构造 | 终块字段回归不可检测 |
| 10 | 未验证项：capture 鉴权头分支、raw dump、chat 流式网关聚合、真实 CountTokens RPC、build REST body | `transport.py:69-116`；`client.py:426-451,498-555`；`streaming.py:69-155` | 重建时需补集成证据，不能仅依赖单测 |

### D07 Build Surface / 9998 / Signer

| 编号 | 冲突/缺口 | 影响 | 证据 |
|---|---|---|---|
| G1 | build 身份只取 lease，但执行期 `self._session.request_scope()` 记的是控制面全局 session/`active_context`；页面却建在 lease adapter 的 context（CDP client 也取自 `self._session`） | 跨账号计数与 keepalive busy 判定可能失真；租约身份与控制面身份不一致 | `client.py:158-177`, `:589`, `:699`; `session.py:146-160` |
| G2 | `_count_live_page_instances` 只数全局 chat 页 + 全局 build 页，不数 pool slot 页 | pool 模式配额可能低估；client 级 surface 与 slot surface 可能各建一页 | `client.py:250-263`, `:343-344`; `instance_pool.py:126-140` |
| G3 | 无 realm 时 `_connections()` 回退到模块级 `_APP_WS_CONNS`，且 realm 连接同时写入全局列表 | 非 realm surface 可能认领其他 realm 的 9998 连接 | `build_surface.py:150-162`, `:816-817` |
| G4 | `_recover_warmup` 在 signer 脚本缺失时仍下发 warmup（无页面吞没器） | 可能产生真实上游请求，违反 signer “零上游流量”约定 | `build_surface.py:1288-1298`; `build_signer.js:1119-1160` |
| G5 | watchdog 仅 `_realm_id` 存在时启动，且任务无取消出口；`aborted` 事件分支无任何产生方 | 非 realm 实例断线只能靠请求触发恢复；abort 路径形同虚设 | `build_surface.py:1314-1321`, `:1798-1806`; `:1844-1854` |
| G6 | `abort_stream` 在 `src` 内无调用方；HTTP 断开不联动 build 任务 | 客户端断开后生成继续占用 build 页与配额 | `build_surface.py:1844-1854`; `transport.py:130-137` |
| G7 | 复用条件依赖 `last_error` 为空，但非 strict 探测失败只写 `signer_error`；`last_error` 仅 strict 路径设置 | 复用门槛语义分裂，signer 失败后页面仍被判可复用 | `build_surface.py:1016-1023`, `:1506-1522`, `:1525-1541` |
| G8 | human gate / 过场点击 / focus 仿真 / watchdog / 原地恢复 / sink 本体均无单测，仅有不可复跑实机日志 | 重建后无回归护栏，只能靠真实账号验收 | `test_build_surface.py:221-233`; `%TEMP%\kilo\p3-build3`、`wp5-verify`、`wp6-verify`、`spikeP/Q*-evidence.json` |
| G9 | 默认 app id 为硬编码外部应用，`da6ea25` 换过一次 | 重建时来源/授权/可用性需用户确认 | `build_surface.py:69`; `da6ea25` |

### D08 Keepalive / Rotation / 24h

| # | 冲突/缺口 | 证据 | 影响 |
|---|---|---|---|
| 1 | auto switch 不尊重 `pinned` | `instance_switch.py:81-87,186-194` 未检查；`instance_pool.py:76,866` pinned 仅影响请求分配 | 用户显式实例会被后台 rebalance 改绑 |
| 2 | auth update 不覆盖 build-only 实例 | `account_keepalive.py:320-323` 仅 `chat_ready()`；`client.py:192` 同 | build-only 账号 cookie 可能长期不刷新 |
| 3 | auto switch 仍按 group/channel 资格打分 | `instance_switch.py:79-136` 使用 `eligible_accounts`/groups | 与“按模型 paid/free tier 映射、拒绝策略组打分”的既有决策冲突 |
| 4 | metadata 失败无短周期重试 | `account_keepalive.py:212-217` 仅 auth_update/no_active_page 重试 | 一次 5xx/地域失败要等 24h |
| 5 | chat_ready 槽位被无条件标记已回写 | `account_keepalive.py:318-325`；`client.py:194-196` | 回写失败时 `auth_saved_at` 仍前进 |
| 6 | 调度任务遇 busy 仍推进 next_due | `account_keepalive.py:206-217`，`run_now` busy 返回后照常赋值 | 手动任务可把定时任务推迟一个完整周期 |
| 7 | active_activity 只操作控制面 page 且默认关 | `account_keepalive.py:415-424`；`model_defaults.py:596-597` | 实例化架构下基本无效 |
| 8 | idle 回收默认禁用 | `86391c8 config.py:276-294` | “idle 回收”实际只在显式配置时生效 |

### D09 Logs / Observability

| 项 | 说明 | 证据 | 影响 |
|---|---|---|---|
| dump 文件泄露敏感信息 | `req_*.json` 同时写原始 `headers`/`raw_body`，与总线脱敏口径不一致 | `logger.py:421-444`（尤其 `:428`、`:433`） | D09 安全、D02 cookie |
| dump 默认目录可疑 | `parents[3]/dumps` 解析为 `src/dumps`，非 data 目录；UI 默认值 `dumps` | `logger.py:154`；`LogConfigCard.vue:32` | D09、D01 配置 |
| 请求日志双份 | middleware access log 与 `uvicorn.access` 输出同类行；dump 开启时 access 行被 dump 取代 | `app.py:289-295`、`:438-478`；`logger.py:260-261` | D09、D11 |
| gap 字段不一致 | 后端 `{"last_id","oldest_id"}`，前端读 `missed/dropped/gap` | `routes_logs.py:63-65`；`LogsView.vue:324-329` | D09 |
| 全量 DEBUG 无降噪 | root DEBUG 使三方库/CDP 调试行进入 ring，仅容量淘汰兜底 | `logger.py:245-258`；`log_broadcast.py:194-201` | D09、D03 |
| 单日导出缺档 | 单日不存在仍返回 200 空 `.log`，多日才 404 | `routes_logs.py:244-253` | D09、D11 |
| 日志级别跨域 | 运行期改级别入口在 `routes_system.py`，UI 在 Settings 卡片 | `routes_system.py:494-549`；`LogConfigCard.vue` | D09、D01、D11 |

### D10 Stats / Token / Usage

| # | 冲突/缺口 | 证据 | 影响 |
|---|---|---|---|
| 1 | attempt 与 client 两套口径混排在同一卡片 | `web/src/components/stats/StatsOverview.vue:46-108` | 两个分母并列；429 同时进 attempt.rate_limited 与 client_errors |
| 2 | client 的 surface 分桶只在非流式成功路径写入 | `src/aistudio_api/application/generation_service.py:488-495,497-502,837-850` | surfaces[*].client_requests 系统性偏低 |
| 3 | attempt 错误分母不全 | `src/aistudio_api/application/upstream_errors.py:186-245,290-360` | 鉴权/4xx/204/404/hang 不入 errors，页面“错误”偏低 |
| 4 | v1 历史把 countTokens 计入 requests | `af3f55c` `src/aistudio_api/api/state.py:167-177` | 已持久化数据无法与新 count_* 拆分 |
| 5 | range.start 是请求值，统计取生效值；all 的 start 取 daily 起点 | `src/aistudio_api/api/state.py:535-580` | 标签与数据不一致；90 天裁剪后 all-time 起点误导 |
| 6 | usage 字段语义不齐 | `src/aistudio_api/infrastructure/gateway/wire_parser.py:391-394`；`src/aistudio_api/api/state.py:231` | reasoning 缺省 0 而非 null；cache_write 恒 null |
| 7 | countTokens 失败路径不传 surface | `src/aistudio_api/application/token_counter.py:290-297` | surfaces 的 count_requests 只含成功 |
| 8 | paid 依赖模型发现结果 | `src/aistudio_api/api/routes_system.py:119-133` | 发现失败时 paid=null，UI 异常检测漏报 |

### D11 API / UI

| 冲突/缺口 | 证据 | 影响 | 决定 |
|---|---|---|---|
| 单实例与双渠道 UI 语义冲突 | `InstanceSwitchModal.vue:354-392` 明示实例=账号+渠道+激活态；默认 `routing.max_contexts: 1`（`defaults/config.yaml:119`） | 单实例下仍渲染 PG/Build 两个渠道选择，与“单实例不开两 page”口径冲突 | 是否在 max_contexts=1 时禁用跨渠道按钮 |
| 手动切号 UI 断链 | `stores/system.ts:140` 定义 forceNextAccount，无任何 `.vue` 引用；后端 `/rotation/next` 存在（`routes_system.py:359-382`） | 用户无法从界面手动切号，旧能力实际不可达 | 重建时补按钮还是删除端点 |
| 孤儿 UI 组件未清理 | 无引用：`AccountTable` 链、`AccountModeBanner`、`ActiveContextsCard`、`AvailableAccountsCard`、`InstanceDetailsPanel`、`InstanceSummaryCard` | 旧槽位/渠道策略代码继续占包体，易被误读为现行 UI | 是否在重建时物理删除 |
| 模型列表前缀与 paid 过滤耦合 | `model_catalog.py:65-108` 对 build 渠道跳过 paid 与 chat-only 模型；`0663cf5` 同时返回裸 ID | API/UI 看到的模型集合依赖 features 与 paid 标记，文档易误读 | 是否把裸 ID 作为唯一稳定契约 |
| 无 Key 即免密且无回环保护 | `dependencies.py:78-80` 无任何 Key 直接放行；无 127.0.0.1 分支 | 误配空 api_keys 时对外全开；也无法仅对本地免密 | 是否要求默认拒绝并显式允许 loopback |
| error 状态与上游语义偏移 | `format_errors.py:134-154` 与 `app.py:303-334` 将策略拒绝映射 403/429/503 | 客户端按原生 429/403 重试可能失效 | 是否保持现映射并写客户端契约 |
| 格式面默认全开与渠道默认关闭 | `defaults/config.yaml:69-73` openai/claude/responses=true；`:94-99` build.enabled=false | 用户看到四协议示例但 build 模型不可用 | 是否按渠道实际能力生成示例与开关 |
| 文档 05 与 UI 现行口径不一致 | 05 记录 `InstanceLimitCard.vue:46` 为现行；当前 `AccountsView` 已改用 `AccountInstanceCard` | 以 05 为依据会复刻已弃用 UI | 是否以 D11 本表覆盖 05 的 UI 结论 |

### D12 Tests / Evidence

| 缺口 | 现状 | 影响 |
|---|---|---|
| 最终镜像 E2E | 未验证：无 `tests/e2e`；仅 CI 构建成功（`03:10`） | 无法证明镜像内路由/实例/流式 |
| Build 双并发 E2E | 未验证：仅 `8e5e0cb` 正文 + `03:11`；无 raw | 200/0 导航不可复核 |
| 自动切号 | 未验证：当前 routing 路径无 live；旧控制面 live 成功（wp10）；`03:12` 称瘫痪 | 换号可靠性未证 |
| 24h 保活 | 未验证：无 24h 实耗；build-only 不回写（`02:26`） | 24h 后静默失效 |
| 工具调用准确率 | 未验证：仅 93 例解析/注入单测 | 无准确率基线 |
| 消息超时 | 未验证：仅 `test_build_surface.py:707` | 端到端超时/重试未定 |
| 测试固化旧语义 | capacity 用例固化 503/旧实例模型；旧 429 证据在 wp5v3 raw | rebuild 不能直接复用全部用例 |

## 7. 合并检查

- 合并时间：2026-10-24；最后更新：2026-10-25
- 总功能行：518
- Domain 数：12
- 8 列完整性：通过（合并脚本逐行校验）
- 末列：P1-P10 全部已填；与 `06-feature-index.csv` 一致
- 含 `未验证` 的行：97
- `原生/改造/新增` 为 `待判定` 的行：4
- 证据含 `不可复跑` 的行：16
- 行数分布：D01 62、D02 31、D03 35、D04 62、D05 43、D06 70、D07 44、D08 25、D09 35、D10 32、D11 42、D12 37
- 代码改动：无；未提交；未合并 master。