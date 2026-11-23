# REV-2.1.5.13 — 出口租约客户端、车道推导与 429 no_capacity / 维护 defer 只读审查

- 审查日期：2026-11-23
- 审查角色：独立只读 Reviewer
- 审查对象：
  - 提交 A `224fd1e feat(exit-pool): 租约客户端、车道推导与 429 no_capacity 语义`
  - 提交 B `25003bd fix(keepalive,accounts): no_capacity 维护延迟重试，不计失败不跳过`
- 前置：`e001cee`（HEAD 之前的本地提交，未 push）
- 结论：**CONDITIONAL**
- 依据：`AGENT.md`、`docs/dev/rebuild/06-decision-log.md`、`docs/dev/rebuild/08-09-decision-record.md`（D-01..D-14）、`data/tools/exit-manager/exit_manager/{server,manager,state}.py` 真实契约、实际 diff 与实跑门禁。

## 0. 审查边界与工作区说明

- 唯一写操作：本文件。其余全部只读。
- 允许的只读命令：`git show/diff`、`read/grep`、`uv run ruff check .`、`uv run pyright src tests`、`uv run pytest -q`。
- 未启动服务/浏览器、未访问 VPS/外网/真实账号；所有结论来自源码、契约比对与注入式单测 + 实跑门禁。
- 工作区状态：`git diff --stat HEAD` 为空；`git status --porcelain` 仅有既存未跟踪文档（`research/exit-pool/PLAN.md`、`REV-2.1.5.12-...md`），与本轮提交无关；`src/aistudio_api/static/` 与 `web/` 无改动。

## 1. 结论

**CONDITIONAL（不可写“可进入 UI/联调”）**。

两个提交的主链方向正确：`POST /v1/lease` 客户端逐字段对齐管理器契约；HMAC + Bearer 正确；429 `no_capacity` 在生成路径端到端为 429 + `Retry-After`，槽位保持 `UNSTARTED`，不换候选；off 模式无行为变化；keepalive/手动刷新/导入遇到 `no_capacity` 一律 defer、不推进 24h due、不计账号失败/冷却、批量继续。门禁全绿（1955 passed；ruff/pyright 0 error）。

仍存在 **0 个 P0 / 0 个 P1 / 3 个 P2**（均为租约计数/并发一致性问题，不破坏数据面正确性，但会让出口预算口径偏离设计）：

1. **P2-1 双倍计数（维护探测，slotted 账号）**：`refresh_account`/`refresh_tier` 先 `resolve_credential_exit(category="maintenance")` 计 1 次，再经 `_LeasedBrowserlessSession.send_hooked_request` 又计 1 次，一次 tier 探测消耗 2 次 maintenance 预算。
2. **P2-2 双倍计数（生成，槽位冷启动首个请求）**：`create_session` 先租约 1 次 generation，`request_scope` 再租约 1 次 generation；首个请求合计 2 次。与 `lease()` docstring“会话解析可复用绑定缓存（use_cache=True）”的自述不一致（`resolve_credential_exit` 从不传 `use_cache=True`）。
3. **P2-3 `_scope_leased` 为会话级共享布尔，非并发安全**：同一槽位并发请求/keepalive 探测与生成请求交错时，`request_scope` 退场会把另一请求仍在用的 scope 置回 `False`，导致后续 `send_hooked_request` 多计/漏计 maintenance。

## 2. 必查项逐条结论（1–9）

> 记号：PASS=符合；P0/P1/P2=缺陷；Q/N=观察/提示。

### 1. 契约一致（`/v1/lease` 逐字段、HMAC/Bearer、错误不外抛）

