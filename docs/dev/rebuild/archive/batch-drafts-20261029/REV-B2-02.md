# REV-B2-02 PKG-B2-02 P3 BrowserSession / Context / Page review

- 被审包：`docs/dev/rebuild/domains/P3-browser/packages/PKG-B2-02-p3-browser.md`
- reviewer：只读 agent
- 状态：CONDITIONAL
- 基线：`99c38ba` + `9b5cdea`；目标树 `.kilo/worktrees/rebuild-upstream`（HEAD `755e28a`，`src`/`tests` 与 `9b5cdea` 一致）

## 结论

包明确排除了旧 fork 的 instance/lease/blue-green 并正确识别了 `connect_page` 只关空白页、`_spawn_process_watchdog` 已不存在等基线事实；但 `active_requests` 句柄与 rebind 入口未冻结，7 个验收用例中 6 个在目标树不存在且未声明为新增，且与 P1 形成依赖环，需先冻结接口与排期。

## 字段核对

| 字段 | 结论 | 说明 |
|---|---|---|
| 范围 | 基本闭合 | 单页/有界 drain/watchdog/prune/ping 已列；`active_requests` 与 rebind 入口未定义（P3-01、P3-02） |
| 文件域 | 可执行但不完整 | src/tests 已列；未标 6 个新用例（P3-03）；`test_browser_session.py` 无对应用例（P3-08） |
| 依赖 | 不足 | 声明 P1/B1 前置并冻结两个句柄，但 rebind 入口缺失且与 P1 形成环（P3-02） |
| 测试 | 部分可执行 | 7 个用例，6 个不存在且未声明新增（P3-03） |
| 命令 | 不完整 | 定向 pytest + ruff；缺全量 pytest 与 pyright（P3-07） |
| 证据 | 齐全 | `docs/dev/evidence/B2/.../PKG-B2-02/`，脱敏明确；日期口径不一致（P3-10） |
| 失败停止 | 齐全 | 3 条停止条件，覆盖泄漏/建页/drain task/prune/watchdog |
| 回滚 | 齐全 | 逐文件还原 + 清理 Chromium/测试产物，不 commit/合并 |
| 未决 | 部分 | 5 条已列；D-02 已决项被重新打开（P3-04），prune/drain 边界未正面列（P3-06） |

## 发现

