# D11 API / UI 台账

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D11 API | Gemini generateContent 非流式端点 | `upstream/master..master`; `src/aistudio_api/api/routes_gemini.py:21-31`; `application/api_service_gemini.py:186-225` | 改造 | working | `tests/unit/test_atg_integration.py:95-284`; `test_gemini_request_normalization.py` | port | 是否继续以 Gemini 作为首选协议面 |
| D11 API | Gemini streamGenerateContent SSE | `upstream/master..master` `af3f55c`; `routes_gemini.py:34-41`; `api_service_gemini.py:54-263` | 改造 | working | `tests/unit/test_atg_integration.py:266-307`; `test_api_responses.py:184-225` | port | SSE 事件格式是否需对齐官方 v1beta |
| D11 API | Gemini countTokens 端点 | `af3f55c`; `routes_gemini.py:44-57`; `application/token_counter.py` | 新增 | working | `tests/unit/test_token_count_routes.py:41-66` | port | 是否保留真实上游计数而非本地估算 |
| D11 API | Gemini 模型列表 `/v1beta/models` | `upstream/master..master` `47cec70`,`6305377`,`0663cf5`; `routes_models.py:77-103`; `application/model_catalog.py:65-108` | 改造 | working | `tests/unit/test_routes_models.py:15-127`; `test_model_catalog.py` | port | 是否同时返回裸 ID 与 pg/build 前缀 |
| D11 API | Gemini 单模型元数据 `/v1beta/models/{id}` | `upstream/master..master` `6305377`,`0663cf5`; `routes_models.py:106-122` | 改造 | working | `tests/unit/test_routes_models.py:33-45,115-127` | port | 前缀渠道解析是否保留 |
| D11 API | OpenAI Chat Completions `/v1/chat/completions` | `c0412c8`; `routes_openai.py:70-147`; `schemas_openai.py` | 新增 | working | `tests/unit/test_format_openai.py:611-809` | port | 非流式与流式是否都保留 |
| D11 API | OpenAI 模型列表与单项 `/v1/models` | `c0412c8`,`0663cf5`; `routes_openai.py:150-185` | 新增 | working | `tests/unit/test_routes_models.py:87-101`; `test_format_openai.py` | port | 是否把 `/v1/models` 与 Gemini 列表统一投影 |
| D11 API | Claude Messages `/v1/messages` | `1375646`; `routes_claude.py:73-168`; `schemas_claude.py` | 新增 | working | `tests/unit/test_format_claude.py:723-850` | port | tool_use 与 thinking 块是否需完整对齐 |
| D11 API | Claude count_tokens `/v1/messages/count_tokens` | `af3f55c`; `routes_claude.py:171-195` | 新增 | working | `tests/unit/test_token_count_routes.py:68-120` | port | 是否复用同一上游计数通路 |
| D11 API | OpenAI Responses `/v1/responses` | `2b39b53`; `routes_responses.py:100-178`; `schemas_openai_responses.py` | 新增 | working | `tests/unit/test_format_responses.py:258-287` | port | 是否保留 Responses 独立事件模型 |
| D11 API | Responses input_tokens `/v1/responses/input_tokens` | `af3f55c`; `routes_responses.py:62-97` | 新增 | working | `tests/unit/test_token_count_routes.py:122-148` | port | 是否与 Gemini countTokens 统一计费口径 |
| D11 API | 协议面功能开关 features.formats | `3d01ed2`,`dcb7592`,`2b39b53`; `format_errors.py:69-114`; `defaults/config.yaml:69-74` | 新增 | working | `tests/unit/test_format_error_routing.py:57-106`; `test_format_openai.py:808-815` | port | 默认三个格式面全开是否可接受 |
| D11 API | 协议感知错误体映射 | `dcb7592`,`2b39b53` `1375646`; `format_errors.py:16-220` | 新增 | working | `tests/unit/test_format_error_routing.py:24-106` | port | 上游错误文案清洗规则是否保留 |
| D11 API | 应用级异常到 HTTP 状态映射 | `0cb1b82`; `app.py:293-373` | 改造 | working | `tests/unit/test_app_error_handlers.py:13-33`; `test_format_error_routing.py` | port | 路由拒绝是否一律 4xx 且 Build 未就绪 503 |
| D11 API | API Key 鉴权四种取键方式 | `upstream/master` 原生；`dependencies.py:18-115` 未被本分支改写 | 原生 | working | `tests/unit/test_api_auth.py:24-126`; `test_routes_models.py:101-115` | keep | 是否保留无 Key 即免密开放与 admin 密码超级权限 |
| D11 API | 本地回环免鉴权 | 无实现；`dependencies.py:69-115` 仅按 Key 判定，未见 127.0.0.1 分支 | 待判定 | broken | `未验证`（全仓检索无 loopback bypass） | drop | 是否要求仅回环地址免鉴权 |
| D11 API | 控制台密码鉴权与 `/auth/check` | `upstream/master..master` `fa1d9d3`; `dependencies.py:47-66`; `app.py:507-512` | 原生 | working | `tests/unit/test_api_auth.py:81-126`; `test_routes_system.py:101-108` | keep | 是否维持 web_password 与 API key 双轨 |
| D11 API | API Key 管理 CRUD `/api-keys` | `upstream/master` 原生；`routes_system.py:570-710` 写回 config.yaml | 原生 | unverified | `未验证`（无 tests/unit 直接覆盖） | port | 是否允许 Web 端直接改写 config.yaml |
| D11 API | 健康检查 `/health` | `upstream/master` 原生；`routes_system.py:164-167`；`9de3c8c` 仅容器探针改路径 | 原生 | working | `tests/unit/test_routes_system.py:92-99` | keep | 公网是否暴露 health |
| D11 API | SPA 导航与静态资源挂载 | `upstream/master..master` `0dd3e3e`,`4e341cf`; `app.py:484-560` | 改造 | working | `tests/unit/test_routes_system.py:110-126`; `test_routes_accounts.py:56-103` | port | API 路径前缀白名单是否继续硬编码 |
| D11 API | 旧轮询切号与路由快照端点 | `upstream/master..master` `28c981f`,`1a3e67a`,`da6ea25`; `routes_system.py:233-382` | 改造 | legacy | `tests/unit/test_routes_system.py:18-41,153-177`（仅 status/clear-cooldown） | drop | `/rotation/next` 是否随旧策略一起移除 |
| D11 UI | SPA 外壳、侧栏与路由守卫 | `upstream/master..master` `fd15dbf`,`2944c3c`; `web/src/router/index.ts:10-90`; `components/layout/Sidebar.vue` | 改造 | working | `tests/unit/test_routes_system.py:110-126`; `docs/dev/evidence/wp7-v2-20261020/p1-dashboard-1440.png` | port | 导航是否保留账号/统计/日志/配置四页 |
| D11 UI | 登录页与控制台密码弹窗 | `upstream/master..master` `fa1d9d3`; `views/LoginView.vue:34-140`; `stores/auth.ts:12-75` | 改造 | working | `tests/unit/test_routes_system.py:101-108`; `test_api_auth.py:81-126` | port | 是否继续用 localStorage token 而非 HttpOnly cookie |
| D11 UI | 控制面板 Dashboard | `fd15dbf`,`2944c3c`; `views/DashboardView.vue:1-135`; `components/dashboard/InstanceOverviewCard.vue` | 改造 | working | `tests/unit/test_routes_instances.py`; 截图 `wp7-v2-20261020/p1-dashboard-1440.png` | port | 实例总览与账号可用带是否并存 |
| D11 UI | 用量统计页 Stats | `1adf3c2`,`c7bdd80`; `views/StatsView.vue:1-300` | 新增 | working | `tests/unit/test_stats_routes.py:21-90`; 截图 `wp6-20261020/stats-desktop-1440.png` | port | 是否保留 paid 标记与自定义日期范围 |
| D11 UI | 日志页实时 SSE 流 | `c7bdd80`; `views/LogsView.vue:337-500`; `api/logs.ts:29-53` | 新增 | working | `tests/unit/test_logs_routes.py:44-211`; 截图 `wp7-v2-20261020/p5-logs-1440.png` | port | 断线重连与 gap 提示是否保留 |
| D11 UI | 日志按时间导出面板 | `c7bdd80`,`eed30b6`; `LogsView.vue:600-780`; `api/logs.ts:55-90` | 新增 | working | `tests/unit/test_logs_routes.py:212-320` | port | 导出范围是否仍限最近 7 天 |
| D11 UI | 账号管理页导入与批量操作 | `7ac7a1a`,`7cd6d74`,`fcd6f1f`,`1282d49`; `views/AccountsView.vue:240-570` | 改造 | working | `tests/unit/test_routes_accounts.py:103-126`; 截图 `wp7-v2-20261020/p2-accounts-1440.png` | port | 是否保留多文件拖拽与重复邮箱标记 |
| D11 UI | 实例切换面板 InstanceSwitchModal | `2944c3c`,`9786b3a`,`1282d49`; `components/accounts/InstanceSwitchModal.vue:36-340` | 新增 | partial | `tests/unit/test_routes_instances.py:120-185`; 截图 `wp7-v2-20261020/p2-switch-modal-390.png` | port | 单实例下是否仍允许 pg/build 双渠道按钮 |
| D11 UI | 账号实例卡片 AccountInstanceCard | `2944c3c`,`a59c885`,`1282d49`,`7cd6d74`,`fcd6f1f`; `AccountInstanceCard.vue:36-300` | 新增 | working | 截图 `wp7-v2-20261020/p2-instance-1440.png`; `test_routes_instances.py` | port | 未激活账号一键切换是否保留 |
| D11 UI | 实例上限设置卡 max_contexts | `2944c3c`,`9786b3a`; `components/config/InstanceLimitCard.vue:20-66` | 新增 | working | 截图 `wp7-v2-20261020/p3-settings-390-instance-limit.png` | port | 1-16 上限与“实例=账号+渠道+激活态”说明是否保留 |
| D11 UI | 系统配置页运行态卡片与免密提醒 | `fe37d9b`,`7cd6d74`,`1282d49`; `views/SettingsView.vue:109-210` | 改造 | working | 截图 `wp7-v2-20261020/p3-settings-1440-top.png`; `test_routes_system.py:128-177` | port | 端口/超时/代理/鉴权四卡是否保留 |
| D11 UI | 可视化配置编辑器 visual 与 yaml 双模式 | `fe37d9b`,`2944c3c`,`9786b3a`; `components/config/VisualConfigEditor.vue:49-530` | 改造 | working | 截图 `wp7-v2-20261020/p3-settings-1024-nested.png`; `test_features_config.py:270-315` | port | 深度分区与原始 YAML 是否同时保留 |
| D11 UI | 日志与调试配置卡 | `upstream/master` 原生；`components/settings/LogConfigCard.vue:45-175`；后端 `880954f` | 原生 | working | `tests/unit/test_logging.py:197-228`; 截图 `wp7-v2-20261020/p3-settings-1440-top.png` | keep | 是否允许运行期改日志级别 |
| D11 UI | API Key 管理卡 | `upstream/master` 原生；`components/settings/ApiKeyManagerCard.vue:141-340` | 原生 | unverified | `未验证`（无 UI 自动化；仅 `routes_system.py:570-710`） | port | 是否保留密钥明文显示与复制 |
| D11 UI | 控制台密码设置弹窗 | `upstream/master` 原生；`components/modals/TokenSettingsModal.vue:51-85` | 原生 | unverified | `未验证`（无 UI 自动化） | keep | 是否改为服务端会话而非本地 token |
| D11 UI | 四协议 API 示例 QuickApiExamples | `fd15dbf`,`2944c3c`; `components/dashboard/QuickApiExamples.vue:98-410` | 改造 | working | 截图 `wp7-v2-20261020/p1-dashboard-1440.png`; `test_format_*.py` | port | 示例是否随 features.formats 开关隐藏 |
| D11 UI | 控制面板模型调用概览表 | `fd15dbf`,`a59c885`; `components/dashboard/ModelStatsTable.vue:32-200` | 改造 | working | `tests/unit/test_stats_routes.py`; 截图 `wp7-v2-20261020/p1-dashboard-1440.png` | port | 是否保留 attempt 与 client 双计数展示 |
| D11 UI | paid/free 档位与付费模型展示 | `dad9cf7`,`1adf3c2`,`a59c885`; `AccountInstanceCard.vue:109-111`; `components/stats/ModelTokenTable.vue:96-100` | 新增 | working | `tests/unit/test_stats_routes.py:21-69`; 截图 `wp6-20261020/stats-desktop-1440.png` | port | 档位徽章与 Paid/Pro-only 标签是否保留 |
| D11 UI | 手动切号 UI 入口 | `upstream/master..master` `fa1d9d3`; `stores/system.ts:140-158,188`; `api/system.ts:187-192` | 原生 | legacy | `未验证`（全 web/src 无 .vue 调用 forceNextAccount） | drop | 手动切号是否重建为显式按钮 |
| D11 UI | 账号多模式与渠道矩阵 UI | `7ac7a1a`,`a59c885`; `AccountTable.vue`,`AccountRow.vue`,`AccountChannelMatrix.vue`,`SlotBindPopover.vue`,`ModelQuotaGrid.vue` | 改造 | legacy | `未验证`（AccountTable 未被 AccountsView 引用） | drop | 是否删除槽位/渠道矩阵旧 UI |
| D11 UI | Token 计数面板 CountTokensPanel | `1adf3c2`; `components/stats/CountTokensPanel.vue:11-85` | 新增 | working | `tests/unit/test_token_count_routes.py`; 截图 `wp6-20261020/stats-desktop-1440.png` | port | 计数请求是否继续不计入模型调用 |