- **PASS（字段）**：客户端 `lease()` 发送 `account/lane/class/calls`（`resolver.py:532-537`）；服务端 `_handle_lease` 读取同名键并调用 `manager.lease(account, lane, cls, calls, request_id=...)`（`server.py:218-244`）。成功响应 `exit_id/socks_url/generation/ttl_s/budget_left`（`manager.py:458-468`）与客户端 `_parse_lease` 一致（`resolver.py:624-642`）。
- **PASS（lane/class 取值）**：`LANE_FREE="free"`/`LANE_PAID="paid"`（`resolver.py:57-58`）对齐 `state.py:27-30`；`generation`/`maintenance` 在 `CLASS_ALIASES` 合法（`state.py:32-44`）。
- **PASS（429 字段）**：`_no_capacity` 返回 `error/no_capacity + retry_after_s + lane + class`（`manager.py:423-450`）；客户端优先取 body `retry_after_s`，退回 `Retry-After` 头，再退回 30s（`resolver.py:594-622`）。
- **PASS（鉴权）**：Bearer 用 `hmac.compare_digest`（`server.py:83-90`）；账号发 `HMAC-SHA256(hmac_key, account_id)`（`resolver.py:297-303,531`），服务端 `_validate_account` 只接受 HMAC 形式（`manager.py:292-299`）。
- **PASS（不外抛）**：`lease()` 捕获 `Exception`（`CancelledError` 显式重抛）返回 `LeaseResult("error")`（`resolver.py:538-565`）；`resolve_credential_exit` 外层再兜底（`resolver.py:244-285`）。
- **Q-6**：`/v1/lease` 服务端不读取 `kind`，客户端也不在 payload 中发送；管理器绑定恒为 `kind="chat"`（`server.py:218-244`、`resolver.py:532-537`、`manager.py:627`）。需确认“绑定按账号而非按 surface”即为预期。

### 2. 车道（paid/free 推导、未知必 free、不改变模型准入/实例池 lane）

- **PASS**：`lane_for_tier` 仅 `account_is_paid` 为真才 paid，空/未知一律 free（`resolver.py:61-68`；`domain/model_access.py:317-318,42`）；测试覆盖 `Free/free//None/unknown -> free`、`Plus/Pro/Ultra/Ultra_20X -> paid`（`test_exit_pool.py:77-93`）。
- **PASS（不改变准入）**：`lane_for_tier` 与实例池 `instance_lane_for_tier` 使用同一 `account_is_paid` 判定（`model_access.py:327-329`）；未修改 `InstanceSpec`/`accepts_tier`/路由准入。
- **N（安全默认）**：导入/锚点阶段 tier 仍为占位的 `"Free"`，此时维护探测走 free 车道（`routes_accounts.py:197-198,1952-1956`）。未知必 free 是安全方向，但若实际为 Pro，会占用 free 维护预算；见 Q-8。

### 3. 429（生成端到端、四协议一致、Retry-After、不换候选/不毒化/active_requests、off 不变）

- **PASS（异常→HTTP）**：`ExitBudgetWaitError` 为 `RoutingError` 子类，`status_code=429`、`code="no_capacity"`、`retry_after=ceil(max(1,retry))`（`errors.py:168-192`）。应用级 `routing_error_handler` 渲染 `error_payload` 并写 `Retry-After`（`app.py:625-633`）；路由内层 `format_error_response` 同样经 `_retry_after_headers` 覆盖 429（`format_errors.py:221-231`）。
- **PASS（四协议 body）**：Gemini `status=RESOURCE_EXHAUSTED`；OpenAI/Responses `type=rate_limit_exceeded`；Claude `type=rate_limit_error`（`error_mapping.py:48-65,169-198`）。
- **PASS（不换候选）**：`acquire()` 对 `RoutingError/TierRequiredError` 直接 `raise`（`routing_pipeline.py:833-838`）；`handle_error` 对 `AistudioError` 分支记录后 `raise`，不进入 `_switch_or_raise`（`routing_pipeline.py:1147-1149`）。
- **PASS（不毒化槽位）**：`is_exit_unavailable_error` 覆盖 `exit_unavailable/no_capacity/exit_rebuild_required`（`errors.py:145-165`）；池 `activate` 与 `_bootstrap_runtime` 命中即保持 `UNSTARTED`/重抛（`instance_pool.py:645-660,709-713`）；e2e 断言 `UNSTARTED`（`test_credential_exit_routing.py:1976-1982`）。
- **PASS（active_requests）**：`request_scope` 在 `generate_content`/`stream_generate_content` 内，异常经 `generation_service.generate` 的 `finally` 释放 handle（`generation_service.py:378-396`、`instance_client.py:141,242`）。
- **PASS（off 不变）**：off 模式 `resolve_credential_exit` 返回全局代理、无 `defer`（`resolver.py:238-240`）；`_LeasedBrowserlessSession` 仅在 external 构造（`engine_runtime.py:463-501`）；off 相关既有用例全绿（`test_exit_pool.py:95,746-783`、`test_credential_exit_routing.py:193,893,1050,1161`）。
- **Q-4**：四协议 429 body/Retry-After 与“已就绪槽位请求期 `request_scope` 429”没有单测断言（仅覆盖 acquire 阶段）。代码路径判断正确，但属必查项覆盖缺口。
- **Q-5**：请求期 no_capacity 429 被 `_record_error("error")` 记为通用 error（`routing_pipeline.py:1147-1149,1963-1981`），进而 `rotator.record_error()` 仅累加统计、不加冷却（`account_orchestrator.py:98-99`、`account_rotator.py:372-381`）；确认统计口径是否应归 `rate_limited`。