| 编号 | 严重度 | 文件:行 | 事实 | 影响 |
|---|---|---|---|---|
| P3-01 | 高 | `PKG-B2-02:13,32,37`；`session.py:84,136-147`；`session_state.py:35-36` | 包把 `active_requests` 定义为唯一并发原语并要求 P2 依赖「`active_requests` 句柄」，但目标树只有 `session._in_flight`、`SessionContext.in_flight`/`drain_event`，不存在 `active_requests` 符号，包也未给出新名称/签名/返回语义 | B2-03 依赖一个未定义的接口；P3/P2 对计数与 drain 的挂接点无法对齐 |
| P3-02 | 高 | `PKG-B2-02:33,36`；`session.py:182-272`；`PKG-B2-01:33` | rebind 入口 `transition_to_account(auth_file, force_refresh=False)` 已存在，但 P3 §1/§2/§3 均未点名或冻结它；P1 依赖 P3 提供该入口，P3 又要求 P1 先完成（`PKG-B2-02:36`） | B2 内依赖环；P1 的「切换成功后提交 registry」没有冻结的返回值/失败语义；P3 改语义时 P1 无法预知 |
| P3-03 | 中 | `PKG-B2-02:43-50`；`test_browser_engine.py:187`；`test_cdp_client.py:216`；`test_session_lifecycle.py:17,70,98,127` | §4 列出 7 个用例，其中 `test_spawn_process_watchdog_creates_pipe_and_reaps`、`test_websocket_ping_is_enabled`、`test_connect_page_prunes_all_non_active_targets`、`test_request_scope_is_only_active_requests`、`test_rebind_drains_once_and_forces_close_on_timeout`、`test_rebind_does_not_create_new_page_while_draining` 在目标树都不存在；包只对 `test_session_blue_green_readiness.py` 标了「重写」 | 新增/改写未在 §2 文件域声明；验收命令会因缺文件失败，执行者无法区分「未实现」与「未创建」 |
| P3-04 | 中 | `PKG-B2-02:16,76`；`08-09-decision-record.md:14` | §8.1 把「恢复 `_spawn_process_watchdog` 还是把 Job Object/pdeathsig 视为等价 watchdog」列为未决；D-02 已明确「`browser_engine._spawn_process_watchdog` 保留」，且 §1 自己写「恢复 pipe watchdog」 | 重新打开已决事项；若选「等价」即偏离 D-02，且与 §1 自相矛盾 |
| P3-05 | 中 | `PKG-B2-02:17,29`；`cdp_client.py:101-102,90-93` | §1 写「保留有限 WebSocket ping」，但基线是 `ping_interval=None, ping_timeout=None`，需要的是设置有限值而非「保留」；§2 把「断线失败 pending futures」列为改动项，但 `fail_pending_futures` 已在 `:87-93` 实现 | 两个范围项对基线描述不准，易造成无效改动或漏改 |
| P3-06 | 中 | `PKG-B2-02:15,46,80`；`08-09-decision-record.md:23` | §4 prune 用例要求「3 个 page target，保留 active，关闭其余 2 个（含非空白页）」，但 D-11 允许「一个明确处于 drain 的旧页且有界超时」；§1/§4 未给 draining 旧页豁免，§8.5 才把「prune 遇到 in-flight 遗留页」列为未决 | prune 可能在 rebind 期间关闭正在 drain 的旧页；测试与 D-11 的边界不一致 |
| P3-07 | 中 | `PKG-B2-02:54-59` | 命令只有定向 pytest 与 ruff，没有 `uv run pytest -q` 全量回归，也没有 `uv run pyright`；B1/B2-01 都包含这两项 | 缺全量回归与类型门；页面生命周期改动的影响面大于定向用例 |
| P3-08 | 低 | `PKG-B2-02:31,57` | §5 命令包含 `test_browser_session.py`，但 §4 没有任何该文件的用例/期望 | 执行范围与验收用例不对应，证据要求不完整 |
| P3-09 | 低 | `PKG-B2-02:5` | 基线写「docs HEAD 以 `git rev-parse HEAD` 为准」，但本包位于主仓库 `rebuild` 分支的未跟踪目录，目标 worktree HEAD `755e28a` 内没有这些域文档；B1/B2-01 直接写 `755e28a` | 「docs HEAD」指向哪个仓库/分支不明确，审计时无法定位文档版本 |
| P3-10 | 低 | `PKG-B2-02:61` | 证据日期 `2026-10-28`，B1/B2-01 用 `2026-10-29` | 跨包证据归档口径不一致 |

## 验收门槛

- 范围是否闭合：**基本闭合**（单页、有界 drain、watchdog、prune、ping），但 `active_requests` 与 rebind 入口未定义（P3-01、P3-02）。
- 文件域是否可并行：**否**，与 P1 形成依赖环（P3-02）；`session.py`/`session_state.py` 的公开句柄未冻结。
- 测试是否可执行：**部分**，6 个用例不存在且未声明新增（P3-03）；命令缺全量回归/类型检查（P3-07）。
- 证据是否可复现：**满足** `docs/dev/evidence/B2/`，日期口径需统一（P3-10）。
- 失败停止/回滚是否明确：**是**，停止条件、逐文件回滚、禁旧 fork 代码都明确；D-02 已决项不应再挂未决（P3-04）。
- 是否误用旧 fork 路径或 534 当任务：**否**，§1 Out-of-scope 明确禁止 `instance_pool.py`/lease/pinned/blue-green，未把 534 当任务。
- 真实测试授权：**未要求**，§4 全部离线；真实 CDP/账号测试另走真实测试闸门。

## 给用户的裁决项

1. `active_requests` 公开句柄：是复用现有 `_in_flight`/`SessionContext.in_flight`，还是新增/重命名；P2 只允许消费哪个入口。
2. rebind 入口：是否确认 `BrowserSession.transition_to_account(auth_file)` 为唯一控制面入口，并由 P3 在 B2 开工前冻结成功/失败/超时返回语义。
3. B2 内启动顺序：P1 先做非 rebind 部分、P3 再做 rebind，还是 P3 先冻结接口后 P1/P2 并行；当前两包互相前置。
4. D-02：`_spawn_process_watchdog` 按决策恢复，是否直接关闭 §8.1，不再讨论「Job Object 等价」。
5. D-10 有界 drain 上限（现有 12s）是否固定/可配置；D-03 ping 的 `ping_interval`/`ping_timeout` 取值。
6. D-11：prune 遇到仍有 in-flight 的旧账号页时，直接关闭还是保留到 drain 结束。

## 只读声明

未修改任何任务包、源码或其他文档，未 commit，未访问真实账号/IP/Google。
