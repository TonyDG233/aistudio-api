# REV-2.1.5.15 — verify_account 事件上报与 Q-10 / Q-11 只读审查

- 审查日期：2026-11-23
- 审查角色：独立只读 Reviewer
- 审查对象：本地提交 `a19d81d`（未 push）「fix(exit-pool,keepalive): verify-account 事件上报与 Q-10/Q-11 回归」
  - `src/aistudio_api/application/routing_pipeline.py`（verify_account 事件上报）
  - `src/aistudio_api/application/account_keepalive.py`（Q-10 邮箱子探测 defer）
  - `src/aistudio_api/infrastructure/exit_pool/resolver.py`（事件上报有界超时重构）
  - `tests/unit/test_403_auth_refresh.py`、`tests/unit/test_credential_exit_routing.py`、`tests/unit/test_exit_pool.py`
- 依据：`AGENT.md`、`docs/dev/rebuild/06-decision-log.md`、`docs/dev/rebuild/08-09-decision-record.md`（D-01..D-14）、`docs/dev/rebuild/domains/P1-account/research/exit-pool/{PLAN,CONTRACT}.md`、`REV-2.1.5.13/14`、本次改动源码与实跑门禁。
- **结论：PASS（0 P0 / 0 P1 / 2 P2 非阻塞观察）**。核心事件语义、去重、非阻塞、off 模式零网络、region/verify 不双报、Q-10、Q-11 均达要求；全量 1974 测试 + ruff + pyright 全绿。P2 均为口径/防御性观察，不阻塞联调。

## 0. 审查边界与工作区说明

- 唯一写操作：本文件。未改代码、未 commit、未启动服务/浏览器、未访问 VPS/外网/真实账号/真实 Google。
- 只读命令：`git show/diff/status`、`read/grep`、`uv run ruff check/format`、`uv run pyright`、`uv run pytest`。
- 工作区状态：`git status` 仅 4 个既存未跟踪文档（`research/exit-pool/PLAN.md`、`REV-2.1.5.12/13/14`）；被审 6 个文件相对 `HEAD=a19d81d` 无本地改动，行号引用有效。

## 1. 必查项逐条结论

> 记号：PASS=符合；P2=非阻塞缺陷/口径观察；Q=提示。

### 1. 事件内容（PASS）

- 仅真实上游 `verification_required` 才触发：`AuthError.verification_required` 由 `domain/errors.py:59` 依报文特征置位，`classify_error` 对 verify 报文优先返回 `AuthError`（`errors.py:356-361`）；`routing_pipeline.handle_error` 的 `AuthError` + `verification_required` 分支唯一调用点 `routing_pipeline.py:1023`，与生成/令牌真实请求的 `classify_error` 路径（`client.py:304,384`、`instance_client.py:171,209`、`streaming.py:241`）一致。
- 字段与管理器契约一致（`CONTRACT.md:136-159`）：`kind=verify_account`、`canary=inconclusive`、`account=HMAC`、`exit_id=当前绑定`、`ts=int(time.time())`，见 `resolver.py:757-770`。`exit_id` 用 `assignment_for(..., include_expired=True)` 取会话仍在用的最后绑定（`resolver.py:443-461`、`routing_pipeline.py:910-917`），`kind` 取 `context.route.surface`（`chat`/`build`），与 session 解析时的 `kind=surface` 缓存键（`engine_runtime.py:495-497,542-546`）对齐。
- 不泄露敏感信息：payload 仅 HMAC/exit/kind/canary/ts；日志仅 `kind`/`exit_id`/异常类型（`resolver.py:801-812`）；测试断言明文 `acc-1` 不在 body（`test_403_auth_refresh.py:656-657`）。

### 2. 去重与量（PASS，附 P2-1）

- 同一 `RouteContext` 同 `(account, exit)` 只报一次：去重集在上下文内（`routing_pipeline.py:225,922-927`），测试 `test_same_account_exit_verify_event_is_reported_once` 连调两次仅 1 条（`test_403_auth_refresh.py:661-694`）。
- 跨请求不会刷屏（生成路径）：首次 verify 经 `_record_error("verification_required")` → `record_verify_required`（`account_orchestrator.py:72-83`、`account_rotator.py:418-432`）置 `verify_required`；`AccountStats.is_available` 对 `verify_required` 恒 False（`account_rotator.py:240-252`），后续请求不再选中该号。仅并发在途请求可能产生 ≤并发数 的同号重复；而管理器按**不同账号**计数（`PLAN.md:156,159,325`，`CONTRACT.md:156-159`），同号重复不放大连坐。
- 建议：生成路径无需全局限频；`countTokens` 路径存在缺口，见 **P2-1**。

