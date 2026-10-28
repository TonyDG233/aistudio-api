# D05 Routing / Candidate / Switch / Retry

基线：`upstream/master..rebuild`（D05 相关模块相对 upstream 全部为新增文件，仅 `account_orchestrator.py` 为改造）。
证据：代码行号为本分支 `rebuild`；`pytest` 结果来自 `2026-10-24` 本地 `.venv`。

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D05 | channel 别名与前缀解析（chat/pg/playground/build/official，`models/` 先剥离） | 8b6b52e,7cd6d74; channel_registry.py:20-103 | 新增 | working | test_routing_eligibility.py:218-228；`pytest` 96 passed | keep | 待用户决定 |
| D05 | 显式 channel 前缀钉死，永不静默换渠道（未启用/未实现/未知前缀均显式拒绝） | 8b6b52e; channel_registry.py:344-368,269-307 | 新增 | working | test_routing_eligibility.py:218-228；test_surface_orchestrator.py:182-195 | keep | 待用户决定 |
| D05 | 裸模型选 channel：单渠道直取；chat-only→chat；已挂载渠道优先；否则 build 优先 | 7cd6d74; channel_registry.py:370-405; routing_pipeline.py:216-225,260-266 | 新增 | working | test_routing_eligibility.py:193-215；test_routing_pipeline.py:183-198 | keep | 待用户决定 |
| D05 | 渠道 enabled/implemented 门槛（official 强制 disabled/unimplemented，WP9 前不可用） | 8b6b52e; channel_registry.py:36-46,180-192 | 新增 | working | test_surface_orchestrator.py:195-206；official 无测试，未验证 | keep | 待用户决定 |
| D05 | 渠道 `allowed_tags` 为准入闸门（旧 `quota_pool` 折算为单一 tag） | 7cd6d74; channel_registry.py:198-217; routing_eligibility.py:197-198,45-49 | 改造 | working | test_routing_eligibility.py:126-165；语义与 tier tag 耦合（见冲突表） | port（与 quota pool 解耦） | 待用户决定 |
| D05 | tier→quota pool 映射（free→free，pro/ultra/plus/paid→pro+free，未知→free+pro） | 8b6b52e; surface_orchestrator.py:47-55,243-247 | 新增 | working | test_routing_eligibility.py:73-81 | keep | 待用户决定 |
| D05 | 账号级 quota_pools 只收窄不放大 | 8b6b52e; routing_eligibility.py:111-121; account_routing.py:41-54,146-158 | 新增 | partial | test_routing_eligibility.py:73-81；`apply_account_quota_pools` 无调用点 | port（接入筛选） | 待用户决定 |
| D05 | 组 `allowed_quota_pools` 收窄 | 8b6b52e; surface_orchestrator.py:267-276,331; routing_eligibility.py:200-207 | 新增 | broken | 仅存入 GroupPolicy，资格图从未读取；test_routing_eligibility.py:168-179 靠 channel tag 偶然通过 | port（补实现） | 待用户决定 |
| D05 | Paid 模型门槛：必须 paid 账号且只能走 chat | 1980ada; routing_eligibility.py:183-186; surface_orchestrator.py:215-231,239-240 | 新增 | working | test_routing_eligibility.py:84-96；test_model_access.py | keep | 待用户决定 |
| D05 | chat-only / model guard：非 chat 渠道显式拒绝 | 8b6b52e,1980ada; channel_registry.py:364-365,382-388; surface_orchestrator.py:495-511 | 新增 | working | test_routing_eligibility.py:226-228；test_surface_policy_routing.py:146-165 | keep | 待用户决定 |
| D05 | 分组匹配：tier/email regex、列表首个命中、组内 OR、禁用组跳过 | d8bbb22; surface_orchestrator.py:189-212,287-333 | 新增 | working | test_surface_orchestrator.py:64-109,230-242；test_surface_policy_v2.py:57-90 | keep | 待用户决定 |
| D05 | 组 allowed_surfaces 只收窄；无显式组回退 tier 默认（free→build-only，未知→chat） | 7cd6d74; surface_orchestrator.py:35-45,304-351 | 改造 | working | test_surface_orchestrator.py:182-229,257-271；test_surface_policy_v2.py:77-101 | keep | 待用户决定 |
| D05 | 账号级 routing 覆盖：surfaces / model_surfaces 钉面 / weight(0.1-100) | 8b6b52e; account_routing.py:88-143; routing_eligibility.py:193-195 | 新增 | working | test_account_routing.py；test_routing_eligibility.py:182-190 | keep | 待用户决定 |
| D05 | 资格图顺序：expired→paid/chat→组启用与面→账号收窄→tag→pools | 8b6b52e,1980ada; routing_eligibility.py:164-207 | 新增 | working | test_routing_eligibility.py:84-190 | keep | 待用户决定 |
| D05 | 原生渠道亲和度（paid=chat 优先，free=build 优先，其他=2） | 8b6b52e; routing_eligibility.py:124-132,205 | 新增 | working | test_routing_pipeline.py:165-198（间接） | keep | 待用户决定 |
| D05 | 资格排序 `rank_eligible`：affinity→组权重→账号权重→account_id | 7cd6d74; routing_selection.py:17-27 | 新增 | working | 无 test_routing_selection.py；仅经 pipeline 排序间接覆盖，未验证 | keep | 待用户决定 |
| D05 | 候选二次排序：available→affinity→weight→公平分→slot→ready→id | cd569bc,ec35c8d; routing_pipeline.py:303-355 | 新增 | working | test_routing_pipeline.py:272-391 | keep | 待用户决定 |
| D05 | 公平分：last_used 为主、requests/weight 为辅，避免老账号被永久饿死 | ec35c8d; routing_pipeline.py:398-412 | 新增 | working | test_routing_pipeline.py:272-347 | keep | 待用户决定 |
| D05 | warm slot / ready 账号仅作同分 tie-breaker，不绑架后续请求 | cd569bc; routing_pipeline.py:305-311,349-353 | 新增 | working | test_routing_pipeline.py:302-390 | keep | 待用户决定 |
| D05 | rotator 可用性过滤（异常时 fail-open） | ec35c8d; routing_pipeline.py:304-324 | 新增 | partial | rotator 异常→available=True，无测试；未验证 | port（fail-open 需复核） | 待用户决定 |
| D05 | acquire：派发前装载候选账号，首次 30s / 后续 15s 预算，返回 lease | cd569bc; routing_pipeline.py:419-442,488-497 | 新增 | working | test_routing_pipeline.py:393-409；`pytest` 96 passed | keep | 待用户决定 |
| D05 | borrow 复用：主 acquire 容量失败后，仅对未试账号二次 acquire | cd569bc; routing_pipeline.py:443-487; features `routing.borrow` | 新增 | working | test_routing_pipeline.py:429-446 | keep | 待用户决定 |
| D05 | 容量拒绝映射：build→`surface_instance_busy`，chat→`surface_quota_exceeded`；无 borrow 直接拒绝 | 8e5e0cb; routing_pipeline.py:443-456; surface_orchestrator.py:61-69 | 改造 | working | test_routing_pipeline.py:412-426；test_generation_routing.py:196-235；真机未复跑 | keep | 待用户决定 |
| D05 | `features.routing.max_switches` 单请求切号预算（默认 2，非法值回落 2） | ec35c8d; routing_pipeline.py:357-362,371; routing_safety.py:24-37 | 新增 | working | test_routing_safety.py:39-49,103-125；test_generation_routing.py:275-289 | keep | 待用户决定 |
| D05 | defer 预算：`max_defers` 固定 2、不可配置；容量/build busy 时 sleep 1s 重试 | b59d6f3,86391c8; routing_safety.py:27,39-56; generation_service.py:193-251 | 新增 | partial | test_generation_routing.py:157-235；sleep 与上限硬编码 | port（改为可配） | 待用户决定 |
| D05 | 429 限额：未出数据→切号；预算/候选耗尽→HTTP 429 显式失败 | ec35c8d,6a6afbe; upstream_errors.py:247-288 | 改造 | working | test_generation_routing.py:257-273；test_genai_sdk_compat.py:253 | keep | 待用户决定 |
| D05 | 401 会话失效：清模板、记录 auth_error、未出数据→切号，否则 401 | ec35c8d; upstream_errors.py:186-219 | 改造 | working | test_api_responses.py:79-83；切号分支未单测，未验证 | keep | 待用户决定 |
| D05 | 403 AuthError：清模板、记录 auth_error、未出数据→切号，否则 403 | ec35c8d; upstream_errors.py:221-245 | 改造 | working | test_api_responses.py:84-88；切号分支未单测，未验证 | keep | 待用户决定 |
| D05 | 204 空响应：首次且未出数据→清模板并重试一次（语义应为同账号 retry） | 6a6afbe; upstream_errors.py:290-299 | 改造 | broken | test_api_responses.py:246-248 仅覆盖流式首块；真实 retry_same 未置真（见下） | port（先修 retry_same） | 待用户决定 |
| D05 | 瞬态 RPC 404（Ambiguous RPC）：未出数据且未到上限→原地重试，严禁切号 | 6a6afbe; upstream_errors.py:302-315 | 改造 | broken | test_api_responses.py:131-156；routing 路径实际会换号 | port（先修 retry_same） | 待用户决定 |
| D05 | 非瞬态 404 / 其他 4xx：不重试，保留上游状态码与清洗后消息 | 6a6afbe; upstream_errors.py:347-360 | 改造 | working | test_api_responses.py:158-169；test_format_error_routing.py | keep | 待用户决定 |
| D05 | browser/BotGuard hang：预算内切号；否则关 session 原地重试（attempt<2） | ec35c8d; upstream_errors.py:317-346 | 改造 | partial | 无 hang 单测；关 session 分支未验证 | port | 待用户决定 |
| D05 | 同账号 retry（`retry_same`）：`record_retry` 只计数，从不置 True → 所有 retry 变成换号 | ec35c8d; routing_pipeline.py:514-522 vs generation_service.py:305,315,445,580,597,716 | 新增 | broken | test_generation_routing.py:64-67 的 FakePipe 自行置 True，掩盖缺陷 | port（必须修） | 待用户决定 |
| D05 | 流式已出数据不可切号：`has_yielded_data` 时 429/403/204/404/hang 一律不再切号 | 6a6afbe,ec35c8d; upstream_errors.py:195,230,261,290-295,326,330; generation_service.py:578,684-685 | 改造 | working | test_api_responses.py:171-180；test_surface_policy_routing.py:186 | keep | 待用户决定 |
| D05 | 熔断：滚动窗口 5 次失败/600s、冷却 300s、半开保留失败 | ec35c8d; routing_safety.py:76-164; routing_pipeline.py:524-530,548-555 | 新增 | broken | test_routing_safety.py:127-270 仅测原语；`record_switch_failure` 全仓无调用点，熔断永不触发 | port（接线或删除） | 待用户决定 |
| D05 | 成功闭环：清熔断失败、`retry_same=False`、释放 lease、记录 rotator success | ec35c8d,cd569bc; routing_pipeline.py:557-565; generation_service.py:390-400,773-785 | 新增 | working | test_generation_routing.py:129-155 | keep | 待用户决定 |
| D05 | lease 释放：异常/取消/流关闭 finally 均释放，不泄漏在途实例 | cd569bc,b59d6f3; generation_service.py:424-457,694-726,810-852 | 新增 | working | test_generation_routing.py:292-355 | keep | 待用户决定 |
| D05 | 切换耗尽：候选/预算/熔断→`surface_switch_exhausted`（HTTP 503），不误报 403 | 0cb1b82,ec35c8d; routing_pipeline.py:524-546; generation_service.py:180-190; surface_orchestrator.py:61-69 | 改造 | working | test_generation_routing.py:275-289；test_app_error_handlers.py；test_surface_policy_v2.py:154 | keep | 待用户决定 |
| D05 | 单实例 failover 限制：pinned slot 不可 rebind，max_contexts=1 时 build 只能等（defer），不能跨账号抢占 | 79901b0,8e5e0cb; instance_pool.py:854-917,490-502; routing_pipeline.py:444-456 | 改造 | partial | test_instance_pool.py 覆盖 rebind；跨账号 build failover 未验证 | port（需与"来消息即唤醒"对齐） | 待用户决定 |
| D05 | 遗留回退路径：pipeline 不可用时 `ensure_active_account` + `try_switch_account`（MAX_RETRIES=5） | 6a6afbe,ec35c8d; generation_service.py:160-177,316-317,598-601; account_orchestrator.py:13,68,202 | 原生 | working | test_account_orchestrator.py；非流式回退 surface 硬编码 chat（见冲突表） | drop（回退仅排障） | 待用户决定 |
| D05 | 换号规划 InstanceSwitcher.rebalance：按最受限 slot 先分配、只动 idle、逐槽 rebind | dd5e359,b59d6f3; instance_switch.py:71-137,173-199 | 新增 | working | test_instance_switch.py；与 D08 自动切号共用，真机未复跑 | keep | 待用户决定 |
| D05 | 上游错误分类：异常→(HTTP 状态, 清洗消息, Gemini status)，JSPB 嵌套消息解包 | 6a6afbe; upstream_errors.py:42-138 | 改造 | working | test_api_responses.py:77-127；`pytest` 9 passed | keep | 待用户决定 |
| D05 | 强制 429 测试钩子（`AISTUDIO_TEST_HOOKS=1` + account/model/surface/count） | ec35c8d; testing_hooks.py:35-60; generation_service.py:335-340,612-617 | 新增 | working | test_testing_hooks.py:1-25 | keep | 待用户决定 |

