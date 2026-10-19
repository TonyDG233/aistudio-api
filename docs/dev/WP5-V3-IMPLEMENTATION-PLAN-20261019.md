# WP5 v3 逐文件实现计划

- 日期：2026-10-19
- 状态：待实施；本计划取代 WP5 v2 的 default/realm 实例层
- 权威设计：`docs/dev/WP5-V3-DESIGN-20261019.md`
- 范围：纯后端；UI 归 WP7；WP10/WP8/WP11 边界不变
- 提交策略：每个 phase 完成后中文 commit；不 push

## 0. 已裁决的开放项

以下是本计划的执行口径；后续不再反复询问，除非实现中发现事实冲突。

1. **渠道规范键**
   - 内部 canonical key 继续用 `chat` / `build` / `official`；
   - `pg`、`playground` 是 `chat` 的输入/配置别名；
   - 模型列表与 UI 展示前缀用 `pg/`、`build/`；
   - `chat/` 只作为输入别名，不在列表中输出；
   - `official` 在 WP9 前注册但 `enabled=false`、`implemented=false`，不进入列表。
2. **裸模型多渠道选择**
   - 显式前缀优先，渠道固定，不静默换渠道；
   - 只有一个启用且实现渠道时直接选它；
   - 多渠道时先做候选资格预览：
     - pro-only/chat-only 模型只考虑 chat；
     - 非 chat-only 模型优先 build；若 build 没有任何合格候选，则回退 chat（这是选择阶段，不是同请求内换渠道）；
     - 若 chat 无候选且 build 有候选，则 build；
     - 两端都无候选 → `surface_no_capable_account` 403。
3. **组解析**
   - 组是账号级约束，不是请求级单组；
   - 每个账号按 `features.groups` 首个命中组解析；未命中按 tier 默认组；`route_by_tier=false` 且 `groups=[]` 时为 implicit-global；
   - `RouteContext` 不再持有单一 `group`，候选各自携带 `group`。
4. **额度池**
   - 渠道默认 `quota_pool: null`（不限制），保证零配置 free 账号仍可 chat；
   - 黄金模板显式配置 `channels.chat.quota_pool=pro`、`channels.build.quota_pool=free`；
   - 账号 tier 默认池：`free → [free]`，paid(`pro/ultra/plus`) → `[pro, free]`，unknown → `[free, pro]`；
   - 账号 `routing.quota_pools` 与组 `allowed_quota_pools` 只能收窄；
   - pro-only 模型独立于额度池，必须落 paid 账号。
5. **页面/Context 预算**
   - `global_max_pages=2`、`channels.chat.max_pages=1`、`channels.build.max_pages=1`、`channels.official.max_pages=0`、`max_contexts=2`；
   - 组 `max_pages` 默认 2；
   - 页面数按活页面计；Context 数按池内存活上下文计；两者分开取最严。
6. **控制面 Context**
   - `BrowserSession` 继续负责浏览器进程、登录、cookie 导入、模型发现的控制面；
   - **控制面页面不是请求实例槽**，不参与 `max_contexts` / 页面预算，也不得承载任何生成请求；
   - 所有生成请求必须从 `InstancePool` 获取 lease；这是“取消默认会话特权”的落地方式。
7. **auth_update**
   - 24h 凭证回写改为“保存池内所有存活槽位的 cookies 到各自 auth 文件”；
   - 控制面页面仍可单独保存；
   - 不改变 `ENABLE_AUTH_UPDATE` 的配置语义与触发周期。
8. **auto_switch**
   - 生产路径接 `InstancePool.rebalance()`：只处理空闲槽位/新会话排序，不触碰在途；
   - 无池或池为空时才回退到控制面切号，保持 WP10 兼容；
   - 方向由槽位渠道角色、组资格、额度池、渠道亲和决定，不再“切一个全局默认账号”。

## 1. 架构不变量

```text
InstancePool(max_contexts)
├── slot 0: context + current_account + pages{chat?, build?} + in_flight
├── slot 1: ...
└── ...

请求路由：渠道/能力 → 候选账号 → 槽位 lease → 派发上游
实例切换：槽位紧急度 → 合格账号 → 最受限优先 → 装载/换绑
```

