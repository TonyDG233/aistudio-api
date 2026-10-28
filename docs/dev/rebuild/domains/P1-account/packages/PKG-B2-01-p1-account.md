# PKG-B2-01 P1 账号 / Cookie / 上下文

- 步骤：B2
- 状态：draft / 未开始；中断时保留本文件与下方 status 行，按 §8 续跑，只完成部分记 `partial`
- 基线：`99c38ba` + `9b5cdea`；目标树 `.kilo/worktrees/rebuild-upstream`（docs HEAD `755e28a`）
- 来源：D-01、D-05、D-08、D-13；D02-001/002/004..011/029..034；`05 §4.1 B2`
- 交叉验证：D02-021..024/025/026/028（paid 种子、surface/chat-only、channel_affinity、模型目录投影）与 D02-015/016/017（refresher/24h 调度）、D02-003（`acc_migrated` 迁移）、D02-019（`duplicate_of`）在目标树**无对应文件或字段**（无 `model_access.py`、`surface_orchestrator.py`、`routing_eligibility.py`、`account_info_refresher.py`；`AccountMeta` 仅 `:95-134` 现有字段），标「不适用/参考」，不得当实现路径；D02-027（accessModes）在 `model_discovery.py` 存在但归 P2；D02-018（OAuth）drop/defer。
- status：`scope=frozen, files=pending, tests=pending, evidence=pending`

## 1. 范围

做：
- 注册表/元数据原子化：`account_store.py:18-65` 的 `_atomic_write_json` 吞异常，改为失败上抛；`auth.json`/`meta.json`/`registry.json` 不得半写。元数据只保留目标树现有字段（`:95-134`），不预埋 `expired`/`keepalive`/`routing`/`tags`（D02-002 的扩展字段由 B3/P2 各自按需引入）。
- Cookie 内容层：解析与域优先级（`cookie_parser.py:111-307`）、注入/回写合并（`session_auth.py:119-157`、`:366-393`）、身份校验防错绑（`session_auth.py:241-363`）；只改内容与合并规则。
- enabled/group：`toggle_account_enabled`（`:566`）、`update_account_group`（`:582`）、`list_groups`（`:611`）只写 registry；启用/停用/预设**必须触发有序 rebind**（D-05）。
- active fallback 修复（D-05 主缺陷）：`get_active_account`（`:354-373`）现在会写 registry 兜底到首个 enabled，改为只读；回退只能在 rebind 成功后显式提交。`routes_accounts.py:446-469` 现「toggle → `get_active_account`（已改 registry）→ `try_switch_account`」使切换被当作已完成（`account_orchestrator.py:50-65,104-117` 早退），浏览器仍持旧 cookie。
- presets 锁与写失败（D-08）：`_load_presets`（`:642-666`）持 `self._lock = threading.Lock()`（`:253`）时调 `_save_presets`（`:668-672`）→ 自死锁；拆「纯加载」与「显式加锁提交」，写失败上报并清临时文件。
- D-13：删 notes 与批量分组——`update_account_notes`（`:597`）、`batch_toggle_group`（`:619`）、`PUT /{id}/notes`（`routes_accounts.py:486-496`）、`POST /groups/{group}/toggle`（`:507-520`）、web `updateNotes`/`batchToggleGroup`（`api/accounts.ts:93-105`）、`types/accounts.ts:14`、`types/rotation.ts:25`；保留分组 CRUD 与过滤 UI（`AccountsView.vue:192-220`）。
- D-01 口径：目标树无 surface 维度，配额键保持 `model`（`account_rotator.py:174-181`）；`quota_scope=account_surface` 不适用，不新增 surface/实例/Context。

不做：页面/Context 创建关闭与 drain（P3）；routing/retry（P2）；paid 与 refresher/keepalive（B3/P5）；BotGuard 激活重试回滚（D02-012）。

## 2. 文件域

修改：`infrastructure/account/account_store.py`（原子写、`get_active_account`、presets 锁、notes/batch 删除）、`infrastructure/gateway/session_auth.py`（cookie 内容，**共享文件、冲突风险高**：P3 同改，本包不碰建页/导航）、`application/account_service.py`（去 notes/batch，toggle/preset 走 rebind）、`application/account_orchestrator.py`（**共享、高风险**：PKG-B2-03 主改候选/重试，本包只碰 `:50-65,104-117` 成功判定）、`api/routes_accounts.py`（toggle/preset 顺序、删 notes/batch 路由）、web `api/accounts.ts`/`types/accounts.ts`/`types/rotation.ts`/`views/AccountsView.vue`。
新增：`tests/unit/test_account_rebind_order.py`、`tests/unit/test_account_presets_write_failure.py`。改写：`tests/unit/test_account_presets.py:43-101`（现断言「禁 a1 后 `get_active_account` 自动回落 a2」正是 D-05 缺陷）、`tests/unit/test_routes_accounts.py`（删端点后 404/405）。

