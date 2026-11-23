# REV-2.1.5.16 — keepalive 首轮分批降噪与元数据/Cookie region 403 仲裁只读审查

- 审查日期：2026-11-23
- 审查角色：独立只读 Reviewer
- 审查对象（本地未 push）：
  - `e9081d2` fix(keepalive): 首轮保活分批与 no_capacity 汇总告警
    - `src/aistudio_api/application/account_keepalive.py`（+212/-19）
    - `src/aistudio_api/infrastructure/account/account_info_refresher.py`（+11/-?，逐账号 no_capacity 降 INFO）
    - `tests/unit/test_account_keepalive.py`（+178）
  - `4493ac0` fix(keepalive): 元数据与 Cookie 轮换 region 403 触发 canary 出口仲裁
    - `src/aistudio_api/application/account_keepalive.py`（+133）
    - `src/aistudio_api/application/exit_arbitration.py`（+38）
    - `src/aistudio_api/infrastructure/account/account_info_refresher.py`（+145）
    - `tests/unit/test_account_keepalive.py`、`tests/unit/test_credential_exit_routing.py`、`tests/unit/test_exit_arbitration.py`
- 依据：`AGENT.md`、`docs/dev/rebuild/06-decision-log.md`、`docs/dev/rebuild/08-09-decision-record.md`（D-01..D-14）、`docs/dev/rebuild/domains/P1-account/research/exit-pool/{PLAN,CONTRACT}.md`、`REV-2.1.5.13/14/15`、本次源码与实跑门禁。
- **结论：CONDITIONAL（0 P0 / 1 P1 / 4 P2 / 3 Q）**。两提交的主干逻辑（分批、降噪、region 403 仲裁、account_bad/exit_bad、off 零网络、registry 权威）实现正确，门禁全绿（1989 passed）。唯一 P1 是“连续停止判据把 region defer 计入 no_capacity streak + 无轮转游标”在特定状态下可造成队首账号永久阻塞后续账号的 metadata/auth（详见 P1-1）；建议修复或明确接受后再 push/部署。P2 均为口径/测试隔离/观测性观察，不阻塞联调。

## 0. 审查边界与工作区说明

- 唯一写操作：本文件。未改代码/配置/测试/契约，未 commit/push，未启动服务或浏览器，未访问 VPS/外网/真实账号/真实 Google。
- 只读命令：`git show/diff/status/log`、`read/grep`、`uv run ruff check .`、`uv run ruff format --check`、`uv run pyright src tests`、`uv run pytest -q`。
- 工作区：`git status --short` 为空；`HEAD` 含 `e9081d2`、`4493ac0`，被审 6 个文件相对 `HEAD` 无本地改动，行号引用有效。

## 1. 必查项逐条结论

> 记号：PASS=符合；P1=阻塞；P2=非阻塞缺陷/口径观察；Q=提示；N=备注。

### 1. 分批：上限 / 连续 no_capacity 停止 / 剩余保持 due / off 全量（P1，见 P1-1）

- external 才启用：`_metadata_refresh` 走 `_refresh_metadata_batched` 仅在 `self._external_runtime()` 为真（`account_keepalive.py:619-625`），`_external_runtime` 取 `refresher.external_mode`（`:254-258`，`account_info_refresher.py:631-633`），off 模式走原全量 `refresh_accounts`（`:626-640`）。
- 上限：metadata `batch = due[:limit]`（`:752-753`，limit=12，`:47`）；auth `if external and auth_attempts >= limit: break`（`:1049-1058`，limit=8，`:48`）。上限行为正确。
- 连续停止：metadata `chunk_size = min(stop_after, limit)` 分块，streak 达 3 停止（`:754-793`，stop=3，`:49`）；auth `no_capacity_streak` 达 3 停止（`:1108-1124`）。
- 剩余保持 due：仅对 `results` 调 `mark_refresh_attempt`（`:656-675`），不改 `tier_checked_at`；未尝试账号不写库，下一轮仍 due。auth 只对 `saved_accounts` 调 `mark_auth_saved`（`:1162-1165`），defer/未尝试保持 due。**正确**。
- off 全量：`test_off_mode_metadata_refresh_still_attempts_every_account` 断言 43 账号一次性全量（`test_account_keepalive.py:1491-1503`）。
- **饥饿评估（不通过，P1-1）**：取批顺序固定为 store 顺序且无轮转游标（metadata `:609-615` + `:752-753`；auth `:1030-1044`），且停止判据使用 `_is_deferred_result`（所有 deferred，含 region defer）而非规格的 `no_capacity`（metadata `:775-783`；auth `:1108-1116`）。队首 ≥3 个持续 defer 的账号会永久占用每轮名额，后续账号永不推进。详见 P1-1。

