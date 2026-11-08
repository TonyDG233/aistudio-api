# REV-2.1-A 双引擎共存与引擎切换（Stage 2.1-A）独立审查报告（终审 PASS 版）

## 0. 审查基线与只读声明

- 审查对象：Q-17/Q-18 修复后的 Step 2.1-A 当前工作区（双引擎全局二选一默认 `browser`；vendor `waa-signer`；BrowserlessSession；事务热切换）。
- `HEAD`：`bb968da test(browser): 隔离本地 .cloakbrowser 目录使单仓全量测试全绿并忽略 .kilo 本地配置`（仍为返工前基线）。
- `upstream/master`：`8ba2810 docs(readme): polish quickstart guides, remove AI tone, and add cookie import walkthrough`；`merge-base`：`852de532efa6b538425443e7abc2212a7487a588`。
- 当前索引：`git diff --cached` 为空（无 staged）；工作区包含全部返工：46 个 tracked 修改、`waa-signer/` untracked 文件、全部新增 Python/WAA 文档/测试。
- 审查范围：`git status`、`git diff HEAD`、全部 untracked（含 `waa-signer/`）、`application/engine_switch.py`、`application/instance_manager.py`、`infrastructure/waa/`、`gateway/instance_pool.py`、`gateway/pooled_session.py`、config/routes/app、全部新增/修改测试。
- 只读声明：本审查仅写入本报告；未修改任何业务代码/配置/测试/文档；未 Commit/Push；未启动真实服务、真实浏览器、真实账号或外网。仅运行离线命令与离线测试。
- 终审结论：**PASS（0 未关闭 P0/P1/P2/Q）**。Q-17、Q-18 已闭环；原 P1/P2/Q 全部 CLOSED；所有指定门禁通过。

---

## 1. Q-17 修复核验

| 核查点 | 结论 | 证据（file:line / 测试） |
|---|---|---|
| 未观察异常 | **CLOSED** | `_observe_task`：`application/engine_switch.py:587-591`；正常完成 `:556-558`、超时后完成 `:566-568`、finally `:582-585` 均注册/执行观察，取回 `task.result()` 并抑制异常。 |
| 超时 | **CLOSED** | `engine_switch.py:559-580`：超时后 `task.cancel()`，有界等待 `_ORPHAN_GRACE_S`（`:29`）；期内完成则 observe；仍未停则显式 `orphaned rollback` 日志、`_orphaned_rollbacks += 1`、注册 done callback（`:571-580`）。 |
| orphan 可观测 | **CLOSED** | `EngineStatus.orphaned_rollbacks`：`engine_switch.py:70,85`；`status()` 序列化：`:183-184`；超时日志明确记录 timeout 与 orphan 计数（`:574-579`）。 |
| 不再静默释放锁 | **CLOSED** | orphan 路径显式记录“outer lifecycle scope will release with the rollback task pending”；返回 False 后调用方记录 `cancelled; rollback timeout`：`engine_switch.py:348-349`。 |
| 新死锁 | **CLOSED（未发现）** | `_observe_task` 无 await；done callback 仅 `task.result()`；`_await_uninterruptibly` 循环吸收 CancelledError（`:551-555`）；测试 `tests/unit/test_engine_switch.py:543-622` 覆盖 timeout 完成/迟到异常/orphan 计数，未出现悬挂。 |
| 回归测试 | **CLOSED** | `test_uninterruptible_wait_observes_rollback_exception_after_timeout`（`test_engine_switch.py:543-572`）；`test_uninterruptible_wait_records_orphan_and_observes_late_error`（`:575-622`）。 |

---

## 2. Q-18 修复核验

| 核查点 | 结论 | 证据 |
|---|---|---|
| 使用真实 PooledSession | **CLOSED** | `RealPooledSessionFactory` 构造真实 `PooledSession`：`tests/unit/test_engine_switch_lifecycle_reentry.py:123-144`，依赖真实 `SessionContext`、`XHRStreamTransport`、`PooledSession`（imports `:14-16`）；`FakePage`/`FakeCDP` 仅作为离线页面/CDP 驱动。 |
| 启动/目标/恢复 | **CLOSED** | `test_cancelled_rollback_restores_real_pooled_session`（`:316-429`）：断言启动 session、目标 rebuild session、恢复后的 final session 均为 `PooledSession`（`:333,374,414-419`），目标 page 已关闭（`:410-411`），恢复 session `engine=="browser"` 且 `is_alive()`（`:417-418`）。 |
| 取消/rollback | **CLOSED** | 首取消进入 rollback，连续取消不提前释放 scope；`:377-403` 取消与 `manual_scope` 阻塞，`:400-429` rollback 完成后的状态断言。 |
| 锁/ContextVar/depth/gate/keepalive | **CLOSED** | `:422-429` 断言 `_lifecycle_depth==0`、`_lifecycle_lock.locked() is False`、`_lifecycle_txn.get() is None`、`requests_open`、`total_active_requests()==0`、keepalive 恢复；与 P2-17 测试 `:176-267` 组合覆盖。 |