## 冲突/缺口

| 冲突/缺口 | 说明 | 涉及 |
|---|---|---|
| retry_same 从未置真 | 204/404/hang 的"同账号重试"实际释放 lease 后调用 `next_candidate` 换号；测试替身自行置 True，回归被掩盖 | routing_pipeline.py:514-522; generation_service.py:305,445,580,716; test_generation_routing.py:64-67 |
| quota pool 收窄失效 | 组 `allowed_quota_pools` 只存不判；账号 `quota_pools` 只在 `effective_account_pools` 返回、无消费者；渠道 `quota_pool` 被折算为 tier tag | routing_eligibility.py:95-121,197-207; surface_orchestrator.py:267-276; account_routing.py:146-158 |
| 熔断为死代码 | `record_switch_failure` 无调用点，`is_open()` 恒 False；文档称 process-global，实际每 pipeline 一个实例 | routing_safety.py:76-164; routing_pipeline.py:149,548-555 |
| 单实例 failover | pinned slot 不参与 rebind；build 单页 busy 只 defer 不换号，与"来消息即唤醒实例/换号重试"的旧需求冲突 | instance_pool.py:862-917; generation_service.py:213-227 |
| 回退路径 surface 不一致 | 非流式回退硬编码 `chat`，流式回退走 `_resolve_surface`（覆盖/分组），同一模型两条路径可落到不同面 | generation_service.py:265 vs 516-520; api_service_gemini.py:_resolve_surface |
| 测试缺口 | 无 `test_routing_selection.py`、无 `test_upstream_errors*.py`；排序/错误分类仅间接覆盖；`test_routing_pipeline` 不覆盖 `record_retry` | tests/unit/ |
| defer 不可配 | `max_defers=2`、`asyncio.sleep(1.0)` 硬编码，`features.routing` 只下发 max_switches | routing_safety.py:27; generation_service.py:225,244 |