### 4. 计数与租约（每请求计数、重复/漏计、TTL、缓存与 exit 隔离）

- **PASS（每请求）**：`request_scope` 无条件租 1 次 generation（`engine_runtime.py:110-118`），capture/replay 在 scope 内不再计；`test_leased_session_counts_once_per_request_scope` 精确断言一次 generation（`test_credential_exit_routing.py:1843-1888`）。
- **PASS（计数权威）**：`lease(use_cache=False)` 恒达管理器，成功租约写缓存但不读缓存（`resolver.py:520-592`）；测试断言第二次 lease 仍命中管理器（`test_exit_pool.py:257-295`）。
- **PASS（TTL 30~60s）**：`_store` 将 TTL 夹到 `[30, 60]`（`resolver.py:51-52,723-731`）；管理器 `lease_min/max_ttl_s=30/60`（`config.py:58-59`）；测试验证 59s 命中、61s 失效（`test_exit_pool.py:313-342`）。
- **PASS（exit 隔离联动）**：`invalidate_exit` 同时清 `_cache` 与 `_last_known`（`resolver.py:477-498`），`lease` 成功仍写 `_last_known`（`resolver.py:572-583`）；既有 `test_invalidate_exit_drops_every_account_on_that_exit` 已适配 POST payload（`test_exit_pool.py:441-448`）。
- **P2-1（重复计数，维护探测）**：`refresh_account` 在 `_post_tier_request` 前先 `_credential_exit(...)` 计 maintenance（`account_info_refresher.py:754-769`），而 slot session 的 `send_hooked_request` 又经 `_LeasedBrowserlessSession` 再计 1 次（`engine_runtime.py:158-169`）——一次 tier 探测 2 次 maintenance。`refresh_tier` 同理（`account_info_refresher.py:970-1010`）。测试未覆盖“resolver + slotted session”组合的计数。
- **P2-2（重复计数，生成首请求）**：`create_session → _resolve_exit_proxy → resolve_credential_exit → lease(calls=1)`（`engine_runtime.py:466-470,503-539`；`resolver.py:244-258`）在槽位冷启动时计 1 次 generation，`request_scope` 再计 1 次；首个请求合计 2 次。`lease()` docstring 声称“only use_cache=True (session resolution) may reuse cache”（`resolver.py:510-516`），但 `resolve_credential_exit` 从不传 `use_cache=True`，`ExitResolver.resolve()` 的缓存路径在生成链已无生产调用方（`resolver.py:644-666`）。
- **Q-3**：`lease(use_cache=True)` 分支无调用方（`resolver.py:500-529`），属死路径；若保留应明确“会话解析”由谁传。

### 5. 维护 defer（due/失败/冷却、逐账号、导入继续、批量不整批 503、重试间隔）

- **PASS（metadata due）**：deferred 结果 `ok=False,error=None,deferred=True`（`account_info_refresher.py:550-570`）；keepalive 对 deferred 用 `max(FAILURE_RETRY_SECONDS, retry_after_s)`=≥300s（`account_keepalive.py:296-305,353-355`）；`mark_refresh_attempt` 仅写 attempt/error、不动 `tier_checked_at`（`account_store.py:1183-1190`）。测试断言 300s + 原 `tier_checked_at` 不变（`test_credential_exit_routing.py:1990-2040`）。
- **PASS（不计失败/冷却）**：deferred 不计 `failed`（`account_keepalive.py:475-479`），auth_update 中 deferred 账号跳过 `saved/failed` 分支（`account_keepalive.py:807-829`），不写 `mark_auth_saved`/`_account_auth_failures`；测试断言 `_account_auth_failures=={}`、due=+300s（`test_credential_exit_routing.py:2150-2199`）。
- **PASS（手动刷新逐账号）**：`RefreshResult.deferred/retry_after_s` 透传到 `RefreshAccountResult`（`routes_accounts.py:594-602,831-837`），接口 200 返回逐账号结果（`routes_accounts.py:779-846`）；测试混合 deferred/ok（`test_credential_exit_routing.py:2043-2085`）。
- **PASS（导入继续/不整批 503）**：`import_cookies`、`probe_and_import`、`_post_save_identity_probe` 遇 deferred 只记录并继续导入（`routes_accounts.py:194-224,1763-1787,1837-1859,1952-1983`）；测试断言账号已保存、探测未调用、reason 前缀 `deferred_no_capacity`（`test_credential_exit_routing.py:2088-2147`）。
- **PASS（轮换 deferred 传播）**：`_LeasedBrowserlessSession.rotate_cookies_via_signer` no_capacity 返回 `deferred/skipped=no_exit`（`engine_runtime.py:185-226`），`account_service` 原样返回不扇出（`account_service.py:198-205`），keepalive 收集到 `deferred_accounts`（`account_keepalive.py:775-782`）。
- **Q-9**：deferred 走 `mark_refresh_attempt(error=None)`（`account_keepalive.py:468-474`），会把账号此前真实 `last_refresh_error` 清空；确认是否需要保留旧错误直到真正成功。