### 2. 日志：逐账号 no_capacity 降 INFO；每轮一条汇总 WARNING；数字一致；不泄露敏感信息（PASS，附 P2-2）

- 逐账号降噪：refresher 三处 no_capacity defer 日志为 `logger.info`（`account_info_refresher.py:579-587`、`:1128-1137`、`:1266-1275`）。
- 汇总 WARNING：metadata `if deferred_ids: log.warning("capacity summary ...")`（`:717-726`）；auth `if deferred_accounts: log.warning(...)`（`:1152-1161`），每轮每 job 一条。
- 数字：metadata `attempted=len(results)`、`succeeded`、`failed`、`skipped`、`deferred`、`no_capacity`（`:676-737`）；auth `attempted=auth_attempts`、`succeeded=len(saved_accounts)`、`deferred`、`no_capacity`（`:1149-1161`）。**存在口径问题（P2-2）**：metadata `failed` 含 skipped；auth `attempted` 只计 rotate 循环而 `succeeded` 含 slot/control/rotate 全通道。
- 敏感信息：新增日志仅 account.id/outcome/count/异常类型（`:261-293`、`:295-318`、`account_info_refresher.py:635-685`），不含 Cookie/邮箱明文。

### 3. 元数据 403：仅 region 403、external 才仲裁、off 零网络、同账号同轮一次、不记 auth 冷却、不双报（PASS）

- 仅 region 403：tier 探测 `response.status == 403 and is_region_restricted_message(response.body)`（`account_info_refresher.py:816-822`）；页面抓取 403+region 才抛 `RegionRestrictedError`（`:1506-1509`），email 路径捕获（`:818`、`:970`）。非 region 403 走原失败路径。
- external 才仲裁、off 零网络：`_arbitrate_region_403` 先判 `self._active_exit_resolver() is None` 直接返回 `None`（`:644-647`），不构造客户端、不触发 canary；测试 `test_off_mode_region_403_does_not_arbitrate` 断言 `arbiter.calls == []`、`deferred is False`、`error` 含 `HTTP 403`（`test_credential_exit_routing.py:2819-2845`）。
- 同账号同轮一次：`once` 为 `refresh_account` 内局部集合（`:780`），tier/email 共用（`:818-822`、`:970-978`）；`_arbitrate_region_403` 先查 `account_id in once`（`:644-646`）。测试 `test_region_canary_fires_once_per_account_per_round` 连调两次仅一次（`test_credential_exit_routing.py:2847-2873`）。
- 不记 auth 冷却：region defer 返回 `deferred=True`、`error=None`、`skip_reason=outcome`（`:687-708`），keepalive 对 deferred 保留旧 error、不计 failed（`:656-675`、`:1126-1132`），不触发 429 冷却/账号失败计数。
- 不双报：`_apply` 按 outcome 只报一种事件（`exit_arbitration.py:341-378`）；account_bad 只报 `verify_account`，exit_bad/inconclusive 只报 `region_403`。

### 4. account_bad：registry 权威、meta 镜像、rotator 统计、重载不回退（PASS）

- 持久化：refresher `update_account_refresh(verify_required=True, expired=False)`（`account_info_refresher.py:670-676`）；keepalive `_persist_account_bad` 再写 store 并 `rotator.record_verify_required`（`account_keepalive.py:295-318`、`:656-659`）。
- registry 权威 + meta 镜像：`update_account_refresh` 同时写 `meta.json` 与 `registry.json`（`account_store.py:1183-1192` 调 `update_account_keepalive`，后者写 meta + registry，`:1131-1181`）。
- 启动不被覆盖：`_load_registry` 仅在 registry 缺 email/tier 时从 meta 回填（`account_store.py:540-620` 段），不复制 verify_required/expired；rotator 初始化以 store 为准覆盖 `stats.verify_required`（`account_rotator.py:588-605`），`get_account_stats` 持续镜像 store 的 verify/expired（`:685-700`）。
- 测试真实：`test_metadata_region_403_arbitration_outcomes[account_bad]` 断言 store verify=True/expired=False、registry 与 meta 文件均为 True、重载 `AccountStore` 后仍 True（`test_credential_exit_routing.py:2784-2816`）；`test_metadata_account_bad_updates_registry_and_rotator` 断言 rotator 记录（`test_account_keepalive.py:1644-1665`）。

