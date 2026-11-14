# REV-2.2.0.2 — 防风控自愈补丁（反连坐切号 / 300s 超时 / 死槽自愈）只读审查报告

- 步骤：Stage 2.2.0 维护项 — 紧急防风控与路由自愈补丁（`REV-2.2.0.2`）
- 目标工作树：`<repo>`（唯一主工作区），HEAD `897c8ce`；本步改动**尚未提交**
- 审查范围：白名单 7 个生产文件 + 5 个测试文件（见 §2）；工作树中同时存在的 `android/`、`waa-signer/`、`.gitignore` 等 P10/APK 改动**不在本次审查范围**
- 权威依据：`AGENT.md` §4/§5/§7/§10；`docs/dev/rebuild/06-decision-log.md`、`08-09-decision-record.md` D-01..D-14
- 审查方式：原始审查为只读；逐行核对未提交 diff、当前源码、测试断言与实跑门禁（原始审查未修改代码/测试/配置，未启动真实服务，未连接真实 Google 账号，未 commit/push）。2026-11-14 修复闭环及其实跑证据见 §7。
- 审查日期：2026-11-14
- **闭环状态：PASS（2026-11-14 修复闭环；原始只读审查结论见 §1 历史说明，全部 P0/P1/P2/Q/N 关闭证据见 §7）**

> 路径说明：任务书指定的 `docs/dev/rebuild/domains/P2-routing-errors/` 在仓库中不存在。仓库 11 域总索引（`docs/dev/rebuild/domains/INDEX.md` §3）与 `AGENT.md` §10.1 明确本域唯一标识为 **`P2-routing`**，并禁止同号域分裂。为避免凭空新建第 12 个重复域，本报告落盘于既有的 `P2-routing/reviews/`，文件名严格保持任务书指定的 `REV-2.2.0.2-anti-correlation-and-timeout-guard.md`。

## 1. 结论

**PASS**

原始只读审查结论为 **CONDITIONAL**：账号保护主目标（不再因瞬态错误/输出超时踢走 Pro 号或健康免费号）已经成立，但存在 1 个 P1（300s 超时仅改签名默认值、生产装配仍为 90s/120s）、2 个 P2（死槽自愈 TOCTOU 与并发双改绑）和 8 个 Q。

修复闭环后（2026-11-14，见 §7）：

- **P1-1 已关闭**：`request_timeout_s` / `read_timeout_s` 的默认值在唯一配置源（`defaults/config.yaml`）、规范化层（`model_defaults.py`）与生产装配回退（`engine_runtime.py`）三处统一为 `300`，生产构造链不再覆盖为 90/120；新增 `test_browserless_runtime_defaults_to_300s_timeouts` 断言实际装配值。
- **P2-1 / P2-2 已关闭**：`RoutingPipeline` 新增 `_heal_lock` 串行化自愈；锁内通过 `_live_instance()` 重读槽位最新绑定账号与在途状态，`active_requests > 0` 直接跳过不 drain；仅在真实换号（`target_id != live.account_id`）时计入 `healed`。
- **Q-1 ~ Q-10 已关闭**：Q-1~Q-6、Q-9 见 §7.3；Q-7（移除生产代码 `PYTEST_CURRENT_TEST` 探测，改由 `tests/conftest.py` autouse fixture 注入 `AISTUDIO_SWITCH_BACKOFF_SECONDS=0`）、Q-8（瞬态 `RequestError` 同号重试复用 `retry_same_delay_sec` 退避）、Q-10（先选改绑目标再 `attempt.release()`，无目标时不再空耗句柄）见 §7.5；`defaults/config.schema.json` 的 `request_timeout_s`/`read_timeout_s` 默认值同步为 300。
- 全量门禁实跑：`ruff` 0 error、`pyright` 0 error、`pytest` 1537 passed、`git diff --check` 无输出（§5/§7.4）。

四类问题当前状态：**P0 = 0，P1 = 0，P2 = 0，Q（非阻塞残留）= 0，N = 0**。


## 2. 白名单与变更核对

### 2.1 六项核心变更逐项核对

