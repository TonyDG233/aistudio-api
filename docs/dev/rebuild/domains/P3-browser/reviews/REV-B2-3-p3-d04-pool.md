# REV-B2-3 — P3-D04 多实例池（InstancePool + InstanceManager）只读审查报告

- 步骤：B2-3（P3 / D04：1 账号 + 1 BrowserContext + 1 Page + 1 surface；InstancePool + InstanceManager；D-05 控制面 rebind）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`，HEAD `a606fbe`；本步改动**尚未提交**
- 主仓留档：`D:\LLM\self_programming\aistudio-api`，HEAD `0775b2d`
- 权威依据：目标树 `AGENT.md`；`06-decision-log.md` P3 章节（177-202）与 D2/D3/D4 裁决；`08-09-decision-record.md` D-05/D-10/D-11；`06-feature-index.csv` D04-001..D04-062；`06-test-disposition.csv` 的 `test_instance_pool` / `test_instance_manager` / `test_routes_instances` / `test_routes_account_presets` 行
- 审查方式：只读；对照实际未提交 diff、生产代码、测试断言与实跑结果；**未修改任何代码/配置/测试，未 commit**
- 审查日期：2026-10-29（初审）；2026-10-29（B-01/B-02/B-03 局部返工复审）

## 1. 结论

**PASS**

初审提出的 3 个阻塞项均已按返工说明修复并复核成立；本轮未发现新阻塞。B2-3 主链满足 D04 / D-05 的当前口径：单实例单 Page、Free/paid 双 lane、`active_requests` 唯一并发原语、SLEEPING/UNSTARTED 唤醒、D-05 有界 RETIRING drain、先关 Page 再 dispose Context、lifespan 接线与 `/api/instances` 字段均已实现。

本轮实跑：**381 passed / 2 failed / 1 skipped**（2 failed 仍为既有 Windows 平台差异，1 skipped 为 POSIX-only watchdog）；`ruff` 0；`pyright src tests` **0 errors, 0 warnings**；`git diff --check` 退出码 0。

### 1.1 B-01/B-02/B-03 局部返工复核记录（2026-10-29）

| 项 | 复审结果 | 证据 |
|---|---|---|
| **B-01** Ultra_20X lane 误判 | **已修复**：`PAID_TIERS` 增加 `ultra_20x`，`instance_lane_for_tier("Ultra_20X")` 现为 `paid`；`test_model_access.py` 已断言 `account_is_paid("Ultra_20X") is True` 与 `instance_lane_for_tier("Ultra_20X") == "paid"` | `model_access.py:29`；`:103-122`；`test_model_access.py:52-56` |
| **B-01** Ultra_20X 账户层不支持 | **已修复**：新增 `normalize_benefit_tier` / `_CANONICAL_TIERS`；`BENEFIT_TIER_TO_HEADER["Ultra_20X"] = "TIER2"`；`get_tier_header_value` 统一走 normalize；`AccountStore.update_account_tier` 改用 normalize 并接受 `Ultra_20X` | `cookie_parser.py:29-49,72-77`；`account_store.py:650-669`；`test_cookie_import.py:388-390`；本地实跑 `update_account_tier("ultra_20x")` 返回 True 且持久化为 `Ultra_20X` |
| **B-02** D-05 失败回滚清空目标记录 | **已修复**：新增 `target_restored` 标记；仅当 `target_record` 未被成功恢复时才清空 `account_id/state`（`instance_manager.py:532-548`）。单槽失败路径已补用例：禁用旧 Free 账号、`count=1`、`ctx-2` 失败，断言 store active、record.account_id、state 均为旧账号/ACTIVE | `instance_manager.py:532-548`；`tests/unit/test_instance_manager.py:392-430` |
| **B-03** 9 个新增 pyright 错误 + 4 个既有错误 | **已修复**：`pyright src tests` 本轮为 `0 errors, 0 warnings, 0 informations`；fake CDP/session 使用 cast，Optional 取值前断言非 None，既有 BotGuard/features_config 错误一并收窄 | 实跑输出 `0 errors`；`test_instance_manager.py:85,100,254,292,335,376,424`；`test_instance_pool.py:76,93,158-159`；`test_routes_account_presets.py:79,82,255,315`；`test_botguard_bootstrap.py`、`test_features_config.py` |
| pytest | **通过**：`2 failed, 381 passed, 1 skipped in 5.98s`；2 failed 为既有 Windows 差异，1 skipped 为 POSIX-only watchdog | 本轮实跑 |
| ruff | **通过**：`All checks passed!` | `uv run ruff check src tests` |
| git 洁净 | **通过**：`git diff --check` 退出码 0（仅 LF→CRLF 提示）；B2-3 仍未 commit | 本轮实跑；HEAD `a606fbe` |

> 备注：`test_cookie_import.py` 本轮补了 `normalize_benefit_tier` 与 `TIER2` 断言，但未直接断言 `update_account_tier("Ultra_20X")` 的持久化；该路径已由本地实跑确认，属非阻塞测试缺口（N-03）。

## 2. 实际执行命令与结果

| 命令 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | **2 failed, 381 passed, 1 skipped in 5.98s**。失败仍为既有 Windows 差异：`test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`（CloakBrowser 路径）与 `test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（`bun.CMD` 报 `\n was unexpected at this time`）；1 skipped 为 POSIX-only watchdog |
| `uv run ruff check src tests` | `All checks passed!`（0 error） |
| `uv run pyright src tests` | **`0 errors, 0 warnings, 0 informations`** |
| `git diff --check` | 退出码 0；无空白错误，仅有 `model_access.py`、`session.py`、`test_botguard_bootstrap.py`、`test_features_config.py`、`test_model_access.py`、`test_routes_account_presets.py` 的 LF→CRLF 提示 |
| `git status --short` | 目标树 14 个已跟踪文件修改 + 7 个未跟踪新代码/测试文件 + 本报告；**未 commit**。主仓仅有本报告未跟踪 |
| `git diff --stat` | `14 files changed, 494 insertions(+), 66 deletions(-)`（未跟踪的 7 个新代码/测试文件另计） |
| 目标树/主仓 HEAD | 目标树 `a606fbe`；主仓 `0775b2d` |