### 3. 非阻塞（PASS，附 P2-2）

- fire-and-forget 有界超时：`report_event_nowait` 以 `create_task(_post_event_bounded)` 派发（`resolver.py:734-753`），`_post_event_bounded` 用 `asyncio.wait_for(..., timeout=self._config.http_timeout_s)`（默认 2.0s，`config.py:39,104`；`resolver.py:790-813`）。
- 管理器挂起不拖慢请求：任务后台化，`handle_error` 正常返回 `switch`；测试 `test_verify_event_failure_does_not_block_the_request` 证明请求在事件 POST 仍挂起时已完成（`test_403_auth_refresh.py:723-758`）；`test_report_event_nowait_is_bounded_on_hung_manager` 证明 sleep(5s) 挂起被 <1s 有界收敛（`test_exit_pool.py:594-616`）。
- 失败吞掉且有日志：`_post_event_bounded` 捕获 `TimeoutError`/`Exception` 返回 False 并记 warning（`resolver.py:801-812`），携超时不外泄 Cookie/邮箱。

### 4. off 模式零网络、零事件（PASS）

- `_report_verify_account_event` 先 `get_exit_resolver()`，off 模式返回 `None` 即直接 return（`routing_pipeline.py:907-909`、`resolver.py:846-858`），不构造 `httpx.AsyncClient`（client 懒建于首次真实请求，`resolver.py:424-433`）。测试 `test_off_mode_verification_sends_no_event` 将 `httpx.AsyncClient` 替换为抛错桩仍正常切号（`test_403_auth_refresh.py:697-720`）。

### 5. 与现有 403 仲裁事件不重复 + has_yielded_data 顺序（PASS）

- verify 与 region 互斥：`verification_required` 分支在 `region_restricted` 分支之前提前 `return`（`routing_pipeline.py:1009-1027` vs `1034-1044`）；`AuthError` 不会同时带两标记（`region_restricted` 仅在 `classify_error` 的 region 分支赋值，`errors.py:367-376`）。region_403 事件仅由 `exit_arbitration._apply` 上报（`exit_arbitration.py:339-362`），verify 分支不会进入仲裁 → 无双报。canary ListModels 命中 verify 时只标 canary 污染、不算出口坏、不上报 verify 事件（`exit_arbitration.py:228-231`）。
- 顺序：先 `_record_error`（隔离账号）再上报（`routing_pipeline.py:1013` 在 `1023` 之前），且上报置于 `has_yielded_data` 早退之前（`1023-1025`），确保已出正文的 verify 也上报、未出正文则继续 `_switch_or_raise`。顺序正确。

### 6. Q-10：邮箱子探测 no_capacity → deferred（PASS）

- 真实性：tier 探测成功（HTTP 200 权威 tier）后，邮箱子探测单独 `_credential_exit`；`deferred` 时置 `email_error="deferred_no_capacity: ..."`，结果仍 `ok=True`、`deferred` 默认 False（`account_info_refresher.py:826-845,873-881`）。keepalive 用 `_is_deferred_result` 同时兜住属性 `deferred` 与该 error 前缀（`account_keepalive.py:63-70`），据此：
  - 计入 `deferred_results`、`deferred` 计数（`account_keepalive.py:471,485`）；
  - 不计入 `failed`（`account_keepalive.py:498`）；
  - `mark_refresh_attempt` 沿用 `previous_errors`，不清历史错误（`account_keepalive.py:479-495`）；
  - `_retry_delay` 对 deferred 返回 `max(FAILURE_RETRY_SECONDS=300, retry_after_s)`，不推进 24h（`account_keepalive.py:310-318`、`39`）。
- 测试真实：`test_keepalive_defers_when_email_subprobe_hits_no_capacity` 用 `_SequencedLeaseResolver`（tier 租约 ok、邮箱租约 no_capacity），断言 `deferred==1`、`_next_due==now+300`、`stored.tier=="Pro"`、`email is None`（`test_credential_exit_routing.py:2582-2655`）。因 keepalive 传 `refresh_email=False`，仅“无邮箱账号”会走到该邮箱子探测（`account_info_refresher.py:828`），故 defer 后账号因缺邮箱仍 due，下一轮 300s 精准重试，无批量风暴。

### 7. Q-11：direct_fallback / skip 冷启动不授予 generation 信用（PASS）