| # | 声明变更 | 结论 | 关键证据 |
|---|---|---|---|
| 1a | 流式/RPC 读取超时提至 `300.0s` | **已关闭** | 唯一配置源 `defaults/config.yaml:36,48`、规范化 `model_defaults.py:707,756`、装配回退 `engine_runtime.py:146,149,248` 全部为 300 |
| 1b | `RequestError` 瞬态（204/404/408/425/5xx）只同号重试 1 次，耗尽抛出，**不切号** | 通过 | `routing_pipeline.py:948-998`；`is_transient_rpc_error` 仅放行 Random RPC 404（`domain/errors.py:297-302`）；测试 `test_transient_request_error_never_switches_or_rebinds[500/502/503/504]` |
| 1c | `is_browser_session_hang_error`（`SignerTimeoutError`/`BrowserTransportError`）只同号重试 1 次，耗尽抛出，**不切号** | **已关闭（Q-3）** | `routing_pipeline.py:1000-1038`；`domain/errors.py:305-330` 识别 `SignerTimeoutError` 类名与中文“超时”；测试 `test_session_hang_*`、`test_raw_signer_timeout_error_never_switches`（首次 retry_same、二次抛出、switches=0） |
| 2 | `begin()` 死槽自愈 + 预算耗尽时 `record_budget=False` 尽力换绑 | **已关闭（P2-1/P2-2）** | `routing_pipeline.py:431,1298-1416`；`_heal_lock` 串行化、锁内 `_live_instance` 重读、忙碌跳过、真实换号才计数；测试 `test_begin_heals_idle_slot_left_on_unavailable_account`、`test_heal_*`、`test_exhausted_budget_*` |
| 3 | 独立 `verification_required` 事件，只隔离当前单号、不连坐兄弟号 | 通过 | `routing_pipeline.py:848-867`；`account_orchestrator.py:72-96`；`test_verification_required_isolates_current_account_without_siblings`、`test_session_expired_still_isolates_same_anchor_siblings` |
| 4 | 429 / `verification_required` 切号加 `3.0~5.0s` 随机退避 | **已关闭（Q-2）** | `routing_pipeline.py:1164-1174`（`reassign` 之前、无条件退避）、`1259-1271`；`test_429_rebind_waits_randomized_switch_backoff` 断言 `["sleep","reassign"]`、`test_exhausted_budget_rebind_still_waits_switch_backoff` |
| 5 | `account_keepalive` 过滤禁用/过期/`is_auth_locked` 账号 + 后台 `refresh_email=False` | **已关闭（Q-4/Q-5）** | `account_rotator.py:651-661` 新增 `is_auth_locked`；`api/app.py:566-572` 显式 `rotator=rotator`；`account_keepalive.py:185-210` |
| 6a | `account_info_refresher` / `model_discovery` 请求头对齐 `Firefox/152.0` + `Sec-Fetch-*` | 通过 | `account_info_refresher.py:59-62,545-560,619-630`；`model_discovery.py:39-43,1143-1157`；两条新增 header 断言测试 |
| 6b | `model_discovery` HTTP 候选上限收紧为 2 个 | **已关闭（Q-6）** | `model_discovery.py:255-331`：先过滤 `is_auth_locked` 账号，再 paid-first 取前 2；`test_account_http_candidates_skips_auth_locked_accounts`、`test_account_http_candidates_caps_to_two_paid_first` |

### 2.2 重点核查：`_try_rebind` / `_switch_or_raise` / `_heal_unavailable_instances` 的句柄释放

结论：**句柄不会因未释放而让 `manager.reassign` 报 `active_requests > 0` 被拒；重复释放也安全。**

1. `_try_rebind` 在调用 `reassign` 之前先 `await self.release(attempt)`（`routing_pipeline.py:1152-1157`），随后才检查实例在途数（`:1157-1162`）。因此 `attempt` 持有的 `active_requests` 句柄已在改绑前归还，`InstanceManager.reassign` 的 `slot.active_requests > 0` 分支（`instance_manager.py:513-516`）不会被本请求自己的句柄误触发。
2. `RequestHandle`/`ActiveRequest.release()` 是**幂等**的：`_released` 守卫在重复调用时直接返回（`instance_pool.py:122-158`）。因此 `_switch_or_raise` 返回 `"switch"` 后 `generation_service.py:387/567` 与 `token_counter.py:228` 的二次 `pipeline.release(attempt)` 是无害的；流式两处调用点均先在 `"switch"` 分支释放并置 `attempt = None`（`generation_service.py:385-389,565-569`），`finally` 不会二次命中（`:394-396,645-647`）。
3. `_heal_unavailable_instances` 在 **准入期**执行，此时请求尚未 `begin_request`，本身不持有句柄；它在 `_heal_lock` 内通过 `_live_instance()` 重读槽位最新绑定账号，并以 `_instance_active_requests(instance.id) > 0` **直接跳过忙碌槽且不调用 `reassign`**（`routing_pipeline.py:1325-1340`）。因此修复后不会再触发 P2-1 描述的“检查后 drain 在途请求”路径；`reassign` 的 drain 语义只保留给人工/请求期显式换绑。
4. `_try_rebind` 的句柄释放顺序已按 `Q-10` 关闭：先选号、再释放句柄、随后才读 `active_requests`；无可用目标时不再提前释放（详见 §7.5）。
5. `test_exhausted_budget_heals_dead_slot_without_charging_budget` 已断言 `attempt.handle.released is True` 且 `context.budget.switches == 0`，与上述链路一致。

