# REV-2.1.5.14 — 出口管理器 v2 预算/调度/烧毁只读审查

- 审查日期：2026-11-23
- 审查角色：独立只读 Reviewer
- 审查对象：`data/tools/exit-manager/`（gitignored）管理器 v2：
  - `exit_manager/{config,state,manager,server,replenish}.py`
  - `config.example.json`、`DEPLOY.md`、`tests/`
- 定稿参数基线：fanout 上限 4（paid 2 + 热备 1 + free_overflow 1）、WARP 目标 3 / 上限 6；paid 30/45/400、free 50/75/2400；维护预留 20、探针 6；烧毁 30min 内 ≥2 个不同账号 verify → 120min；无容量 429 + `retry_after`。
- 结论：**CONDITIONAL**
- 依据：`AGENT.md`、`docs/dev/rebuild/06-decision-log.md`、`docs/dev/rebuild/08-09-decision-record.md`（D-01..D-14）、`docs/dev/rebuild/domains/P1-account/research/exit-pool/{PLAN,CONTRACT}.md`、`REV-2.1.5.12/13`、管理器真实代码与实跑门禁、app 侧 `src/aistudio_api/infrastructure/exit_pool/resolver.py` 契约比对（只读）。

## 0. 审查边界与工作区说明

- 唯一写操作：本文件。未改代码、未 commit、未启动服务/浏览器、未访问 VPS/外网/真实账号。
- 允许的只读命令：`read/grep`、`python -m unittest discover -s data/tools/exit-manager/tests -v`、`python main.py --config ... --check-config`（本地、无状态落盘）。
- 工作区状态：`git status --short` 仅有既存未跟踪文档（`research/exit-pool/PLAN.md`、`REV-2.1.5.12`、`REV-2.1.5.13`）；`data/tools/exit-manager/state/` 未被 `--check-config` 创建（`Test-Path` 为 `False`）。
- 未运行 `ruff/pyright/pytest` 全仓门禁（本轮对象是 gitignored、纯 stdlib 的独立工具；按要求只跑其自带 unittest 与 `--check-config`）。

## 1. 结论

**CONDITIONAL（不建议直接写“可联调/可上线”）**。

管理器 v2 的主链方向正确：paid/free/free_overflow 三车道隔离、窗口/日计数、软/硬阈值、维护/探针预留、新绑定软/已绑定硬、429 + `Retry-After`、Bearer + 客户端 CIDR、事件幂等、整组迁移、verify 连坐烧毁、原子写与重启恢复均已落地，137 个 unittest 全绿。

仍有 **0 个 P0 / 1 个 P1 / 5 个 P2**：

1. **P1-1 状态语义混用**：`verify_account` 连坐烧毁会把已经 `quarantined`（尤其 region 403 隔离）的出口改写成 `cooling`，冷却到期后 `_refresh` 直接归为 `healthy`，绕过 `POST /v1/admin/recover` 与探活（`manager.py:1028-1054, 753-794, 269-281`；`DEPLOY.md:349, 452-453`）。
2. **P2-1 日预算可被生成吃掉**：`_budget_ok` 先检查共享 `daily_total`，再检查维护/探针预留；生成打满日上限后，维护/探针（含已绑定账号）到 UTC 午夜前全部 429，且已绑定维护不会溢出到其它出口（`manager.py:399-400, 634-635, 439-441`）。
3. **P2-2 fanout 上限与定稿不一致**：定稿“fanout 上限 4（paid 2+热备 1+overflow 1）”，但 `paid_cap=4` + `overflow_cap=1` = 最多 5 条 fanout（`config.py:70-75`、`config.example.json:53-58`）。
4. **P2-3 `replenish_tick` 无生产调用方**：`main.py`/`HealthLoop`/`server.py` 都不调用，配置的补充命令在真实进程里永远不会执行；`last_replenish_at` 恒为 0（`manager.py:1154-1180`；仅 `tests/test_replenish.py:185` 调用）。
5. **P2-4 overflow 车道不被补充**：`replenish_tick` 只遍历 `paid/free`，`overflow_target/overflow_cap` 只进 `/v1/status`，不驱动任何补充（`manager.py:1165`、`config.py:74-75, 81-86`）。
6. **P2-5 租约幂等仅进程内、键不隔离、保留期与 TTL 不一致**：`request_id` 缓存不持久化、按全局 id 命中（跨账号复用会拿到别人的 `socks_url`），且保留 `budget.window_s=1800s` 而租约 `ttl_s` 仅 30~60s（`manager.py:103, 665-681, 282-286`）。

## 2. 必查项逐条结论（A–J）

> 记号：PASS=符合；P0/P1/P2=缺陷；Q/N=观察/提示。

### A. 车道隔离

- **PASS（paid 只落 paid fanout）**：`_exit_serves_lane` 对 `request_lane=paid` 仅返回 `exit_lane=="paid"`（`manager.py:336-344`）；测试 `test_paid_only_uses_paid_fanout`、`test_paid_without_paid_exit_is_no_capacity`（`tests/test_lease.py:120-137`）。
- **PASS（free 只落 WARP + 条件 overflow）**：free 请求只接受 `lane=free`，`free_overflow` 仅在 `overflow_ok` 时可用（`manager.py:340-343, 346-351`）；`free_overflow` 的启用条件是“无 healthy free + 维护积压 >0”，测试 `test_overflow_only_after_all_free_down_and_backlog`（`tests/test_lease.py:152-165`）。
- **PASS（未知/空 lane 安全）**：配置缺省按 kind 推导（fanout→paid、warp→free），显式非法/空值在加载期 `ConfigError`（`config.py:251-259`）；运行期 `/v1/lease`、`/v1/assign` 只接受 `paid|free`，`free_overflow` 作为请求 lane 被拒（`manager.py:313-317`、`tests/test_lease.py:100-102`）。
- **PASS（不互相串道）**：paid 不会落 WARP（`exit_lane=="free"` 对 paid 返回 False）；free 不会落 paid fanout（`exit_lane=="paid"` 对 free 返回 False）。绑定迁移按 `binding.lane` 选目标（`manager.py:514-544`）。
- **Q-1（overflow 的触发口径偏窄）**：`_overflow_ok` 只看“是否存在 healthy free 出口”，不看其预算是否耗尽。WARP 全部 healthy 但窗口/日预算打满时，free 生成直接 429，不会临时走 `free_overflow`（`manager.py:346-351`）。若“WARP 全不可用”只指进程/网络不可用，这是预期；若包含“预算不可用”，则是缺口，需确认。

### B. 预算

- **PASS（窗口/日计数）**：`ExitBudget.events` 记 `[ts, class, count]`，`prune` 按滑动窗口裁剪；`day_key`/`daily_total` 按 UTC 日滚动（`state.py:107-160`）；测试 `test_window_counting_and_rollover`、`test_daily_counting_and_midnight_reset`（`tests/test_lease.py:221-251`）。
- **PASS（软/硬阈值）**：新绑定 `ceiling=soft`，已有绑定 `ceiling=hard`（`manager.py:414-418`）；测试 `test_new_binding_soft_cap_and_bound_hard_cap`（`tests/test_lease.py:172-187`）。
- **PASS（窗口级维护/探针预留）**：生成预留扣除 `maint_reserve+probe_reserve`，维护/探针各自受 reserve 限制（`manager.py:402-421`）；测试 `test_generation_cannot_eat_maint_or_probe_reserve`（`tests/test_lease.py:194-214`）。
- **PASS（按 calls 计数、幂等不重复扣）**：`budget.add(now, cls, calls)` + `daily_total += calls`（`manager.py:634-635`）；同 `request_id` 在进程内直接返回缓存结果、不重复扣（`manager.py:568-575`）；测试 `test_same_request_id_is_not_charged_twice`（`tests/test_lease.py:258-270`）。
- **P2-1（日上限共享，维护/探针无日预留）**：`_budget_ok` 第一条就是 `daily_total + calls > daily_limit`，对所有 class 生效（`manager.py:399-400`），且 `daily_total` 由生成/维护/探针共用（`manager.py:634-635`、`state.py:119`）。生成打满日上限后，维护/探针在 UTC 午夜前全部 429；已绑定账号的维护又不会尝试其它出口（`manager.py:586-632`），因此无法绕过。详见 §3 P2-1。
- **Q-3（soft 扣除预留后的新绑定生成上限偏小）**：paid 新绑定 `generation_cap = soft(30) - reserved(26) = 4`，已绑定 `hard(45)-26=19`（`manager.py:414-418`）。这是“总量阈值包含预留”的自洽解释，但运维若把 `soft=30` 理解为“新账号每窗口可生成 30 次”会严重低估；`/v1/status.window_remaining = soft - total`（`manager.py:1195-1197`）同样高估可用生成量。需确认口径。
- **Q-4（维护不能借用未用生成空间）**：维护严格受 `used(maint)+calls <= maint_reserve=20`（`manager.py:402-407`），与 `PLAN.md:427`“维护先用预留，再用未用生成空间”不同；`DEPLOY.md:265` 则写明“只能吃各自预留”。按 DEPLOY 口径实现正确，但 41 个 free 账号约 41 次/出口/周期的维护需要 ≥3 个窗口，不是“两波 20~30 分钟”的乐观估算，需确认最终口径。

### C. 租约契约