必须始终成立：

1. 没有任何请求使用 `BrowserSession` 的控制面页面。
2. 一个槽位同一时刻只绑定一个账号；在途槽位不可换绑。
3. 槽位空闲时可关闭旧 Context、创建新 Context 装载新账号；不做账号硬绑定。
4. 页面数、组页面数、渠道页面数、Context 数四类预算在**新建页面/新建 Context/换绑**前判定。
5. 候选资格只来自配置与账号元数据；不因“当前活跃账号”放宽或收窄。
6. 首字节后不切换；同账号重试不消耗切换预算。
7. 所有错误显式：`surface_no_capable_account` 403、`surface_quota_exceeded` 429、`surface_switch_exhausted` 503。

## 2. Phase 0：配置契约与纯资格图

### 文件

- `src/aistudio_api/application/channel_registry.py`
  - `ChannelSpec` 增加 `max_pages: int`、`quota_pool: str | None`、`display_prefix: str`；
  - `build_registry` 读取并 clamp `max_pages` / `quota_pool`；
  - `chat` 的 `display_prefix="pg"`，`build` 为 `"build"`；
  - `enabled_channels` 继续过滤 enabled+implemented；
  - 新增 `requires_paid_account(model)` / `channel_affinity(tier, channel)` 或在资格模块实现。
- `src/aistudio_api/application/account_routing.py`
  - `AccountRouting` 增加 `quota_pools: tuple[str, ...]`；
  - `normalize_account_routing` 接受 `quota_pools`，去重、白名单 `free/pro/official`，非法值丢弃；
  - `to_dict/from_dict` 往返。
- `src/aistudio_api/infrastructure/gateway/model_defaults.py`
  - `_FEATURES_DEFAULTS.channels` 改为 `max_pages/quota_pool`；
  - `routing` 删除 `global_max_instances/max_realms/realm_mode`，增加 `global_max_pages/max_contexts`；
  - `_normalize_channels`、`_normalize_routing` 同步；
  - `_normalize_feature_group` 增加 `max_pages`、`allowed_quota_pools`，保留 `max_page_instances` 作为旧别名映射到 `max_pages`；
  - 环境变量兼容：若旧 env 仍给 `global_max_instances`，映射到 `global_max_pages` 并告警。
- `src/aistudio_api/application/surface_orchestrator.py`
  - `GroupPolicy` 增加 `max_pages`、`allowed_quota_pools`；
  - `select_group` 增加这些字段；`max_page_instances` 兼容别名；
  - 新增 `is_pro_only_model`、`model_account_requirement`、`account_tier_pools`；
  - 保留 `resolve_surface` 作为 UI/兼容 helper，但路由管线不再用它选账号。
- 新增 `src/aistudio_api/application/routing_eligibility.py`
  - `QuotaPool` 规范化；
  - `AccountView`（id/name/tier/email/expired/routing/group/quota_pools）；
  - `candidate(account, channel, model, registry, groups, features)`；
  - `eligible_accounts(...)`；
  - `choose_channel(model, accounts, ...)`；
  - `channel_affinity(tier, channel)`；
  - `most_constrained_order(slots, candidates)` 供 Phase 3 复用。
- `defaults/config.yaml`
  - 同步新默认值、注释、示例；
  - 保留 `build_surface` legacy alias。

### 测试

- `tests/unit/test_channel_registry_v3.py`
  - `chat`/`pg` 规范键与 `pg/` 展示；
  - `max_pages`/`quota_pool` 归一化；
  - `official` 未实现不启用。
- `tests/unit/test_routing_eligibility.py`
  - free/pro 额度池交集；
  - pro-only 只允许 paid；
  - 组 allowed_surfaces 只收窄；
  - 裸模型多渠道按候选选择；
  - 组为账号级约束。
- 更新 `tests/unit/test_account_routing.py`、`test_surface_policy_v2.py`、`test_instance_manager.py` 中与默认值相关的断言。

### 完成标准

- 新配置可从 YAML 解析，旧配置可兼容映射；
- 纯函数测试覆盖上述资格矩阵；
- 质量门通过；
- commit：`feat(routing): WP5 v3 配置契约与资格图`

## 3. Phase 1：统一 Context 实例池