不改：`cookie_parser.py`（无缺口则零改动）、`config.yaml`（归 B1）、`account_rotator.py` 打分逻辑。

## 3. 依赖

- 前置：`PKG-B1-01` 通过（启动装配/guard）。
- 冻结接口（`PKG-B2-02` 提供）：其 §1 已认领控制面 rebind（RETIRING→停新请求→一次有界等待→关旧页→再建新页），但未命名入口；本包按候选 `BrowserSession.transition_to_account(auth_file)`（`session.py:182-272`）对接，由 P3 冻结。P1 只负责调用顺序与「切换成功后提交 registry」，不实现 drain/建页。
- 配置：`data/accounts/presets.json` 位置不变（D-08）。fixture：`tmp_path` + FakeSession/假 browser；禁止真实账号/IP/Google。

## 4. 测试

| 用例 | 输入 | 期望 | 方式 |
|---|---|---|---|
| `test_account_rebind_order.py::test_disable_commits_registry_after_switch` | 活跃 A 停用，候选 B | 先 switch 后 registry=B | 离线 |
| `...::test_disable_switch_failure_restores_enabled` | switch 抛错 | 异常显式返回，A 仍 enabled，registry 仍 A | 离线 |
| `...::test_get_active_account_is_read_only` | 活跃账号被禁用 | 返回 None 且 registry 不变 | 离线 |
| `test_account_presets.py::test_apply_preset_active_requires_rebind` | preset 禁当前活跃 | 无「registry 先改后切」 | 离线 |
| `test_account_presets_write_failure.py::test_presets_write_error_surfaces` | 只读目录/写抛错 | 上报失败、无 `.tmp.*` 残留 | 离线 |
| `...::test_presets_load_no_deadlock` | 并发 `list_presets` | 限时内完成 | 离线（timeout 守卫） |
| `test_routes_accounts.py::test_notes_and_batch_group_endpoints_removed` | `PUT /notes`、`POST /groups/x/toggle` | 404/405 | 离线 |

授权要求：全部离线；真实账号测试须另行审批，且不得替代本包验收。

## 5. 命令与证据

```powershell
# workdir = .kilo/worktrees/rebuild-upstream
uv run pytest tests/unit/test_account_presets.py tests/unit/test_account_rebind_order.py tests/unit/test_account_presets_write_failure.py tests/unit/test_routes_accounts.py tests/unit/test_cookie_import.py -q
uv run pytest -q
uv run ruff check src tests
uv run pyright
```
`test_cookie_import.py` 为回归（既有 15 例不改断言）。证据 `docs/dev/evidence/B2/2026-10-29/PKG-B2-01/`：pytest 日志、切换顺序 trace、presets 写失败输出。脱敏：不落 cookie、邮箱、API Key、真实 IP。

## 6. 失败停止

- registry 与浏览器会话不一致（UI 显示 B、session 仍 A）→ 停。
- 出现「先改 registry 再切」或「无界等待」路径 → 停。
- `list_presets` 仍可死锁，或写失败被吞 → 停。
- 仍需在 P1 内实现 drain/建页 → 停（越界 P3）。
- 用例重试 >2 次、需新增 D-xx 或改 `05` 顺序 → 停并上报用户。

## 7. 回滚

回滚点：包开始前目标树 HEAD。文本改动按 §2 逐文件 `git checkout --`。数据回滚：先备份 `data/accounts/registry.json`、`data/accounts/presets.json`，失败即恢复，不得删账号目录。web 侧只删 notes/batch 调用，回滚须前后端同时回退（否则类型不一致）。回滚后重跑 §5 全量。

## 8. 未决

1. rebind 入口未冻结：PKG-B2-02 §1 认领了控制面 rebind，但 §2 文件域只有 `session.py`/`session_state.py`，未给入口名；需其落地时冻结 `transition_to_account(auth_file)`。P3 未按期交付时本包只到「`get_active_account` 只读 + 显式错误」，不得自建 drain。
2. D02-020（名称/tag 更新与 tag 目录）台账 decision=drop：是否连重命名一起删？`EditAccountModal` 现依赖 rename，需裁决。
3. D-05 有界等待超时值（旧 fork 12s）与超时后强制关页口径，须与 D-10 一致后冻结。
4. presets 写失败的可观测形式（500 vs 409）未定；D-08 只要求上报且不留半写文件。
5. D02-010 删除账号的 active 迁移是否纳入 D-05 有序 rebind（现有测试只断言 registry 迁移）。