## 3. 问题清单

### P0（阻塞发布）

无（0 项，无残留）。

### P1

#### P1-1 300s 超时仅改签名默认值，生产装配仍为 90s/120s，声明变更未生效 — **已关闭（2026-11-14）**

- **关闭证据**：`defaults/config.yaml:36,48` → `300`；`model_defaults.py:707,756` 默认 → `300`；`engine_runtime.py:146,149,248` 生产装配回退 → `300`；`test_engine_runtime_lifecycle.py::test_browserless_runtime_defaults_to_300s_timeouts` 断言实际构造的 `SignerClient.request_timeout_s == 300.0`、`read_timeout_s == 300.0` 与 `BrowserlessSession._read_timeout_s == 300.0`。详细复验见 §7.1。

- **file:line**
  - `src/aistudio_api/infrastructure/waa/signer_client.py:230`（`request_timeout_s: float = 300.0`）
  - `src/aistudio_api/infrastructure/waa/browserless_session.py:258,276`（`read_timeout_s: float = 300.0`）
  - `src/aistudio_api/infrastructure/waa/engine_runtime.py:143-157`（`request_timeout_s=...get("request_timeout_s", 90) or 90`，`read_timeout_s=...get("read_timeout_s", 120) or 120`）
  - `src/aistudio_api/infrastructure/waa/engine_runtime.py:235-250`（`BrowserlessSession(..., read_timeout_s=...get("read_timeout_s", 120) or 120)`）
  - `src/aistudio_api/infrastructure/gateway/model_defaults.py:706-710,755-759,800,812`（校验与规范化默认 90/120）
  - `defaults/config.yaml:36,48`（`request_timeout_s: 90`、`read_timeout_s: 120`）
  - 本地运行时 `config.yaml:18,29`（`90.0` / `120.0`，untracked）
- **证据**：生产构造链 `engine_runtime._ensure_client()` 与 `create_session()` 每次都显式传入配置值；配置由 `defaults/config.yaml` 深合并并规范化，两项均默认 90/120。因此 `SignerClient` / `BrowserlessSession` 的 300s 默认值永远不会被生产代码触达，只有直接 `SignerClient(endpoint)` 的测试/工具会用到 300。
- **影响**：`browserless` 引擎下单次生成/流式读取仍在 90s（signer RPC）/120s（读取）处超时。账号保护目标仍由 1b/1c 的“不切号”达标，但“把超时提至 300s”这一独立目标未交付，长生成仍会被截断为失败。
- **建议修复**：把 300s 落到唯一配置源：`defaults/config.yaml`、`defaults/config.schema.json`、`model_defaults.py` 的 `get(..., 90/120)` 默认、`engine_runtime.py` 的 `get(..., 90/120)` 回退四处同步改为 300（或抽单一常量），并为 `engine_runtime` 增加断言测试；注意 `config.yaml` 为本地 untracked，首次运行从默认模板复制。

### P2

#### P2-1 `begin()` 死槽自愈与 `acquire()` 存在 TOCTOU：可 drain 掉并发请求正在使用的槽 — **已关闭（2026-11-14）**

- **关闭证据**：`routing_pipeline.py:431` 新增 `self._heal_lock`；`:1352-1367` 在锁内 `_live_instance()` 重读槽位，`_instance_active_requests(live.id) > 0` 时直接 `continue`，绝不调用 `reassign`。`test_heal_skips_slot_that_became_busy_after_snapshot` 断言忙碌槽 `healed == 0` 且 `reassign_calls == []`。详细复验见 §7.2。