### 文件

- 新增 `src/aistudio_api/infrastructure/gateway/instance_pool.py`
  - `SlotState`：`empty/loading/ready/busy/cooldown/retiring`；
  - `InstanceSlot`：`slot_id/context_id/state/channel_role/current_account_id/pages/in_flight/last_used/assigned_slot_rows`；
  - `InstanceLease`：`slot_id/account_id/channel/surface/realm_id/context_id/adapter/is_borrow`；
  - `InstancePool`：
    - `acquire(account, channel, surface, model, require_idle=True)`；
    - `release(lease)`；
    - `borrow(surface, model, exclude_accounts)`；
    - `reclaim_idle(now)` / `prewarm(account, channel)`；
    - `bind_account(slot, account)`：空闲槽关闭旧 Context、创建新 Context、bootstrap `PooledSession`；
    - `ensure_page(slot, channel)`：chat 用 `PooledSession` 页面，build 用 `BuildSurface(context_id)`；
    - `page_counts()` / `context_count()` / `snapshot()` / `slot_rows()`；
    - `close()`。
  - `PooledSession`：从 `RealmSession` 迁移（bootstrap/request_scope/generate_snapshot/send_hooked_request/send_streaming_request/save_current_cookies/close）；`realm_id` 保留为兼容别名，但语义是 `slot_id`。
  - `InstanceBudget` 纯函数：`evaluate_page_budget`、`evaluate_context_budget`、`group_page_count`。
- 删除/替换 `src/aistudio_api/infrastructure/gateway/realm_pool.py`
  - `RealmPool` 与 `_by_account` 删除；
  - 如其他模块仍 import，保留一个 deprecated shim 指向 `instance_pool`，下一 phase 删除。
- `src/aistudio_api/infrastructure/gateway/client.py`
  - 构造/持有 `InstancePool`；
  - `instance_pool` property；
  - `in_flight_count` 聚合池内所有槽位 + 控制面；
  - `save_pool_cookies()` 遍历池槽位回写；
  - `_ensure_realm_services` / `_replay_for` 对 lease adapter 兼容；
  - build surface 通过 slot 的 `ensure_build_surface`；
  - 无 lease 的生成路径标记为 legacy，仅控制面/测试使用。
- `src/aistudio_api/infrastructure/gateway/build_surface.py`
  - 不改协议；确认 `context_id`、`realm_id` 唯一绑定；
  - 增加 `close()`/`is_alive()` 供池回收。
- `src/aistudio_api/infrastructure/gateway/session.py`
  - 保留 `ensure_browser_cdp_client`；
  - 增补 `close_control_context()`（可选）；
  - 不改变控制面登录/切换语义。

### 测试

- 新增 `tests/unit/test_instance_pool.py`
  - 2 槽位创建/复用/关闭；
  - 50 账号轮换时 context 数 ≤2；
  - 同一账号复用槽位；
  - 换绑时旧 Context 被 dispose；
  - 在途槽位不可换绑；
  - 页面数/Context 数预算；
  - borrow 兼容性与排除账号；
  - snapshot 不含 cookie/密钥。

### 完成标准

- 生成请求可通过 lease 走池内 chat/build；
- 不再有 `realm_id="default"` 的请求 lease；
- 质量门通过；
- commit：`feat(gateway): WP5 v3 统一 Context 实例池`

## 4. Phase 2：请求期派发前切换

### 文件

- `src/aistudio_api/application/routing_pipeline.py`
  - `Candidate` 增加 `group/quota_pool/affinity`；
  - `RouteContext` 删除单一 `group`，增加 `groups/candidates/lease`；
  - `begin()`：
    - 账号视图全部来自 `list_accounts()`；
    - `choose_channel` 用候选预览；
    - `eligible_accounts` 生成候选；
    - 无候选显式 403；
  - `acquire(ctx, candidate)`：
    - 调 `pool.acquire`；
    - 当前槽位不合格但空闲时池内换绑；
    - 无空闲槽/上限时等待 ≤5s 或显式 429/503；
    - 返回 lease；
  - 删除 `InstanceManager` 依赖；
  - `next_candidate` 只做候选迭代，不做预算顺延。