### 5. exit_bad：上报 kind=region_403/canary=exit_bad、invalidate、本账号 defer、隔离不重复（PASS，附 P2-1）

- 上报 + invalidate：`_apply` 先 `await report_event(event_kind="region_403", canary="exit_bad")`，再 `invalidate_exit(exit_id)`（`exit_arbitration.py:344-357`）。
- 本账号 defer：metadata `_region_deferral`（`account_info_refresher.py:687-708`）；auth `region_deferred_accounts` + `deferred_accounts[300]`（`account_keepalive.py:1072-1090`）。
- 隔离不重复：keepalive 仅 defer，不额外调用隔离；slot 重建只由真实请求经 `routing_pipeline._rebuild_for_new_exit` 触发（`routing_pipeline.py:1311-1363`），keepalive 不重复触发。
- 测试：`test_metadata_region_403_arbitration_outcomes[exit_bad]` 断言事件 `("acc1","exit-1","region_403","exit_bad")`、`invalidated == ["exit-1"]`（`test_credential_exit_routing.py:2784-2816`）。
- 观察：维护仲裁固定 `kind="chat"`，build-surface 槽位账号可能取不到绑定（P2-1）。

### 6. Cookie 轮换：403 无响应体按出口嫌疑交 canary、不影响 signer/session 路径、同轮一次、defer 不推进 due（PASS）

- 判定：`_rotation_is_region_403` 只认 `status == 403`，`body/error` 为空或命中 region 标记即视为出口嫌疑（`account_keepalive.py:320-340`）。RotateCookies 两条路径（裸 httpx `cookie_parser.py:863-875`、signer `browserless_session.py:659-691`）均只回 status/updated，无响应体，故空体 403 → 交 canary，符合规格。
- 不影响现有路径：`rotate_account_cookies` 的成功/失败/signer/sibling 逻辑未改（`account_service.py:148-284`），新逻辑仅在 keepalive 调用方判定后分流。
- 同轮一次：`_region_arbitrated_round` 每 `run_now` 清空（`account_keepalive.py:555`），`_arbitrate_region_once` 先查集合再写入（`:261-268`）。
- defer 不推进 due：auth defer 账号不进 `saved_accounts`、不 `mark_auth_saved`（`:1126-1132`、`:1162-1165`），`_retry_delay` 返回 300s（`:476-486`）。测试 `test_auth_update_rotate_region_403_triggers_canary_and_defers` 断言 `auth_saved_at is None`（`test_account_keepalive.py:1603-1643`）。

### 7. 生产接线：默认仲裁登记可靠、resolver 未初始化/显式注入、阻塞性（PASS，附 P2-3）

- 默认登记：`build_exit_arbitration` 仅在 `resolver is None` 时写 `_default_arbitration`（`exit_arbitration.py:440-447`）；`app.py` 以关键字参数调用、不传 resolver（`app.py:523-529`），且该调用是 `RoutingPipeline(...)` 的实参，在 `AccountKeepaliveService(...)`（`app.py:531-537`）之前求值，故 keepalive 构造时默认已登记；keepalive 还每轮 external 时 `_attach_region_arbiter()` 懒查默认（`account_keepalive.py:231-252`、`:619-620`、`:1036-1037`）。
- resolver 未初始化（off）：`active is None` → 默认置 None、返回 None（`exit_arbitration.py:414-418`），keepalive external=False，零网络。
- 显式注入：不更新默认（`:444-446`），供测试隔离；生产单次构建无影响，但若未来 app 改用显式 resolver 而 keepalive 仍读默认，会拿到旧实例（P2-3）。
- 阻塞：`adjudicate` 全 async，canary 探针受 `wait_for(timeout)` 约束（`exit_arbitration.py:292-326`），全局 `_probe_lock` 忙时立即返回 inconclusive（`:300-305`），不阻塞事件循环/线程；keepalive 单轮最坏为多次有界 canary（受 600s 出口冷却收敛）。