## 3. 逐项核对表

### 3.1 实例边界、lane 与配置

| 检查点 | 结论 | 证据 |
|---|---|---|
| 1 实例 = 1 账号 + 1 BrowserContext + 1 Page + 1 surface | 通过 | `instance_pool.py:94-156`（slot 只持一个 `PooledSession`/一个 Page）；`pooled_session.py:52-100` 每次 bootstrap 只建一页；`instance_pool.py:248-265` snapshot `pages` 为 0/1；`test_instance_pool.py:151-171` 断言单页且 Page 先于 Context 关闭 |
| 同账号不得 chat/build 双记录/双页 | 通过 | `instance_manager.py:246-253` 创建前 `_has_record_for_account`；`:848-856` 账号唯一性；`startup` 用 `used` 集合（`:156,186`）；`reassign` 同样排他（`:360-361`）；`switch_to` 关闭旧 runtime 后在同一 slot 重建（`:331-341`）；`test_instance_manager.py:196-215` 断言同账号只 ACTIVE 一个实例 |
| 无第二 channel/sibling/`close_channel` 旧逻辑 | 通过 | 新文件 `instance_pool.py`/`instance_manager.py`/`domain/instances.py`/`routes_instances.py` 无 `channel` 字段或 sibling/close_channel API；`test_instance_pool.py:279-292` 显式断言 legacy 符号不存在；全仓 `channel` 命中仅在文档/既有骨架，不在本步新增实现 |
| Free/Pro lane 严格隔离，Free 不占 Pro 配额 | 通过（B-01 已修复） | `instance_manager.py:158` 由 `spec.min_tier` 推导 lane；`:230-244` 按 lane 列候选；`:651-673` `_pick_account` 同时校验 lane 与 `tier_satisfies_min`；`model_access.py:29,103-122` 现把 `Ultra_20X` 归入 paid；`test_instance_manager.py:142-193`、`test_instance_pool.py:126-147`、`test_model_access.py:52-56` 覆盖默认 Free/Pro 与 Ultra_20X |
| 消费 B1 `get_configured_instance_specs` 与 `max_contexts` | 实现通过，测试部分缺失 | `app.py:186-203` 用 `specs_provider=get_configured_instance_specs`、`max_contexts_provider=get_configured_max_contexts`；`model_defaults.py:180-198,1140-1146` 提供归一化；`defaults/config.yaml:18` 新增 `max_contexts: 2`；没有针对 provider/校验的单元测试（见 N-03） |
| 实例配置只由 `count/surface/min_tier` 决定，全局只做上限 | 通过 | `InstanceSpec.from_mapping` `instance_pool.py:59-69`；`add_slot` 容量只按实例数（`:282-287`）；`startup` 按 spec count 建记录（`:157-201`）；无 group/quota/affinity 逻辑 |