- **PASS（字段）**：成功响应 `exit_id/socks_url/generation/ttl_s/budget_left`（`manager.py:458-468`），与 app 侧 `_parse_lease` 一致（`resolver.py:623-641`）。
- **PASS（ttl 30~60）**：`_lease_ttl` 夹在 `budget.lease_min/max_ttl_s`（默认 30/60，`config.py:58-59`；`manager.py:452-456`）；测试 `tests/test_lease.py:88-89`、`tests/test_server.py:402-404`。
- **PASS（429 + retry_after）**：`_no_capacity` 返回 `error/no_capacity + retry_after_s + lane + class`，并夹在 60~600s（`manager.py:423-450`）；服务端同时写 `Retry-After`（`server.py:199-206, 236-243`）；测试 `tests/test_server.py:406-415`。
- **PASS（Bearer / loopback / 错误码）**：先客户端 CIDR、后 Bearer（`server.py:121-147, 83-90`）；400/401/403/404/405/409/429/202 路径齐全（`server.py` 各 handler）；测试 `tests/test_server.py` 覆盖 401/403/404/405/429。
- **PASS（`/v1/assign` 不重复计数）**：`_handle_assign` 只调用一次 `manager.lease`（`server.py:184-216`），没有额外再计一次；测试 `test_assign_returns_contract_shape_and_is_sticky`（`tests/test_server.py:106-121`）。
- **P2-5（幂等不持久、键不隔离、保留期过长）**：见 §1/§3。
- **Q-7（`/v1/assign` 无 request_id）**：每次 GET 都计 1 次 generation（`server.py:194-195`），重复 GET 会重复扣；若旧客户端会轮询，需确认是否预期。
- **Q-6（重申 REV-2.1.5.13 Q-6）**：`/v1/lease` 不读取 `kind`，绑定恒为 `chat`（`server.py:218-244`、`manager.py:627`）。app 客户端也不发 `kind`（`resolver.py:531-536`），当前不影响分配；仅是元数据口径。

### D. 烧毁 / 迁移

- **P1-1（verify 烧毁覆盖 quarantine → 绕过人工 recover）**：`record_event` 对任何 `kind=="verify_account"` 都调用 `_record_verify_burn`，不区分 canary 与出口状态（`manager.py:1028-1030`）；`_record_verify_burn` 只排除 `disabled`（`manager.py:1046-1047`），随后 `_burn` 无条件把状态置为 `cooling`（`manager.py:753-794`）；`_refresh` 冷却到期直接 `healthy`、不探活（`manager.py:269-281`）。这与 `DEPLOY.md:349`“quarantined 出口……直到 `POST /v1/admin/recover`”以及 `DEPLOY.md:452-453`“惰性归位”叠加后，等价于 **region/quarantine 可以被 verify 连坐自动解除**。详见 §3 P1-1。
- **PASS（不同账号/时间窗/幂等）**：`verify_events` 按不同 account 去重计数，窗口 `verify_cluster_window_s=1800s`，阈值 2（`manager.py:1033-1044`、`config.py:56-57`）；事件摘要含 `account|exit|kind|canary|ts`，重复上报返回 `duplicate` 且持久化（`manager.py:991-1003`、`tests/test_manager_events.py:87-116`）；单账号重复 verify 不烧（`tests/test_lease.py:306-311`）。
- **PASS（整组迁移）**：`quarantine`/`_burn` 都调用 `_migrate_group`，按 `binding.lane` 迁移全部绑定并保留 `kind`（`manager.py:514-544, 723-727, 776-779`；`tests/test_manager_assign.py:97-120, 223-232`）。
- **PASS（region quarantine 与 verify burn 的代码路径分离）**：region 403 仅在 `canary=exit_bad` 时 quarantine（`manager.py:1015-1022`），verify 连坐走 `_burn`；两者状态分别为 `quarantined`/`cooling`。唯一混用点就是 P1-1。
- **Q-2（冷却到期不探活）**：`cooling→healthy` 无 L0/L1 探活（`manager.py:269-281`），`DEPLOY.md:452-453` 已列为已知限制。对纯 verify 冷却可接受；修复 P1-1 后仍建议至少 L0 或一次 canary 再放回。
- **Q-5（app 尚未产生 verify_account 事件）**：全仓 `src/**/*.py` 无 `event_kind="verify_account"` 调用；仲裁只上报 `region_403`（`exit_arbitration.py:339-361`）。因此连坐烧毁目前只有管理器的单测覆盖，端到端未接通，联调时需先确认事件生产者。
- **N-2（窗口锚点取事件 ts）**：`_record_verify_burn` 用入站事件的 `ts` 作为“现在”做 `prune`/过滤（`manager.py:1037-1042`）；乱序旧事件可能与库中较新事件凑成“同一窗口”而误烧。app 发送当前 `int(time.time())`（`resolver.py:761-767`），风险低，但建议锚定 `self.clock()` 并加 `now - ts <= window`。

### E. 补充（replenish）

- **PASS（target/cap、5min 冷却、每小时 3 次）**：`can_replenish` 先 target 后 cap，`cooldown_s=300`、`hourly_limit=3`（`replenish.py:50-66`、`config.py:76-77`）；测试 `tests/test_replenish.py:64-110`。
- **PASS（候选必须验证、未配置命令只告警）**：`admit` 无 verifier 或 verifier 抛异常一律拒绝（`replenish.py:138-151`）；无命令时 `attempt` 只 `warning` 并返回 `no_command`（`replenish.py:95-101`）；测试 `tests/test_replenish.py:55-62, 135-148`。
- **PASS（命令注入面）**：`_expand` 只替换 `{slot}`/`{region}`，`slot` 为 int、`region` 在管理器内恒为 `"auto"`，且 `subprocess.run(argv)` 不走 shell（`replenish.py:68-84, 86-119`）。同理 `rotate` 的 `{instance}`/`{id}` 分别来自 int 与 `EXIT_ID_RE` 校验值，且无 shell（`manager.py:1058-1085`、`config.py:22`）；`fanout.swap_node` 同理（`fanout.py:80-101`）。
- **PASS（状态字段）**：`/v1/status.lanes.{paid,free,free_overflow}.{healthy,target,cap,window_remaining,daily_remaining}`、`maintenance_queue`、`last_replenish_at`（`manager.py:1191-1224`）；测试 `tests/test_server.py:303-343`。
- **P2-3 / P2-4**：`replenish_tick` 无生产调用方；且只遍历 paid/free，overflow 车道不补充。详见 §3。

### F. 持久化 / 并发

- **PASS（原子写）**：`tempfile.mkstemp` + `fsync` + `os.replace`，状态目录 0700、文件 0600（`store.py:17-36, 42-67`；`manager.py:93-98`）；测试 `tests/test_store.py`。
- **PASS（重启恢复）**：`ExitBudget.restore` 恢复窗口事件/日计数并按当前时间裁剪；`probing` 归一、过期 `cooling` 归一、`disabled` 保持、socks_url 变更 bump generation（`manager.py:111-221`；`tests/test_lease.py:363-378`、`tests/test_manager_assign.py:152-180`、`tests/test_manager_events.py:229-272`）。
- **PASS（窗口/日重置）**：`_refresh` 每次 lease/status 都 `roll_day`+`prune`（`manager.py:263-288`）；`budget.roll_day` 按 UTC 日（`state.py:125-129`）。
- **PASS（单写者 / 锁范围）**：所有读写路径统一 `threading.RLock`（`manager.py:90, 565-643, 694-751, 798-876, 882-924, 994-1054`）；`quarantine` 内的 RLock 可重入；文档明确单写者（`DEPLOY.md:390`）。
- **Q-8（维护积压不持久）**：`_maint_backlog` 仅内存（`manager.py:105, 645-647, 1223`），重启后 `maintenance_queue` 归零，进而 `_overflow_ok` 关闭 overflow，直到下一次维护失败；这是“故障后重启”场景的隐含行为，需确认可接受。
- **N-1（冷却归位残留字段）**：`_refresh` 冷却→healthy 未清 `quarantined_at`/`last_error`（`manager.py:274-281`）；若曾被 quarantine 再 burn，恢复后 `/v1/status` 之外的 snapshot 仍带旧隔离时间。建议归一。
- **N-7（探活失败→隔离之间的窗口）**：`apply_probe_result` 在“失败达到阈值”的 `_save_exits` 后释放锁，再调用 `quarantine`（`manager.py:907-924`）；期间一个 lease 可能进入该失败出口一次。影响小，但建议在同一锁内完成或置 `probing`。
- **N-8（未捕获的持久化异常）**：HTTP handler 只捕获 `ValidationError/NotFoundError/ConflictError`；`_save_exits` 的 `OSError` 会直接打断请求（`server.py:218-271`）。app 侧会按 manager unreachable/fail-closed 处理；建议至少返回 500 JSON 并记录。

### G. 状态白名单

- **PASS（不泄露 socks 凭据）**：`/v1/status` 只返回汇总计数，不含 `socks_url`/token/出口 id/账号（`manager.py:1184-1224`；`tests/test_server.py:303-343` 断言 `socks`/token/account/`warp-1` 均不出现）；`/healthz` 只返回计数（`server.py:129-143`）。
- **PASS（仅 lease/assign 内部返回 socks_url）**：成功租约/分配返回 `socks_url`（`manager.py:458-468`、`server.py:207-216`），符合“仅调用方可见”的设计。
- **N-3（状态文件含 socks_url）**：`exits_state.json` 保存 `socks_url`（含 fanout `user:pass`）用于 socks 变更检测（`manager.py:229-241`）。文件 0600/目录 0700，非 API/日志泄露；若该文件会被备份/导出，需按凭据处理。
- **PASS（日志脱敏）**：探测错误用 `safe_url` 去 userinfo/query（`util.py:48-62`、`health.py:189-204`）；HTTP 访问日志只记 path、去 query（`server.py:33-34, 49-55`）；账号只记截断 tag（`util.py:65-69`、`manager.py:493-512`）。

### H. 测试质量

