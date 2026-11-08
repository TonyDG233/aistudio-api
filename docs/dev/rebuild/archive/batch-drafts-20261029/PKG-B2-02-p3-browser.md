# PKG-B2-02 P3 BrowserSession / Context / Page 生命周期

- 步骤：B2
- 状态：draft
- 基线：`99c38ba` + `9b5cdea`（目标 worktree `.kilo/worktrees/rebuild-upstream`；docs HEAD 以 `git rev-parse HEAD` 为准）
- 来源：D-02 / D-03 / D-05 / D-10 / D-11；D03-004、D03-006、D03-009、D03-017..019、D03-029、D03-034、D03-035；D04-005..007、D04-019、D04-023、D04-052、D04-056、D04-057、D04-061；`05 §4.1 B2`；`06 P3`

## 1. 范围

负责：

- `BrowserSession` 单页生命周期：懒启动、`ensure_ready`、`SessionContext`/`CDPPage` 归属与关闭；一账号一逻辑 Context 一 Page。
- `active_requests` 为唯一并发原语：`request_scope`/`in_flight` 在成功、失败、取消、流关闭的 `finally` 释放；不引入 lease/reservation。
- 控制面 rebind（禁用账号、应用预设）：`RETIRING` → 停新请求 → 一次有界等待 `active_requests==0` 或超时 → 强制关旧 Page → 再建新页；drain 期间不建新页。
- `connect_page`/prune：关闭所有非当前请求、非当前活跃账号的 Page/target，不得只关 `about:blank`。
- Chromium 孤儿回收：恢复 pipe watchdog，仅父进程死亡后回收；保留进程组 SIGTERM/SIGKILL、Windows Job Object、atexit。
- CDP 传输：保留有限 WebSocket ping；半开连接由命令超时与断线失败兜底；不恢复周期性 liveness 轮询。

不负责：账号/路由/429/403（PKG-B2-03）、Build（C1）、cookie/BotGuard、统计与 UI。

退出条件：离线用例通过；同一会话 rebind 后仅 1 个 active Page；`active_requests` 始终归零；无第二页/drain task；watchdog 仅回收孤儿进程。

**Out-of-scope（534 与目标树交叉验证）**：旧 fork 的 `instance_pool.py`、`instance_manager.py`、`routing_pipeline.py`、lease/reservation、pinned slot、blue-green、chat+build 双页/一 Context 多 Page 均不得引入；534 行只是 inventory，不是任务包依据。

## 2. 文件域

- `src/aistudio_api/infrastructure/gateway/session.py`：rebind 顺序、单页、有界 drain、prune 接线。
- `src/aistudio_api/infrastructure/gateway/session_state.py`：`SessionContext`/`active_requests` 计数与一次 drain event。
- `src/aistudio_api/infrastructure/browser/cdp_client.py`：WS ping、`connect_page` 全量 prune、断线失败 pending futures。
- `src/aistudio_api/infrastructure/browser/browser_engine.py`：恢复 pipe watchdog；现有进程组/Job Object/atexit 保留。
- `tests/unit/test_session_lifecycle.py`、`test_browser_session.py`、`test_cdp_client.py`、`test_browser_engine.py`、`test_session_blue_green_readiness.py`。
- 共享语义文件：`session.py`、`session_state.py` 被 P2/P5 消费，先冻结 `ensure_ready` 与 `active_requests` 句柄；与 PKG-B2-03 有冲突风险。

## 3. 依赖

- PKG-B2-01（P1 账号/控制面）与 B1 装配/鉴权已完成；控制面只登录/保活，不占请求实例。
- `connect_page`/`ensure_ready`/`request_scope` 接口冻结后再并行 P2；P2 不得另建 lease 或第二页。
- D-02 的 pipe watchdog 实现来源为 99c38ba 之前版本；D-03 的 ping 参数与 D-10 的有界等待上限需在实现前定值。
- 离线 fake CDP/fake process fixture；本包不访问真实账号、IP、Google 或真实 Chromium 页面。

## 4. 测试

- `test_browser_engine.py::test_spawn_process_watchdog_creates_pipe_and_reaps`：父进程退出后子进程被回收；不触发账号复活/rebind；离线。
- `test_browser_engine.py::test_windows_job_object_lifecycle`：Job Object 关闭杀进程树；与 pipe watchdog 共存；离线。
- `test_cdp_client.py::test_websocket_ping_is_enabled`：连接参数 `ping_interval`/`ping_timeout` 为有限值，非 `None`；离线 fake WS。
- `test_cdp_client.py::test_connect_page_prunes_all_non_active_targets`：3 个 page target，保留 active，关闭其余 2 个（含非空白页）；离线 fake `/json/list`。
- `test_session_lifecycle.py::test_request_scope_is_only_active_requests`：并发 scope 正常/异常/取消后计数归零；离线。
- `test_session_lifecycle.py::test_rebind_drains_once_and_forces_close_on_timeout`：旧页 in-flight 不归零时仅一次有界等待，超时强制关页，无常驻 drain task；离线。
- `test_session_lifecycle.py::test_rebind_does_not_create_new_page_while_draining`：旧页关闭前 `create_new_page` 调用次数为 0；离线。
- `test_session_blue_green_readiness.py` 重写：只允许 1 个 active Page，不允许 1 active + 1 drain 预算例外；离线。
- 授权要求：全部离线；真实 CDP/账号测试不在本包，另走真实测试闸门。

## 5. 命令与证据

```
cd .kilo/worktrees/rebuild-upstream
uv run pytest tests/unit/test_browser_engine.py tests/unit/test_cdp_client.py tests/unit/test_browser_session.py tests/unit/test_session_lifecycle.py tests/unit/test_session_blue_green_readiness.py -q
uv run ruff check src/aistudio_api/infrastructure/browser src/aistudio_api/infrastructure/gateway/session.py src/aistudio_api/infrastructure/gateway/session_state.py
```

证据路径：`docs/dev/evidence/B2/2026-10-28/PKG-B2-02/`（pytest/junit、watchdog/prune/drain 事件日志）。脱敏：禁止 cookie/token/auth/账号邮箱/IP/真实请求参数入证据。

## 6. 失败停止

- 任一测试失败，或 `active_requests` 泄漏、drain 期间新建页、drain task 常驻、prune 关闭 active 页、watchdog 触发账号复活/自动 rebind。
- 单包内修复重试上限 2 次；仍失败即停止并提交失败证据，不扩大范围、不引入旧 fork 代码。
- 需要改 `ensure_ready`/`active_requests` 契约、drain 上限或 watchdog 形态时，向用户请求裁决后再继续。

## 7. 回滚

- 回滚点：本包开始前的目标 worktree HEAD；仅还原上述 src/tests 文件，不整树 checkout。
- 无数据/配置迁移；关闭本包启动的 Chromium 进程并清理测试产物；不合并 master/rebuild，不 commit。

## 8. 未决

- 目标树 99c38ba 已无 `_spawn_process_watchdog`：按 D-02 从 99c38ba 之前版本恢复，还是将现有 Job Object/pdeathsig 视为等价 watchdog，需用户确认。
- 目标树无 CDP BrowserContext；本包的 “Context” 指 `SessionContext` 逻辑上下文，是否确认不新增真实 BrowserContext。
- D-03 下 `CDPPage.is_alive` 的一次性探测是否保留为诊断；不得演变为周期性 liveness 轮询。
- D-10 有界 drain 的默认上限（现有 12s）是否固定、可配置，或按请求类型区分。
- D-11 prune 遇到其他账号遗留页且仍有 in-flight 时，直接关闭还是先记录并等待；需 reviewer 判定。