### 3.2 `active_requests` 唯一并发原语与遗留删除

| 检查点 | 结论 | 证据 |
|---|---|---|
| 只保留 `active_requests` 作为准入原语 | 通过 | `instance_pool.py:94-156`（`active_requests`/`active_zero`）；`:422-436` begin；`:147-156` acquire/release；pool 内无 lease/reservation/borrow/defer/pinned/COOLDOWN |
| 成功/失败/取消/流关闭 finally 释放 | 部分通过（消费者待 P2） | `ActiveRequest.release` 幂等（`instance_pool.py:87-91`）；本步只提供句柄，没有 P2 请求执行管线；`begin_request`/`wake_eligible`/`eligible_instances` 在 `src` 内暂无生产调用方（grep 仅定义与实例路由），最终 `finally` 释放需 B2-4 保证，见 N-02 与第 5 节 |
| 有在途拒绝普通手动关闭 | 通过 | manager 先检查（`instance_manager.py:297-305`），pool 在锁内二次检查（`instance_pool.py:487-499`）；`test_instance_manager.py:238-256`、`test_instance_pool.py:174-197` |
| 有在途拒绝手动改绑/切换 surface | 部分通过 | manager 的 `switch_to`/`reassign`/`restart` 先检查 `slot.active_requests`（`instance_manager.py:327,363,388`），但 `pool.activate` 在锁内不再检查；存在极窄 TOCTOU，见 N-01 |
| 删除 lease/reservation/30s-15s/borrow/defer/pinned/COOLDOWN/retire_idle/`_runtime_occupancy`/InstanceSwitcher | 通过 | `test_instance_pool.py:279-292` 断言 `InstanceLease`、`acquire/release/rebind/retire_idle/borrow`、`reservations/pinned/pending_pages`、`COOLDOWN` 均不存在；`instance_pool.py` 全文无这些符号；`instance_manager.py` 无 `_runtime_occupancy`/InstanceSwitcher |
| 不得第二套并发账本 | 基本通过，需 P2 约束 | pool 只有 `active_requests`；`pooled_session.py:50,111-122,213-226` 的 `_in_flight`/`SessionContext.in_flight` 是既有会话/流诊断计数，不参与池准入；B2-4 不得用其替代 `active_requests` |

### 3.3 SLEEPING/UNSTARTED、唤醒、手动切换、容量

| 检查点 | 结论 | 证据 |
|---|---|---|
| 候选包含 SLEEPING/UNSTARTED，消息唤醒所有合格实例 | 通过（尚未接 P2） | `eligible_instances` 包含 ACTIVE/STARTING/SLEEPING/UNSTARTED（`instance_manager.py:230-244`）；`wake_eligible` 遍历所有匹配 lane/surface 的 UNSTARTED/SLEEPING 并逐个激活（`:420-461`）；`test_instance_manager.py:167-193,218-235` 覆盖 UNSTARTED 与 SLEEPING 唤醒 |
| 失败/stale 仅手动重启，禁止自动复活 | 通过 | `_sync_records` 将 page closed 标为 INVALID（`instance_manager.py:866-896`）；无后台 stale 复活任务；`restart` 为显式入口（`:382-393`）；`test_instance_manager.py:437-456` 断言 stale 后不会自动新建 Context，手动 restart 才创建 |
| 手动 `switch_to` | 通过（HTTP 有入口） | `instance_manager.py:312-344`；`routes_instances.py:106-115`；`test_instance_manager.py:386-433`、`test_routes_instances.py:157-166` |
| 容量满关闭旧 channel/旧 runtime 后重建 + 失败回滚 | 通过（适配为同 slot 重建） | `switch_to` 先 `_activate(target)`，内部 `pool.activate` 先 `_close_runtime`（`instance_pool.py:331-344`），失败时用旧 surface 再激活（`instance_manager.py:331-341`）；`test_instance_manager.py:418-433` 断言容量满仍只有 1 个 Context；`:386-415` 断言失败回滚到 chat |
| 不得 idle 自动回收 | 通过 | 无 idle timer/后台回收任务；`last_used` 仅记录（`instance_pool.py:113,150-154`）；`retire_idle` 已删除 |