- **PASS（数量与真实性）**：实跑 137 用例全绿（见 §5）；抽查 12 组均为真实断言、无空跑：
  1. 车道隔离：`tests/test_lease.py:120-137`；
  2. overflow 触发：`tests/test_lease.py:152-165`；
  3. 预留不被吃：`tests/test_lease.py:194-214`；
  4. 429 + Retry-After：`tests/test_server.py:406-415`、`tests/test_lease.py:179-187`；
  5. 幂等：`tests/test_lease.py:258-270`；
  6. 整组迁移 / kind 保留：`tests/test_manager_assign.py:97-120, 223-232`；
  7. verify 连坐烧毁 / 单账号不烧：`tests/test_lease.py:284-311`；
  8. 冷却到期恢复：`tests/test_lease.py:315-332`；
  9. 重启恢复（窗口/绑定/事件摘要）：`tests/test_lease.py:363-378`、`tests/test_manager_assign.py:152-157`、`tests/test_manager_events.py:87-116`；
  10. 补充冷却/每小时上限：`tests/test_replenish.py:83-110`；
  11. 日计数与 midnight：`tests/test_lease.py:237-251`；
  12. 状态白名单：`tests/test_server.py:303-343`。
- **PASS（fake 类型一致）**：`FakeClock` 为 `Callable[[], float]`（`tests/support.py:33-41`）；`ScriptedProbe` 返回 `ProbeResult`（`tests/support.py:54-64`）；`RecordingRunner`/`FakeRunner` 返回带 `returncode/stdout/stderr` 的 duck type（`tests/test_replenish.py:30-38`、`tests/test_fanout.py:65-79`）；`FakeOpener/FakeResponse` 具备 context manager + `read(size)`（`tests/test_fanout.py:38-62`）。均与生产 `clock/probe/subprocess.run/urlopen` 的使用面一致。
- **PASS（离线）**：测试只 bind `127.0.0.1:0`（`tests/support.py:182-195`）、只对 `127.0.0.1:1` 做一次本地拒连（`tests/test_health.py:113-116`）；无外网、无真实浏览器/账号。
- **测试缺口**（对应缺陷/设计确认）：
  - 无“verify 连坐不能解除 quarantine”的用例（P1-1）；
  - 无“生成打满日上限后维护仍可前进”的用例（P2-1）；
  - 无 lease 幂等跨重启/保留期/跨账号复用用例（P2-5）；
  - 无 `paid_cap + overflow_cap` 与定稿上限的断言（P2-2）；
  - 无 `replenish_tick` overflow 车道/生产接线的用例（P2-3/P2-4）；
  - 无 `calls>1` 单次租约计数断言；无 `_overflow_ok` 在“free healthy 但预算耗尽”下的断言（Q-1）；
  - `tests/test_replenish.py:185` 是唯一 `replenish_tick` 调用，掩盖了生产未接线。
- **N-6（typing 细节）**：`tests/support.py:59` 的 `-> ProbeResult` 未 import，因文件顶部 `from __future__ import annotations` 未求值，不影响运行；可清理。

### I. 与 PLAN / REV 的差异

- **PASS（paid/free 预算）**：v2 实现 paid 30/45/400、free 50/75/2400（`config.py:50-60`），与定稿一致；`PLAN.md:95-97` 仍是旧的“全出口 50/75/2400 + per-account 12/400”，属已计划更新的文档漂移（会话交接明确下一步更新 `PLAN.md`）。
- **PASS（429 而非 503）**：v2 统一 429 + `retry_after`（`manager.py:423-450`），与 `REV-2.1.5.13` 和 app 客户端一致（`resolver.py:555-621`）；`CONTRACT.md:125-127` 仍写 503 `no_healthy_exit`，属文档漂移。
- **PASS（overflow 车道）**：`PLAN.md` 无独立 overflow；v2 新增 `free_overflow` 并以“WARP 全不可用 + 维护积压”为门（`manager.py:346-351`）。方向合理，Q-1 是口径细化。
- **Q（无 per-account 配额）**：`PLAN.md:97,143-144` 有 `per_account_window_cap=12 / daily=400`；v2 完全不实现（`state.py`/`manager.py` 无该字段）。定稿未列出 per-account 配额，视为有意删除；风险是单个账号可吃满某出口的生成上限（paid 约 19/窗口），需确认接受。
- **Q（无分级冷却）**：`PLAN.md:158` 曾计划 `min(上次×2, 14400s)`；v2 固定 7200s，`burn_count` 只记录不使用（`state.py:209`、`manager.py:772`）。定稿为固定 120min，属有意简化；`burn_count` 可留作观测或后续启用。
- **Q（overflow 分组未实现）**：`PLAN.md:275` 曾讨论维护溢出/分组；v2 只有单条 `free_overflow` 车道、无分组。定稿未要求，记录为已知限制。
- **P2-2（fanout 上限口径）**：`PLAN.md:164` 旧值 `free 4 / paid 2`；定稿为 fanout 总上限 4（paid 2+热备 1+overflow 1）、WARP 3/6。v2 的 `paid_target=3`/`free_target=3`/`free_cap=6` 对齐定稿，但 `paid_cap=4` 与 overflow 1 相加为 5，需修正或改口径。
- **Q-10（文档漂移清单）**：`PLAN.md`（预算/503/per-account/分级冷却）、`CONTRACT.md:118,125,132`（ttl 300、503、app 缓存基线）、`DEPLOY.md:194`（`AISTUDIO_EXIT_TTL` 与 `ttl_s=300` 对齐）与 v2 的 30~60s 租约、429 语义不一致；建议随 Stage 1 一并更新，避免联调按旧契约断言。

### J. 实跑门禁与 `--check-config`

- **PASS（unittest）**：`python -m unittest discover -s data/tools/exit-manager/tests -v` → `Ran 137 tests` / `OK`（详见 §5）。
- **PASS（真入口 `--check-config`）**：`python data/tools/exit-manager/main.py --config data/tools/exit-manager/config.example.json --check-config` → `config OK: ...`，退出码 0（详见 §5），未创建 `state/`。
- **N-4（`python -m exit_manager --check-config` 不支持）**：包内无 `__main__.py`，仓库根执行报 `No module named exit_manager`，工具目录内执行报 `No module named exit_manager.__main__`。交付文档用的是 `python3 main.py ...`（`DEPLOY.md:104`），与本轮要求的 `-m` 形式不一致；建议补 `exit_manager/__main__.py` 或在审查/联调文档统一用 `main.py`。
- **N-4b（check-config 输出粒度）**：只打印 `listen/exits/health/interval/state`，不打印 lane/budget/上限（`main.py:91-103`）；建议补一行 `lanes=`/`budget=` 便于部署核对定稿参数。

## 3. P0/P1/P2 详情

### P1-1 verify 连坐烧毁覆盖 quarantine，绕过人工 recover 与探活

- 位置：
  - `manager.py:1028-1030`：`if kind == "verify_account": if self._record_verify_burn(...)`，对 canary 与当前状态不设前置条件。
  - `manager.py:1033-1054`：`_record_verify_burn` 只排除 `STATUS_DISABLED`（1046-1047），未排除 `STATUS_QUARANTINED`/`STATUS_COOLING`。
  - `manager.py:753-794`：`_burn` 无条件 `state.status = STATUS_COOLING`。
  - `manager.py:269-281`：`_refresh` 在 `now >= cooldown_until` 时直接 `HEALTHY`，不探活。
  - `DEPLOY.md:349`：`quarantined` 出口“直到 `POST /v1/admin/recover`”；`DEPLOY.md:452-453`：冷却“惰性归位，未强制重新 L1/canary”。
- 复现（本地 unittest 即可构造，不需要真实出口）：
  1. 配置两个 WARP 出口，`budget` 用 20/40；
  2. `lease(acct1/free/gen)`、`lease(acct2/free/gen)` 都落 `warp-1`；
  3. `record_event(region_403, canary=exit_bad, exit=warp-1, ts=T)` → `warp-1` 进入 `quarantined`，绑定迁移到 `warp-2`；
  4. `record_event(verify_account, canary=inconclusive, acct1, warp-1, ts=T+1)`；再 `record_event(... acct2 ..., ts=T+2)` → 不同账号达到阈值 2 → `_burn` → `warp-1` 变为 `cooling`；
  5. `clock.advance(7201)`；下一次 `lease(acct3/free/gen)` → `warp-1` 以 `healthy` 被重新分配，期间没有 `recover`、没有 L0/L1。
  - 更短路径：先一个 verify（acct1），再 `region_403 exit_bad`（quarantine），再 verify（acct2）→ 同一事件把 quarantine 直接烧成 cooling。
- 影响：region 403 的“人工确认后再恢复”语义被打破；一个已被判定出口级 region 封锁的 IP 可能在 120min 后无探活回到池中，继续触发 verify/region 风控。属于状态机安全缺陷，非纯计数偏差。
- 建议：
  1. 在 `_record_verify_burn` 中把 `state.status != STATUS_HEALTHY` 作为“只记录事件、不升级为 burn”的前置条件（至少排除 `QUARANTINED`；`COOLING` 是否允许续期单独定）；
  2. 或在 `_burn` 中保留 `was == STATUS_QUARANTINED` 的隔离态，仅累加 `burn_count`/`cooldown_until` 供观测，不把状态降级为 `cooling`；
  3. `_refresh` 的 `cooling→healthy` 对“曾有 `quarantined_at` 且非纯 verify 烧毁”的出口要求一次探活/人工确认；
  4. 增加回归测试：quarantine 后 2 个不同账号 verify 仍为 `quarantined`；纯 verify 烧毁在冷却后仍按设计归位。

### P2-1 日预算共享，生成可打满并阻塞维护/探针到 UTC 午夜