## 冲突/缺口

| 冲突/缺口 | 证据 | 影响 | 待用户决定 |
|---|---|---|---|
| 单实例与双渠道 UI 语义冲突 | `InstanceSwitchModal.vue:354-392` 明示实例=账号+渠道+激活态；默认 `routing.max_contexts: 1`（`defaults/config.yaml:119`） | 单实例下仍渲染 PG/Build 两个渠道选择，与“单实例不开两 page”口径冲突 | 是否在 max_contexts=1 时禁用跨渠道按钮 |
| 手动切号 UI 断链 | `stores/system.ts:140` 定义 forceNextAccount，无任何 `.vue` 引用；后端 `/rotation/next` 存在（`routes_system.py:359-382`） | 用户无法从界面手动切号，旧能力实际不可达 | 重建时补按钮还是删除端点 |
| 孤儿 UI 组件未清理 | 无引用：`AccountTable` 链、`AccountModeBanner`、`ActiveContextsCard`、`AvailableAccountsCard`、`InstanceDetailsPanel`、`InstanceSummaryCard` | 旧槽位/渠道策略代码继续占包体，易被误读为现行 UI | 是否在重建时物理删除 |
| 模型列表前缀与 paid 过滤耦合 | `model_catalog.py:65-108` 对 build 渠道跳过 paid 与 chat-only 模型；`0663cf5` 同时返回裸 ID | API/UI 看到的模型集合依赖 features 与 paid 标记，文档易误读 | 是否把裸 ID 作为唯一稳定契约 |
| 无 Key 即免密且无回环保护 | `dependencies.py:78-80` 无任何 Key 直接放行；无 127.0.0.1 分支 | 误配空 api_keys 时对外全开；也无法仅对本地免密 | 是否要求默认拒绝并显式允许 loopback |
| error 状态与上游语义偏移 | `format_errors.py:134-154` 与 `app.py:303-334` 将策略拒绝映射 403/429/503 | 客户端按原生 429/403 重试可能失效 | 是否保持现映射并写客户端契约 |
| 格式面默认全开与渠道默认关闭 | `defaults/config.yaml:69-73` openai/claude/responses=true；`:94-99` build.enabled=false | 用户看到四协议示例但 build 模型不可用 | 是否按渠道实际能力生成示例与开关 |
| 文档 05 与 UI 现行口径不一致 | 05 记录 `InstanceLimitCard.vue:46` 为现行；当前 `AccountsView` 已改用 `AccountInstanceCard` | 以 05 为依据会复刻已弃用 UI | 是否以 D11 本表覆盖 05 的 UI 结论 |