### 3.4 关闭/重载顺序、generation/realm、失败一致性

| 检查点 | 结论 | 证据 |
|---|---|---|
| 严格先关 Page 再 `dispose_browser_context` | 通过 | `instance_pool.py:516-533`：`session.close()`（Page）→ `cdp.dispose_browser_context(context_id)`；`test_instance_pool.py:163-170` 断言 `page.close` 在 `context.dispose` 之前；`test_instance_manager.py:299-302,333-337` 也断言顺序 |
| generation/realm_id 自增并触发缓存清理回调 | 通过 | `_bootstrap_runtime` 计算 `next_generation` 并写回（`instance_pool.py:391-418`）；`realm_id` 为 `slot_id-gN`（`:125-128`）；`_close_runtime` 用旧 realm 调 `_on_slot_retired`（`:516-533`）；`app.py:198` 回调 `client.clear_templates()`；测试 `test_instance_pool.py:163-170,235-251` |
| 失败显式、无 registry/UI/session 不一致 | 通过（B-02 已修复） | `instance_manager.py:532-548` 用 `target_restored` 保护；单槽 `count=1` 失败用例 `test_instance_manager.py:392-430` 断言旧账号恢复为 ACTIVE 且 store active/account_id 一致 |
| 关闭失败显式 | 部分通过 | `_close_runtime` 对 `session.close`/`dispose_browser_context`/回调使用 `suppress(Exception)`（`instance_pool.py:525-533`），清理异常被静默吞掉；见 N-04 |

### 3.5 D-05 控制面 rebind

| 检查点 | 结论 | 证据 |
|---|---|---|
| toggle/apply_preset 的 rebind 信号已接实例池 | 通过 | `routes_accounts.py:79-119` `_run_control_rebind`；`toggle_account` `:651-700`；`apply_preset` `:793-845`；`app.py:261-269` 注册 `InstanceError` 处理器；`test_routes_account_presets.py` 覆盖 applied/unsupported/failure |
| `RETIRING` → 停新请求 → 一次有界等待 active==0（超时强关） | 通过 | `instance_pool.py:451-485` 在 `lifecycle_lock` 内置 RETIRING，单次 `wait_for(active_zero, timeout)`，超时后 `_close_runtime`；`timed_out` 被记录；`test_instance_pool.py:200-231`、`test_instance_manager.py:305-338` |
| drain 期间不建新页 | 通过 | `retire_runtime` 返回前不激活；`rebind_account` 在 `_retire_record` 完成后再 `_activate`（`instance_manager.py:500-523`）；`test_instance_manager.py:299-302,333-337` 断言 `dispose → create`；`test_routes_account_presets.py:244-248` 断言旧 context 被 dispose |
| 关旧页/Context 后再切新 enabled 账号 | 通过 | 同上；目标账号经 `_validate_account`（enabled/未过期/凭据存在，`instance_manager.py:701-714`）；`_find_target_record`（`:792-811`）选择目标记录 |
| 失败显式回滚；不得先改写 active | 通过（B-02 已修复） | active 只在成功激活后 `_set_active_account`（`instance_manager.py:550-552`）；路由失败会回滚 enabled（`routes_accounts.py:684` 与 `:823`）；单槽失败用例 `test_instance_manager.py:392-430` 断言回滚后记录与 store active 一致 |
| 有在途时不得直接拒绝（D-05 例外于普通 close） | 通过 | `rebind_account` 走 `_retire_record` 的 bounded drain，而非 `close_slot` 的 busy 拒绝（`instance_manager.py:500-520,774-790`）；`test_instance_manager.py:259-287` 在 active request 未释放时也能完成切换（drain 等待后） |

### 3.6 lifespan 与 `/api/instances`