- 位置：`manager.py:399-400`（日检查先于 class 检查、对所有 class 生效）；`manager.py:634-635`（所有 class 都加 `daily_total`）；`state.py:117-119`（单一 `daily_total`）；`manager.py:439-441`（`_no_capacity` 的等待集合）。
- 复现：
  1. 单出口 `budget(soft=5, hard=5, daily=2, maint=0, probe=0)`；
  2. `lease(acct1 gen)`、`lease(acct2 gen)` 成功（daily=2）；
  3. `lease(acct1 maint)` → `no_capacity`（`reason` 为 `daily`），即使窗口未满；
  4. 推进 600s/1800s 仍 `no_capacity`；只有跨过 UTC 午夜 `roll_day` 后才恢复。
  - 已绑定账号的维护（`binding` 指向该出口）不会尝试其它出口（`manager.py:586-632`），因此在单出口/全出口日满时无法通过换出口绕过。
- 影响：若日上限被视为“运维护栏”（`PLAN.md:129`），这可能是有意行为；但它与“维护/探针永远有前进空间”的窗口预留目标叠加时，会把维护/凭证更新推迟到次日，且 app 的 defer 会以 ≤600s 间隔反复重试（`manager.py:442-444`），看不到进展。
- 建议：明确口径并二选一——① 日上限也按 class 预留（至少给维护/探针一个日切片，或让维护/探针不计入日上限）；② 保持“日上限是硬护栏”，在 `_no_capacity` 返回可区分的 `daily_exhausted` 并把 `retry_after` 指向午夜（app 侧应识别为“次日再试”而非短退避）。补一条“日满后维护行为”的测试，避免后续改动回归。

### P2-2 fanout 上限 4 与 `paid_cap=4 + overflow_cap=1` 不一致

- 位置：`config.py:70-75`（`paid_target=3`、`paid_cap=4`、`overflow_target=1`、`overflow_cap=1`）；`config.example.json:53-58`；`tests/test_config.py:40-41` 只断言 `paid_target/free_cap`。
- 事实：定稿“fanout 上限 4 = paid 2 + 热备 1 + free_overflow 1”，即 paid 车道最多 3 + overflow 1；当前 `paid_cap=4` + `overflow_cap=1` 允许 5 条 fanout。样例配置只定义了 2 条 paid + 1 条 overflow，所以当前不会超；但一旦接线补充（P2-3），`paid_target=3` 会补到 3，`paid_cap=4` 仍允许再补 1 条。
- 建议：把 `paid_cap` 改为 3（若 4 是总上限），或在定稿/文档中明确“paid_cap 4 是 paid 车道上限，overflow 不计入 4”；增加 `paid_cap + overflow_cap == 4` 的配置断言，防止参数漂移。

### P2-3 `replenish_tick` 无生产调用方

- 位置：`manager.py:1154-1180` 定义；`main.py:117-130, 151-160` 只启动 `HealthLoop`；`health.py:228-241` 只 `refresh_fanout`+probe；`server.py:129-175` 无 replenish 路由。全仓唯一调用是 `tests/test_replenish.py:185`。
- 影响：`replenish.paid_command/free_command` 即使在配置里写好，真实进程也永远不会执行；`last_replenish_at` 恒为 0；`DEPLOY.md:448-450`“已实现并带 5 分钟冷却/每小时 3 次上限”只描述了组件，没有描述接线。若“仅骨架、不接线”是有意交付，应在 DEPLOY/配置注释里显式写明“不会自动运行，仅供后续接线”；否则这是交付缺口。
- 建议：在 `HealthLoop.run_once` 末尾（或独立定时器）调用 `manager.replenish_tick()`，把返回决策写日志并按需暴露；或明确删除/标注为 manual-only，避免运维以为配置即生效。

### P2-4 overflow 车道不被补充

- 位置：`manager.py:1165` 只遍历 `(LANE_PAID, LANE_FREE)`；`config.py:74-75, 81-86` 的 `overflow_target/overflow_cap` 只被 `status_summary` 读取（`manager.py:1201-1208`）。
- 影响：`free_overflow` 作为“全部 WARP 不可用时的应急出口”，一旦它自己挂掉/被隔离，没有任何自动补充决策；`overflow_target=1` 只是状态展示值。
- 建议：把 `LANE_FREE_OVERFLOW` 纳入 tick（使用 `free_command`，或新增 `overflow_command`），由 `_overflow_ok()` 或显式开关控制；若定稿决定 overflow 纯人工，删除 target/cap 或标注为“仅展示”。

### P2-5 租约幂等仅进程内、键不隔离、保留期与 TTL 不一致

- 位置：`manager.py:103`（`_idempotency` 内存字典）；`manager.py:665-681`（get/put，键仅为 `request_id`）；`manager.py:282-286`（按 `budget.window_s=1800s` 清理）；`manager.py:568-575`（命中即返回，不再校验出口状态）。
- 复现：
  1. `lease(acctA, "free", "gen", 1, request_id="r1")` 成功；
  2. 重启管理器（同一 state 目录）；
  3. 再发 `request_id="r1"` → 未命中缓存，重新扣 1 次（跨重启不幂等）；
  4. 进程内 `lease(acctB, ..., request_id="r1")` → 返回 acctA 的缓存结果（包括 acctA 的 `socks_url`），不扣费；
  5. `r1` 首次成功后 20 分钟再重放，仍返回同一租约（`ttl_s` 只有 30~60s），期间该出口可能已隔离/烧毁。
- 影响：当前 app 客户端不发 `request_id`（`resolver.py:531-536`），所以生产暂无影响；但一旦启用该契约，会出现“重复扣/跨账号拿到出口/过期租约”三类问题。
- 建议：以 `(account, lane, class, calls, request_id)` 为键；保留期改为 `lease_max_ttl_s`（或返回的 `ttl_s`）；持久化幂等记录（或明确文档化为“仅进程内 at-most-once”）；同 id 不同参数返回 409/400；补跨重启/跨账号/过期重放测试。

## 4. Q / N 汇总

- **Q-1**：`free_overflow` 只在“无 healthy free + 维护积压”时启用（`manager.py:346-351`）；WARP healthy 但预算耗尽时 free 生成 429，不溢出。确认“不可用”是否包含“预算不可用”。
- **Q-2**：`cooling→healthy` 惰性归位无探活（`manager.py:269-281`）；除 P1-1 外，纯 verify 冷却是否也要至少 L0。
- **Q-3**：paid 新绑定生成上限 = 30-26 = 4（`manager.py:414-418`）；`/v1/status.window_remaining` 用 `soft-total`（`manager.py:1195-1197`）高估生成余量。确认 soft 的口径。
- **Q-4**：维护/探针严格限额、不能借用未用生成空间（`manager.py:402-421`），与 `PLAN.md:427` 不同、与 `DEPLOY.md:265` 一致；41 个 free 账号的维护窗口估算需按 20/出口/窗口重新核算。
- **Q-5**：app 尚未上报 `verify_account`（`src/` 无该 event_kind），连坐烧毁端到端未接通。
- **Q-6**：`/v1/lease` 无 `kind`，绑定恒 `chat`（`server.py:218-244`、`manager.py:627`；重申 `REV-2.1.5.13` Q-6）。
- **Q-7**：`/v1/assign` 无 `request_id`，重复 GET 每次计 1 次（`server.py:194-195`）。
- **Q-8**：`_maint_backlog` 仅内存（`manager.py:105, 645-647`），重启后 overflow 门与 `maintenance_queue` 归零。
- **Q-9**：`last_replenish_at` 在命令返回后即写（即使 `returncode != 0`），语义是“最近尝试”（`replenish.py:119-120`）。
- **Q-10**：`PLAN.md`/`CONTRACT.md:118,125,132`/`DEPLOY.md:194` 仍写 503、ttl 300、全出口 soft 50、per-account 配额、分级冷却；v2 已改为 429、30~60s、paid 30/45/400、无 per-account、固定 120min。计划中的 `PLAN.md` 更新应一并覆盖 `CONTRACT.md` 与 DEPLOY 的 ttl 对齐。
- **N-1**：冷却→healthy 未清理 `quarantined_at`/`last_error`（`manager.py:274-281`），重启后 `healthy + quarantined_at` 不归一。
- **N-2**：verify 窗口锚点取入站 `ts`（`manager.py:1037-1042`），乱序旧事件可能跨窗配对；建议锚 `self.clock()`。
- **N-3**：`exits_state.json` 保存含凭据的 `socks_url`（`manager.py:235`，0600/0700）；不是 API/日志泄露，但备份/导出需按凭据处理。
- **N-4**：`python -m exit_manager` 无 `__main__.py`，不支持；`--check-config` 仅打印 listen/exits/health/state（`main.py:91-103`）。
- **N-5**：`fanout.swap_node` 仍是命令钩子 + TODO（`fanout.py:80-85`、`DEPLOY.md:444-447`）；REV-2.1.5.12 已为 fork 增加 `/api/machine/*`，可评估后续接线。
- **N-6**：`tests/support.py:59` 的 `-> ProbeResult` 未 import（因 future annotations 不报错），可清理。
- **N-7**：`apply_probe_result` 在“达到失败阈值”后释放锁再 `quarantine`（`manager.py:907-924`），期间可能被一个 lease 插入；影响一个请求，建议同锁置 `probing` 或直接隔离。
- **N-8**：HTTP handler 未兜底 `OSError`（如磁盘满）等运行时异常（`server.py:218-271`），客户端会收到断连而非 JSON 500；app 侧 fail-closed，但仍建议统一 500。
- **N-9**：`record_event` 接受任意数值 `ts`（未来/过旧），未来 ts 会长期留在 `verify_events`（`manager.py:983-989, 1037-1042`）；app 可信，风险低。

## 5. 实跑输出

### 5.1 unittest（允许命令，全绿）

```text
$ python -m unittest discover -s data/tools/exit-manager/tests -v
...
test_write_and_read_roundtrip (test_store.AtomicJsonTest.test_write_and_read_roundtrip) ... ok
----------------------------------------------------------------------
Ran 137 tests in 5.381s

OK
```

### 5.2 `--check-config`（真入口）

