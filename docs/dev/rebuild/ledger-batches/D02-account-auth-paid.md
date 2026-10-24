# D02 Account / Auth / Paid

基线 `upstream/master`；范围 `upstream/master..master`。行内路径均相对 `src/aistudio_api/` 或 `tests/`。

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D02 | 账号注册表与原子持久化（AccountMeta/Registry/auth.json/meta.json/registry.json） | upstream/master..master; infrastructure/account/account_store.py:24-39,126-330 | 原生 | working | test_account_info_refresh.py::test_account_meta_backward_compatible_defaults, test_account_meta_roundtrip_with_refresh_fields | keep | 待用户决定 |
| D02 | 账号元数据扩展字段（tier/tier_checked_at/expired/duplicate_of/keepalive/routing/tags） | 1f9ba61,dad9cf7,cdb9e15,fcd6f1f; account_store.py:126-194 | 改造 | working | account_store.py:549-671 落盘；test_account_info_refresh.py 字段往返用例 | keep | 待用户决定 |
| D02 | 旧 `data/auth.json` 自动迁移为 `acc_migrated` 账号 | upstream/master..master; account_store.py:258-286 | 原生 | unverified | 仅代码路径，无迁移单测；未验证多账号/已存在 registry 分支 | keep | 待用户决定 |
| D02 | 单份 Cookie 导入（POST /accounts/import-cookies，含 email/tier 探测） | upstream/master..master; api/routes_accounts.py:624-708; cookie_parser.py:429-442 | 原生 | working | test_cookie_import.py::test_parse_cookie_string_preserves_cross_domain_google_cookies, test_import_bundle_flow | keep | 待用户决定 |
| D02 | 多格式 Cookie 解析（JSON 数组/对象、Netscape、Cookie Header） | upstream/master..master; cookie_parser.py:158-260 | 原生 | working | test_cookie_import.py::test_parse_raw_cookies_json_array_and_netscape | keep | 待用户决定 |
| D02 | 跨域 Cookie 保真与域名优先级（parse_storage_state/_cookie_domain_rank） | 7cd6d74; cookie_parser.py:126-155,345-407; cookie_refresher.py:85-113 | 改造 | working | test_cookie_import.py::test_parse_cookie_string_preserves_cross_domain_google_cookies, test_cookie_injection_preserves_storage_state_domains | keep | 待用户决定 |
| D02 | 瞬态/遥测 Cookie 丢弃与 `__Host-` 注入跳过 | upstream/master..master,7cd6d74; cookie_parser.py:89-108,284-342; cookie_refresher.py:31-40 | 改造 | working | test_cookie_import.py::test_parse_cookie_string_discards_transient_and_telemetry_cookies, test_domain_overrides_scopes_osid_strictly_to_aistudio | keep | 待用户决定 |
| D02 | 多账号探活批量导入（POST /accounts/probe-import，u/0..u/N 无限探测） | upstream/master..master; cookie_parser.py:575-677; api/routes_accounts.py:711-761 | 原生 | working | test_cookie_import.py::test_probe_import_activates_first_account_and_registers_in_rotator | keep | 待用户决定 |
| D02 | 凭据 Bundle 导入（POST /accounts/import-bundle，兼容旧项目 auth JSON） | 8ad9a63; api/routes_accounts.py:764-915 | 改造 | working | test_cookie_import.py::test_import_bundle_flow, test_import_bundle_accepts_single_old_project_auth_json | keep | 待用户决定 |
| D02 | 账号删除与活跃账号兜底迁移（单删 + Cookie 组删） | upstream/master..master; api/routes_accounts.py:490-524,565-607; account_store.py:478-495 | 原生 | working | test_cookie_import.py::test_delete_cookie_group_migrates_active_account | keep | 待用户决定 |
| D02 | 批量删除（POST /accounts/batch-delete，去重） | 7cd6d74; api/routes_accounts.py:527-562 | 新增 | working | test_routes_accounts.py::test_batch_delete_accounts_deduplicates_and_deletes | keep | 待用户决定 |
| D02 | 账号激活（POST /{id}/activate + AccountService.activate_account，BotGuard 模板重试/失败回滚） | upstream/master..master; application/account_service.py:52-124; api/routes_accounts.py:270-290 | 原生 | partial | test_browser_session.py:283 起；test_account_orchestrator.py:169-294；回滚分支未单独断言 | keep | 待用户决定 |
| D02 | 邮箱探测与回填（OGS/AI Studio HTML `oPEP7c` + data-au，按 auth_user） | upstream/master..master,0a3342b; cookie_parser.py:445-526; api/routes_accounts.py:293-379 | 改造 | partial | test_cookie_import.py::test_parse_emails_from_ogs_html_and_meta_reconciliation；detect-email 路由级未验证 | keep | 待用户决定 |
| D02 | 订阅等级探测映射（GetAiStudioBenefitTier：0 Free/1 Pro/2 Ultra/3 Plus） | upstream/master..master; cookie_parser.py:22-61,529-572 | 原生 | working | test_cookie_import.py::test_parse_benefit_tier_and_update_account_tier; test_account_info_refresh.py::test_tier_mapping | keep | 待用户决定 |
| D02 | AccountInfoRefresher 批量刷新（tier/expired/email、并发限流、409 进程内互斥） | dad9cf7,0a3342b; infrastructure/account/account_info_refresher.py:172-344; api/routes_accounts.py:200-267 | 新增 | working | test_account_info_refresh.py 前 13 项（含 test_batch_concurrency_bounded、test_batch_mutual_exclusion_raises） | keep | 待用户决定 |
| D02 | 401 标记 expired；403 key 回收重试一次；5xx/网络失败不改状态 | dad9cf7; account_info_refresher.py:274-307,467-513 | 新增 | working | test_account_info_refresh.py::test_401_marks_expired, test_401_cleared_after_recovery, test_server_error_does_not_change_state, test_403_key_blocked_recovers_and_retries_once | keep | 待用户决定 |
| D02 | expired 账号资格排除 + 24h 元数据/凭证刷新钩子（调度本体属 D08） | dad9cf7,1f9ba61; application/routing_eligibility.py:181-182; application/account_keepalive.py:54-66,265-299; config.py:343,349 | 改造 | partial | test_account_info_refresh.py::test_dispatch_excludes_expired, test_is_account_available_false_for_expired；24h 定时链路未验证 | port | 待用户决定 |
| D02 | OAuth/token 过期与 refresh_token 通道 | upstream/master..master..rebuild 全仓 `.py` 无 oauth/refresh_token 实现；仅 cookie 会话过期（infrastructure/gateway/session_auth.py:495-572 登录页判定 → SessionExpiredError） | 待判定 | legacy | 全仓 grep `.py` 无 oauth/refresh_token；未验证（无对应代码/测试）；不可复跑（真实账号） | drop（当前不存在） | 是否需新增 OAuth 支持 |
| D02 | 同邮箱重复账号标记（refresh_duplicate_flags：保留最早创建，其余 duplicate_of） | fcd6f1f; account_store.py:336-372,494 | 新增 | working | test_account_duplicates.py 4 项（标记/删除后清除/唯一邮箱/重载持久化） | keep | 待用户决定 |
| D02 | 账号名称与自定义 tag 更新、tag 目录（自定义 + tier 派生） | upstream/master..master,cdb9e15; application/account_service.py:130-149; api/routes_accounts.py:192-197,610-621 | 改造 | working | test_routes_accounts.py::test_account_tags_catalog_endpoint, test_account_routing_get_put_delete | keep | 待用户决定 |
| D02 | Paid 默认种子（`_DEFAULT_PAID_MODELS` + `_FALLBACK_MODELS.paid` + import 时 set_paid_models） | a10953d,1980ada; application/model_access.py:9-22; infrastructure/gateway/model_discovery.py:92-191 | 新增 | working | test_model_access.py::test_fallback_paid_models_allow_catalog_projection_without_discovery | keep | 待用户决定 |
| D02 | ListModels paid 解析：item[77]→protobuf field 78，`paid = access_tier == 2`，保留 access_tier | a10953d; model_discovery.py:526-529,631-632 | 改造 | working | test_model_discovery_paid.py::test_parse_raw_models_marks_paid_tier; test_routes_models.py:195-214 | keep | 待用户决定 |
| D02 | Discovery→paid 集合同步（升级/降级/缺失保守/快照恢复） | a10953d; model_access.py:33-56; model_discovery.py:213-233,285-289,349-363 | 新增 | working | test_model_access.py::test_discovery_updates_downgrade_upgrade_and_preserves_absent_models, test_snapshot_restores_status_and_applies_downgrade | keep | 待用户决定 |
| D02 | paid map API（/api/stats 每模型 `paid` 字段，_paid_map 读 discovery） | 1adf3c2; api/routes_system.py:98-161 | 新增 | partial | test_stats_routes.py:20-25 仅 stub `_paid_map`；真实 paid 值/缓存失效未验证 | port | 是否需独立 paid 查询端点 |
| D02 | Paid 模型仅 chat（is_chat_only_model / surface_capability / resolve_surface 守卫） | 1980ada,d8bbb22; application/surface_orchestrator.py:215-236,386-396,495-518 | 新增 | working | test_surface_orchestrator.py chat-only 6 项（含 build 组拒绝、强制 chat） | keep | 待用户决定 |
| D02 | Paid 模型需 paid 账号 + free/pro 资格（eligible_account/account_is_paid/PAID_TIERS/channel_affinity/配额池） | 1980ada,8b6b52e,d8bbb22; surface_orchestrator.py:35-55,239-247; routing_eligibility.py:124-132,164-186 | 新增 | working | test_routing_eligibility.py::test_pro_only_model_requires_paid_account, test_tier_default_quota_pools_are_narrowable, test_expired_and_surface_pinned_accounts_are_ineligible | keep | 待用户决定 |
| D02 | accessModes(Field 83) 3/4 档位过滤（is_model_allowed_for_tier） | upstream/master..master; model_discovery.py:402-417 | 原生 | working | test_routes_models.py:224-227（Free/Pro 拒、Ultra 放行） | keep | 待用户决定 |
| D02 | 模型目录 paid 投影（paid 模型不进 build，chat-only 去重） | 6305377,1980ada; application/model_catalog.py:73-92 | 改造 | working | test_model_catalog.py::test_chat_only_model_is_not_duplicated_into_build | keep | 待用户决定 |
| D02 | 账号身份校验与防错绑（verify_account_identity，不匹配拒绝回写） | 8e5e0cb; infrastructure/gateway/session_auth.py:235-363 | 改造 | working | test_session_lifecycle.py::test_verify_account_identity_does_not_rmtree（不匹配返回 False） | keep | 待用户决定 |
| D02 | Cookie 注入页面（先清 Cookie/Cache，全量注入含 accounts/OSID） | 9786b3a; session_auth.py:121-149 | 改造 | working | test_cookie_persistence.py::test_load_cookies_keeps_account_and_osid_cookies | keep | 待用户决定 |
| D02 | Cookie 回写合并（name+domain+path 键，保留未过期跨域 cookie，原子写） | 9786b3a; session_auth.py:366-437; account_store.py:45-65 | 改造 | working | test_cookie_persistence.py::test_save_cookies_merges_missing_account_cookies, test_save_cookies_preserves_cross_domain_twins, test_write_storage_state_preserves_non_cookie_fields | keep | 待用户决定 |