| 检查点 | 结论 | 证据 |
|---|---|---|
| lifespan 创建/接线 `runtime_state.instance_manager` | 通过 | `app.py:186-222`：`InstancePool`（provider 注入 specs/max_contexts）→ `InstanceManager` → `runtime_state.instance_manager`；`startup_task` 等待 warmup 后 `manager.startup()` |
| 关闭顺序 keepalive → instance_manager → client | 通过 | `app.py:69-76` `_shutdown_runtime` 依次 `keepalive.stop`、`instance_manager.close`、`client.close`（先取消后台任务） |
| `/api/instances` 字段无 channel/双页 | 通过 | `routes_instances.py:20-35,51-61` 只暴露 `id/number/account_id/state/surface/min_tier/in_flight/latest`；`test_routes_instances.py:126-139` 断言字段集、无 `channel`/`context`/`pages` |
| 错误映射与鉴权 | 通过 | `app.py:235-239` 实例路由挂 `require_web_auth`；`InstanceError` → 409/404 + `{detail:{code,message}}`（`:261-269`）；`test_routes_instances.py:179-202` |

### 3.7 测试是否真正证明

| 目标 | 结论 | 证据/缺口 |
|---|---|---|
| Free/Pro 同时在线隔离 | 通过 | `test_instance_manager.py:142-164` 两 lane 同时 ACTIVE；`:167-193` Free 账号不占 Pro 槽；`test_instance_pool.py:126-147` 两 Context/realm 不同 |
| Ultra_20X lane / 账户层 | 通过 | `test_model_access.py:52-56` 断言 paid/lane；`test_cookie_import.py:388-390` 断言 normalize/header；本地实跑 `update_account_tier("ultra_20x")` 持久化为 `Ultra_20X`；直接持久化断言见 N-03 |
| 同账号单实例 | 通过 | `test_instance_manager.py:196-215`（startup 第二记录 UNSTARTED，只建 1 Context）；`create` 重复拒绝路径无直接用例（部分缺口） |
| `active_requests` 释放/忙拒绝 | 通过 | `test_instance_pool.py:174-197`（幂等 release、busy close）；`test_instance_manager.py:238-256`（manager close busy） |
| SLEEPING/UNSTARTED 唤醒 | 通过 | `test_instance_manager.py:218-235,167-193` |
| legacy lease/pinned/COOLDOWN 等删除 | 通过 | `test_instance_pool.py:279-292,295-317` |
| 容量满回滚 | 通过 | `test_instance_pool.py:142-144`（add_slot capacity）；`test_instance_manager.py:418-433`（switch 容量满仍 1 Context）；manager `create` 容量失败无直接用例（部分缺口） |
| D-05 成功/超时/失败/不建新页 | 通过 | `test_instance_manager.py:259-383` 覆盖成功、超时强关、失败恢复与事件顺序；新增 `:392-430` 覆盖单槽 `count=1` 失败回滚 |
| 路由端到端 | 通过 | `test_routes_instances.py` 覆盖 list/create/delete/switch/reassign/409/503；`test_routes_account_presets.py` 覆盖 toggle/preset applied/unsupported/failure 与 active/enabled 一致性 |
| `max_contexts`/配置 provider | 缺口 | 无 `get_configured_max_contexts`、默认 2、非法值/上限 16 的测试；无 manager 使用 provider 的集成测试（N-03） |

### 3.8 范围与洁净

| 检查点 | 结论 | 证据 |
|---|---|---|
| 无 P6/P2 路由 | 通过 | 仅新增 `routes_instances.py`；`app.py` 未新增 keepalive/P2 路由；`routes_accounts.py` 改动仅 D-05 |
| 无 `05/06/08-09/archive` 改动 | 通过 | `git status --short` 无 docs 变更；仅代码/配置/测试 |
| 无真实 Google/账号/IP | 通过 | 新测试用本地 `tmp_path` 账号与 `FakeCDP`/`FakeSession`；未访问真实网络 |
| 未 commit | 通过 | HEAD 仍为 `a606fbe`；工作区有 14 个已跟踪修改 + 7 个未跟踪新代码/测试文件 + 本报告 |
| 改动范围符合本步 | 通过 | 修改 `app.py`、`routes_accounts.py`、`model_access.py`、`account_store.py`、`cookie_parser.py`、`model_defaults.py`、`session.py`、`defaults/config.yaml`、`conftest.py` 及 5 个测试文件；未知/无关改动未发现 |

## 4. 问题清单

### 4.1 已修复项（复审确认）