```text
$ python data/tools/exit-manager/main.py --config data/tools/exit-manager/config.example.json --check-config
config OK: listen=172.17.0.1:8788 exits=6 health=on interval=120.0s state=D:\LLM\self_programming\aistudio-api\data\tools\exit-manager\state
EXIT=0
```

核对：样例 6 个出口 = 3 WARP(free) + 2 fanout(paid) + 1 fanout(free_overflow)；`Test-Path data/tools/exit-manager/state` 为 `False`（check-config 未落盘）。

### 5.3 模块入口（不支持，N-4）

```text
$ python -m exit_manager --config config.example.json --check-config   # 仓库根
D:\coding\Anaconda3\python.exe: No module named exit_manager
EXIT=1

$ python -m exit_manager --config config.example.json --check-config   # data/tools/exit-manager 内
D:\coding\Anaconda3\python.exe: No module named exit_manager.__main__; 'exit_manager' is a package and cannot be directly executed
EXIT=1
```

### 5.4 工作区

```text
$ git status --short data/tools/exit-manager docs/dev/rebuild/domains/P1-account
?? docs/dev/rebuild/domains/P1-account/research/exit-pool/PLAN.md
?? docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.12-fanout-machine-api.md
?? docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.13-lease-and-defer.md
```

`data/tools/exit-manager/` 为 gitignored，未出现在 status；本轮未改动其中任何文件。

## 6. 只读声明

- 本 Reviewer 全程 100% 只读：未修改 `data/tools/exit-manager/` 任何代码/配置/测试，未 commit，未启动 `main.py server`/浏览器/真实服务，未访问 VPS/外网/账号/真实出口。
- 唯一写操作：本文件 `docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.14-manager-budget-scheduler.md`。
- 实跑仅限：`python -m unittest discover -s data/tools/exit-manager/tests -v`（临时目录由测试自建/清理）与 `python main.py --config ... --check-config`（加载即退出，未创建 `state/`）。
- 结论依据均给出 `file:line`；未伪造测试结果，未用文档进度冒充运行证据。

---

# 二轮复审（2026-11-23）

- 复审角色：独立只读 Reviewer
- 复审对象：管理器 v2 修复轮（`data/tools/exit-manager/exit_manager/{config,state,manager,health,replenish}.py`、`main.py`、`config.example.json`、`DEPLOY.md`、`tests/`），以及 app 提交 `a19d81d` 的 verify-account 事件上报。
- 复审范围：首轮 `REV-2.1.5.14` 的 1 P1 + 5 P2 + Q-8，逐项复核；另做 app↔manager 端到端字段核对与 151 用例回归。
- **二轮结论：CONDITIONAL（0 P0 / 0 P1 / 1 P2）**。
  - 首轮 P1-1、P2-1..P2-5、Q-8 全部按要求修复并通过；
  - 新增残留 **P2-1**：`record_event(canary=exit_bad)` 的 `quarantine()` 仍会覆盖 `disabled`，与“disabled 不被覆盖”的同一不变量冲突（verify 烧毁路径已修，exit_bad 隔离路径未修）；
  - 其余为 Q/N（见 §3），不阻塞联调。
- 依据：本轮源码逐行比对、`python -m unittest discover -s data/tools/exit-manager/tests -v`（151 用例）、`python data/tools/exit-manager/main.py --config ... --check-config`、app `a19d81d` 源码与单测断言（只读）。

## 1. 复核点逐条

### 1. P1-1 verify 连坐不覆盖隔离态 — PASS

- **PASS（只烧 healthy）**：`_record_verify_burn` 在追加/裁剪 verify 事件后，`if state.status != STATUS_HEALTHY: save; log; return False`（`manager.py:1129-1139`）。`quarantined`/`cooling`/`disabled`/`probing` 均只记录，不改状态、不改 `cooldown_until`、不加 `burn_count`。
- **PASS（冷却到期先 L0）**：`_refresh` 对 `STATUS_COOLING` 且 `now >= cooldown_until` 的出口调用 `self.l0_probe(state)`；L0 healthy → `HEALTHY` 并清 `cooldown_until/burn_reason/fail_count`；失败 → `QUARANTINED`、`cooldown_until=None`、`quarantined_at` 保留/补齐、`last_error="cooldown probe: ..."`（`manager.py:317-351`）。`burn_reason` 在失败分支未清空，`burn_count/last_burn_at` 也保留，隔离原因与时间可追溯。
- **PASS（不绕过人工 recover）**：L0 失败保持 `quarantined`，`recover` 仍走人工 `POST /v1/admin/recover`；`recover` 成功才清 `quarantined_at`（`manager.py:936-953`）。
- **PASS（回归测试真实）**：`VerifyBurnIsolationTest` 断言 region 隔离后 2 个不同账号 verify 仍为 `quarantined`、`quarantined_at/last_error` 不变、`cooldown_until is None`、`burn_count==0`、仅 `verify_events` 增加（`tests/test_lease.py:399-436`）；`test_verify_does_not_extend_cooling_burn` 断言 cooling 不被续期（`:438-454`）；`CoolingExpiryProbeTest` 分别断言 L0 失败保持隔离、L0 成功归位（`:457-516`）；`BurnCoolingRecoveryTest` 已补 `l0_probe=healthy`（`:374-396`）。
- **N-1（L0 在全局锁内执行）**：`_refresh` 在 `lease`（`manager.py:638-640`）与 `status_summary`（`:1288-1291`）持有 `RLock` 时被调用，L0 的 `socket.create_connection` 最长阻塞 `health.timeout_s=8s`（`health.py:176-188`、`config.py:117-118`）。默认 socks 端点是本机/网桥，连接/拒连均在毫秒级；若配置了远端 socks 或被防火墙 DROP，多个 cooling 同时到期会串行阻塞锁（最多 N×8s），而 app 的 `/v1/lease` HTTP 超时默认 2s（`exit_pool/config.py:39,104`），可能出现一次租约超时。建议在锁外收集到期 cooling、锁外 L0、锁内应用结果，或把 L0 超时限制在 1~2s；当前不阻塞联调。
- **N-7（L0 成功未清遗留字段）**：成功分支清 `cooldown_until/burn_reason/fail_count`，但未清 `quarantined_at/last_error`（`manager.py:327-332`）。正常 burn（仅 healthy）时 `quarantined_at` 为 None、`last_error` 通常为 None；仅旧版本落盘的 `cooling+quarantined_at` 状态会留下陈旧字段，建议归一。
- **N-8（隔离期 verify 事件在恢复后仍计入窗口）**：非 healthy 出口的 verify 事件仍被追加到 `verify_events`（`manager.py:1129-1130`），并在 30min 窗口内保留；人工 recover 后若再来 1 个新账号 verify，旧事件可继续凑数触发 burn。属于保守方向，需确认是否预期。

### 2. P2-1 日上限只约束 generation — PASS

- **PASS（分支顺序）**：`_budget_ok` 先处理 `maintenance`/`probe`（只看各自 reserve 与窗口 `hard_limit`），`generation` 才检查 `daily_total`（`manager.py:472-493`）。
- **PASS（日计数只加 gen）**：`target.budget.daily_total += calls` 仅在 `cls == CLASS_GENERATION` 时执行（`manager.py:712-714`）；维护/探针不写日计数。
- **PASS（daily=2 用例真实）**：`test_daily_limit_only_blocks_generation` 用 `daily=2` 做 2 次 gen，第 3 次 `no_capacity`；随后 maint/probe 均成功，且 `daily_total==2`（`tests/test_lease.py:255-269`）。
- **Q（日上限不再是总量护栏）**：按本轮验收，maintenance/probe 可脱离 daily（窗口内仍受 `hard_limit=45` 与各自 reserve 约束）；paid 出口一天的维护/探针理论上限约 `26/窗口 × 48 ≈ 1248`，超过 paid daily 400。DEPLOY 已写明“日上限只约束 generation”（`DEPLOY.md:274-275`），视为有意口径；若仍需“日总量防封 IP”护栏，需另加日维护/探针上限。
- **N-13（daily 429 的 retry 不一定指向午夜）**：`_no_capacity` 会把 `info`（daily→`seconds_until_utc_midnight`）与健康出口的 `window_reset_in` 取最小值并夹到 60~600s（`manager.py:502-517`），因此 daily 耗尽时 app 仍会按窗口重置/600s 重试并在当天继续 429，直到 UTC 午夜。符合“defer 不失败”的语义，但不是“等到午夜”的精确提示。

### 3. P2-2 fanout 总上限 4 — PASS

- **PASS（默认与校验）**：`MAX_FANOUT_CAP=4`；`paid_cap` 默认 3、`overflow_cap` 默认 1；加载时 `paid_cap + overflow_cap > 4` 直接 `ConfigError`（`config.py:27, 71-72, 510-526`）。
- **PASS（示例一致）**：`config.example.json` 为 `paid_target=3, paid_cap=3, free_target=3, free_cap=6, overflow_target=1, overflow_cap=1, interval_s=60`；`test_example_config_loads` 断言 `paid_cap=3`、`overflow_cap=1`、和=4（`tests/test_config.py:40-44`）。
- **PASS（拒绝 >4）**：`test_fanout_cap_total_above_four_rejected` 用 `paid_cap=4, overflow_cap=1` 断言 `ConfigError`（`tests/test_config.py:61-67`）；`paid_cap=3, overflow_cap=1` 允许（`:53-59`）。
- **N-5（静态 exits 未计数）**：`MAX_FANOUT_CAP` 只约束 replenish 的 `paid_cap+overflow_cap`；若配置里**静态**写了 5 条 `kind=fanout` 的 exits，`load_config` 不会拒绝（`config.py:647-706` 无 fanout 计数检查）。若“上限 4”也约束静态出口数量，需补一条校验；按本轮给出的验收口径（paid_cap+overflow_cap）为 PASS。

### 4. P2-3 replenish 接入健康循环 — PASS

