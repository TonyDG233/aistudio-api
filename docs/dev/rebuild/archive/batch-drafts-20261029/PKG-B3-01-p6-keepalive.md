# PKG-B3-01 P6 保活 / 24h 回写 / 429 冷却

- 步骤：B3
- 状态：draft（待用户审计；中断时保留本文件，不提交）
- 基线：`99c38ba` + 目标 worktree HEAD `755e28a`
- 来源：D-02/D-03/D-05；D08-001..025；`06` P6-Q1..Q8；`05` B3；534 台账 P6=25 行

## 1. 范围

负责：
- 保活服务单循环、启停幂等、保留 job：auth 24h、metadata 24h、手动触发/状态；不建 auto_switch。
- 24h Cookie 回写：chat、build-only、active session；逐 slot 记账；失败不提前推进 `auth_saved_at`。
- 429 冷却 60/300/600 与美西午夜跨天：目标树已有，只做回归与边界补测，不改口径。
- pipe watchdog 仅父进程死亡后的孤儿 Chromium 回收；WS 只保留 ping，删除周期 liveness/Runtime.evaluate 轮询。
- `session.close` 关闭前保存 cookies；控制面不占请求容量；手动保活/切号入口复用 B2。
不负责：
- 不恢复 idle 浏览器回收、`active_activity`（06 裁决 disable）、自动 rebind/stale 复活。
- 不恢复 auto_switch/rebalance/instance_switch/legacy rotator 兜底（D08-015..018）。
- 不实现 build 协议本体（C1）；不实现日志/统计（B4/C4）；不改请求路由与重试（P2）。
- D-02：watchdog 不得触发账号复活/自动重启；D-03：不得恢复周期性 liveness；D-05：rebind 只走 B2 的 RETIRING+有界 drain，不先建新页。

## 2. 文件域

新增：`src/aistudio_api/application/account_keepalive.py`、`src/aistudio_api/infrastructure/account/account_info_refresher.py`、`src/aistudio_api/api/routes_keepalive.py`；对应 `tests/unit/test_account_keepalive.py`、`test_keepalive_config.py`、`test_routes_keepalive.py`、`test_account_info_refresh.py`。
修改（共享语义，单列冲突风险）：`api/app.py`（lifespan 启停/router，B1/B2/B4 冲突高）、`gateway/session.py`（关闭前回写，B2 冲突高）、`gateway/client.py`（pool/build-only 回写，B2 冲突高）、`browser/browser_engine.py`（恢复 `_spawn_process_watchdog`，P3 冲突高）、`browser/cdp_client.py`（WS ping，P3 冲突高）、`gateway/model_defaults.py` + `config.yaml` + `defaults/config.yaml`（keepalive 默认值，B1 冲突高）。
修改测试：`test_browser_engine.py`（恢复 watchdog 用例）、`test_cdp_client.py`（ping/断线失败）、`test_session_lifecycle.py`（close 回写）、`test_account_rotation_concurrency.py`（跨天边界）。
删除/不建：`application/instance_switch.py`、auto_switch job、legacy rotator 回退、`active_activity`。
交叉验证 534 与目标树：目标树已有 `account_rotator.py:26-53,165-217,252-287`、`browser_engine.py:471-600,683-790`、`cdp_client.py:95-108,291-299`、`session.py:489-529`、`test_account_orchestrator.py:303-341`、`test_account_rotation_concurrency.py:111-163,306-311`。目标树**不存在** fork 的 `account_keepalive.py`、`routes_keepalive.py`、`account_info_refresher.py`、`test_account_keepalive.py`、`test_keepalive_config.py`、`test_routes_keepalive.py`、`test_account_info_refresh.py`、`instance_switch.py`；这些行标“不适用/参考”，不得当作目标树既有实现或既有测试。

## 3. 依赖

- 前置：B1 配置/lifespan seam；B2 `active_requests`、账号切换与有界 rebind 接口；P3 冻结的 session/CDP 接口；P2 提供 429 归因。
- 目标树缺口：无 `AccountInfoRefresher`、无 keepalive、无 build-only 会话；相关 port 必须用 FakeClient/FakeSession 离线验证，build-only 真实验收后置到 C1。
- 配置：`rotator.cooldowns` 已存在（60/300/600）；新增 keepalive 默认值只进现有 config/defaults，启动校验缺失块。
- 环境：本包全程离线；不访问真实账号/IP/Google；真实 VPS 验收需另行授权。