---

## 3. 历史发现逐项状态

| 编号 | 状态 | 证据/说明 |
|---|---|---|
| P1-01 | **CLOSED** | `git diff --cached` 为空，无 staged 缺失；Commit 前统一 `git add`。 |
| P1-02 | **CLOSED** | 同 endpoint 二次 preflight 重新拉起 daemon；`engine_switch.py:277-290`、`engine_runtime.py:185-215`；`test_engine_runtime_lifecycle.py:223-252`。 |
| P1-03 | **CLOSED** | ContextVar 逻辑事务 token：`instance_manager.py:112-116,168-210`；真实 manager 单次/连续取消测试；`test_engine_switch_lifecycle_reentry.py:148-429`。 |
| P2-01 | **CLOSED** | 503 + Retry-After：`routes_system.py:530-537`；`test_engine_switch.py:419-443`。 |
| P2-02 | **CLOSED** | 四协议 Retry-After：`format_errors.py:221-258`；`test_format_error_routing.py:119-126`。 |
| P2-03 | **CLOSED** | 取消回滚 + requests_open/keepalive：`engine_switch.py:203-215,327-356`；真实/单元取消测试。 |
| P2-04 | **CLOSED** | `lifecycle_scope` 整段事务锁：`engine_switch.py:200-201`、`instance_manager.py:176-210,908-944`；外部 rebind 阻塞测试 `test_instance_manager.py:1081-1129`。 |
| P2-05 | **CLOSED** | `_gate_lock` + epoch/generation：`instance_pool.py:124-159,696-740`；`test_instance_pool.py:426-446`。 |
| P2-06 | **CLOSED** | keepalive 锁内二次检查：`account_keepalive.py:150-166,280-302`；`test_account_keepalive.py:695-740`。 |
| P2-07 | **CLOSED** | local endpoint 校验：`model_defaults.py:519-544`；`test_engine_config.py:59-104`。 |
| P2-08 | **CLOSED** | Windows `.exe` fallback：`local_daemon.py:31-43`；`test_engine_config.py:156-184`。 |
| P2-09 | **CLOSED** | Go forceRecreate generation/pending/waitForDrain：`poolstate/state.go:14-44`、`main.go:325-365,437-525`；Go 三包测试通过。 |
| P2-10 | **CLOSED** | 32 MiB + Google allowlist：`main.go:48,893-930`、`upstreamguard/guard.go:1-66`；Go 测试通过。 |
| P2-11 | **CLOSED** | engine 配置项接通/清理；schema/tests 通过。 |
| P2-12 | **CLOSED** | README Docker 事实：`waa-signer/README.md:67-74`。 |
| P2-13 | **CLOSED** | 05 记录三项上游授权：`05-restart-map.md:78-80`。 |
| P2-14 | **CLOSED** | 新增 engine/daemon/switch/signer/browserless/manager/go 测试；全量 pytest 通过。 |
| P2-15 | **CLOSED** | accountKey 非法值稳定哈希：`browserless_session.py:266-277`；`test_browserless_session.py:442-453`。 |
| P2-16 | **CLOSED** | preflight 只拒绝 `health.ok=false`：`engine_runtime.py:202-215`；`test_engine_runtime_lifecycle.py:294-308`。 |
| P2-17 | **CLOSED** | `_await_uninterruptibly` + orphan 观察：`engine_switch.py:527-597`；真实 manager 连续三次取消测试 `test_engine_switch_lifecycle_reentry.py:176-267`；timeout 测试 `test_engine_switch.py:543-622`。 |
| Q-01 | **CLOSED** | Cookie 删除/tombstone/truncated；Python/Go/tests 闭环。 |
| Q-02 | **CLOSED** | Firefox 152 权威，移除 Python 指纹空壳。 |
| Q-03 | **CLOSED** | target 回落：`engine_switch.py:201-215`。 |
| Q-04 | **CLOSED** | 配置写盘最后；失败测试回滚。 |
| Q-05 | **CLOSED** | 禁用账号槽位 UNSTARTED：`instance_manager.py:930-948`。 |
| Q-06 | **CLOSED** | 整数 fail-fast：`model_defaults.py:483-506`。 |
| Q-07 | **CLOSED（文件级）** | browser preflight 可执行文件校验：`engine_runtime.py:53-66`。 |
| Q-08 | **CLOSED** | `go 1.23` + `toolchain go1.27.0`；Go 三包通过。 |
| Q-09 | **CLOSED** | `.deps` ignore + `ARCHITECTURE.md`/`AGENT.md` 双引擎定义。 |
| Q-10 | **CLOSED** | 55 个本轮 Python 文件 `ruff format --check` 全部已格式化。 |
| Q-11 | **CLOSED** | health `_safe_int`：`signer_client.py:138-148`。 |
| Q-12 | **CLOSED** | Set-Cookie header 日志脱敏：`log_broadcast.py:44-47,68-69`。 |
| Q-13 | **CLOSED** | 稳定 accountKey；mint/rpc/stream/close 一致。 |
| Q-14 | **CLOSED** | health 字段契约：`signerapi/types.go:83-92`。 |
| Q-15 | **CLOSED** | `resolve_engine_*` 死代码已删除。 |
| Q-16 | **CLOSED** | `_close_bounded` 复用 `_await_uninterruptibly`，取消时关闭 new runtime。 |
| **Q-17** | **CLOSED** | `_observe_task` + orphan 记录/计数/日志；timeout 回归。 |
| **Q-18** | **CLOSED** | 真实 `PooledSession` + `SessionContext` + `XHRStreamTransport` 的取消/rollback 测试。 |