- **PASS（接线）**：`HealthLoop._maybe_replenish` 在 `run_once()` 中、`refresh_fanout()` 之后调用 `manager.replenish_tick()`（`health.py:246-272`）；`main.py` 把 `replenish.interval_s` 传入 `HealthLoop`（`main.py:150-159`）。
- **PASS（无命令不执行）**：`replenish_tick` 对 `command_for_lane(lane)` 为空的 lane 直接 `continue`，不调用 `attempt`，不产生命令执行；`Replenisher.can_replenish` 的 `no_command` 告警不会在 tick 路径触发（`manager.py:1268-1269`；DEPLOY `:472-473`）。
- **PASS（last_replenish_at）**：`Replenisher.attempt` 在命令返回后设置 `last_replenish_at`（成功/失败均算“最近尝试”，`replenish.py:119-120`）；`replenish_tick` 回写到 `_last_replenish_at`（`manager.py:1279-1280`）；`/v1/status` 暴露（`:1326`）。测试断言成功执行后 `last_replenish_at == clock()`（`tests/test_replenish.py:190-191`、`:320`）。
- **PASS（测试真实）**：`test_tick_runs_command_and_updates_status`、`test_tick_skips_lanes_without_command`、`test_health_loop_drives_replenish_on_interval`（`tests/test_replenish.py:152-224, 271-320`）。
- **N-2（命令在健康循环内同步执行）**：`Replenisher.attempt` 用 `subprocess.run(..., timeout=120)`（`replenish.py:104-111`），`replenish_tick` 串行跑 3 条 lane（`manager.py:1263`）。若配置了慢命令，健康循环最多被阻塞约 3×120s，期间不跑 L0/L1、不进入下一轮；lease 不受锁影响，但隔离/健康判定会延迟。当前交付命令为空，暂无影响；接入真实 provisioning 时建议独立线程/队列或更短超时。
- **N-3（实际节奏是 120s 而非 60s）**：健康线程只在 `_loop` 每 `health.interval_s=120s` 调一次 `run_once`，`replenish_interval_s=60` 只是 `_maybe_replenish` 的下限（`health.py:288-294`、`main.py:151-154`）。因此实际补充节奏为 `max(health.interval_s, replenish.interval_s)=120s`；DEPLOY “每 replenish.interval_s（默认 60s）调用一次”（`:469-470`）与之不符，建议改文案或让健康循环按更短间隔唤醒。
- **N-4（--once-health 会触发补充）**：`main.py:161-168` 直接调用 `health_loop.run_once()`，首次 `_next_replenish_at=0`，会执行已配置的 replenish 命令；DEPLOY `:153` 只提示“可能触发隔离/轮换”，未提示补隧道命令。建议在文档明确，或在 `--once-health` 下禁用 replenish。

### 5. P2-4 overflow 纳入补充、命令回退 — PASS

- **PASS（纳入 tick）**：`replenish_tick` 遍历 `(LANE_PAID, LANE_FREE, LANE_FREE_OVERFLOW)`（`manager.py:1263`）；healthy 计数按 `state.lane` 统计，overflow 独立计数（`:1257-1261`）。
- **PASS（回退链）**：`ReplenishConfig.command_for_lane("free_overflow")` 返回 `overflow_command or free_command`（`config.py:91-96`）；解析时 `overflow_command = _command(...) or free_command`（`config.py:506-509`）。因此未显式配置 overflow_command 时自动复用 free_command。
- **PASS（测试真实）**：`test_tick_includes_overflow_lane` 用 disabled overflow 使 healthy=0，断言 decisions 只有 `free_overflow`、started、命令 `["/bin/true","1"]`（`tests/test_replenish.py:226-269`）。
- **N（回退未单独断言）**：现有测试用的是显式 `overflow_command`；未显式配置时的 `free_command` 回退没有专门用例。代码路径正确，建议补一条。
- **Q-15（disabled 出口仍触发补充）**：上述测试特意用 `disabled: true` 的 overflow 让 healthy=0，并期望 tick 发起补充；也就是说 config `disabled` 不会抑制补充。若运维用 `disabled` 表示“不要这个出口”，补充命令会持续尝试把它补回来（受 5min/每小时 3 次限制）。需确认 `disabled` 语义：是“不分配”还是“也不补充”。

### 6. P2-5 幂等持久化 600s — PASS

- **PASS（键与 TTL）**：`IDEMPOTENCY_TTL_S=600.0`（`manager.py:57`）；`_idem_key` 对 `[account, lane, class, calls, request_id]` 做 SHA-256（`manager.py:746-752`），跨账号/跨 lane/class/calls 不串用。
- **PASS（持久化/重启）**：`idempotency.json` 独立 `JsonStore`（`manager.py:106`）；`_load` 读取并按 600s 过滤（`:150-166`）；`_save_idempotency` 写入（`:279-289`）；`_idem_put` 立即落盘（`:765-772`）；`_refresh` 清理过期项（`:352-358`）。
- **PASS（测试真实）**：同 request_id 不重复扣（`tests/test_lease.py:276-288`）；跨重启命中（`:290-304`）；不同账号隔离（`:306-315`）；599s 命中、601s 过期（`:317-330`）。
- **PASS（app 兼容）**：app `lease()` 的 payload 只有 `account/lane/class/calls`，不发送 `request_id`（`resolver.py:531-537`）；管理器 `request_id=None` 时 `idem_key=None`，走正常计数，无行为变化。
- **N-6（幂等文件的两个边角）**：① `idempotency.json` 损坏/非对象会使 `_load` 抛 `StateError`，`main` 直接退出（`manager.py:150-152`、`main.py:146-148`）；幂等缓存是非关键数据，建议损坏时丢弃缓存继续启动。② 结果里含 `socks_url`（fanout `user:pass`），持久化 600s；文件 0600/目录 0700（`store.py:17-36`），非 API/日志泄露，但备份/导出需按凭据处理。
- **N（缓存结果不按出口状态复验）**：600s 内重放直接返回原结果（含 30~60s 的 `ttl_s` 与当时的 `socks_url`），即使出口随后被隔离/烧毁。幂等的定义如此，且 app 暂不发送 `request_id`；若未来启用，调用方应按 `ttl_s` 重新租约，不要把 600s 当租约有效期。

### 7. Q-8 维护积压持久化 — PASS

- **PASS**：`_save_exits` 写入 `maint_backlog`（仅 >0，`manager.py:271-275`）；`_load` 恢复（`:139-148`）；`_note_no_capacity` 维护失败时递增并落盘（`:724-727`）；维护成功按 lane 递减（`:718-719`）；`/v1/status.maintenance_queue` 汇总（`:1327`）。
- **PASS（测试真实）**：`test_backlog_survives_restart` 断言维护失败后 `maintenance_queue==1`，重启后仍为 1（`tests/test_lease.py:519-533`）。
- **Q-16（积压只按请求 lane 递减）**：维护失败在 `free` 记 `free+1`，若之后同一维护改在 `paid` 成功，只递减 `paid`（`:718-719`），`free` 积压会保留到下次 free 维护成功；不影响 overflow 的安全性（只会更保守），但 `maintenance_queue` 可能偏高，需确认口径。

### 8. app a19d81d ↔ manager 端到端 — PASS（代码核对，未发请求）

- **PASS（触发点）**：app 仅在 `AuthError.verification_required` 时上报（`routing_pipeline.py:1008-1028`）；`_report_verify_account_event` 通过 `assignment_for(..., include_expired=True)` 取当前 `(account, surface)` 的出口（`routing_pipeline.py:897-927`；`resolver.py:443-461`）。
- **PASS（字段一致）**：app 发送 `account=HMAC-SHA256(hmac_key, account_id)`、`exit_id=assignment.exit_id`、`kind="verify_account"`、`canary="inconclusive"`、`ts=int(time.time())`（`routing_pipeline.py:929-934`、`resolver.py:753-767`）；管理器 `_validate_account` 接受 64 位 hex HMAC、`_validate_exit_id` 接受 `warp-1/fanout-*-*`、`EVENT_KINDS` 含 `verify_account`、`CANARY_VERDICTS` 含 `inconclusive`（`manager.py:50-53, 365-388, 1059-1080`）。app 单测逐字段断言（`tests/unit/test_403_auth_refresh.py:600-688`）。
- **PASS（现在真能触发）**：出口 healthy 时，第一个账号 verify 只记录（distinct=1）；第二个**不同 HMAC 账号**在同一 `verify_cluster_window_s=1800s` 内 verify → `distinct>=2` → `_burn` 置 `cooling`、`cooldown_until=now+7200s`、整组迁移（`manager.py:1124-1152`；`config.py:56-58`）。app 的 `reported_verify_exits` 只在单次 `RouteContext` 内按 `(account, exit_id)` 去重（`routing_pipeline.py:223-225, 920-927`），跨请求的不同账号不会被本地去重吞掉；`report_event_nowait` 已用 `_post_event_bounded`（`resolver.py:734-752, 785-800`）按 `http_timeout_s` 收敛，不会堆积悬挂任务。
- **N-10（空 exit_id 噪声）**：`_report_verify_account_event` 在 `assignment_for` 返回 None 时仍会以 `exit_id=""` 上报（`routing_pipeline.py:913-934`）；管理器 `_validate_exit_id("")` 返回 400，app 只记 warning。外部模式生成请求通常有 assignment；建议 app 在 `exit_id` 为空时直接 skip，避免无意义 400。
- **N-9（account_bad verify 也计入连坐）**：管理器 `_record_verify_burn` 不区分 canary；若未来 app 对 verify 事件给出 `canary=account_bad`，两个“账号侧问题”的 verify 仍可能烧掉健康出口（`manager.py:1119-1120, 1140-1145`）。当前 app 固定 `inconclusive`，无生产影响；需确认未来 canary 语义是否应排除 `account_bad`。
- **N（空窗口/时钟）**：app 的 `ts` 与 manager 的 `self.clock()` 若跨机漂移，只影响窗口/冷却时间；同机部署（当前）无问题。