## 4. 测试

- 配置：`test_keepalive_config.py` — 默认 24h/24h、缺块启动失败、busy 不推进 next_due；离线。
- 24h：`test_account_keepalive.py` — chat/active session/build-only FakeClient 均回写；slot 部分成功只记成功；no_active_page 60s 重试；fake clock；离线。
- metadata：`test_account_info_refresh.py` — 启动即跑、排除 expired、401 标过期、5xx 不改状态、24h 后再试；离线。
- 生命周期：`test_account_keepalive.py` — start/stop 幂等、异常退出不留 task；`test_routes_keepalive.py` — 200 状态/手动触发、409 busy、400 unknown；离线。
- 429/跨天：`test_account_rotation_concurrency.py` — 60/300/600、美西午夜、`session_expired` 86400 隔离；边界 0/86400/DST；离线。
- watchdog/ping：`test_browser_engine.py::test_spawn_process_watchdog_creates_pipe_and_reaps`（POSIX；Windows 走 Job Object）；`test_cdp_client.py` 校验 ping 配置、断线立即失败 pending；无周期 liveness；离线。
- 回归：`test_session_lifecycle.py` close 保存 cookies 且关闭失败不阻塞；`test_account_orchestrator.py` 现有 429 用例不改断言。
- 禁止项：新增 `test_keepalive_no_auto_rebind.py` 断言无 auto_switch/自动 rebind/自动 stale 复活；离线。

## 5. 命令与证据

```powershell
cd .kilo/worktrees/rebuild-upstream
python -m pytest tests/unit/test_account_keepalive.py tests/unit/test_keepalive_config.py tests/unit/test_routes_keepalive.py tests/unit/test_account_info_refresh.py tests/unit/test_account_orchestrator.py tests/unit/test_account_rotation_concurrency.py tests/unit/test_browser_engine.py tests/unit/test_cdp_client.py tests/unit/test_session_lifecycle.py -q
python -m ruff check src tests
python -m pyright
```

证据：`docs/dev/evidence/B3/20261028/`（`pytest.txt`、`ruff.txt`、`pyright.txt`、`keepalive-fakeclock.json`、`watchdog-posix.txt`、`ws-ping.txt`）；仅脱敏摘要，禁止 auth/cookie/token、真实账号/IP、原始请求体。

## 6. 失败停止

- 任何自动 rebind/auto_switch/idle 回收/watchdog 复活账号的迹象；或 24h 回写跳过 build-only/active session → 停止。
- 回写失败仍推进 `auth_saved_at`、只记总数不记逐 slot、no_active_page 不重试 → 停止。
- 周期性 liveness/Runtime.evaluate 轮询回归，或断线 pending 不立即失败 → 停止。
- 目标树 `src` diff 非 0、B2 接口变更、测试连续 2 次失败、需真实账号/IP → 停止并向用户请求裁决/授权。

## 7. 回滚

- 回滚点：B3 开始前 worktree HEAD（当前 `755e28a`），只 revert B3 提交。
- 文件：删除新增 keepalive/refresher/routes/tests；还原 `app.py`、`session.py`、`client.py`、`browser_engine.py`、`cdp_client.py`、`model_defaults.py`、`config.yaml`、`defaults/config.yaml` 与四个既有测试。
- 数据：真实账号回写前快照 `data/accounts/*/auth.json` 与 registry；回滚用快照恢复；不迁移/不删除账号。
- 配置：删除 keepalive 段或恢复原 config；不覆盖用户运行期 `logging`/`rotator` 值。

## 8. 未决

- D08-014 台账末列 `keep` 与 06 P6-Q6 “active_activity disable” 冲突；本包按 06 不实现，需用户确认并同步台账。
- WS ping 间隔/超时（目标 `ping_interval=None`）与是否保留按需 `CDPPage.is_alive`；建议 20s/20s，仅非周期。
- build-only 回写真实验收依赖 C1；B3 只验 FakeClient seam，是否接受为 B3 退出条件。
- `next_due` 是否持久化（重启重算 vs 持久化）；busy 跳过且不推进 due 已按 06，需 reviewer 确认。
- keepalive 默认值与 `rotator.cooldowns` 的配置归属：合并进现有 `rotator` 还是新增 `keepalive` 段。
- 手动切号入口是否复用 B2 `switch_to_account`；若 B2 未冻结该接口则本包阻塞。