### 8. 回归：D-05/active_requests、off、无第二账本、无新依赖、1989 测试、格式化（PASS）

- D-05/`active_requests`：两提交未触碰实例池/排空/并发账本（diff 仅 keepalive/refresher/arbitration 与测试）。
- 无第二账本：`_region_arbitrated_round` 仅为单轮去重集合（`account_keepalive.py:220,263-268`），不参与准入/计数。
- 无新依赖：`git show --stat` 仅 6 个文件，`pyproject.toml`/`uv.lock` 未变。
- off 模式：metadata/auth 均走旧全量路径（`:619-640`、`:1036-1044`），测试覆盖（`test_account_keepalive.py:1491-1503`、`test_credential_exit_routing.py:2819-2845`）。
- 1989 测试真实：`uv run pytest -q` → 1989 passed；ruff/pyright 全绿；改动 6 文件已格式化（详见第 4 节）。

### 9. 测试质量：抽查 ≥6 个关键用例（PASS，附 P2-4 缺口）

详见第 3 节；抽查的 8 个用例断言均非空转。

## 2. 缺陷与观察

### P1-1（阻塞，条件性）：连续停止把 region defer 计入 no_capacity streak + 无轮转游标 → 队首永久阻塞可致后续账号饥饿

- **判据与规格不符**：metadata 停止用 `_is_deferred_result`（所有 deferred）而非 `_is_no_capacity_result`（`account_keepalive.py:775-783`）；auth 用 `account_deferred`，region defer 也置 True（`:1072-1090`、`:1108-1116`）。但日志写“consecutive no_capacity”（metadata `:787`），auth 写“no_capacity/deferred”（`:1120-1122`）。规格要求“连续 3 个 no_capacity 停止”，实现是“连续 3 个 deferred 停止”。
- **无轮转游标**：metadata `batch = due[:limit]`（`:752-753`），`due` 按 `store.list_accounts()` 稳定顺序（`:598-615`）；auth 顺序遍历 `accounts`（`:1030-1044`）。成功账号因 `tier_checked_at`/`auth_saved_at` 推进而退出队首，但持续 defer 的账号永远留在队首。
- **可永久化的具体路径**：slot 绑定账号在坏出口上持续 region 403 时，`invalidate_exit` 只清 resolver 缓存（`resolver.py:476-499`），keepalive 不重建 slot（唯一重建点是真实请求的 `routing_pipeline._rebuild_for_new_exit`，`routing_pipeline.py:1311-1363`）。下一轮 `assignment_for(kind="chat")` 已无缓存 → `adjudicate` 返回 inconclusive（`exit_arbitration.py:279-290`）→ 继续 defer。若队首 ≥3 个此类账号（或 per-account 永久 no_capacity），本轮永远只处理这 3 个，其余账号 metadata/auth 无限期不推进，滚动 Cookie（1PSIDTS）可能过期。
- **影响**：违反必查项 1“不会导致某些账号永久饥饿”；非崩溃/数据损坏，但属可用性回归（e9081d2 之前每轮尝试全部账号）。
- **最小修复（任一或组合）**：
  1. streak 只计 `_is_no_capacity_result`，region defer 不触发停止（同时修正日志文字）；
  2. 每轮从持久/内存游标开始取 due/accounts（轮转起点）；
  3. exit_bad 且账号绑定 slot 时标记/触发 slot 重建（或由管理器事件驱动）。
- **若维持现设计**：需在决策记录中显式声明“exit 异常也停止本轮 + 接受饥饿风险”，并把日志/文档改为 deferred；否则建议按上述修复。

### P2-1（非阻塞，口径）：维护仲裁固定 `kind="chat"`，build-surface 槽位账号无法归因出口

- `_arbitrate_region_403` 固定 `kind="chat"`（`account_info_refresher.py:648-654`）；`_arbitrate_region_once` 同样（`account_keepalive.py:266-274`）。而 browserless slot 按 surface 存绑定（`engine_runtime.py:541-546`）。
- build 槽位账号维护期 region 403 → `assignment_for(kind="chat")` 为 None → `adjudicate` 返回 inconclusive（`exit_arbitration.py:279-290`）→ 不上报/invalidate，只 defer；exit 隔离依赖真实 build 请求。
- 测试 double `_ArbitrationResolver.assignment_for` 忽略 kind，未覆盖该差异。影响：build 槽位维护期不主动隔离坏出口，账号持续 defer（与 P1-1 叠加）。有真实流量兜底，故 P2。

