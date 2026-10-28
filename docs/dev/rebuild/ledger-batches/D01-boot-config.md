# D01 Boot & Config 功能台账

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D01 Boot & Config | FastAPI app 单例与 lifespan 挂载 | upstream/master..master + src/aistudio_api/api/app.py:54-270 | 原生 | partial | test_routes_system.py / test_format_error_routing.py 导入并请求 app；lifespan 未直接测试 | keep | 待用户决定 |
| D01 Boot & Config | 进程退出清理钩子 | upstream/master..master + src/aistudio_api/api/app.py:64-68 | 原生 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | AIStudioClient 启动初始化 | upstream/master..master + src/aistudio_api/api/app.py:70-73 | 原生 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | AccountStore / AccountService 初始化 | upstream/master..master + src/aistudio_api/api/app.py:76-78 | 原生 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | InstanceManager 初始化与账号切换回调 | 79901b0 + src/aistudio_api/api/app.py:80-96 | 新增 | partial | test_instance_manager.py:300-344 覆盖 manager.startup；app 装配未验证 | port | 待用户决定 |
| D01 Boot & Config | 黏性 rotator 初始化 init_rotator | upstream/master..master + src/aistudio_api/api/app.py:98-100 | 原生 | legacy | test_runtime_persistence.py:46-91 覆盖 rotator 持久化；app 装配未验证 | drop | 待用户决定 |
| D01 Boot & Config | AccountInfoRefresher 初始化 | 1f9ba61 + src/aistudio_api/api/app.py:102-103 | 新增 | partial | test_account_info_refresh.py；app 装配未验证 | port | 待用户决定 |
| D01 Boot & Config | 启动浏览器预热（优先激活 active 账号，3 次重试） | 1980ada + src/aistudio_api/api/app.py:111-133 | 改造 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | 实例预启动 task（warmup 后 manager.startup） | da6ea25 + src/aistudio_api/api/app.py:135-149 | 新增 | partial | test_instance_manager.py:300-344 覆盖 startup；app task 未验证 | port | 待用户决定 |
| D01 Boot & Config | 模型发现周期预热 task | 1980ada + src/aistudio_api/api/app.py:151-163 | 新增 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | Keepalive 服务启动 | 1f9ba61 + src/aistudio_api/api/app.py:165-173 | 新增 | partial | test_account_keepalive.py；app 装配未验证 | port | 待用户决定 |
| D01 Boot & Config | 浏览器空闲回收监控与活跃工作保护 | 86391c8 + src/aistudio_api/api/app.py:175-195 | 改造 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | 启动时账号 email/tier 自动校验 | 47cec70 + src/aistudio_api/api/app.py:197-241 | 改造 | unverified | 未验证 | port | 待用户决定 |
| D01 Boot & Config | 关闭流程：取消后台任务、停 keepalive、client.close、清空 runtime_state | da6ea25, 1980ada, 1f9ba61 + src/aistudio_api/api/app.py:243-267 | 改造 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | 基础路由注册（system/accounts/gemini/models） | upstream/master..master + src/aistudio_api/api/app.py:270-290 | 原生 | working | test_routes_system.py, test_routes_models.py, test_format_error_routing.py | keep | 待用户决定 |
| D01 Boot & Config | 管理面路由注册（logs/keepalive/instances + web auth） | eed30b6, 1f9ba61, 79901b0 + src/aistudio_api/api/app.py:273-284 | 新增 | partial | test_logs_routes.py, test_routes_keepalive.py, test_routes_instances.py | port | 待用户决定 |
| D01 Boot & Config | 协议面路由注册（openai/responses/claude + api key） | c0412c8, 1375646, 2b39b53 + src/aistudio_api/api/app.py:288-290 | 新增 | working | test_format_openai.py, test_format_claude.py, test_format_responses.py, test_format_error_routing.py | port | 待用户决定 |
| D01 Boot & Config | InstanceError 异常处理 | 79901b0 + src/aistudio_api/api/app.py:293-300 | 新增 | partial | test_routes_instances.py 间接覆盖；无 handler 专项测试 | port | 待用户决定 |
| D01 Boot & Config | SurfacePolicyError 映射客户端 4xx | 0cb1b82 + src/aistudio_api/api/app.py:303-308 | 新增 | working | test_app_error_handlers.py:11-21 | port | 待用户决定 |
| D01 Boot & Config | BuildSurfaceError 映射 503/502 | 0cb1b82 + src/aistudio_api/api/app.py:311-334 | 新增 | working | test_app_error_handlers.py:24-28 | port | 待用户决定 |
| D01 Boot & Config | HTTPException 按 API 格式分派错误体 | c0412c8, dcb7592 + src/aistudio_api/api/app.py:337-353 | 改造 | working | test_format_error_routing.py:27-57 | keep | 待用户决定 |
| D01 Boot & Config | RequestValidationError 按 API 格式分派错误体 | c0412c8 + src/aistudio_api/api/app.py:356-373 | 改造 | working | test_format_error_routing.py:85-106 | keep | 待用户决定 |
| D01 Boot & Config | HTTP 请求生命周期日志与 dump 中间件 | upstream/master..master + src/aistudio_api/api/app.py:376-480 | 原生 | working | test_logging.py:233-245 | keep | 待用户决定 |
| D01 Boot & Config | 静态资源 no-cache 挂载类 | a59c885 + src/aistudio_api/api/app.py:484-504 | 新增 | partial | test_routes_system.py:110-124 覆盖 SPA 头；static 资源未验证 | keep | 待用户决定 |
| D01 Boot & Config | SPA 导航中间件与 Vary/Cache-Control | a59c885, eed30b6, c0412c8 + src/aistudio_api/api/app.py:515-566 | 改造 | working | test_routes_system.py:109-124 | keep | 待用户决定 |
| D01 Boot & Config | /auth/check 认证状态端点 | upstream/master..master + src/aistudio_api/api/app.py:507-512 | 原生 | working | test_routes_system.py:100-106 | keep | 待用户决定 |
| D01 Boot & Config | 根路径 / 前端兜底 | upstream/master..master + src/aistudio_api/api/app.py:569-579 | 原生 | working | test_routes_system.py:109-124 | keep | 待用户决定 |
| D01 Boot & Config | CLI main 与 --port / --browser-port | upstream/master..master + src/aistudio_api/api/app.py:582-595 | 原生 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | .env 加载与默认文本/图片模型常量 | upstream/master..master + src/aistudio_api/config.py:15-22 | 原生 | working | config 模块被全部单测导入；专项未验证 | keep | 待用户决定 |
| D01 Boot & Config | 布尔/整数环境变量解析与容错 | 3d01ed2, dceaa3d + src/aistudio_api/config.py:33-47 | 改造 | working | test_features_config.py:39-79 | keep | 待用户决定 |
| D01 Boot & Config | 浮点/枚举环境变量解析（钳制与回退） | 3d01ed2, 1f9ba61 + src/aistudio_api/config.py:50-70 | 新增 | working | test_features_config.py:52-84 | keep | 待用户决定 |
| D01 Boot & Config | 配置文件路径解析与模板初始化 | upstream/master..master + src/aistudio_api/config.py:105-130 | 原生 | working | test_runtime_persistence.py:94-122 | keep | 待用户决定 |
| D01 Boot & Config | data/stats/rotator 路径解析 | upstream/master..master + src/aistudio_api/config.py:133-171 | 原生 | working | test_runtime_persistence.py:13-91 | keep | 待用户决定 |
| D01 Boot & Config | 模型缓存文件路径解析 | 1980ada + src/aistudio_api/config.py:154-161 | 新增 | partial | test_model_discovery_paid.py 间接；路径解析专项未验证 | port | 待用户决定 |
| D01 Boot & Config | auth 文件发现（registry 活跃账号优先 + 扫描 + utf-8） | dceaa3d + src/aistudio_api/config.py:174-203 | 改造 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | 代理探测与浏览器 proxy 构造 | upstream/master..master + src/aistudio_api/config.py:206-247 | 原生 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | 时区与 locale 解析 | upstream/master..master + src/aistudio_api/config.py:250-273 | 原生 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | 浏览器空闲超时默认值改为 0 | 86391c8 + src/aistudio_api/config.py:276-283 | 改造 | unverified | 未验证 | keep | 待用户决定 |
| D01 Boot & Config | Settings 基础字段（端口/浏览器/鉴权/超时/统计） | upstream/master..master + src/aistudio_api/config.py:286-323 | 原生 | working | config 导入与 test_features_config.py:87-93 | keep | 待用户决定 |
| D01 Boot & Config | Settings 功能开关基线（WP1/WP10 环境变量） | 3d01ed2, 1f9ba61 + src/aistudio_api/config.py:324-359 | 新增 | working | test_features_config.py:87-93 | port | 待用户决定 |
| D01 Boot & Config | Settings aistudio_app_url | da6ea25 + src/aistudio_api/config.py:298 | 新增 | unverified | 未验证 | port | 待用户决定 |
| D01 Boot & Config | 模型默认值 YAML profiles/overrides/safety 解析与缓存 | upstream/master..master + src/aistudio_api/infrastructure/gateway/model_defaults.py:34-422 | 原生 | working | test_model_defaults.py:5-196 | keep | 待用户决定 |
| D01 Boot & Config | config.yaml API key 解析与 mtime 缓存 | upstream/master..master + src/aistudio_api/infrastructure/gateway/model_defaults.py:425-483 | 原生 | working | test_format_error_routing.py 经 app 鉴权链间接覆盖 | keep | 待用户决定 |
| D01 Boot & Config | logging 配置读取与写回 | upstream/master..master + src/aistudio_api/infrastructure/gateway/model_defaults.py:486-541 | 原生 | working | test_logging.py:181-230 | keep | 待用户决定 |
| D01 Boot & Config | features 默认值、env 基线、YAML 深合并与归一化 | 3d01ed2 + src/aistudio_api/infrastructure/gateway/model_defaults.py:583-661,1022-1130,1174-1198 | 新增 | working | test_features_config.py:99-228 | port | 待用户决定 |
| D01 Boot & Config | features 热更新原子写回 | 1f9ba61 + src/aistudio_api/infrastructure/gateway/model_defaults.py:1200-1233 | 新增 | working | test_keepalive_config.py:14-39, test_features_config.py:286-323 | port | 待用户决定 |
| D01 Boot & Config | channels 配置归一化（别名/build legacy alias/allowed_tags） | cdb9e15, 7cd6d74 + src/aistudio_api/infrastructure/gateway/model_defaults.py:764-858 | 新增 | working | test_features_routing_config.py:24-123 | port | 待用户决定 |
| D01 Boot & Config | routing 预算归一化（max_switches/global_max_pages/max_contexts/borrow/prewarm/circuit_breaker） | 8b6b52e, dd5e359 + src/aistudio_api/infrastructure/gateway/model_defaults.py:861-896 | 新增 | working | test_features_routing_config.py:126-154 | port | 待用户决定 |
| D01 Boot & Config | slots 配置归一化、坏项丢弃与去重 | cdb9e15 + src/aistudio_api/infrastructure/gateway/model_defaults.py:899-930 | 新增 | working | test_features_routing_config.py:157-181 | port | 待用户决定 |
| D01 Boot & Config | groups 配置归一化与 groups_enabled | d8bbb22, 7cd6d74 + src/aistudio_api/infrastructure/gateway/model_defaults.py:933-979,1044 | 新增 | working | test_features_groups.py, test_features_routing_config.py:184-199 | drop | 待用户决定 |
| D01 Boot & Config | ATG 配置归一化（mode/mode_by_format/限额） | 792f4e1, dcb7592, 493b72f + src/aistudio_api/infrastructure/gateway/model_defaults.py:548-556,982-994,1087-1124 | 新增 | working | test_atg_config.py, test_features_config.py:99-228 | port | 待用户决定 |
| D01 Boot & Config | fallback 配置归一化（默认全 off） | 3d01ed2, 1f9ba61 + src/aistudio_api/infrastructure/gateway/model_defaults.py:655-660,1125-1129 | 新增 | working | test_features_config.py:128-132 | port | 待用户决定 |
| D01 Boot & Config | legacy features key 迁移（max_realms/global_max_instances/max_page_instances） | 8b6b52e + src/aistudio_api/infrastructure/gateway/model_defaults.py:1133-1171 | 新增 | partial | test_features_groups.py:50-92 覆盖 max_page_instances；routing 旧键未验证 | port | 待用户决定 |
| D01 Boot & Config | 渠道别名、已知渠道与 normalize_channel | cdb9e15 + src/aistudio_api/application/channel_registry.py:20-103 | 新增 | working | test_channel_registry.py:36-103 | port | 待用户决定 |
| D01 Boot & Config | 渠道注册表构建与默认值 | cdb9e15 + src/aistudio_api/application/channel_registry.py:166-218 | 新增 | working | test_channel_registry.py:105-221 | port | 待用户决定 |
| D01 Boot & Config | allowed_tags 与 max_pages=max_instances | 7cd6d74 + src/aistudio_api/application/channel_registry.py:49-70,138-150,187-217 | 改造 | working | test_channel_registry.py:156-181 | port | 待用户决定 |
| D01 Boot & Config | enabled_channels/model_list_prefixes/is_multi_mode | cdb9e15 + src/aistudio_api/application/channel_registry.py:221-262 | 新增 | working | test_channel_registry.py:222-271 | port | 待用户决定 |
| D01 Boot & Config | select_channel 不静默换渠道与 chat-only guard | cdb9e15, 8b6b52e + src/aistudio_api/application/channel_registry.py:344-405 | 新增 | working | test_channel_registry.py:273-406 | port | 待用户决定 |
| D01 Boot & Config | setup_logging 三 sink（stderr/broadcast/daily file）与 7 天清理 | eed30b6 + src/aistudio_api/infrastructure/utils/logger.py:264-297 | 改造 | partial | test_logging.py 覆盖级别与 dump；三 sink 装配未直接断言 | port | 待用户决定 |
| D01 Boot & Config | 动态日志级别（root DEBUG + handler 分级） | eed30b6 + src/aistudio_api/infrastructure/utils/logger.py:234-261 | 改造 | working | test_logging.py:68-87 | keep | 待用户决定 |
| D01 Boot & Config | dump 开关优先级与敏感字段掩码 | upstream/master..master + src/aistudio_api/infrastructure/utils/logger.py:72-227,311-337 | 原生 | working | test_logging.py:89-180,247-309 | keep | 待用户决定 |
| D01 Boot & Config | get_logger 命名空间 | upstream/master..master + src/aistudio_api/infrastructure/utils/logger.py:300-306 | 原生 | working | test_logging.py:57-66 | keep | 待用户决定 |

## 冲突/缺口

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