| 项 | 状态 | 复核证据 |
|---|---|---|
| **B-01** `Ultra_20X` 被归入 Free lane，合法 `min_tier` 实例静默不可启动 | **已修复** | `PAID_TIERS` 加 `ultra_20x`（`model_access.py:29`）；`instance_lane_for_tier("Ultra_20X") == "paid"`；`test_model_access.py:52-56`；账户层 `normalize_benefit_tier`/`_CANONICAL_TIERS`/`Ultra_20X: TIER2`（`cookie_parser.py:29-49`），`update_account_tier` 走 normalize（`account_store.py:650-669`）；本地实跑 `update_account_tier("ultra_20x")` 返回 True 且读取为 `Ultra_20X` |
| **B-02** D-05 rebind 失败回滚后无条件清空目标记录，单槽配置留下不一致状态 | **已修复** | `target_restored` 标志（`instance_manager.py:532-548`）：仅目标记录未被成功恢复时才清空；新增单槽 `count=1` 失败用例（`test_instance_manager.py:392-430`），断言 store active、`record.account_id`、state 一致 |
| **B-03** 新增测试 9 个 pyright 错误 + 4 个既有错误 | **已修复** | `uv run pyright src tests` → `0 errors, 0 warnings, 0 informations`；fake CDP/session 使用 cast，Optional 先断言非 None；`test_botguard_bootstrap.py`/`test_features_config.py` 既有错误一并清除 |

### 4.2 保留的非阻塞项

### N-01（非阻塞）manager 检查 `active_requests` 与 `pool.activate` 关闭 runtime 之间存在 TOCTOU

- **位置**：`instance_manager.py:327,363,388` 先检查；`instance_pool.py:331-344` 的 `activate` 在 `lifecycle_lock` 内不再检查 `active_requests`。
- **说明**：`close_slot` 在锁内二次检查（`instance_pool.py:492-494`），因此普通 close 安全；但 `switch_to`/`reassign`/`restart` 在 manager 检查后、`pool.activate` 取得锁前，另一个 `begin_request` 可能把 slot 从 READY 加 1，随后 `activate` 直接 `_close_runtime` 杀掉在途请求。当前 P2 尚未接线，实际触发窗口小。
- **建议**：把 `active_requests == 0` 检查移入 `pool.activate` 的 `lifecycle_lock` 内（普通重载/改绑路径），或明确在 B2-4 使用统一的 close/rebind 入口。

### N-02（非阻塞）手动重启/睡眠/唤醒未暴露 HTTP 路由

- **位置**：`routes_instances.py:73-125` 只有 list/create/delete/switch/reassign；`instance_manager.py:382-393` 的 `restart`、`:395-418` 的 `sleep/wake` 无生产调用方。
- **说明**：`switch_to` 传与当前相同的 surface 可重启 INVALID，但 `/switch` 不传 surface 时默认 `chat`，对 build 实例不是“原 surface 重启”；若 UI 需要明确的手动重启/睡眠/唤醒入口，本步尚未提供。建议在 B2-4/前端接线时补显式 `restart`（或规定 switch 语义）。

### N-03（非阻塞）测试覆盖缺口

- 无 `get_configured_max_contexts` / `max_contexts` 默认值（2）、非法值、上限 16 的测试；无 manager 通过 provider 读取配置的集成测试。
- 无 manager `create` 容量失败/激活失败回滚的测试（现有测试只覆盖 pool `add_slot` 与 route fake）。
- 无 `update_account_tier("Ultra_20X")` 持久化的直接断言（仅 header/normalize 断言 + 本地实跑验证）。
- 无 HTTP `restart`/`sleep`/`wake` 测试（N-02）。

### N-04（非阻塞）`_close_runtime` 静默吞掉清理异常

- **位置**：`instance_pool.py:525-533` 对 `session.close`、`dispose_browser_context`、`_on_slot_retired` 均 `suppress(Exception)`。
- **说明**：关闭/重载主路径满足“先关 Page 再 dispose”，但 dispose 或缓存清理失败不会显式上报，诊断上难以发现 Context 泄漏；建议至少 `log.warning` 记录失败目标（不改变主流程）。

### N-05（非阻塞）`switch_to` 只检查目标 surface 存在，不校验目标 spec 的 `min_tier`/lane

- **位置**：`instance_manager.py:329-330` 仅 `any(spec.surface == target for spec in self._pool.specs)`；`record.min_tier` 保持原值。
- **说明**：若配置只有 `build + Pro` 而没有 Free build 实例，Free 记录仍可切到 build；这属于 P4 build surface 的配置约束，建议 P4 或 B2-4 在切换前校验目标 surface 的 spec/最低 tier，避免绕过配置。