### 6. 并发与竞态（defer vs may-burn/rebuild、槽位状态、是否第二套账本、D-05）

- **PASS（defer vs rebuild）**：no_capacity 只经 `is_exit_unavailable_error` 保持 `UNSTARTED`/重抛（`instance_pool.py:645-660,709-713`；`instance_manager.py:295,1072,1367`），不触发 `may_burn`/自动 rebind；无第二套并发账本，仍只用 `active_requests`（`instance_pool.py:249-258`）。
- **PASS（D-05）**：租约发现管理器已迁移出口时仅节流告警，会话继续用旧出口直至既有 region-403/D-05 重建（`engine_runtime.py:49-69,152-156`），无热切换、无蓝绿。
- **P2-3（共享布尔竞态）**：`_scope_leased` 是 `_LeasedBrowserlessSession` 的单实例布尔（`engine_runtime.py:97,110-118,165,178`）。同一槽位允许并发 `active_requests>1`（`instance_pool.py:761-787`），keepalive 也会同时使用 `slot.session` 探测；请求 A 退场会把 B 仍在使用的 scope 置 `False`，B 后续 `send_hooked_request` 会多计一次 maintenance；反之 A 仍在 scope 时 B 的维护发送会漏计。`request_scope` 本身每次仍计 generation（不会漏生成计数），故影响集中在 maintenance 口径。建议用 `contextvars.ContextVar` 或“按 task 的 scope 集合”替代单布尔。

### 7. 迁移风险（resolve 兼容、direct fallback/verify、off 回归）

- **PASS（resolve 兼容）**：`ExitAssignmentResolver` 协议仍只有 `resolve`（`resolver.py:84-86`）；`resolve_credential_exit` 与 `_LeasedBrowserlessSession._lease` 均对“仅实现 resolve 的旧/fake resolver”兜底（`resolver.py:245-277`、`engine_runtime.py:120-131`）；测试 `test_resolve_keeps_legacy_fake_resolver_surface` 验证（`test_exit_pool.py:297-311`）。
- **PASS（删除旧分配路径）**：`_fetch_assignment/_parse_assignment` 已移除，无残留调用；网络请求改为 `POST /v1/lease`（`resolver.py:500-592`）。
- **PASS（direct fallback 未误改）**：`direct_fallback_enabled` 分支与 `ExitDecision.allowed`（`action in proxy/direct_fallback`）保持应急阀语义（`resolver.py:132-146,286-294`）。
- **PASS（verify 路径未改）**：本两提交未触及 verify-account 扫码/风控判定代码。
- **PASS（off 回归）**：见必查项 3；off 模式不构造 resolver/wrapper/httpx client。
- **Q-1**：`resolver.py` 模块 docstring 仍写 `GET /v1/assign`（`resolver.py:3-16`），`ASSIGN_PATH` 仅剩定义/导出（`resolver.py:40,873`），与实际 `POST /v1/lease` 不符。

### 8. 测试质量（抽查 ≥6 关键用例）