- **file:line**
  - `src/aistudio_api/application/routing_pipeline.py:1278`（先读 `active_requests == 0`）→ `:1308-1310`（再 `await reassign`）
  - `src/aistudio_api/application/instance_manager.py:722-729`（`begin_request` **不取** `_lifecycle_lock`）
  - `src/aistudio_api/application/instance_manager.py:513-516` + `:1242-1258`（`reassign` 见 `active_requests > 0` 即 `_retire_record → retire_runtime(drain)`，超时强制关闭）
  - `src/aistudio_api/application/instance_pool.py:139-158`（generation 变更后的 stale release 被忽略）
  - `src/aistudio_api/application/routing_pipeline.py:620-630` + `account_rotator.py:237-259`（可用性按 **model+surface** 计，429 冷却按模型）
- **过程**：请求 A 的 `begin()` 在账号 X 仍健康时把 slot 放入候选；随后 X 在模型 A 上 429；请求 B 针对模型 A `begin()` 触发自愈，`_instance_active_requests == 0` 检查通过；在 `reassign` 取锁的 await 让出点，请求 A 的 `acquire()` 调用 `begin_request` 把该槽 `active_requests` 加 1；`reassign` 取锁后看到 >0，走 `_retire_record` 有界 drain，超时强制关闭 A 正在进行的生成。
- **额外放大因素**：`_account_available` 是按 `route.model` 判定的（冷却按模型），因此该槽对模型 B 仍完全健康；自愈只因模型 A 的 429 就可能中断该槽上模型 B 的在途请求。
- **影响**：低概率但真实的在途请求被 drain/强制关闭（客户端报错、会话被回收）。窗口窄（检查→取锁之间），但 429 风暴正是本补丁目标场景。
- **建议**：在 `_lifecycle_lock` 内二次确认 `active_requests == 0` 后再改绑，忙碌则放弃自愈；或提供“仅空闲时改绑、不 drain”的内部入口（区别于人工换绑的 drain 语义）；`acquire()` 也应在 `begin_request` 前复核账号可用性，避免陈旧候选落到已冷却账号。

#### P2-2 并发 `begin()` 可对同一死槽连续改绑（X→Y 抖动），日志还会计入一次“自愈成功” — **已关闭（2026-11-14）**

- **关闭证据**：`routing_pipeline.py:1327-1416` 全部自愈逻辑进入 `_heal_lock`；每槽锁内重读最新账号，`target_id == live.account_id` 时跳过；仅真实换号才 `bound.add(target_id); healed += 1`。`test_heal_rereads_live_account_and_skips_rebound_slot` 断言并发已换绑的槽 `healed == 0` 且无二次 `reassign`。详细复验见 §7.2。

- **file:line**
  - `routing_pipeline.py:1273`（`bound` 在循环前一次性快照）→ `:1275-1319`（逐槽 `reassign`，`bound.add(target)` 仅影响后续槽）
  - `instance_manager.py:487-488`（目标账号与当前绑定相同时早退、不激活）
  - `routing_pipeline.py:1319-1330`（无论是否真正改绑，都 `healed += 1` 并打印“死槽自愈完成”）
- **过程**：两个并发 `begin()` 同时看到同一槽绑定死号，均以各自快照选号。串行过锁后，若选择器两次给出不同账号，就会发生一次多余的全量改绑（关闭/重建 Page、再次同 IP 换号），与“反连坐/降频”目标相反；若第二次给出同一账号，则 `reassign` 早退，但日志仍记为 healed。
- **建议**：在锁内重读槽当前绑定账号并与快照比对（已变则跳过）；仅在 `reassign` 真正改绑时累加 `healed`；必要时对同一槽的自愈加短 TTL 去重。

### Q（质量/风险，非阻塞）