## 冲突/缺口

| 冲突/缺口 | 涉及行 | 说明/证据 |
|---|---|---|
| OAuth 刷新链路缺失 | OAuth 行 | 全仓 `.py` 无 oauth/refresh_token；仅 cookie 会话过期与 24h 元数据刷新，若要求 OAuth 过期语义需新设计 |
| paid 不从模型 API 暴露 | ListModels / paid map 行 | test_model_discovery_paid.py:44-67 断言 `/v1beta/models` 不含 `paid`/`access_tier`；仅 /api/stats 有 paid |
| paid map 仅覆盖已统计模型且实际值未测 | paid map 行 | routes_system.py:119-132 以 discovery 建模名映射；test_stats_routes.py 只 stub，无真实 paid 断言 |
| 默认 paid 种子双份且不完全一致 | Paid 默认种子行 | model_access.py:11-20 与 model_discovery.py:93-189 各自维护（如 gemini-3.1-pro-preview 为 False、argon 系为 True），冷启动覆盖顺序隐式 |
| free 默认组仅 build，而 paid 模型仅 chat | Paid 仅 chat / free-pro 行 | surface_orchestrator.py:35-41 与 :394-396 组合后 free 账号默认不可用 paid 模型（可能是有意，需确认） |
| 24h 调度跨 domain | expired / 24h 行 | D02 只落 expired 状态与刷新器；定时器在 account_keepalive.py（D08），合并时需核对边界 |
| 重复账号只标记不合并 | 重复账号行 | refresh_duplicate_flags 只写 duplicate_of，删除依赖用户/批量删除 |