- **PASS（租约解析/计数）**：`test_lease_counts_every_call_and_parses_no_capacity` 断言请求字段、缓存已写、第二次仍达管理器、`retry_after_s=18.4`（`test_exit_pool.py:257-295`）。
- **PASS（429 回退）**：`test_lease_no_capacity_retry_after_fallbacks` 覆盖 body→header→默认 30s（`test_exit_pool.py:344-358`）。
- **PASS（TTL）**：`test_lease_cache_ttl_is_clamped_to_30_60s`（`test_exit_pool.py:313-342`）。
- **PASS（scope 计数）**：`test_leased_session_counts_once_per_request_scope` 精确断言 1 generation + 3 maintenance（`test_credential_exit_routing.py:1843-1888`）。
- **PASS（生成 429 e2e）**：`test_generation_no_capacity_surfaces_429_and_keeps_slot_retryable` 断言 status/code/retry 与槽位 `UNSTARTED`（`test_credential_exit_routing.py:1922-1982`）。
- **PASS（keepalive due）**：`test_keepalive_metadata_deferred_does_not_advance_due`（`test_credential_exit_routing.py:1990-2040`）。
- **PASS（手动刷新 deferred）**：`test_manual_refresh_reports_per_account_deferred`（`test_credential_exit_routing.py:2043-2085`）。
- **PASS（导入继续）**：`test_import_probe_deferred_still_imports`、`test_probe_and_import_deferred_keeps_account`（`test_credential_exit_routing.py:2088-2160`）。
- **PASS（auth_update defer）**：`test_auth_update_deferred_is_not_an_account_failure`（`test_credential_exit_routing.py:2163-2199`）。
- **Q-4（缺口）**：缺少四协议 429 body/`Retry-After` 渲染断言，以及“READY 槽位请求期 `request_scope` 429 经 `handle_error` 不切号”的用例；建议补 2 条。

### 9. 实跑门禁与静态产物

- **PASS（ruff）**：`uv run ruff check .` → `All checks passed!`
- **PASS（pyright）**：`uv run pyright src tests` → `0 errors, 0 warnings, 0 informations`
- **PASS（pytest）**：`uv run pytest -q` → `1955 passed in 55.94s`
- **PASS（静态产物）**：本轮无前端改动，`git diff --stat HEAD` 为空；`src/aistudio_api/static/` 未变更。

## 3. 缺陷清单（P0 / P1 / P2）

### P0

- 无。

### P1

- 无。

### P2-1 维护探测在 slotted 会话上双倍计数 maintenance

- 位置：`src/aistudio_api/infrastructure/account/account_info_refresher.py:754-769`、`:970-1010`；`src/aistudio_api/infrastructure/waa/engine_runtime.py:158-169`。
- 现象：`refresh_account`/`refresh_tier` 先 `resolve_credential_exit(category="maintenance")` 计 1 次；随后 `_post_tier_request` 走 `slot.session.send_hooked_request`，`_LeasedBrowserlessSession` 再计 1 次。一次 tier 探测消耗 2 次 maintenance 预算（`manager.py:634-635` 每次 lease 都 `budget.add`）。
- 复现：`_LeaseResolver` 计数 + 真实 `refresh_account` 且 `slot_provider` 返回同一 `_LeasedBrowserlessSession`，可见 `calls` 含 2 条 `maintenance`。
- 建议：session 非空时复用该会话的绑定出口，不再单独 `_credential_exit`；或把探测包进一个 maintenance “scope”，让 `send_hooked_request` 在 scope 内不再计租约。

### P2-2 槽位冷启动首个生成请求双倍计数 generation

- 位置：`src/aistudio_api/infrastructure/waa/engine_runtime.py:466-470`、`:503-539`、`:110-118`；`src/aistudio_api/infrastructure/exit_pool/resolver.py:510-516,644-666`。
- 现象：`create_session` 的 `_resolve_exit_proxy` 经 `resolve_credential_exit` 计 1 次 generation，`request_scope` 再计 1 次；首个请求合计 2 次。与 `lease()` docstring“session resolution 可用缓存跳过管理器”的意图不一致（实际无 `use_cache=True` 调用方，`resolve()` 缓存路径在生成链已无生产调用方）。
- 复现：external 模式新建槽位后发出 1 个生成请求，管理器 `/v1/lease` 收到 2 次 `class=generation`。
- 建议：二选一并写清口径——① 会话创建用 `use_cache=True`/`resolve()`，只让 `request_scope` 计数；② 若刻意让“建绑”也计 1 次，更新 `lease()` docstring 与 PLAN 预算假设（每槽位冷启动多 1 次）。

### P2-3 `_scope_leased` 会话级布尔在并发下失真