- **Q-1 死代码 — 已关闭**：`routing_pipeline.py:1029-1031` 不可达的重复 `if isinstance(exc, RequestError):` 分支已删除；当前 `RequestError` 仅保留 `:951-990` 一处处理。
- **Q-2 退避未覆盖自愈路径，且发生在切号之后 — 已关闭**：`_switch_backoff` 已前移到 `reassign` 之前（`routing_pipeline.py:1166-1175`），且不再受 `record_budget` 限制；429/`verification_required` 无论预算是否耗尽都先退避再激活新账号。`test_429_rebind_waits_randomized_switch_backoff` 断言调用顺序为 `["sleep","reassign"]`；`test_exhausted_budget_rebind_still_waits_switch_backoff` 断言 `record_budget=False` 仍退避。
- **Q-3 原始 `SignerTimeoutError` 不被 hang 判定命中 — 已关闭**：`domain/errors.py:313` 增加类名识别，`:317` 增加中文“超时”识别；`test_raw_signer_timeout_error_never_switches` 已更新为首次 `retry_same`、二次抛出且 `switches == 0`。
- **Q-4 `verification_required` 复用 `session_expired` 语义 — 已关闭（按设计）**：`account_rotator.py:651-661` 新增公共 `is_auth_locked()` 读取 `session_expired`，为 keepalive/发现提供统一只读判定；独立 `verification_locked` 状态属于后续协议演进，不是本轮 Q 残留，按设计关闭。
- **Q-5 keepalive 的 rotator 装配是隐式全局 — 已关闭**：`api/app.py:566-572` 构造 `AccountKeepaliveService` 时显式传入 `rotator=rotator`；`is_auth_locked` 现为真实方法，`account_keepalive.py:192-201` 的首选分支不再恒为死路。
- **Q-6 model discovery 候选截断未考虑冷却/锁定 — 已关闭**：`model_discovery.py:255-331` 在排序/截断前通过 `_auth_locked()` 过滤 `rotator.is_auth_locked()` 为真的账号；`test_account_http_candidates_skips_auth_locked_accounts` 断言锁定 Ultra 后候选为 `Pro, Plus`。
- **Q-7 生产代码内嵌测试环境探测 — 已关闭**：`_default_switch_backoff_range()` 已删除 `PYTEST_CURRENT_TEST` 分支与 `TEST_SWITCH_BACKOFF_RANGE` 常量（`routing_pipeline.py:345-378`），仅按 `AISTUDIO_SWITCH_BACKOFF_SECONDS` 解析；`tests/conftest.py:167` 的 autouse fixture 统一注入 `"0"`，测试零延迟语义不变；`test_default_switch_backoff_reads_zero_env` / `test_default_switch_backoff_uses_production_range_without_env`（`test_routing_pipeline.py:1629-1639`）分别断言 env=0 解析为 `(0.0, 0.0)` 与无 env 时回落生产 `(3.0, 5.0)`。
- **Q-8 瞬态 `RequestError` 无重试退避 — 已关闭**：瞬态 204/404/408/425/5xx 的同号重试在 `context.retry_same_delay_sec > 0` 时执行 `await asyncio.sleep(...)` 并记录退避日志（`routing_pipeline.py:981-989`），与 429 分支口径一致；`test_transient_request_error_retry_same_sleeps_configured_delay[204/503]`（`test_routing_pipeline.py:633-665`）断言只退避一次 2.0s，403 分支仍不退避（`test_auth_retry_same_never_sleeps`，`:600-630`）。
- **Q-9 （可选）204 提示文案不准确 — 已关闭**：`routing_pipeline.py:986-992` 按 `attempt.same_retries` 区分“上游返回 204 空响应”与“上游返回 204 空响应，同账号重试后仍为空”；`test_204_without_retry_reports_plain_empty_response` 覆盖未重试文案。
- **Q-10 （可选）`_try_rebind` 先释放句柄后选号 — 已关闭（按设计）**：`routing_pipeline.py:1141-1162` 现在先用 `_pick_rebind_account()`（`:1236-1257`，只读 lane/model/tier 与排除集）选出目标，再 `attempt.release()`，随后才读 `active_requests`；无可用目标时直接返回且不释放句柄，消除“选号失败空耗句柄”的窗口。注释与本节明确说明：必须先 `attempt.release()` 才能用 `instance.active_requests > 0` 准确判断是否有**其他**并发请求在途（`active_requests` 账本包含本请求自己的句柄）。回归测试 `test_try_rebind_selects_target_before_releasing_handle` 断言选号时 `handle.released is False`、之后才释放；`test_try_rebind_keeps_handle_when_no_target_account` 断言无目标时句柄保持未释放（`test_routing_pipeline.py:1566-1626`）。

### N（细节）