### 9. 回归与门禁 — PASS

- **PASS（151 用例真实）**：`Ran 151 tests in 7.185s / OK`（§4）。首轮 137 + 本轮 14 个新增（daily-only 1、幂等 3、verify 隔离 2、冷却 L0 2、积压 1、补充 3、配置 2），新增用例均命中本轮修复点。
- **PASS（车道/429/预算/迁移未破坏）**：`LaneIsolationTest`、`OverflowTest`、`SoftHardThresholdTest`、`ReserveTest`、`CountingTest`、`MigrationAndBurnTest`、`QuarantineTest` 全部随 151 套件通过；`test_server.LeaseEndpointTest` 仍断言 429 + `Retry-After`。
- **PASS（--check-config）**：真实输出见 §4；新增 `lanes` 与 `budget` 行，打印 `paid_cap=3 + overflow_cap=1 = fanout cap 4`、`daily=generation-only`、`lease_ttl=30-60s`。
- **N-11（`-m` 入口仍不支持）**：`python -m exit_manager --check-config` 仍报 `No module named exit_manager.__main__`；真入口是 `python main.py --config ... --check-config`（首轮 N-4 未变）。

## 2. 残留 P0/P1/P2

### P2-1 `quarantine()` 仍会覆盖 `disabled`（exit_bad 事件路径）

- 位置：
  - `manager.py:801-802`：`quarantine` 无条件 `state.status = STATUS_QUARANTINED`，不保留 `STATUS_DISABLED`。
  - `manager.py:1106-1113`：`record_event` 对 `canary=exit_bad` 直接调用 `quarantine`，不检查当前是否 `disabled`。
  - `manager.py:893-894`：`recover` 只在 `state.status == STATUS_DISABLED` 时返回 409；一旦被 quarantine 覆盖，这个保护就失效。
  - `manager.py:191-192`：`_load` 会在重启时按 config `disabled` 重新置为 `DISABLED`，所以该问题是**运行期**的，重启可自愈。
- 复现（单测级，按代码逻辑；本轮未运行自定义脚本）：
  1. config 中 `warp-1` 设 `disabled: true`，`health.enabled=false`；
  2. `manager.record_event({"account":"aaaa1111bbbb2222","exit_id":"warp-1","kind":"region_403","canary":"exit_bad","ts":...})` → `warp-1` 从 `disabled` 变为 `quarantined`；
  3. `manager.recover("warp-1")` → 因状态已是 `quarantined`（非 `disabled`），不返回 409；health 关闭时直接置 `healthy`，health 开启时 L0/L1 通过也会置 `healthy`；
  4. 结果：config 明确 `disabled` 的出口被人工 recover 重新启用，`/v1/status` 统计也从 `disabled` 变成 `quarantined/healthy`。
- 影响：`disabled` 是配置级硬开关（`recover` 409、`apply_probe_result`/`refresh_fanout` 都跳过 disabled），但 exit_bad 事件路径绕过了它；若该出口因坏 IP/政策被禁用，误 recover 会把问题 IP 放回池。需要人工 recover，非自动数据面故障，故列 P2。
- 建议：在 `quarantine()` 入口或 `record_event` 的 `canary=exit_bad` 分支加 guard——`if state.status == STATUS_DISABLED: 记录 digest/日志，返回 {"status":"accepted","applied":false}`，不改变状态；或让 `quarantine` 保留 `DISABLED`。补一条“disabled + region_403 exit_bad 后仍为 disabled、recover 仍 409”的回归测试。
- 备注：本轮要求验证的 **verify 烧毁路径**（`_record_verify_burn`）已正确排除 disabled（`manager.py:1131-1139`），本条是同一“disabled 不被覆盖”不变量在 **exit_bad 隔离路径**上的残留。

## 3. 其余 Q/N（不阻塞联调）

- **Q-1（首轮未变）**：`free_overflow` 仅在“无 healthy free + 维护积压”时启用（`manager.py:418-423`）；WARP healthy 但预算耗尽时 free 生成仍 429，不溢出。确认“不可用”是否含“预算不可用”。
- **Q-3（首轮未变）**：paid 新绑定生成上限 = soft 30 - reserve 26 = 4，已绑定 = hard 45 - 26 = 19（`manager.py:486-490`）；`/v1/status.window_remaining = soft - total`（`:1299-1301`）仍高估生成余量。确认 soft 口径。
- **Q-4（首轮未变）**：维护/探针严格受各自 reserve，不能借用未用生成空间（`manager.py:472-483`）；与 `PLAN.md:427` 不同、与 `DEPLOY.md:276` 一致。41 个 free 账号的维护窗口估算仍需按 20/出口/窗口复核。
- **Q-5（首轮未变）**：仍无 per-account 配额、无分级冷却（`burn_count` 只记录不使用）、无 overflow 分组；按定稿为有意简化。
- **Q-10（文档漂移）**：`PLAN.md`/`CONTRACT.md` 仍描述旧的 503、ttl 300、全出口 soft 50、per-account 配额、分级冷却；`DEPLOY.md` 已更新到 v2。建议随 Stage 1 同步 `PLAN`/`CONTRACT`。
- **Q-15**：replenish 把 `disabled` 出口视为“低于 target”并尝试补回（测试 `test_replenish.py:226-269` 明确如此）；确认 `disabled` 是否也应抑制补充。
- **Q-16**：`maint_backlog` 只按请求 lane 递减（`manager.py:718-719`），跨 lane 成功后另一 lane 的积压仍保留；偏保守，确认显示口径。
- **N-2/N-3/N-4**：补充命令同步阻塞健康循环（最长 3×120s）；实际补充节奏是 `max(health.interval_s, replenish.interval_s)=120s`（文档写 60s）；`--once-health` 会触发补充命令。见 §1.4。
- **N-5**：`MAX_FANOUT_CAP` 只校验 replenish 的 `paid_cap+overflow_cap`，不校验静态 fanout exits 数量；若“上限 4”是绝对上限，需补静态计数校验。
- **N-6**：`idempotency.json` 损坏会导致启动失败（非关键缓存建议容错）；该文件含 `socks_url` 凭据（0600/0700）。
- **N-7/N-8**：L0 成功未清遗留 `quarantined_at/last_error`（仅旧状态）；隔离期 verify 事件在人工恢复后仍可计入 30min 窗口。
- **N-9/N-10**：`account_bad` verify 也计入连坐（当前 app 只发 `inconclusive`）；app 在无 assignment 时会以空 `exit_id` 上报并收到 400，建议 skip。
- **N-11**：`python -m exit_manager` 仍不支持；用 `main.py`。
- **N-13**：daily 耗尽的 `retry_after` 可能指向窗口重置而非午夜，app 会在当天继续短退避重试。
- **N-14**：`paid_target > paid_cap` / `overflow_target > overflow_cap` 未在加载期拒绝；运行期 `can_replenish` 会以 `AT_CAP` 处理（测试 `test_target_and_cap_block`），不会超配。

## 4. 二轮门禁输出

```text
$ python -m unittest discover -s data/tools/exit-manager/tests -v
...
Ran 151 tests in 7.185s

OK
```

```text
$ python data/tools/exit-manager/main.py --config data/tools/exit-manager/config.example.json --check-config
config OK: listen=172.17.0.1:8788 exits=6 health=on interval=120.0s state=D:\LLM\self_programming\aistudio-api\data\tools\exit-manager\state
lanes: paid target=3 cap=3 | free target=3 cap=6 | free_overflow target=1 cap=1 (fanout cap=4)
budget: window=1800.0s paid=30/45/400 free=50/75/2400 maint=20 probe=6 lease_ttl=30-60s daily=generation-only
EXIT=0
```

- `Test-Path data/tools/exit-manager/state` → `False`（check-config 未创建状态目录）。
- 本轮未运行 app 单测（按“读代码，不发请求”）；app 端仅做源码/既有单测断言核对。

## 5. 二轮只读声明

- 本轮仍为 100% 只读：未修改 `data/tools/exit-manager/` 与 `src/` 任何代码/配置/测试，未 commit，未启动服务/浏览器，未访问 VPS/外网/账号。
- 唯一写操作：在本 Review 文件末尾追加“二轮复审”章节。
- 实跑仅限：管理器 151 个 unittest（临时目录由测试自建/清理）与本地 `--check-config`（加载即退出，未创建 `state/`）。

---

# 三轮确认（2026-11-23）

- 确认角色：独立只读 Reviewer
- 确认对象：管理器 `manager.py` 的 `DISABLED` 硬开关修复 + `tests/test_manager_events.py` 新增 `DisabledHardSwitchTest`（6 用例）；157 用例实跑。
- **三轮结论：PASS（0 P0 / 0 P1 / 0 P2）→ 可联调。**
- 行号漂移说明：本轮 `quarantine`/`_burn` 的 disabled 短路把 `manager.py` 约 776 行之后的符号整体后移（约 +34 行）；二轮章节中的行号以二轮代码为准，本节全部使用当前文件行号。

## 1. quarantine / _burn 对 DISABLED 短路 — PASS