- 位置：`src/aistudio_api/infrastructure/waa/engine_runtime.py:97,110-118,158-183`。
- 现象：同一槽位允许多个并发请求（`instance_pool.py:761-787` 只 `active_requests += 1`），keepalive 也会并发使用 `slot.session`。A 退场 `finally` 置 `False` 会覆盖 B 仍在使用的 scope，导致 B 后续 `send_hooked_request`/流式多发一次 maintenance 租约；A 仍在 scope 时并发维护探测则漏计。生成计数不受影响（`request_scope` 每次无条件租）。
- 复现：两协程交错 `request_scope`，A 先退出后 B 再 `send_hooked_request`，管理器收到额外 `maintenance`。
- 建议：改用 `contextvars.ContextVar[int]`（按 task 计数）或维护 `set[task_id]`；或把 scope 状态放到 delegate 的请求上下文里。

## 4. Q / N

- **Q-1**：`resolver.py` 模块 docstring 仍描述 `GET /v1/assign`（`resolver.py:3-16`），`ASSIGN_PATH` 仅剩定义/导出（`resolver.py:40,873`）；建议同步为 `POST /v1/lease` 并删除死常量。
- **Q-2**：`EXIT_REBUILD_REQUIRED_CODE` 已加入 transient 集合（`errors.py:143,148-150`），但全库无 raise 点；确认是否预留。
- **Q-3**：`lease(use_cache=True)` 无调用方（`resolver.py:500-529`）；`ExitResolver.resolve()` 的缓存路径在生成链已无生产调用方（`resolver.py:644-666`）。
- **Q-4（测试缺口）**：四协议 429 body/`Retry-After` 与 READY 槽位请求期 429 未断言；建议补测（`error_mapping.py:169-198`、`app.py:625-633`、`routing_pipeline.py:1147-1149`）。
- **Q-5（统计口径）**：请求期 no_capacity 429 记为通用 `error`，走 `rotator.record_error` 仅累加统计不加冷却（`routing_pipeline.py:1147-1149,1963-1981`、`account_rotator.py:372-381`）；确认是否应归 `rate_limited`。
- **Q-6**：`/v1/lease` 忽略 `kind`，客户端亦不发送；管理器绑定恒 `chat`（`server.py:218-244`、`resolver.py:532-537`）。确认“按账号绑定”即预期。
- **Q-7**：canary 探针走 `create_canary_session`（未包 `_LeasedBrowserlessSession`，`engine_runtime.py:541-573`；`exit_arbitration.py:170-201`），其上游请求不计任何租约；管理器 `CLASS_PROBE` 从未使用。确认是否应计 probe。
- **Q-8**：导入/锚点阶段 tier 占位 `"Free"`（`routes_accounts.py:197-198,1952-1956`），Pro 账号的首次维护探测会计入 free 车道；安全但需确认预算意图。
- **Q-9**：deferred 调 `mark_refresh_attempt(error=None)`（`account_keepalive.py:468-474`）会清空账号此前真实 `last_refresh_error`；确认是否需要保留。
- **N（正向）**：租约不匹配只节流告警、旧出口继续使用直至既有 D-05 重建（`engine_runtime.py:49-69`），未热切换。
- **N（正向）**：`is_exit_unavailable_error` 扩集后，无出口/无预算/需重建都让槽位保持 `UNSTARTED` 而非 `INVALID`，启动与 rebuild 均不再中止（`instance_manager.py:295,1072,1367`）。

## 5. 实跑门禁输出（原样摘要）

```
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ uv run pytest -q
........................................................................ [  3%]
... (略)
........................................................................ [ 99%]
...........                                                              [100%]
1955 passed in 55.94s
```

- `git diff --stat HEAD`：无输出（工作区相对 HEAD 无改动）。
- `git status --porcelain`：仅 `?? docs/.../research/exit-pool/PLAN.md`、`?? docs/.../reviews/REV-2.1.5.12-fanout-machine-api.md`（与本轮审查对象无关）。
- `src/aistudio_api/static/`、`web/`：未修改。

## 6. 只读声明

本次审查为 100% 只读：未修改任何源码、测试、配置或静态产物；未 commit/push；未启动服务或浏览器；未访问 VPS/外网/真实账号。唯一写操作为落盘本文件。以上结论均基于 `224fd1e`/`25003bd` 的实际 diff、`data/tools/exit-manager/exit_manager` 真实契约、注入式单测与本地实跑门禁。

---

# 二轮复审 — `d09f591`（lease / defer P2 修复）