- **N-1（已随 P2-2 关闭）** `_heal_unavailable_instances` 现在仅在 `target_id != live.account_id` 时 `healed += 1` 并打印完成，no-op 不再计入遥测。
- **N-2（已关闭，按设计）** `_switch_backoff` 每次切号打一条 `logger.info`（`routing_pipeline.py:1265-1271`），429 风暴下日志量增加但属预期可观测性；不改变退避行为，按设计保留。
- **N-3（已关闭，按设计）** `begin()` 自愈在请求准入路径内同步等待 `reassign`，但仅在存在不可用槽时触发且已由 `_heal_lock` 串行化；单次 `begin()` 只处理本轮快照内的死槽，按设计接受，不做后台化。
- **N-4（已关闭，已确认无害）** `_heal_unavailable_instances` 的 `exclusions.discard(live.account_id)`（`routing_pipeline.py:1363-1364`）会把该槽自身的死号从排除集移除；选择器内部仍会过滤不可用账号（`account_rotator.py:684-698`），不会选回死号，按设计关闭。

## 4. 与既有决策/红线的一致性

| 检查点 | 结论 | 证据 |
|---|---|---|
| 瞬态/超时不再切号，Free/Pro lane 不混用 | 通过 | `routing_pipeline.py:948-1038`；`reassign` 内 `instance_lane_for_tier != record.lane` 硬拒绝（`instance_manager.py:489-498`） |
| `active_requests` 仍是唯一并发账本，无第二套 | 通过 | 自愈只读 `instance_active_requests`；释放走既有 `RequestHandle`（`instance_pool.py:122-158`），未新增计数 |
| 不恢复蓝绿/后台自动 rebind | 通过 | 自愈仅在请求 `begin()` 同步执行，未启动后台任务；`instance_manager.py` 未见新后台 rebind |
| 不连坐同源兄弟号 | 通过 | `account_orchestrator.py:90-96` 的兄弟传播仅保留在 `session_expired`；`verification_required` 不调用 `_sibling_account_ids` |
| 不跨 lane/不越 tier 区间换绑 | 通过 | `reassign` 双重校验 lane 与 `accepts_tier`（`instance_manager.py:489-498`）；自愈选择器带 `lane/only_tier/min_tier/max_tier/required_tiers`（`routing_pipeline.py:1371-1380`） |
| 实机/真实账号红线 | 未触碰 | 本次未启动服务、未连接 Google；全部为离线单测与静态检查 |

## 5. 门禁命令实跑结果（2026-11-14，本机 Windows / PowerShell 7）

### 5.1 原始只读审查（修复前）

| # | 命令（均带 `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8';`） | 实际结果 | 退出码 |
|---|---|---|---|
| 1 | `uv run ruff check <7 个生产文件 + 5 个测试文件>` | `All checks passed!` | 0 |
| 2 | `uv run ruff format --check <同上 12 个文件>` | `12 files already formatted` | 0 |
| 3 | `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` | 0 |
| 4 | `uv run pytest tests/unit/test_routing_pipeline.py tests/unit/test_account_orchestrator.py tests/unit/test_account_keepalive.py tests/unit/test_account_info_refresh.py tests/unit/test_model_discovery_browserless.py -q` | `125 passed in 3.55s` | 0 |
| 5 | `git diff --check` | 无输出（无空白错误） | 0 |

补充观察（非门禁项）：

- `git status --porcelain` 显示工作树同时包含本次范围内的 12 个文件与范围外的 P10/APK 改动（`android/`、`waa-signer/`、`.gitignore`、`P10-android-apk/`、`waa-signer/cmd/waa-signer/main_test.go` 等，其中部分为未跟踪新文件）。本次仅对白名单文件做语义审查；`pyright src tests` 已覆盖全仓源码与测试。
- 原始审查未执行全量 `uv run pytest`（任务书只要求 5 个白名单测试文件）；闭环复验已执行全量，见 §7.4。

### 5.2 修复闭环后全量门禁（2026-11-14）