---

## 4. 变更域逐项结论

| 变更域 | 结论 | 说明 |
|---|---|---|
| 红线（保留浏览器/CDP/capture/replay/Android/四协议/active_requests） | **PASS** | `git diff --diff-filter=D` 为空；`active_requests` 仍是唯一并发账本；四协议测试通过；未吸收删除浏览器提交。 |
| WAA vendor 与 Go 补丁 | **PASS** | `waa-signer` 文件完整；`gofmt -l` 空；Go 三包测试通过；P2-09/10 闭环。 |
| BrowserlessSession | **PASS** | 每槽独立 cookie/minter/transport；mint headers 进入 RPC；direct/signer 双路径；删除/tombstone/truncated、稳定 accountKey、Firefox 152 均闭环。 |
| 引擎切换 | **PASS** | P1-02、P1-03、P2-03/04/17、Q-16/Q-17 均闭环；真实 manager + 真实 PooledSession 取消测试通过；无死锁。 |
| 配置/API/状态一致性 | **PASS** | engine schema/defaults/fail-fast、GET config/health/instances、503+Retry-After、P2-15/16 均闭环。 |
| 测试真实性 | **PASS** | 1403 pytest + Go 三包全绿；真实 `PooledSession`、真实 `InstanceManager`/`InstancePool`、连续取消、timeout/orphan 回归齐全。 |

---

## 5. 实跑门禁结果（本轮实际）

| 命令 | 实际输出 | 结论 |
|---|---|---|
| `uv run ruff check .` | `All checks passed!`，exit 0 | PASS |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations`，exit 0 | PASS |
| `uv run pytest -q`（`PYTHONUTF8=1`） | `1403 passed in 40.84s`，exit 0 | PASS |
| `gofmt -l waa-signer`（项目根） | 无输出，exit 0 | PASS |
| `cd waa-signer; GOTOOLCHAIN=go1.27.0 go test ./internal/poolstate ./internal/upstreamguard ./internal/signerapi` | `ok .../poolstate (cached)`、`ok .../upstreamguard (cached)`、`ok .../signerapi (cached)`，exit 0 | PASS |
| `git diff --check` | exit 0（仅 CRLF 警告） | PASS |
| `git ls-files config.yaml` | 无输出，exit 0 | PASS（untracked） |
| `git diff --diff-filter=D --name-only HEAD` | 无输出，exit 0 | PASS（无删除） |
| `git diff --cached` | 无输出（索引为空） | PASS（无残留 staged） |
| `uv run ruff format --check <全部 55 个本轮变更/新增 .py>` | `55 files already formatted`，exit 0 | PASS |

---

## 6. 未验证 / 残余风险（本步明确不做）

1. 未启动真实 `waa-signer`、未做真实 Google/AI Studio 请求；proof、Cookie 轮换、BotGuard 真实结果未验证。
2. 未执行完整 `go build ./...` / cgo(mozjs) 链接；仅验证 `gofmt` 与纯 Go 三包（`poolstate/upstreamguard/signerapi`）。
3. 未跑 `go test -race`；P2-09 与 ContextVar 并发逻辑仅单元级验证。
4. 未启动真实浏览器/CloakBrowser 做 browser↔browserless 实机切换；P1-02/P2-17 由代码路径与真实 manager/PooledSession 离线测试证明。
5. 未执行 `AISTUDIO_UI_DEBUG=1` + Playwright 三分辨率验收（本步无 `.vue` 变更，engine UI 仍待后续 UI 步骤）。
6. 未构建 Docker 镜像；README 已明确 Docker 内置 waa-signer 留待 2.1-B。

---

## 7. Commit / 2.1-B 建议

- **Commit：建议可以提交。**
  - 提交前统一 `git add` 全部 waa-signer/Python/测试/docs（当前索引为空，无 staged 丢失风险），用 `git diff --cached` 复核后提交。
  - 当前代码/测试门禁全部通过，未发现未关闭 P0/P1/P2/Q。
- **2.1-B：代码审查层面不阻塞。**
  - 建议在 Commit 后按既定发布口径完成一次受控真实 signer/Google 消息验收，再启动 2.1-B；该实机验收不属于本次只读审查范围，未被本报告伪证。