- `src/aistudio_api/application/generation_service.py`
  - `pipe.acquire` 替代 `next_candidate + land`；
  - 未获得 lease 不得调用 `client.generate_content/stream_generate_content`；
  - 在 `finally` 释放 lease；
  - 流式 `routing_wait` 保留；
  - 同账号重试保留 lease。
- `src/aistudio_api/application/upstream_errors.py`
  - 切换前释放旧 lease；
  - 继续使用 `RoutingBudget/CircuitBreaker`；
  - 首字节后不切换。
- `src/aistudio_api/application/account_orchestrator.py`
  - `switch_to_account` 只影响控制面/新会话排序；
  - 不再被请求路由当作“默认实例”。

### 测试

- 新增 `tests/unit/test_routing_pipeline_v3.py`
  - 活跃 free + 候选中 pro：pro-only 请求自动选 pro，而不是 403；
  - 跨组候选；
  - 当前槽位账号不合格但空闲 → 换绑后派发；
  - 在途槽位不换绑；
  - 无槽位等待/429/503；
  - 渠道显式前缀不换渠道。
- 更新 `test_generation_routing.py`：
  - `acquire` 调用次数、lease 释放、首字节后不切换。

### 完成标准

- 请求不再按活跃账号选组；
- “列表有合格账号 → 派发前切换/装载”成立；
- 质量门通过；
- commit：`feat(routing): WP5 v3 派发前实例切换`

## 5. Phase 3：预算、borrow、再平衡与保活

### 文件

- `src/aistudio_api/infrastructure/gateway/instance_manager.py`
  - 删除 `InstanceManager`/`RealmPool` 语义；保留 `evaluate_budget` 纯函数或迁移到 `instance_pool.py`；
  - 更新所有 import。
- 新增 `src/aistudio_api/application/instance_switch.py`
  - `plan_rebalance(slots, accounts, groups, channels, ...)`；
  - 槽位紧急度排序：失效/冷却/过期 → 空槽补位 → 策略变更 → 空闲回收；
  - 最受限槽位优先；
  - 槽内评分：渠道亲和 → 黏性 → 占用惩罚 → 权重/请求量 → last_used → account_id；
  - 在途槽位跳过。
- `src/aistudio_api/application/account_keepalive.py`
  - `auto_switch` 优先调用 `pool.rebalance()`；
  - `auth_update` 优先调用 `client.save_pool_cookies()`；
  - 状态快照包含所有 Context；
  - 无池回退保持旧语义。
- `src/aistudio_api/application/account_orchestrator.py`
  - 手动切号仍走控制面；
  - 新增 pool rebalance 钩子，避免全局默认账号。
- `src/aistudio_api/infrastructure/gateway/client.py`
  - `save_pool_cookies()`；
  - `in_flight_count` 覆盖池；
  - `close()` 关闭池。

### 测试

- `tests/unit/test_instance_switch.py`
  - 2 槽位 pg/build，pro/free 账号矩阵；
  - 最受限 pg 先分配；
  - build 优先 free，pro 回退；
  - 在途跳过；
  - 冷却/过期/槽位失效。
- 更新 `test_account_keepalive.py`
  - auto_switch 走池、不碰在途；
  - auth_update 写所有槽位；
  - 单账号/无池回退。

### 完成标准

- 预算四层（global/channel/group/context）分别生效；
- borrow 首字节前生效；
- auto_switch 不切全局默认账号；
- 质量门通过；
- commit：`feat(gateway): WP5 v3 实例切换与预算`

## 6. Phase 4：API 快照与模型列表

### 文件

- 新增 `src/aistudio_api/application/model_catalog.py`
  - `project_model_ids(models, features, registry)`；
  - 单模式裸 ID；
  - 多模式仅启用且实现的 `pg/`、`build/`；
  - `chat/`、`playground/`、`official/` 不输出；
  - 同一模型按真实渠道数出现。
- `src/aistudio_api/api/routes_models.py`
  - `/v1beta/models` 使用投影；
  - `/v1beta/models/{id}` 接受 `pg/`、`build/` 前缀并清洗。
- `src/aistudio_api/api/routes_openai.py`
  - `/v1/models`、`/v1/models/{id}` 使用同一投影。