| # | 命令 | 实际结果 | 退出码 |
|---|---|---|---|
| 1 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run ruff check .` | `All checks passed!` | 0 |
| 2 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` | 0 |
| 3 | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest` | `1537 passed in 47.90s`（§7.4 同批次复验） | 0 |
| 4 | `git diff --check` | 无输出（无空白错误） | 0 |

## 6. 只读声明（原始审查）

- 原始只读审查**未修改**任何业务代码、测试、配置、脚本或 `.json/.yaml` 文件。
- 原始审查仅写入 3 个文档：本报告、`docs/dev/rebuild/domains/P2-routing/index.md`、`docs/dev/rebuild/domains/INDEX.md`。
- **未执行** `git commit` / `git push` / `git checkout` / `git reset`；**未启动** `main.py server`、真实浏览器或 waa-signer；**未连接**真实 Google 账号或发起真实上游请求。
- 所有结论均给出 `file:line` 与可复现门禁命令；未预填证据，未伪造审计。

> 注：§7 为 2026-11-14 修复闭环的追加记录，由修复 Worker 在实现 P1/P2/Q 修复后写入；它不改变 §6 对“原始审查”的只读声明，也不代表 Reviewer 参与了代码修改。

## 7. 修复闭环记录（2026-11-14）

### 7.1 P1-1 300s 超时真正接入生产装配

| 文件 | 精确位置 | 变更 |
|---|---|---|
| `defaults/config.yaml` | `:36` / `:48` | `request_timeout_s: 300` / `read_timeout_s: 300` |
| `src/aistudio_api/infrastructure/gateway/model_defaults.py` | `:707` / `:756` | 规范化默认 `signer.get("request_timeout_s", 300)` / `transport.get("read_timeout_s", 300)` |
| `src/aistudio_api/infrastructure/waa/engine_runtime.py` | `:146` / `:149` / `:248` | `_ensure_client()` 与 `create_session()` 装配回退统一为 `300` |
| `defaults/config.schema.json` | `:101` / `:148` | `request_timeout_s` / `read_timeout_s` 的 `default` 由 `90`/`120` 同步为 `300` |

- 生产链验证：`_normalize_engine_settings` → `get_configured_engine_settings` → `BrowserlessEngineRuntime._ensure_client()/create_session()` 全部携带 300；不再存在 90/120 覆盖点。
- 单测：`tests/unit/test_engine_runtime_lifecycle.py::test_browserless_runtime_defaults_to_300s_timeouts` 用裸 settings（无显式超时）构造 runtime，断言 `SignerClient.request_timeout_s == 300.0`、`read_timeout_s == 300.0`、`BrowserlessSession._read_timeout_s == 300.0`。
- 说明：`defaults/config.schema.json` 是纯文档 schema，运行期无任何代码读取（`grep config.schema.json` 在 `src/` 无命中）；本次仍按任务书要求把默认值同步为 300，避免分发文档与 `defaults/config.yaml` 漂移。生产生效值以 `defaults/config.yaml` + `model_defaults.py` 为准。

### 7.2 P2-1 / P2-2 死槽自愈并发与 TOCTOU

| 文件 | 精确位置 | 变更 |
|---|---|---|
| `src/aistudio_api/application/routing_pipeline.py` | `:431` | `RoutingPipeline.__init__` 新增 `self._heal_lock = asyncio.Lock()` |
| 同上 | `:1298-1324` | 新增 `_live_instance()`：优先 manager `get_instance`，否则 `lane_instances` 重读最新绑定账号/状态 |
| 同上 | `:1327-1416` | `_heal_unavailable_instances` 全流程进入 `async with self._heal_lock`；锁内逐槽重读 `live`；`live.active_requests > 0`（经 `instance_active_requests`）直接跳过；仅 `target_id != live.account_id` 时 `healed += 1` |

- 单测：
  - `test_heal_skips_slot_that_became_busy_after_snapshot`：快照后槽位出现在途请求，`healed == 0` 且 `reassign_calls == []`（不会 drain 同槽其他模型请求）。
  - `test_heal_rereads_live_account_and_skips_rebound_slot`：快照后槽位已被并发请求改绑到健康账号，`healed == 0` 且无重复改绑。
  - 原有 `test_begin_heals_idle_slot_left_on_unavailable_account`、`test_begin_does_not_heal_slot_with_active_requests` 继续通过。

### 7.3 Q-1 ~ Q-6、Q-9 关闭证据

| 项 | 精确位置 | 修复与验证 |
|---|---|---|
| Q-1 | `routing_pipeline.py:948-998` | 删除原 `:1029-1031` 不可达的重复 `RequestError` 分支；全文件仅剩一处 `isinstance(exc, RequestError)` |
| Q-2 | `routing_pipeline.py:1164-1174`、`:1259-1271` | 429/verification 退避移到 `reassign` 之前且不受 `record_budget` 限制；`test_429_rebind_waits_randomized_switch_backoff` 断言 `order == ["sleep","reassign"]`；`test_exhausted_budget_rebind_still_waits_switch_backoff` 断言预算耗尽仍退避 |
| Q-3 | `domain/errors.py:313`、`:317` | 识别 `SignerTimeoutError` 类名与中文“超时”；`test_raw_signer_timeout_error_never_switches` 更新为首次 `retry_same`、二次抛出、`switches == 0` |
| Q-4 | `account_rotator.py:651-661` | 新增公共 `is_auth_locked(account_id)`，读取 `get_account_stats().session_expired`；`test_rotator_is_auth_locked_tracks_session_expiry` 覆盖 |
| Q-5 | `api/app.py:566-572` | `AccountKeepaliveService(..., rotator=rotator)` 显式注入；`account_keepalive.py:192-201` 首选分支生效 |
| Q-6 | `model_discovery.py:255-331` | `_active_rotator()` 经 `sys.modules` 读取全局 rotator（避免 infrastructure→application 静态导入，`test_layer_boundaries` 通过）；`_auth_locked()` 在排序/截断前过滤锁定账号；`test_account_http_candidates_skips_auth_locked_accounts` 断言锁定 Ultra 后为 `Pro, Plus` |
| Q-9 | `routing_pipeline.py:991-997` | 按 `attempt.same_retries` 区分文案；`test_204_without_retry_reports_plain_empty_response` 断言未重试时精确为“上游返回 204 空响应” |

### 7.4 全量门禁复验

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check src/aistudio_api/application/routing_pipeline.py tests/conftest.py tests/unit/test_routing_pipeline.py` | `3 files already formatted` |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| `uv run pytest tests/unit/test_routing_pipeline.py -q` | `54 passed in 0.51s` |
| `uv run pytest -q` | `1537 passed in 47.90s` |
| `git diff --check` | 无输出（无空白错误） |
| `uv run python -c "import json,pathlib; json.loads(...)"` | `schema json ok` |