### N-06（非阻塞）自动 benefit-tier 探测仍无法区分 Ultra 与 Ultra_20X

- **位置**：`cookie_parser.py:22-27` 的 `BENEFIT_TIER_TO_NAME` 仍将数值 `2` 映射为 `Ultra`；`parse_benefit_tier_response`（`:52-69`）因此不会自动产出 `Ultra_20X`。
- **说明**：本轮已补齐账户层字符串规范化与持久化，`Ultra_20X` 可由导入请求的显式 `tier`、DOM 提取或 `update_account_tier` 写入；若要求 BenefitTier 探测自动识别 `Ultra_20X`，需要上游提供可区分信号，属后续步骤口径（与 `f2b1e28` 同款最小规范化一致）。

## 5. 用户裁决项

| 项 | 说明 | 建议 |
|---|---|---|
| `max_contexts` 顶层默认 2 | `defaults/config.yaml:18` 新增 `max_contexts: 2`，等于默认 Free+Pro 两个 spec 之和；`_normalize_max_contexts` 缺省回退为 spec count 之和。但 `06-feature-index.csv` D04-062 记录旧口径为“默认 max_contexts=1”。 | 若按 B1 当前双 lane 默认，2 合理；请用户确认是否改写 D04-062 的旧表述或保留 1 的兼容口径。 |
| P2 请求路由未接线 | `eligible_instances`/`begin_request`/`wake_eligible` 在 `src` 内无生产调用方；`AIStudioClient` 仍走控制面 session。 | 属 B2-4 范围；请确认 B2-3 只交付池/管理面、允许 P2 接线后再验收。 |
| build surface 实际页面属 P4 | 当前 `switch_to(..., "build")` 仍由 `PooledSession.bootstrap` 加载 AI Studio chat 页面，不是 P4 的 build 页面。 | 确认 B2-3 不要求 build 实际页面；P4 再落。 |
| 非当前账号 toggle 的退休范围 | `rebind_account` 的 `affected` 包含 `previous_id` 与所有 disabled 记录（`instance_manager.py:486-492`），不只是当前 active。 | 请确认“禁用任意账号时退休其所有已禁用实例”是否符合预期，还是只退休当前 active 记录。 |
| 手动 restart/sleep/wake 的 API 形状 | 见 N-02。 | 若 UI 需要显式入口，请在 B2-4 前补路由；否则确认通过 switch 语义即可。 |
| Ultra_20X 自动探测口径 | 见 N-06：字符串账户层已支持，数值 BenefitTier 探测仍映射 Ultra。 | 确认是否接受“显式导入/DOM/手动更新可设 Ultra_20X，探测自动识别留待上游信号”的当前口径。 |

## 6. 只读声明与状态

- 本次审查**只读**：未修改任何源代码、配置、测试或 git 状态；仅更新本报告（目标树与主仓各一份，内容一致）。
- 实跑命令均为只读检查；未访问真实 Google/真实账号/真实 IP，未启动真实浏览器，未 commit。
- **B2-3 尚未 commit**（目标树 HEAD `a606fbe`，工作区有 14 个已跟踪修改与 7 个未跟踪新代码/测试文件），等待用户审计并确认后再由有权限方提交。
- 结论：**PASS**。初审 B-01/B-02/B-03 已关闭，本轮无新阻塞；保留 N-01..N-06 非阻塞项与第 5 节用户裁决项。

## 7. N-01 / N-02 / N-04 加固记录（2026-10-29）

- **N-01**：`instance_pool.activate` 在 `lifecycle_lock` 内检查 `active_requests > 0`，忙时抛 `InstanceBusyError`；新增 pool 忙时拒绝覆盖测试。
- **N-02**：新增 `POST /api/instances/{instance_id}/restart`，接 `InstanceManager.restart`；新增路由测试。
- **N-04**：`_close_runtime` 的 session close / context dispose / retire callback 异常改为 `log.warning`，不再静默。
- 实测：`383 passed / 2 failed / 1 skipped`；`ruff check src tests` 全绿；`pyright src tests` 0 errors；`git diff --check` 通过。
- 复核结论：**PASS**；N-01/N-02/N-04 已完成，无新增阻塞。B2-3 等待提交。