- 审查日期：2026-11-23
- 审查角色：独立只读 Reviewer
- 审查对象：`d09f591 fix(exit-pool): 单次维护/冷启动租约计数与 per-task scope`（前置 `25003bd`）
- 结论：**PASS**
- 0 个 P0 / 0 个 P1 / 0 个 P2 —— **可进入 UI/联调**
- 唯一写操作：本文件追加。其余全部只读；未跑 build、未启动服务/浏览器。

## 二轮结论摘要

上一轮 3 个 P2（维护双计、冷启动双计、共享 scope 布尔）与 Q-1/Q-4/Q-9 均已修复且有真实断言；off 模式、D-05、`active_requests`、旧 resolver 兼容均无回归。门禁全绿：`ruff` 通过、`pyright` 0 error、`pytest` **1966 passed**（较上轮 1955 增加 11 条新用例），7 个改动文件 `ruff format --check` 全部已格式化。

## 1. P2-1（slotted 会话维护只计 1 次）— PASS

- 修复：`refresh_account` 在 `session` 为 browserless 时不再预 `_credential_exit`（`account_info_refresher.py:758-780`）；`refresh_tier` 同样（`:1001-1051`）。只有 `session is None or not _session_is_browserless(session)` 的裸路径才预租（`:764-770`、`:1005-1042`）。
- defer 由 session 抛出：`_LeasedBrowserlessSession._lease` 命中 `no_capacity` 抛 `ExitBudgetWaitError`（`engine_runtime.py:157-162`）；`probe_tier_over_session` 显式 `except ExitBudgetWaitError: raise`，不再降级成 502（`account_info_refresher.py:356-359`）；`refresh_account/refresh_tier` 捕获后返回 deferred（`:781-791`、`:1052-1061`）。
- 测试真实断言 lease 数：
  - `test_slotted_session_tier_probe_leases_maintenance_once` 断言 `resolver.calls == [(id,"free","maintenance",1,"chat")]`（`test_credential_exit_routing.py:1949-1976`）。
  - `test_slotted_session_tier_probe_defer_comes_from_session` 断言 `deferred=True`、`retry_after_s==33`、且仍只有 1 条 maintenance（`:1978-2002`）。
  - `test_slotted_session_refresh_tier_leases_maintenance_once` 覆盖 `refresh_tier`（`:2004-2031`）。
- 邮箱子探测：tier 成功后的邮箱探测在 `decision is None` 时单独 `_credential_exit` 再走裸 HTTP（`:829-857`），tier 1 次 + 邮箱 1 次，对应 2 个上游请求，无重复计数。

## 2. P2-2（冷启动首请求复用建会话租约）— PASS

- 修复：`create_session` 用 `ExitDecision` 判定，仅当 `action=="proxy"` 且 resolver 真有 `lease` 能力时授予 `cold_start_lease`（`engine_runtime.py:484-526`）；直接回退/跳过/defer 不授予信用。
- `request_scope` 首请求同步消费信用（无 await，先置 False 再设置 scope），第二次 scope 才 `await self._lease("generation")`（`engine_runtime.py:124-136`）。
- 失败路径：`_resolve_exit_proxy` 在 skip/defer 时仍先抛（`:549-569`），不会构造带信用的 wrapper；`direct_fallback`（action 非 proxy）`cold_start_lease=False`，首请求会正常租约，不白送。
- 测试真实：`test_cold_start_session_reuses_creation_lease_for_first_request` 断言建会话后 generation=1，首个 scope 后仍=1，第二个 scope 后=2（`test_credential_exit_routing.py:2033-2073`）。

## 3. P2-3（ContextVar per-task scope）— PASS

- 修复：`_scope_leased` 改为每会话 `contextvars.ContextVar[bool]`（`engine_runtime.py:104-106`）；`request_scope` 用 token `set/reset`（`:131-136`）；`send_hooked_request`/`send_streaming_request` 读 `.get()`（`:183-201`）。
- 测试真实：`test_request_scope_is_per_task_not_a_shared_session_flag` 用两个 task + 事件让 A 先退场、B 仍在 scope 内发送，断言只有 2 条 generation、0 条 maintenance（`test_credential_exit_routing.py:2075-2147`），直接复现上轮竞态。

## 4. Q-1（docstring/死常量）— PASS

- 模块 docstring 改为 `POST {endpoint}/v1/lease (JSON: account/lane/class/calls)`（`resolver.py:3-6`）。
- `ASSIGN_PATH` 从常量与 `__all__` 删除（`resolver.py:37-43,869-876`），全库 `src/tests` 已无引用（仅出口管理器自身仍保留 `/v1/assign`）。