- `src/aistudio_api/api/routes_system.py`
  - `/surface/instances` 返回 contexts/slots/pages/current/planned/budgets/quota 摘要；
  - `/routing/state` 保持熔断/预算；
  - 不返回 cookie/密钥。
- `src/aistudio_api/infrastructure/gateway/client.py`
  - `surface_instance_snapshot()` 改读池。

### 测试

- 新增 `tests/unit/test_model_catalog.py`
  - 单模式/多模式；
  - 不重复别名；
  - official 未实现不出现；
  - 模型在某渠道不可用不生成条目。
- 更新 `test_routes_models.py`、相关 API 测试。

### 完成标准

- 模型列表符合 v3 规则；
- 实例快照能解释当前/计划与预算；
- 质量门通过；
- commit：`feat(api): WP5 v3 实例快照与模型列表`

## 7. Phase 5：行为契约、真机与证据

### 文件

- `tests/unit/*`：补齐/重写 v3 行为契约；
- `tests/integration/*`：API 级路由；
- `docs/dev/evidence/wp5v3-20261019/`：
  - `README.md`、`BOARD.md`、harness、raw JSON；
  - 不含 cookie/API key。
- `docs/dev/WP5-V3-DESIGN-20261019.md` / `PLAN` / `UI` 只在实现事实偏差时更新。

### 真机最小验证

1. 3 账号、2 Context 轮换：Context 数稳定；
2. pro chat + free build 并发：两个流互不打断；
3. build 优先 free，pro 不抢 build；pg 不空缺；
4. 渠道预算 1/1 + global 2：第三个页面请求显式 429；
5. 空闲回收后重新装载账号成功；
6. pro-only 模型无 pro 账号 → 403；有 pro → 派发前装载成功；
7. argon ≤2 次，优先 free/flash。

### 完成标准

- `ruff check` / `ruff format --check` / `pyright` / `pytest tests/unit` 全绿；
- 真机证据落盘；
- commit：`test(wp5v3): 行为契约与真机证据`

## 8. 删除/保留清单

### 删除

- `RealmPool`、`RealmCapacityError`、`_by_account` 账号硬绑定；
- `InstanceManager.land()` 的 `realm_id="default"` 特权路径；
- `max_realms`、`realm_mode`、`global_max_instances` 配置与计数；
- “一个 Realm 算一个全局实例”的补丁；
- 活跃账号决定请求组/请求面的逻辑；
- 按别名重复模型列表；
- `auto_switch` 切全局默认账号的生产路径。

### 保留/复用

- 渠道注册表、前缀解析、surface guard；
- 组 `allowed_surfaces`、账号 `AccountMeta.routing`；
- `RoutingBudget` / `CircuitBreaker` / 失败链；
- `BuildSurface` 的 per-context + `realm=` WS 解复用；
- `PooledSession`（原 `RealmSession`）的 bootstrap/capture/stream；
- slot rows 的计划数/优先级；
- borrow 语义；
- WP10 双保活、WP8 格式层、WP11 token 计数边界。

## 9. 风险与控制

1. **控制面 Context 不计入池**：必须在代码与文档中明确“不得承载生成请求”；加入断言/日志。
2. **换绑会关闭 Context**：在途保护依赖 `in_flight`/`request_scope`；所有生成路径必须 finally 释放 lease。
3. **预算并发**：池操作与预算判定在同一把锁内完成，避免两个请求同时新建超限。
4. **模型列表前缀**：只做输入清洗，不改变上游模型名。
5. **真机成本**：argon ≤2 次；默认 flash/free；证据不含密钥。
6. **中途兼容**：Phase 1-3 期间允许旧测试暂时失败，但每个 phase 结束必须恢复全绿后再提交。

## 10. 执行顺序

```text
Phase 0 配置/资格
  ↓
Phase 1 统一池
  ↓
Phase 2 派发前切换
  ↓
Phase 3 预算/borrow/再平衡/保活
  ↓
Phase 4 API/模型列表
  ↓
Phase 5 行为契约/真机/证据
```

每 phase 结束：

```powershell
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
uv run pytest tests/unit -q
```

通过后 commit；不 push。最终汇报包含：修改文件、提交号、质量门结果、真机结果、语义原理解释与遗留风险。