### P2-2（非阻塞，口径）：汇总数字口径与日志措辞

- metadata `failed` 含 skipped（`account_keepalive.py:676-684`），同时 `skipped` 单列（`:688-701`），`ok: failed == 0` 使“仅 skipped”轮也返回 ok=False（有意，让账号保持 due）。
- auth `attempted=auth_attempts` 只计 rotate 循环（`:1049-1058`），`succeeded=len(saved_accounts)` 含 slot/control/rotate 全通道（`:1126-1145`），slot 较多时可出现 succeeded > attempted。
- 汇总 WARNING 在“仅 region defer”时也触发且 `no_capacity=0`（`:717-726`、`:1152-1161`），措辞易让运维误读为预算耗尽。均为 log-only。

### P2-3（非阻塞，测试隔离/接线对称性）：`_default_arbitration` 未纳入 conftest 重置

- 新全局 `_default_arbitration` 仅 `resolver=None` 时写入（`exit_arbitration.py:392-447`）。`tests/conftest.py:26-33` 的 autouse fixture 只 `reset_exit_resolver()`，未 `reset_default_exit_arbitration()`。
- `test_403_auth_refresh.py` 在 external env 下构造 `RoutingPipeline`（`_verify_pipeline` `:589-595`，`_enable_external_exit` `:603`），会经 `routing_pipeline.py:486` 的 `build_exit_arbitration(account_store=...)`（resolver=None）写入默认；`finally: reset_exit_resolver()` 后默认仍残留旧 resolver/旧 `asyncio.Lock`，可能污染后续 keepalive 用例（跨 loop 复用会降级为 inconclusive）。
- 生产单次构建可靠（`app.py:523` + `:531` 顺序），故仅 P2；建议 conftest autouse `reset_default_exit_arbitration()`。

### P2-4（非阻塞，测试缺口）

- 无“达到上限（metadata 12 / auth 8）即停止、剩余保持 due”的用例（现有仅覆盖连续 defer 停止）。
- 无“`build_exit_arbitration(resolver=None)` 在 external 下正向登记默认”的用例（`test_default_arbitration_registry_resets` 只验证显式注入不写默认，`test_exit_arbitration.py:327-340`）。
- “同轮一次”仅经私有 `_arbitrate_region_403` 直调（`test_credential_exit_routing.py:2847-2873`），未覆盖 tier 200 + email 403 的端到端路径。
- 测试 double 忽略 `kind`，无法发现 P2-1。

### Q1：`_persist_account_bad` 无条件 `expired=False`

- `account_keepalive.py:295-318`。keepalive 的 metadata/auth 均过滤 expired（`:598-604`、`:1040-1041`），故正常保活不会把已过期号“复活”；但手动 `refresh_account`/`refresh_tier` 对 expired 账号命中 region 403 时，可能把 expired 清成 false 并置 verify。请确认是否符合“account_bad=verify、expired=false”口径。

### Q2：RotateCookies 空体 403 一律按出口嫌疑

- `account_keepalive.py:320-340`。若某号 Cookie 真失效导致 403，canary 健康时会被判 account_bad→verify_required（而非 expired）。与必查项 6 一致，但属标签取舍，建议在决策记录备注。

### Q3：`REGION_DEFER_RETRY_SECONDS` 双份定义

- `account_keepalive.py:51` 与 `account_info_refresher.py:109` 各一份 300.0；建议单一来源以免漂移。

### N（备注）

- 无新依赖；无第二并发账本；D-05/`active_requests` 未触碰。
- off 模式零网络：`_arbitrate_region_403` 先判 resolver（`account_info_refresher.py:644-647`），keepalive `_external_runtime` False 走旧全量。
- `changelog.md` 未更新属正常（本次非发版提交）。

## 3. 测试质量抽查（≥6，断言非空转）