## 5. Q-4（四协议 429 + 请求期不切号）— PASS

- 四协议 body/Retry-After：`test_exit_budget_429_body_and_retry_after` 参数化 gemini/openai/responses/claude，断言 429、`Retry-After: 18`（17.2 向上取整）、Gemini `RESOURCE_EXHAUSTED` + reason `no_capacity`、Claude/OpenAI/Responses 的 type 与 code（`test_format_error_routing.py:140-174`）。
- READY 槽位请求期 429：`test_exit_budget_429_at_request_time_does_not_switch` 断言 `same_retries==0`、`switches==0`、`failed_accounts==∅`、`reassign_calls==[]`（`test_routing_pipeline.py:1123-1149`）。

## 6. Q-9（deferred 保留历史 `last_refresh_error`）— PASS

- 修复：metadata 前快照 `previous_errors`，deferred 结果回写旧值而不是 `None`（`account_keepalive.py:468-485`）。
- 测试真实：先写入 `"earlier upstream 403"` 再触发 no_capacity，断言 `last_refresh_error=="earlier upstream 403"`、`tier_checked_at` 不变、attempt 时间已推进（`test_credential_exit_routing.py:2264-2312`）。

## 7. 回归项 — PASS

- **off 模式不变**：external=False 时 `decision=None` → `proxy=settings.proxy_url` 且不构造 wrapper（`engine_runtime.py:484-526`）；off 相关用例（`test_exit_pool.py:95,746-783`、`test_credential_exit_routing.py:193,893,1050,1161`）全绿。
- **D-05 未破坏**：租约不匹配仍只节流告警、旧出口继续用到既有重建（`engine_runtime.py:52-72,170-174`），无热切换/蓝绿。
- **`active_requests` 未破坏**：本提交未改实例池/路由并发账本；`request_scope` 异常仍由 `generation_service` 的 `finally` 释放 handle。
- **无第二个账本**：ContextVar 仅作 scope 标记，不参与准入；`active_requests` 仍是唯一并发原语。
- **旧 resolver 兼容**：`_lease`/`rotate_cookies_via_signer` 仍对仅实现 `resolve` 的 fake 兜底（`engine_runtime.py:139-149,233-243`）；既有 legacy 用例通过。

## 8. 门禁输出（二轮原样）

```
$ uv run ruff check .
All checks passed!

$ uv run pyright src tests
0 errors, 0 warnings, 0 informations

$ uv run ruff format --check <7 changed files>
7 files already formatted

$ uv run pytest -q
........................................................................ [ 98%]
......................                                                   [100%]
1966 passed in 61.93s (0:01:01)
```

- 改动文件（7 个）已逐一格式检查；全库其余文件不在本轮范围。
- `git diff --stat HEAD` 为空（提交后工作树无源码改动）；无前端/静态产物改动。

## 9. 残留观察（Q，不阻断）

- **Q-10**：`refresh_account` tier 成功后的“邮箱子探测”若遇 `no_capacity`，只写 `email_error="deferred_no_capacity: ..."` 且 `ok=True`，24h due 会推进（`account_info_refresher.py:838-846,881-889`；`account_keepalive.py:468-485`）。tier 主数据已成功，属 best-effort；如要求邮箱也按 300s 重试，需要把该子结果标 `deferred`，请确认口径。
- **Q-11**：未新增 `direct_fallback`/`skip` 冷启动“不授予信用”的专门用例；代码路径正确（`cold_start_lease` 仅在 `action=="proxy"` 时为真，`engine_runtime.py:509-514`），建议补 1 条以防回归。
- **Q-12**：`rotate_cookies_via_signer` 仍无条件租 maintenance，不读 `_scope_leased`（`engine_runtime.py:203-212`）；当前调用点不在 generation scope 内，属既有实现，若未来在 scope 内调用会多计 1 次。
- **N**：`ContextVar` 在同 task 内嵌套 scope 时按 token LIFO 复位；`create_task` 子任务默认继承父 context（会继承 `True`），符合“同一请求内 capture/replay 不再计租约”的意图。

## 10. 二轮只读声明

本轮同样为 100% 只读复审：未修改任何源码/测试/配置/静态产物、未 commit/push、未跑 build、未启动服务或浏览器、未访问 VPS/外网/真实账号。唯一写操作为向本文件追加本“二轮复审”章节；全部结论来自 `d09f591` 实际 diff、契约比对与本地实跑门禁（1966 passed）。