- 生产口径：`cold_start_lease` 仅当 `decision.action=="proxy"` 且 resolver 有 `lease` 时为真（`engine_runtime.py:519-528`），direct_fallback（`action=="direct_fallback"`）与 skip（`_resolve_exit_proxy` 直接抛 `RoutingError`，`engine_runtime.py:565-571`）都不置信用；`request_scope` 未持信用时正常 `_lease("generation")`（`engine_runtime.py:124-137`）。
- 测试真实：direct_fallback 用例断言 create 期 1 次 generation 失败后，首个 `request_scope` 再租 1 次（共 2）（`test_credential_exit_routing.py:2111-2140`）；skip 用例断言 `create_session` 抛 `RoutingError` 且 generation 计数恒为 1、无 session 可授予信用（`test_credential_exit_routing.py:2143-2171`）。

### 8. 回归与门禁（PASS）

- `uv run pytest` → **1974 passed**（与预期一致）。
- `uv run ruff check .` → **All checks passed**。
- `uv run pyright src tests` → **0 errors, 0 warnings**。
- D-05 / `active_requests`：本提交未改 release/`active_requests` 计数与排空逻辑，仅新增一次同步查表 + fire-and-forget，且在既有 `_switch_or_raise` 之前；D-05 相关测试随全量套件全绿。
- 无新依赖：`git diff <parent> a19d81d -- pyproject.toml uv.lock` 为空；`httpx[socks]` 已是既有依赖（`pyproject.toml:14`）。

## 2. 缺陷与观察

### P2-1（非阻塞，口径/一致性）：`countTokens` 路径也会上报 verify_account，且不本地隔离

- `count_tokens` 复用 `RoutingPipeline.handle_error` 且将 `context.record_attempts=False`（`token_counter.py:173,215-217`）；`count_tokens` 对上游 ≥400 走 `classify_error`（`instance_client.py:208-209`），可产生 `verification_required`。该分支的 `_record_error` 因 `record_attempts=False` 直接跳过（`routing_pipeline.py:2015-2016`），但 `_report_verify_account_event` 未被 `record_attempts` 门控，仍会上报（`routing_pipeline.py:1023`）。
- 影响：①与“仅真实生成路径触发”的字面口径有偏差；②该号未被本地置 `verify_required`，后续 `countTokens` 仍可能再命中并重复上报同一 `(account,exit)`。因 `canary=inconclusive` 仅记录、管理器按不同账号计数（`PLAN.md:159`），**不构成连坐误烧**，仅少量冗余事件。
- 建议：要么将上报加 `and context.record_attempts` 真·生成路径门控；要么显式认定 countTokens 的 verify 亦属真实信号并补一条“同号短窗口”进程内节流（`resolver` 侧或上下文外缓存），避免重复。

### P2-2（非阻塞，防御性）：无绑定时以空 `exit_id` 上报；同步段未 try 包裹

- 若 external 模式但账号无绑定（应急直连 `AISTUDIO_EXIT_DIRECT_FALLBACK` 或会话早于 external 开启），`assignment_for(..., include_expired=True)` 返回 `None` → `exit_id=""`，仍调用 `report(...)`（`routing_pipeline.py:910-933`）。空 `exit_id` 的事件对管理器无归属意义（契约示例 `exit_id` 为真实出口 id，`CONTRACT.md:146`），可能被记到未知/空出口桶。
- 另：`_report_verify_account_event` 的同步段（`get_exit_resolver`/`assignment_for`/`create_task`）无 try 包裹；真实 `ExitResolver.assignment_for` 为纯字典查表不会抛，但自定义/legacy resolver 若在 `assignment_for` 抛错会向上冒泡并打断 `handle_error`。当前测试均使用真实 resolver，未覆盖该风险。
- 建议：`exit_id` 为空时直接 return（不上报）；对整段做 `try/except Exception: logger.debug(...)`，兑现“失败吞掉、绝不拖慢请求”。

## 3. 门禁输出（原样）

```
uv run ruff check .
All checks passed!

uv run pyright src tests
0 errors, 0 warnings, 0 informations

uv run pytest -q
1974 passed in 64.96s (0:01:04)

uv run ruff format --check <本次 6 个改动文件>
6 files already formatted
```

- 说明：`uv run ruff format --check .` 全仓有 59 个文件显示“would be reformatted”，均为 **本提交未触碰** 的既有文件（含 `docs/dev/evidence/*` harness 与 45 个 src/tests 文件），属仓库既有 format 漂移（很可能是 ruff 版本差异），**非 a19d81d 引入**；本次 6 个改动文件已格式化。

## 4. 只读声明

本次审查为 **100% 只读**：未修改任何代码/配置/测试/契约，未 commit、未 push，未启动服务或浏览器，未访问 VPS/外网/真实账号/真实 Google。唯一写操作是落盘本文件。