- **PASS（quarantine）**：`manager.py:801-819` 在取到 state 后、任何状态变更前判断 `state.status == STATUS_DISABLED`；进入后只做 `digest` 落盘（`_recent_events` + `_prune_events_locked` + `_save_exits`）、写 warning，返回 `{"status":"disabled","changed":False,"applied":False,"migrated":0,"stuck":0}`。不置 `QUARANTINED`、不改 `last_error/quarantined_at`、不调用 `_migrate_group`、不会走到 `should_rotate`/`_schedule_rotation`（`manager.py:820-854`）。
- **PASS（_burn）**：`manager.py:880-894` 先取 `was = state.status`，命中 `DISABLED` 时只写 warning 并返回 `disabled/changed=False/applied=False/migrated=0`；`burn_count/cooldown_until/burn_reason/last_burn_at` 均不变，不迁移、不落盘（无状态可落）。这是第二层防御；正常路径上 `_record_verify_burn` 已先对非 healthy 短路（`manager.py:1165-1173`）。
- **PASS（record_event 的 applied）**：`record_event` 对 `canary=exit_bad` 调 `quarantine` 后用 `changed or migrated` 计算 `applied`（`manager.py:1140-1147`）；disabled 分支两者均为 False，故 `/v1/events` 返回 `accepted/applied:false`，不误报“已隔离”。
- **PASS（落盘仅 digest）**：disabled 分支只在 `digest` 非空时 `_save_exits`，摘要与 `exits_state.json` 中的 `status: disabled` 一并保留；`test_exit_bad_event_keeps_disabled_and_only_records_digest` 断言 `doc["exits"]["warp-1"]["status"]=="disabled"` 且 `len(recent_events)==1`（`tests/test_manager_events.py:304-310`）；`test_direct_quarantine_keeps_disabled` 断言返回值四字段 + 落盘摘要（`:320-327`）。

## 2. exit_bad 后仍 disabled，recover 仍 409 — PASS

- **PASS（状态不变）**：disabled 出口收到 `record_event(canary=exit_bad)` 后，内存与落盘均仍为 `disabled`（`tests/test_manager_events.py:304-310`）；`applied:false`，绑定不迁移。
- **PASS（recover 409）**：`recover` 仅以 `state.status == STATUS_DISABLED` 判定并抛 `ConflictError`（`manager.py:927-928`）；由于 disabled 不再被 event 改写，`test_recover_after_exit_bad_still_conflicts` 在 exit_bad 后仍得到 `ConflictError`，状态保持 `disabled`（`tests/test_manager_events.py:312-318`）。这修复了二轮 P2-1 的绕过链。

## 3. verify 连坐 / 健康探针 / refresh 不能复活 disabled — PASS

- **PASS（verify 连坐）**：`_record_verify_burn` 对 `status != healthy` 只追加事件、落盘、return False（`manager.py:1165-1173`）；`test_verify_cluster_does_not_burn_disabled` 用两个不同账号的 `verify_account`（`canary=account_bad`）后断言 `disabled`、`burn_count==0`、`cooldown_until is None`、`verify_events==2`（`tests/test_manager_events.py:329-352`）。`_burn` 的 disabled 短路是第二层防线。
- **PASS（健康探针）**：`apply_probe_result` 在 `manager.py:1009-1014` 对 `QUARANTINED/DISABLED/COOLING` 直接 return，连 `last_checked_at` 都不改；`test_probe_result_cannot_override_disabled` 传入 healthy 后仍为 `disabled`，且 `exits_for_health()` 不含它（`tests/test_manager_events.py:354-358`）。
- **PASS（refresh/_refresh）**：`_refresh` 只处理 `COOLING` 到期（`manager.py:317-351`），不会触及 `DISABLED`；`test_refresh_keeps_disabled_out_of_pool` 在 `status_summary()`（内部 `_refresh`）后仍为 `disabled`（`tests/test_manager_events.py:360-363`）。
- **PASS（refresh_fanout 代码层）**：`refresh_fanout` 在 `manager.py:1080-1085` 对 `QUARANTINED/DISABLED/COOLING` 跳过 `tunnel_looks_bad`/`quarantine`；disabled 仅可能更新 `last_exit_ip`（`:1077-1079`），状态不变。该路径没有单独的 disabled 用例（见 §4 N-1），但 guard 明确。
- **PASS（recover/lease/迁移不复活）**：`recover` 409（`:927-928`）；`_lane_candidates` 只收 `HEALTHY`（`manager.py:434-437`），disabled 不会被分配或作为迁移目标；`_load` 在配置仍 `disabled:true` 时强制 `DISABLED`（`:191-192`）。

## 4. 6 个新用例真实性与 157 实跑 — PASS

- **PASS（6 个用例存在且真实）**：`tests/test_manager_events.py:290-363` 的 `DisabledHardSwitchTest` 共 6 个方法：
  1. `test_exit_bad_event_keeps_disabled_and_only_records_digest`（`:304-310`）：返回值 `accepted/applied:false`、内存 disabled、落盘 disabled、`recent_events==1`；
  2. `test_recover_after_exit_bad_still_conflicts`（`:312-318`）：`ConflictError` + 状态 disabled；
  3. `test_direct_quarantine_keeps_disabled`（`:320-327`）：`status/changed/applied/migrated` 四断言 + 内存 disabled + 落盘摘要；
  4. `test_verify_cluster_does_not_burn_disabled`（`:329-352`）：两个不同账号 verify 后 disabled、`burn_count==0`、`cooldown_until is None`、`verify_events==2`；
  5. `test_probe_result_cannot_override_disabled`（`:354-358`）：healthy 探针后仍 disabled，且 `exits_for_health()` 只含 `warp-2`；
  6. `test_refresh_keeps_disabled_out_of_pool`（`:360-363`）：`status_summary()` 后仍 disabled。
- **PASS（非空跑）**：6 个用例均有状态/返回值断言，且 1/3 直接读取 `exits_state.json` 断言落盘（`doc["exits"]["warp-1"]["status"]`、`recent_events`），不是只看函数返回。
- **N-1（落盘断言分布）**：4/5/6 为内存断言（它们要证明“无状态变更/无写入”，落盘断言可选）；只有 1/3 读 JSON。若要求“每个用例都断言落盘”，可给 4/5/6 各补一行 `_exits_doc()["exits"]["warp-1"]["status"] == "disabled"`；当前不构成缺陷。
- **N-2（_burn 直测）**：`_burn` 的 disabled 短路是第二层防御，正常路径已被 `_record_verify_burn` 的 `status != healthy` 拦截，故 6 个用例没有直接调用私有 `_burn`；verify 集群用例覆盖了可观测结果（不 burn）。
- **N-3（refresh_fanout 直测）**：`refresh_fanout` 的 disabled skip 只有代码级 guard（`manager.py:1080-1085`），没有像 `apply_probe_result` 那样的直接用例；如需补齐，可注入一个 `tunnel_looks_bad` 的 fanout client 断言 disabled 不转 quarantined。
- **PASS（157 实跑）**：`Ran 157 tests in 7.419s / OK`（§7）；相对二轮 151 新增 6 个 `DisabledHardSwitchTest`，与本节逐条对应。

## 5. 回归：二轮修复未破坏 — PASS

- **P1-1**：`_record_verify_burn` 的非 healthy 短路仍在（`manager.py:1165-1173`），cooling 到期 L0 探活仍在（`_refresh` `311-360`，L0 分支 `317-351`）；`tests/test_lease.py:399-533` 全部通过。
- **P2-1**：`_budget_ok` 仍先判 maintenance/probe 再判 generation daily（`manager.py:459-493`，daily 仅 `484-485`；`daily_total` 仅 generation `713-714`）；`test_daily_limit_only_blocks_generation` 通过。
- **P2-2**：`config.py` 未变（`MAX_FANOUT_CAP=4`、`paid_cap+overflow_cap>4` 拒绝、示例 `paid_cap=3/overflow_cap=1`）；`test_config.py:40-67` 通过。
- **P2-3/P2-4**：`health.py`/`replenish.py` 未变；`replenish_tick` 仍遍历 paid/free/free_overflow 并对无命令 lane 跳过（`manager.py:1286-1318`）；`test_replenish.py` 全部通过。
- **P2-5**：`IDEMPOTENCY_TTL_S=600`、五元组键、`idempotency.json` 持久化/重启恢复代码未变（`manager.py:57, 746-772, 150-166, 279-289`）；`test_lease.py:276-330` 通过。
- **Q-8**：`maint_backlog` 持久化代码未变（`manager.py:139-148, 271-275, 724-727`）；`test_backlog_survives_restart` 通过。
- **门禁**：157 用例全绿；`--check-config` 输出与二轮一致（§7）。

## 6. 残留 P0/P1/P2

**无（0 P0 / 0 P1 / 0 P2）→ 可联调。**

二轮列出的 Q/N（L0 在锁内、replenish 同步/节奏、静态 fanout 上限、幂等文件容错、文档漂移等）仍为非阻塞观察项，未在本轮代码中引入新的 P 级问题；本轮仅新增 §4 的 N-1/N-2/N-3（测试覆盖建议）。

## 7. 三轮门禁输出

```text
$ python -m unittest discover -s data/tools/exit-manager/tests -v
...
Ran 157 tests in 7.419s

OK
```

```text
$ python data/tools/exit-manager/main.py --config data/tools/exit-manager/config.example.json --check-config
config OK: listen=172.17.0.1:8788 exits=6 health=on interval=120.0s state=D:\LLM\self_programming\aistudio-api\data\tools\exit-manager\state
lanes: paid target=3 cap=3 | free target=3 cap=6 | free_overflow target=1 cap=1 (fanout cap=4)
budget: window=1800.0s paid=30/45/400 free=50/75/2400 maint=20 probe=6 lease_ttl=30-60s daily=generation-only
EXIT=0
```

- `Test-Path data/tools/exit-manager/state` → `False`（check-config 未创建状态目录）。

## 8. 三轮只读声明

- 本轮仍为 100% 只读：未修改 `data/tools/exit-manager/` 与 `src/` 任何代码/配置/测试，未 commit，未启动服务/浏览器，未访问 VPS/外网/账号。
- 唯一写操作：在本 Review 文件末尾追加“三轮确认”章节。
- 实跑仅限：管理器 157 个 unittest（临时目录由测试自建/清理）与本地 `--check-config`（加载即退出，未创建 `state/`）。