1. **metadata 分批 + 连续停止 + 下一轮继续**：`test_metadata_refresh_batches_and_stops_on_consecutive_no_capacity`（`test_account_keepalive.py:1438-1490`）。断言 `resolver.calls == acc01..acc06`、`attempted=6/succeeded=3/deferred=3/no_capacity=3`、acc01-03 有 `tier_checked_at` 而 acc04+ 为 None、仅 1 条汇总 WARNING、第二轮从 acc04-06 继续且不含 acc01。真实覆盖“停止 + 剩余 due + 续跑”。
2. **off 全量**：`test_off_mode_metadata_refresh_still_attempts_every_account`（`:1491-1503`）。断言 43 账号一次性全量、`attempted=43`、`deferred=0`。
3. **auth 分批 + 剩余保持 due**：`test_auth_update_batches_and_leaves_remaining_accounts_due`（`:1504-1602`）。断言 `rotated == acc01..acc06`、`deferred_accounts == [acc04,05,06]`、acc01-03 `auth_saved_at` 非空、acc07+ 为 None。
4. **auth region 403 → canary + defer**：`test_auth_update_rotate_region_403_triggers_canary_and_defers`（`:1603-1643`）。断言 `arbiter.calls == [acc01,02,03]`、`result["deferred"] is True`、三号 `auth_saved_at is None`。
5. **account_bad registry/meta/rotator**：`test_metadata_region_403_arbitration_outcomes[account_bad]`（`test_credential_exit_routing.py:2784-2816`）断言 store verify=True/expired=False、registry 与 meta 文件 verify=True、重载后仍 True；`test_metadata_account_bad_updates_registry_and_rotator`（`test_account_keepalive.py:1644-1665`）断言 rotator `verify_recorded == ["acc_bad"]`。
6. **exit_bad 上报 + invalidate**：`test_metadata_region_403_arbitration_outcomes[exit_bad]`（`:2784-2816`）断言事件 `("acc1","exit-1","region_403","exit_bad")`、`invalidated == ["exit-1"]`、verify=False。
7. **off 零网络**：`test_off_mode_region_403_does_not_arbitrate`（`:2819-2845`）断言 `arbiter.calls == []`、`deferred is False`、`region_outcome is None`、`error` 含 `HTTP 403`。
8. **同轮一次**：`test_region_canary_fires_once_per_account_per_round`（`:2847-2873`）断言首调 `inconclusive`、次调 `None`、`arbiter.calls == ["acc1"]`。
9. **account_bad 事件类型覆盖**：`test_account_bad_event_kind_override_reports_verify_account`（`test_exit_arbitration.py:305-325`）断言 `event_kind=verify_account`、`canary=account_bad`、无 invalidate。
10. **默认登记不误写**：`test_default_arbitration_registry_resets`（`:327-340`）断言显式 resolver 构建不写默认。

结论：抽查用例均有实质断言，非空转；缺口见 P2-4。

## 4. 门禁输出（原样）

```
uv run ruff check .
All checks passed!

uv run ruff format --check src/aistudio_api/application/account_keepalive.py src/aistudio_api/application/exit_arbitration.py src/aistudio_api/infrastructure/account/account_info_refresher.py tests/unit/test_account_keepalive.py tests/unit/test_credential_exit_routing.py tests/unit/test_exit_arbitration.py
6 files already formatted

uv run pyright src tests
0 errors, 0 warnings, 0 informations

uv run pytest -q
1989 passed in 64.68s (0:01:04)
```

- 说明：`uv run ruff format --check .` 全仓有 58 个文件显示“would be reformatted”，均为本两提交未触碰的既有文件（既有 format 漂移），**非本次引入**；被审 6 个文件已格式化。

## 5. 只读声明

本次审查为 **100% 只读**：未修改任何代码/配置/测试/契约，未 commit/push，未启动服务或浏览器，未访问 VPS/外网/真实账号/真实 Google。唯一写操作是落盘本文件。

## 6. 部署建议

- **不建议在 P1-1 处理前直接 push/部署**。P1-1 的最小修复（streak 只计 `no_capacity` + 可选轮转游标 / slot 重建）改动很小，建议本轮闭环。
- 若产品明确接受“exit 异常也停止本轮、接受条件性饥饿”的设计取舍，则需同步修改日志措辞与决策记录，P1-1 可降级为 P2，其余 P2/Q 不阻塞。
