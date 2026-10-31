# 实例池与路由技术规格

- 状态：依据当前 `rebuild-upstream` 工作树源码整理，只读核对，不涉及运行态验收。
- 范围：实例池（`InstancePool` / `InstanceSlot` / `PooledSession`）、实例管理器（`InstanceManager`）、Free/Paid 物理隔离、tier 选择器归一化、`active_requests` 并发原语、`RoutingPipeline` 候选排序与错误/改绑路径、`quota_scope` 记账口径、付费模型本地 403 契约。
- 非范围：浏览器启动细节、wire 编解码、日志/保活、ATG 协议细节。这些分别在其它文档中描述。
- 文中 `file:line` 均指本仓库路径；代码行为以当前文件为准，项目决策文档仅用于解释意图。

---

## 目录

- [1. 术语与核心不变量](#1-术语与核心不变量)
- [2. 数据结构与实例生命周期](#2-数据结构与实例生命周期)
  - [2.1 用户可见实例契约](#21-用户可见实例契约)
  - [2.2 Slot 状态机](#22-slot-状态机)
  - [2.3 启动流程](#23-启动流程)
  - [2.4 显式创建、关闭、切换、休眠、唤醒](#24-显式创建关闭切换休眠唤醒)
  - [2.5 PooledSession 生命周期](#25-pooledsession-生命周期)
- [3. Free / Paid 物理隔离](#3-free--paid-物理隔离)
  - [3.1 lane 的推导](#31-lane-的推导)
  - [3.2 选择器本身带 lane](#32-选择器本身带-lane)
  - [3.3 入池时的强制校验](#33-入池时的强制校验)
  - [3.4 管理器层的跨池拒绝](#34-管理器层的跨池拒绝)
  - [3.5 Paid 的 chat-only 限制](#35-paid-的-chat-only-限制)
  - [3.6 默认配置](#36-默认配置)
- [4. only / min / max tier 区间归一化](#4-only--min--max-tier-区间归一化)
  - [4.1 规范化输入](#41-规范化输入)
  - [4.2 归一化规则总表](#42-归一化规则总表)
  - [4.3 配置层 fail-fast 与合并键](#43-配置层-fail-fast-与合并键)
  - [4.4 运行期匹配](#44-运行期匹配)
  - [4.5 max_contexts](#45-max_contexts)
- [5. `active_requests` 并发原语](#5-active_requests-并发原语)
  - [5.1 字段与语义](#51-字段与语义)
  - [5.2 获取句柄](#52-获取句柄)
  - [5.3 忙时拒绝与有界 drain](#53-忙时拒绝与有界-drain)
  - [5.4 关闭顺序](#54-关闭顺序)
  - [5.5 发现会话不使用 `active_requests`](#55-发现会话不使用-active_requests)
- [6. 路由准入与候选排序](#6-路由准入与候选排序)
  - [6.1 请求入口](#61-请求入口)
  - [6.2 `begin` 的准入顺序](#62-begin-的准入顺序)
  - [6.3 候选排序](#63-候选排序)
  - [6.4 “最小满足 tier”的精确定义](#64-最小满足-tier的精确定义)
  - [6.5 `_current_tier` 与 403 判定](#65-_current_tier-与-403-判定)
  - [6.6 切换预算与重试上限](#66-切换预算与重试上限)
- [7. 执行、错误处理与请求期改绑](#7-执行错误处理与请求期改绑)
  - [7.1 `acquire`](#71-acquire)
  - [7.2 `handle_error` 决策表](#72-handle_error-决策表)
  - [7.3 `_switch_or_raise` 与 `_try_rebind`](#73-_switch_or_raise-与-_try_rebind)
  - [7.4 单槽与多槽语义](#74-单槽与多槽语义)
  - [7.5 429 耗尽错误](#75-429-耗尽错误)
  - [7.6 成功记录](#76-成功记录)
- [8. `quota_scope` 双模式记账](#8-quota_scope-双模式记账)
  - [8.1 两种模式](#81-两种模式)
  - [8.2 键构造](#82-键构造)
  - [8.3 记账与可用性](#83-记账与可用性)
  - [8.4 软额度不是硬禁用](#84-软额度不是硬禁用)
  - [8.5 429 冷却](#85-429-冷却)
- [9. Paid 本地 TierRequiredError 403 契约](#9-paid-本地-tierrequirederror-403-契约)
  - [9.1 领域错误](#91-领域错误)
  - [9.2 本地触发点](#92-本地触发点)
  - [9.3 不触发切换](#93-不触发切换)
  - [9.4 协议渲染](#94-协议渲染)
  - [9.5 与其他错误的边界](#95-与其他错误的边界)
- [10. 时序图（文本）](#10-时序图文本)
  - [10.1 启动与 lane 绑定](#101-启动与-lane-绑定)
  - [10.2 正常请求](#102-正常请求)
  - [10.3 多槽 429](#103-多槽-429)
  - [10.4 单槽 429 / 401 / 二次 403](#104-单槽-429--401--二次-403)
  - [10.5 Paid tier 不足](#105-paid-tier-不足)
- [11. 残余限制](#11-残余限制)
- [12. 证据索引](#12-证据索引)

---

## 1. 术语与核心不变量

| 术语 | 含义 | 证据 |
|---|---|---|
| Instance | 用户可见实例：一个账号 + 一个 BrowserContext + 一个 Page + 一个 surface | `src/aistudio_api/domain/instances.py:18-33` |
| InstanceSlot | 池内运行槽位，持有账号、context、page、session、`active_requests` | `src/aistudio_api/infrastructure/gateway/instance_pool.py:138-214` |
| PooledSession | 单个 BrowserContext 的会话外壳，持有 page、cookie jar、transport | `src/aistudio_api/infrastructure/gateway/pooled_session.py:42-58` |
| Lane | `free` / `paid` 两条物理隔离的实例池 | `src/aistudio_api/domain/model_access.py::account_is_paid`, `::instance_lane_for_tier` |
| `active_requests` | 唯一并发原语；一个实例同一时刻只允许一个请求句柄 | `instance_pool.py:1-11`, `:205-214` |
| RouteCandidate | 路由阶段冻结的候选快照 | `src/aistudio_api/application/routing_pipeline.py:150-183` |
| 最小满足 tier | 满足模型 `required_tiers` 的最低账号 tier | `domain/model_access.py:295-317` |
| 改绑（rebind） | 不新建实例，原地关闭旧 Page/Context 后激活同槽位的新账号 | `instance_pool.py:527-561`, `instance_manager.py:389-425` |

核心不变量：

1. 一个实例只对应一个账号、一个 Context、一个 Page、一个 surface（`instance_pool.py:1-11`, `:138-166`）。
2. Free 与 Paid 是物理隔离的两条 lane，lane 由账号 tier 推导，不由模型名推导（`domain/model_access.py::account_is_paid`, `::instance_lane_for_tier`）。
3. 实例创建时必须满足 lane 与 tier 区间；跨 lane 改绑在管理器层直接拒绝（`instance_pool.py:336-343`, `instance_manager.py:395-404`）。
4. 同一账号不能同时占用两个实例；`create` / `reassign` 均检查（`instance_manager.py:291-294`, `:405-406`）。
5. 没有 lease / reservation / pinned / defer / 后台 rebalance；只有 `active_requests`（`instance_pool.py:7-10`, `routing_pipeline.py:5-6`）。
6. 关闭/退役实例必须先关闭 Page，再销毁 BrowserContext，最后触发 realm 级缓存清理（`instance_pool.py:592-627`）。
7. 路由切换最多消耗 `features.routing.max_switches`（默认 2）；同账号重试不消耗切换预算（`routing_safety.py:13-49`, `routing_pipeline.py:651-678`）。
8. 一旦流式响应已经向客户端 yield 过数据，不再切换实例（`routing_pipeline.py:633-640`, `:649-650`, `:667-668`, `:720-721`）。

---

## 2. 数据结构与实例生命周期

### 2.1 用户可见实例契约

`Instance` 只暴露用户 API 需要的字段（`domain/instances.py:18-33`）：

- `id` / `number`：稳定编号，编号在管理器内单调递增（`instance_manager.py:949-973`）。
- `account_id`：当前绑定账号。
- `state`：`starting` / `active` / `sleeping` / `unstarted` / `reclaiming` / `invalid`（`domain/instances.py:9-15`）。
- `surface`：`chat` / `build`。
- `min_tier` / `only_tier` / `max_tier`：归一化后的 tier 选择器视图。
- `in_flight`：来自 slot 的 `active_requests`（`instance_manager.py:1031-1047`）。
- `latest`：最近一次成功激活的实例。
- `tier` / `last_used`：来自 slot 的运行态字段。

`InstanceList` 额外携带全局 `max_instances`、`latest_instance_id`、`current_instance_id`（`domain/instances.py:36-41`）。

### 2.2 Slot 状态机

`SlotState` 定义（`instance_pool.py:38-46`）：

```text
UNSTARTED --add_slot--> UNSTARTED
UNSTARTED/SLEEPING/INVALID --activate--> STARTING --成功--> READY
STARTING --失败--> INVALID
READY --sleep--> SLEEPING
READY/SLEEPING --close_slot/retire--> RETIRING --close--> UNSTARTED
READY --page 被关闭--> INVALID
```

管理器侧的 `InstanceState` 映射（`instance_manager.py:1016-1029`）：

| SlotState | InstanceState |
|---|---|
| `STARTING` | `starting` |
| `READY` | `active` |
| `SLEEPING` | `sleeping` |
| `RETIRING` | `reclaiming` |
| `INVALID` | `invalid` |
| 其他 | `unstarted` |

`_sync_records` 会在列表/查询时把 slot 状态同步到 record，并检测 page 已关闭的 READY slot（`instance_manager.py:999-1014`）。

### 2.3 启动流程

`InstanceManager.startup()`（`instance_manager.py:164-232`）：

```text
get_active_account()
ordered_accounts = active 账号优先 + 其余账号
for spec in pool.specs:
    for i in range(spec.count):
        slot = pool.add_slot(InstanceSpec(count=1,...), lane=spec.lane)
        record = new_record(slot, surface, only/min/max, lane)
        account = pick_account(spec, accounts, used)
        if account is None: record=UNSTARTED; continue
        used.add(account.id)
        activate(record, account)
```

关键点：

- `pool.add_slot` 在超过 `max_contexts` 时抛 `InstanceCapacityError`，启动循环记录日志后 `break`（`instance_manager.py:186-191`, `instance_pool.py:344-349`）。
- 账号选择要求：未使用、enabled、未 expired、满足 tier 区间、auth 文件存在（`instance_manager.py:773-792`）。
- 启动失败不会自动重启；后续只能人工 `restart` / `wake`（`instance_manager.py:1-12`）。
- 启动完成后选择当前实例、修复 latest/current（`instance_manager.py:221-232`）。

### 2.4 显式创建、关闭、切换、休眠、唤醒

| 操作 | 行为 | 证据 |
|---|---|---|
| `create` | 校验账号；同账号已有实例则拒绝；按账号 tier 选 lane；按账号 tier 匹配 spec；异步启动 | `instance_manager.py:287-317` |
| `close` | `STARTING` 取消任务；`active_requests>0` 拒绝；否则 `close_slot` 并删除 record | `instance_manager.py:319-353` |
| `switch_to` | 同一 surface 直接激活；跨 surface 先关旧 Page/Context，再激活新 surface；失败回滚旧 surface | `instance_manager.py:355-387` |
| `reassign` | 校验 lane/tier/重复账号/忙；激活新账号；失败回滚旧账号 | `instance_manager.py:389-425` |
| `restart` | 忙则拒绝；重新激活当前账号 | `instance_manager.py:427-438` |
| `sleep` | 忙则拒绝；关闭运行态并保留槽位 | `instance_manager.py:440-449` |
| `wake` | 仅 `UNSTARTED` / `SLEEPING` 可唤醒；要求账号 enabled | `instance_manager.py:451-463` |
| `wake_eligible` | 消息到达时唤醒同 lane/surface 的所有可唤醒实例 | `instance_manager.py:465-505` |
| `rebind_account` | D-05 控制面改绑：旧实例进入 RETIRING、有界 drain、超时强制关闭，成功后切换账号 | `instance_manager.py:507-608` |

`_activate` 的失败分类（`instance_manager.py:843-898`）：

- tier 不匹配 -> `InstanceInvalidAccountError`，record=`INVALID`。
- pool busy -> `InstanceBusyError`，record 恢复原状态。
- capacity/其他异常 -> `InstanceNotReadyError`，record=`INVALID`。
- 成功 -> `ACTIVE`、更新 `started_at`、`latest`、必要时 `current`。

### 2.5 PooledSession 生命周期

`PooledSession.bootstrap`（`pooled_session.py:64-114`）：

1. 在指定 Context 创建 Page，`block_assets=True`。
2. 加载 cookie 到 Page，导航到 AI Studio。
3. 运行 BotGuard + 模板捕获，带 90s 超时。
4. 从 auth 元数据读取 tier，构造 `SessionContext`。
5. 返回持有 `XHRStreamTransport` 的 session。

`request_scope`（`pooled_session.py:128-136`）维护会话内部 `_in_flight` 与 `SessionContext.in_flight`；`generate_content` / `stream_generate_content` 都包在该 scope 内（`instance_client.py:136-171`, `:241-265`）。

注意：`PooledSession._in_flight` 与 `InstanceSlot.active_requests` 是两个层次。前者是会话内部计数，后者是池级请求句柄；路由/关闭/退役判断使用后者。

`close` 清空模板缓存并关闭 Page（`pooled_session.py:264-267`）。

---

## 3. Free / Paid 物理隔离

### 3.1 lane 的推导

`model_access.py:40-61`：

- `PAID_TIERS = {pro, ultra, plus, paid, ultra_20x}`。
- `_TIER_RANK`：`free=0, plus=1, pro=2, ultra=3, ultra_20x=4, paid=2`。
- `instance_lane_for_tier(tier)`：paid tier -> `"paid"`，其余 -> `"free"`（`model_access.py:283-285`）。

因此 lane 只看账号 tier，不看模型名，也不看请求路径。

### 3.2 选择器本身带 lane

`TierSelector` 包含 `lane`、`only_tier`、`min_tier`、`max_tier`（`model_access.py:71-86`）。`resolve_tier_selector` 的返回值始终带 lane（`model_access.py:119-188`）：

- Free 选择器 -> `lane="free"`。
- 付费选择器 -> `lane="paid"`。
- Free/paid 混合 -> 抛 `TierSelectorError`（`model_access.py:172-176`）。

### 3.3 入池时的强制校验

`InstanceSpec.from_mapping` 解析选择器（`instance_pool.py:61-97`）；`InstanceSpec.lane` 返回 `selector.lane`（`instance_pool.py:108-110`）。

`InstancePool.add_slot`（`instance_pool.py:327-364`）：

- surface 必须为 `chat`/`build`。
- lane 必须为 `free`/`paid`。
- `normalized.lane != lane` -> `ValueError("instance tier selector lane ... cannot use ... slot")`。
- 超过 `max_contexts` -> `InstanceCapacityError`。

`InstancePool.activate`（`instance_pool.py:366-460`）：

- `account_lane != slot.lane` -> `InstanceTierError`。
- `not slot.accepts_tier(tier)` -> `InstanceTierError`。
- 已 READY 且账号/auth/surface 相同且 Page 未关闭 -> 复用，不重启（`:404-412`）。
- `active_requests > 0` -> `InstanceBusyError`（`:414-417`）。
- 否则关闭旧运行态、置 STARTING、启动（`:419-447`）。

### 3.4 管理器层的跨池拒绝

- `create`：`lane = instance_lane_for_tier(account.tier)`，再用 `_matching_spec` 找同 surface 且接受该 tier 的 spec（`instance_manager.py:295-304`）。
- `reassign`：`instance_lane_for_tier(account.tier) != record.lane` -> `InstanceInvalidAccountError("Free/paid 实例池互相隔离，不能跨池改绑账号")`（`instance_manager.py:395-398`）。
- `reassign` 还要求 `record.accepts_tier(account.tier)`（`:399-404`）。
- `_activate` 再次检查 `record.accepts_tier`（`:851-856`）。
- `eligible_instances(paid=...)` 只返回对应 lane（`instance_manager.py:249-267`）。
- `discovery_sessions` 固定 Paid 优先、同 lane 空闲优先（`instance_manager.py:628-651`）。

### 3.5 Paid 的 chat-only 限制

`is_chat_only_model` 对 Paid 模型返回 True（`model_access.py:255-261`），`surface_capability` 把能力面限制为 `chat`（`model_access.py:328-338`）。`resolve_model_route` 在 surface 不在能力面时抛 `SurfaceCapacityError`，错误消息带 `[ROUTING-RULE: PAID_CHAT_ONLY]`（`routing_eligibility.py:221-233`）。

`RoutingPipeline.begin` 还显式拒绝 `build` surface（`routing_pipeline.py:326-330`），因此当前版本 Paid 模型不会落到 build 实例。

### 3.6 默认配置

`defaults/config.yaml:8-18` 默认两条实例：

```yaml
instances:
  - count: 1
    surface: chat
    only_tier: Free
  - count: 1
    surface: chat
    min_tier: Pro
max_contexts: 2
```

这对应默认 1 个 Free 槽位 + 1 个 Paid 槽位；Paid 槽位接受 `Pro..Ultra_20X`。

---

## 4. only / min / max tier 区间归一化

### 4.1 规范化输入

`canonical_tier` 将输入 trim、转小写、空格/连字符转下划线，再映射到规范标签（`model_access.py:88-93`）：

| 输入示例 | 规范标签 |
|---|---|
| `free` | `Free` |
| `plus` | `Plus` |
| `pro` | `Pro` |
| `ultra` | `Ultra` |
| `ultra_20x` / `ultra20x` | `Ultra_20X` |

`_selector_label` 对非字符串、未知 tier 直接抛 `TierSelectorError`（`model_access.py:96-116`）；空字符串视为未提供。

### 4.2 归一化规则总表

`resolve_tier_selector`（`model_access.py:119-188`）：

| 输入 | lane | 结果区间 | 说明 |
|---|---|---|---|
| 三者都没有 | — | 抛错 | 至少需要一个选择器（`:135-138`） |
| `only_tier: Free` | free | `Free..Free` | `:141-142`, `:154-155` |
| `only_tier: Ultra` | paid | `Ultra..Ultra_20X` | `:143-144`，Ultra 家族整体接受 |
| `only_tier: Pro` / `Plus` / `Ultra_20X` | paid | 精确单 tier | `:145-146` |
| `only_tier` + 非等价 min/max | — | 抛错 | `:147-153` |
| `max_tier: Free`（无 min/only） | free | `Free..Free` | `:157-159` |
| `max_tier` 单独且低于 Pro | — | 抛错 | `:160-164` |
| `max_tier` 单独且 >= Pro | paid | `Pro..max` | `:165` |
| `min_tier: Free`（无 max/only） | free | `Free..Free` | `:167-169` |
| `min_tier` 单独且为付费 tier | paid | `min..Ultra_20X` | `:170` |
| `min=Free, max!=Free` 或 `max=Free, min!=Free` | — | 抛错 | Free/Paid 不能混合（`:172-176`） |
| `min=Free, max=Free` | free | `Free..Free` | `:174-175` |
| `min > max` | — | 抛错 | `:177-180` |
| 付费 `min < Plus` | — | 抛错 | `:181-182` |
| 付费 `max < Pro` | — | 抛错 | `:183-187` |
| 合法付费 `min..max` | paid | `min..max` | `:188` |

关键设计：

- `only_tier: Ultra` 被展开为 `Ultra..Ultra_20X`，而不是单个 `Ultra`；这是为了接受规范化的 Ultra 家族标签（`model_access.py:71-80`, `:143-144`）。
- `only_tier: Pro` 保持精确 Pro，不会接受 Ultra（`model_access.py:145-146`）。
- `max_tier` 单独配置默认下界为 Pro，避免出现无法满足付费准入的区间（`model_access.py:160-165`）。
- Free 选择器统一归一为 `only=Free,min=Free,max=Free`，用于配置合并（`model_defaults.py:345-353`）。

### 4.3 配置层 fail-fast 与合并键

`_normalize_instance_specs`（`model_defaults.py:261-392`）：

- 非 dict 项、非法 count、非法 surface 会被跳过并 warning（`:283-298`）。
- `only_tier`/`min_tier`/`max_tier` 任一非字符串 -> `ConfigValidationError`（`:303-315`）。
- 未知 tier -> `ConfigValidationError`（`:316-321`）。
- 三者全缺失 -> `ConfigValidationError`（`:322-332`）。
- 调用 `resolve_tier_selector`，把 `TierSelectorError` 转为 `ConfigValidationError`（`:334-343`）。
- 合并键（`:345-375`）：
  - Free：`(surface, "Free", "Free", "Free")`。
  - `only_tier` 非 Free：`(surface, only, "", "")`。
  - 纯 min/max：`(surface, "", min, max if 显式 max else "")`。
- 相同键的 count 累加（`:377-392`）。

因此历史 `min_tier: Free` 与显式 `only_tier: Free` 会合并为同一条 Free spec，不会产生两个互不合并的槽位。

### 4.4 运行期匹配

`tier_matches_selector`（`model_access.py:191-196`）：

```text
lane(account_tier) == selector.lane
AND rank(selector.min_tier) <= rank(account_tier) <= rank(selector.max_tier)
```

`InstanceSpec.accepts_tier` / `InstanceSlot.accepts_tier` 都调用该函数（`instance_pool.py:112-113`, `:189-190`），确保池层与管理器层使用同一套匹配逻辑。

### 4.5 max_contexts

`get_configured_max_contexts` 缺省为 `instances` count 之和，上限 16（`model_defaults.py:398-416`, `:1524-1531`）。`InstancePool.max_contexts` 取 provider 或静态值，至少 1（`instance_pool.py:269-273`）。`InstanceManager.max_instances` 同样至少 1（`instance_manager.py:160-162`）。

---

## 5. `active_requests` 并发原语

### 5.1 字段与语义

`InstanceSlot` 关键字段（`instance_pool.py:138-166`）：

- `active_requests: int = 0`
- `active_zero: asyncio.Event`，初始 set。
- `lifecycle_lock: asyncio.Lock`，串行化激活/关闭/退役。

`acquire_request`（`:205-208`）：

```text
active_requests += 1
active_zero.clear()
last_used = monotonic()
```

`release_request`（`:210-214`）：

```text
active_requests = max(0, active_requests - 1)
last_used = monotonic()
if active_requests == 0: active_zero.set()
```

`ActiveRequest.release` 是幂等的；重复调用不会重复减计数（`instance_pool.py:116-135`）。

### 5.2 获取句柄

`InstancePool.begin_request`（`instance_pool.py:498-512`）：

1. slot 必须存在。
2. state 必须 `READY`。
3. Page 未关闭；若已关闭则把 slot 置 `INVALID` 并抛 `InstanceCapacityError`。
4. `acquire_request()`，返回 `ActiveRequest`。

`InstanceManager.begin_request`（`instance_manager.py:610-617`）先检查 record 为 `ACTIVE`，再调用 pool；pool busy 转为 `InstanceBusyError`。

### 5.3 忙时拒绝与有界 drain

| 操作 | 忙时行为 | 证据 |
|---|---|---|
| `activate` | `InstanceBusyError`，不覆盖运行态 | `instance_pool.py:414-417` |
| `sleep` | `InstanceCapacityError` -> 管理器转 `InstanceBusyError` | `instance_pool.py:519-525`, `instance_manager.py:440-449` |
| `close_slot` | 抛 `InstanceCapacityError`，不删除 | `instance_pool.py:563-575` |
| `retire_runtime` | 置 RETIRING；等待 `active_zero`，超时后强制关闭并返回 True | `instance_pool.py:527-561` |
| 管理器 `close`/`switch`/`reassign`/`restart` | 先检查 `slot.active_requests > 0` 并拒绝 | `instance_manager.py:340-342`, `:369-371`, `:407-409`, `:432-434` |

`retire_runtime` 的 drain 超时来自 `InstanceManager._drain_timeout_s`，默认 12 秒（`instance_manager.py:39`, `:144-150`），由 `_retire_record` 传入（`instance_manager.py:900-916`）。

### 5.4 关闭顺序

`_close_runtime`（`instance_pool.py:592-627`）：

1. 保存旧 `session` / `context_id` / `cdp` / `realm_id`。
2. 先 `session.close()`，即关闭 Page。
3. 再 `cdp.dispose_browser_context(context_id)`。
4. 若之前有运行态，调用 `_on_slot_retired(old_realm)` 清理 realm 级缓存。
5. 清空 slot 的 `session` / `context_id`。

`activate` 在启动新运行态前总是先 `_close_runtime`（`:419`），因此不会出现同一槽位两个 Page 并存。

### 5.5 发现会话不使用 `active_requests`

`discovery_sessions` 只返回 READY、session 非空、Page 未关闭的槽位；排序 Paid 优先、空闲优先（`instance_manager.py:628-651`）。它不获取 `active_requests`，不关闭/退役/改 lane（`:632-636`）。因此模型发现可以在不影响请求调度的前提下复用现有 Page。

---

## 6. 路由准入与候选排序

### 6.1 请求入口

- Gemini：`api/routes_gemini.py:21-45` -> `api/gemini_service.py::handle_gemini_generate_content` / `::_build_gemini_streaming_response` -> `generation_service.generate` / `stream_generation`。
- OpenAI：`api/routes_openai.py:79-158` -> `convert_openai_request` -> `GeminiGenerateContentRequest` -> `generation_service`。
- `countTokens`：`routes_gemini.py:48-61` -> `token_counter.count_tokens`。

这些入口最终都调用 `RoutingPipeline.begin/acquire/release`。

### 6.2 `begin` 的准入顺序

`RoutingPipeline.begin`（`routing_pipeline.py:313-393`）：

```text
ensure_started()
route = resolve_model_route(model_path, requested_surface, enabled_surfaces)
if route.surface == "build": raise SurfaceCapacityError
wake(route)                       # 唤醒同 lane/surface 的 sleeping/unstarted
lane_candidates = manager.eligible_instances(paid=route.paid, surface=route.surface)
if not lane_candidates:
    if paid and current_tier 不满足 required: raise TierRequiredError
    raise SurfaceCapacityError
eligible = [i for i in lane_candidates if tier_allows(i.tier, route)]
if not eligible:
    if paid and current_tier 不满足 required: raise TierRequiredError
    raise SurfaceCapacityError
available = [i for i in eligible if account_available(i.account_id, route)]
if not available: raise SurfaceCapacityError(冷却/登录态)
ordered = _order(route, available)
max_switches = features.routing.max_switches (default 2)
return RouteContext(route, candidates, budget, max_retries)
```

逐段说明：

1. `_ensure_started` 在 instance manager 尚未配置 surface 时调用 `startup()`（`routing_pipeline.py:504-513`）。
2. `resolve_model_route` 冻结 surface、lane、`required_tiers`、`required_tier`、`tier`（`routing_eligibility.py:207-260`）。
3. `build` surface 目前显式拒绝，不会静默换面（`routing_pipeline.py:326-330`）。
4. `_wake` 只唤醒同 lane/surface 的 `UNSTARTED`/`SLEEPING`（`routing_pipeline.py:515-524`, `instance_manager.py:465-505`）。
5. `eligible_instances` 已按 lane/surface 过滤，并包含 `UNSTARTED`/`SLEEPING`/`STARTING`/`ACTIVE`（`instance_manager.py:249-267`）。
6. `tier_allows` 按模型 `required_tiers` 做最低要求过滤（`routing_eligibility.py:263-264`, `model_access.py:307-317`）。
7. `_account_available` 通过 rotator 检查账号 enabled/expired/session/cooldown（`routing_pipeline.py:469-481`, `account_rotator.py:634-646`）。
8. 若 Paid 请求没有可用账号，先用 `_current_tier` 判断是否属于 tier 不足，再决定 403 还是 503（`routing_pipeline.py:336-347`, `:354-360`）。

### 6.3 候选排序

`_order`（`routing_pipeline.py:395-438`）构造的排序键严格为：

```python
(
    in_flight,
    account_tier_rank,
    total_requests,
    model_requests,
    last_used,
    warm,
    sticky,
    number,
)
```

含义与来源：

| 键 | 方向 | 语义 | 来源 |
|---|---|---|---|
| `in_flight` | 升序 | 空闲实例优先 | `RouteCandidate.in_flight`（`:157`, `:170-183`） |
| `account_tier_rank` | 升序 | 最小满足 tier 优先 | `tier_rank(self.tier)`（`:164-167`, `model_access.py:277-280`） |
| `total_requests` | 升序 | 总请求数更少优先 | rotator `get_account_stats().total_requests`（`:413-418`） |
| `model_requests` | 升序 | 该模型今日请求数更少优先 | `stats.model_requests_total(route.model)`（`:419-423`） |
| `last_used` | 升序 | 最久未使用优先（LRU） | `candidate.last_used`（slot 的 monotonic） |
| `warm` | 升序 | `ACTIVE` 先于非 ACTIVE | `0 if ACTIVE else 1`（`:424`） |
| `sticky` | 升序 | 当前实例作为最终 tie-breaker | `0 if id == current_instance_id else 1`（`:425`） |
| `number` | 升序 | 稳定编号兜底 | `candidate.number`（`:434`） |

注意：

- `warm` 与 `sticky` 在键的末尾，不会让一个健康槽位长期垄断流量（`:398-405`）。
- 若 rotator 不存在或没有 `get_account_stats`，`total_requests` / `model_requests` 均为 0，排序退化到 `in_flight -> tier_rank -> last_used -> warm -> sticky -> number`（`:413-424`）。
- `last_used` 来自 `InstanceSlot.last_used`，在 acquire/release 时更新为 `time.monotonic()`（`instance_pool.py:205-214`）。

### 6.4 “最小满足 tier”的精确定义

模型侧：

- `resolve_model_route` 用 `resolve_required_tiers` 得到允许 tier 集合，再取 `minimum_required_tier` 作为 `required_tier`（`routing_eligibility.py:234-260`, `model_access.py:295-304`）。
- `tier_allows(account_tier, route)` 只要求 `tier_rank(account_tier) >= tier_rank(minimum_required_tier)`（`model_access.py:307-317`, `routing_eligibility.py:263-264`）。

实例侧：

- `_order` 在所有满足 `tier_allows` 的候选中按 `account_tier_rank` 升序，因此优先选择“刚好满足”的最低等级账号（`routing_pipeline.py:426-435`）。
- 例如 Paid 模型要求 Pro：Pro 排在 Ultra/Ultra_20X 之前；Free 账号不会进入 Paid lane。
- `Plus` 的 rank 为 1，但 Paid 路由的最低要求通常是 Pro（rank 2），因此 Plus 不能承接 Pro 模型。

### 6.5 `_current_tier` 与 403 判定

`_current_tier` 取候选实例 tier 与 enabled/未 expired 账号 tier 的最高值（`routing_pipeline.py:449-467`）。因此：

- Paid lane 没有候选但存在一个可用的 Pro/Ultra 账号时，不会误报 403，而是可能报 `SurfaceCapacityError`（实例容量/账号冷却）。
- 只有当“最高可用 tier”也不满足模型要求时才报 `TierRequiredError`。

### 6.6 切换预算与重试上限

- `max_switches`：来自 `features.routing.max_switches`，默认 2；非法值回退 2（`model_defaults.py:1420-1447`, `defaults/config.yaml:43-46`）。
- `max_retries`：来自 `rotator.max_retries`，默认 5，最小 1（`routing_pipeline.py:496-502`, `defaults/config.yaml:33-41`）。
- `RoutingBudget` 只统计 switch；`record_retry` 单独统计同账号重试（`routing_safety.py:13-49`）。
- `can_continue` 需要同时满足 `total_attempts < max_retries` 且 `budget.can_switch()`（`routing_pipeline.py:891-894`）。

---

## 7. 执行、错误处理与请求期改绑

### 7.1 `acquire`

`RoutingPipeline.acquire`（`routing_pipeline.py:528-584`）：

1. `next_candidate`：跳过已尝试实例和 `failed_accounts`（`routing_pipeline.py:199-206`）。
2. 若候选为 `UNSTARTED` / `SLEEPING`，先 `wake`（`:537-545`）。
3. `manager.begin_request(instance_id)` 获取句柄（`:546-548`）。
4. 若 session 为空，释放句柄并抛 `SurfaceCapacityError`（`:549-552`）。
5. 构造 `InstanceRequestClient`（`:553-558`）。
6. 记录 attempt/account，返回 `RouteAttempt`（`:559-569`）。

若候选在获取过程中抛非路由/非 tier 异常，记录 warning 并继续下一个候选（`:570-579`）。

### 7.2 `handle_error` 决策表

`handle_error`（`routing_pipeline.py:619-735`）返回 `"retry_same"` / `"switch"` / `"raise"`：

| 异常 | 首次处理 | 二次处理 | 已 yield 数据 | 证据 |
|---|---|---|---|---|
| `TierRequiredError` | 直接抛出 | — | — | `:628-629` |
| `ValueError` | 转 `RequestError(400)` | — | — | `:630-631` |
| `SessionExpiredError` | 记录 `session_expired`；清模板；立即 `_switch_or_raise` | 预算/候选耗尽后抛出 | 抛出，不切换 | `:633-640` |
| `AuthError`（403） | 若候选 tier 不满足 -> `TierRequiredError`；否则记录 `auth_error`、清模板；`same_retries==0` 且未超 max_retries -> `retry_same` | 第二次 -> `_switch_or_raise(reason="auth_error")` | 抛出，不切换 | `:642-663` |
| `UsageLimitExceeded`（429） | 记录 `rate_limited`；`same_retries==0` 且未超 max_retries -> `retry_same` | 第二次 -> `_switch_or_raise(reason="429", quota_exhausted=True)` | 抛出，不切换 | `:665-685` |
| `RequestError(204)` | 同账号重试一次（清模板） | 仍空 -> `BrowserTransportError` | 抛出，不切换 | `:687-715` |
| `RequestError(404)` 且 transient RPC | 同账号重试一次 | 仍失败 -> 抛出 | 抛出，不切换 | `:687-715` |
| 非 transient `RequestError(404)` | 直接抛出 | — | — | `:689-693` |
| 浏览器会话挂起 | 记录、清模板；未 yield -> `_switch_or_raise(reason="session_hang")` | 预算/候选耗尽 -> 抛出 | 抛出，不切换 | `:717-724` |
| 其他 `RequestError` | 直接抛出 | — | — | `:726-728` |
| 其他 `AistudioError` / `BrowserTransportError` | 直接抛出 | — | — | `:730-732` |
| 未知异常 | 记录后抛出 | — | — | `:734-735` |

同账号重试使用 `budget.record_retry(...)`，不消耗 switch 预算（`:654`, `:672`, `:701`）。切换使用 `budget.record_switch(...)`（`:853`, `:908`）。

### 7.3 `_switch_or_raise` 与 `_try_rebind`

`_switch_or_raise`（`routing_pipeline.py:879-917`）：

```text
if not attempt.account_id: raise exc
failed_accounts.add(attempt.account_id)
can_continue = total_attempts < max_retries AND budget.can_switch()
if can_continue:
    reuse = next_candidate() is None        # 单槽为 True，多槽为 False
    if await _try_rebind(context, attempt, reason, reuse=reuse):
        return "switch"
if not can_continue or next_candidate() is None:
    if quota_exhausted: raise SurfaceSwitchExhaustedError(503)
    raise exc
budget.record_switch(reason)
retry_same = False
await _wake(route)
return "switch"
```

`_try_rebind`（`routing_pipeline.py:737-867`）的前提与步骤：

1. manager 必须有 `reassign`，rotator 必须有 `select_rebind_account`；否则返回 False（`:766-770`）。
2. 排除集合：`failed_accounts ∪ attempted_accounts ∪ {当前账号} ∪ 其他实例已绑定账号`（`:772-790`）。
3. 先 `release(attempt)` 释放 `active_requests`，因为 pool 在忙时拒绝激活（`:792-795`）。
4. 若释放后实例仍有在途请求，跳过改绑（`:796-801`）。
5. 循环调用 `select_rebind_account`：
   - 传入 lane、model、surface、排除集合、only/min/max、required_tiers。
   - 选择器按 `(tier_rank, total_requests, model_requests_total, last_used)` 取最小（`account_rotator.py:700-710`）。
6. `reassign(instance_id, target_id)`；失败则把目标账号加入 `failed_accounts` 和排除集合后继续（`:823-835`）。
7. 成功后用新 `Instance` 更新 `RouteContext.candidates` 中同 instance_id 的候选（`:837-850`）。
8. 单槽 `reuse=True` 时从 `attempted_instances` 移除该实例，使 `next_candidate` 能再次选中它（`:851-852`）。
9. `budget.record_switch(reason)`；清模板；返回 True（`:853-866`）。

`reassign` 本身（`instance_manager.py:389-425`）：

- 同账号直接返回。
- 跨 lane 拒绝。
- tier 区间不满足拒绝。
- 同账号已有其他实例拒绝。
- `active_requests > 0` 拒绝。
- `_activate` 新账号；失败回滚旧账号；回滚失败标记 `INVALID`。

### 7.4 单槽与多槽语义

| 场景 | `reuse` | 行为 |
|---|---|---|
| 只有一个槽位，429/401/二次 403 | True | 释放句柄 -> 原地改绑到同 lane 的另一个健康账号 -> 从 `attempted_instances` 移除 -> 当前请求重新 acquire 同一槽位 |
| 多个槽位，某个槽位失效 | False | 释放句柄 -> 原地改绑该槽位 -> 当前请求继续走 `next_candidate()` 的其他槽位；被改绑槽位供后续请求使用 |
| 没有其他候选账号 | — | `_try_rebind` 返回 False；按原错误/503 路径抛出 |

补充：

- 单槽改绑没有“每槽最多一次”的硬上限；一个槽位可以 A -> B -> C，但受 `attempted_accounts`/`failed_accounts` 和 `max_switches` 约束（`:760-765`）。
- 429 的第二次处理才会进入改绑；401 首次即进入改绑；403 首次同账号重签，第二次进入改绑（见 7.2）。
- 改绑是请求内同步完成的，不创建后台任务（`:745-755`）。

### 7.5 429 耗尽错误

`UsageLimitExceeded` 在无法继续时，`_switch_or_raise(quota_exhausted=True)` 抛 `SurfaceSwitchExhaustedError`（`routing_pipeline.py:902-907`）。该错误 `status_code = 503`，code=`surface_switch_exhausted`（`domain/errors.py:119-138`）。因此路由层不会把 429 直接作为最终响应；最终是 503。

### 7.6 成功记录

`record_success`（`routing_pipeline.py:589-617`）：

- 清 `retry_same`，`last_reason="success"`。
- 调 `record_rotator_event("success", ...)` 更新账号统计与用量。
- 调 `runtime_state.record(...)` 更新模型级统计。

`account_rotator.record_success` 会累加请求/成功/Token，并清空该模型冷却（`account_rotator.py:284-319`）。

---

## 8. `quota_scope` 双模式记账

### 8.1 两种模式

`AccountRotator.quota_scope`（`account_rotator.py:544-552`）由 `resolve_quota_scope()` 决定：

- `"account"`（默认）：同一账号的 chat/build 共用同一模型键。
- `"account_surface"`：按 `surface::model` 分开记账。

配置解析（`model_defaults.py:1472-1483`）：非法值 warning 并回退 `"account"`。默认配置 `defaults/config.yaml:33-41` 写 `quota_scope: account`。

### 8.2 键构造

`quota_key`（`account_rotator.py:82-89`）：

```text
norm_model = normalize_model_key(model) or "general"
if scope == "account_surface" and surface in ("chat","build"):
    return f"{surface}::{norm_model}"
return norm_model
```

`quota_keys` 额外返回 legacy 未分面键，用于读取与迁移（`:92-100`）：

```text
primary = quota_key(...)
legacy = norm_model or "general"
return (primary,) if primary == legacy else (primary, legacy)
```

`split_quota_key` 反向拆分（`:70-79`）：只有 `chat::` / `build::` 前缀才拆，否则 surface 为 None。

### 8.3 记账与可用性

- `get_model_daily` 按 scope 选键并创建当日统计（`:215-224`）。
- `model_requests_total` 汇总所有键中模型部分等于目标的请求数（`:226-235`）。
- `is_available` 同时检查 `quota_keys` 的冷却（`:237-261`）。
- `get_cooldown_remaining` 同样检查 `quota_keys`（`:263-282`）。
- 每日统计在美西日期变化时清空（`:33-41`, `:206-213`）。

### 8.4 软额度不是硬禁用

账号可用性只看 enabled、expired、session_expired、auth_cooldown、模型冷却；软额度超限不会禁用账号（`account_rotator.py:237-245` 的 docstring，`:634-646` 的实现）。`resolve_tier_quota` 只用于仪表盘百分比展示（`account_rotator.py:758-761`, `model_defaults.py:1283-1299`）。

### 8.5 429 冷却

`record_rate_limited`（`account_rotator.py:321-358`）：

- 第 1-2 次：短冷却 `rate_limited_short`。
- 超过 2 次：进入 `drip_mode`，使用 `rate_limited_drip`。
- drip 模式超过 4 次：使用 `rate_limited_drip_extended`。
- 冷却上限为该模型到美西午夜的剩余秒数（至少 60s）。

`record_auth_error` 明确不设置 auth 冷却（`auth_cooldown=0.0`），只累加 `auth_errors` 计数；调度准入不读取 `auth_errors`，不存在动态避让；首次 403 原位重签，二次 403 才切号（`account_rotator.py:371-388`）。

`record_session_expired` 设置 24h `auth_cooldown` 并标记 `session_expired`（`:390-404`）。

---

## 9. Paid 本地 TierRequiredError 403 契约

### 9.1 领域错误

`TierRequiredError`（`domain/errors.py:24-62`）：

- `status_code = 403`
- `code = "tier_required"`
- 字段：`model`、`tier`、`required_tier`、`current_tier`
- `as_fields()` 返回上述四个字段。

### 9.2 本地触发点

`RoutingPipeline` 只在本地准入阶段抛该错误：

1. Paid lane 完全没有候选，且 `_current_tier` 不满足要求（`routing_pipeline.py:336-347`）。
2. Paid lane 有候选但没有任何候选满足 `tier_allows`，且 `_current_tier` 不满足要求（`:354-360`）。
3. 已选实例返回 `AuthError`，但该实例 tier 不满足路由要求（`:642-646`）。

`_tier_error`（`:440-447`）：

```python
required = route.required_tier or "Pro"
TierRequiredError(
    model=route.model,
    tier=route.tier or required,
    required_tier=required,
    current_tier=current_tier or "Free",
)
```

注意：`route.tier` 本身就是 `minimum_required_tier(required_tiers)` 或 `"Free"`（`routing_eligibility.py:251-260`），因此 `tier` 与 `required_tier` 通常相同；一个是“路由冻结的 tier”，一个是“最低 required tier”。

### 9.3 不触发切换

`handle_error` 对 `TierRequiredError` 直接 `raise exc`（`routing_pipeline.py:628-629`），不进入同账号重试或切号。这与“tier 不足不是运行时故障，而是请求不合法”的契约一致。

### 9.4 协议渲染

`api/error_mapping.py:60-81` 统一映射：

- Gemini：`403`，`error.status=PERMISSION_DENIED`，`error` 内合并四个字段。
- Claude：`403`，`type=error`，`error.type=permission_error`，合并四个字段。
- OpenAI / Responses：`403`，`error.type=tier_required`，`error.code=tier_required`，合并四个字段。

Gemini 流式错误也保留字段：`format_sse_error` 在 `TierRequiredError`/`RoutingError` 时 `error.update(exc.as_fields())`（`api/gemini_service.py::format_sse_error`）。

`classify_gemini_error_payload` 对 `TierRequiredError` 返回 `(403, str(exc), "PERMISSION_DENIED")`（`api/error_mapping.py::classify_gemini_error_payload`）。

### 9.5 与其他错误的边界

- `SurfaceCapacityError`：503（`surface_instance_busy` 或 `surface_quota_exceeded`）（`domain/errors.py:94-116`）。
- `SurfaceSwitchExhaustedError`：503（`surface_switch_exhausted`）（`domain/errors.py:119-138`）。
- `TierRequiredError`：403，绝不与 429/503 混用（`error_mapping.py:1-7` 的模块说明）。

---

## 10. 时序图（文本）

### 10.1 启动与 lane 绑定

```text
App lifespan
  -> InstanceManager.startup()
     -> active = account_store.get_active_account()
     -> accounts = active 优先 + 其余
     -> for spec in pool.specs:
          for count:
            slot = pool.add_slot(spec(count=1), lane=spec.lane)
                 [lane 校验：selector.lane == lane]
            record = new_record(slot, surface, only/min/max, lane)
            account = pick_account(spec, accounts, used)
                 [enabled, 未 expired, tier 匹配, auth 文件存在]
            activate(record, account)
                 -> pool.activate(slot, account, tier, account_lane, surface)
                      [account_lane == slot.lane]
                      [slot.accepts_tier(tier)]
                      -> close old runtime (若有)
                      -> bootstrap context + page + BotGuard
                      -> slot=READY
                 -> record=ACTIVE
     -> select current / repair latest
```

### 10.2 正常请求

```text
Client -> RoutingPipeline.begin(model)
  -> resolve_model_route -> lane/surface/required_tiers
  -> wake(same lane/surface)
  -> eligible_instances(lane, surface)
  -> tier_allows filter
  -> account_available filter
  -> _order(candidates)
  -> RouteContext

RoutingPipeline.acquire(context)
  -> next_candidate()
  -> wake if UNSTARTED/SLEEPING
  -> manager.begin_request(instance)
       -> record.state == ACTIVE
       -> pool.begin_request(slot)
            -> slot.state == READY
            -> slot.page_closed()? -> INVALID + 503
            -> slot.active_requests += 1
  -> InstanceRequestClient(session)

generation_service.generate/stream_generation
  -> client.generate_content / stream_generate_content
  -> pipeline.record_success
  -> pipeline.release(attempt)
       -> ActiveRequest.release()
            -> slot.active_requests -= 1
            -> active_zero.set() when 0
```

### 10.3 多槽 429

```text
attempt A (slot-1, account A)
  -> 429 UsageLimitExceeded
  -> handle_error: same_retries=0
  -> budget.record_retry("429")
  -> "retry_same"
attempt A again
  -> 429
  -> handle_error: same_retries=1
  -> _switch_or_raise(reason="429", quota_exhausted=True)
       -> failed_accounts.add(A)
       -> can_continue? (attempts<max_retries && switches<max_switches)
       -> reuse = next_candidate() is None  # 多槽 -> False
       -> _try_rebind:
            release(A handle)
            select_rebind_account(lane, exclude A/attempted/bound)
            reassign(slot-1, account B)  # 原地关闭旧 Page/Context，激活 B
            context.candidates 更新 slot-1 -> B
            budget.record_switch("429")
       -> "switch"
  -> acquire next_candidate() -> slot-2 (account C)
  -> 成功 -> release
后续请求可使用已修复的 slot-1(B)
```

### 10.4 单槽 429 / 401 / 二次 403

```text
attempt (only slot, account A)
  case 429:
    first 429 -> retry_same (same account)
    second 429 -> _switch_or_raise
  case 401 SessionExpired:
    first 401 -> _switch_or_raise (无同账号重试)
  case 403 AuthError:
    first 403 -> retry_same (同账号重签)
    second 403 -> _switch_or_raise

_switch_or_raise:
  failed_accounts.add(A)
  reuse = next_candidate() is None  # 单槽 -> True
  _try_rebind(reuse=True):
    release(A handle)
    select_rebind_account(lane, exclude A/attempted/bound)
    reassign(slot, account B)
    context.candidates[slot] = B
    attempted_instances.discard(slot)   # 允许当前请求重新取同一槽
    budget.record_switch(reason)
  return "switch"

generation_service:
  release(old attempt) -> attempt=None
  loop -> acquire -> same slot with account B
```

### 10.5 Paid tier 不足

```text
begin(paid_model)
  -> route.paid=True, required_tiers=(Pro,Ultra,Ultra_20X), required_tier=Pro
  -> eligible_instances(paid=True, surface=chat)
  -> 若为空:
       current = _current_tier(paid lane + free lane + accounts)
       if not tier_satisfies_required(current, required_tiers):
           raise TierRequiredError(model, tier, required_tier, current_tier)
  -> 若有候选但 tier_allows 全 false:
       同样检查 current -> 403 或 503
  -> handle_error(TierRequiredError) -> raise
  -> error_mapping -> HTTP 403
       body.error: code=403, status=PERMISSION_DENIED,
                   tier/model/required_tier/current_tier
```

---

## 11. 残余限制

1. **无后台自愈**：改绑只发生在请求路径；没有后台 rebind/rebalance，也没有 idle 回收（`instance_manager.py:7-12`, `routing_pipeline.py:745-755`）。
2. **单槽改绑依赖 rotator**：若没有 `select_rebind_account` 或没有同 lane 健康账号，`_try_rebind` 返回 False，原错误继续上抛（`routing_pipeline.py:766-770`, `:818-819`）。
3. **改绑排除集合较宽**：其他实例已绑定账号也会被排除（`:777-790`），因此多槽场景下可用改绑账号可能少于账号总数。
4. **释放句柄后仍有忙检查**：`_try_rebind` 先 release，再检查 `active_requests`；若同一槽位被其他并发请求重新占用，则放弃改绑（`:792-801`）。这是有意的安全阀，但会让改绑在极端并发下跳过。
5. **多槽改绑不重用失败槽**：`reuse=False` 时当前请求继续使用其他候选，不会立即回到刚改绑的槽位；这避免在刚失败的槽位上重复等待，但也意味着新账号的实际验证延后到下一个请求。
6. **tier 错误字段的语义**：`tier` 与 `required_tier` 通常相同（都来自最低 required tier）；如果调用方需要“模型自身 tier”与“最低要求 tier”的区分，当前契约没有提供额外字段（`routing_pipeline.py:440-447`）。
7. **`_current_tier` 包含未绑定账号**：403 的 `current_tier` 可能来自账号库中一个尚未绑定实例的高 tier 账号，而不是当前槽位 tier（`:449-467`）。这是“可用能力”口径，不是“当前实例”口径。
8. **`build` surface 未实现**：`begin` 直接拒绝，不会切换（`:326-330`）。
9. **`max_contexts` 上限 16**：超过上限配置会被截断并 warning（`model_defaults.py:395-416`）；启动时达到上限会停止创建后续实例（`instance_manager.py:186-191`）。
10. **`in_flight` 是槽位级而非账号级**：同一账号不能有两个实例，因此槽位级与账号级在当前模型下等价；但如果未来允许同账号多实例，需要重新审视（`instance_manager.py:291-294`, `:405-406`）。
11. **`last_used` 是 monotonic**：进程重启后不保留；跨进程的“最久未使用”语义只依赖 rotator 的持久化 `last_used` 统计，而排序键用的是 slot monotonic（`instance_pool.py:159`, `routing_pipeline.py:431`）。
12. **rotator 缺失时排序信息退化**：`total_requests` / `model_requests` 为 0，候选只在 `in_flight/tier/last_used/warm/sticky/number` 上排序（`routing_pipeline.py:413-424`）。
13. **403 的“首次同账号重签”不是无限制**：受 `max_retries` 约束；如果 `total_attempts` 已达上限，第二次 403 直接进入切换/抛出（`:651-663`）。
14. **409/503 的区分依赖调用链**：`InstanceBusyError`/`InstanceNotReadyError` 等管理器错误在路由层可能被归入通用异常并继续尝试其他候选（`routing_pipeline.py:570-579`），最终可能表现为 `SurfaceCapacityError`，而不是原始 409。

---

## 12. 证据索引

| 主题 | 主要证据 |
|---|---|
| 实例契约 | `src/aistudio_api/domain/instances.py:9-41` |
| Slot/ActiveRequest | `src/aistudio_api/infrastructure/gateway/instance_pool.py:116-214` |
| Pool 生命周期 | `instance_pool.py:327-364`, `:366-460`, `:498-575`, `:592-627` |
| Manager 生命周期 | `src/aistudio_api/application/instance_manager.py:164-232`, `:287-505`, `:507-608` |
| 状态映射 | `instance_manager.py:999-1047` |
| lane 推导 | `src/aistudio_api/domain/model_access.py::account_is_paid`, `::tier_rank`, `::instance_lane_for_tier` |
| tier 归一化 | `domain/model_access.py:71-188`, `:191-196` |
| 配置归一化/合并 | `src/aistudio_api/infrastructure/gateway/model_defaults.py:261-416` |
| 路由准入 | `src/aistudio_api/application/routing_pipeline.py:313-393` |
| 候选排序 | `routing_pipeline.py:395-438` |
| 错误处理 | `routing_pipeline.py:619-735` |
| 改绑 | `routing_pipeline.py:737-917`, `instance_manager.py:389-425` |
| 预算 | `src/aistudio_api/application/routing_safety.py:13-49` |
| quota_scope | `src/aistudio_api/application/account_rotator.py:70-101`, `:215-282`, `:544-552`; `model_defaults.py:1472-1483` |
| 403 契约 | `src/aistudio_api/domain/errors.py:24-62`; `src/aistudio_api/api/error_mapping.py::classify_gemini_error_payload`; `src/aistudio_api/api/gemini_service.py::format_sse_error` |
| 默认配置 | `defaults/config.yaml:8-18`, `:33-46` |