### 7.5 Q-7 / Q-8 / Q-10 与 schema 默认值关闭证据

| 项 | 精确位置 | 修复与验证 |
|---|---|---|
| Q-7 | `routing_pipeline.py:345-378`、`tests/conftest.py:167` | 删除 `PYTEST_CURRENT_TEST` 探测与 `TEST_SWITCH_BACKOFF_RANGE` 常量；autouse fixture 统一 `AISTUDIO_SWITCH_BACKOFF_SECONDS=0`；`test_default_switch_backoff_reads_zero_env` / `test_default_switch_backoff_uses_production_range_without_env`（`test_routing_pipeline.py:1629-1639`） |
| Q-8 | `routing_pipeline.py:981-989` | 瞬态 `RequestError` 同号重试在 `retry_same_delay_sec > 0` 时 `await asyncio.sleep(...)` 并记录退避日志；`test_transient_request_error_retry_same_sleeps_configured_delay[204/503]` 断言只退避一次 2.0s；403 仍不退避 |
| Q-10 | `routing_pipeline.py:1141-1162`、`:1236-1257` | 先 `_pick_rebind_account()` 选号，再 `attempt.release()`，随后才用 `active_requests > 0` 判断其他并发请求；无目标时不释放句柄（按设计关闭）；`test_try_rebind_selects_target_before_releasing_handle` / `test_try_rebind_keeps_handle_when_no_target_account`（`test_routing_pipeline.py:1566-1626`） |
| schema | `defaults/config.schema.json:101`、`:148` | `request_timeout_s` / `read_timeout_s` 默认值由 `90`/`120` 同步为 `300`；JSON 解析校验通过 |

- Q-10 设计说明：`active_requests` 账本包含本请求自己的句柄，因此必须先 `attempt.release()`，之后 `instance.active_requests > 0` 才表示“有其他并发请求在途”。选号本身不依赖 `active_requests`，所以可以安全地先选号再释放。

### 7.6 残留项（已清零）

- 无 P0/P1/P2/Q/N 残留。Q-4 后半的独立 `verification_locked` 状态属后续协议演进，不是本轮 Q 残留，已按设计关闭。
- N-1~N-4 均已在 §3 标记关闭：N-1 随 P2-2 修复；N-2/N-3 为按设计接受的可观测性/准入成本；N-4 已确认选择器不会选回死号。
